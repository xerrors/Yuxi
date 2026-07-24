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

SUPPORTED_QUEUE_POLICIES = ("enqueue", "reject", "steer")
NOT_IMPLEMENTED_QUEUE_POLICIES = ("guided", "bridge")

# Request lifecycle states.
REQUEST_STATUS_QUEUED = "queued"
REQUEST_STATUS_STEER_READY = "steer_ready"
REQUEST_STATUS_DISPATCHED = "dispatched"
REQUEST_STATUS_CANCELLED = "cancelled"
REQUEST_STATUS_REJECTED = "rejected"
REQUEST_STATUS_FAILED = "failed"
REQUEST_TERMINAL_STATUSES = frozenset({REQUEST_STATUS_CANCELLED, REQUEST_STATUS_REJECTED, REQUEST_STATUS_FAILED})

# Message delivery states aligned with messages.delivery_status.
DELIVERY_STATUS_QUEUED = "queued"
DELIVERY_STATUS_DISPATCHED = "dispatched"
DELIVERY_STATUS_COMPLETE = "complete"
DELIVERY_STATUS_REJECTED = "rejected"
DELIVERY_STATUS_FAILED = "failed"
DELIVERY_STATUS_CANCELLED = "cancelled"

# AgentRun terminal status → Message.delivery_status. ``interrupted`` 不在内：
# 被中断的请求未真正完成，保留原 delivery_status 以便 UI 区分完成 / 中断。
RUN_STATUS_TO_DELIVERY_STATUS: dict[str, str] = {
    "completed": DELIVERY_STATUS_COMPLETE,
    "failed": DELIVERY_STATUS_FAILED,
    "cancelled": DELIVERY_STATUS_CANCELLED,
}


@dataclass(frozen=True)
class IntakeResult:
    """入队决策结果。"""

    request_id: str
    status: str  # queued / dispatched / rejected
    queue_policy: str
    message_id: int | None
    thread_id: str
    run_id: str | None = None
    target_run_id: str | None = None
    # FIFO 队内位置；未在排队（dispatched/rejected/已存在）时为 None。
    queue_position: int | None = None


@dataclass(frozen=True)
class DispatchResult:
    """一次已提交前的 FIFO 队头派发结果。"""

    request_id: str
    run_id: str


@dataclass(frozen=True)
class SteerHandoff:
    """旧 Run 安全退出后的原子接替结果。"""

    target_run_id: str
    request_id: str | None = None
    new_run_id: str | None = None
    changed: bool = False


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
    queue_policy: str = "enqueue",
    input_message: AgentRunInputMessage,
    agent_item: Any,
    agent_backend: Any,
    model_spec: str | None = None,
    tool_approval_mode: str | None = None,
    meta: dict | None = None,
) -> IntakeResult:
    """创建 request + Message，尝试立即派发。

    全部 flush 在调用方事务内完成；不 commit。
    返回 IntakeResult：dispatched 时含 run_id（调用方需 commit 后 enqueue ARQ）。
    """
    policy = validate_queue_policy(queue_policy)
    if policy == "steer" and source != "chat":
        raise HTTPException(status_code=422, detail="queue_policy 'steer' 仅支持主会话 Chat")
    meta = meta or {}
    uid_str = str(uid)
    repo = AgentRunRequestRepository(db)

    async def existing_intake_result() -> IntakeResult | None:
        """幂等：相同 request_id 已存在时返回既有 request/run 视图，不存在返回 None。"""
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
            queue_policy=policy,
        )

    if result := await existing_intake_result():
        return result

    conversation = await _get_thread_conversation(
        db=db,
        uid=uid_str,
        agent_slug=agent_slug,
        thread_id=thread_id,
        lock=True,
    )
    if result := await existing_intake_result():
        return result
    existing_requests = await repo.list_queued(
        uid=uid_str,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    existing_head = existing_requests[0] if existing_requests else None
    run_repo = AgentRunRepository(db)
    active_run = await run_repo.get_active_run_by_thread_for_user(
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
        uid=uid_str,
    )
    latest_run = await run_repo.get_latest_chat_or_resume_run(
        uid=uid_str,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if latest_run is not None and latest_run.status == "interrupted":
        raise _queue_conflict("run_interrupted", "线程正在等待用户回答或审批")

    target_run_id = None
    if policy == "steer":
        if active_run is None:
            raise _queue_conflict("steer_target_missing", "当前没有可接替的运行")
        active_run = await run_repo.lock_run(active_run.id)
        if active_run is None:
            raise _queue_conflict("steer_target_missing", "当前没有可接替的运行")
        if active_run.status == "interrupted":
            raise _queue_conflict("run_interrupted", "线程正在等待用户回答或审批")
        if active_run.status == "pending" or active_run.run_type != "chat":
            raise _queue_conflict("run_not_steerable", "当前运行尚未进入可引导状态")
        if active_run.status == "cancel_requested":
            raise _queue_conflict("run_cancel_pending", "当前运行正在取消")
        if active_run.status != "running":
            raise _queue_conflict("run_not_steerable", "当前运行不可引导")
        pending_steer = await repo.get_pending_steer(
            uid=uid_str,
            agent_slug=agent_slug,
            conversation_thread_id=thread_id,
            lock=True,
        )
        if pending_steer:
            raise _queue_conflict("steer_already_pending", "线程已有等待生效的引导请求")
        target_run_id = active_run.id

    # reject 表示“不能立即成为并派发 FIFO 队头就拒绝”。
    reject_without_immediate_dispatch = policy == "reject" and (active_run is not None or existing_head is not None)
    if reject_without_immediate_dispatch:
        request_status = REQUEST_STATUS_REJECTED
        delivery_status = DELIVERY_STATUS_REJECTED
        input_payload = {}
    else:
        request_status = REQUEST_STATUS_QUEUED
        delivery_status = DELIVERY_STATUS_QUEUED
        resolved_model_spec, resolved_tool_approval_mode = resolve_agent_run_config(
            model_spec, tool_approval_mode, agent_item, agent_backend
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
                queue_policy=policy,
                input_message_id=persisted_message.id,
                input_payload=input_payload,
                status=request_status,
                target_run_id=target_run_id,
            )
    except IntegrityError:
        if result := await existing_intake_result():
            return result
        raise

    if not reject_without_immediate_dispatch and policy != "steer":
        dispatched = await _dispatch_ready_head(
            db=db,
            uid=uid_str,
            agent_slug=agent_slug,
            thread_id=thread_id,
            conversation_id=conversation.id,
            expected_request_id=request_id if policy == "reject" else None,
        )
        if dispatched and dispatched.request_id == request_id:
            return IntakeResult(
                request_id=request_id,
                status=REQUEST_STATUS_DISPATCHED,
                queue_policy=policy,
                message_id=persisted_message.id,
                thread_id=thread_id,
                run_id=dispatched.run_id,
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
            )

    if reject_without_immediate_dispatch:
        return IntakeResult(
            request_id=request_id,
            status=REQUEST_STATUS_REJECTED,
            queue_policy=policy,
            message_id=persisted_message.id,
            thread_id=thread_id,
        )

    return IntakeResult(
        request_id=request_id,
        status=REQUEST_STATUS_QUEUED,
        queue_policy=policy,
        message_id=persisted_message.id,
        thread_id=thread_id,
        target_run_id=target_run_id,
        queue_position=await repo.get_queue_position(request_id),
    )


async def finalize_intake(*, db: AsyncSession, intake: IntakeResult) -> None:
    """调用方在 intake_request 后提交事务，并条件性将派发的 run 投入 ARQ。"""
    dispatch = (
        DispatchResult(request_id=intake.request_id, run_id=intake.run_id)
        if intake.status == REQUEST_STATUS_DISPATCHED and intake.run_id
        else None
    )
    await finalize_dispatch(db=db, dispatch=dispatch)


async def finalize_dispatch(*, db: AsyncSession, dispatch: DispatchResult | None) -> None:
    """提交当前事务；提交成功后才把已创建的 run 投递给 ARQ。"""
    await db.commit()
    if dispatch:
        await enqueue_agent_run(dispatch.run_id)


async def steer_queued_request(
    *,
    request_id: str,
    current_uid: str,
    db: AsyncSession,
) -> IntakeResult:
    """把已有普通 queued request 原地升级为 Steer。"""
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
    if request.queue_policy == "steer" and request.status in {REQUEST_STATUS_QUEUED, REQUEST_STATUS_STEER_READY}:
        return await _build_existing_intake_result(
            repo=repo,
            request=request,
            uid=request.uid,
            agent_slug=request.agent_slug,
            thread_id=request.conversation_thread_id,
            source=request.source,
            queue_policy="steer",
        )
    if request.status != REQUEST_STATUS_QUEUED or request.queue_policy != "enqueue" or request.source != "chat":
        raise _queue_conflict("request_not_queued", "只有普通 Chat 排队请求可以升级为引导")

    run_repo = AgentRunRepository(db)
    latest_run = await run_repo.get_latest_chat_or_resume_run(
        uid=request.uid,
        agent_slug=request.agent_slug,
        conversation_thread_id=request.conversation_thread_id,
    )
    if latest_run is not None and latest_run.status == "interrupted":
        raise _queue_conflict("run_interrupted", "线程正在等待用户回答或审批")
    active_run = await run_repo.get_active_run_by_thread_for_user(
        uid=request.uid,
        agent_slug=request.agent_slug,
        conversation_thread_id=request.conversation_thread_id,
    )
    if active_run is None:
        raise _queue_conflict("steer_target_missing", "当前没有可接替的运行")
    active_run = await run_repo.lock_run(active_run.id)
    if active_run is None:
        raise _queue_conflict("steer_target_missing", "当前没有可接替的运行")
    if active_run.status == "interrupted":
        raise _queue_conflict("run_interrupted", "线程正在等待用户回答或审批")
    if active_run.status == "cancel_requested":
        raise _queue_conflict("run_cancel_pending", "当前运行正在取消")
    if active_run.status != "running" or active_run.run_type != "chat":
        raise _queue_conflict("run_not_steerable", "当前运行不可引导")

    pending_steer = await repo.get_pending_steer(
        uid=request.uid,
        agent_slug=request.agent_slug,
        conversation_thread_id=request.conversation_thread_id,
        lock=True,
    )
    if pending_steer and pending_steer.request_id != request_id:
        raise _queue_conflict("steer_already_pending", "线程已有等待生效的引导请求")

    request.queue_policy = "steer"
    request.target_run_id = active_run.id
    request.updated_at = utc_now_naive()
    await db.flush()
    return IntakeResult(
        request_id=request.request_id,
        status=request.status,
        queue_policy=request.queue_policy,
        message_id=request.input_message_id,
        thread_id=request.conversation_thread_id,
        target_run_id=request.target_run_id,
    )


async def mark_pending_steer_ready(target_run_id: str) -> bool:
    """在 ``before_model`` 安全点持久化两阶段交接标记。"""
    async with pg_manager.get_async_session_context() as db:
        run_repo = AgentRunRepository(db)
        target = await run_repo.get_run(target_run_id)
        if target is None:
            return False
        conversation = await ConversationRepository(db).lock_conversation_by_thread_id(target.conversation_thread_id)
        if not _conversation_matches(conversation, uid=target.uid, agent_slug=target.agent_slug):
            return False
        target = await run_repo.lock_run(target_run_id)
        request = await AgentRunRequestRepository(db).get_pending_steer_for_target(target_run_id, lock=True)
        if request is None or target is None:
            return False
        if request.status == REQUEST_STATUS_STEER_READY:
            return True
        if target.status != "running" or target.run_type != "chat" or request.status != REQUEST_STATUS_QUEUED:
            return False
        request.status = REQUEST_STATUS_STEER_READY
        request.updated_at = utc_now_naive()
        await db.flush()
        return True


async def finalize_ready_steer_handoff(target_run_id: str) -> SteerHandoff:
    """旧 Graph 完全退出后，原子终结旧 Run 并创建 replacement Run 事实。"""
    dispatch = None
    request_id = None
    async with pg_manager.get_async_session_context() as db:
        run_repo = AgentRunRepository(db)
        target = await run_repo.get_run(target_run_id)
        if target is None:
            return SteerHandoff(target_run_id=target_run_id)
        conversation = await ConversationRepository(db).lock_conversation_by_thread_id(target.conversation_thread_id)
        if not _conversation_matches(conversation, uid=target.uid, agent_slug=target.agent_slug):
            return SteerHandoff(target_run_id=target_run_id)
        target = await run_repo.lock_run(target_run_id)
        request = await AgentRunRequestRepository(db).get_pending_steer_for_target(target_run_id, lock=True)
        if target is None or request is None or request.status != REQUEST_STATUS_STEER_READY:
            return SteerHandoff(target_run_id=target_run_id)
        if target.status != "running":
            return SteerHandoff(target_run_id=target_run_id, request_id=request.request_id)

        target.status = "cancelled"
        target.error_type = "steered"
        target.error_message = "当前运行已由引导请求接替"
        target.finished_at = utc_now_naive()
        target.updated_at = target.finished_at
        if target.input_message_id:
            target_message = await db.get(Message, target.input_message_id)
            if target_message:
                target_message.delivery_status = DELIVERY_STATUS_COMPLETE
        await db.flush()
        dispatch = await _dispatch_locked_head(
            db=db,
            head=request,
            uid=target.uid,
            agent_slug=target.agent_slug,
            thread_id=target.conversation_thread_id,
            conversation_id=conversation.id,
        )
        if dispatch is None:
            raise RuntimeError(f"Steer handoff failed to create replacement for {target_run_id}")
        request_id = request.request_id

    return SteerHandoff(
        target_run_id=target_run_id,
        request_id=request_id,
        new_run_id=dispatch.run_id,
        changed=True,
    )


async def recover_steered_replacement(target_run_id: str) -> str | None:
    """旧 Run job 重试时恢复已提交但尚未执行的 replacement 投递。"""
    async with pg_manager.get_async_session_context() as db:
        run_repo = AgentRunRepository(db)
        target = await run_repo.get_run(target_run_id)
        if target is None or target.status != "cancelled" or target.error_type != "steered":
            return None

        request = await AgentRunRequestRepository(db).get_dispatched_steer_for_target(target_run_id)
        if request is None or not request.dispatched_run_id:
            raise RuntimeError(f"Steered run {target_run_id} is missing its dispatched replacement")
        replacement = await run_repo.get_run(request.dispatched_run_id)
        if replacement is None:
            raise RuntimeError(f"Steered run {target_run_id} points to a missing replacement")
        replacement_run_id = replacement.id if replacement.status == "pending" else None

    if replacement_run_id:
        await enqueue_agent_run(replacement_run_id)
    return replacement_run_id


async def settle_target_steer_after_terminal(target_run_id: str) -> str | None:
    """目标自然终结后优先派发或明确失败 Steer，并返回 replacement run id。"""
    dispatch = None
    async with pg_manager.get_async_session_context() as db:
        run_repo = AgentRunRepository(db)
        target = await run_repo.get_run(target_run_id)
        if target is None or target.status not in {"completed", "failed", "cancelled", "interrupted"}:
            return None
        conversation = await ConversationRepository(db).lock_conversation_by_thread_id(target.conversation_thread_id)
        if not _conversation_matches(conversation, uid=target.uid, agent_slug=target.agent_slug):
            return None
        target = await run_repo.lock_run(target_run_id)
        request = await AgentRunRequestRepository(db).get_pending_steer_for_target(target_run_id, lock=True)
        if target is None or request is None:
            return None
        if target.status == "completed":
            dispatch = await _dispatch_locked_head(
                db=db,
                head=request,
                uid=target.uid,
                agent_slug=target.agent_slug,
                thread_id=target.conversation_thread_id,
                conversation_id=conversation.id,
            )
            if dispatch is None:
                raise RuntimeError(f"Completed target failed to dispatch Steer {request.request_id}")
        else:
            error_codes = {
                "failed": "steer_target_failed",
                "cancelled": "steer_target_cancelled",
                "interrupted": "steer_target_interrupted",
            }
            request.status = REQUEST_STATUS_FAILED
            request.error_code = error_codes[target.status]
            request.error_message = "引导目标未在安全点正常结束"
            request.updated_at = utc_now_naive()
            message = await db.get(Message, request.input_message_id)
            if message:
                message.delivery_status = DELIVERY_STATUS_FAILED
            await db.flush()

    if dispatch:
        await enqueue_agent_run(dispatch.run_id)
        return dispatch.run_id
    return None


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
    async with pg_manager.get_async_session_context() as db:
        conversation = await ConversationRepository(db).lock_conversation_by_thread_id(thread_id)
        if not _conversation_matches(conversation, uid=uid, agent_slug=agent_slug):
            return None
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
            )
            if dispatch:
                run_id = dispatch.run_id

    if run_id:
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
        steer_targets_result = await db.execute(
            select(AgentRunRequest.target_run_id)
            .where(
                AgentRunRequest.queue_policy == "steer",
                AgentRunRequest.status.in_((REQUEST_STATUS_QUEUED, REQUEST_STATUS_STEER_READY)),
                AgentRunRequest.target_run_id.is_not(None),
            )
            .distinct()
        )
        scopes = {tuple(row) for row in pending_result.all()}
        scopes.update(tuple(row) for row in scopes_result.all())
        steer_target_ids = list(steer_targets_result.scalars().all())

    await asyncio.gather(*(settle_target_steer_after_terminal(target_run_id) for target_run_id in steer_target_ids))

    recovered = await asyncio.gather(
        *(
            dispatch_next_request(uid=uid, agent_slug=agent_slug, thread_id=thread_id)
            for uid, agent_slug, thread_id in scopes
        )
    )
    for run_id in recovered:
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
    所有状态读取与状态写入在同一 ``SELECT ... FOR UPDATE`` 内完成，
    避免无锁读后再锁写之间被并发修改。
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
    if request.status == REQUEST_STATUS_STEER_READY:
        raise _queue_conflict("steer_handoff_started", "引导已进入安全交接，不能取消")
    request.status = REQUEST_STATUS_CANCELLED
    request.updated_at = utc_now_naive()
    message = await db.get(Message, request.input_message_id)
    if message:
        message.delivery_status = DELIVERY_STATUS_CANCELLED
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
    items = await repo.list_pending(uid=str(uid), agent_slug=agent_slug, conversation_thread_id=thread_id)

    message_ids = [request.input_message_id for request in items if request.input_message_id is not None]
    contents: dict[int, str] = {}
    if message_ids:
        result = await db.execute(select(Message.id, Message.content).where(Message.id.in_(message_ids)))
        contents = {row[0]: row[1] for row in result.all()}

    requests = []
    fifo_position = 0
    for request in items:
        data = request.to_dict()
        if request.input_message_id is not None:
            data["content"] = contents.get(request.input_message_id, "")
        if request.queue_policy == "enqueue" and request.status == REQUEST_STATUS_QUEUED:
            fifo_position += 1
            data["queue_position"] = fifo_position
        else:
            data["queue_position"] = None
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

    dispatched = await _dispatch_locked_head(
        db=db,
        head=head,
        uid=str(uid),
        agent_slug=agent_slug,
        thread_id=thread_id,
        conversation_id=conversation.id,
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
    last_status = None

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
                        {
                            "request_id": request_id,
                            "status": request.status,
                            "error_code": request.error_code,
                        },
                        event=request.status,
                    )
                    return

                if request.status == REQUEST_STATUS_STEER_READY:
                    if last_status != REQUEST_STATUS_STEER_READY:
                        last_status = REQUEST_STATUS_STEER_READY
                        yield format_sse(
                            {
                                "request_id": request_id,
                                "status": REQUEST_STATUS_STEER_READY,
                                "queue_policy": "steer",
                                "target_run_id": request.target_run_id,
                            },
                            event=REQUEST_STATUS_STEER_READY,
                        )
                elif request.queue_policy == "steer":
                    if last_status != "steering":
                        last_status = "steering"
                        yield format_sse(
                            {
                                "request_id": request_id,
                                "status": REQUEST_STATUS_QUEUED,
                                "queue_policy": "steer",
                                "target_run_id": request.target_run_id,
                            },
                            event="steering",
                        )
                else:
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
    queue_policy: str,
) -> IntakeResult:
    expected_scope = (str(uid), agent_slug, thread_id, source, queue_policy)
    actual_scope = (
        request.uid,
        request.agent_slug,
        request.conversation_thread_id,
        request.source,
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
        target_run_id=request.target_run_id,
        queue_position=await repo.get_queue_position(request.request_id)
        if request.status == REQUEST_STATUS_QUEUED
        else None,
    )


def _build_message_metadata(
    *, request_id: str, source: str, input_message: AgentRunInputMessage, meta: dict
) -> dict[str, Any]:
    """构建 Message.extra_metadata：request_id + source + raw_message + 附加上下文。"""
    metadata: dict[str, Any] = {"request_id": request_id}
    if source:
        metadata["source"] = source
    if raw_message := input_message.raw_message():
        metadata["raw_message"] = raw_message
    if attachment_file_ids := meta.get("attachment_file_ids"):
        metadata["attachment_file_ids"] = attachment_file_ids
    if isinstance(meta.get("agent_invocation_meta"), dict):
        metadata["agent_invocation_meta"] = meta["agent_invocation_meta"]
    if meta.get("tool_approval_mode") is not None:
        metadata["tool_approval_mode"] = meta["tool_approval_mode"]
    return metadata


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
    active_run = await run_repo.get_active_run_by_thread_for_user(
        uid=str(uid), agent_slug=agent_slug, conversation_thread_id=thread_id
    )
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
    )


async def _dispatch_locked_head(
    *,
    db: AsyncSession,
    head: AgentRunRequest,
    uid: str,
    agent_slug: str,
    thread_id: str,
    conversation_id: int,
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
                agent_slug=agent_slug,
                uid=uid,
                request_id=head.request_id,
                input_payload=head.input_payload or {},
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

    return DispatchResult(request_id=head.request_id, run_id=run_id)
