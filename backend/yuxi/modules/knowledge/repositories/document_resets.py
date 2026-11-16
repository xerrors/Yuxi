"""文档撤销的任务准入、文件归属与失败恢复事实。"""

import hashlib
import json
from contextlib import asynccontextmanager

from sqlalchemy import select

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.file_status import FileStatus
from yuxi.modules.knowledge.models import KnowledgeFile
from yuxi.shared.errors import InvalidOperationError, ResourceNotFoundError, StateConflictError

RESET_JOB_TYPE = "knowledge_document_reset"
RESET_ACTION_LABELS = {
    "clear_index": "清除入库数据",
    "clear_parse": "清除解析结果",
    "reparse": "重新解析",
    "save_markdown": "保存 Markdown",
}
KNOWLEDGE_JOB_TYPES = {
    "knowledge_parse", "knowledge_index", "knowledge_ingest", "knowledge_graph_index", "knowledge_media_index", RESET_JOB_TYPE
}


class DocumentResetRepository:
    """在调用方事务内裁决撤销和普通处理的互斥。"""

    @staticmethod
    async def require_submission(session, kb_id: str, *, destructive: bool = False) -> None:
        """调用方持有知识库准入锁；普通动作仅阻止活动撤销。"""
        types = KNOWLEDGE_JOB_TYPES if destructive else {RESET_JOB_TYPE}
        active = await session.scalar(
            select(BackgroundJobRecord.id)
            .where(
                BackgroundJobRecord.type.in_(types),
                BackgroundJobRecord.status.in_({"pending", "running"}),
                BackgroundJobRecord.payload["kb_id"].as_string() == kb_id,
            )
            .limit(1)
        )
        if active is not None:
            raise StateConflictError(
                "该知识库有排队或运行中的处理任务，请等待完成后再操作" if destructive else "该知识库正在撤销文档产物，请等待完成后再处理"
            )

    @staticmethod
    async def require_processing(
        session, kb_id: str, file_id: str | None = None, *, job_id: str | None = None, file_ids: list[str] | None = None
    ) -> None:
        """executor 的最终处理边界拒绝活动撤销及文件未完成的撤销意图。"""
        active_query = select(BackgroundJobRecord.id).where(
            BackgroundJobRecord.type == RESET_JOB_TYPE,
            BackgroundJobRecord.status.in_({"pending", "running"}),
            BackgroundJobRecord.payload["kb_id"].as_string() == kb_id,
        )
        if job_id is not None:
            active_query = active_query.where(BackgroundJobRecord.id != job_id)
        if await session.scalar(active_query.limit(1)) is not None:
            raise StateConflictError("该知识库正在撤销文档产物，请等待完成后再处理")
        blocked = select(KnowledgeFile.projection_status).where(
            KnowledgeFile.kb_id == kb_id,
            KnowledgeFile.deleted_at.is_(None),
            KnowledgeFile.projection_status.in_(RESET_ACTION_LABELS),
        )
        if file_id is not None:
            blocked = blocked.where(KnowledgeFile.file_id == file_id)
        if file_ids is not None:
            blocked = blocked.where(KnowledgeFile.file_id.in_(file_ids))
        if job_id is not None:
            blocked = blocked.where(KnowledgeFile.processing_job_id.is_distinct_from(job_id))
        action = await session.scalar(blocked.limit(1))
        if action is not None:
            raise StateConflictError(f"{RESET_ACTION_LABELS[action]}尚未完成，请先重试此动作")

    async def require_processable(self, kb_id: str, file_id: str | None = None, *, job_id: str | None = None) -> None:
        """为图谱执行器提供不持久化任何状态的准入复核。"""
        async with pg_manager.get_async_session_context() as session:
            await self.require_processing(session, kb_id, file_id, job_id=job_id)

    @staticmethod
    async def lock_target(
        session, kb_id: str, file_id: str, *, action: str, generation: int | None = None, resume_job_id: str | None = None
    ):
        """锁住有效文件并限制失败清理只能由相同动作恢复。"""
        file = await session.scalar(
            select(KnowledgeFile)
            .where(
                KnowledgeFile.kb_id == kb_id,
                KnowledgeFile.file_id == file_id,
                KnowledgeFile.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if file is None:
            raise ResourceNotFoundError("文件不存在或不属于该知识库")
        if file.is_folder:
            raise InvalidOperationError("目录不能撤销文档产物")
        if file.status not in {
            FileStatus.UPLOADED,
            FileStatus.ERROR_PARSING,
            FileStatus.PARSED,
            FileStatus.INDEXED,
            FileStatus.ERROR_INDEXING,
            "done",
            "failed",
        }:
            raise StateConflictError("文件当前状态不能撤销，请等待处理完成")
        previous_reset = file.projection_status if file.projection_status in RESET_ACTION_LABELS else None
        if generation is not None and file.generation != generation:
            if not (file.processing_job_id == resume_job_id and file.generation == generation + 1):
                raise StateConflictError("文件代次已改变，请刷新后重新提交")
        if previous_reset is not None and previous_reset != action:
            raise StateConflictError(f"{RESET_ACTION_LABELS[previous_reset]}尚未完成，请先重试此动作")
        if action == "clear_index" and not file.markdown_file:
            raise InvalidOperationError("文件没有可保留的解析 Markdown")
        if action in {"clear_parse", "reparse"} and not (file.minio_url or file.path):
            raise InvalidOperationError("文件没有可保留的原件")
        return file, previous_reset

    @staticmethod
    def markdown_revision(file, *, generation: int | None = None) -> str:
        """将文件代次和不可变正文指针编码为不透明修订。"""
        value = [file.kb_id, file.file_id, file.generation if generation is None else generation, file.markdown_file]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def require_markdown_revision(file, expected_revision: str) -> None:
        """仅同一未完成保存允许原稿修订跨过已经撤销的一个空代次。"""
        allowed = {DocumentResetRepository.markdown_revision(file)}
        if file.projection_status == "save_markdown" and file.generation > 1:
            allowed.add(DocumentResetRepository.markdown_revision(file, generation=file.generation - 1))
        if expected_revision not in allowed:
            raise StateConflictError("Markdown 修订已改变，请重新打开后再编辑")

    @staticmethod
    @asynccontextmanager
    async def markdown_snapshot(kb_id: str, file_id: str):
        """在正文读取期间固定投影与文件修订，读取失败不解释为空内容。"""
        from yuxi.modules.knowledge.repositories.projections import acquire_knowledge_projection_lock

        async with pg_manager.get_async_session_context() as session:
            await acquire_knowledge_projection_lock(session, kb_id, shared=True)
            from yuxi.modules.knowledge.repositories.directories import DirectoryRepository

            kb = await DirectoryRepository().lock(session, kb_id)
            if kb.kb_type.lower() != "milvus":
                raise InvalidOperationError("仅 Milvus 知识库支持编辑 Markdown")
            file, _previous = await DocumentResetRepository.lock_target(session, kb_id, file_id, action="save_markdown")
            yield file

    @staticmethod
    async def fail_unfinished(session, job_record, error: str) -> None:
        """保留撤销指针和可见恢复提示，删除后的文件不被旧失败回调复活。"""
        file = await session.scalar(
            select(KnowledgeFile)
            .where(
                KnowledgeFile.file_id == job_record.payload["file_id"],
                KnowledgeFile.kb_id == job_record.payload["kb_id"],
                KnowledgeFile.deleted_at.is_(None),
                KnowledgeFile.processing_job_id == job_record.id,
            )
            .with_for_update()
        )
        if file is not None:
            label = RESET_ACTION_LABELS[job_record.payload["action"]]
            file.projection_status = job_record.payload["action"]
            file.projection_error = str(error)[:1000]
            file.error_message = f"{label}未完成，请重试此动作；普通处理须先恢复，详情见后台任务"
