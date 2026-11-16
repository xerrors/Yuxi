"""媒体当前版本、文件锁与后台构建提交的 PostgreSQL 边界。"""

from contextlib import asynccontextmanager

from sqlalchemy import select

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import ResourcePermission, resolve_knowledge_base_permission
from yuxi.modules.knowledge.models import KnowledgeDocumentRevision, KnowledgeFile, KnowledgeProjectionOutbox
from yuxi.modules.knowledge.repositories.directories import DirectoryRepository
from yuxi.modules.knowledge.repositories.document_resets import DocumentResetRepository
from yuxi.modules.knowledge.repositories.projections import acquire_knowledge_projection_lock
from yuxi.modules.knowledge.repositories.revisions import DocumentRevisionRepository
from yuxi.modules.knowledge.revision_identity import body_revision, original_revision, source_object_uri
from yuxi.modules.knowledge.services.documents import file_record_to_meta
from yuxi.shared.errors import InvalidOperationError, PermissionDeniedError, ResourceNotFoundError, StateConflictError

MEDIA_JOB_TYPE = "knowledge_media_index"


async def active_media_build(session, kb_id: str, file_id: str, *, lock: bool = True):
    """按调用方锁序读取该文件的活动媒体构建。"""
    query = select(BackgroundJobRecord).where(
        BackgroundJobRecord.type == MEDIA_JOB_TYPE,
        BackgroundJobRecord.status.in_({"pending", "running"}),
        BackgroundJobRecord.payload["kb_id"].as_string() == kb_id,
        BackgroundJobRecord.payload["file_id"].as_string() == file_id,
    )
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def latest_media_build(session, kb_id: str, file_id: str):
    """在媒体文件快照内读取最近构建，不用任务缓存替代 PostgreSQL 状态。"""
    return await session.scalar(
        select(BackgroundJobRecord)
        .where(
            BackgroundJobRecord.type == MEDIA_JOB_TYPE,
            BackgroundJobRecord.payload["kb_id"].as_string() == kb_id,
            BackgroundJobRecord.payload["file_id"].as_string() == file_id,
        )
        .order_by(BackgroundJobRecord.created_at.desc())
        .limit(1)
    )


async def enqueue_unpublished_build_cleanup(session, kb_id: str, file_id: str, revision: str, attempt: str) -> bool:
    """只为尚未发布的 attempt 登记唯一回收意图，与失败终态共用事务。"""
    if await DocumentRevisionRepository().is_registered(session, revision):
        return False
    key = f"media_cleanup:{revision.removeprefix('media2:')}:{attempt}"
    existing = await session.scalar(select(KnowledgeProjectionOutbox).where(KnowledgeProjectionOutbox.event_key == key))
    if existing is None:
        session.add(
            KnowledgeProjectionOutbox(event_key=key, kb_id=kb_id, aggregate_id=file_id, generation=0, operation="media_build_cleanup")
        )
    return True


async def require_media_manage(session, kb, operator_id: str) -> None:
    """执行器与发布点重核当前管理权限，队列中的旧授权不能继续产生副作用。"""
    user = await session.scalar(select(User).where(User.uid == operator_id, User.is_deleted == 0))
    if user is None or resolve_knowledge_base_permission(user, kb) != ResourcePermission.MANAGE:
        raise PermissionDeniedError("当前用户不再具有媒体构建权限", code="media_build_forbidden")


@asynccontextmanager
async def media_file_snapshot(kb_id: str, file_id: str):
    """持有投影和文件共享锁，原件与清单读取期间不能回收或更新。"""
    async with pg_manager.get_async_session_context() as session:
        await acquire_knowledge_projection_lock(session, kb_id, shared=True)
        kb = await DirectoryRepository().lock(session, kb_id)
        if kb.kb_type != "milvus":
            raise InvalidOperationError("原生媒体仅支持本地 Milvus 知识库", code="unsupported_media_store")
        file = await session.scalar(
            select(KnowledgeFile)
            .where(
                KnowledgeFile.kb_id == kb_id,
                KnowledgeFile.file_id == file_id,
                KnowledgeFile.deleted_at.is_(None),
                KnowledgeFile.is_folder.is_(False),
            )
            .with_for_update(read=True)
        )
        if file is None:
            raise ResourceNotFoundError("媒体文件不存在")
        yield session, file


async def current_media_revision(session, file) -> KnowledgeDocumentRevision:
    """只有原件及正文依赖仍相同的最新已发布媒体可以用于当前问答。"""
    rows = await session.scalars(
        select(KnowledgeDocumentRevision).where(
            KnowledgeDocumentRevision.kb_id == file.kb_id,
            KnowledgeDocumentRevision.file_id == file.file_id,
            KnowledgeDocumentRevision.kind == "media",
            KnowledgeDocumentRevision.deleted_at.is_(None),
        )
    )
    selected = select_current_media_revision(list(rows), file)
    if selected is not None:
        return selected
    raise ResourceNotFoundError("当前原件尚无有效媒体索引，请构建或重建", code="media_index_unavailable")


def select_current_media_revision(revisions: list[KnowledgeDocumentRevision], file) -> KnowledgeDocumentRevision | None:
    """按运行时顺序从候选映射中选出唯一匹配当前原件和正文的版本。"""
    source, basis = current_dependencies(file)
    if source is None:
        return None
    candidates = sorted(revisions, key=lambda row: (row.created_at, row.revision), reverse=True)
    for row in candidates:
        if (
            (row.kb_id, row.file_id, row.kind) != (file.kb_id, file.file_id, "media")
            or row.deleted_at is not None
            or row.source_revision != source
        ):
            continue
        if row.basis_document_revision is None or row.basis_document_revision == basis:
            return row
    return None


async def current_media_files(kb_id: str, file_ids: list[str] | None) -> list[str]:
    """从当前文件表读取授权候选；空列表保留为空，最多接受一千个文件。"""
    if file_ids == []:
        return []
    async with pg_manager.get_async_session_context() as session:
        query = select(KnowledgeFile.file_id).where(
            KnowledgeFile.kb_id == kb_id,
            KnowledgeFile.deleted_at.is_(None),
            KnowledgeFile.is_folder.is_(False),
            select(KnowledgeDocumentRevision.revision)
            .where(
                KnowledgeDocumentRevision.file_id == KnowledgeFile.file_id,
                KnowledgeDocumentRevision.kb_id == kb_id,
                KnowledgeDocumentRevision.kind == "media",
                KnowledgeDocumentRevision.deleted_at.is_(None),
            )
            .exists(),
        )
        if file_ids is not None:
            query = query.where(KnowledgeFile.file_id.in_(file_ids))
        ids = list(await session.scalars(query.order_by(KnowledgeFile.file_id).limit(1001)))
        if len(ids) > 1000:
            raise InvalidOperationError("媒体检索文件超过一千个，请收窄文件范围", code="media_scope_budget")
        return ids


async def register_media_result(
    session,
    file,
    *,
    prepared_revision: dict,
    expected_source: str,
    expected_basis: str | None,
    job_id: str,
    owner: str,
    source_job_id: str | None = None,
):
    """发布点核对作业 lease 和文件依赖；旧 worker 不能晋升产物。"""
    from yuxi.shared.datetime import utc_now

    job = await session.scalar(select(BackgroundJobRecord).where(BackgroundJobRecord.id == job_id).with_for_update())
    if (
        job is None
        or job.status != "running"
        or job.worker_id != owner
        or job.lease_expires_at is None
        or job.lease_expires_at <= utc_now()
        or job.cancel_requested
    ):
        raise StateConflictError("媒体构建已失去有效执行租约", code="media_job_lost")
    if current_dependencies(file)[0] != expected_source or (expected_basis is not None and current_dependencies(file)[1] != expected_basis):
        raise StateConflictError("媒体构建期间来源已变化", code="stale_source")
    await DocumentResetRepository.require_processing(session, file.kb_id, file.file_id, job_id=source_job_id)
    return await DocumentRevisionRepository().register(session, file, prepared_revision)


def current_dependencies(file) -> tuple[str | None, str | None]:
    """来源身份由现有不可变对象指针决定，不依赖可编辑参数。"""
    uri = source_object_uri(file.minio_url, file.path, file.file_type)
    source = original_revision(file.kb_id, file.file_id, uri) if uri else None
    basis = body_revision(file.kb_id, file.file_id, file.markdown_file) if file.markdown_file else None
    return source, basis


async def load_build_source(
    kb_id: str, file_id: str, *, document_images: bool, processing_job_id: str | None = None
) -> tuple[dict, str, str | None]:
    """领取前读取来源快照，真正发布时仍做依赖 CAS。"""
    async with media_file_snapshot(kb_id, file_id) as (session, file):
        await DocumentResetRepository.require_processing(session, kb_id, file_id, job_id=processing_job_id)
        source, basis = current_dependencies(file)
        if source is None or document_images and basis is None:
            raise InvalidOperationError("媒体索引缺少当前原件或所需正文", code="media_source_unavailable")
        return file_record_to_meta(file), source, basis if document_images else None
