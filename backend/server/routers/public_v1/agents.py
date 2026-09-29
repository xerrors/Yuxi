"""Agents Public API 的 Thread/Request 协议与 Session/Turn 兼容入口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.agents.public_api import (
    create_public_session,
    get_public_agent,
    get_public_request,
    get_public_session,
    get_public_thread,
    get_public_turn,
    get_public_turn_items,
    has_legacy_public_creation,
    list_public_agents,
    resolve_public_user,
    stream_public_request_events,
    submit_public_message,
)
from yuxi.services.agents.session_input import cancel_public_turn, resume_public_turn
from yuxi.services.agents.session_events import stream_public_session_events
from yuxi.storage.postgres.models_business import APIKey, Agent, User
from yuxi.utils.sse_utils import format_sse


@dataclass(frozen=True)
class PublicAgentContext:
    """已认证用户与可信产品或 APP 来源。"""

    user: User
    owner: User
    api_key: APIKey | None

    @property
    def app_id(self) -> str | None:
        """产品 JWT 使用无 APP 的独立资源命名空间。"""
        return self.api_key.app_id if self.api_key else None


async def require_public_context(
    request: Request,
    end_user_id: str | None = Header(default=None, alias="X-End-User-Id"),
    owner: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
) -> PublicAgentContext:
    """Public API 接受产品 JWT 或绑定 APP 的 API Key。"""
    api_key = getattr(request.state, "api_key", None)
    if api_key is None:
        if end_user_id is not None:
            raise HTTPException(status_code=403, detail="X-End-User-Id 仅适用于 API Key")
        return PublicAgentContext(user=owner, owner=owner, api_key=None)
    if not api_key.app_id:
        raise HTTPException(status_code=403, detail="Agents Public API 需要绑定 app_id 的 API Key")
    user = await resolve_public_user(owner=owner, api_key=api_key, end_user_id=end_user_id, db=db)
    return PublicAgentContext(user=user, owner=owner, api_key=api_key)


public_agents_router = APIRouter(
    prefix="/v1/agents", tags=["agents-public-v1"], dependencies=[Depends(require_public_context)]
)


class InputTextPart(BaseModel):
    """首发支持的文本输入内容块。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["input_text"]
    text: str = Field(min_length=1, max_length=32768)


class InputImagePart(BaseModel):
    """只接受内联图片，供现有多图模型输入使用。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["input_image"]
    image_url: str = Field(min_length=1)


class InputMessage(BaseModel):
    """一条用户消息可包含文本与内联图片。"""

    model_config = ConfigDict(extra="forbid")
    role: Literal["user"]
    content: list[InputTextPart | InputImagePart] = Field(min_length=1, max_length=18)


class ThreadCreate(BaseModel):
    """创建线程并提交第一条消息。"""

    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=64)
    input: list[InputMessage] = Field(min_length=1, max_length=1)


class SessionCreate(BaseModel):
    """创建空 Session 或提交首条输入并订阅首轮事件。"""

    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=64)
    input: list[InputMessage] | None = Field(default=None, min_length=1, max_length=1)
    stream: StrictBool = False
    project_id: str | None = None
    title: str | None = Field(default=None, max_length=255)
    model_spec: str | None = None
    tool_approval_mode: str | None = None


class RequestCreate(BaseModel):
    """向已有线程提交一条用户消息。"""

    model_config = ConfigDict(extra="forbid")
    input: list[InputMessage] = Field(min_length=1, max_length=1)


class InputEvent(BaseModel):
    """Session 兼容协议中的单条输入事件。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["agent.session.input.message"]
    input: list[InputMessage] = Field(min_length=1, max_length=1)
    mode: Literal["follow_up", "steer"] = "steer"
    model_spec: str | None = None
    tool_approval_mode: str | None = None
    attachment_file_ids: list[str] = Field(default_factory=list, max_length=20)


class CancelEvent(BaseModel):
    """取消首次接收时选定的当前 Turn。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["agent.session.input.cancel"]
    turn_id: str | None = None
    run_id: str | None = None


class ResumeEvent(BaseModel):
    """把产品审批或回答恢复到指定 Turn 和暂停 Run。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["yuxi.session.input.resume"]
    turn_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    resume: Any


class SessionEventCreate(BaseModel):
    """Session 兼容协议每次只接收一个事件。"""

    model_config = ConfigDict(extra="forbid")
    events: list[InputEvent | CancelEvent | ResumeEvent] = Field(min_length=1, max_length=1)


@public_agents_router.get("")
async def list_agents(
    context: PublicAgentContext = Depends(require_public_context), db: AsyncSession = Depends(get_db)
):
    """列出当前 Key 主体可见的主 Agent。"""
    agents = await list_public_agents(user=context.owner, db=db)
    return {"data": [_agent_response(agent) for agent in agents]}


@public_agents_router.post("/threads")
async def create_thread(
    payload: ThreadCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """创建 Thread，并持久接收首个 Request。"""
    receipt = await submit_public_message(
        agent_id=payload.agent_id,
        thread_id=None,
        text_parts=_message_parts(payload.input),
        image_content=_message_images(payload.input),
        ordered_content=_ordered_message_content(payload.input),
        creation_input_intent=[item.model_dump(mode="json") for item in payload.input],
        idempotency_key=idempotency_key,
        user=context.user,
        api_key=context.api_key,
        db=db,
    )
    return {
        **_thread_response({**receipt, "agent_id": payload.agent_id}),
        **_request_urls(receipt["thread_id"], receipt["request_id"]),
    }


@public_agents_router.get("/threads/{thread_id}")
async def retrieve_thread(
    thread_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """读取当前 APP 的 Thread 和最近 Request。"""
    thread = await get_public_thread(thread_id=thread_id, user=context.user, app_id=context.app_id, db=db)
    return _thread_response(thread)


@public_agents_router.post("/threads/{thread_id}/requests", status_code=202)
async def create_request(
    thread_id: str,
    payload: RequestCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """向现有 Thread 提交一个持久 Request。"""
    thread = await get_public_thread(thread_id=thread_id, user=context.user, app_id=context.app_id, db=db)
    receipt = await submit_public_message(
        agent_id=thread["agent_id"],
        thread_id=thread_id,
        text_parts=_message_parts(payload.input),
        image_content=_message_images(payload.input),
        ordered_content=_ordered_message_content(payload.input),
        idempotency_key=idempotency_key,
        user=context.user,
        api_key=context.api_key,
        db=db,
    )
    return {
        "object": "agent.request.accepted",
        **receipt,
        **_request_urls(receipt["thread_id"], receipt["request_id"]),
    }


@public_agents_router.get("/threads/{thread_id}/requests/{request_id}")
async def retrieve_request(
    thread_id: str,
    request_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """读取当前 APP 线程内指定 Request 的持久结果。"""
    result = await get_public_request(
        thread_id=thread_id, request_id=request_id, user=context.user, app_id=context.app_id, db=db
    )
    return _request_response(result, thread_id)


@public_agents_router.get("/threads/{thread_id}/requests/{request_id}/events")
async def observe_request_events(
    thread_id: str,
    request_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """订阅指定 Request 的排队与唯一 Run 事件。"""
    await get_public_request(
        thread_id=thread_id, request_id=request_id, user=context.user, app_id=context.app_id, db=db
    )
    uid = str(context.user.uid)
    # 校验事务必须在 SSE 开始前归还连接；事件轮询使用独立短会话。
    await db.close()
    return _stream_response(
        request_id=request_id,
        uid=uid,
        after_seq=last_event_id or "0-0",
        stream_url=_request_urls(thread_id, request_id)["events_url"],
    )


@public_agents_router.post("/sessions")
async def create_session(
    payload: SessionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """在 Public 边界创建 Session，可选首条输入。"""
    creation_key = idempotency_key if idempotency_key is not None else uuid4().hex
    if payload.input is None:
        if await has_legacy_public_creation(
            idempotency_key=creation_key, user=context.user, api_key=context.api_key, db=db
        ):
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于带输入的 Session")
        if payload.stream:
            raise HTTPException(status_code=422, detail="空 Session 不能以流式创建")
        session = await create_public_session(
            agent_id=payload.agent_id,
            idempotency_key=creation_key,
            user=context.user,
            api_key=context.api_key,
            project_id=payload.project_id,
            title=payload.title,
            model_spec=payload.model_spec,
            tool_approval_mode=payload.tool_approval_mode,
            db=db,
        )
        return _session_response(session)
    receipt = await submit_public_message(
        agent_id=payload.agent_id,
        thread_id=None,
        text_parts=_message_parts(payload.input),
        image_content=_message_images(payload.input),
        ordered_content=_ordered_message_content(payload.input),
        creation_input_intent=[item.model_dump(mode="json") for item in payload.input],
        idempotency_key=creation_key,
        user=context.user,
        api_key=context.api_key,
        project_id=payload.project_id,
        title=payload.title,
        model_spec=payload.model_spec,
        tool_approval_mode=payload.tool_approval_mode,
        db=db,
    )
    session = await get_public_session(
        thread_id=receipt["thread_id"], user=context.user, app_id=context.app_id, db=db
    )
    response = {
        **_session_response({**session, "turn_id": receipt["turn_id"], "run_id": receipt["run_id"]}),
        **_session_urls(receipt["thread_id"], receipt["turn_id"]),
    }
    if payload.stream:
        uid = str(context.user.uid)
        # 提交已持久化；释放随后读取 Session 状态开启的事务。
        await db.close()
        return _session_stream_response(
            thread_id=receipt["thread_id"],
            turn_id=receipt["turn_id"],
            uid=uid,
            app_id=context.app_id,
            after_seq="0-0",
            stream_url=response["events_url"],
            initial_event=response,
        )
    return response


@public_agents_router.get("/sessions/{session_id}")
async def retrieve_session(
    session_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """将兼容 Session ID 作为内部 Thread ID 查询。"""
    thread_id = session_id
    session = await get_public_session(thread_id=thread_id, user=context.user, app_id=context.app_id, db=db)
    return _session_response(session)


@public_agents_router.post("/sessions/{session_id}/events", status_code=202)
async def create_input_event(
    session_id: str,
    payload: SessionEventCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """接收消息、固定目标的取消或产品暂停恢复事件。"""
    thread_id = session_id
    event = payload.events[0]
    if isinstance(event, CancelEvent):
        return await cancel_public_turn(
            thread_id=thread_id,
            target_turn_id=event.turn_id,
            target_run_id=event.run_id,
            idempotency_key=idempotency_key if idempotency_key is not None else uuid4().hex,
            user=context.user,
            app_id=context.app_id,
            db=db,
        )
    if isinstance(event, ResumeEvent):
        return await resume_public_turn(
            thread_id=thread_id,
            turn_id=event.turn_id,
            parent_run_id=event.run_id,
            resume=event.resume,
            idempotency_key=idempotency_key if idempotency_key is not None else uuid4().hex,
            user=context.user,
            app_id=context.app_id,
            db=db,
        )
    thread = await get_public_thread(thread_id=thread_id, user=context.user, app_id=context.app_id, db=db)
    receipt = await submit_public_message(
        agent_id=thread["agent_id"],
        thread_id=thread_id,
        text_parts=_message_parts(event.input),
        image_content=_message_images(event.input),
        ordered_content=_ordered_message_content(event.input),
        idempotency_key=idempotency_key if idempotency_key is not None else uuid4().hex,
        user=context.user,
        api_key=context.api_key,
        db=db,
        mode=event.mode,
        mode_explicit="mode" in event.model_fields_set,
        model_spec=event.model_spec,
        tool_approval_mode=event.tool_approval_mode,
        attachment_file_ids=event.attachment_file_ids,
    )
    return {
        "object": "agent.session.event.accepted",
        "session_id": receipt["thread_id"],
        "turn_id": receipt["turn_id"],
        "request_id": receipt["request_id"],
        "status": receipt["status"],
        "run_id": receipt["run_id"],
        **_session_urls(receipt["thread_id"], receipt["turn_id"]),
    }


@public_agents_router.get("/sessions/{session_id}/turns/{turn_id}")
async def retrieve_turn(
    session_id: str,
    turn_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """读取独立 Turn 及其绑定的 Request/Run 结果。"""
    result = await get_public_turn(
        thread_id=session_id, turn_id=turn_id, user=context.user, app_id=context.app_id, db=db
    )
    return _turn_response(result, session_id)


@public_agents_router.get("/sessions/{session_id}/turns/{turn_id}/items")
async def list_turn_items(
    session_id: str,
    turn_id: str,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """按持久 Message 读取指定 Turn 的可见输入与输出。"""
    return await get_public_turn_items(
        thread_id=session_id,
        turn_id=turn_id,
        user=context.user,
        app_id=context.app_id,
        after_id=after_id,
        limit=limit,
        db=db,
    )


@public_agents_router.get("/sessions/{session_id}/events")
async def observe_turn_events(
    session_id: str,
    turn_id: str | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """订阅 Session 当前轮或指定 Turn 的跨 Run 事件。"""
    thread_id = session_id
    if turn_id is None:
        await get_public_session(thread_id=thread_id, user=context.user, app_id=context.app_id, db=db)
    else:
        await get_public_turn(thread_id=thread_id, turn_id=turn_id, user=context.user, app_id=context.app_id, db=db)
    uid = str(context.user.uid)
    # 校验事务必须在 SSE 开始前归还连接；事件轮询使用独立短会话。
    await db.close()
    stream_url = (
        _session_urls(thread_id, turn_id)["events_url"]
        if turn_id
        else f"/api/v1/agents/sessions/{thread_id}/events"
    )
    return _session_stream_response(
        thread_id=thread_id,
        turn_id=turn_id,
        uid=uid,
        app_id=context.app_id,
        after_seq=last_event_id or "0-0",
        stream_url=stream_url,
    )


@public_agents_router.get("/{agent_id}")
async def retrieve_agent(
    agent_id: str,
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """查询可见 Agent 的公开目录字段。"""
    agent = await get_public_agent(agent_id=agent_id, user=context.owner, db=db)
    return _agent_response(agent)


def _message_parts(messages: list[InputMessage]) -> list[str]:
    """保留内容块边界供幂等意图判定。"""
    return [part.text for part in messages[0].content if isinstance(part, InputTextPart)]


def _message_images(messages: list[InputMessage]) -> list[str]:
    """只在 HTTP 边界提取内联图片正文。"""
    images = []
    for part in messages[0].content:
        if not isinstance(part, InputImagePart):
            continue
        if not part.image_url.startswith("data:image/") or ";base64," not in part.image_url:
            raise HTTPException(status_code=422, detail="input_image 仅支持 data:image base64 URL")
        image_content = part.image_url.split(";base64,", 1)[1]
        if not image_content:
            raise HTTPException(status_code=422, detail="input_image 内容不能为空")
        images.append(image_content)
    return images


def _ordered_message_content(messages: list[InputMessage]) -> list[dict]:
    """保留 Public 输入图文块的顺序和图片 MIME 类型。"""
    content = []
    for part in messages[0].content:
        if isinstance(part, InputTextPart):
            content.append({"type": "text", "text": part.text})
        else:
            content.append({"type": "image_url", "image_url": part.image_url})
    return content


def _agent_response(agent: Agent) -> dict:
    """只返回公开 Agent 目录字段。"""
    return {"id": agent.slug, "object": "agent", "name": agent.name, "description": agent.description}


def _thread_response(thread: dict) -> dict:
    """组装原生 Thread 资源。"""
    return {
        "object": "agent.thread",
        "thread_id": thread["thread_id"],
        "agent_id": thread["agent_id"],
        "status": thread["status"],
        "request_id": thread["request_id"],
        "run_id": thread["run_id"],
    }


def _session_response(thread: dict) -> dict:
    """把 Thread 投影为兼容 Session 资源。"""
    return {
        "id": thread["thread_id"],
        "object": "agent.session",
        "agent": {"id": thread["agent_id"]},
        "status": thread["status"],
        "turn_id": thread.get("turn_id") or thread["request_id"],
        "run_id": thread["run_id"],
        "project_id": thread.get("project_id"),
        "title": thread.get("title"),
    }


def _request_response(result: dict, thread_id: str) -> dict:
    """只返回指定 Request 的公开结果字段。"""
    return {
        "object": "agent.request",
        "thread_id": thread_id,
        "request_id": result["request_id"],
        "status": result["status"],
        "request_status": result["request_status"],
        "run_id": result["run_id"],
        "run_status": result["run_status"],
        "output": result["output"],
        "usage": result["usage"],
        "error": result["error"],
    }


def _turn_response(result: dict, thread_id: str) -> dict:
    """把同一 Request 投影为兼容 Turn 资源。"""
    return {
        "id": result["turn_id"],
        "object": "agent.session.turn",
        "session_id": thread_id,
        "status": result["status"],
        "request_status": result["request_status"],
        "request_id": result["request_id"],
        "request_ids": result["request_ids"],
        "run_ids": result["run_ids"],
        "run_id": result["run_id"],
        "run_status": result["run_status"],
        "output": result["output"],
        "usage": result["usage"],
        "error": result["error"],
    }


def _request_urls(thread_id: str, request_id: str) -> dict:
    """生成原生 Request 查询及事件地址。"""
    base = f"/api/v1/agents/threads/{thread_id}/requests/{request_id}"
    return {"result_url": base, "events_url": f"{base}/events"}


def _session_urls(thread_id: str, request_id: str) -> dict:
    """生成兼容 Turn 查询及事件地址。"""
    base = f"/api/v1/agents/sessions/{thread_id}"
    return {
        "result_url": f"{base}/turns/{request_id}",
        "events_url": f"{base}/events?turn_id={request_id}",
    }


def _stream_response(
    *, request_id: str, uid: str, after_seq: str, stream_url: str, initial_event: dict | None = None
) -> StreamingResponse:
    """订阅 Request 派发后衔接的唯一 Run 事件。"""

    async def events():
        """创建流先发布持久 Session 标识，再跟随 Request/Run。"""
        if initial_event is not None:
            yield format_sse(initial_event, event="session_created")
        async for event in stream_public_request_events(
            request_id=request_id, uid=uid, after_seq=after_seq, run_stream_url=stream_url
        ):
            yield event

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _session_stream_response(
    *,
    thread_id: str,
    turn_id: str | None,
    uid: str,
    app_id: str | None,
    after_seq: str,
    stream_url: str,
    initial_event: dict | None = None,
) -> StreamingResponse:
    """创建响应和后续订阅使用同一跨 Run 会话流。"""

    async def events():
        """先报告已持久化 Session，再跟随当前 Turn 的执行段。"""
        if initial_event is not None:
            yield format_sse(initial_event, event="session_created")
        async for event in stream_public_session_events(
            thread_id=thread_id,
            turn_id=turn_id,
            uid=uid,
            app_id=app_id,
            after_id=after_seq,
            stream_url=stream_url,
        ):
            yield event

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
