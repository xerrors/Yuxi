"""线程 artifact 下载与保存用例。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import PurePosixPath

from yuxi.infrastructure.file_preview import prepare_file_preview, preview_too_large
from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.office_conversion import (
    OfficeConversionError,
    convert_office_to_pdf,
    is_office_pdf_convertible,
)
from yuxi.modules.extensions.skills.access import lock_accessible_shared_skill_for_file
from yuxi.modules.identity.repositories.users import UserRepository
from yuxi.modules.workspace.errors import FileTransferLimitError
from yuxi.modules.workspace.runtime_files import RuntimeFiles
from yuxi.modules.workspace.runtime_paths import (
    SHARED_SKILLS_PATH,
    is_runtime_path,
    normalize_artifact_path,
    runtime_workspace_path,
)
from yuxi.modules.workspace.services.bindings import resolve_authorized_workdir
from yuxi.shared.errors import (
    ContentTooLargeError,
    DomainError,
    InvalidOperationError,
    PermissionDeniedError,
    ResourceNotFoundError,
    StateConflictError,
)
from yuxi.shared.files import MAX_FILE_PREVIEW_BYTES, PreparedFile, PreviewResult, detect_media_type

MAX_ARTIFACT_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_SAVED_ARTIFACT_NAME_ATTEMPTS = 1000
DEFAULT_ARTIFACT_DESTINATION = "/saved_artifacts"


async def resolve_thread_artifact_view(
    *,
    thread_id: str,
    current_uid: str,
    db,
    path: str,
    app_id: str | None = None,
    download: bool = False,
    preview: bool = False,
) -> PreparedFile | PreviewResult | dict:
    """把实时授权文件导出为自动清理的 HTTP 文件响应。"""
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=current_uid, db=db, app_id=app_id)
    normalized = _normalize_artifact_path(runtime_workspace_path(access.workdir.root_path), path)
    await _require_skill_artifact_access(normalized_path=normalized, current_uid=current_uid, db=db)
    is_preview = preview and not download
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-artifact-", suffix=PurePosixPath(normalized).suffix)
    os.close(descriptor)
    try:
        await _copy_artifact_to_path(
            access,
            normalized,
            temp_path,
            MAX_FILE_PREVIEW_BYTES if is_preview else MAX_ARTIFACT_DOWNLOAD_BYTES,
        )
    except DomainError as exc:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
        if is_preview and isinstance(exc, ContentTooLargeError):
            return preview_too_large().payload()
        raise
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
        raise
    file_name = PurePosixPath(normalized).name or "artifact"
    if is_preview:
        try:
            with open(temp_path, "rb") as artifact_file:
                raw_content = artifact_file.read()
            if is_office_pdf_convertible(normalized):
                return PreviewResult(
                    content=await convert_office_to_pdf(PurePosixPath(normalized).name, raw_content),
                    preview_type="pdf",
                    supported=True,
                    media_type="application/pdf",
                    filename=f"{PurePosixPath(normalized).stem or 'preview'}.pdf",
                )
            return prepare_file_preview(normalized, raw_content)
        except OfficeConversionError as exc:
            raise InvalidOperationError(str(exc)) from exc
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_path)

    with open(temp_path, "rb") as artifact_file:
        media_type = detect_media_type(file_name, artifact_file.read(16 * 1024))
    return PreparedFile(
        temp_path,
        media_type=media_type,
        filename=file_name if download else None,
    )


async def save_thread_artifact_to_workspace_view(
    *,
    thread_id: str,
    current_uid: str,
    db,
    path: str,
    destination_path: str | None = None,
    app_id: str | None = None,
) -> dict[str, str]:
    """把可见 artifact 复制到用户选择的工作区目录。"""
    access = await resolve_authorized_workdir(thread_id=thread_id, uid=current_uid, db=db, app_id=app_id)
    normalized = _normalize_artifact_path(runtime_workspace_path(access.workdir.root_path), path)
    raw_destination = str(destination_path or DEFAULT_ARTIFACT_DESTINATION).strip()
    destination = PurePosixPath(raw_destination)
    if (
        not destination.is_absolute()
        or ".." in PurePosixPath(raw_destination).parts
        or "\\" in raw_destination
        or "://" in raw_destination
        or is_runtime_path(raw_destination)
    ):
        raise PermissionDeniedError("invalid artifact destination")
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
            raise ResourceNotFoundError("artifact destination does not exist") from exc
        except NotADirectoryError as exc:
            raise InvalidOperationError("artifact destination is not a directory") from exc
        except (PermissionError, ValueError) as exc:
            raise PermissionDeniedError("artifact destination access denied") from exc
        if not destination_item["is_dir"]:
            raise InvalidOperationError("artifact destination is not a directory")
    await _require_skill_artifact_access(normalized_path=normalized, current_uid=current_uid, db=db)
    descriptor, temp_path = tempfile.mkstemp(prefix="yuxi-save-artifact-")
    os.close(descriptor)
    try:
        await _copy_artifact_to_path(access, normalized, temp_path)
        file_name = PurePosixPath(normalized).name or "artifact"
        stem = PurePosixPath(file_name).stem
        suffix = PurePosixPath(file_name).suffix
        for index in range(MAX_SAVED_ARTIFACT_NAME_ATTEMPTS + 1):
            candidate_name = file_name if index == 0 else f"{stem} ({index}){suffix}"
            target_scope = f"{destination_scope.rstrip('/')}/{candidate_name}"
            target = runtime_workspace_path(target_scope)
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
                raise ResourceNotFoundError("artifact destination does not exist") from exc
            except NotADirectoryError as exc:
                raise InvalidOperationError("artifact destination is not a directory") from exc
            except (PermissionError, ValueError) as exc:
                raise PermissionDeniedError("artifact destination access denied") from exc
        else:
            raise StateConflictError("saved artifact name space is exhausted")
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)
    return {
        "name": PurePosixPath(target).name,
        "source_path": normalized,
        "saved_path": target,
        "saved_artifact_url": f"/api/v1/agents/sessions/{thread_id}/artifacts/{target.lstrip('/')}",
    }


def _normalize_artifact_path(workdir_path: str, path: str) -> str:
    """以共同 runtime 规则验证产物，领域层保留拒绝契约。"""
    try:
        return normalize_artifact_path(workdir_path, path)
    except ValueError as exc:
        raise PermissionDeniedError(str(exc)) from exc


async def _require_skill_artifact_access(*, normalized_path: str, current_uid: str, db) -> None:
    """读取用户投影前锁定 Skill，并重新检查当前授权。"""
    skills_prefix = f"{SHARED_SKILLS_PATH}/"
    if not normalized_path.startswith(skills_prefix):
        return
    slug = normalized_path[len(skills_prefix) :].split("/", 1)[0]
    user = await UserRepository(db).get_by_uid(str(current_uid))
    if user is None or bool(user.is_deleted):
        raise PermissionDeniedError("artifact access denied")
    shared = await lock_accessible_shared_skill_for_file(db, user, slug)
    if shared is None:
        raise PermissionDeniedError("artifact access denied")
    relative_path = normalized_path[len(skills_prefix) + len(slug) :].lstrip("/")
    if not relative_path:
        raise InvalidOperationError("artifact path is not a regular file")


async def _copy_artifact_to_path(
    access,
    normalized_path: str,
    target_path: str,
    max_bytes: int = MAX_ARTIFACT_DOWNLOAD_BYTES,
) -> None:
    """把已授权的 Workspace 或 Skill artifact 有界复制到临时文件。"""
    try:
        await await_io(
            asyncio.to_thread(
                RuntimeFiles(access.uid).download_to_path,
                normalized_path,
                target_path,
                max_bytes,
            )
        )
    except PermissionError as exc:
        raise PermissionDeniedError("artifact access denied") from exc
    except IsADirectoryError as exc:
        raise InvalidOperationError("artifact path is not a regular file") from exc
    except FileTransferLimitError as exc:
        raise ContentTooLargeError("artifact exceeds transfer limit") from exc
    except (FileNotFoundError, ValueError) as exc:
        raise ResourceNotFoundError("artifact not found") from exc
