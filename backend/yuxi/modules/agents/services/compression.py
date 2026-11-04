"""线程级主动上下文压缩用例。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.agents.repositories.state import AgentStateRepository
from yuxi.modules.agents.runtime.agent_backends import AgentBackendNotFoundError, get_agent_backend
from yuxi.modules.agents.runtime.context import DEFAULT_SUMMARY_THRESHOLD_K, BaseContext, prepare_agent_runtime_context
from yuxi.modules.agents.runtime.middlewares import create_summary_middleware_from_context
from yuxi.modules.agents.runtime.middlewares.token_usage import TOKEN_USAGE_CONTEXT_FIELDS
from yuxi.modules.agents.runtime.sandbox.backend import create_agent_composite_backend
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.thread_access import require_thread
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.runtime_paths import runtime_workdir_path
from yuxi.modules.workspace.services.bindings import ensure_session_workdir_available
from yuxi.shared.errors import ResourceNotFoundError, StateConflictError


async def compress_thread_context(
    *,
    thread_id: str,
    current_user: User,
    db: AsyncSession,
    app_id: str | None = None,
) -> dict[str, Any]:
    """在线程空闲时压缩 checkpoint；同线程新请求由 Session 行锁串行化。"""
    uid = str(current_user.uid)
    agent_session = await require_thread(db=db, scope=ActorScope(uid=uid, app_id=app_id), thread_id=thread_id, lock=True)
    if agent_session.status != "active":
        raise ResourceNotFoundError("对话线程不存在")

    agent_slug = agent_session.agent_id
    await _ensure_thread_idle(db=db, thread_id=thread_id)

    agent_item = await AgentRepository(db).get_visible_by_slug(
        slug=agent_slug,
        user=current_user,
    )
    if agent_item is None:
        raise ResourceNotFoundError("智能体不存在")
    try:
        agent = get_agent_backend(agent_item.backend_id)
    except AgentBackendNotFoundError as exc:
        raise ResourceNotFoundError(str(exc)) from exc

    context = agent.context_schema()
    configured = agent_session.config_snapshot
    if configured is None:
        raise ValueError("Session 缺少配置快照")
    context.update_config(configured)
    model_spec = context.model
    workdir_path = await ensure_session_workdir_available(
        agent_session=agent_session,
        uid=uid,
        db=db,
    )
    context.update(
        {
            "tool_approval_mode": configured["tool_approval_mode"],
            "uid": uid,
            "thread_id": thread_id,
            "model": model_spec,
            "workdir_relative_path": workdir_path,
            "workdir_path": runtime_workdir_path(workdir_path),
        }
    )
    await prepare_agent_runtime_context(context)
    result = await _compress_agent_checkpoint(agent=agent, context=context)
    await db.commit()
    return result


async def _ensure_thread_idle(*, db: AsyncSession, thread_id: str) -> None:
    """在 Thread 锁内拒绝活跃 Turn 和待消费 Input。"""
    active_turn = await db.scalar(
        select(AgentTurn.id)
        .where(
            AgentTurn.thread_id == thread_id,
            AgentTurn.status.in_(("running", "waiting", "cancelling")),
        )
        .limit(1)
    )
    pending_input = await db.scalar(select(AgentInput.id).where(AgentInput.thread_id == thread_id, AgentInput.status == "pending").limit(1))
    if active_turn is None and pending_input is None:
        return
    raise StateConflictError("线程仍有运行、交互或排队输入，暂时不能压缩", code="thread_busy")


async def _compress_agent_checkpoint(*, agent, context: BaseContext) -> dict[str, Any]:
    """使用当前 Agent 配置生成摘要并通过 canonical graph 更新 checkpoint。"""
    graph = await agent.get_graph(context=context)
    compressor = create_summary_middleware_from_context(
        context,
        backend=create_agent_composite_backend(context),
    )
    state_repository = AgentStateRepository(
        graph,
        uid=str(context.uid),
        thread_id=str(context.thread_id),
    )
    values = await state_repository.get_values()
    update, result = await compressor.aforce_summarize(values)
    if update:
        trigger_tokens = getattr(context, "summary_threshold", DEFAULT_SUMMARY_THRESHOLD_K) * 1024
        await state_repository.update(
            _with_compression_usage(
                update,
                previous_values=values,
                result=result,
                summary_trigger_tokens=trigger_tokens,
            )
        )
    return result


def _with_compression_usage(
    update: dict[str, Any],
    *,
    previous_values: dict[str, Any],
    result: dict[str, Any],
    summary_trigger_tokens: int,
) -> dict[str, Any]:
    """把主动压缩结果合并进下一轮上下文压力指标。"""
    previous_usage = previous_values.get("token_usage")
    token_usage = {
        key: value
        for key, value in (previous_usage.items() if isinstance(previous_usage, dict) else ())
        if key not in TOKEN_USAGE_CONTEXT_FIELDS
    }
    token_usage.update(
        {
            "compression": dict(result),
            "summary_active": True,
            "summary_trigger_tokens": summary_trigger_tokens,
        }
    )
    return {**update, "token_usage": token_usage}
