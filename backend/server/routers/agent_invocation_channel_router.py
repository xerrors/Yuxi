"""纯文本 Channel 消息入口。

Channel 只负责把消息信封转换为统一 Run 提交命令；少量控制命令在普通
消息提交之前处理，避免状态查询或审批决议被错误排进 Agent Request 队列。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services.agent_run_service import create_resume_run_view
from yuxi.services.channel_command_service import parse_slash_command
from yuxi.services.chat_service import get_agent_state_view
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.services.agent_request_service import RunOrigin, AgentRequestInput, submit_agent_request
from yuxi.storage.postgres.models_business import User
from yuxi.utils.hash_utils import hash_id

from server.utils.auth_middleware import get_db, get_required_user

agent_invocation_channel_router = APIRouter(prefix="/agent-invocation/channel", tags=["agent-invocation"])


class ChannelTextMessage(BaseModel):
    """Channel 普通文本消息体。"""

    type: Literal["text"] = "text"
    text: str = Field(..., min_length=1, description="纯文本消息")


class ChannelMessageRequest(BaseModel):
    """Channel 消息信封，承载来源账号、线程与幂等标识。"""

    channel: str = Field("cli", max_length=32, description="通道名称")
    account_id: str = Field("default", description="通道账号标识")
    chat_id: str | None = Field(None, description="通道侧会话标识")
    thread_id: str | None = Field(None, description="可选 Yuxi Thread ID")
    sender_id: str | None = Field(None, description="通道侧发送者标识")
    message_id: str | None = Field(None, max_length=128, description="通道侧消息 ID")
    request_id: str | None = Field(None, description="请求幂等 ID")
    agent_slug: str = Field(..., description="目标 Agent slug")
    message: ChannelTextMessage
    queue_policy: Literal["enqueue", "reject", "steer"] = "steer"


@agent_invocation_channel_router.post("/messages")
async def receive_channel_message(
    payload: ChannelMessageRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """处理纯文本 Channel 消息或最小 slash command。"""
    channel = _normalize_required(payload.channel, "channel")
    account_id = _normalize_required(payload.account_id, "account_id")
    agent_slug = _normalize_required(payload.agent_slug, "agent_slug")
    thread_id = _resolve_thread_id(
        uid=str(current_user.uid),
        channel=channel,
        account_id=account_id,
        chat_id=payload.chat_id,
        requested_thread_id=payload.thread_id,
    )
    message_text = payload.message.text.strip()
    if not message_text:
        raise HTTPException(status_code=422, detail="text 不能为空")
    raw_request_id = str(payload.request_id or "").strip()
    external_id = str(payload.message_id or "").strip() or raw_request_id or str(uuid.uuid4())
    request_id = raw_request_id or hash_id(
        "channel_request_",
        f"{current_user.uid}:{channel}:{account_id}:{payload.chat_id or thread_id}:{external_id}",
        length=64,
    )
    if len(request_id) > 64:
        raise HTTPException(status_code=422, detail="request_id 不能超过 64 个字符")
    origin_metadata = {
        key: value
        for key, value in {
            "account_id": account_id,
            "chat_id": payload.chat_id,
            "sender_id": payload.sender_id,
        }.items()
        if value
    }

    try:
        command = parse_slash_command(message_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if command is not None:
        if command.name == "state":
            _require_no_args(command.name, command.args)
            state = await get_agent_state_view(
                thread_id=thread_id,
                current_user=current_user,
                db=db,
                include_messages=False,
            )
            return {"kind": "command", "command": "state", "thread_id": thread_id, "state": state}
        if command.name == "approve":
            _require_no_args(command.name, command.args)
            return await _approve_latest_run(
                agent_slug=agent_slug,
                thread_id=thread_id,
                request_id=request_id,
                external_id=external_id,
                channel=channel,
                origin_metadata=origin_metadata,
                current_user=current_user,
                db=db,
            )
        raise HTTPException(status_code=422, detail=f"不支持的 slash command: /{command.name}")

    latest_run = await AgentRunRepository(db).get_latest_chat_or_resume_run(
        uid=str(current_user.uid),
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if latest_run and latest_run.status == "interrupted" and latest_run.error_type != "human_approval_required":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ask_user_question_unsupported",
                "message": "当前线程等待用户回答，Channel 暂不支持 ask_user_question",
            },
        )

    result = await submit_agent_request(
        request_input=AgentRequestInput(
            agent_slug=agent_slug,
            thread_id=thread_id,
            request_id=request_id,
            input_message=build_chat_input_message(message_text),
            origin=RunOrigin(
                source="channel",
                channel=channel,
                external_id=external_id,
                metadata=origin_metadata,
            ),
            request_metadata={"message_type": "text"},
            queue_policy=payload.queue_policy,
            create_conversation=True,
            conversation_title=f"{channel} Channel Run",
        ),
        current_user=current_user,
        db=db,
    )
    result["kind"] = "run"
    result["channel"] = channel
    return result


async def _approve_latest_run(
    *,
    agent_slug: str,
    thread_id: str,
    request_id: str,
    external_id: str,
    channel: str,
    origin_metadata: dict[str, str],
    current_user: User,
    db: AsyncSession,
) -> dict:
    """审批当前等待中的工具调用，并优先复用同 request_id 的恢复 run。"""
    run_repo = AgentRunRepository(db)
    existing_run = await run_repo.get_run_by_request_id(request_id)
    latest_run = await run_repo.get_latest_chat_or_resume_run(
        uid=str(current_user.uid),
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if existing_run:
        if (
            existing_run.uid != str(current_user.uid)
            or existing_run.agent_slug != agent_slug
            or existing_run.conversation_thread_id != thread_id
            or existing_run.run_type != "resume"
            or not existing_run.created_by_run_id
            or latest_run is None
            or latest_run.id != existing_run.id
        ):
            raise HTTPException(status_code=409, detail="request_id 冲突")
        parent_run_id = existing_run.created_by_run_id
    else:
        if not latest_run or latest_run.status != "interrupted":
            raise HTTPException(
                status_code=409,
                detail={"code": "no_pending_approval", "message": "没有待审批的运行"},
            )
        if latest_run.error_type != "human_approval_required":
            raise HTTPException(
                status_code=409,
                detail={"code": "ask_user_question_unsupported", "message": "当前中断不是工具审批，暂不支持处理"},
            )
        parent_run_id = latest_run.id

    result = await create_resume_run_view(
        agent_slug=agent_slug,
        thread_id=thread_id,
        meta={"request_id": request_id, "source": "channel", "channel": channel},
        current_uid=str(current_user.uid),
        db=db,
        resume={"decisions": [{"type": "approve"}]},
        created_by_run_id=parent_run_id,
        source="channel",
        channel=channel,
        external_id=external_id,
        origin_metadata=origin_metadata,
    )
    return {"kind": "command", "command": "approve", "thread_id": thread_id, "run": result}


def _resolve_thread_id(
    *,
    uid: str,
    channel: str,
    account_id: str,
    chat_id: str | None,
    requested_thread_id: str | None,
) -> str:
    """根据显式 thread 或通道会话信息解析稳定 Yuxi Thread ID。"""
    if requested_thread_id and requested_thread_id.strip():
        return requested_thread_id.strip()
    if not chat_id or not chat_id.strip():
        raise HTTPException(status_code=422, detail="thread_id 或 chat_id 至少提供一个")
    return hash_id("channel_", f"{uid}:{channel}:{account_id}:{chat_id.strip()}", length=64)


def _normalize_required(value: str | None, field_name: str) -> str:
    """校验并清理必填字符串字段。"""
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能为空")
    return normalized


def _require_no_args(name: str, args: tuple[str, ...]) -> None:
    """拒绝当前不支持参数的 slash command 变体。"""
    if args:
        raise HTTPException(status_code=422, detail=f"/{name} 不接受参数")
