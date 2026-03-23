from __future__ import annotations

import asyncio
import io
import mimetypes
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.backends import get_sandbox_provider
from yuxi.agents.backends.sandbox_config import SKILLS_PATH, USER_DATA_PATH
from yuxi.agents.backends.skills_backend import SelectedSkillsReadonlyBackend
from yuxi.agents.middlewares.skills_middleware import normalize_selected_skills
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.storage.postgres.models_business import User
from yuxi.services.conversation_service import require_user_conversation
from yuxi.services.filesystem_service import _resolve_filesystem_context


def _normalize_path(path: str | None) -> str:
    normalized = (path or "/").strip() or "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/") if normalized not in {"/", SKILLS_PATH, USER_DATA_PATH} else normalized


def _is_user_data_path(path: str) -> bool:
    return path == USER_DATA_PATH or path.startswith(f"{USER_DATA_PATH}/")


def _is_skills_path(path: str) -> bool:
    return path == SKILLS_PATH or path.startswith(f"{SKILLS_PATH}/")


def _strip_skills_prefix(path: str) -> str:
    if path == SKILLS_PATH:
        return "/"
    return path[len(SKILLS_PATH) :] or "/"


def _remap_skills_entry(entry: dict) -> dict:
    raw_path = str(entry.get("path") or "")
    is_dir = bool(entry.get("is_dir", False))
    remapped = f"{SKILLS_PATH}{raw_path}" if raw_path != "/" else f"{SKILLS_PATH}/"
    if is_dir and not remapped.endswith("/"):
        remapped = f"{remapped}/"
    return {
        "path": remapped,
        "name": PurePosixPath(remapped.rstrip("/")).name or remapped,
        "is_dir": is_dir,
        "size": int(entry.get("size", 0) or 0),
        "modified_at": str(entry.get("modified_at", "") or ""),
    }


def _normalize_entries(entries: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for entry in entries or []:
        raw_path = str(entry.get("path") or "")
        if not raw_path:
            continue
        is_dir = bool(entry.get("is_dir", False))
        display_path = raw_path
        if is_dir and not display_path.endswith("/"):
            display_path = f"{display_path}/"
        normalized.append(
            {
                "path": display_path,
                "name": PurePosixPath(display_path.rstrip("/")).name or display_path,
                "is_dir": is_dir,
                "size": int(entry.get("size", 0) or 0),
                "modified_at": str(entry.get("modified_at", "") or ""),
            }
        )
    return normalized


async def _resolve_viewer_state(
    *,
    thread_id: str,
    agent_id: str | None,
    agent_config_id: int | None,
    current_user: User,
    db: AsyncSession,
):
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_user.id))

    runtime_context = await _resolve_filesystem_context(
        db=db,
        user=current_user,
        agent_id=agent_id or conversation.agent_id,
        agent_config_id=agent_config_id,
    )
    runtime_context.thread_id = thread_id
    runtime_context.user_id = str(current_user.id)

    provider = get_sandbox_provider()
    sandbox_backend = await asyncio.to_thread(provider.acquire, thread_id)
    selected_skills = normalize_selected_skills(getattr(runtime_context, "skills", None) or [])
    skills_backend = SelectedSkillsReadonlyBackend(selected_slugs=selected_skills)
    return sandbox_backend, skills_backend, selected_skills


async def list_viewer_filesystem_tree(
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

    normalized_path = _normalize_path(path)
    sandbox_backend, skills_backend, selected_skills = await _resolve_viewer_state(
        thread_id=thread_id,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )

    if normalized_path == "/":
        entries = [
            {"path": f"{USER_DATA_PATH}/", "name": "user-data", "is_dir": True, "size": 0, "modified_at": ""}
        ]
        if selected_skills:
            entries.append({"path": f"{SKILLS_PATH}/", "name": "skills", "is_dir": True, "size": 0, "modified_at": ""})
        return {"entries": entries}

    try:
        if _is_user_data_path(normalized_path):
            entries = await asyncio.to_thread(sandbox_backend.ls_info, normalized_path)
            return {"entries": _normalize_entries(entries)}

        if _is_skills_path(normalized_path):
            entries = await asyncio.to_thread(skills_backend.ls_info, _strip_skills_prefix(normalized_path))
            return {"entries": [_remap_skills_entry(entry) for entry in entries]}
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    raise HTTPException(status_code=400, detail=f"Access denied: '{normalized_path}' is outside viewer namespace")


async def read_viewer_file_content(
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
    normalized_path = _normalize_path(path)

    sandbox_backend, skills_backend, _selected_skills = await _resolve_viewer_state(
        thread_id=thread_id,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )

    try:
        if _is_user_data_path(normalized_path):
            responses = await asyncio.to_thread(sandbox_backend.download_files, [normalized_path])
        elif _is_skills_path(normalized_path):
            responses = await asyncio.to_thread(skills_backend.download_files, [_strip_skills_prefix(normalized_path)])
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Access denied: '{normalized_path}' is outside viewer namespace",
            )
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    response = responses[0] if responses else None
    if response is None or response.error == "file_not_found":
        raise HTTPException(status_code=404, detail="文件不存在")
    if response.error == "is_directory":
        raise HTTPException(status_code=400, detail="当前路径是目录")
    if response.error:
        raise HTTPException(status_code=400, detail=str(response.error))

    raw_content = response.content or b""
    try:
        content = raw_content.decode("utf-8")
    except UnicodeDecodeError:
        content = raw_content.decode("utf-8", errors="replace")
    return {"content": content}


async def download_viewer_file(
    *,
    thread_id: str,
    path: str,
    agent_id: str | None,
    agent_config_id: int | None,
    current_user: User,
    db: AsyncSession,
) -> StreamingResponse:
    normalized_path = _normalize_path(path)
    sandbox_backend, skills_backend, _selected_skills = await _resolve_viewer_state(
        thread_id=thread_id,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )

    try:
        if _is_user_data_path(normalized_path):
            responses = await asyncio.to_thread(sandbox_backend.download_files, [normalized_path])
        elif _is_skills_path(normalized_path):
            responses = await asyncio.to_thread(skills_backend.download_files, [_strip_skills_prefix(normalized_path)])
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Access denied: '{normalized_path}' is outside viewer namespace",
            )
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    response = responses[0] if responses else None
    if response is None or response.error == "file_not_found":
        raise HTTPException(status_code=404, detail="文件不存在")
    if response.error == "is_directory":
        raise HTTPException(status_code=400, detail="当前路径是目录")
    if response.error:
        raise HTTPException(status_code=400, detail=str(response.error))

    file_name = PurePosixPath(normalized_path).name or "download"
    media_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    stream = io.BytesIO(response.content or b"")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}",
    }
    return StreamingResponse(stream, media_type=media_type, headers=headers)
