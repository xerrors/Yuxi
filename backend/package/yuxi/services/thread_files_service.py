from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from yuxi.agents.backends.sandbox import (
    ensure_thread_dirs,
    resolve_virtual_path,
    sandbox_outputs_dir,
    sandbox_uploads_dir,
    sandbox_user_data_dir,
    sandbox_workspace_dir,
    virtual_path_for_thread_file,
)
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.services.conversation_service import require_user_conversation
from yuxi.services.mention_search_service import invalidate_mention_cache, invalidate_workspace_mention_cache
from yuxi.utils.datetime_utils import utc_isoformat_from_timestamp
from yuxi.utils.paths import VIRTUAL_PATH_PREFIX


def _get_virtual_root() -> str:
    """Return the virtual root exposed by the thread-files API."""
    return "/" + VIRTUAL_PATH_PREFIX.strip("/")


def _thread_file_entry(
    thread_id: str,
    uid: str,
    child: Path,
    *,
    directory_paths_end_with_slash: bool = False,
) -> dict[str, Any]:
    stat = child.stat()
    child_virtual_path = virtual_path_for_thread_file(thread_id, child, uid=uid)
    if directory_paths_end_with_slash and child.is_dir() and not child_virtual_path.endswith("/"):
        child_virtual_path = f"{child_virtual_path}/"
    return {
        "path": child_virtual_path,
        "name": child.name,
        "is_dir": child.is_dir(),
        "size": stat.st_size if child.is_file() else 0,
        "modified_at": utc_isoformat_from_timestamp(stat.st_mtime),
        "artifact_url": None
        if child.is_dir()
        else f"/api/chat/thread/{thread_id}/artifacts/{child_virtual_path.lstrip('/')}",
    }


async def list_thread_files_view(
    *,
    thread_id: str,
    current_uid: str,
    db,
    path: str | None = None,
    recursive: bool = False,
) -> dict:
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_uid))
    uid = str(conversation.uid)

    ensure_thread_dirs(thread_id, uid)
    virtual_path = path or _get_virtual_root()
    try:
        actual_path = resolve_virtual_path(thread_id, virtual_path, uid=uid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not actual_path.exists():
        return {"path": virtual_path, "files": []}
    if not actual_path.is_dir():
        raise HTTPException(status_code=400, detail="path must be a directory")

    if recursive:
        if virtual_path.rstrip("/") == _get_virtual_root():
            return await asyncio.to_thread(
                _list_user_data_root_entries,
                thread_id,
                uid,
                virtual_path,
                recursive=True,
            )
        return await asyncio.to_thread(_list_files_recursive, thread_id, uid, actual_path, virtual_path)

    if virtual_path.rstrip("/") == _get_virtual_root():
        return await asyncio.to_thread(_list_user_data_root_entries, thread_id, uid, virtual_path)

    entries = await asyncio.to_thread(_list_directory_entries, thread_id, uid, actual_path)

    return {"path": virtual_path, "files": entries}


def _list_directory_entries(thread_id: str, uid: str, actual_path: Path) -> list[dict[str, Any]]:
    return [
        _thread_file_entry(thread_id, uid, child)
        for child in sorted(actual_path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    ]


def _list_user_data_root_entries(thread_id: str, uid: str, virtual_path: str, recursive: bool = False) -> dict:
    """List the thread root and inject the user workspace entry if needed."""
    entries: list[dict[str, Any]] = []
    thread_root = sandbox_user_data_dir(thread_id)
    for child in sorted(thread_root.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        entry = _thread_file_entry(thread_id, uid, child, directory_paths_end_with_slash=True)
        entries.append(entry)
        if recursive and child.is_dir():
            nested = _list_files_recursive(thread_id, uid, child, entry["path"])
            entries.extend(nested["files"])

    workspace_dir = sandbox_workspace_dir(thread_id, uid)
    workspace_virtual_path = virtual_path_for_thread_file(thread_id, workspace_dir, uid=uid)
    if workspace_virtual_path.rstrip("/") not in {str(entry["path"]).rstrip("/") for entry in entries}:
        # workspace lives outside the per-thread root, so expose it as a top-level entry.
        entry = _thread_file_entry(thread_id, uid, workspace_dir, directory_paths_end_with_slash=True)
        entries.append(entry)
        if recursive:
            nested = _list_files_recursive(thread_id, uid, workspace_dir, entry["path"])
            entries.extend(nested["files"])
    return {"path": virtual_path, "files": entries}


def _list_files_recursive(thread_id: str, uid: str, actual_path: Path, virtual_path: str) -> dict:
    """Recursively scan a directory while preserving viewer virtual paths."""
    entries: list[dict[str, Any]] = []

    def _scan_dir(base_actual_path: Path):
        try:
            for child in sorted(base_actual_path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                entry = _thread_file_entry(thread_id, uid, child)
                entries.append(entry)
                if child.is_dir():
                    _scan_dir(child)
        except PermissionError:
            pass

    _scan_dir(actual_path)
    return {"path": virtual_path, "files": entries}


async def read_thread_file_content_view(
    *,
    thread_id: str,
    current_uid: str,
    db,
    path: str,
    offset: int = 0,
    limit: int = 2000,
) -> dict:
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_uid))
    uid = str(conversation.uid)

    try:
        actual_path = resolve_virtual_path(thread_id, path, uid=uid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not actual_path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    if not actual_path.is_file():
        raise HTTPException(status_code=400, detail="path must be a file")

    text = await asyncio.to_thread(actual_path.read_text, encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(0, int(offset))
    count = min(max(1, int(limit)), 5000)
    selected = lines[start : start + count]

    return {
        "path": path,
        "content": selected,
        "offset": start,
        "limit": count,
        "total_lines": len(lines),
        "artifact_url": f"/api/chat/thread/{thread_id}/artifacts/{path.lstrip('/')}",
    }


async def resolve_thread_artifact_view(
    *,
    thread_id: str,
    current_uid: str,
    db,
    path: str,
) -> Path:
    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_uid))
    uid = str(conversation.uid)

    ensure_thread_dirs(thread_id, uid)

    normalized = "/" + path.lstrip("/")
    try:
        actual_path = resolve_virtual_path(thread_id, normalized, uid=uid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not actual_path.exists():
        raise HTTPException(status_code=404, detail="artifact not found")
    if not actual_path.is_file():
        raise HTTPException(status_code=400, detail="artifact path is not a file")

    resolved_path = actual_path.resolve()
    workspace_root = sandbox_workspace_dir(thread_id, uid).resolve()
    uploads_root = sandbox_uploads_dir(thread_id).resolve()
    outputs_root = sandbox_outputs_dir(thread_id).resolve()
    if not (
        resolved_path.is_relative_to(workspace_root)
        or resolved_path.is_relative_to(uploads_root)
        or resolved_path.is_relative_to(outputs_root)
    ):
        raise HTTPException(status_code=403, detail="access denied")

    return resolved_path


async def save_thread_artifact_to_workspace_view(
    *,
    thread_id: str,
    current_uid: str,
    db,
    path: str,
) -> dict[str, str]:
    source_path = await resolve_thread_artifact_view(
        thread_id=thread_id,
        current_uid=current_uid,
        db=db,
        path=path,
    )

    conv_repo = ConversationRepository(db)
    conversation = await require_user_conversation(conv_repo, thread_id, str(current_uid))
    uid = str(conversation.uid)
    target_dir = sandbox_workspace_dir(thread_id, uid) / "saved_artifacts"
    target_dir.mkdir(parents=True, exist_ok=True)

    target_path = _next_available_artifact_path(target_dir, source_path.name)
    with source_path.open("rb") as src, target_path.open("wb") as dst:
        shutil.copyfileobj(src, dst)

    await invalidate_mention_cache(thread_id)
    await invalidate_workspace_mention_cache(uid)

    saved_virtual_path = virtual_path_for_thread_file(thread_id, target_path, uid=uid)
    return {
        "name": target_path.name,
        "source_path": "/" + path.lstrip("/"),
        "saved_path": saved_virtual_path,
        "saved_artifact_url": f"/api/chat/thread/{thread_id}/artifacts/{saved_virtual_path.lstrip('/')}",
    }


def _next_available_artifact_path(target_dir: Path, filename: str) -> Path:
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate

    base_name = Path(filename).stem
    suffix = Path(filename).suffix
    index = 1
    while True:
        candidate = target_dir / f"{base_name} ({index}){suffix}"
        if not candidate.exists():
            return candidate

        index += 1
        if index >= 1000:
            # This is a safety check to prevent infinite loops in case of some unexpected issue with file naming.
            raise RuntimeError(f"Unable to find available filename for {filename} after 1000 attempts.")
