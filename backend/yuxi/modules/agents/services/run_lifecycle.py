"""拥有 Run 结算锁序、终态投影及持久清理；用例显式拥有提交点。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.runs import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.runtime.sandbox import get_sandbox_provider
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.cooperation_notifications import notify_turn_state
from yuxi.modules.agents.services.queue_control import pause_pending_queue
from yuxi.modules.agents.services.run_creation import create_turn_run
from yuxi.modules.agents.services.scheduler import Dispatch, dispatch_next_input
from yuxi.modules.agents.services.thread_access import require_thread
from yuxi.modules.agents.services.tracing import finish_turn_observation_if_terminal
from yuxi.modules.agents.services.transport import clear_cancel_signal
from yuxi.modules.workspace.services.bindings import resolve_session_workdir_binding


@dataclass(frozen=True, slots=True)
class RunSettlement:
    """返回本事务固定的 Run 结束原因及后续投递目标。"""

    status: str
    changed: bool
    next_dispatch: Dispatch | None = None


@dataclass(frozen=True, slots=True)
class LockedRunLifecycle:
    """同一事务已取得的 Thread、Turn、Input 与 Run 边界。"""

    agent_session: Session
    turn: AgentTurn
    run: AgentRun
    pending_steer: AgentInput | None


async def lock_run_lifecycle(
    db: AsyncSession,
    *,
    run_id: str,
    thread_id: str | None = None,
    turn_id: str | None = None,
    allow_steer: bool = False,
    require_current: bool = True,
) -> LockedRunLifecycle:
    """按 Thread→Turn→Input→Run 锁序准备结算，并在锁下重读归属。"""
    repo = AgentRunRepository(db)
    candidate = await repo.get_run(run_id)
    if candidate is None:
        raise ValueError("AgentRun 不存在")
    if thread_id is not None and candidate.thread_id != thread_id:
        raise ValueError("AgentRun 输出必须属于同一 thread")
    if turn_id is not None and candidate.turn_id != turn_id:
        raise ValueError("AgentRun 输出必须属于同一 Turn")
    agent_session = await SessionRepository(db).lock_session_by_thread_id(candidate.thread_id)
    if agent_session is None or agent_session.uid != candidate.uid or agent_session.app_id != candidate.app_id:
        raise ValueError("Run 的 Thread 归属不一致")
    turn = await AgentTurnRepository(db).get_for_scope(
        turn_id=candidate.turn_id,
        thread_id=candidate.thread_id,
        uid=candidate.uid,
        app_id=candidate.app_id,
        for_update=True,
    )
    if turn is None or (require_current and turn.current_run_id != run_id):
        raise ValueError("AgentRun 不是当前 Turn 的执行段")
    if allow_steer:
        pending_steer = await _lock_ready_steer(db, agent_session=agent_session, turn=turn)
    else:
        await AgentInputRepository(db).get_pending_steer(thread_id=candidate.thread_id, uid=candidate.uid, app_id=candidate.app_id)
        pending_steer = None
    run = await repo.lock_run_for_user(run_id, candidate.uid)
    if run is None or run.thread_id != agent_session.thread_id or run.turn_id != turn.id or run.app_id != agent_session.app_id:
        raise ValueError("Run 的生命周期归属不一致")
    return LockedRunLifecycle(agent_session=agent_session, turn=turn, run=run, pending_steer=pending_steer)


async def settle_run(
    *,
    db: AsyncSession,
    run: AgentRun,
    worker_id: str | None,
    status: str,
    token_usage: dict | None,
    waitpoint: dict | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> RunSettlement:
    """为不写 checkpoint 输出的用例取得生命周期边界并结算，保留调用方事务。"""
    locked = await lock_run_lifecycle(db, run_id=run.id, allow_steer=status == "completed")
    return await settle_checkpoint(
        db=db,
        locked=locked,
        worker_id=worker_id,
        status=status,
        token_usage=token_usage,
        waitpoint=waitpoint,
        error_type=error_type,
        error_message=error_message,
    )


async def settle_checkpoint(
    *,
    db: AsyncSession,
    locked: LockedRunLifecycle,
    worker_id: str | None,
    status: str,
    token_usage: dict | None,
    waitpoint: dict | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> RunSettlement:
    """在生命周期边界内更新 Run/Turn；提交和接续投递由用例拥有。"""
    run, agent_session, turn, pending_steer = locked.run, locked.agent_session, locked.turn, locked.pending_steer
    turn_repo = AgentTurnRepository(db)
    run_repo = AgentRunRepository(db)
    if turn.current_run_id != run.id:
        raise ValueError("Run 不是目标 Turn 的当前执行段")
    if agent_session.uid != run.uid or agent_session.app_id != run.app_id:
        raise ValueError("Run 的 Thread 归属不一致")

    if status == "completed" and pending_steer is not None:
        terminal, changed = await run_repo.set_terminal_status(run.id, status="yielded", token_usage=token_usage, worker_id=worker_id)
        if terminal is None or not changed:
            raise ValueError("Steer 接管前当前 Run 所有权已失效")
        next_dispatch = await _consume_steer(db=db, agent_session=agent_session, turn=turn, previous=run, pending=pending_steer)
        return RunSettlement(status="yielded", changed=True, next_dispatch=next_dispatch)

    if status == "interrupted" and turn.status == "running":
        if not waitpoint or waitpoint.get("run_id") != run.id:
            raise ValueError("等待终态缺少绑定当前 Run 的等待点")
        terminal, changed = await run_repo.set_terminal_status(
            run.id,
            status="interrupted",
            token_usage=token_usage,
            worker_id=worker_id,
            error_type=error_type,
            error_message=error_message,
        )
        if terminal is None or not changed:
            raise ValueError("等待终态的 Run 所有权已失效")
        await turn_repo.set_waiting(turn, run_id=run.id, waitpoint=waitpoint)

        await notify_turn_state(db, run)
        return RunSettlement(status="interrupted", changed=True)

    if status == "completed" and turn.status == "running":
        terminal, changed = await run_repo.set_terminal_status(run.id, status="completed", token_usage=token_usage, worker_id=worker_id)
        if terminal is None or not changed:
            raise ValueError("完成终态的 Run 所有权已失效")
        await turn_repo.set_terminal(turn, status="completed", result_run_id=run.id)

        await notify_turn_state(db, run)
        return RunSettlement(status="completed", changed=True)

    if status in {"failed", "cancelled"}:
        terminal, changed = await run_repo.set_terminal_status(
            run.id,
            status=status,
            token_usage=token_usage,
            worker_id=worker_id,
            error_type=error_type,
            error_message=error_message,
        )
        if terminal is None or not changed:
            return RunSettlement(status=terminal.status if terminal else status, changed=False)
        await pause_pending_queue(db, agent_session)
        if status == "failed":
            await turn_repo.set_terminal(turn, status="failed")
        elif turn.status != "cancelling":
            await turn_repo.set_cancelling(turn)

        await notify_turn_state(db, run)
        return RunSettlement(status=status, changed=True)

    raise ValueError(f"Turn 状态 {turn.status} 不能接受 Run 终态 {status}")


async def reconcile_expired_run(db: AsyncSession, *, run_id: str, now: datetime | None = None) -> AgentRun | None:
    """在共同锁序下收敛失联 Run，保留过期 lease 的独立裁决与审计结果。"""
    locked = await lock_run_lifecycle(db, run_id=run_id, require_current=False)
    run = await AgentRunRepository(db).reconcile_expired_lease(run_id, now=now)
    if run is None:
        return None
    if locked.turn.current_run_id == run.id:
        await pause_pending_queue(db, locked.agent_session)
        await AgentTurnRepository(db).set_terminal(locked.turn, status="failed")
        await notify_turn_state(db, run)
    return run


async def finish_run_lifecycle(run: AgentRun) -> None:
    """按已提交状态清除取消信号、派发下一输入并结束 Turn observation。"""
    if run.status == "cancelled":
        await clear_cancel_signal(run.id)
    if run.status in {"completed", "failed", "cancelled"} and not run.runtime_cleanup_pending:
        await dispatch_next_input(uid=run.uid, agent_slug=run.agent_slug, thread_id=run.thread_id)
    if run.status in TERMINAL_RUN_STATUSES:
        await finish_turn_observation_if_terminal(run.turn_id)


async def complete_run_runtime_cleanup(run: AgentRun) -> bool:
    """确认该 Run 拥有的命令与环境已释放后才解除持久清理责任。"""
    async with pg_manager.get_async_session_context() as db:
        current = await db.scalar(select(AgentRun).where(AgentRun.id == run.id).with_for_update(key_share=True))
        if current is None:
            raise RuntimeError("Run 的 cleanup Owner 不存在")
        if current.runtime_cleanup_pending:
            # 与 retry 领取持同一 Run 锁；取消也必须等待外部清理结束后才能释放。
            await await_io(asyncio.to_thread(get_sandbox_provider().cleanup_run, current.uid, current.id))
            current.runtime_cleanup_pending = False
            await db.flush()
    if current.status == "cancelled":
        await finish_cancelled_run(current)
    return True


async def settle_waiting_cancel(*, thread_id: str, turn_id: str, uid: str, app_id: str | None) -> bool:
    """清理等待 checkpoint，再把 cancelling Turn 变为 cancelled。"""
    async with pg_manager.get_async_session_context() as db:
        scope = ActorScope(uid=uid, app_id=app_id)
        # 与其他清理和新轮领取串行；checkpoint 按 Thread 保存，末尾复查无法撤销迟到的清理。
        await require_thread(db=db, scope=scope, thread_id=thread_id, lock=True)
        turn = await AgentTurnRepository(db).get_for_scope(turn_id=turn_id, thread_id=thread_id, uid=uid, app_id=app_id, for_update=True)
        if turn is None or turn.status != "cancelling" or not turn.waitpoint:
            return False
        run = await AgentRunRepository(db).get_run(turn.current_run_id)
        if run is None or run.status != "interrupted":
            return False
        await complete_run_runtime_cleanup(run)
        await _clear_waitpoint_checkpoint(run)
        await AgentTurnRepository(db).set_terminal(turn, status="cancelled")

        await notify_turn_state(db, run)
    await finish_turn_observation_if_terminal(turn_id)
    return True


async def finish_cancelled_run(run: AgentRun) -> bool:
    """当前取消 Run 的必要清理完成后，直接结束所属 Turn。"""
    async with pg_manager.get_async_session_context() as db:
        scope = ActorScope(uid=run.uid, app_id=run.app_id)
        await require_thread(db=db, scope=scope, thread_id=run.thread_id, lock=True)
        turn = await AgentTurnRepository(db).get_for_scope(
            turn_id=run.turn_id,
            thread_id=run.thread_id,
            uid=run.uid,
            app_id=run.app_id,
            for_update=True,
        )
        if turn is None or turn.status != "cancelling" or turn.current_run_id != run.id:
            return False
        current = await AgentRunRepository(db).get_run(run.id)
        if current.status != "cancelled" or current.runtime_cleanup_pending:
            return False
        await AgentTurnRepository(db).set_terminal(turn, status="cancelled")
        await notify_turn_state(db, current)
    await finish_turn_observation_if_terminal(turn.id)
    return True


async def reconcile_cancelling_turns() -> list[str]:
    """补偿清理进程失联后仍占用线程的 cancelling Turn。"""
    async with pg_manager.get_async_session_context() as db:
        candidates = list((await db.execute(select(AgentTurn).where(AgentTurn.status == "cancelling"))).scalars())
    settled = []
    failures = 0
    for candidate in candidates:
        try:
            run_id = candidate.current_run_id
            async with pg_manager.get_async_session_context() as db:
                run = await AgentRunRepository(db).get_run(run_id)
            if run is None:
                continue
            if run.status == "interrupted" and candidate.waitpoint:
                if await settle_waiting_cancel(
                    thread_id=candidate.thread_id,
                    turn_id=candidate.id,
                    uid=candidate.uid,
                    app_id=candidate.app_id,
                ):
                    settled.append(candidate.id)
            elif run.status == "cancelled" and not run.runtime_cleanup_pending:
                if await finish_cancelled_run(run):
                    settled.append(candidate.id)
        except Exception:
            failures += 1
            logger.exception("Failed to reconcile cancelling Turn: {}", candidate.id)
    if failures:
        raise RuntimeError(f"cancelling Turn recovery failed for {failures} records")
    return settled


async def _lock_ready_steer(db: AsyncSession, *, agent_session: Session, turn: AgentTurn) -> AgentInput | None:
    """在 Thread/Turn 锁之后锁定 steer，裁决附件就绪，再允许取得 Run 锁。"""
    if turn.status != "running" or agent_session.queue_paused:
        return None
    pending = await AgentInputRepository(db).get_pending_steer(
        thread_id=agent_session.thread_id,
        uid=agent_session.uid,
        app_id=agent_session.app_id,
        turn_id=turn.id,
    )
    if pending is None or await AttachmentRepository(db).has_unready(pending.id):
        return None
    return pending


async def _consume_steer(*, db: AsyncSession, agent_session: Session, turn: AgentTurn, previous: AgentRun, pending) -> Dispatch:
    """消费 Thread 优先批次，在当前 Turn 建立下一执行段。"""
    input_repo = AgentInputRepository(db)
    batch = await input_repo.ready_batch(thread_id=previous.thread_id, uid=previous.uid, app_id=previous.app_id, turn_id=turn.id)
    if not batch or batch[0].id != pending.id or batch[0].kind != "steer":
        raise ValueError("Steer 队头已变化或尚未就绪")
    run = await create_turn_run(
        db=db, agent_session=agent_session, turn=turn, source=pending, previous=previous, api_key_id=pending.api_key_id, input_batch=batch
    )
    run_id = run.id
    messages = await input_repo.consume(inputs=batch, turn_id=turn.id, run_id=run_id)
    await AgentRunRepository(db).set_input_message(run_id, messages[0].id)
    binding = await resolve_session_workdir_binding(agent_session=agent_session, uid=previous.uid, db=db)
    return Dispatch(run_id=run_id, binding=binding)


async def _clear_waitpoint_checkpoint(run: AgentRun) -> None:
    """为取消中的 Run 装配原图并清理等待 checkpoint。"""
    from yuxi.modules.agents.repositories.definitions import AgentRepository
    from yuxi.modules.agents.runtime.agent_backends import get_agent_backend
    from yuxi.modules.agents.runtime.checkpoint_cleanup import cancel_waitpoint_checkpoint
    from yuxi.modules.workspace.runtime_paths import runtime_workdir_path
    from yuxi.modules.workspace.services.bindings import resolve_session_workdir_binding

    async with pg_manager.get_async_session_context() as db:
        agent_item = await AgentRepository(db).get_by_slug(run.agent_slug)
        if agent_item is None:
            raise ValueError("等待点 Agent 不存在")
        backend = get_agent_backend(agent_item.backend_id)
        agent_session = await require_thread(
            db=db,
            scope=ActorScope(uid=run.uid, app_id=run.app_id),
            thread_id=run.thread_id,
        )
        binding = await resolve_session_workdir_binding(agent_session=agent_session, uid=run.uid, db=db)

    context = backend.context_schema()
    context.update_config(run.input_payload["context_snapshot"])
    context.update(
        {
            "tool_approval_mode": run.input_payload["context_snapshot"]["tool_approval_mode"],
            "thread_id": run.thread_id,
            "uid": run.uid,
            "run_id": run.id,
            "worker_id": "waitpoint-cleanup",
            "runtime_scope_id": run.runtime_scope_id,
            "workdir_relative_path": binding.workdir_path,
            "workdir_path": runtime_workdir_path(binding.workdir_path),
        }
    )
    # 持久 Run 与 Thread 的完整归属已验证；清理不取得资源使用授权。
    graph = await backend.get_graph(context=context, checkpoint_only=True)
    config = {"configurable": {"uid": run.uid, "thread_id": run.thread_id}}
    await cancel_waitpoint_checkpoint(graph, config)
