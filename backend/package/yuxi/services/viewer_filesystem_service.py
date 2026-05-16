from __future__ import annotations

import asyncio
import contextlib
import io
import mimetypes
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import aiofiles
from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.backends.sandbox import (
    SKILLS_PATH,
    USER_DATA_PATH,
    ensure_thread_dirs,
    resolve_virtual_path,
    sandbox_user_data_dir,
    sandbox_workspace_dir,
    virtual_path_for_thread_file,
)
from yuxi.agents.backends.skills_backend import SelectedSkillsReadonlyBackend
from yuxi.agents.middlewares.skills_middleware import normalize_selected_skills
from yuxi.services.filesystem_service import _resolve_filesystem_state
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import utc_isoformat_from_timestamp
from yuxi.utils.paths import VIRTUAL_PATH_OUTPUTS, VIRTUAL_PATH_UPLOADS, VIRTUAL_PATH_WORKSPACE

_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown", ".mdx"})
_PDF_EXTENSIONS = frozenset({".pdf"})
_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".text",
        ".log",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".csv",
        ".tsv",
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".vue",
        ".html",
        ".htm",
        ".css",
        ".less",
        ".scss",
        ".xml",
        ".sql",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".env",
        ".dockerfile",
        ".gitignore",
        ".weather",
    }
)
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"})
_BINARY_SIGNATURES = (
    b"\x7fELF",
    b"MZ",
    b"%PDF-",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"RIFF",
)
_PROTECTED_USER_DATA_ROOTS = frozenset(
    {
        VIRTUAL_PATH_WORKSPACE,
        VIRTUAL_PATH_UPLOADS,
        VIRTUAL_PATH_OUTPUTS,
    }
)
_LEGACY_KBS_PATH = "/home/gem/kbs"


def _detect_preview_type(path: str, raw_content: bytes) -> tuple[str, bool, str | None]:
    suffix = PurePosixPath(path).suffix.lower()
    mime_type, _encoding = mimetypes.guess_type(path)
    head = raw_content[:1024]

    if suffix in _IMAGE_EXTENSIONS or (mime_type and mime_type.startswith("image/")):
        return "image", True, None

    if suffix in _PDF_EXTENSIONS or mime_type == "application/pdf" or head.startswith(b"%PDF-"):
        return "pdf", True, None

    if suffix in _MARKDOWN_EXTENSIONS:
        return "markdown", True, None

    if suffix in _TEXT_EXTENSIONS:
        return "text", True, None

    if b"\x00" in head:
        return "unsupported", False, "当前文件是二进制文件，暂不支持预览"

    if any(head.startswith(signature) for signature in _BINARY_SIGNATURES):
        if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
            return "image", True, None
        return "unsupported", False, "当前文件格式暂不支持预览"

    if mime_type:
        if mime_type.startswith("text/"):
            return "text", True, None
        if mime_type in {"application/json", "application/xml", "application/javascript"}:
            return "text", True, None
        if mime_type.startswith("application/"):
            return "unsupported", False, "当前文件格式暂不支持预览"

    if not raw_content:
        return "text", True, None

    try:
        raw_content.decode("utf-8")
        return "text", True, None
    except UnicodeDecodeError:
        return "unsupported", False, "当前文件不是可读文本，暂不支持预览"


def _normalize_path(path: str | None) -> str:
    normalized = (path or "/").strip() or "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/") if normalized not in {"/", SKILLS_PATH, USER_DATA_PATH} else normalized


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_local_user_data_path(thread_id: str, user_id: str, path: str) -> Path:
    try:
        actual_path = resolve_virtual_path(thread_id, path, user_id=user_id)
    except ValueError as exc:
        # 真实路径越过允许根目录时，按权限拒绝处理，而不是当作普通参数错误。
        if "path traversal" in str(exc):
            raise HTTPException(status_code=403, detail="Access denied") from exc
        raise
    resolved_path = actual_path.resolve()
    allowed_roots = (
        sandbox_user_data_dir(thread_id).resolve(),
        sandbox_workspace_dir(thread_id, user_id).resolve(),
    )
    if not any(_is_path_within(resolved_path, root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Access denied")
    return resolved_path


def _is_user_data_path(path: str) -> bool:
    return path == USER_DATA_PATH or path.startswith(f"{USER_DATA_PATH}/")


def _is_workspace_path(path: str) -> bool:
    return path == VIRTUAL_PATH_WORKSPACE or path.startswith(f"{VIRTUAL_PATH_WORKSPACE}/")


def _is_skills_path(path: str) -> bool:
    return path == SKILLS_PATH or path.startswith(f"{SKILLS_PATH}/")


def _is_in_home_gem(path: str) -> bool:
    """检查路径是否在 /home/gem/ 下但不在虚拟挂载点内"""
    if not path.startswith("/home/gem/"):
        return False
    # 排除虚拟挂载点
    if path.startswith(f"{USER_DATA_PATH}/") or path == USER_DATA_PATH:
        return False
    if path.startswith(f"{SKILLS_PATH}/") or path == SKILLS_PATH:
        return False
    if path == _LEGACY_KBS_PATH or path.startswith(f"{_LEGACY_KBS_PATH}/"):
        return False
    return True


def _strip_skills_prefix(path: str) -> str:
    if path == SKILLS_PATH:
        return "/"
    return path[len(SKILLS_PATH) :] or "/"


def _remap_prefixed_entry(entry: dict, prefix: str) -> dict:
    raw_path = str(entry.get("path") or "")
    is_dir = bool(entry.get("is_dir", False))
    remapped = f"{prefix}{raw_path}" if raw_path != "/" else f"{prefix}/"
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


def _sort_entries(entries: list[dict]) -> list[dict]:
    """Sort entries: folders first, then files alphabetically."""
    return sorted(
        entries,
        key=lambda e: (
            not bool(e.get("is_dir")),
            PurePosixPath(str(e.get("path") or "").rstrip("/")).name.lower(),
        ),
    )


def _entry_for_local_path(thread_id: str, user_id: str, path: Path) -> dict:
    stat = path.stat()
    is_dir = path.is_dir()
    display_path = virtual_path_for_thread_file(thread_id, path, user_id=user_id)
    if is_dir and not display_path.endswith("/"):
        display_path = f"{display_path}/"
    return {
        "path": display_path,
        "name": path.name,
        "is_dir": is_dir,
        "size": 0 if is_dir else stat.st_size,
        "modified_at": utc_isoformat_from_timestamp(stat.st_mtime) or "",
    }


def _list_local_entries(thread_id: str, user_id: str, actual_path) -> list[dict]:
    """List a local directory and remap children back into viewer virtual paths."""
    entries: list[dict] = []
    for child in sorted(actual_path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        entries.append(_entry_for_local_path(thread_id, user_id, child))
    return entries


def _validate_child_name(name: str, *, field_name: str) -> str:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能为空")
    if clean_name in {".", ".."} or "/" in clean_name or "\\" in clean_name:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能包含路径分隔符")
    if PurePosixPath(clean_name).name != clean_name:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能包含路径分隔符")
    return clean_name


def _resolve_workspace_parent_dir(thread_id: str, user_id: str, parent_path: str) -> Path:
    normalized_parent = _normalize_path(parent_path)
    if not _is_workspace_path(normalized_parent):
        raise HTTPException(status_code=400, detail="当前路径不支持写入")

    ensure_thread_dirs(thread_id, user_id)
    try:
        actual_parent = _resolve_local_user_data_path(thread_id, user_id, normalized_parent)
    except ValueError as exc:
        # workspace 写入边界按真实路径校验，软链接逃逸应表现为权限拒绝。
        if "path traversal" in str(exc):
            raise HTTPException(status_code=403, detail="Access denied") from exc
        raise
    if not actual_parent.exists():
        raise HTTPException(status_code=404, detail="目标目录不存在")
    if not actual_parent.is_dir():
        raise HTTPException(status_code=400, detail="目标路径不是目录")
    return actual_parent


def _resolve_new_workspace_child(thread_id: str, user_id: str, parent_path: Path, name: str) -> Path:
    target_path = parent_path / name
    workspace_root = sandbox_workspace_dir(thread_id, user_id).resolve()
    if not _is_path_within(target_path.resolve(strict=False), workspace_root):
        raise HTTPException(status_code=403, detail="Access denied")
    if target_path.exists():
        raise HTTPException(status_code=400, detail="同名文件或文件夹已存在")
    return target_path


def _list_user_data_root_entries(thread_id: str, user_id: str) -> list[dict]:
    """Expose thread-root files while keeping the user workspace entry visible."""
    entries = _list_local_entries(thread_id, user_id, sandbox_user_data_dir(thread_id))
    visible_paths = {str(entry.get("path") or "").rstrip("/") for entry in entries}
    workspace_dir = sandbox_workspace_dir(thread_id, user_id)
    workspace_virtual_path = virtual_path_for_thread_file(thread_id, workspace_dir, user_id=user_id).rstrip("/")
    if workspace_virtual_path not in visible_paths:
        # workspace is stored outside the per-thread root, so add it explicitly when needed.
        stat = workspace_dir.stat()
        entries.append(
            {
                "path": f"{workspace_virtual_path}/",
                "name": workspace_dir.name,
                "is_dir": True,
                "size": 0,
                "modified_at": utc_isoformat_from_timestamp(stat.st_mtime) or "",
            }
        )
    return entries


async def _resolve_viewer_state(
    *,
    thread_id: str,
    agent_id: str | None,
    agent_config_id: int | None,
    current_user: User,
    db: AsyncSession,
):
    _conversation, runtime_context, sandbox_backend = await _resolve_filesystem_state(
        thread_id=thread_id,
        user=current_user,
        db=db,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
    )
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
        # 根目录只显示 viewer 暴露的虚拟命名空间，避免为只读树视图触发 sandbox 冷启动。
        entries = []

        entries.append(
            {"path": f"{USER_DATA_PATH}/", "name": "user-data", "is_dir": True, "size": 0, "modified_at": ""}
        )
        if selected_skills:
            entries.append({"path": f"{SKILLS_PATH}/", "name": "skills", "is_dir": True, "size": 0, "modified_at": ""})

        return {"entries": _sort_entries(entries)}

    try:
        if _is_user_data_path(normalized_path):
            user_id = str(current_user.id)
            ensure_thread_dirs(thread_id, user_id)
            if normalized_path == USER_DATA_PATH:
                entries = await asyncio.to_thread(_list_user_data_root_entries, thread_id, user_id)
                return {"entries": _sort_entries(entries)}
            actual_path = _resolve_local_user_data_path(thread_id, user_id, normalized_path)
            if not actual_path.exists():
                return {"entries": []}
            if not actual_path.is_dir():
                raise HTTPException(status_code=400, detail="当前路径不是目录")
            entries = await asyncio.to_thread(_list_local_entries, thread_id, user_id, actual_path)
            return {"entries": _sort_entries(entries)}

        if _is_skills_path(normalized_path):
            entries = await asyncio.to_thread(skills_backend.ls_info, _strip_skills_prefix(normalized_path))
            remapped = [_remap_prefixed_entry(entry, SKILLS_PATH) for entry in entries]
            return {"entries": _sort_entries(remapped)}
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
            actual_path = _resolve_local_user_data_path(thread_id, str(current_user.id), normalized_path)
            if not actual_path.exists():
                raise HTTPException(status_code=404, detail="文件不存在")
            if not actual_path.is_file():
                raise HTTPException(status_code=400, detail="当前路径是目录")
            raw_content = await asyncio.to_thread(actual_path.read_bytes)
            preview_type, supported, message = _detect_preview_type(normalized_path, raw_content)
            if preview_type in {"image", "pdf"} or not supported:
                return {
                    "content": None,
                    "preview_type": preview_type,
                    "supported": supported,
                    "message": message,
                }
            return {
                "content": raw_content.decode("utf-8"),
                "preview_type": preview_type,
                "supported": supported,
                "message": message,
            }
        elif _is_skills_path(normalized_path):
            responses = await asyncio.to_thread(skills_backend.download_files, [_strip_skills_prefix(normalized_path)])
        elif _is_in_home_gem(normalized_path):
            # /home/gem/ 下的其他文件（如 workspace 目录）
            responses = await asyncio.to_thread(sandbox_backend.download_files, [normalized_path])
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
    preview_type, supported, message = _detect_preview_type(normalized_path, raw_content)

    if preview_type in {"image", "pdf"}:
        return {
            "content": None,
            "preview_type": preview_type,
            "supported": supported,
            "message": message,
        }

    if not supported:
        return {
            "content": None,
            "preview_type": preview_type,
            "supported": supported,
            "message": message,
        }

    content = raw_content.decode("utf-8")
    return {
        "content": content,
        "preview_type": preview_type,
        "supported": supported,
        "message": message,
    }


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
            actual_path = _resolve_local_user_data_path(thread_id, str(current_user.id), normalized_path)
            if not actual_path.exists():
                raise HTTPException(status_code=404, detail="文件不存在")
            if not actual_path.is_file():
                raise HTTPException(status_code=400, detail="当前路径是目录")

            file_name = actual_path.name or "download"
            media_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            headers = {
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}",
            }
            return FileResponse(path=actual_path, media_type=media_type, headers=headers)

        if _is_skills_path(normalized_path):
            responses = await asyncio.to_thread(skills_backend.download_files, [_strip_skills_prefix(normalized_path)])
        elif _is_in_home_gem(normalized_path):
            # /home/gem/ 下的其他文件（如 workspace 目录）
            responses = await asyncio.to_thread(sandbox_backend.download_files, [normalized_path])
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


async def delete_viewer_file(
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
    await _resolve_viewer_state(
        thread_id=thread_id,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )

    if not _is_user_data_path(normalized_path):
        raise HTTPException(status_code=400, detail="当前路径不支持删除")
    if normalized_path in _PROTECTED_USER_DATA_ROOTS:
        raise HTTPException(status_code=400, detail="当前目录不允许删除")

    try:
        actual_path = _resolve_local_user_data_path(thread_id, str(current_user.id), normalized_path)
        if not actual_path.exists():
            raise HTTPException(status_code=404, detail="文件不存在")
        if actual_path.is_dir():
            await asyncio.to_thread(shutil.rmtree, actual_path)
        else:
            await asyncio.to_thread(actual_path.unlink)
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return {"success": True, "path": normalized_path}


async def create_viewer_directory(
    *,
    thread_id: str,
    parent_path: str,
    name: str,
    agent_id: str | None,
    agent_config_id: int | None,
    current_user: User,
    db: AsyncSession,
) -> dict:
    if not thread_id:
        raise HTTPException(status_code=422, detail="thread_id 不能为空")

    await _resolve_viewer_state(
        thread_id=thread_id,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )

    user_id = str(current_user.id)
    directory_name = _validate_child_name(name, field_name="文件夹名")

    try:
        actual_parent = _resolve_workspace_parent_dir(thread_id, user_id, parent_path)
        target_path = _resolve_new_workspace_child(thread_id, user_id, actual_parent, directory_name)
        await asyncio.to_thread(target_path.mkdir)
    except FileExistsError as e:
        raise HTTPException(status_code=400, detail="同名文件或文件夹已存在") from e
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return {"success": True, "entry": _entry_for_local_path(thread_id, user_id, target_path)}


async def upload_viewer_file(
    *,
    thread_id: str,
    parent_path: str,
    file: UploadFile,
    agent_id: str | None,
    agent_config_id: int | None,
    current_user: User,
    db: AsyncSession,
) -> dict:
    if not thread_id:
        raise HTTPException(status_code=422, detail="thread_id 不能为空")

    await _resolve_viewer_state(
        thread_id=thread_id,
        agent_id=agent_id,
        agent_config_id=agent_config_id,
        current_user=current_user,
        db=db,
    )

    user_id = str(current_user.id)
    file_name = _validate_child_name(Path(file.filename or "").name, field_name="文件名")
    target_path: Path | None = None
    created_file = False
    upload_completed = False

    try:
        actual_parent = _resolve_workspace_parent_dir(thread_id, user_id, parent_path)
        target_path = _resolve_new_workspace_child(thread_id, user_id, actual_parent, file_name)
        async with aiofiles.open(target_path, "xb") as buffer:
            created_file = True
            while chunk := await file.read(1024 * 1024):
                await buffer.write(chunk)
        upload_completed = True
    except FileExistsError as e:
        raise HTTPException(status_code=400, detail="同名文件或文件夹已存在") from e
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        # 上传来自用户输入，传输中断时清理本次创建的半成品文件。
        if created_file and not upload_completed and target_path and target_path.exists():
            with contextlib.suppress(OSError):
                await asyncio.to_thread(target_path.unlink)

    return {"success": True, "entry": _entry_for_local_path(thread_id, user_id, target_path)}
