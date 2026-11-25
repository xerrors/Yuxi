"""AgentPanel Viewer 的实时 Project Workdir 文件服务。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import PurePosixPath

from yuxi.infrastructure.file_preview import preview_too_large
from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.office_conversion import OfficeConversionError
from yuxi.modules.workspace.errors import FileTransferLimitError
from yuxi.modules.workspace.preview import preview_workspace_file
from yuxi.modules.workspace.runtime_paths import is_conversation_archive_path, is_runtime_path, runtime_path_for_workdir_scope
from yuxi.modules.workspace.services.bindings import AuthorizedWorkdir, resolve_authorized_workdir
from yuxi.modules.workspace.services.file_entries import file_entry, sort_file_entries
from yuxi.modules.workspace.workdir import Workdir
from yuxi.shared.errors import ContentTooLargeError, InvalidOperationError, PermissionDeniedError, ResourceNotFoundError, StateConflictError
from yuxi.shared.files import MAX_FILE_PREVIEW_BYTES, FileInput, PreparedFile, PreviewResult, detect_media_type

SEARCH_MAX_RESULTS = 100
SEARCH_MAX_DIRECTORIES = 600
MAX_VIEWER_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_VIEWER_DOWNLOAD_BYTES = 1024 * 1024 * 1024


async def list_viewer_filesystem_tree(*, thread_id: str, path: str, current_user, db) -> dict:
    """列出实时 Project Workdir；根路径 `/` 直接表示 Workdir 根。"""
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=str(current_user.uid), db=db)
    _validate_viewer_path(access, path)
    return {"entries": await _list_directory(access, path)}


async def search_viewer_files(*, thread_id: str, query: str, current_user, db) -> dict:
    """在实时 Workdir 内按文件名搜索。"""
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return {"entries": []}
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=str(current_user.uid), db=db)
    try:
        matches = await asyncio.to_thread(
            access.workdir.search,
            normalized_query,
            max_results=SEARCH_MAX_RESULTS,
            max_directories=SEARCH_MAX_DIRECTORIES,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
        raise ResourceNotFoundError("目录不存在") from exc
    entries = []
    for item in matches:
        parent = str(PurePosixPath(str(item["path"])).parent)
        entries.append(_entry(access, parent, item))
    return {"entries": entries}


async def read_viewer_file_content(
    *,
    thread_id: str,
    path: str,
    current_user,
    db,
    office_render: str = "source",
) -> dict | PreviewResult:
    """从实时 Workdir 读取预览，不经过 MinIO revision。"""
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=str(current_user.uid), db=db)
    _validate_viewer_path(access, path)
    try:
        raw_content = await asyncio.to_thread(access.workdir.read_file, path, MAX_FILE_PREVIEW_BYTES)
    except PermissionError as exc:
        raise PermissionDeniedError("Access denied") from exc
    except IsADirectoryError as exc:
        raise InvalidOperationError("路径不是普通文件") from exc
    except FileTransferLimitError:
        return preview_too_large().payload()
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise ResourceNotFoundError("文件不存在") from exc
    try:
        return await preview_workspace_file(
            path,
            raw_content,
            office_cache_key=f"viewer:{access.uid}:{access.workdir_path}:{path}",
            office_render=office_render,
        )
    except OfficeConversionError as exc:
        raise InvalidOperationError(str(exc)) from exc


async def download_viewer_file(*, thread_id: str, path: str, current_user, db) -> PreparedFile:
    """从实时 Workdir 流式下载普通文件。"""
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=str(current_user.uid), db=db)
    _validate_viewer_path(access, path)
    try:
        temp_path = await _download_to_temp(access.workdir, path, MAX_VIEWER_DOWNLOAD_BYTES)
    except PermissionError as exc:
        raise PermissionDeniedError("Access denied") from exc
    except IsADirectoryError as exc:
        raise InvalidOperationError("路径不是普通文件") from exc
    except FileTransferLimitError as exc:
        raise ContentTooLargeError("文件超过下载大小限制") from exc
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise ResourceNotFoundError("文件不存在") from exc
    file_name = PurePosixPath(path).name or "download"
    return PreparedFile(
        temp_path,
        media_type=detect_media_type(file_name),
        filename=file_name,
        explicit_disposition=True,
    )


async def delete_viewer_file(*, thread_id: str, path: str, current_user, db) -> dict:
    """实时删除 Workdir 内文件或目录。会话压缩留档目录及其内容不可删除。"""
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=str(current_user.uid), db=db)
    normalized = PurePosixPath(path).as_posix()
    _validate_viewer_path(access, normalized)
    if normalized == "/":
        raise InvalidOperationError("Project Workdir 根目录不允许删除")
    if is_conversation_archive_path(normalized):
        raise InvalidOperationError(
            "该路径是会话压缩留档，删除后无法恢复本会话的历史摘要",
            code="conversation_archive_protected",
        )
    try:
        await asyncio.to_thread(access.workdir.delete, normalized)
    except FileNotFoundError as exc:
        raise ResourceNotFoundError("文件不存在") from exc
    return {"success": True, "path": normalized}


async def create_viewer_directory(*, thread_id: str, parent_path: str, name: str, current_user, db) -> dict:
    """实时创建 Workdir 目录。"""
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=str(current_user.uid), db=db)
    _validate_viewer_path(access, parent_path)
    clean_name = str(name or "").strip()
    try:
        metadata = await asyncio.to_thread(access.workdir.create_directory, parent_path, clean_name)
    except ValueError as exc:
        raise InvalidOperationError(str(exc)) from exc
    # create_directory 已验证名称为单个普通组件，条目字段直接复用共享规范化。
    return {"entry": file_entry(f"{parent_path.rstrip('/')}/{clean_name}/", metadata)}


async def upload_viewer_files(*, thread_id: str, parent_path: str, files: list[FileInput], current_user, db) -> dict:
    """把用户上传直接写入实时 Workdir。"""
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=str(current_user.uid), db=db)
    _validate_viewer_path(access, parent_path)
    file_names = [PurePosixPath(str(upload.filename or "")).name for upload in files]
    if any(not name or name in {".", ".."} for name in file_names):
        raise InvalidOperationError("无法识别的文件名")
    if len(file_names) != len(set(file_names)):
        raise StateConflictError("同一次上传不能包含重名文件")
    # 冲突预检只取原始 name：目录里可能存在沙盒命令直接创建的非常规文件名，
    # 构建完整 Viewer 条目会引入额外的规范化失败面。
    try:
        items = await asyncio.to_thread(access.workdir.list_directory, parent_path)
    except PermissionError as exc:
        raise PermissionDeniedError("Access denied") from exc
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise ResourceNotFoundError("目录不存在") from exc
    existing_names = {item["name"] for item in items}
    collisions = existing_names.intersection(file_names)
    if collisions:
        raise StateConflictError(f"文件已存在: {', '.join(sorted(collisions))}")

    entries: list[dict] = []
    for upload, file_name in zip(files, file_names, strict=True):
        target = f"{parent_path.rstrip('/')}/{file_name}"
        try:
            metadata = await await_io(
                asyncio.to_thread(
                    access.workdir.copy_file_from_stream,
                    target,
                    upload.source,
                    max_bytes=MAX_VIEWER_UPLOAD_BYTES,
                    overwrite=False,
                )
            )
        except FileTransferLimitError as exc:
            raise InvalidOperationError("文件过大") from exc
        except FileExistsError as exc:
            raise StateConflictError(f"文件已存在: {file_name}") from exc
        except PermissionError as exc:
            raise PermissionDeniedError("Access denied") from exc
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ResourceNotFoundError("目录不存在") from exc
        entries.append(_entry(access, parent_path, {"name": file_name, **metadata}))
    return {"entries": entries}


def _validate_viewer_path(access: AuthorizedWorkdir, path: str) -> None:
    """Viewer 只接受 Workdir scope，不接受 Backend runtime 绝对路径。"""
    if is_runtime_path(path):
        raise PermissionDeniedError("Access denied")
    try:
        access.workdir.resolve_path(path)
    except ValueError as exc:
        raise PermissionDeniedError("Access denied") from exc


def _entry(access: AuthorizedWorkdir, parent_scope: str, item: dict) -> dict:
    """规范化目录项，并为文件补充 Viewer 的 artifact 下载地址。"""
    scope = f"{parent_scope.rstrip('/')}/{item['name']}"
    if not scope.startswith("/"):
        scope = f"/{scope}"
    entry = file_entry(scope, item)
    runtime_path = runtime_path_for_workdir_scope(access.workdir_path, scope)
    entry["artifact_url"] = None if entry["is_dir"] else f"/api/v1/agents/sessions/{access.thread_id}/artifacts/{runtime_path.lstrip('/')}"
    return entry


async def _list_directory(access: AuthorizedWorkdir, scope: str) -> list[dict]:
    """列出目录并规范化为 Viewer 条目，统一映射目录访问错误。"""
    try:
        items = await asyncio.to_thread(access.workdir.list_directory, scope)
    except PermissionError as exc:
        raise PermissionDeniedError("Access denied") from exc
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise ResourceNotFoundError("目录不存在") from exc
    return sort_file_entries(_entry(access, scope, item) for item in items)


async def _download_to_temp(workdir: Workdir, path: str, max_bytes: int) -> str:
    """把 Workdir 文件限量复制到临时文件，失败时负责清理临时文件。"""
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-viewer-", suffix=PurePosixPath(path).suffix)
    os.close(descriptor)
    try:
        await await_io(asyncio.to_thread(workdir.copy_file_to_path, path, temp_path, max_bytes))
    except BaseException:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
    return temp_path
