"""线程级主动上下文压缩用例。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.backends import create_agent_composite_backend
from yuxi.agents.backends.paths import runtime_workdir_path
from yuxi.agents.backends.sandbox import ProvisionerSandboxBackend, get_sandbox_provider
from yuxi.agents.buildin import agent_manager
from yuxi.agents.context import (
    DEFAULT_SUMMARY_THRESHOLD_K,
    BaseContext,
    prepare_agent_runtime_context,
)
from yuxi.agents.middlewares import create_summary_middleware_from_context
from yuxi.agents.middlewares.token_usage import TOKEN_USAGE_CONTEXT_FIELDS
from yuxi.agents.skills.service import get_user_skills_root_dir
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
from yuxi.repositories.agent_state_repository import AgentStateRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.agent_run_service import resolve_agent_run_model_spec
from yuxi.services.workdir_service import ensure_conversation_workdir_available
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger


async def compress_thread_context(
    *,
    thread_id: str,
    current_user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    """在线程空闲时压缩 checkpoint；同线程新请求由 Conversation 行锁串行化。"""
    uid = str(current_user.uid)
    conversation = await ConversationRepository(db).lock_conversation_by_thread_id(thread_id)
    if conversation is None or conversation.uid != uid or conversation.status == "deleted":
        raise HTTPException(status_code=404, detail="对话线程不存在")

    agent_slug = conversation.agent_id
    await _ensure_thread_idle(db=db, uid=uid, agent_slug=agent_slug, thread_id=thread_id)

    agent_item = await AgentRepository(db).get_visible_by_slug(
        slug=agent_slug,
        user=current_user,
        kind="main",
    )
    if agent_item is None:
        raise HTTPException(status_code=404, detail="智能体不存在")
    agent = agent_manager.get_agent(agent_item.backend_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="智能体后端不存在")
    if "context_compression" not in getattr(agent, "capabilities", []):
        raise HTTPException(status_code=422, detail="当前智能体不支持主动上下文压缩")

    context = agent.context_schema()
    context.update_config((agent_item.config_json or {}).get("context") or {})
    model_spec = await resolve_agent_run_model_spec(
        (conversation.extra_metadata or {}).get("model_spec"),
        context.model,
        db,
    )
    workdir_path = await ensure_conversation_workdir_available(
        conversation=conversation,
        uid=uid,
        db=db,
    )
    context.update(
        {
            "uid": uid,
            "thread_id": thread_id,
            "model": model_spec,
            "runtime_scope_id": thread_id,
            "workdir_relative_path": workdir_path,
            "workdir_path": runtime_workdir_path(workdir_path),
        }
    )
    result = await _compress_agent_checkpoint_in_runtime(
        agent=agent,
        context=context,
        thread_id=thread_id,
        uid=uid,
        workdir_path=workdir_path,
    )
    await db.commit()
    return result


async def _ensure_thread_idle(*, db: AsyncSession, uid: str, agent_slug: str, thread_id: str) -> None:
    """拒绝会与 checkpoint 维护竞争的运行、等待交互和排队请求。"""
    run_repo = AgentRunRepository(db)
    active_run = await run_repo.get_active_run_by_thread_for_user(
        uid=uid,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    latest_run = await run_repo.get_latest_chat_or_resume_run(
        uid=uid,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    queued_requests = await AgentRunRequestRepository(db).list_queued(
        uid=uid,
        agent_slug=agent_slug,
        conversation_thread_id=thread_id,
    )
    if active_run is None and not queued_requests and (latest_run is None or latest_run.status != "interrupted"):
        return
    raise HTTPException(
        status_code=409,
        detail={"code": "thread_busy", "message": "线程仍有运行、交互或排队请求，暂时不能压缩"},
    )


async def _compress_agent_checkpoint_in_runtime(
    *,
    agent,
    context: BaseContext,
    thread_id: str,
    uid: str,
    workdir_path: str,
) -> dict[str, Any]:
    """在一次性 Sandbox 生命周期内压缩 checkpoint。"""
    try:
        await prepare_agent_runtime_context(context)
        await _ensure_runtime_available(thread_id=thread_id, uid=uid, workdir_path=workdir_path)
        result = await _compress_agent_checkpoint(agent=agent, context=context)
    except BaseException:
        try:
            await _release_runtime(thread_id=thread_id, uid=uid, workdir_path=workdir_path)
        except BaseException as release_error:
            logger.error(f"主动压缩失败后释放 Sandbox 失败: {release_error}")
        raise
    else:
        await _release_runtime(thread_id=thread_id, uid=uid, workdir_path=workdir_path)
        return result


async def _ensure_runtime_available(*, thread_id: str, uid: str, workdir_path: str) -> None:
    """确保主动压缩可以通过 Agent backend 写入可恢复历史。"""
    await asyncio.to_thread(get_user_skills_root_dir, uid)
    backend = ProvisionerSandboxBackend(thread_id=thread_id, uid=uid, workdir_path=workdir_path)
    await asyncio.to_thread(backend.ensure_available)


async def _release_runtime(*, thread_id: str, uid: str, workdir_path: str) -> None:
    """释放主动压缩创建或复用的 Sandbox。"""
    await asyncio.to_thread(
        get_sandbox_provider().release,
        thread_id,
        uid=uid,
        clear_cache_on_delete_failure=True,
        workdir_path=workdir_path,
    )


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
