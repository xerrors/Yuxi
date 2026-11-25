"""知识库文档解析与入库的提交和单文件执行用例。"""

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.dispatch import dispatch_job, register_job_in_session
from yuxi.modules.knowledge.file_status import needs_parse_before_index
from yuxi.modules.knowledge.read_models import KnowledgeBaseDetail
from yuxi.modules.knowledge.repositories.directories import DirectoryRepository
from yuxi.modules.knowledge.repositories.document_resets import DocumentResetRepository
from yuxi.modules.knowledge.services import documents, statistics

PENDING_PARSE_STATUSES = ["uploaded"]
PENDING_INDEX_STATUSES = ["parsed", "error_indexing"]


async def submit_document_action(
    *,
    kb_id: str,
    file_ids: list[str],
    params: dict,
    operator_id: str,
    kb_info: KnowledgeBaseDetail,
    action: str,
) -> dict:
    """提交指定文件的解析或入库任务。"""
    label = _action_label(action)
    job, _ = await submit_knowledge_job(
        name=f"文档{label} ({kb_info.name})",
        job_type=f"knowledge_{action}",
        payload={
            "kb_id": kb_id,
            "file_ids": file_ids,
            "params": params,
            "operator_id": operator_id,
        },
    )
    return {"message": f"{label}任务已提交", "status": "queued", "job_id": job.id}


async def submit_pending_document_action(
    *,
    kb_id: str,
    params: dict,
    operator_id: str,
    kb_info: KnowledgeBaseDetail,
    action: str,
) -> dict:
    """提交按状态全量的解析或入库任务，重复请求复用活动作业。"""
    label = _action_label(action)
    if action == "parse":
        pending_count = kb_info.pending_parse_count
        statuses = PENDING_PARSE_STATUSES
    else:
        pending_count = kb_info.pending_index_count
        statuses = PENDING_INDEX_STATUSES

    if pending_count <= 0:
        return {"message": f"没有待{label}文档", "status": "success", "queued_count": 0}

    job, created = await submit_knowledge_job(
        name=f"待{label}文档{label} ({kb_info.name})",
        job_type=f"knowledge_{action}",
        payload={
            "kb_id": kb_id,
            "scope": "pending",
            "action": action,
            "statuses": statuses,
            "count": pending_count,
            "params": params,
            "operator_id": operator_id,
        },
        payload_match={"kb_id": kb_id, "scope": "pending", "action": action},
    )
    return {
        "message": f"{label}任务已提交" if created else f"已有待{label}任务正在执行",
        "status": "queued",
        "job_id": job.id,
        "queued_count": pending_count,
    }


async def parse_file(
    kb_id: str,
    file_id: str,
    operator_id: str | None = None,
    *,
    params: dict | None = None,
    processing_job_id: str | None = None,
    processing_owner: str | None = None,
    recover_missing_markdown: bool = False,
) -> dict:
    """解析为 Markdown，并刷新此次执行后的知识库统计。

    只读连接器的文档能力由其执行器统一拒绝。
    """
    from yuxi.modules.knowledge.runtime import knowledge_base

    config = await knowledge_base.get_kb_config(kb_id)
    executor = await knowledge_base.get_executor_for_type(config.kb_type)
    return await statistics.run_with_stats_refresh(
        kb_id,
        executor.parse_file(
            kb_id,
            file_id,
            operator_id,
            params=params,
            additional_params=config.additional_params,
            processing_job_id=processing_job_id,
            processing_owner=processing_owner,
            recover_missing_markdown=recover_missing_markdown,
        ),
    )


async def index_file(
    kb_id: str,
    file_id: str,
    operator_id: str | None = None,
    params: dict | None = None,
    *,
    processing_job_id: str | None = None,
    processing_owner: str | None = None,
) -> dict:
    """需要时先补解析，再用同一作业执行权和参数入库。"""
    from yuxi.modules.knowledge.runtime import knowledge_base

    config = await knowledge_base.get_kb_config(kb_id)
    executor = await knowledge_base.get_executor_for_type(config.kb_type)
    meta = await documents.load_file_meta(kb_id, file_id)
    if needs_parse_before_index(meta):
        await parse_file(
            kb_id,
            file_id,
            operator_id,
            params=params,
            processing_job_id=processing_job_id,
            processing_owner=processing_owner,
            recover_missing_markdown=True,
        )
    return await statistics.run_with_stats_refresh(
        kb_id,
        executor.index_file(
            kb_id,
            file_id,
            operator_id,
            params=params,
            embedding_model_spec=config.embedding_model_spec,
            additional_params=config.additional_params,
            processing_job_id=processing_job_id,
            processing_owner=processing_owner,
        ),
    )


def _action_label(action: str) -> str:
    return "解析" if action == "parse" else "入库"


async def submit_knowledge_job(*, name: str, job_type: str, payload: dict, payload_match: dict | None = None):
    """普通处理在同一知识库短锁内拒绝活动撤销，登记提交后才投递。"""
    async with pg_manager.get_async_session_context() as session:
        await DirectoryRepository().lock(session, payload["kb_id"])
        await DocumentResetRepository.require_processing(
            session, payload["kb_id"], payload.get("file_id"), file_ids=payload.get("file_ids")
        )
        job, created = await register_job_in_session(
            session,
            name=name,
            job_type=job_type,
            payload=payload,
            payload_match=payload_match,
        )
    if created:
        await dispatch_job(job.id)
    return job, created
