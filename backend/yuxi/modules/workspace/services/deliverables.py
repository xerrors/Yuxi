"""对话交付文件的自动归集与个人成果读取。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import PurePosixPath

from sqlalchemy.exc import IntegrityError

from yuxi.infrastructure.file_preview import preview_too_large
from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.office_conversion import OfficeConversionError
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.services.artifacts import resolve_thread_artifact_view
from yuxi.modules.workspace.errors import FileTransferLimitError
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.models import Deliverable
from yuxi.modules.workspace.preview import preview_workspace_file
from yuxi.modules.workspace.repositories.deliverables import DeliverableRepository
from yuxi.modules.workspace.runtime_paths import normalize_artifact_path, runtime_workspace_path
from yuxi.modules.workspace.services.bindings import resolve_authorized_session_workdir
from yuxi.shared.datetime import format_utc_datetime
from yuxi.shared.errors import (
    ContentTooLargeError,
    InvalidOperationError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from yuxi.shared.files import MAX_FILE_PREVIEW_BYTES, PreparedFile

MAX_DELIVERABLE_BYTES = 1024 * 1024 * 1024
MAX_DELIVERABLE_COUNT = 100
MAX_DELIVERABLE_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


async def _discard_snapshot(filesystem: Workspace, saved_path: str) -> None:
    """删除本次发布创建的随机副本目录，不触碰其他发布版本。"""
    try:
        await asyncio.to_thread(filesystem.delete_authorized_path, str(PurePosixPath(saved_path).parent), root="/")
    except (FileNotFoundError, OSError, ValueError):
        pass


async def save_run_deliverables(
    *, uid: str, thread_id: str, run_id: str, worker_id: str, operation_id: str, paths: list[str], db
) -> list[Deliverable]:
    """在当前执行所有权下保存独立副本，返回已提交的成果记录。"""
    if not operation_id.strip():
        raise InvalidOperationError("成果发布缺少工具调用身份")
    agent_session = await SessionRepository(db).lock_session_by_thread_id(thread_id)
    if agent_session is None or agent_session.uid != uid or agent_session.status != "active":
        raise PermissionDeniedError("成果来源对话不可用")
    run_repository = AgentRunRepository(db)
    run = await run_repository.lock_output_persistence(run_id, worker_id=worker_id, thread_id=thread_id)
    if run is None or run.uid != uid or run.app_id != agent_session.app_id or run.session_record_id != agent_session.id:
        raise PermissionDeniedError("成果来源执行不属于当前对话")

    repository = DeliverableRepository(db)
    access = await resolve_authorized_session_workdir(agent_session=agent_session, uid=uid, app_id=run.app_id, db=db)
    normalized_paths = list(
        dict.fromkeys(normalize_artifact_path(runtime_workspace_path(access.workdir.root_path), path) for path in paths)
    )
    if len(normalized_paths) > MAX_DELIVERABLE_COUNT:
        raise ContentTooLargeError(f"一次最多交付 {MAX_DELIVERABLE_COUNT} 个文件")
    await db.commit()
    # 成果副本位于用户数据根的专属目录，不进入可编辑的 Agent Workspace。
    filesystem = Workspace.user_data(uid)
    candidates = []
    created_paths = []
    total_size = 0
    try:
        for source_path in normalized_paths:
            existing = await repository.get_publication(run_id, operation_id, source_path)
            if existing is not None:
                candidates.append(existing)
                await db.commit()
                continue
            prepared = await resolve_thread_artifact_view(
                thread_id=thread_id, current_uid=uid, app_id=run.app_id, db=db, path=source_path, download=True
            )
            try:
                await db.commit()
                identity = json.dumps([run_id, operation_id, source_path], ensure_ascii=False, separators=(",", ":"))
                deliverable_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                name = PurePosixPath(source_path).name
                saved_path = f"/deliverables/{deliverable_id}/{uuid.uuid4().hex}/{name}"
                info = await await_io(
                    asyncio.to_thread(filesystem.upload_authorized_file_from_path, saved_path, prepared.path, overwrite=False)
                )
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(prepared.path)
            created_paths.append(saved_path)
            total_size += info["size"]
            if total_size > MAX_DELIVERABLE_TOTAL_BYTES:
                raise ContentTooLargeError(f"一次交付文件总大小不能超过 {MAX_DELIVERABLE_TOTAL_BYTES} 字节")
            candidates.append(
                Deliverable(
                    id=deliverable_id,
                    uid=uid,
                    app_id=run.app_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    turn_id=run.turn_id,
                    operation_id=operation_id,
                    source_path=source_path,
                    saved_path=saved_path,
                    name=name,
                    size=info["size"],
                    mime_type=prepared.media_type,
                )
            )

        # 副本准备完毕才重新取得短锁；过期执行不能发布成功事实。
        await SessionRepository(db).lock_session_by_thread_id(thread_id)
        current_run = await run_repository.lock_output_persistence(run_id, worker_id=worker_id, thread_id=thread_id)
        if current_run is None or current_run.uid != uid or current_run.turn_id != run.turn_id:
            raise PermissionDeniedError("成果来源执行已失效")
        saved = []
        for candidate in candidates:
            existing = await repository.get_publication(run_id, operation_id, candidate.source_path)
            if existing is not None:
                if candidate.saved_path in created_paths:
                    await _discard_snapshot(filesystem, candidate.saved_path)
                    created_paths.remove(candidate.saved_path)
                saved.append(existing)
                continue
            try:
                async with db.begin_nested():
                    await repository.add(candidate)
            except IntegrityError as exc:
                existing = await repository.get_publication(run_id, operation_id, candidate.source_path)
                if existing is None:
                    raise exc
                await _discard_snapshot(filesystem, candidate.saved_path)
                created_paths.remove(candidate.saved_path)
                saved.append(existing)
            else:
                saved.append(candidate)
        await db.commit()
        return saved
    except BaseException:
        for saved_path in created_paths:
            await _discard_snapshot(filesystem, saved_path)
        raise


async def list_deliverables(*, uid: str, query: str, file_type: str, page: int, page_size: int, db) -> dict:
    """返回当前 Web 用户的成果分页，查询与来源标题保持同一身份边界。"""
    items, total = await DeliverableRepository(db).list_for_user(
        uid=uid, query=query.strip(), file_type=file_type, page=page, page_size=page_size
    )
    return {
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "size": item.size,
                "mime_type": item.mime_type,
                "created_at": format_utc_datetime(item.created_at),
                "thread_id": item.thread_id,
                "thread_title": title,
                "run_id": item.run_id,
            }
            for item, title in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def read_deliverable(*, deliverable_id: str, uid: str, db, preview: bool = False, download: bool = False):
    """按成果身份授权后读取保存副本，不依赖原对话文件仍然存在。"""
    item = await DeliverableRepository(db).get_for_user(deliverable_id, uid)
    if item is None:
        raise ResourceNotFoundError("成果不存在")
    filesystem = Workspace.user_data(uid)
    if preview:
        try:
            content = await await_io(asyncio.to_thread(filesystem.read_authorized_file, item.saved_path, MAX_FILE_PREVIEW_BYTES))
            return await preview_workspace_file(item.name, content, office_cache_key=f"deliverable:{uid}:{item.id}")
        except FileTransferLimitError:
            return preview_too_large()
        except FileNotFoundError as exc:
            raise ResourceNotFoundError("成果副本不存在") from exc
        except (PermissionError, ValueError) as exc:
            raise PermissionDeniedError("成果文件访问被拒绝") from exc
        except (IsADirectoryError, NotADirectoryError, OfficeConversionError) as exc:
            raise InvalidOperationError("成果文件无法预览") from exc

    descriptor, temporary_path = tempfile.mkstemp(prefix="yuxi-deliverable-", suffix=PurePosixPath(item.name).suffix)
    os.close(descriptor)
    try:
        await await_io(
            asyncio.to_thread(filesystem.download_authorized_file_to_path, item.saved_path, temporary_path, MAX_DELIVERABLE_BYTES)
        )
        return PreparedFile(temporary_path, media_type=item.mime_type, filename=item.name if download else None)
    except BaseException as exc:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_path)
        if isinstance(exc, FileTransferLimitError):
            raise ContentTooLargeError("成果文件超过下载限制") from exc
        if isinstance(exc, FileNotFoundError):
            raise ResourceNotFoundError("成果副本不存在") from exc
        if isinstance(exc, (PermissionError, ValueError)):
            raise PermissionDeniedError("成果文件访问被拒绝") from exc
        if isinstance(exc, (IsADirectoryError, NotADirectoryError)):
            raise InvalidOperationError("成果副本不是普通文件") from exc
        raise
