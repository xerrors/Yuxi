"""Run 查询与安全接管点的只读 steer 检查。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.repositories.attachments import AttachmentRepository
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.attachments import readable_ready_prefix
from yuxi.modules.workspace.services.bindings import session_workdir_is_available
from yuxi.shared.errors import ResourceNotFoundError


async def should_yield_for_steer(run_id: str) -> bool:
    """供 Agent 图安全钩子查询待接管输入；终态仍在 Thread 锁内裁决。"""
    from yuxi.infrastructure.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        run = await AgentRunRepository(db).get_run(run_id)
        if run is None or run.status != "running":
            return False
        agent_session = await SessionRepository(db).get_session_by_thread_id(run.thread_id)
        if agent_session is None or agent_session.queue_paused:
            return False
        turn = await AgentTurnRepository(db).get(run.turn_id)
        if turn is None or turn.status != "running" or turn.current_run_id != run_id:
            return False
        pending = await AgentInputRepository(db).get_pending_steer(
            thread_id=run.thread_id,
            uid=run.uid,
            app_id=run.app_id,
            for_update=False,
            turn_id=turn.id,
        )
        if pending is None or await AttachmentRepository(db).has_unready(pending.id):
            return False
        return await session_workdir_is_available(agent_session=agent_session, db=db) and bool(
            await readable_ready_prefix(db=db, batch=[pending])
        )


async def get_run_snapshot(*, db: AsyncSession, scope: ActorScope, thread_id: str, run_id: str) -> dict:
    """按 Thread/Turn/APP 归属读取执行段及持久输出。"""
    agent_session = await SessionRepository(db).get_session_by_thread_id(thread_id)
    if agent_session is None or agent_session.uid != scope.uid or agent_session.app_id != scope.app_id:
        raise ResourceNotFoundError("Run 不存在")
    run = await AgentRunRepository(db).get_run(run_id)
    if run is None or run.uid != scope.uid or run.app_id != scope.app_id or run.thread_id != thread_id:
        raise ResourceNotFoundError("Run 不存在")
    turn = await AgentTurnRepository(db).get_for_scope(
        turn_id=run.turn_id,
        thread_id=run.thread_id,
        uid=scope.uid,
        app_id=scope.app_id,
    )
    if turn is None:
        raise ResourceNotFoundError("Run 不存在")
    from yuxi.modules.agents.services.public_items import serialize_public_run

    input_ids = await AgentInputRepository(db).input_ids_for_runs([run.id])
    result = serialize_public_run(run, input_ids[run.id])
    if run.output_message_id is not None:
        from yuxi.modules.agents.models.messages import Message

        message = await db.get(Message, run.output_message_id)
        if message is None or message.run_id != run.id or message.turn_id != run.turn_id:
            raise ValueError("Run 输出消息归属不一致")
        from yuxi.modules.agents.services.public_items import serialize_public_items

        result["output"] = serialize_public_items(message, run, turn.result_run_id)
    else:
        result["output"] = None
    result["langfuse_url"] = None
    if run.langfuse_trace_id:
        from yuxi.infrastructure.observability.langfuse import get_trace_url_by_id_async

        result["langfuse_url"] = await get_trace_url_by_id_async(run.langfuse_trace_id)
    return result
