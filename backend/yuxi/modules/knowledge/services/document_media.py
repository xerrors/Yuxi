"""文档解析完成后，在正文发布事务内登记图片媒体索引。"""

from sqlalchemy import select

from yuxi.modules.background_jobs.dispatch import register_job_in_session
from yuxi.modules.knowledge.media.repository import MEDIA_JOB_TYPE, active_media_build, current_dependencies, require_media_manage
from yuxi.modules.knowledge.media.schemas import DocumentImageAutoIndexSettings, IndexMediaInput, MediaConfig
from yuxi.modules.knowledge.media.settings import DOCUMENT_IMAGE_AUTO_INDEX_KEY
from yuxi.modules.knowledge.models import KnowledgeBase
from yuxi.modules.knowledge.services.structure import document_images as extract_document_images
from yuxi.shared.errors import InvalidOperationError, StateConflictError


async def register_document_image_index(
    session,
    *,
    kb_id: str,
    file,
    operator_id: str | None,
    additional_params: dict,
    markdown_content: str,
    source_job_id: str | None = None,
) -> dict | None:
    """与正文修订共同提交解析图片索引意图，调用方提交后再投递。"""
    raw_settings = (additional_params or {}).get(DOCUMENT_IMAGE_AUTO_INDEX_KEY)
    if not raw_settings:
        return None

    settings = DocumentImageAutoIndexSettings.model_validate(raw_settings)
    if not settings.enabled:
        return None

    images = extract_document_images(markdown_content, kb_id=kb_id, file_id=file.file_id)
    if not any(image["readable"] for image in images):
        return {"status": "skipped", "reason": "no_images"}
    if not operator_id:
        raise InvalidOperationError("解析任务缺少媒体作业归属")

    kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
    if kb is None or kb.deleted_at is not None or kb.kb_type != "milvus":
        raise InvalidOperationError("媒体索引仅支持有效的 Milvus 知识库")
    await require_media_manage(session, kb, operator_id)

    source_revision, basis_document_revision = current_dependencies(file)
    if source_revision is None or basis_document_revision is None:
        raise InvalidOperationError("媒体索引缺少当前原件或正文")

    request = IndexMediaInput(
        config=MediaConfig(embedding_model=settings.embedding_model),
        document_images=True,
    )
    payload = {
        "kb_id": kb_id,
        "file_id": file.file_id,
        "operator_id": operator_id,
        "source_revision": source_revision,
        "basis_document_revision": basis_document_revision,
        **request.model_dump(),
    }
    if source_job_id is not None:
        payload["source_job_id"] = source_job_id
    # 解析事务已持有文件锁，普通读取避免与媒体终结的 job -> file 锁序冲突。
    active = await active_media_build(session, kb_id, file.file_id, lock=False)
    if active is not None:
        active_payload = {key: value for key, value in active.payload.items() if key != "source_job_id"}
        comparable_payload = {key: value for key, value in payload.items() if key != "source_job_id"}
        if active_payload != comparable_payload:
            raise StateConflictError("同一文件已有不同参数的媒体构建，请等待完成", code="media_build_busy")
        return {"status": "queued", "job_id": active.id, "created": False}

    job, created = await register_job_in_session(
        session,
        name=f"媒体索引 ({file.filename})",
        job_type=MEDIA_JOB_TYPE,
        payload=payload,
        timeout_seconds=1800,
    )
    return {"status": "queued", "job_id": job.id, "created": created}
