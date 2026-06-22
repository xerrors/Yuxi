from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import aiofiles
from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from yuxi.agents.backends.sandbox.paths import _global_user_data_dir, ensure_workspace_default_files
from yuxi.services.file_preview import (
    MAX_BINARY_PREVIEW_SIZE_BYTES,
    OfficePreviewConversionError,
    convert_office_to_pdf,
    detect_preview_type,
    detect_media_type,
    is_office_pdf_preview_file,
    is_binary_preview_type,
    render_preview_payload,
    render_preview_too_large_payload,
)
from yuxi.services.mention_search_service import invalidate_workspace_mention_cache
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import utc_isoformat_from_timestamp
from yuxi.utils.paths import VIRTUAL_PATH_WORKSPACE, WORKSPACE_DIR_NAME
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES, write_upload_to_buffer

EDITABLE_WORKSPACE_SUFFIXES = {".md", ".markdown", ".mdx", ".txt"}
MAX_WORKSPACE_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_BYTES
MAX_WORKSPACE_UPLOAD_FILES = 50


def _workspace_root(user: User) -> Path:
    try:
        user_data_root = _global_user_data_dir(str(user.uid)).resolve()
        root = user_data_root / WORKSPACE_DIR_NAME
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    if root.is_symlink():
        raise HTTPException(status_code=403, detail="Access denied")
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    try:
        resolved_root.relative_to(user_data_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    ensure_workspace_default_files(resolved_root)
    return resolved_root


def _normalize_workspace_path(path: str | None) -> PurePosixPath:
    raw_path = (path or "/").strip() or "/"
    if not raw_path.startswith("/"):
        raw_path = f"/{raw_path}"
    normalized = PurePosixPath(raw_path)
    if ".." in normalized.parts:
        raise HTTPException(status_code=403, detail="Access denied")
    return normalized


def _resolve_workspace_path(user: User, path: str | None) -> Path:
    root = _workspace_root(user)
    normalized = _normalize_workspace_path(path)
    relative_parts = [part for part in normalized.parts if part not in {"/", ""}]
    target = (root.joinpath(*relative_parts) if relative_parts else root).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    return target


def _entry_for_path(root: Path, path: Path) -> dict:
    stat = path.stat()
    is_dir = path.is_dir()
    relative = path.relative_to(root).as_posix()
    display_path = f"/{relative}" if relative else "/"
    if is_dir and display_path != "/" and not display_path.endswith("/"):
        display_path = f"{display_path}/"
    virtual_path = VIRTUAL_PATH_WORKSPACE if display_path == "/" else f"{VIRTUAL_PATH_WORKSPACE}{display_path}"
    return {
        "path": display_path,
        "virtual_path": virtual_path,
        "name": path.name or "工作区",
        "is_dir": is_dir,
        "size": 0 if is_dir else stat.st_size,
        "modified_at": utc_isoformat_from_timestamp(stat.st_mtime) or "",
    }


def _sort_entries(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda item: (not bool(item.get("is_dir")), str(item.get("name") or "").lower()))


def _validate_child_name(name: str, *, field_name: str) -> str:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能为空")
    if clean_name in {".", ".."} or "/" in clean_name or "\\" in clean_name:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能包含路径分隔符")
    if PurePosixPath(clean_name).name != clean_name:
        raise HTTPException(status_code=422, detail=f"{field_name} 不能包含路径分隔符")
    return clean_name


def _resolve_parent_directory(user: User, parent_path: str) -> Path:
    parent = _resolve_workspace_path(user, parent_path)
    if not parent.exists():
        raise HTTPException(status_code=404, detail="目标目录不存在")
    if not parent.is_dir():
        raise HTTPException(status_code=400, detail="目标路径不是目录")
    return parent


def _resolve_new_child(root: Path, parent: Path, name: str) -> Path:
    target = parent / name
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    if target.exists():
        raise HTTPException(status_code=400, detail="同名文件或文件夹已存在")
    return target


def _list_directory(root: Path, target: Path, *, recursive: bool = False, files_only: bool = False) -> list[dict]:
    children = list(target.iterdir())
    entries = [_entry_for_path(root, child) for child in children if not files_only or child.is_file()]
    if recursive:
        for child in children:
            if child.is_dir() and not child.is_symlink():
                entries.extend(_list_directory(root, child, recursive=True, files_only=files_only))
    return _sort_entries(entries)


async def list_workspace_tree(
    *, path: str, recursive: bool = False, files_only: bool = False, current_user: User
) -> dict:
    root = _workspace_root(current_user)
    target = _resolve_workspace_path(current_user, path)
    if not target.exists():
        return {"entries": []}
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="当前路径不是目录")
    entries = await asyncio.to_thread(_list_directory, root, target, recursive=recursive, files_only=files_only)
    return {"entries": entries}


def resolve_workspace_file_path(*, path: str, current_user: User) -> Path:
    target = _resolve_workspace_path(current_user, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"工作区文件不存在: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"当前路径不是文件: {path}")
    return target


async def read_workspace_file_content(*, path: str, current_user: User) -> dict | StreamingResponse:
    target = _resolve_workspace_path(current_user, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="当前路径是目录")

    stat = await asyncio.to_thread(target.stat)
    if stat.st_size > MAX_BINARY_PREVIEW_SIZE_BYTES:
        return render_preview_too_large_payload()

    if is_office_pdf_preview_file(path):
        pdf_content = await _convert_workspace_office_to_pdf(current_user, target, target.name)
        return _preview_binary_response(
            filename=f"{target.stem or 'preview'}.pdf",
            content=pdf_content,
            media_type="application/pdf",
            preview_type="pdf",
        )

    raw_content = await asyncio.to_thread(target.read_bytes)
    preview_type, supported, message = detect_preview_type(path, raw_content)
    if is_binary_preview_type(preview_type) and supported:
        return _preview_binary_response(
            filename=target.name or "preview",
            content=raw_content,
            media_type=detect_media_type(path, raw_content),
            preview_type=preview_type,
        )
    if not supported:
        return {
            "content": None,
            "preview_type": preview_type,
            "supported": False,
            "message": message,
            "truncated": False,
            "limit": None,
        }
    return render_preview_payload(path, raw_content)


def _preview_binary_response(
    *, filename: str, content: bytes, media_type: str, preview_type: str
) -> StreamingResponse:
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        "X-Yuxi-Preview-Type": preview_type,
        "X-Yuxi-Preview-Filename": quote(filename),
    }
    return StreamingResponse(io.BytesIO(content), media_type=media_type, headers=headers)


async def write_workspace_file_content(*, path: str, content: str, current_user: User) -> dict:
    root = _workspace_root(current_user)
    target = _resolve_workspace_path(current_user, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="当前路径是目录")
    if target.suffix.lower() not in EDITABLE_WORKSPACE_SUFFIXES:
        raise HTTPException(status_code=400, detail="当前文件类型不支持编辑")

    raw_content = await asyncio.to_thread(target.read_bytes)
    preview_type, supported, _message = detect_preview_type(path, raw_content)
    if preview_type not in {"markdown", "text"} or not supported:
        raise HTTPException(status_code=400, detail="当前文件类型不支持编辑")
    try:
        raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="当前文件不是 UTF-8 文本") from exc

    try:
        await asyncio.to_thread(target.write_text, content, encoding="utf-8")
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "success": True,
        "path": _normalize_workspace_path(path).as_posix(),
        "entry": _entry_for_path(root, target),
    }


async def delete_workspace_path(*, path: str, current_user: User) -> dict:
    root = _workspace_root(current_user)
    target = _resolve_workspace_path(current_user, path)
    if target == root:
        raise HTTPException(status_code=400, detail="工作区根目录不允许删除")
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    try:
        if target.is_dir():
            await asyncio.to_thread(shutil.rmtree, target)
        else:
            await asyncio.to_thread(target.unlink)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await invalidate_workspace_mention_cache(str(current_user.uid))
    return {"success": True, "path": _normalize_workspace_path(path).as_posix()}


async def create_workspace_directory(*, parent_path: str, name: str, current_user: User) -> dict:
    root = _workspace_root(current_user)
    directory_name = _validate_child_name(name, field_name="文件夹名")
    parent = _resolve_parent_directory(current_user, parent_path)
    target = _resolve_new_child(root, parent, directory_name)

    try:
        await asyncio.to_thread(target.mkdir)
    except FileExistsError as exc:
        raise HTTPException(status_code=400, detail="同名文件或文件夹已存在") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await invalidate_workspace_mention_cache(str(current_user.uid))
    return {"success": True, "entry": _entry_for_path(root, target)}


async def _write_workspace_upload(file: UploadFile, target: Path) -> None:
    created_file = False
    upload_completed = False

    try:
        async with aiofiles.open(target, "xb") as buffer:
            created_file = True
            await write_upload_to_buffer(
                file,
                buffer,
                max_size_bytes=MAX_WORKSPACE_UPLOAD_SIZE_BYTES,
                too_large_message="文件过大，当前仅支持 100 MB 以内的文件",
            )
        upload_completed = True
    except FileExistsError as exc:
        raise HTTPException(status_code=400, detail="同名文件或文件夹已存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if created_file and not upload_completed and target.exists():
            with contextlib.suppress(OSError):
                await asyncio.to_thread(target.unlink)


async def upload_workspace_files(*, parent_path: str, files: list[UploadFile], current_user: User) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
    if len(files) > MAX_WORKSPACE_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多上传 {MAX_WORKSPACE_UPLOAD_FILES} 个文件")

    root = _workspace_root(current_user)
    parent = _resolve_parent_directory(current_user, parent_path)
    seen_names = set()
    upload_targets: list[tuple[UploadFile, Path]] = []

    for file in files:
        file_name = _validate_child_name(Path(file.filename or "").name, field_name="文件名")
        if file_name in seen_names:
            raise HTTPException(status_code=400, detail=f"选择的文件中存在重复文件名: {file_name}")
        seen_names.add(file_name)
        upload_targets.append((file, _resolve_new_child(root, parent, file_name)))

    completed_targets: list[Path] = []
    try:
        for file, target in upload_targets:
            await _write_workspace_upload(file, target)
            completed_targets.append(target)
    except HTTPException:
        for target in completed_targets:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(target.unlink)
        raise

    await invalidate_workspace_mention_cache(str(current_user.uid))
    return {"success": True, "entries": [_entry_for_path(root, target) for _file, target in upload_targets]}


async def _convert_workspace_office_to_pdf(user: User, target: Path, file_name: str) -> bytes:
    user_data_root = _global_user_data_dir(str(user.uid)).resolve()
    cache_dir = user_data_root / ".office_preview_cache"
    stat = await asyncio.to_thread(target.stat)
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()
    cache_path = cache_dir / f"{digest}-{stat.st_mtime_ns}-{stat.st_size}.pdf"

    cached = await asyncio.to_thread(lambda: cache_path.read_bytes() if cache_path.exists() else None)
    if cached is not None:
        return cached

    content = await asyncio.to_thread(target.read_bytes)
    try:
        pdf_content = await convert_office_to_pdf(file_name, content)
    except OfficePreviewConversionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await asyncio.to_thread(_store_office_pdf_cache, cache_dir, digest, cache_path, pdf_content)
    return pdf_content


def _store_office_pdf_cache(cache_dir: Path, digest: str, cache_path: Path, pdf_content: bytes) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for stale in cache_dir.glob(f"{digest}-*.pdf"):
        if stale != cache_path:
            stale.unlink(missing_ok=True)
    cache_path.write_bytes(pdf_content)


async def download_workspace_file(*, path: str, current_user: User) -> StreamingResponse | FileResponse:
    target = _resolve_workspace_path(current_user, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="当前路径是目录")

    file_name = target.name or "download"
    media_type = detect_media_type(file_name)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"}
    if target.stat().st_size > 1024 * 1024 * 16:
        return FileResponse(path=target, media_type=media_type, headers=headers)

    content = await asyncio.to_thread(target.read_bytes)
    return StreamingResponse(io.BytesIO(content), media_type=media_type, headers=headers)
