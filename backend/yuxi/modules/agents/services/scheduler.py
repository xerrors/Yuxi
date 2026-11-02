"""Thread 锁下领取持久输入，并在提交后投递 Run。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.models.attachments import AgentAttachment
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.services.run_creation import create_turn_run
from yuxi.modules.workspace.paths import ensure_bound_user_workdir
from yuxi.modules.workspace.services.bindings import WorkdirBinding, resolve_session_workdir_binding


@dataclass(frozen=True, slots=True)
class Dispatch:
    """记录 owning transaction 已创建、尚待提交投递的 Run。"""

    run_id: str
    binding: WorkdirBinding


async def claim_next_input(*, db: AsyncSession, agent_session: Session, binding: WorkdirBinding | None = None) -> Dispatch | None:
    """在已锁 Thread 上领取优先队头，原子建立 Turn 与首段 Run。"""
    if agent_session.status != "active" or agent_session.queue_paused:
        return None
    if await AgentRunRepository(db).has_thread_cleanup(agent_session.thread_id):
        return None

    from yuxi.modules.agents.models.cooperation import CooperationRuntime

    runtime = await db.get(CooperationRuntime, agent_session.tree_root_thread_id)
    if runtime is not None and runtime.stopped:
        return None
    turn_repo = AgentTurnRepository(db)
    if await turn_repo.lock_active_for_thread(thread_id=agent_session.thread_id, uid=agent_session.uid, app_id=agent_session.app_id):
        return None

    input_repo = AgentInputRepository(db)
    batch = await input_repo.ready_batch(thread_id=agent_session.thread_id, uid=agent_session.uid, app_id=agent_session.app_id)
    if not batch:
        return None
    head = batch[0]
    if binding is None:
        binding = await resolve_session_workdir_binding(agent_session=agent_session, uid=agent_session.uid, db=db)

    turn_id = str(uuid.uuid4())
    turn = await turn_repo.create(turn_id=turn_id, thread_id=agent_session.thread_id, uid=agent_session.uid, app_id=agent_session.app_id)
    run = await create_turn_run(db=db, agent_session=agent_session, turn=turn, source=head, api_key_id=head.api_key_id)
    run_id = run.id
    messages = await input_repo.consume(inputs=batch, turn_id=turn_id, run_id=run_id)
    await AgentRunRepository(db).set_input_message(run_id, messages[0].id)
    return Dispatch(run_id=run_id, binding=binding)


async def dispatch_next_input(*, uid: str, agent_slug: str, thread_id: str) -> bool:
    """自管事务领取并投递；返回本轮发布是否未失败，没有可执行工作同样为 True。"""
    async with pg_manager.get_async_session_context() as db:
        agent_session = await SessionRepository(db).lock_session_by_thread_id(thread_id)
        if agent_session is None or agent_session.uid != uid or agent_session.agent_id != agent_slug:
            return True
        from yuxi.modules.agents.services.attachments import prepare_input_attachments

        # 归档只停止执行；已接收文件仍要完成提交，领取门禁由 claim_next_input 执行。
        binding = await resolve_session_workdir_binding(agent_session=agent_session, uid=uid, db=db)
        for input_item in await AttachmentRepository(db).list_preparing_inputs(thread_id, uid, agent_session.app_id):
            if not await prepare_input_attachments(db=db, agent_session=agent_session, input_item=input_item, binding=binding):
                # 保留准备错误和来源；普通队列仍受优先队头约束。
                continue
        dispatch = await claim_next_input(db=db, agent_session=agent_session, binding=binding)

    delivered = await deliver(dispatch) if dispatch is not None else True
    from yuxi.modules.agents.services.attachments import cleanup_prepared_sources

    async with pg_manager.get_async_session_context() as db:
        await cleanup_prepared_sources(db=db, thread_id=thread_id)
    return delivered


async def deliver(dispatch: Dispatch) -> bool:
    """提交后物化目录并发布 Run；队列失败保留 pending，由恢复扫描按原身份补投。"""
    if dispatch.binding.materialize_managed:
        ensure_bound_user_workdir(dispatch.binding.uid, dispatch.binding.workdir_path)
    from yuxi.modules.agents.services.transport import enqueue_agent_run

    try:
        await enqueue_agent_run(dispatch.run_id)
    except Exception:
        logger.exception("Failed to publish committed AgentRun: {}", dispatch.run_id)
        return False
    return True


async def recover_pending_dispatches() -> None:
    """补投持久 pending Run，并领取崩溃后仍处于 ready 的队头。"""
    async with pg_manager.get_async_session_context() as db:
        pending = list((await db.execute(select(AgentRun).where(AgentRun.status == "pending"))).scalars())
        scopes = list(
            (
                await db.execute(
                    select(AgentInput.uid, AgentInput.agent_slug, AgentInput.thread_id)
                    .where(
                        (AgentInput.status == "pending")
                        | exists().where(
                            AgentAttachment.input_id == AgentInput.id,
                            (AgentAttachment.status == "preparing") | AgentAttachment.object_name.is_not(None),
                        )
                    )
                    .distinct()
                )
            ).all()
        )

    failures = 0
    for run in pending:
        try:
            async with pg_manager.get_async_session_context() as db:
                agent_session = await SessionRepository(db).get_session_by_thread_id(run.thread_id)
                if (
                    agent_session is None
                    or agent_session.uid != run.uid
                    or agent_session.agent_id != run.agent_slug
                    or agent_session.app_id != run.app_id
                    or agent_session.status != "active"
                ):
                    continue
                current = await AgentRunRepository(db).get_run(run.id)
                turn = await AgentTurnRepository(db).get_for_scope(
                    turn_id=run.turn_id,
                    thread_id=run.thread_id,
                    uid=run.uid,
                    app_id=run.app_id,
                )
                if current is None or current.status != "pending" or turn is None or turn.current_run_id != run.id:
                    continue
                binding = await resolve_session_workdir_binding(agent_session=agent_session, uid=run.uid, db=db)
            if not await deliver(Dispatch(run_id=run.id, binding=binding)):
                failures += 1
        except Exception:
            failures += 1
            logger.exception("Failed to republish pending AgentRun: {}", run.id)

    for uid, agent_slug, thread_id in scopes:
        try:
            if not await dispatch_next_input(uid=uid, agent_slug=agent_slug, thread_id=thread_id):
                failures += 1
        except Exception:
            failures += 1
            logger.exception("Failed to recover ready AgentInput: {}", thread_id)

    if failures:
        raise RuntimeError(f"待执行任务补投失败：{failures} 条")
