"""线程 artifact 下载与保存用例。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import PurePosixPath

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from yuxi.agents.backends.paths import (
    VIRTUAL_PATH_PREFIX,
    VIRTUAL_SKILLS_PATH,
    is_runtime_path,
    runtime_user_data_path,
    workspace_scope_from_runtime_path,
)
from yuxi.agents.skills.service import ResolvedSkill, list_accessible_skills
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.file_preview import render_file_preview
from yuxi.services.personal_trash_service import lock_user_files, require_no_pending_file_operations
from yuxi.services.workdir_service import resolve_authorized_workdir
from yuxi.utils.filepreview import (
    MAX_BINARY_PREVIEW_SIZE_BYTES,
    OfficePreviewConversionError,
    detect_media_type,
    preview_too_large,
)
from yuxi.utils.paths import open_regular_file_fd
from yuxi.workspace.errors import FileTransferLimitError

MAX_ARTIFACT_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_SAVED_ARTIFACT_NAME_ATTEMPTS = 1000
DEFAULT_ARTIFACT_DESTINATION = "/saved_artifacts"


def _normalize_artifact_path(workdir_path: str, path: str) -> str:
    raw = str(path or "").strip()
    normalized = str(PurePosixPath(raw if raw.startswith("/") else f"/{raw}"))
    if ".." in PurePosixPath(raw).parts:
        raise HTTPException(status_code=403, detail="access denied")
    allowed = normalized.startswith(f"{workdir_path}/") or normalized.startswith(f"{VIRTUAL_PATH_PREFIX.rstrip('/')}/")
    allowed = allowed or normalized.startswith(f"{VIRTUAL_SKILLS_PATH}/")
    if not allowed:
        raise HTTPException(status_code=403, detail="artifact is outside the current user's visible roots")
    return normalized


async def _require_skill_artifact_access(
    *, normalized_path: str, current_uid: str, db
) -> tuple[ResolvedSkill, str] | None:
    skills_prefix = f"{VIRTUAL_SKILLS_PATH}/"
    if not normalized_path.startswith(skills_prefix):
        return None
    slug = normalized_path[len(skills_prefix) :].split("/", 1)[0]
    user = await UserRepository(db).get_by_uid(str(current_uid))
    if user is None or bool(user.is_deleted):
        raise HTTPException(status_code=403, detail="artifact access denied")
    accessible = {skill.slug: skill for skill in await list_accessible_skills(db, user)}
    skill = accessible.get(slug)
    if skill is None:
        raise HTTPException(status_code=403, detail="artifact access denied")
    relative_path = normalized_path[len(skills_prefix) + len(slug) :].lstrip("/")
    if not relative_path:
        raise HTTPException(status_code=400, detail="artifact path is not a regular file")
    return skill, relative_path


def _copy_skill_file_to_path(skill: ResolvedSkill, relative_path: str, target_path: str, max_bytes: int) -> int:
    """从已授权 Skill 真实来源有界复制普通文件。"""
    parts = tuple(PurePosixPath(relative_path).parts)
    if not parts or ".." in parts:
        raise ValueError("invalid skill artifact path")
    target_fd = None
    with open_regular_file_fd(skill.source_dir, parts) as (source_fd, source_stat):
        if source_stat.st_size > max_bytes:
            raise FileTransferLimitError("file exceeds transfer limit")
        try:
            target_fd = os.open(target_path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            total = 0
            while chunk := os.read(source_fd, 1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise FileTransferLimitError("file exceeds transfer limit")
                offset = 0
                while offset < len(chunk):
                    offset += os.write(target_fd, chunk[offset:])
            return total
        finally:
            if target_fd is not None:
                os.close(target_fd)


async def _copy_artifact_to_path(
    access,
    normalized_path: str,
    skill_source,
    target_path: str,
    max_bytes: int = MAX_ARTIFACT_DOWNLOAD_BYTES,
) -> None:
    """把已授权的 Workspace 或 Skill artifact 有界复制到临时文件。"""
    try:
        if skill_source is None:
            await asyncio.to_thread(
                access.workdir.workspace.download_authorized_file_to_path,
                workspace_scope_from_runtime_path(normalized_path),
                target_path,
                max_bytes,
            )
            return
        await asyncio.to_thread(
            _copy_skill_file_to_path,
            skill_source[0],
            skill_source[1],
            target_path,
            max_bytes,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="artifact access denied") from exc
    except IsADirectoryError as exc:
        raise HTTPException(status_code=400, detail="artifact path is not a regular file") from exc
    except FileTransferLimitError as exc:
        raise HTTPException(status_code=413, detail="artifact exceeds transfer limit") from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc


async def resolve_thread_artifact_view(
    *,
    thread_id: str,
    current_uid: str,
    db,
    path: str,
    download: bool = False,
    preview: bool = False,
) -> FileResponse | StreamingResponse | dict:
    """把实时授权文件导出为自动清理的 HTTP 文件响应。"""
    await lock_user_files(db, str(current_uid))
    await require_no_pending_file_operations(db, str(current_uid))
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=current_uid, db=db)
    normalized = _normalize_artifact_path(runtime_user_data_path(access.workdir.root_path), path)
    skill_source = await _require_skill_artifact_access(normalized_path=normalized, current_uid=current_uid, db=db)
    is_preview = preview and not download
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-artifact-", suffix=PurePosixPath(normalized).suffix)
    os.close(descriptor)
    try:
        await _copy_artifact_to_path(
            access,
            normalized,
            skill_source,
            temp_path,
            MAX_BINARY_PREVIEW_SIZE_BYTES if is_preview else MAX_ARTIFACT_DOWNLOAD_BYTES,
        )
    except HTTPException as exc:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
        if is_preview and exc.status_code == 413:
            return preview_too_large().payload()
        raise
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
        raise
    file_name = PurePosixPath(normalized).name or "artifact"
    if is_preview:
        try:
            with open(temp_path, "rb") as artifact_file:
                raw_content = artifact_file.read()
            return await render_file_preview(
                normalized,
                raw_content,
                office_cache_key=f"artifact:{current_uid}:{normalized}",
            )
        except OfficePreviewConversionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_path)

    with open(temp_path, "rb") as artifact_file:
        media_type = detect_media_type(file_name, artifact_file.read(16 * 1024))
    return FileResponse(
        temp_path,
        media_type=media_type,
        filename=file_name if download else None,
        content_disposition_type="attachment",
        background=BackgroundTask(os.unlink, temp_path),
    )


async def save_thread_artifact_to_workspace_view(
    *, thread_id: str, current_uid: str, db, path: str, destination_path: str | None = None
) -> dict[str, str]:
    """把可见 artifact 复制到用户选择的工作区目录。"""
    await lock_user_files(db, str(current_uid))
    await require_no_pending_file_operations(db, str(current_uid))
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=current_uid, db=db)
    normalized = _normalize_artifact_path(runtime_user_data_path(access.workdir.root_path), path)
    raw_destination = str(destination_path or DEFAULT_ARTIFACT_DESTINATION).strip()
    destination = PurePosixPath(raw_destination)
    if (
        not destination.is_absolute()
        or ".." in PurePosixPath(raw_destination).parts
        or "\\" in raw_destination
        or "://" in raw_destination
        or is_runtime_path(raw_destination)
    ):
        raise HTTPException(status_code=403, detail="invalid artifact destination")
    destination_scope = destination.as_posix()
    destination_must_exist = destination_path is not None and destination_scope != DEFAULT_ARTIFACT_DESTINATION
    if destination_must_exist:
        try:
            destination_item = await asyncio.to_thread(
                access.workdir.workspace.stat_authorized_path,
                destination_scope,
                root="/",
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact destination does not exist") from exc
        except NotADirectoryError as exc:
            raise HTTPException(status_code=400, detail="artifact destination is not a directory") from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=403, detail="artifact destination access denied") from exc
        if not destination_item["is_dir"]:
            raise HTTPException(status_code=400, detail="artifact destination is not a directory")
    skill_source = await _require_skill_artifact_access(normalized_path=normalized, current_uid=current_uid, db=db)
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-save-artifact-")
    os.close(descriptor)
    try:
        await _copy_artifact_to_path(access, normalized, skill_source, temp_path)
        file_name = PurePosixPath(normalized).name or "artifact"
        stem = PurePosixPath(file_name).stem
        suffix = PurePosixPath(file_name).suffix
        for index in range(MAX_SAVED_ARTIFACT_NAME_ATTEMPTS + 1):
            candidate_name = file_name if index == 0 else f"{stem} ({index}){suffix}"
            target_scope = f"{destination_scope.rstrip('/')}/{candidate_name}"
            target = runtime_user_data_path(target_scope)
            try:
                await asyncio.to_thread(
                    access.workdir.workspace.upload_authorized_file_from_path,
                    target_scope,
                    temp_path,
                    overwrite=False,
                    create_parents=not destination_must_exist,
                )
                break
            except FileExistsError:
                continue
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="artifact destination does not exist") from exc
            except NotADirectoryError as exc:
                raise HTTPException(status_code=400, detail="artifact destination is not a directory") from exc
            except (PermissionError, ValueError) as exc:
                raise HTTPException(status_code=403, detail="artifact destination access denied") from exc
        else:
            raise HTTPException(status_code=409, detail="saved artifact name space is exhausted")
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
    return {
        "name": PurePosixPath(target).name,
        "source_path": normalized,
        "saved_path": target,
        "saved_artifact_url": f"/api/chat/thread/{thread_id}/artifacts/{target.lstrip('/')}",
    }
