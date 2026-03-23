from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.context import BaseContext
from yuxi.agents.backends import create_agent_composite_backend, get_sandbox_provider
from yuxi.repositories.agent_config_repository import AgentConfigRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.postgres.models_business import User
from yuxi.services.conversation_service import require_user_conversation


async def _resolve_filesystem_context(
    *,
    db: AsyncSession,
    user: User,
    agent_id: str,
    agent_config_id: int | None,
) -> BaseContext:
    context = BaseContext(thread_id="", user_id=str(user.id))
    repo = AgentConfigRepository(db)

    config_item = None
    if agent_config_id is not None:
        config_item = await repo.get_by_id(config_id=int(agent_config_id))
        if config_item is not None and (
            config_item.department_id != user.department_id or config_item.agent_id != agent_id
        ):
            config_item = None

    if config_item is None:
        config_item = await repo.get_or_create_default(
            department_id=user.department_id,
            agent_id=agent_id,
            created_by=str(user.id),
        )

    context.update_from_dict((config_item.config_json or {}).get("context", {}))
    return context


async def list_filesystem_entries_view(
    *,
    thread_id: str,
    path: str,
    agent_id: str | None,
    agent_config_id: int | None,
    current_user: User,
    db: AsyncSession,
) -> dict:
    if not thread_id:
        raise HTTPException(status_code=422, detail="thread_id 不能为空")

    normalized_path = (path or "/").strip() or "/"

    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_user.id))

    provider = get_sandbox_provider()
    sandbox_backend = await asyncio.to_thread(provider.acquire, thread_id)

    runtime_context = await _resolve_filesystem_context(
        db=db,
        user=current_user,
        agent_id=agent_id or conversation.agent_id,
        agent_config_id=agent_config_id,
    )
    runtime_context.thread_id = thread_id
    runtime_context.user_id = str(current_user.id)

    runtime_stub = type("RuntimeStub", (), {"context": runtime_context})()
    composite_backend = create_agent_composite_backend(runtime_stub, sandbox_backend=sandbox_backend)
    try:
        entries = await asyncio.to_thread(composite_backend.ls_info, normalized_path)
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return {"entries": entries or []}


async def read_file_content_view(
    *,
    thread_id: str,
    path: str,
    agent_id: str | None,
    agent_config_id: int | None,
    current_user: User,
    db: AsyncSession,
) -> dict:
    if not thread_id:
        raise HTTPException(status_code=422, detail="thread_id 不能为空")
    if not path:
        raise HTTPException(status_code=422, detail="path 不能为空")

    normalized_path = path.strip()

    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_user.id))

    provider = get_sandbox_provider()
    sandbox_backend = await asyncio.to_thread(provider.acquire, thread_id)

    runtime_context = await _resolve_filesystem_context(
        db=db,
        user=current_user,
        agent_id=agent_id or conversation.agent_id,
        agent_config_id=agent_config_id,
    )
    runtime_context.thread_id = thread_id
    runtime_context.user_id = str(current_user.id)

    runtime_stub = type("RuntimeStub", (), {"context": runtime_context})()
    composite_backend = create_agent_composite_backend(runtime_stub, sandbox_backend=sandbox_backend)
    try:
        responses = await asyncio.to_thread(composite_backend.download_files, [normalized_path])
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    response = responses[0] if responses else None
    if response is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if response.error == "file_not_found":
        raise HTTPException(status_code=404, detail="文件不存在")
    if response.error == "is_directory":
        raise HTTPException(status_code=400, detail="当前路径是目录")
    if response.error:
        raise HTTPException(status_code=400, detail=response.error)

    raw_content = response.content or b""
    try:
        content = raw_content.decode("utf-8")
    except UnicodeDecodeError:
        content = raw_content.decode("utf-8", errors="replace")

    return {"content": content}
