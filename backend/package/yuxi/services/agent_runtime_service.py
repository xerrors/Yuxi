"""Agent runtime context resolution helpers.

This module loads the configured Agent backend and prepares a ``BaseContext``
for UI/runtime operations that need the same context shape as a real run. It is
read-oriented: resolve agent config, normalize context fields and prepare
thread-specific runtime state.

It does not create ``AgentRun`` records, enqueue workers, read run results or
format external invocation responses. Those responsibilities stay in
``agent_run_service`` and ``agent_invocation_service`` so context resolution can
remain reusable by chat state queries, file views and other runtime helpers.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.buildin import agent_manager
from yuxi.agents.context import BaseContext, normalize_agent_context_config, prepare_agent_runtime_context
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.conversation_service import require_user_conversation
from yuxi.storage.postgres.models_business import User


async def resolve_agent_runtime_context(
    *,
    db: AsyncSession,
    user: User,
    agent_slug: str,
) -> BaseContext:
    agent_item = await AgentRepository(db).get_visible_by_slug(slug=agent_slug, user=user)
    if not agent_item:
        raise HTTPException(status_code=404, detail="智能体不存在")

    backend = agent_manager.get_agent(agent_item.backend_id)
    if not backend:
        raise HTTPException(status_code=404, detail="智能体后端不存在")

    context_schema = backend.context_schema
    context = context_schema(thread_id="", uid=str(user.uid))
    normalized_config = await normalize_agent_context_config(
        (agent_item.config_json or {}).get("context", {}),
        db=db,
        user=user,
        context_schema=context_schema,
    )
    context.update_from_dict(normalized_config)
    return context


async def resolve_thread_agent_runtime_context(
    *,
    thread_id: str,
    user: User,
    db: AsyncSession,
) -> BaseContext:
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(user.uid))

    runtime_context = await resolve_agent_runtime_context(
        db=db,
        user=user,
        agent_slug=conversation.agent_id,
    )
    runtime_context.thread_id = thread_id
    runtime_context.uid = str(user.uid)
    await prepare_agent_runtime_context(runtime_context)
    return runtime_context
