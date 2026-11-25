"""文件派生产物的显式撤销与重新解析。"""

import asyncio
from uuid import uuid4

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.minio.object_urls import parse_minio_url
from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.dispatch import dispatch_job, register_job_in_session
from yuxi.modules.knowledge.base import KnowledgeExecutor
from yuxi.modules.knowledge.file_status import FileStatus
from yuxi.modules.knowledge.repositories.directories import DirectoryRepository
from yuxi.modules.knowledge.repositories.document_resets import RESET_ACTION_LABELS, RESET_JOB_TYPE, DocumentResetRepository
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.runtime import knowledge_base
from yuxi.modules.knowledge.services import document_actions, documents
from yuxi.modules.knowledge.utils import resolve_processing_params, sanitize_processing_params
from yuxi.shared.errors import InvalidOperationError, OperationFailedError, StateConflictError


async def read_markdown_for_edit(kb_id: str, file_id: str) -> dict:
    """严格读取当前正文；无解析指针才允许显式空白人工解析。"""
    async with DocumentResetRepository.markdown_snapshot(kb_id, file_id) as file:
        content = ""
        if file.markdown_file:
            try:
                result = await await_io(documents.get_file_download(kb_id, file_id, variant="parsed"))
                content = result["content"].decode("utf-8")
            except Exception as exc:
                raise OperationFailedError("无法读取现有 Markdown，请恢复解析产物后重试") from exc
        return {
            "content": content,
            "revision": DocumentResetRepository.markdown_revision(file),
            "has_parsed_markdown": bool(file.markdown_file),
        }


async def submit_document_reset(
    kb_id: str,
    file_id: str,
    *,
    action: str,
    operator_id: str,
    params: dict | None = None,
    expected_revision: str | None = None,
    content: str | None = None,
) -> dict:
    """在知识库准入事务内登记撤销，旧排队意图优先完成或显式取消。"""
    if action not in RESET_ACTION_LABELS:
        raise InvalidOperationError("不支持的文档撤销动作")
    if params is not None and not isinstance(params, dict):
        raise InvalidOperationError("解析参数必须是对象")
    if action == "save_markdown" and (not isinstance(expected_revision, str) or not expected_revision or not isinstance(content, str)):
        raise InvalidOperationError("正文和修订必须是字符串")
    async with pg_manager.get_async_session_context() as session:
        kb = await DirectoryRepository().lock(session, kb_id)
        if kb.kb_type.lower() != "milvus":
            raise InvalidOperationError("仅 Milvus 知识库支持撤销文档产物")
        await DocumentResetRepository.require_submission(session, kb_id, destructive=True)
        file, _previous = await DocumentResetRepository.lock_target(session, kb_id, file_id, action=action)
        if action == "save_markdown":
            DocumentResetRepository.require_markdown_revision(file, expected_revision)
        job, _ = await register_job_in_session(
            session,
            name=f"{RESET_ACTION_LABELS[action]} ({file.filename})",
            job_type=RESET_JOB_TYPE,
            payload={
                "kb_id": kb_id,
                "file_id": file_id,
                "generation": int(file.generation),
                "action": action,
                "operator_id": operator_id,
                "params": params or {},
                **(
                    {"expected_revision": expected_revision, "content": content, "source_markdown": file.markdown_file}
                    if action == "save_markdown"
                    else {}
                ),
            },
            payload_match={"kb_id": kb_id},
        )
    await dispatch_job(job.id)
    return {"message": f"{RESET_ACTION_LABELS[action]}任务已提交", "status": "queued", "job_id": job.id}


async def run_document_reset(context) -> dict:
    """严格清理成功后发布目标阶段，解析失败沿用普通解析状态和重试入口。"""
    payload = context.payload
    if not isinstance(payload, dict):
        raise ValueError("文档撤销任务范围无效")
    action, file_id, generation = payload.get("action"), payload.get("file_id"), payload.get("generation")
    if (
        not isinstance(action, str)
        or action not in RESET_ACTION_LABELS
        or not isinstance(file_id, str)
        or not file_id
        or type(generation) is not int
        or generation < 1
    ):
        raise ValueError("文档撤销任务范围无效")
    if not isinstance(payload.get("params", {}), dict):
        raise ValueError("解析参数必须是对象")
    if action == "save_markdown" and (
        not isinstance(payload.get("expected_revision"), str)
        or not payload["expected_revision"]
        or not isinstance(payload.get("content"), str)
        or (payload.get("source_markdown") is not None and not isinstance(payload["source_markdown"], str))
    ):
        raise ValueError("Markdown 保存内容或修订无效")
    kb_id, operator_id = payload["kb_id"], payload["operator_id"]
    label = RESET_ACTION_LABELS[action]
    from yuxi.modules.knowledge.repositories.projections import knowledge_projection_lock

    async with knowledge_projection_lock(kb_id):
        await context.raise_if_cancelled()
        await context.set_progress(5, f"准备{label}")

        scope = {}
        attempt_id, staged_markdown = uuid4().hex, None

        if action == "save_markdown":

            async def preflight(session, _job):
                """暂存前再次裁决修订，过期草稿不能产生对象或撤销读取。"""
                await DirectoryRepository().lock(session, kb_id)
                file, _previous = await DocumentResetRepository.lock_target(
                    session, kb_id, file_id, action=action, generation=generation, resume_job_id=context.job_id
                )
                DocumentResetRepository.require_markdown_revision(file, payload["expected_revision"])
                if file.markdown_file != payload.get("source_markdown"):
                    raise StateConflictError("Markdown 正文已改变，请重新打开后再编辑")

            await context.run_owned_transaction(preflight)

        async def begin(session, _job):
            """在租约事务内发布不可由普通处理绕过的撤销归属。"""
            kb = await DirectoryRepository().lock(session, kb_id)
            file, previous = await DocumentResetRepository.lock_target(
                session, kb_id, file_id, action=action, generation=generation, resume_job_id=context.job_id
            )
            if action == "save_markdown":
                DocumentResetRepository.require_markdown_revision(file, payload["expected_revision"])
                if file.markdown_file != payload.get("source_markdown"):
                    raise StateConflictError("Markdown 正文已改变，请重新打开后再编辑")
            elif action != "clear_index":
                file.markdown_file = None
            if previous is None:
                file.generation += 1
                file.active_generation, file.building_generation = file.generation, None
            file.chunk_count, file.token_count = 0, 0
            scope["generation"] = file.generation
            file.processing_job_id, file.processing_owner = context.job_id, context.worker_id
            file.projection_status = action
            file.error_message = f"{label}尚未完成，请等待后台任务或重试此动作"
            file.projection_error = file.error_message
            if action == "reparse" and payload.get("params"):
                file.processing_params = sanitize_processing_params(
                    resolve_processing_params(
                        kb_additional_params=kb.additional_params,
                        file_processing_params=file.processing_params,
                        request_params=payload["params"],
                    )
                )
            await session.flush()
            stats = await KnowledgeFileRepository().query_kb_file_stats(kb_id, session=session)
            kb.additional_params = {**(kb.additional_params or {}), "stats": stats}

        async def publish(session, _job):
            """唯一发布事务复核归属，清空计数并推进空代次，目录统计同事务更新。"""
            kb = await DirectoryRepository().lock(session, kb_id)
            file, _previous = await DocumentResetRepository.lock_target(
                session, kb_id, file_id, action=action, generation=scope["generation"]
            )
            if file.processing_job_id != context.job_id or file.processing_owner != context.worker_id:
                raise StateConflictError("文件撤销执行权已改变")
            file.chunk_count, file.token_count = 0, 0
            file.status = FileStatus.PARSED if action in {"clear_index", "save_markdown"} else FileStatus.UPLOADED
            if action == "save_markdown":
                if file.markdown_file != payload.get("source_markdown"):
                    raise StateConflictError("Markdown 正文已改变，请重新打开后再编辑")
                file.markdown_file = staged_markdown
            elif action != "clear_index":
                file.markdown_file = None
            file.projection_status, file.projection_error = "pending", None
            file.error_message, file.processing_job_id, file.processing_owner = None, None, None
            file.updated_by = operator_id
            await session.flush()
            stats = await KnowledgeFileRepository().query_kb_file_stats(kb_id, session=session)
            kb.additional_params = {**(kb.additional_params or {}), "stats": stats}

        try:
            if action == "save_markdown":
                executor = await knowledge_base.get_cleanup_executor(kb_id)
                if executor is None:
                    raise InvalidOperationError("知识库执行器不可用")
                staged_markdown = await await_io(executor.save_markdown_artifact(kb_id, file_id, attempt_id, payload["content"]))
            await context.run_owned_transaction(begin)
            await clear_document_derivatives(kb_id, file_id, clear_parsed=action not in {"clear_index", "save_markdown"}, context=context)
            await context.run_owned_transaction(publish)
        finally:
            if action == "save_markdown":
                client = get_minio_client()
                try:
                    # 发布事务可能已提交但返回时失去租约或取消；对象回收依据持久指针。
                    published = await KnowledgeExecutor.markdown_attempt_is_referenced(kb_id, file_id, attempt_id)
                except Exception as exc:
                    logger.warning("无法确认 Markdown 发布事实，保留暂存对象: kb={} file={} error={}", kb_id, file_id, exc)
                else:
                    try:
                        if not published:
                            await await_io(
                                client.adelete_objects_by_prefix(client.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{file_id}/{attempt_id}/")
                            )
                        elif payload.get("source_markdown"):
                            bucket, key = parse_minio_url(payload["source_markdown"])
                            if bucket == client.KB_BUCKETS["parsed"] and key.startswith(f"{kb_id}/parsed/{file_id}/"):
                                await await_io(client.adelete_file(bucket, key))
                    except Exception as exc:
                        logger.warning("Markdown 对象回收失败: kb={} file={} published={} error={}", kb_id, file_id, published, exc)
    if action == "reparse":
        await context.raise_if_cancelled()
        await context.set_progress(65, "正在从原件重新解析")
        result = await document_actions.parse_file(
            kb_id,
            file_id,
            operator_id=operator_id,
            processing_job_id=context.job_id,
            processing_owner=context.worker_id,
        )
    else:
        file = await KnowledgeFileRepository().get_by_file_id(file_id)
        result = documents.file_record_to_meta(file)
    summary = {"kb_id": kb_id, "items": [{"file_id": file_id, "status": result["status"]}], "processed": 1, "succeeded": 1, "failed": 0}
    await context.set_result(summary)
    await context.set_progress(100, f"{label}完成")
    return summary


async def finish_document_reset(session, job_record, result) -> None:
    """终态事务移除无需留在任务历史中的正文。"""
    _discard_markdown_content(job_record)


async def fail_document_reset(session, job_record, error: str) -> None:
    """保留未完成撤销的归属以便同动作恢复，解析阶段失败沿用既有文件状态。"""
    await KnowledgeFileRepository.fail_job_processing_in_session(session, job_id=job_record.id, error=error)
    payload = job_record.payload or {}
    if not isinstance(payload, dict):
        return
    if (
        isinstance(payload.get("action"), str)
        and payload["action"] in RESET_ACTION_LABELS
        and isinstance(payload.get("kb_id"), str)
        and isinstance(payload.get("file_id"), str)
    ):
        await DocumentResetRepository.fail_unfinished(session, job_record, error)
    _discard_markdown_content(job_record)


def _discard_markdown_content(job_record) -> None:
    """只清除已终结的人工正文，保留任务范围和诊断字段。"""
    payload = job_record.payload or {}
    if isinstance(payload, dict) and "content" in payload:
        job_record.payload = {key: value for key, value in payload.items() if key != "content"}


async def clear_document_derivatives(kb_id: str, file_id: str, *, clear_parsed: bool, context) -> None:
    """调用方持有排他投影锁，严格撤销下游；重新解析和正文保存复用此领域原子。"""
    executor = await knowledge_base.get_cleanup_executor(kb_id)
    if executor is None:
        raise InvalidOperationError("知识库执行器不可用")
    await context.set_progress(20, "正在清除图谱、分块和检索索引")
    await executor.delete_file_chunks_only(kb_id, file_id, update_file_stats=False)
    if not clear_parsed:
        return
    await context.set_progress(55, "正在清除 Markdown 和解析图片")
    client = get_minio_client()
    # 两个线程都结束后才能释放投影锁；单侧失败不能让另一侧晚删新 attempt。
    results = await await_io(
        asyncio.gather(
            client.adelete_objects_by_prefix(client.KB_BUCKETS["images"], f"{kb_id}/kb-images/{file_id}/"),
            client.adelete_objects_by_prefix(client.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{file_id}/"),
            return_exceptions=True,
        )
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
