"""按授权 Thread 读取 LangGraph checkpoint 与持久执行关系。"""

from __future__ import annotations

import json
from typing import Any

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.checkpointer import get_langgraph_checkpointer
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.models.runs import AGENT_RUN_TERMINAL_STATUSES
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.services.cooperation import tree_snapshot
from yuxi.modules.agents.services.execution import build_pending_interrupt_payload, extract_agent_state
from yuxi.modules.identity.models import User
from yuxi.shared.errors import ResourceNotFoundError

# extract_agent_state（0.8.0 面板读模型）实际消费的小通道；不存 files/todos 等旁路大字段。
AGENT_STATE_PROJECTION_CHANNELS = ("warnings", "artifacts", "token_usage")


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
    """按用户和 APP 作用域读取面板状态；终态投影新鲜时零 checkpoint，陈旧或缺失时全量读并懒回填。"""
    current_uid = str(current_user.uid)
    session_repo = SessionRepository(db)
    run_repo = AgentRunRepository(db)
    agent_session = await session_repo.get_session_by_thread_id(thread_id)
    if agent_session:
        if agent_session.uid != str(current_uid) or agent_session.app_id != app_id or agent_session.status == "deleted":
            raise ResourceNotFoundError("对话线程不存在")

        latest_run = await run_repo.get_latest_run_by_thread_for_user(thread_id, current_uid)
        projection = agent_session.agent_state_view
        # 新鲜度门与写主共用同一终态前提：非终态 Run 的标签不得放行快路径（压缩/建 Run 竞态窗内不把演进状态定格）。
        projection_fresh = isinstance(projection, dict) and (
            latest_run is None or (latest_run.status in AGENT_RUN_TERMINAL_STATUSES and projection.get("run_id") == str(latest_run.id))
        )
        if projection_fresh:
            # 快路径：投影由终态钩子、压缩刷新或懒回填写入；本请求不得触碰 checkpoint，也不得回写投影。
            values = projection.get("values") or {}
            interrupt_payload = projection.get("interrupt") if isinstance(projection.get("interrupt"), dict) else None
        else:
            # checkpoint 全量读在应用进程内反序列化，大线程可达数十秒；先结束请求级只读事务，
            # 否则该会话在反序列化期间停在 idle in transaction，占住池连接并钉住快照内的 autovacuum。
            await db.commit()
            values, interrupts = await _read_checkpoint_state(uid=current_uid, thread_id=thread_id)
            interrupt_payload = build_pending_interrupt_payload(interrupts, thread_id) if interrupts else None
        response = {"agent_state": extract_agent_state(_projection_values(values))}
        if latest_run and latest_run.status == "interrupted" and interrupt_payload:
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
                response["interrupt"] = {**interrupt_payload, "run_id": latest_run.id}
        if include_relations:
            response["agent_state"]["cooperation"] = await tree_snapshot(db, agent_session)
            response["parent_thread_id"] = agent_session.parent_thread_id

        if not projection_fresh and latest_run is not None and latest_run.status in AGENT_RUN_TERMINAL_STATUSES:
            # 懒回填：兜底读为属主本人行幂等回写，冷线程打开一次即转快路径；非终态不写，避免把演进中的状态定格成新鲜投影。
            await _save_state_projection_best_effort(
                db,
                uid=current_uid,
                thread_id=thread_id,
                payload=build_agent_state_projection(values, interrupt_payload, run_id=latest_run.id),
            )
        return response

    raise ResourceNotFoundError("对话线程不存在")


def _projection_values(values: Any) -> dict:
    """按 extract_agent_state 消费面把缺失/None 通道归一为稳定形态，快慢路径逐键一致。"""
    if not isinstance(values, dict):
        values = {}
    defaults = {"warnings": [], "artifacts": [], "token_usage": None}
    normalized = {}
    for channel in AGENT_STATE_PROJECTION_CHANNELS:
        value = values.get(channel)
        normalized[channel] = value if value is not None else defaults[channel]
    return normalized


def build_agent_state_projection(
    values: Any,
    interrupt_payload: dict | None = None,
    *,
    run_id: Any = None,
) -> dict:
    """构造面板读模型投影：小通道 + run_id 新鲜度标签 + 中断载荷。

    投影是只增读的 UI 读模型；checkpoint 仍是执行侧唯一权威状态，写主只有 Run 终态钩子、
    压缩刷新与属主打开时的懒回填。
    """
    raw = _projection_values(values)
    payload: dict[str, Any] = {"values": json.loads(json.dumps(raw, ensure_ascii=False, default=str))}
    if run_id is not None:
        payload["run_id"] = str(run_id)
    if interrupt_payload:
        payload["interrupt"] = json.loads(json.dumps(interrupt_payload, ensure_ascii=False, default=str))
    return payload


async def project_agent_state_after_terminal(
    *,
    uid: str,
    thread_id: str,
    run_id: str | None,
    values: dict | None = None,
    interrupt_payload: dict | None = None,
) -> None:
    """Run 终态/压缩后用独立会话持久化投影；缺内存态才回读一次 checkpoint，全程 best-effort 不影响调用方。"""
    try:
        if values is None:
            values, interrupts = await _read_checkpoint_state(uid=str(uid), thread_id=thread_id)
            if interrupts and not interrupt_payload:
                interrupt_payload = build_pending_interrupt_payload(interrupts, thread_id)
        payload = build_agent_state_projection(values, interrupt_payload, run_id=run_id)
        async with pg_manager.get_async_session_context() as session:
            await SessionRepository(session).save_agent_state_view(uid=str(uid), thread_id=thread_id, payload=payload)
    except Exception:
        logger.warning(f"State projection skipped: thread={thread_id}")


async def refresh_state_projection_after_compression(*, uid: str, thread_id: str) -> None:
    """压缩后回读 checkpoint 并以最新 Run 重打标签写投影。

    压缩改写 checkpoint 通道值但不产生新 Run；不重打标签则新鲜度门永远不放行快路径。
    投影是读模型，任何失败不回抛给压缩结果。
    """
    try:
        async with pg_manager.get_async_session_context() as session:
            latest = await AgentRunRepository(session).get_latest_run_by_thread_for_user(thread_id, str(uid))
        run_id = str(latest.id) if latest is not None and latest.status in AGENT_RUN_TERMINAL_STATUSES else None
    except Exception:
        logger.warning(f"Projection retag skipped: thread={thread_id}")
        run_id = None
    await project_agent_state_after_terminal(uid=uid, thread_id=thread_id, run_id=run_id)


async def _save_state_projection_best_effort(db, *, uid: str, thread_id: str, payload: dict) -> None:
    """读路径懒回填：任何失败只回滚告警，不得改变本次响应。"""
    try:
        await SessionRepository(db).save_agent_state_view(uid=uid, thread_id=thread_id, payload=payload)
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        logger.warning(f"Lazy projection backfill failed: thread={thread_id}")
