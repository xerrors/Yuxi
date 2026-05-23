from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.backends import create_agent_composite_backend
from yuxi.agents.backends.sandbox.backend import _looks_like_binary
from yuxi.agents.buildin import agent_manager
from yuxi.agents.context import BaseContext, normalize_agent_context_config, prepare_agent_runtime_context
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.conversation_service import require_user_conversation
from yuxi.storage.postgres.models_business import User


async def _resolve_filesystem_context(
    *,
    db: AsyncSession,
    user: User,
    bound_agent_id: str,
) -> BaseContext:
    agent_item = await AgentRepository(db).get_visible_by_slug(slug=bound_agent_id, user=user)
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


async def _resolve_filesystem_state(
    *,
    thread_id: str,
    user: User,
    db: AsyncSession,
):
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(user.uid))

    runtime_context = await _resolve_filesystem_context(
        db=db,
        user=user,
        bound_agent_id=conversation.agent_id,
    )
    runtime_context.thread_id = thread_id
    runtime_context.uid = str(user.uid)
    await prepare_agent_runtime_context(runtime_context)

    return conversation, runtime_context


async def list_filesystem_entries_view(
    *,
    thread_id: str,
    path: str,
    current_user: User,
    db: AsyncSession,
) -> dict:
    if not thread_id:
        raise HTTPException(status_code=422, detail="thread_id 不能为空")

    normalized_path = (path or "/").strip() or "/"

    _conversation, runtime_context = await _resolve_filesystem_state(
        thread_id=thread_id,
        user=current_user,
        db=db,
    )

    runtime_stub = type("RuntimeStub", (), {"context": runtime_context})()
    composite_backend = create_agent_composite_backend(runtime_stub)
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
    current_user: User,
    db: AsyncSession,
) -> dict:
    if not thread_id:
        raise HTTPException(status_code=422, detail="thread_id 不能为空")
    if not path:
        raise HTTPException(status_code=422, detail="path 不能为空")

    normalized_path = path.strip()

    _conversation, runtime_context = await _resolve_filesystem_state(
        thread_id=thread_id,
        user=current_user,
        db=db,
    )

    runtime_stub = type("RuntimeStub", (), {"context": runtime_context})()
    composite_backend = create_agent_composite_backend(runtime_stub)
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
    if response.error == "read_failed":
        raise HTTPException(status_code=400, detail="文件读取失败")
    if response.error:
        raise HTTPException(status_code=400, detail=response.error)

    raw_content = response.content or b""
    if _looks_like_binary(raw_content):
        raise HTTPException(status_code=400, detail="当前文件是二进制文件，不能按文本读取")
    try:
        content = raw_content.decode("utf-8")
    except UnicodeDecodeError:
        content = raw_content.decode("utf-8", errors="replace")

    return {"content": content}
