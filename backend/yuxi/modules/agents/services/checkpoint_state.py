"""按授权 Thread 读取 LangGraph checkpoint 与持久执行关系。"""

from __future__ import annotations

from yuxi.infrastructure.postgres.checkpointer import get_langgraph_checkpointer
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.services.cooperation import tree_snapshot
from yuxi.modules.agents.services.execution import build_pending_interrupt_payload, extract_agent_state
from yuxi.modules.identity.models import User
from yuxi.shared.errors import ResourceNotFoundError


async def _read_checkpoint_state(*, uid: str, thread_id: str) -> tuple[dict, list | None]:
    """读取完整 checkpoint 快照与同批全部中断；调用方先校验线程可见性。"""
    checkpointer = get_langgraph_checkpointer(pg_manager)
    saved = await checkpointer.aget_tuple({"configurable": {"uid": uid, "thread_id": thread_id, "checkpoint_ns": ""}})
    if saved is None:
        return {}, None

    # 面板只展示完整快照，pending writes 中的业务增量留给执行图合并。
    # 已完成工具的业务写入与旧 interrupt 同批保留；__resume__ 本身不证明任务完成。
    completed_tasks = {task_id for task_id, channel, _value in saved.pending_writes or [] if not channel.startswith("__")}
    interrupts = []
    for task_id, channel, items in saved.pending_writes or []:
        if channel == "__interrupt__" and task_id not in completed_tasks and items:
            interrupts.extend(items)
    return saved.checkpoint["channel_values"], interrupts or None


async def get_agent_state_view(
    *,
    thread_id: str,
    current_user: User,
    db,
    app_id: str | None = None,
    include_relations: bool = True,
) -> dict:
    """按用户和 APP 作用域读取 checkpoint 及持久执行关系。"""
    current_uid = str(current_user.uid)
    session_repo = SessionRepository(db)
    run_repo = AgentRunRepository(db)
    agent_session = await session_repo.get_session_by_thread_id(thread_id)
    if agent_session:
        if agent_session.uid != str(current_uid) or agent_session.app_id != app_id or agent_session.status == "deleted":
            raise ResourceNotFoundError("对话线程不存在")

        latest_run = await run_repo.get_latest_run_by_thread_for_user(thread_id, current_uid)
        # checkpoint 全量读在应用进程内反序列化，大线程可达数十秒；先结束请求级只读事务，
        # 否则该会话在反序列化期间停在 idle in transaction，占住池连接并钉住快照内的 autovacuum。
        await db.commit()
        values, interrupts = await _read_checkpoint_state(uid=current_uid, thread_id=thread_id)
        response = {"agent_state": extract_agent_state(values)}
        if latest_run and latest_run.status == "interrupted" and interrupts:
            turn = await AgentTurnRepository(db).get_for_scope(
                turn_id=latest_run.turn_id,
                thread_id=thread_id,
                uid=current_uid,
                app_id=app_id,
            )
            if (
                turn
                and turn.status == "waiting"
                and turn.current_run_id == latest_run.id
                and turn.waitpoint
                and turn.waitpoint["run_id"] == latest_run.id
            ):
                response["interrupt"] = {
                    **build_pending_interrupt_payload(interrupts, thread_id),
                    "run_id": latest_run.id,
                }
        if include_relations:
            response["agent_state"]["cooperation"] = await tree_snapshot(db, agent_session)
            response["parent_thread_id"] = agent_session.parent_thread_id
        return response

    raise ResourceNotFoundError("对话线程不存在")
