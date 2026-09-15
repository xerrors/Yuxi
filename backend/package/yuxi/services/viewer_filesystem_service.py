"""AgentPanel Viewer 的实时 Project Workdir 文件服务。"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from yuxi.agents.backends.paths import is_runtime_path, runtime_path_for_workdir_scope
from yuxi.services.file_preview import render_file_preview
from yuxi.services.personal_trash_service import (
    lock_user_files,
    require_no_pending_file_operations,
    trash_personal_paths,
)
from yuxi.services.workdir_service import AuthorizedWorkdir, resolve_authorized_workdir
from yuxi.utils.datetime_utils import utc_isoformat_from_timestamp
from yuxi.utils.filepreview import (
    MAX_BINARY_PREVIEW_SIZE_BYTES,
    OfficePreviewConversionError,
    preview_too_large,
)
from yuxi.utils.upload_utils import write_upload_to_path
from yuxi.workspace.errors import FileTransferLimitError

SEARCH_MAX_RESULTS = 100
SEARCH_MAX_DIRECTORIES = 600
MAX_VIEWER_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_VIEWER_DOWNLOAD_BYTES = 1024 * 1024 * 1024


async def _viewer_state(*, thread_id: str, current_user, db) -> AuthorizedWorkdir:
    await lock_user_files(db, str(current_user.uid))
    await require_no_pending_file_operations(db, str(current_user.uid))
    return await resolve_authorized_workdir(
        thread_id=thread_id,
        uid=str(current_user.uid),
        db=db,
    )


def _validate_viewer_path(access: AuthorizedWorkdir, path: str) -> None:
    """Viewer 只接受 Workdir scope，不接受 Backend runtime 绝对路径。"""
    if is_runtime_path(path):
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        access.workdir.resolve_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc


def _entry(access: AuthorizedWorkdir, parent_scope: str, item: dict) -> dict:
    scope = f"{parent_scope.rstrip('/')}/{item['name']}"
    if not scope.startswith("/"):
        scope = f"/{scope}"
    is_dir = bool(item.get("is_dir"))
    runtime_path = runtime_path_for_workdir_scope(access.workdir_path, scope)
    return {
        "path": f"{scope}/" if is_dir else scope,
        "name": str(item["name"]),
        "is_dir": is_dir,
        "size": int(item.get("size") or 0),
        "modified_at": utc_isoformat_from_timestamp(float(item.get("modified_at") or 0)) or "",
        "artifact_url": None if is_dir else f"/api/chat/thread/{access.thread_id}/artifacts/{runtime_path.lstrip('/')}",
    }


async def _list_directory(access: AuthorizedWorkdir, scope: str) -> list[dict]:
    try:
        items = await asyncio.to_thread(
            access.workdir.list_directory,
            scope,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="目录不存在") from exc
    return sorted(
        (_entry(access, scope, item) for item in items),
        key=lambda item: (not item["is_dir"], item["name"].lower()),
    )


async def list_viewer_filesystem_tree(*, thread_id: str, path: str, current_user, db) -> dict:
    """列出实时 Project Workdir；根路径 `/` 直接表示 Workdir 根。"""
    access = await _viewer_state(thread_id=thread_id, current_user=current_user, db=db)
    _validate_viewer_path(access, path)
    return {"entries": await _list_directory(access, path)}


async def search_viewer_files(*, thread_id: str, query: str, current_user, db) -> dict:
    """在实时 Workdir 内按文件名搜索。"""
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return {"entries": []}
    access = await _viewer_state(thread_id=thread_id, current_user=current_user, db=db)
    try:
        matches = await asyncio.to_thread(
            access.workdir.search,
            normalized_query,
            max_results=SEARCH_MAX_RESULTS,
            max_directories=SEARCH_MAX_DIRECTORIES,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="目录不存在") from exc
    entries = []
    for item in matches:
        parent = str(PurePosixPath(str(item["path"])).parent)
        entries.append(_entry(access, parent, item))
    return {"entries": entries}


async def _download_to_temp(workdir, path: str, max_bytes: int) -> str:
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-viewer-", suffix=PurePosixPath(path).suffix)
    os.close(descriptor)
    try:
        await asyncio.to_thread(workdir.copy_file_to_path, path, temp_path, max_bytes)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
    return temp_path


async def read_viewer_file_content(*, thread_id: str, path: str, current_user, db) -> dict | StreamingResponse:
    """从实时 Workdir 读取预览，不经过 MinIO revision。"""
    access = await _viewer_state(thread_id=thread_id, current_user=current_user, db=db)
    _validate_viewer_path(access, path)
    try:
        raw_content = await asyncio.to_thread(
            access.workdir.read_file,
            path,
            MAX_BINARY_PREVIEW_SIZE_BYTES,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="路径不是普通文件") from exc
    except FileTransferLimitError:
        return preview_too_large().payload()
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    try:
        return await render_file_preview(
            path,
            raw_content,
            office_cache_key=f"viewer:{access.uid}:{access.workdir_path}:{path}",
        )
    except OfficePreviewConversionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def download_viewer_file(*, thread_id: str, path: str, current_user, db) -> FileResponse:
    """从实时 Workdir 流式下载普通文件。"""
    access = await _viewer_state(thread_id=thread_id, current_user=current_user, db=db)
    _validate_viewer_path(access, path)
    try:
        temp_path = await _download_to_temp(access.workdir, path, MAX_VIEWER_DOWNLOAD_BYTES)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="路径不是普通文件") from exc
    except FileTransferLimitError as exc:
        raise HTTPException(status_code=413, detail="文件超过下载大小限制") from exc
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    file_name = PurePosixPath(path).name or "download"
    return FileResponse(
        temp_path,
        media_type=mimetypes.guess_type(file_name)[0] or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"},
        background=BackgroundTask(os.unlink, temp_path),
    )


async def delete_viewer_file(*, thread_id: str, path: str, current_user, db) -> dict:
    """将 Workdir 内文件或目录移入统一回收站。"""
    access = await _viewer_state(thread_id=thread_id, current_user=current_user, db=db)
    normalized = PurePosixPath(path).as_posix()
    _validate_viewer_path(access, normalized)
    if normalized == "/":
        raise HTTPException(status_code=400, detail="Project Workdir 根目录不允许删除")
    workspace_path = "/" + str(PurePosixPath(access.workdir_path) / normalized.lstrip("/")).lstrip("/")
    entry = await trash_personal_paths(
        db=db,
        uid=str(current_user.uid),
        paths=[workspace_path],
        name=PurePosixPath(normalized).name,
        kind="workspace",
    )
    return {"success": True, "path": normalized, "trash": entry}


async def create_viewer_directory(*, thread_id: str, parent_path: str, name: str, current_user, db) -> dict:
    """实时创建 Workdir 目录。"""
    access = await _viewer_state(thread_id=thread_id, current_user=current_user, db=db)
    _validate_viewer_path(access, parent_path)
    try:
        metadata = await asyncio.to_thread(
            access.workdir.create_directory,
            parent_path,
            str(name or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "entry": {
            "path": f"{parent_path.rstrip('/')}/{str(name or '').strip()}/",
            "name": str(name or "").strip(),
            "is_dir": True,
            "size": int(metadata.get("size") or 0),
            "modified_at": utc_isoformat_from_timestamp(float(metadata.get("modified_at") or 0)) or "",
        }
    }


async def upload_viewer_files(*, thread_id: str, parent_path: str, files: list[UploadFile], current_user, db) -> dict:
    """把用户上传直接写入实时 Workdir。"""
    access = await _viewer_state(thread_id=thread_id, current_user=current_user, db=db)
    _validate_viewer_path(access, parent_path)
    file_names = [PurePosixPath(str(upload.filename or "")).name for upload in files]
    if any(not name or name in {".", ".."} for name in file_names):
        raise HTTPException(status_code=400, detail="无法识别的文件名")
    if len(file_names) != len(set(file_names)):
        raise HTTPException(status_code=409, detail="同一次上传不能包含重名文件")
    try:
        existing_names = {
            str(item["name"])
            for item in await asyncio.to_thread(
                access.workdir.list_directory,
                parent_path,
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="目录不存在") from exc
    collisions = existing_names.intersection(file_names)
    if collisions:
        raise HTTPException(status_code=409, detail=f"文件已存在: {', '.join(sorted(collisions))}")

    entries: list[dict] = []
    for upload, file_name in zip(files, file_names, strict=True):
        descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-viewer-upload-")
        os.close(descriptor)
        try:
            await write_upload_to_path(
                upload,
                Path(temp_path),
                max_size_bytes=MAX_VIEWER_UPLOAD_BYTES,
                too_large_message="文件过大",
            )
            target = f"{parent_path.rstrip('/')}/{file_name}"
            try:
                metadata = await asyncio.to_thread(
                    access.workdir.copy_file_from_path,
                    target,
                    temp_path,
                    overwrite=False,
                )
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail=f"文件已存在: {file_name}") from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail="Access denied") from exc
            except (FileNotFoundError, NotADirectoryError) as exc:
                raise HTTPException(status_code=404, detail="目录不存在") from exc
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        entries.append(_entry(access, parent_path, {"name": file_name, **metadata}))
    return {"entries": entries}
