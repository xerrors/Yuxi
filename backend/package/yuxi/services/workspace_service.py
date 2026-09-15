from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from yuxi.agents.backends.paths import runtime_user_data_path
from yuxi.repositories.project_repository import ProjectRepository
from yuxi.services.file_preview import render_file_preview
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import utc_isoformat_from_timestamp
from yuxi.utils.filepreview import (
    MAX_BINARY_PREVIEW_SIZE_BYTES,
    OfficePreviewConversionError,
    detect_media_type,
    detect_preview_type,
    preview_too_large,
)
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES, write_upload_to_path
from yuxi.workspace.errors import FileTransferLimitError
from yuxi.workspace.filesystem import Workspace
from yuxi.workspace.paths import (
    ensure_user_workspace,
)

EDITABLE_WORKSPACE_SUFFIXES = {".md", ".markdown", ".mdx", ".txt"}
MAX_WORKSPACE_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_BYTES
MAX_WORKSPACE_UPLOAD_FILES = 50
MAX_WORKSPACE_DOWNLOAD_SIZE_BYTES = 1024 * 1024 * 1024

# 搜索返回条数上限，避免超大工作区一次性返回过多结果
WORKSPACE_SEARCH_MAX_RESULTS = 100
WORKSPACE_SCOPE_ROOT = "/"


async def search_workspace_files(*, query: str, current_user: User) -> dict:
    """按文件名在个人工作区内递归搜索，仅返回文件条目。"""
    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        return {"entries": []}

    backend = _workspace_backend(current_user)
    try:
        matches = await asyncio.to_thread(
            backend.search_authorized_tree,
            WORKSPACE_SCOPE_ROOT,
            normalized_query,
            include_directories=False,
            max_results=WORKSPACE_SEARCH_MAX_RESULTS,
        )
    except FileNotFoundError:
        return {"entries": []}
    return {"entries": [_entry_from_metadata(item["path"], item) for item in matches]}


async def list_workspace_tree(
    *,
    path: str,
    recursive: bool = False,
    files_only: bool = False,
    include_unbound_project_dirs: bool = False,
    current_user: User,
    db,
) -> dict:
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    try:
        if recursive:
            scanned = await asyncio.to_thread(
                backend.search_authorized_tree,
                workspace_path,
                "",
                include_directories=not files_only,
                max_results=5000,
            )
            entries = [_entry_from_metadata(item["path"], item) for item in scanned]
        else:
            entries = await asyncio.to_thread(
                _list_workspace_directory,
                backend,
                workspace_path,
                files_only=files_only,
            )
    except FileNotFoundError:
        return {"entries": []}
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail="当前路径不是目录") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    if not include_unbound_project_dirs:
        entries = await _filter_project_tree_entries(entries, uid=str(current_user.uid), db=db)
    return {"entries": entries}


async def _filter_project_tree_entries(entries: list[dict], *, uid: str, db) -> list[dict]:
    """隐藏未归属 selectable Project 的 projects 子树。"""
    entries_with_paths = [(entry, PurePosixPath(str(entry.get("path") or "/").strip("/"))) for entry in entries]
    if not any(path.parts[:1] == ("projects",) and path.as_posix() != "projects" for _, path in entries_with_paths):
        return entries

    visible_paths = await ProjectRepository(db).list_selectable_workdir_paths_for_user(uid)
    selected_project_paths = [
        PurePosixPath(path) for path in visible_paths if PurePosixPath(path).parts[:1] == ("projects",)
    ]

    def is_visible(candidate: PurePosixPath) -> bool:
        if candidate.parts[:1] != ("projects",) or candidate.as_posix() == "projects":
            return True
        return any(
            candidate == selected or candidate.is_relative_to(selected) or selected.is_relative_to(candidate)
            for selected in selected_project_paths
        )

    return [entry for entry, path in entries_with_paths if is_visible(path)]


async def read_workspace_file_bytes(*, path: str, current_user: User) -> tuple[str, bytes]:
    """在 no-follow Workspace 边界内读取知识库导入文件。"""
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    try:
        content = await asyncio.to_thread(
            backend.read_authorized_file,
            workspace_path,
            MAX_WORKSPACE_UPLOAD_SIZE_BYTES,
        )
    except FileTransferLimitError as exc:
        raise HTTPException(status_code=400, detail="文件过大，当前仅支持 100 MB 以内的工作区文件") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"工作区文件不存在: {path}") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail=f"当前路径不是文件: {path}") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    return PurePosixPath(workspace_path).name, content


async def read_workspace_file_content(*, path: str, current_user: User) -> dict | StreamingResponse:
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    try:
        raw_content = await asyncio.to_thread(
            backend.read_authorized_file,
            workspace_path,
            MAX_BINARY_PREVIEW_SIZE_BYTES,
        )
    except FileTransferLimitError:
        return preview_too_large().payload()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="当前路径不是文件") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc

    try:
        return await render_file_preview(
            path,
            raw_content,
            office_cache_key=f"workspace:{current_user.uid}:{workspace_path}",
        )
    except OfficePreviewConversionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def write_workspace_file_content(*, path: str, content: str, current_user: User) -> dict:
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    if PurePosixPath(workspace_path).suffix.lower() not in EDITABLE_WORKSPACE_SUFFIXES:
        raise HTTPException(status_code=400, detail="当前文件类型不支持编辑")

    try:
        raw_content = await asyncio.to_thread(
            backend.read_authorized_file,
            workspace_path,
            MAX_WORKSPACE_UPLOAD_SIZE_BYTES,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="当前路径是目录") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    preview_type, supported, _message = detect_preview_type(path, raw_content)
    if preview_type not in {"markdown", "text"} or not supported:
        raise HTTPException(status_code=400, detail="当前文件类型不支持编辑")
    try:
        raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="当前文件不是 UTF-8 文本") from exc

    try:
        item = await asyncio.to_thread(backend.write_authorized_file, workspace_path, content.encode("utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="当前路径是目录") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc

    return {
        "success": True,
        "path": _normalize_workspace_path(path).as_posix(),
        "entry": _entry_from_metadata(workspace_path, item),
    }


async def delete_workspace_path(*, path: str, current_user: User, db) -> dict:
    """个人文件和目录进入30天统一回收站。"""
    from yuxi.services.personal_trash_service import trash_personal_paths

    _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    if workspace_path == WORKSPACE_SCOPE_ROOT:
        raise HTTPException(status_code=400, detail="工作区根目录不允许删除")
    result = await trash_personal_paths(
        db=db,
        uid=str(current_user.uid),
        paths=[workspace_path],
        name=PurePosixPath(workspace_path).name,
        kind="workspace",
    )
    return {"success": True, "path": workspace_path, "trash": result}


async def create_workspace_directory(*, parent_path: str, name: str, current_user: User) -> dict:
    backend = _workspace_backend(current_user)
    directory_name = _validate_child_name(name, field_name="文件夹名")
    virtual_parent = _workspace_path(parent_path)
    target = f"{virtual_parent.rstrip('/')}/{directory_name}"

    try:
        item = await asyncio.to_thread(
            backend.create_authorized_directory,
            virtual_parent,
            directory_name,
            root=WORKSPACE_SCOPE_ROOT,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=400, detail="同名文件或文件夹已存在") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="目标目录不存在") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc

    return {"success": True, "entry": _entry_from_metadata(target, item)}


async def upload_workspace_files(*, parent_path: str, files: list[UploadFile], current_user: User) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
    if len(files) > MAX_WORKSPACE_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多上传 {MAX_WORKSPACE_UPLOAD_FILES} 个文件")

    backend = _workspace_backend(current_user)
    parent = _workspace_path(parent_path)
    try:
        parent_stat = await asyncio.to_thread(backend.stat_authorized_path, parent, root=WORKSPACE_SCOPE_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="目标目录不存在") from exc
    except (PermissionError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    if not parent_stat["is_dir"]:
        raise HTTPException(status_code=400, detail="目标路径不是目录")
    seen_names = set()
    upload_targets: list[tuple[UploadFile, str]] = []

    for file in files:
        file_name = _validate_child_name(Path(file.filename or "").name, field_name="文件名")
        if file_name in seen_names:
            raise HTTPException(status_code=400, detail=f"选择的文件中存在重复文件名: {file_name}")
        seen_names.add(file_name)
        upload_targets.append((file, f"{parent.rstrip('/')}/{file_name}"))

    completed_entries: list[tuple[str, dict]] = []
    try:
        for file, target in upload_targets:
            item = await _write_workspace_upload(file, backend, target)
            completed_entries.append((target, item))
    except HTTPException:
        for target, _item in completed_entries:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(
                    backend.delete_authorized_path,
                    target,
                    root=WORKSPACE_SCOPE_ROOT,
                )
        raise

    entries = [_entry_from_metadata(target, item) for target, item in completed_entries]
    return {"success": True, "entries": entries}


async def download_workspace_file(*, path: str, current_user: User) -> FileResponse:
    backend = _workspace_backend(current_user)
    workspace_path = _workspace_path(path)
    file_name = PurePosixPath(workspace_path).name or "download"
    media_type = detect_media_type(file_name)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"}
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-workspace-download-", suffix=PurePosixPath(file_name).suffix)
    os.close(descriptor)
    try:
        await asyncio.to_thread(
            backend.download_authorized_file_to_path,
            workspace_path,
            temp_path,
            MAX_WORKSPACE_DOWNLOAD_SIZE_BYTES,
        )
    except FileNotFoundError as exc:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    except FileTransferLimitError as exc:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
        raise HTTPException(status_code=413, detail="文件超过下载大小限制") from exc
    except (PermissionError, IsADirectoryError, NotADirectoryError, ValueError) as exc:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
        raise HTTPException(status_code=403, detail="Access denied") from exc
    return FileResponse(
        path=temp_path,
        media_type=media_type,
        headers=headers,
        background=BackgroundTask(os.unlink, temp_path),
    )


def _workspace_backend(user: User) -> Workspace:
    """物化并返回 uid 级 no-follow 文件系统。"""
    backend = Workspace(str(user.uid))
    try:
        ensure_user_workspace(str(user.uid))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    return backend


def _normalize_workspace_path(path: str | None) -> PurePosixPath:
    raw_path = (path or "/").strip() or "/"
    if not raw_path.startswith("/"):
        raw_path = f"/{raw_path}"
    normalized = PurePosixPath(raw_path)
    if ".." in normalized.parts:
        raise HTTPException(status_code=403, detail="Access denied")
    return normalized


def _workspace_path(path: str | None) -> str:
    return _normalize_workspace_path(path).as_posix()


def _validate_child_name(name: str, *, field_name: str) -> str:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能为空")
    if clean_name in {".", ".."} or "/" in clean_name or "\\" in clean_name:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能包含路径分隔符")
    if PurePosixPath(clean_name).name != clean_name:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能包含路径分隔符")
    return clean_name


def _entry_from_metadata(workspace_path: str, item: dict) -> dict:
    is_dir = bool(item["is_dir"])
    display_path = PurePosixPath(workspace_path).as_posix()
    if is_dir and display_path != "/" and not display_path.endswith("/"):
        display_path = f"{display_path}/"
    virtual_path = runtime_user_data_path(display_path)
    if is_dir and display_path != "/":
        virtual_path = f"{virtual_path}/"
    return {
        "path": display_path,
        "virtual_path": virtual_path,
        "name": PurePosixPath(display_path.rstrip("/")).name or "工作区",
        "is_dir": is_dir,
        "size": 0 if is_dir else int(item.get("size") or 0),
        "modified_at": utc_isoformat_from_timestamp(float(item.get("modified_at") or 0)) or "",
    }


def _sort_entries(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda item: (not bool(item.get("is_dir")), str(item.get("name") or "").lower()))


def _list_workspace_directory(
    backend: Workspace,
    target: str,
    *,
    files_only: bool = False,
) -> list[dict]:
    children = backend.list_authorized_directory(target, root=WORKSPACE_SCOPE_ROOT)
    entries = []
    for child in children:
        child_path = f"{target.rstrip('/')}/{child['name']}"
        if not files_only or not child["is_dir"]:
            entries.append(_entry_from_metadata(child_path, child))
    return _sort_entries(entries)


async def _write_workspace_upload(file: UploadFile, backend: Workspace, target: str) -> dict:
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-workspace-upload-")
    os.close(descriptor)
    try:
        await write_upload_to_path(
            file,
            Path(temp_path),
            max_size_bytes=MAX_WORKSPACE_UPLOAD_SIZE_BYTES,
            too_large_message="文件过大，当前仅支持 100 MB 以内的文件",
        )
        return await asyncio.to_thread(
            backend.upload_authorized_file_from_path,
            target,
            temp_path,
            overwrite=False,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=400, detail="同名文件或文件夹已存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
