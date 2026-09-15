"""Agent request queue service.

提供请求入队、FIFO 派发、取消和恢复扫描的完整事务逻辑。
不调用 agent_run_service 私有函数。
``recover_pending_dispatches`` 自管会话，提交后才调 ``enqueue_agent_run``。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.agent_run_service import (
    create_agent_run_input_message,
    enqueue_agent_run,
    resolve_agent_run_config,
)
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.services.personal_trash_service import lock_user_files, require_no_pending_file_operations
from yuxi.services.workdir_service import (
    WorkdirBinding,
    resolve_conversation_workdir_binding,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun, AgentRunRequest, Message
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger
from yuxi.utils.sse_utils import (
    SSE_HEARTBEAT_SECONDS,
    SSE_MAX_CONNECTION_MINUTES,
    SSE_POLL_INTERVAL_SECONDS,
    format_heartbeat,
    format_sse,
)
from yuxi.workspace.paths import ensure_bound_user_workdir

SUPPORTED_QUEUE_POLICIES = ("enqueue", "reject", "steer")
NOT_IMPLEMENTED_QUEUE_POLICIES = ("guided", "bridge")

# Request lifecycle states.
REQUEST_STATUS_QUEUED = "queued"
REQUEST_STATUS_DISPATCHED = "dispatched"
REQUEST_STATUS_CANCELLED = "cancelled"
REQUEST_STATUS_REJECTED = "rejected"
REQUEST_STATUS_FAILED = "failed"
REQUEST_TERMINAL_STATUSES = frozenset({REQUEST_STATUS_CANCELLED, REQUEST_STATUS_REJECTED, REQUEST_STATUS_FAILED})

# Message delivery states aligned with messages.delivery_status.
DELIVERY_STATUS_QUEUED = "queued"
DELIVERY_STATUS_DISPATCHED = "dispatched"
DELIVERY_STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class IntakeResult:
    """入队决策结果。"""

    request_id: str
    status: str  # queued / dispatched / rejected
    queue_policy: str
    message_id: int | None
    thread_id: str
    run_id: str | None = None
    # FIFO 队内位置；未在排队（dispatched/rejected/已存在）时为 None。
    queue_position: int | None = None
    workdir_binding: WorkdirBinding | None = None


@dataclass(frozen=True)
class DispatchResult:
    """一次已提交前的 FIFO 队头派发结果。"""

    request_id: str
    run_id: str
    workdir_binding: WorkdirBinding


def validate_queue_policy(queue_policy: str) -> str:
    """校验 queue_policy，对未实现策略返回 422。"""
    if queue_policy in NOT_IMPLEMENTED_QUEUE_POLICIES:
        raise HTTPException(
            status_code=422,
            detail=f"queue_policy '{queue_policy}' 暂未实现",
        )
    if queue_policy not in SUPPORTED_QUEUE_POLICIES:
        raise HTTPException(status_code=422, detail=f"不支持的 queue_policy: {queue_policy}")
    return queue_policy


async def intake_request(
    *,
    db: AsyncSession,
    request_id: str,
    uid: str,
    agent_slug: str,
    thread_id: str,
    source: str = "chat",
    channel: str = "web",
    external_id: str | None = None,
    origin_metadata: dict | None = None,
    queue_policy: str = "enqueue",
    input_message: AgentRunInputMessage,
    agent_item: Any,
    agent_backend: Any,
    model_spec: str | None = None,
    tool_approval_mode: str | None = None,
    meta: dict | None = None,
    workdir_binding: WorkdirBinding | None = None,
) -> IntakeResult:
    """创建 request + Message，尝试立即派发。

    全部 flush 在调用方事务内完成；不 commit。
    返回 IntakeResult：dispatched 时含 run_id（调用方需 commit 后 enqueue ARQ）。
    """
    policy = validate_queue_policy(queue_policy)
    if policy == "steer" and source not in {"chat", "channel"}:
        raise HTTPException(status_code=422, detail="queue_policy 'steer' 仅支持主会话 Chat/Channel")
    meta = meta or {}
    uid_str = str(uid)
    await lock_user_files(db, uid_str)
    await require_no_pending_file_operations(db, uid_str)
    repo = AgentRunRequestRepository(db)

    async def existing_intake_result(binding: WorkdirBinding | None = None) -> IntakeResult | None:
        """幂等：相同 request_id 已存在时返回既有 request/run 视图，不存在返回 None。"""
        if binding is not None and (binding.uid != uid_str or binding.thread_id != thread_id):
            raise RuntimeError("传入的 Workdir 绑定与请求作用域不一致")
        existing = await repo.get_by_request_id(request_id)
        if not existing:
            return None
        return await _build_existing_intake_result(
            repo=repo,
            request=existing,
            uid=uid_str,
            agent_slug=agent_slug,
            thread_id=thread_id,
            source=source,
            channel=channel,
            external_id=external_id,
            queue_policy=policy,
            workdir_binding=binding,
        )

    if result := await existing_intake_result(workdir_binding):
        return result

    conversation = await _get_thread_conversation(
        db=db,
        uid=uid_str,
        agent_slug=agent_slug,
        thread_id=thread_id,
        lock=True,
    )
    if workdir_binding is None:
        workdir_binding = await resolve_conversation_workdir_binding(
            conversation=conversation,
            uid=uid_str,
            db=db,
        )
    elif (
        workdir_binding.uid != uid_str
        or workdir_binding.conversation_id != conversation.id
        or workdir_binding.thread_id != conversation.thread_id
        or workdir_binding.project_id != conversation.project_id
    ):
        raise RuntimeError("传入的 Workdir 绑定与 Conversation 不一致")
    if result := await existing_intake_result(workdir_binding):
        return result
    existing_requests = await repo.list_queued(
        uid=uid_str,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    existing_head = existing_requests[0] if existing_requests else None
    active_run = await AgentRunRepository(db).get_active_run_by_thread_for_user(
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
        uid=uid_str,
    )
    latest_run = await AgentRunRepository(db).get_latest_chat_or_resume_run(
        uid=uid_str,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if latest_run is not None and latest_run.status == "interrupted":
        raise _queue_conflict("run_interrupted", "线程正在等待用户回答或审批")
    if policy == "steer" and active_run is not None and not await _is_steerable_message_run(db=db, run=active_run):
        raise _queue_conflict("run_not_steerable", "当前运行不支持引导")
    if policy == "steer" and await repo.get_pending_steer(
        uid=uid_str,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    ):
        raise _queue_conflict("steer_already_pending", "线程已有等待执行的引导请求")

    # reject 表示“不能立即成为并派发 FIFO 队头就拒绝”。
    reject_without_immediate_dispatch = policy == "reject" and (active_run is not None or existing_head is not None)
    if reject_without_immediate_dispatch:
        request_status = REQUEST_STATUS_REJECTED
        delivery_status = DELIVERY_STATUS_REJECTED
        input_payload = {}
    else:
        request_status = REQUEST_STATUS_QUEUED
        delivery_status = DELIVERY_STATUS_QUEUED
        conversation_model_spec = (conversation.extra_metadata or {}).get("model_spec")
        requested_model_spec = (
            model_spec if isinstance(model_spec, str) and model_spec.strip() else conversation_model_spec
        )
        resolved_model_spec, resolved_tool_approval_mode = await resolve_agent_run_config(
            requested_model_spec, tool_approval_mode, agent_item, agent_backend, db
        )
        input_payload = {
            "model_spec": resolved_model_spec,
            "tool_approval_mode": resolved_tool_approval_mode,
        }

    run_input_message = input_message.with_metadata(
        _build_message_metadata(request_id=request_id, source=source, input_message=input_message, meta=meta)
    )
    try:
        async with db.begin_nested():
            attachment_file_ids = _normalize_attachment_file_ids(meta.get("attachment_file_ids"))
            if not reject_without_immediate_dispatch and attachment_file_ids:
                bound_attachments = await ConversationRepository(db).bind_attachments_to_request(
                    conversation.id,
                    request_id,
                    attachment_file_ids,
                )
                bound_ids = {str(item.get("file_id")) for item in bound_attachments}
                missing_ids = [file_id for file_id in attachment_file_ids if file_id not in bound_ids]
                if missing_ids:
                    raise HTTPException(
                        status_code=422,
                        detail=f"附件不存在、已被使用或已被删除: {', '.join(missing_ids)}",
                    )
            persisted_message = await create_agent_run_input_message(
                db=db,
                conversation_id=conversation.id,
                request_id=request_id,
                input_message=run_input_message,
                delivery_status=delivery_status,
            )
            persisted_request = await repo.create(
                request_id=request_id,
                uid=uid_str,
                agent_slug=agent_slug,
                conversation_thread_id=thread_id,
                source=source,
                channel=channel,
                external_id=external_id,
                origin_metadata=origin_metadata,
                queue_policy=policy,
                input_message_id=persisted_message.id,
                input_payload=input_payload,
                status=request_status,
            )
    except IntegrityError:
        if result := await existing_intake_result(workdir_binding):
            return result
        raise

    if not reject_without_immediate_dispatch:
        if policy != "reject":
            await ConversationRepository(db).set_model_spec(conversation, resolved_model_spec)
        dispatched = await _dispatch_ready_head(
            db=db,
            uid=uid_str,
            agent_slug=agent_slug,
            thread_id=thread_id,
            conversation_id=conversation.id,
            workdir_binding=workdir_binding,
            expected_request_id=request_id if policy == "reject" else None,
        )
        if dispatched and dispatched.request_id == request_id:
            if policy == "reject":
                await ConversationRepository(db).set_model_spec(conversation, resolved_model_spec)
            return IntakeResult(
                request_id=request_id,
                status=REQUEST_STATUS_DISPATCHED,
                queue_policy=policy,
                message_id=persisted_message.id,
                thread_id=thread_id,
                run_id=dispatched.run_id,
                workdir_binding=dispatched.workdir_binding,
            )

        if policy == "reject":
            persisted_request.status = REQUEST_STATUS_REJECTED
            persisted_request.input_payload = {}
            persisted_request.updated_at = utc_now_naive()
            persisted_message.delivery_status = DELIVERY_STATUS_REJECTED
            await db.flush()
            return IntakeResult(
                request_id=request_id,
                status=REQUEST_STATUS_REJECTED,
                queue_policy=policy,
                message_id=persisted_message.id,
                thread_id=thread_id,
                workdir_binding=workdir_binding,
            )

    if reject_without_immediate_dispatch:
        return IntakeResult(
            request_id=request_id,
            status=REQUEST_STATUS_REJECTED,
            queue_policy=policy,
            message_id=persisted_message.id,
            thread_id=thread_id,
            workdir_binding=workdir_binding,
        )

    return IntakeResult(
        request_id=request_id,
        status=REQUEST_STATUS_QUEUED,
        queue_policy=policy,
        message_id=persisted_message.id,
        thread_id=thread_id,
        queue_position=await repo.get_queue_position(request_id),
        workdir_binding=workdir_binding,
    )


async def steer_queued_request(
    *,
    request_id: str,
    current_uid: str,
    db: AsyncSession,
) -> IntakeResult:
    """把普通 Chat 排队请求提升为下一条执行的 Steer。"""
    repo = AgentRunRequestRepository(db)
    existing = await repo.get_by_request_id(request_id)
    if existing is None or existing.uid != str(current_uid):
        raise HTTPException(status_code=404, detail={"code": "request_not_found", "message": "请求不存在"})

    await _get_thread_conversation(
        db=db,
        uid=existing.uid,
        agent_slug=existing.agent_slug,
        thread_id=existing.conversation_thread_id,
        lock=True,
    )
    request = await repo.lock_by_request_id(request_id)
    if request is None or request.uid != str(current_uid):
        raise HTTPException(status_code=404, detail={"code": "request_not_found", "message": "请求不存在"})
    if request.queue_policy == "steer" and request.status == REQUEST_STATUS_QUEUED:
        return await _build_existing_intake_result(
            repo=repo,
            request=request,
            uid=request.uid,
            agent_slug=request.agent_slug,
            thread_id=request.conversation_thread_id,
            source=request.source,
            channel=request.channel,
            external_id=request.external_id,
            queue_policy="steer",
        )
    if request.status != REQUEST_STATUS_QUEUED or request.queue_policy != "enqueue" or request.source != "chat":
        raise _queue_conflict("request_not_queued", "只有普通 Chat 排队请求可以升级为引导")

    pending_steer = await repo.get_pending_steer(
        uid=request.uid,
        agent_slug=request.agent_slug,
        conversation_thread_id=request.conversation_thread_id,
    )
    if pending_steer and pending_steer.request_id != request_id:
        raise _queue_conflict("steer_already_pending", "线程已有等待执行的引导请求")

    active_run = await AgentRunRepository(db).get_active_run_by_thread_for_user(
        uid=request.uid,
        agent_slug=request.agent_slug,
        conversation_thread_id=request.conversation_thread_id,
    )
    if active_run is None or not await _is_steerable_message_run(db=db, run=active_run):
        raise _queue_conflict("run_not_steerable", "当前运行不支持引导")

    request.queue_policy = "steer"
    request.updated_at = utc_now_naive()
    await db.flush()
    return IntakeResult(
        request_id=request.request_id,
        status=request.status,
        queue_policy=request.queue_policy,
        message_id=request.input_message_id,
        thread_id=request.conversation_thread_id,
        queue_position=1,
    )


async def should_end_run_for_steer(run_id: str) -> bool:
    """判断当前 Chat Run 是否应在模型调用前让位给 Steer。"""
    async with pg_manager.get_async_session_context() as db:
        run = await AgentRunRepository(db).get_run(run_id)
        if run is None or not await _is_steerable_message_run(db=db, run=run):
            return False
        request = await AgentRunRequestRepository(db).get_pending_steer(
            uid=run.uid,
            agent_slug=run.agent_slug,
            conversation_thread_id=run.conversation_thread_id,
        )
        return request is not None


async def finalize_intake(
    *,
    db: AsyncSession,
    intake: IntakeResult,
) -> None:
    """调用方在 intake_request 后提交事务，并条件性将派发的 run 投入 ARQ。"""
    binding = intake.workdir_binding
    if binding is None:
        raise RuntimeError(f"Request {intake.request_id} 缺少 Workdir 绑定，无法完成 intake")
    dispatch = (
        DispatchResult(
            request_id=intake.request_id,
            run_id=intake.run_id,
            workdir_binding=binding,
        )
        if intake.status == REQUEST_STATUS_DISPATCHED and intake.run_id
        else None
    )
    if dispatch:
        await finalize_dispatch(db=db, dispatch=dispatch)
        return
    await db.commit()
    if binding.materialize_managed:
        ensure_bound_user_workdir(binding.uid, binding.workdir_path)


async def finalize_dispatch(
    *,
    db: AsyncSession,
    dispatch: DispatchResult,
) -> None:
    """提交事务并物化 Workdir，随后才把已创建的 run 投递给 ARQ。"""
    await db.commit()
    binding = dispatch.workdir_binding
    if binding.materialize_managed:
        ensure_bound_user_workdir(binding.uid, binding.workdir_path)
    await enqueue_agent_run(dispatch.run_id)


async def dispatch_next_request(
    *,
    uid: str,
    agent_slug: str,
    thread_id: str,
) -> str | None:
    """派发线程队头请求。自管会话，提交后投递 ARQ。

    供 run 完成后的下一个请求派发和恢复扫描调用。
    """
    run_id = None
    workdir_binding = None
    async with pg_manager.get_async_session_context() as db:
        conversation = await ConversationRepository(db).lock_conversation_by_thread_id(thread_id)
        if not _conversation_matches(conversation, uid=uid, agent_slug=agent_slug):
            return None
        workdir_binding = await resolve_conversation_workdir_binding(
            conversation=conversation,
            uid=str(uid),
            db=db,
        )
        active_run = await AgentRunRepository(db).get_active_run_by_thread_for_user(
            uid=str(uid),
            agent_slug=agent_slug,
            conversation_thread_id=thread_id,
        )
        if active_run:
            if active_run.status == "pending":
                run_id = active_run.id
        else:
            dispatch = await _dispatch_ready_head(
                db=db,
                uid=str(uid),
                agent_slug=agent_slug,
                thread_id=thread_id,
                conversation_id=conversation.id,
                workdir_binding=workdir_binding,
            )
            if dispatch:
                run_id = dispatch.run_id

    if run_id:
        if workdir_binding is None:
            raise RuntimeError(f"Conversation {thread_id} 缺少 Workdir 绑定，无法派发 Run")
        if workdir_binding.materialize_managed:
            ensure_bound_user_workdir(workdir_binding.uid, workdir_binding.workdir_path)
        await enqueue_agent_run(run_id)
        return run_id
    return None


async def recover_pending_dispatches() -> None:
    """恢复 pending 投递及 completed hook 留下的 ready 队列。"""
    async with pg_manager.get_async_session_context() as db:
        pending_result = await db.execute(
            select(AgentRun.uid, AgentRun.agent_slug, AgentRun.conversation_thread_id).where(
                AgentRun.status == "pending"
            )
        )
        scopes_result = await db.execute(
            select(
                AgentRunRequest.uid,
                AgentRunRequest.agent_slug,
                AgentRunRequest.conversation_thread_id,
            )
            .where(AgentRunRequest.status == REQUEST_STATUS_QUEUED)
            .distinct()
        )
        scopes = {tuple(row) for row in pending_result.all()}
        scopes.update(tuple(row) for row in scopes_result.all())

    recovered = await asyncio.gather(
        *(
            dispatch_next_request(uid=uid, agent_slug=agent_slug, thread_id=thread_id)
            for uid, agent_slug, thread_id in scopes
        ),
        return_exceptions=True,
    )
    for result in recovered:
        if isinstance(result, BaseException):
            logger.error(f"Failed to recover pending run scope: {result}")
            continue
        run_id = result
        if run_id:
            logger.info(f"Recovered pending run or queue: {run_id}")


async def cancel_queued_request(
    *,
    request_id: str,
    current_uid: str,
    db: AsyncSession,
) -> str:
    """取消一个 queued 请求；已 dispatched 的不可取消。

    返回最终状态字符串。请求不存在或越权返回 404。
    先锁定 Conversation，再在 ``SELECT ... FOR UPDATE`` 后判断最终请求状态；
    Steer 在仍有活跃 Run 时拒绝取消，避免与 Middleware 安全点竞争。
    """
    repo = AgentRunRequestRepository(db)
    existing = await repo.get_by_request_id(request_id)
    if existing is None or existing.uid != str(current_uid):
        raise HTTPException(status_code=404, detail="请求不存在")

    await _get_thread_conversation(
        db=db,
        uid=existing.uid,
        agent_slug=existing.agent_slug,
        thread_id=existing.conversation_thread_id,
        lock=True,
    )

    request = await repo.lock_by_request_id(request_id)
    if request is None or request.uid != str(current_uid):
        raise HTTPException(status_code=404, detail="请求不存在")
    if request.status == REQUEST_STATUS_DISPATCHED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "request_already_dispatched",
                "message": "请求已派发，请通过 run 取消接口取消正在进行的运行",
                "run_id": request.dispatched_run_id,
            },
        )
    if request.status in REQUEST_TERMINAL_STATUSES:
        return request.status
    if request.queue_policy == "steer":
        active_run = await AgentRunRepository(db).get_active_run_by_thread_for_user(
            uid=request.uid,
            agent_slug=request.agent_slug,
            conversation_thread_id=request.conversation_thread_id,
        )
        if active_run is not None:
            raise _queue_conflict("steer_in_progress", "引导已等待当前运行结束，暂时不能取消")
    request.status = REQUEST_STATUS_CANCELLED
    request.updated_at = utc_now_naive()
    await db.flush()
    return REQUEST_STATUS_CANCELLED


async def get_request(*, db: AsyncSession, request_id: str, uid: str) -> dict | None:
    """按 request_id 查询请求（含 uid 归属校验）。"""
    repo = AgentRunRequestRepository(db)
    request = await repo.get_by_request_id(request_id)
    if not request or request.uid != str(uid):
        return None
    return request.to_dict()


async def get_thread_queue_snapshot(*, db: AsyncSession, uid: str, agent_slug: str, thread_id: str) -> dict:
    """读取队列请求与最小状态投影。"""
    await _get_thread_conversation(db=db, uid=uid, agent_slug=agent_slug, thread_id=thread_id)
    repo = AgentRunRequestRepository(db)
    items = await repo.list_queued(uid=str(uid), agent_slug=agent_slug, conversation_thread_id=thread_id)

    message_ids = [request.input_message_id for request in items if request.input_message_id is not None]
    contents: dict[int, str] = {}
    if message_ids:
        result = await db.execute(select(Message.id, Message.content).where(Message.id.in_(message_ids)))
        contents = {row[0]: row[1] for row in result.all()}

    requests = []
    for position, request in enumerate(items, start=1):
        data = request.to_dict()
        if request.input_message_id is not None:
            data["content"] = contents.get(request.input_message_id, "")
        data["queue_position"] = position
        requests.append(data)
    status, metadata = await _get_queue_state(
        db=db,
        uid=str(uid),
        agent_slug=agent_slug,
        thread_id=thread_id,
        head=items[0] if items else None,
    )
    return {"requests": requests, "queue": {"status": status, **metadata}}


async def continue_thread_queue(
    *,
    db: AsyncSession,
    uid: str,
    agent_slug: str,
    thread_id: str,
) -> DispatchResult:
    """在同一事务内确认 paused 状态并派发 FIFO 队头。"""
    conversation = await _get_thread_conversation(
        db=db,
        uid=uid,
        agent_slug=agent_slug,
        thread_id=thread_id,
        lock=True,
    )
    repo = AgentRunRequestRepository(db)
    head = await repo.get_queue_head(
        uid=str(uid),
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if not head:
        raise _queue_conflict("queue_empty", "队列为空")

    status, _ = await _get_queue_state(
        db=db,
        uid=str(uid),
        agent_slug=agent_slug,
        thread_id=thread_id,
        head=head,
    )
    if status == "running":
        raise _queue_conflict("run_active", "线程已有正在执行的运行")
    if status == "interrupted":
        raise _queue_conflict("run_interrupted", "线程正在等待用户回答或审批")
    if status != "paused":
        raise _queue_conflict("queue_not_paused", "当前队列不需要人工继续")

    workdir_binding = await resolve_conversation_workdir_binding(
        conversation=conversation,
        uid=str(uid),
        db=db,
    )
    dispatched = await _dispatch_locked_head(
        db=db,
        head=head,
        uid=str(uid),
        agent_slug=agent_slug,
        thread_id=thread_id,
        conversation_id=conversation.id,
        workdir_binding=workdir_binding,
    )
    if dispatched:
        return dispatched

    active_run = await AgentRunRepository(db).get_active_run_by_thread_for_user(
        uid=str(uid),
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if active_run:
        raise _queue_conflict("run_active", "线程已有正在执行的运行")
    raise _queue_conflict("queue_not_paused", "当前队列状态已变化")


async def stream_request_events(
    *,
    request_id: str,
    uid: str,
    db_session_factory,
) -> AsyncIterator[str]:
    """Request SSE：发送 queued 心跳、位置变化，dispatched 时发送 run_created 并结束。"""
    started_at = utc_now_naive()
    last_heartbeat_ts = started_at
    last_position = -1

    try:
        while True:
            async with db_session_factory() as db:
                repo = AgentRunRequestRepository(db)
                request = await repo.get_by_request_id(request_id)
                if not request or request.uid != str(uid):
                    yield format_sse({"request_id": request_id, "message": "请求不存在"}, event="error")
                    return

                if request.status == REQUEST_STATUS_DISPATCHED:
                    yield format_sse(
                        {
                            "request_id": request_id,
                            "run_id": request.dispatched_run_id,
                            "stream_url": f"/api/agent/runs/{request.dispatched_run_id}/events",
                        },
                        event="run_created",
                    )
                    return

                if request.status in REQUEST_TERMINAL_STATUSES:
                    yield format_sse(
                        {"request_id": request_id, "status": request.status},
                        event=request.status,
                    )
                    return

                # queued: 用 COUNT 查询位置（O(1)），仅在变化时上报
                position = await repo.get_queue_position_for(request)
                if position != last_position:
                    last_position = position
                    yield format_sse(
                        {"request_id": request_id, "status": REQUEST_STATUS_QUEUED, "position": position},
                        event=REQUEST_STATUS_QUEUED,
                    )

            now = utc_now_naive()
            if (now - last_heartbeat_ts).total_seconds() >= SSE_HEARTBEAT_SECONDS:
                yield format_heartbeat()
                last_heartbeat_ts = now

            if (now - started_at).total_seconds() >= SSE_MAX_CONNECTION_MINUTES * 60:
                return

            await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        return


def _queue_conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


async def _build_existing_intake_result(
    *,
    repo: AgentRunRequestRepository,
    request: AgentRunRequest,
    uid: str,
    agent_slug: str,
    thread_id: str,
    source: str,
    channel: str,
    external_id: str | None,
    queue_policy: str,
    workdir_binding: WorkdirBinding | None = None,
) -> IntakeResult:
    expected_scope = (str(uid), agent_slug, thread_id, source, channel, external_id, queue_policy)
    actual_scope = (
        request.uid,
        request.agent_slug,
        request.conversation_thread_id,
        request.source,
        request.channel,
        request.external_id,
        request.queue_policy,
    )
    if actual_scope != expected_scope:
        raise _queue_conflict("request_id_conflict", "request_id 已用于其他请求作用域")
    return IntakeResult(
        request_id=request.request_id,
        status=request.status,
        queue_policy=request.queue_policy,
        message_id=request.input_message_id,
        thread_id=request.conversation_thread_id,
        run_id=request.dispatched_run_id,
        queue_position=await repo.get_queue_position(request.request_id)
        if request.status == REQUEST_STATUS_QUEUED
        else None,
        workdir_binding=workdir_binding,
    )


def _build_message_metadata(
    *, request_id: str, source: str, input_message: AgentRunInputMessage, meta: dict
) -> dict[str, Any]:
    """构建 Message.extra_metadata：request_id + source + raw_message + 附加上下文。"""
    metadata: dict[str, Any] = {"request_id": request_id}
    if source:
        metadata["source"] = source
    if channel := meta.get("channel"):
        metadata["channel"] = channel
    if raw_message := input_message.raw_message():
        metadata["raw_message"] = raw_message
    if attachment_file_ids := meta.get("attachment_file_ids"):
        metadata["attachment_file_ids"] = attachment_file_ids
    if isinstance(meta.get("agent_invocation_meta"), dict):
        metadata["agent_invocation_meta"] = meta["agent_invocation_meta"]
    if meta.get("tool_approval_mode") is not None:
        metadata["tool_approval_mode"] = meta["tool_approval_mode"]
    return metadata


def _normalize_attachment_file_ids(value: object) -> list[str]:
    """规范化请求附件 ID，保持原始顺序并去重。"""
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for file_id in value:
        current = str(file_id).strip()
        if current and current not in seen:
            seen.add(current)
            normalized.append(current)
    return normalized


async def _is_steerable_message_run(*, db: AsyncSession, run: AgentRun) -> bool:
    """确认 Run 正在运行且来自支持 Steer 的消息入口。"""
    if run.status != "running" or run.run_type != "chat":
        return False
    request = await AgentRunRequestRepository(db).get_by_request_id(run.request_id)
    return request is not None and request.source in {"chat", "channel"}


async def _get_thread_conversation(
    *,
    db: AsyncSession,
    uid: str,
    agent_slug: str,
    thread_id: str,
    lock: bool = False,
):
    repo = ConversationRepository(db)
    conversation = (
        await repo.lock_conversation_by_thread_id(thread_id)
        if lock
        else await repo.get_conversation_by_thread_id(thread_id)
    )
    if _conversation_matches(conversation, uid=uid, agent_slug=agent_slug):
        return conversation
    raise HTTPException(status_code=404, detail="对话线程不存在")


def _conversation_matches(conversation, *, uid: str, agent_slug: str) -> bool:
    """线程归属校验：存在、未删除、归属当前用户与 agent。"""
    return (
        conversation is not None
        and conversation.uid == str(uid)
        and conversation.status != "deleted"
        and conversation.agent_id == agent_slug
    )


async def _get_queue_state(
    *,
    db: AsyncSession,
    uid: str,
    agent_slug: str,
    thread_id: str,
    head: AgentRunRequest | None,
) -> tuple[str, dict]:
    """基于队头、active run 与最新顶层 run 派生队列状态。"""
    if head is None:
        return "idle", {"paused_reason": None, "blocking_run_id": None, "can_continue": False}

    run_repo = AgentRunRepository(db)
    active_run = await run_repo.get_active_run_by_runtime_scope_for_user(uid=str(uid), runtime_scope_id=thread_id)
    if active_run:
        return "running", {"paused_reason": None, "blocking_run_id": None, "can_continue": False}

    latest_run = await run_repo.get_latest_chat_or_resume_run(
        uid=str(uid), agent_slug=agent_slug, conversation_thread_id=thread_id
    )
    if latest_run and latest_run.status == "interrupted":
        return "interrupted", {
            "paused_reason": None,
            "blocking_run_id": latest_run.id,
            "can_continue": False,
        }

    if latest_run and latest_run.status in {"failed", "cancelled"} and latest_run.finished_at is None:
        raise RuntimeError(f"Terminal run {latest_run.id} is missing finished_at")

    if latest_run and latest_run.status in {"failed", "cancelled"} and head.created_at <= latest_run.finished_at:
        return "paused", {
            "paused_reason": latest_run.status,
            "blocking_run_id": latest_run.id,
            "can_continue": True,
        }

    return "ready", {"paused_reason": None, "blocking_run_id": None, "can_continue": False}


async def _dispatch_ready_head(
    *,
    db: AsyncSession,
    uid: str,
    agent_slug: str,
    thread_id: str,
    conversation_id: int,
    workdir_binding: WorkdirBinding,
    expected_request_id: str | None = None,
) -> DispatchResult | None:
    """只在 ready 状态派发 FIFO 队头。"""
    repo = AgentRunRequestRepository(db)
    head = await repo.get_queue_head(
        uid=uid,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if not head:
        return None
    if expected_request_id is not None and head.request_id != expected_request_id:
        return None
    status, _ = await _get_queue_state(
        db=db,
        uid=uid,
        agent_slug=agent_slug,
        thread_id=thread_id,
        head=head,
    )
    if status != "ready":
        return None
    return await _dispatch_locked_head(
        db=db,
        head=head,
        uid=uid,
        agent_slug=agent_slug,
        thread_id=thread_id,
        conversation_id=conversation_id,
        workdir_binding=workdir_binding,
    )


async def _dispatch_locked_head(
    *,
    db: AsyncSession,
    head: AgentRunRequest,
    uid: str,
    agent_slug: str,
    thread_id: str,
    conversation_id: int,
    workdir_binding: WorkdirBinding,
) -> DispatchResult | None:
    """将已锁定的 queued 队头转换为 AgentRun，不提交事务。"""
    repo = AgentRunRequestRepository(db)
    run_repo = AgentRunRepository(db)
    run_id = str(uuid.uuid4())
    try:
        async with db.begin_nested():
            await run_repo.create_run(
                run_id=run_id,
                conversation_thread_id=thread_id,
                runtime_scope_id=thread_id,
                agent_slug=agent_slug,
                uid=uid,
                request_id=head.request_id,
                input_payload=head.input_payload or {},
                source=head.source,
                channel=head.channel,
                external_id=head.external_id,
                origin_metadata=head.origin_metadata,
                conversation_id=conversation_id,
                run_type="chat",
                input_message_id=head.input_message_id,
            )
            msg = await db.get(Message, head.input_message_id)
            if msg:
                msg.run_id = run_id
                msg.delivery_status = DELIVERY_STATUS_DISPATCHED
            await db.flush()
            await repo.mark_dispatched(head.request_id, run_id=run_id)
    except IntegrityError as exc:
        cause = getattr(exc.orig, "__cause__", None)
        constraint_name = getattr(exc.orig, "constraint_name", None) or getattr(cause, "constraint_name", None)
        if constraint_name != "uq_agent_runs_one_active_per_thread":
            raise
        logger.info(f"Dispatch conflict for request {head.request_id}, keeping queued")
        return None

    return DispatchResult(
        request_id=head.request_id,
        run_id=run_id,
        workdir_binding=workdir_binding,
    )
