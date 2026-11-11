"""Public Thread 的输入事件与 SSE 编码。"""

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db
from yuxi.api.routers.public_v1.agents.auth import PublicAgentContext, require_public_context
from yuxi.api.routers.public_v1.agents.responses import PUBLIC_ERRORS, SSE_RESPONSE
from yuxi.api.routers.public_v1.agents.schemas import (
    CancelEvent,
    CancelInputEvent,
    ContinueEvent,
    EventAccepted,
    MessageEvent,
    PromoteInputEvent,
    ResumeEvent,
    SessionEventCreate,
    ThreadEvent,
    TreeControlEvent,
    input_messages_to_domain,
)
from yuxi.api.sse import format_sse
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.inputs import accept_message
from yuxi.modules.agents.services.public_resources import event_receipt
from yuxi.modules.agents.services.subscriptions import stream_thread_events, validate_event_cursor
from yuxi.modules.agents.services.thread_access import require_thread
from yuxi.modules.agents.services.threads import cancel_input, continue_queue, promote_input
from yuxi.modules.agents.services.turns import cancel_turn, resume_turn

router = APIRouter(dependencies=[Depends(require_public_context)], responses=PUBLIC_ERRORS)


@router.post("/sessions/{session_id}/events", status_code=202, summary="提交消息或控制事件", response_model=EventAccepted)
async def submit_public_event(
    session_id: str,
    payload: SessionEventCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """一次提交一个输入事件，可包含多条有序用户消息。

    202 仅证明持久接收。相同会话、幂等键和意图可重试；改变意图返回 409。
    普通消息在运行中默认 steer，空闲默认 follow_up；等待回答/审批时返回 409。
    显式 follow_up 按 FIFO 排队，Turn/Run ID 在消费时绑定。
    cancel 仅暂停已有待办；新用户消息优先恢复目标队列，重放不再次恢复。
    恢复、审批、队列及协作控制使用 yuxi.* 扩展。
    """
    result = await submit_thread_event(
        db=db,
        scope=context.scope,
        thread_id=session_id,
        event=payload.events[0],
        idempotency_key=idempotency_key,
    )
    return event_receipt(result)


async def submit_thread_event(*, db: AsyncSession, scope: ActorScope, thread_id: str, event: ThreadEvent, idempotency_key: str) -> dict:
    """把已规范化事件交给唯一的生命周期用例。"""
    if isinstance(event, TreeControlEvent):
        from yuxi.modules.agents.services.cooperation import control_tree

        result = await control_tree(
            db=db,
            scope=scope,
            thread_id=thread_id,
            idempotency_key=idempotency_key,
            stopped=event.type == "yuxi.session.tree.stop",
        )
    elif isinstance(event, MessageEvent):
        result = await accept_message(
            db=db,
            scope=scope,
            thread_id=thread_id,
            idempotency_key=idempotency_key,
            mode=event.yuxi.mode,
            messages=input_messages_to_domain(event.input),
            attachment_file_ids=event.yuxi.attachment_file_ids,
            mcps=event.yuxi.mcps,
            model_spec=event.yuxi.model,
            tool_approval_mode=event.yuxi.tool_approval_mode,
        )
    elif isinstance(event, ResumeEvent):
        result = await resume_turn(
            db=db,
            scope=scope,
            thread_id=thread_id,
            turn_id=event.turn_id,
            waitpoint_id=event.waitpoint_id,
            response=event.response.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
    elif isinstance(event, CancelEvent):
        result = await cancel_turn(
            db=db,
            scope=scope,
            thread_id=thread_id,
            turn_id=event.yuxi.turn_id,
            expected_run_id=event.yuxi.expected_run_id,
            idempotency_key=idempotency_key,
        )
    elif isinstance(event, ContinueEvent):
        result = await continue_queue(db=db, scope=scope, thread_id=thread_id, idempotency_key=idempotency_key)
    elif isinstance(event, CancelInputEvent):
        result = await cancel_input(db=db, scope=scope, thread_id=thread_id, input_id=event.input_id, idempotency_key=idempotency_key)
    elif isinstance(event, PromoteInputEvent):
        result = await promote_input(db=db, scope=scope, thread_id=thread_id, input_id=event.input_id, idempotency_key=idempotency_key)
    return result


@router.get(
    "/sessions/{session_id}/events",
    summary="订阅会话事件",
    response_class=StreamingResponse,
    responses={200: SSE_RESPONSE},
)
async def observe_public_events(
    session_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    context: PublicAgentContext = Depends(require_public_context),
    db: AsyncSession = Depends(get_db),
):
    """返回长期 text/event-stream，覆盖同一会话的多轮工作。

    event 等于 data.type，data 是公开事件本身；id 是恢复 cursor，区别于逻辑 event_id。
    断线带 Last-Event-ID 续订；收到 yuxi.session.resync 后回读 Items 和会话快照。
    agent.session.turn.completed/failed/cancelled 表达指定 Turn 的终态。
    连接不随单轮终态自动结束，客户端回读结果后自行关闭，不依赖 SDK finalResult。
    """
    await require_thread(db=db, scope=context.scope, thread_id=session_id)
    await db.close()
    return public_stream_response(scope=context.scope, thread_id=session_id, after_cursor=last_event_id)


def public_stream_response(
    *,
    scope: ActorScope,
    thread_id: str,
    after_cursor: str | None,
    initial_event: dict | None = None,
) -> StreamingResponse:
    """仅在 HTTP 边界把结构化输出编码为 SSE。"""
    validate_event_cursor(after_cursor)

    async def events():
        """输出可选创建回执并订阅持久生命周期事件。"""
        if initial_event is not None:
            created = {
                "type": "agent.session.created",
                "event_id": initial_event["yuxi"]["receipt"]["event_id"],
                "session_id": thread_id,
                "session": initial_event,
            }
            yield format_sse(created, event=created["type"])
        async for cursor, event in stream_thread_events(scope=scope, thread_id=thread_id, after_cursor=after_cursor):
            yield format_sse(event, event=event["type"], event_id=cursor)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        status_code=201 if initial_event is not None else 200,
    )
