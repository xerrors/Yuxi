"""Agent Call HTTP 协议适配。

本模块只处理 Agent Call 的请求/响应格式、同步等待和 OpenAI-compatible
响应装配；Conversation、Request、Run 的创建统一交给 ``submit_agent_request``。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.agent_run_service import (
    AgentRunWaitTimeout,
    await_agent_run_result,
    get_agent_run_result,
    get_agent_run_view,
)
from yuxi.services.input_message_service import (
    AgentRunInputMessage,
    build_chat_input_message_from_openai_content,
)
from yuxi.services.agent_request_service import RunOrigin, AgentRequestInput, submit_agent_request
from yuxi.storage.postgres.models_business import User
from yuxi.utils.hash_utils import hash_id

from server.utils.auth_middleware import get_db, get_required_user

agent_invocation_call_router = APIRouter(prefix="/agent-invocation/agent-call", tags=["agent-invocation"])

MAX_REQUEST_ID_LENGTH = 64


class AgentCallRunCreate(BaseModel):
    """Agent Call 创建请求，兼容 OpenAI 风格消息输入。"""

    agent_slug: str = Field(..., description="要调用的智能体 slug")
    messages: list[dict[str, Any]] = Field(..., description="消息列表，取最后一条 user 消息作为输入")
    stream: bool = Field(False, description="暂不支持流式，传 true 会返回 422")
    agent_call_meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Agent Call 元数据；不允许通过 context 覆盖 Agent 运行上下文",
    )
    thread_id: str | None = Field(None, description="可选会话线程 ID，不传则自动创建临时线程")
    request_id: str | None = Field(None, description="可选请求幂等 ID，不传则自动生成")
    model_spec: str | None = Field(None, description="可选模型覆盖")
    tool_approval_mode: str | None = Field(None, description="可选工具审批模式覆盖")
    async_mode: bool = Field(False, description="是否只创建运行并立即返回 run_id")
    queue_policy: str | None = Field(None, description="排队策略；异步调用默认 enqueue，同步调用固定 reject")


class AgentCallRunResultRequest(BaseModel):
    """Agent Call 结果读取请求。"""

    run_id: str = Field(..., description="AgentRun ID")
    agent_slug: str | None = Field(None, description="可选，传入时校验 run 归属")


@agent_invocation_call_router.post("/runs")
async def create_agent_call_run(
    payload: AgentCallRunCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """创建 Agent Call，并按 async_mode 决定是否等待最终结果。"""
    agent_slug = _normalize_required_text(payload.agent_slug, field_name="agent_slug")
    if payload.stream:
        raise HTTPException(status_code=422, detail="agent-call 暂不支持 stream=true")

    input_message = _extract_input_message(payload.messages)
    request_id = _normalize_request_id(payload.request_id)
    _validate_agent_call_meta(payload.agent_call_meta)
    queue_policy = str(payload.queue_policy or ("enqueue" if payload.async_mode else "reject")).strip()
    if not payload.async_mode and queue_policy != "reject":
        raise HTTPException(status_code=422, detail="同步 agent-call 仅支持 queue_policy=reject")

    run_response = await submit_agent_request(
        request_input=AgentRequestInput(
            agent_slug=agent_slug,
            thread_id=str(payload.thread_id or "").strip()
            or _invocation_thread_id(current_user.uid, agent_slug, request_id),
            request_id=request_id,
            input_message=input_message,
            origin=RunOrigin(
                source="agent_call",
                channel="api",
                external_id=request_id,
                metadata={"agent_invocation_meta": dict(payload.agent_call_meta or {})}
                if payload.agent_call_meta
                else {},
            ),
            request_metadata={"request_id": request_id},
            model_spec=payload.model_spec,
            tool_approval_mode=payload.tool_approval_mode,
            queue_policy=queue_policy,
            create_conversation=True,
            conversation_title="Agent Call Run",
        ),
        current_user=current_user,
        db=db,
    )

    if payload.async_mode:
        if not run_response.get("run_id"):
            return run_response
        return _build_agent_call_response(
            {
                "run_id": run_response["run_id"],
                "agent_slug": agent_slug,
                "thread_id": run_response["thread_id"],
                "status": run_response["status"],
                "request_id": run_response["request_id"],
                "output": "",
            }
        )

    if run_response["status"] == "rejected":
        return run_response
    try:
        result = await await_agent_run_result(run_id=run_response["run_id"], current_uid=str(current_user.uid))
    except AgentRunWaitTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail={"message": "运行仍在进行中，等待最终结果超时", "run": exc.result},
        ) from exc
    return _build_agent_call_response(result)


@agent_invocation_call_router.post("/runs/result")
async def get_agent_call_run_result(
    payload: AgentCallRunResultRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """读取 Agent Call Run 的 OpenAI-compatible 结果。"""
    run_id = str(payload.run_id or "").strip()
    if not run_id:
        raise HTTPException(status_code=422, detail="run_id 不能为空")
    run_view = await get_agent_run_view(run_id=run_id, current_uid=str(current_user.uid), db=db)
    run = run_view["run"]
    expected_agent_slug = str(payload.agent_slug or "").strip()
    if expected_agent_slug and run.get("agent_slug") != expected_agent_slug:
        raise HTTPException(status_code=409, detail="run_id 与 agent_slug 不匹配")
    result = await get_agent_run_result(run_id=run_id, current_uid=str(current_user.uid), db=db)
    return _build_agent_call_response(result)


def _invocation_thread_id(uid: object, agent_slug: str, request_id: str) -> str:
    """为没有显式 Thread 的 Agent Call 生成稳定线程 ID。"""
    return hash_id("invocation_", f"{uid}:{agent_slug}:{request_id}", length=64)


def _normalize_required_text(value: str | None, *, field_name: str) -> str:
    """清理必填文本字段，空值返回 422。"""
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能为空")
    return normalized


def _normalize_request_id(value: str | None) -> str:
    """生成或校验 Agent Call 请求幂等 ID。"""
    if value is None or not str(value).strip():
        return str(uuid.uuid4())
    normalized = str(value).strip()
    if len(normalized) > MAX_REQUEST_ID_LENGTH:
        raise HTTPException(status_code=422, detail=f"request_id 不能超过 {MAX_REQUEST_ID_LENGTH} 个字符")
    return normalized


def _validate_agent_call_meta(meta: dict[str, Any]) -> None:
    """拒绝通过元数据绕过显式运行上下文字段。"""
    if isinstance(meta, dict) and "context" in meta:
        raise HTTPException(
            status_code=422,
            detail="agent_call_meta.context 不允许覆盖 Agent context，请使用 model_spec 覆盖模型",
        )


def _extract_input_message(messages: list[dict[str, Any]]) -> AgentRunInputMessage:
    """从消息列表中提取最后一条 user 消息作为运行输入。"""
    if not messages:
        raise HTTPException(status_code=422, detail="messages 不能为空")
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        try:
            return build_chat_input_message_from_openai_content(message.get("content"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail="messages 必须包含 user 消息")


def _normalize_usage(usage: object) -> dict[str, int] | None:
    """把不同来源的 usage 字段归一为 OpenAI-compatible 计数字段。"""
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
    total = usage.get("total_tokens")
    prompt = prompt if isinstance(prompt, int) else 0
    completion = completion if isinstance(completion, int) else 0
    total = total if isinstance(total, int) else prompt + completion
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _build_agent_call_response(result: dict[str, Any]) -> dict[str, Any]:
    """将 AgentRun 结果装配为 Agent Call 响应。"""
    raw_status = str(result.get("status") or "unknown")
    status = "pending" if raw_status == "dispatched" else raw_status
    output = result.get("output") if isinstance(result.get("output"), str) else ""
    token_usage = result.get("token_usage")
    token_total = (
        token_usage.get("total") if isinstance(token_usage, dict) and token_usage.get("complete") is True else None
    )
    payload: dict[str, Any] = {
        "run_id": result.get("agent_run_id") or result.get("run_id"),
        "agent_slug": result.get("agent_slug"),
        "thread_id": result.get("thread_id"),
        "status": status,
        "request_id": result.get("request_id"),
        "output": output,
        "choices": [
            {
                "index": 0,
                "messages": [{"role": "assistant", "content": output}],
                "finish_reason": _finish_reason(status),
            }
        ],
        "usage": _normalize_usage(token_total),
    }
    if result.get("error"):
        payload["error"] = result["error"]
    return payload


def _finish_reason(status: str) -> str | None:
    """根据运行终态生成 OpenAI choices.finish_reason。"""
    if status == "completed":
        return "stop"
    if status in {"failed", "cancelled", "interrupted"}:
        return status
    return None
