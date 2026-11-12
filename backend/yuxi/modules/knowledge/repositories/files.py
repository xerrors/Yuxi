from __future__ import annotations

from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.file_status import FileStatus, status_filter_values
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeFile, KnowledgeProjectionOutbox
from yuxi.modules.knowledge.utils.kb_utils import content_parser_id
from yuxi.shared.datetime import utc_now

# 一次文件过滤最多接受的标识数量。文件名搜索和检索 file_ids 共用。
MAX_FILTER_FILE_IDS = 10_000


def first_matching_fingerprint(records, parser_id: str):
    """候选已按创建时间排序，返回解析器一致的第一条。"""

    for record in records:
        if content_parser_id(getattr(record, "processing_params", None)) == parser_id:
            return record
    return None


# 列表排序只接受这些字段和 ant-design-vue 的方向；未传时保持默认顺序。
_DOCUMENT_SORT_FIELDS = frozenset({"filename", "content_amount", "status", "created_at"})
_DOCUMENT_SORT_ORDERS = frozenset({"ascend", "descend"})
# 与前端 knowledge_file_policy.js 的 STATUS_SORT_ORDER 一致；未知状态更靠后。
_STATUS_SORT_WEIGHTS = (
    ("done", 1),
    ("indexed", 1),
    ("processing", 2),
    ("indexing", 2),
    ("parsing", 2),
    ("waiting", 3),
    ("uploaded", 3),
    ("parsed", 3),
    ("failed", 4),
    ("error_indexing", 4),
    ("error_parsing", 4),
)
_UNKNOWN_STATUS_SORT_WEIGHT = 5


class KnowledgeFileRepository:
    """知识库文件与目录节点事实的持久化查询、写入与删除登记。"""

    async def get_by_file_id(self, file_id: str, *, include_deleted: bool = False) -> KnowledgeFile | None:
        filters = [KnowledgeFile.file_id == file_id]
        if not include_deleted:
            filters.append(KnowledgeFile.deleted_at.is_(None))
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(*filters))
            return result.scalar_one_or_none()

    async def list_by_kb_id(self, kb_id: str) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.deleted_at.is_(None)))
            return list(result.scalars().all())

    async def list_documents(
        self,
        *,
        kb_id: str,
        parent_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
        recursive: bool = False,
        files_only: bool = False,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> tuple[list[KnowledgeFile], int]:
        """按筛选结果整体排序后再分页；非法排序在查询前失败。"""
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 100), 1), 500)
        offset = (page - 1) * page_size
        order_by = self._document_order_by(sort_by, sort_order)
        has_status_filter = self._status_condition(status) is not None
        effective_recursive = recursive and has_status_filter
        filters = self._document_filters(
            kb_id=kb_id,
            parent_id=parent_id,
            status=status,
            recursive=effective_recursive,
            files_only=files_only,
        )

        async with pg_manager.get_async_session_context() as session:
            total_result = await session.execute(select(func.count()).select_from(KnowledgeFile).where(*filters))
            total = int(total_result.scalar_one() or 0)

            result = await session.execute(select(KnowledgeFile).where(*filters).order_by(*order_by).offset(offset).limit(page_size))
            return list(result.scalars().all()), total

    @staticmethod
    def _normalize_document_sort(sort_by: str | None, sort_order: str | None) -> tuple[str | None, str | None]:
        """空排序表示默认顺序；只给字段或方向、或给出未支持的值都拒绝。"""
        field = (sort_by or "").strip() or None
        order = (sort_order or "").strip() or None
        if field is None and order is None:
            return None, None
        if field not in _DOCUMENT_SORT_FIELDS or order not in _DOCUMENT_SORT_ORDERS:
            raise ValueError("Unsupported document sort field or direction")
        return field, order

    def _document_order_by(self, sort_by: str | None, sort_order: str | None):
        """生成全量排序。filename 两种方向都先排文件夹；其他列文件夹与文件一起排。"""
        field, order = self._normalize_document_sort(sort_by, sort_order)
        if field is None:
            return (
                KnowledgeFile.is_folder.desc(),
                func.lower(KnowledgeFile.filename).asc(),
                KnowledgeFile.created_at.desc(),
                KnowledgeFile.file_id.asc(),
            )

        descending = order == "descend"
        tie = KnowledgeFile.file_id.asc()
        if field == "filename":
            name = func.lower(KnowledgeFile.filename)
            return (KnowledgeFile.is_folder.desc(), name.desc() if descending else name.asc(), tie)
        if field == "content_amount":
            amount = func.coalesce(KnowledgeFile.token_count, 0)
            return (amount.desc() if descending else amount.asc(), tie)
        if field == "created_at":
            created = KnowledgeFile.created_at
            # 缺失时间视为最早，与前端 compareFileCreatedAt 的空值等价。
            created_order = created.desc().nulls_last() if descending else created.asc().nulls_first()
            return (created_order, tie)

        status_weight = case(
            *((KnowledgeFile.status == status, rank) for status, rank in _STATUS_SORT_WEIGHTS),
            else_=_UNKNOWN_STATUS_SORT_WEIGHT,
        )
        return (status_weight.desc() if descending else status_weight.asc(), tie)

    @staticmethod
    def _status_condition(status: str | None):
        values = status_filter_values(status)
        return KnowledgeFile.status.in_(values) if values else None

    @staticmethod
    def _parent_condition(parent_id: str | None):
        if parent_id:
            return KnowledgeFile.parent_id == parent_id
        return KnowledgeFile.parent_id.is_(None)

    def _document_filters(
        self,
        *,
        kb_id: str,
        parent_id: str | None,
        status: str | None,
        recursive: bool,
        files_only: bool,
    ) -> list:
        filters = [KnowledgeFile.kb_id == kb_id, KnowledgeFile.deleted_at.is_(None)]
        if not recursive:
            filters.append(self._parent_condition(parent_id))
        if files_only:
            filters.append(KnowledgeFile.is_folder.is_(False))

        status_condition = self._status_condition(status)
        if status_condition is not None:
            # 状态是文件语义，按状态过滤时隐含只返回文件。
            filters.append(KnowledgeFile.is_folder.is_(False))
            filters.append(status_condition)

        return filters

    async def count_children_by_parent_ids(self, *, kb_id: str, parent_ids: list[str]) -> dict[str, int]:
        if not parent_ids:
            return {}

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.parent_id, func.count())
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.deleted_at.is_(None),
                    KnowledgeFile.parent_id.in_(parent_ids),
                )
                .group_by(KnowledgeFile.parent_id)
            )
            return {str(parent_id): int(count or 0) for parent_id, count in result.all() if parent_id}

    async def search_files(
        self,
        *,
        kb_id: str,
        filename_query: str | None = None,
        statuses: set[str] | None = None,
        offset: int = 0,
        limit: int = 100,
        files_only: bool = True,
    ) -> tuple[list[KnowledgeFile], int]:
        filters = [KnowledgeFile.kb_id == kb_id, KnowledgeFile.deleted_at.is_(None)]
        if files_only:
            filters.append(KnowledgeFile.is_folder.is_(False))
        if statuses is not None:
            filters.append(KnowledgeFile.status.in_(statuses))

        normalized_query = (filename_query or "").strip().lower()
        if normalized_query:
            escaped_query = self._escape_like_pattern(normalized_query)
            filters.append(func.lower(KnowledgeFile.filename).like(f"%{escaped_query}%", escape="\\"))

        normalized_offset = max(int(offset or 0), 0)
        normalized_limit = min(max(int(limit or 100), 1), 10_000)

        async with pg_manager.get_async_session_context() as session:
            total_result = await session.execute(select(func.count()).select_from(KnowledgeFile).where(*filters))
            total = int(total_result.scalar_one() or 0)
            result = await session.execute(
                select(KnowledgeFile)
                .where(*filters)
                .order_by(KnowledgeFile.updated_at.desc(), KnowledgeFile.file_id.asc())
                .offset(normalized_offset)
                .limit(normalized_limit)
            )
            return list(result.scalars().all()), total

    async def list_file_ids_by_filename_contains(
        self,
        *,
        kb_id: str,
        filename_pattern: str,
        limit: int = MAX_FILTER_FILE_IDS,
    ) -> list[str]:
        normalized_pattern = filename_pattern.replace("%", "").strip().lower()
        if not normalized_pattern:
            return []

        escaped_pattern = self._escape_like_pattern(normalized_pattern)

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    KnowledgeFile.deleted_at.is_(None),
                    func.lower(KnowledgeFile.filename).like(f"%{escaped_pattern}%", escape="\\"),
                )
                .order_by(KnowledgeFile.file_id.asc())
                .limit(min(max(int(limit or 100), 1), MAX_FILTER_FILE_IDS))
            )
            return [str(file_id) for file_id in result.scalars().all()]

    async def list_live_file_ids(self, *, kb_id: str, file_ids: list[str]) -> list[str]:
        """返回目标知识库中仍然存在的非目录文件。"""

        if not file_ids:
            return []
        async with pg_manager.get_async_session_context() as session:
            result = await session.scalars(
                select(KnowledgeFile.file_id).where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.file_id.in_(file_ids),
                    KnowledgeFile.deleted_at.is_(None),
                    KnowledgeFile.is_folder.is_(False),
                )
            )
            return [str(file_id) for file_id in result.all()]

    async def find_live_file_by_fingerprint(
        self,
        session: AsyncSession,
        *,
        kb_id: str,
        content_hash: str,
        file_size: int,
        parser_id: str,
    ) -> KnowledgeFile | None:
        """在同一知识库内按内容哈希、大小和解析器查找未删除文件。"""

        result = await session.scalars(
            select(KnowledgeFile)
            .where(
                KnowledgeFile.kb_id == kb_id,
                KnowledgeFile.deleted_at.is_(None),
                KnowledgeFile.is_folder.is_(False),
                KnowledgeFile.content_hash == content_hash,
                KnowledgeFile.file_size == file_size,
            )
            .order_by(KnowledgeFile.created_at.asc(), KnowledgeFile.file_id.asc())
        )
        return first_matching_fingerprint(result.all(), parser_id)

    @staticmethod
    def _escape_like_pattern(value: str) -> str:
        """转义 LIKE 通配符；反斜杠必须最先转义，供 escape="\\" 的模糊匹配复用。"""
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    async def list_file_ids_by_exact_statuses(
        self,
        *,
        kb_id: str,
        statuses: list[str],
        after_file_id: str | None = None,
        limit: int = 500,
    ) -> list[str]:
        normalized_statuses = [status for status in statuses if status]
        if not normalized_statuses:
            return []

        normalized_limit = min(max(int(limit or 100), 1), 500)
        filters = [
            KnowledgeFile.kb_id == kb_id,
            KnowledgeFile.is_folder.is_(False),
            KnowledgeFile.status.in_(normalized_statuses),
        ]
        if after_file_id:
            filters.append(KnowledgeFile.file_id > after_file_id)

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id).where(*filters).order_by(KnowledgeFile.file_id.asc()).limit(normalized_limit)
            )
            return [str(file_id) for file_id in result.scalars().all()]

    async def exists_by_filename(self, *, kb_id: str, filename: str) -> bool:
        """沿真实父关系解析已有文件的根相对路径。"""
        from yuxi.modules.knowledge.names import source_segments
        from yuxi.modules.knowledge.repositories.directories import DirectoryRepository

        segments = source_segments(filename)
        parent_id = None
        async with pg_manager.get_async_session_context() as session:
            for index, segment in enumerate(segments):
                node = await DirectoryRepository().named(session, kb_id, parent_id, segment)
                if node is None:
                    return False
                if index == len(segments) - 1:
                    return not node.is_folder
                if not node.is_folder:
                    return False
                parent_id = node.file_id
        return False

    async def list_active_generations(self, kb_id: str) -> list[tuple[str, int]]:
        """只读取可见文件的 active 代次，供向量库在截取 top-k 前过滤。"""
        async with pg_manager.get_async_session_context() as session:
            rows = await session.execute(
                select(KnowledgeFile.file_id, KnowledgeFile.active_generation)
                .join(KnowledgeBase, KnowledgeBase.kb_id == KnowledgeFile.kb_id)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.deleted_at.is_(None),
                    KnowledgeBase.deleted_at.is_(None),
                    KnowledgeFile.is_folder.is_(False),
                )
            )
            return [(row.file_id, int(row.active_generation)) for row in rows]

    async def get_chunk_sources_by_file_ids(self, *, kb_id: str, file_ids: list[str]) -> dict[str, dict]:
        """批量读取检索分片的文件来源与总分片数。"""
        normalized_ids = [file_id for file_id in file_ids if file_id]
        if not normalized_ids:
            return {}

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(
                    KnowledgeFile.file_id,
                    KnowledgeFile.filename,
                    KnowledgeFile.chunk_count,
                    KnowledgeFile.active_generation,
                ).where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.deleted_at.is_(None),
                    KnowledgeFile.file_id.in_(normalized_ids),
                )
            )
            return {
                str(file_id): {
                    "source": str(filename or ""),
                    "chunk_count": int(chunk_count or 0),
                    "active_generation": int(active_generation or 1),
                }
                for file_id, filename, chunk_count, active_generation in result.all()
            }

    async def count_all(self) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(func.count()).select_from(KnowledgeFile).where(KnowledgeFile.deleted_at.is_(None)))
            return int(result.scalar() or 0)

    async def aggregate_dashboard_stats(self) -> list[tuple[str, int, int, int]]:
        """按文件类型聚合真实文件数、大小与 Chunk 数。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(
                    KnowledgeFile.file_type,
                    func.count(KnowledgeFile.file_id),
                    func.coalesce(func.sum(KnowledgeFile.file_size), 0),
                    func.coalesce(func.sum(KnowledgeFile.chunk_count), 0),
                )
                .where(
                    KnowledgeFile.deleted_at.is_(None),
                    or_(KnowledgeFile.is_folder.is_(False), KnowledgeFile.is_folder.is_(None)),
                )
                .group_by(KnowledgeFile.file_type)
            )
            return [
                (str(file_type or "unknown"), int(count or 0), int(size or 0), int(nodes or 0))
                for file_type, count, size, nodes in result.all()
            ]

    async def get_kb_file_stats(self, kb_id: str) -> dict[str, Any]:
        """从 PostgreSQL 读取当前统计，避免文件提交后列表仍引用旧缓存。"""
        async with pg_manager.get_async_session_context() as session:
            return await self.query_kb_file_stats(kb_id, session=session)

    async def query_kb_file_stats(self, kb_id: str, *, session: AsyncSession) -> dict[str, Any]:
        """一次分组聚合提供同一快照中的全库统计和逐状态计数。"""
        rows = await session.execute(
            select(
                KnowledgeFile.is_folder,
                KnowledgeFile.status,
                func.count().label("n"),
                func.coalesce(func.sum(KnowledgeFile.file_size), 0).label("size"),
                func.coalesce(func.sum(KnowledgeFile.chunk_count), 0).label("chunks"),
                func.coalesce(func.sum(KnowledgeFile.token_count), 0).label("tokens"),
            )
            .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.deleted_at.is_(None))
            .group_by(KnowledgeFile.is_folder, KnowledgeFile.status)
        )
        stats = dict.fromkeys(["row_count", "file_count", "folder_count", "total_size", "chunk_count", "token_count"], 0)
        counts = {}
        for row in rows:
            stats["row_count"] += int(row.n)
            if row.is_folder:
                stats["folder_count"] += int(row.n)
                continue
            stats["file_count"] += int(row.n)
            stats["total_size"] += int(row.size)
            stats["chunk_count"] += int(row.chunks)
            stats["token_count"] += int(row.tokens)
            status = row.status or FileStatus.UPLOADED
            counts[status] = counts.get(status, 0) + int(row.n)
        return {
            **stats,
            "pending_parse_count": counts.get(FileStatus.UPLOADED, 0),
            "pending_index_count": counts.get(FileStatus.PARSED, 0) + counts.get(FileStatus.ERROR_INDEXING, 0),
            "processing_count": sum(counts.get(status, 0) for status in ["processing", "waiting", "parsing", "indexing"]),
            "file_status_counts": counts,
        }

    _writable_fields = {
        "kb_id",
        "parent_id",
        "filename",
        "original_filename",
        "file_type",
        "path",
        "minio_url",
        "markdown_file",
        "status",
        "content_hash",
        "file_size",
        "chunk_count",
        "token_count",
        "content_type",
        "processing_params",
        "is_folder",
        "error_message",
        "processing_job_id",
        "processing_owner",
        "created_by",
        "updated_by",
        "generation",
        "active_generation",
        "building_generation",
        "deleted_at",
        "projection_status",
        "projection_error",
    }

    @classmethod
    def _sanitize_data(cls, data: dict[str, Any]) -> dict[str, Any]:
        sanitized = {key: value for key, value in data.items() if key in cls._writable_fields}
        if sanitized:
            sanitized["updated_at"] = utc_now()
        return sanitized

    async def upsert(self, file_id: str, data: dict[str, Any]) -> KnowledgeFile:
        """写入文件事实。"""
        sanitized_data = self._sanitize_data(data)
        async with pg_manager.get_async_session_context() as session:
            # 先锁知识库再锁文件，与 fail_job_processing_in_session 保持同一 KB → File 锁序。
            kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == sanitized_data["kb_id"]).with_for_update())
            if kb is None or kb.deleted_at is not None:
                raise ValueError("Knowledge base not found or deleted")
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id).with_for_update())
            record = result.scalar_one_or_none()
            if record is None:
                record = KnowledgeFile(file_id=file_id, **sanitized_data)
                session.add(record)
            else:
                for key, value in sanitized_data.items():
                    setattr(record, key, value)
            await session.flush()

            return record

    async def update_fields(
        self,
        *,
        file_id: str,
        data: dict[str, Any],
        kb_id: str | None = None,
    ) -> KnowledgeFile | None:
        sanitized_data = self._sanitize_data(data)
        if not sanitized_data:
            return await self.get_by_file_id(file_id)

        filters = [KnowledgeFile.file_id == file_id]
        if kb_id:
            filters.append(KnowledgeFile.kb_id == kb_id)

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(*filters))
            record = result.scalar_one_or_none()
            if record is None:
                return None
            if "processing_params" in sanitized_data:
                from yuxi.modules.knowledge.repositories.document_resets import DocumentResetRepository

                await DocumentResetRepository.require_processing(session, record.kb_id, file_id)
            for key, value in sanitized_data.items():
                setattr(record, key, value)
            return record

    async def update_fields_if_status(
        self,
        *,
        kb_id: str,
        file_id: str,
        allowed_statuses: set[str],
        data: dict[str, Any],
        processing_job_id: str | None = None,
        processing_owner: str | None = None,
        processing_params: dict | None = None,
        kb_additional_params: dict | None = None,
        missing_markdown_statuses: set[str] | None = None,
    ) -> KnowledgeFile | None:
        """条件领取或完成文件处理；参数合并和执行权发布共享同一事务。

        missing_markdown_statuses 只在同一次行锁里额外放行没有正文的非目录文件。
        """
        lease_job_id = processing_job_id or data.get("processing_job_id")
        lease_owner = processing_owner or data.get("processing_owner")
        if (lease_job_id is None) != (lease_owner is None):
            raise ValueError("File processing owner must include job and worker")
        sanitized_data = self._sanitize_data(data)
        if not sanitized_data:
            return await self.get_by_file_id(file_id)

        status_match = KnowledgeFile.status.in_(sorted(allowed_statuses))
        if missing_markdown_statuses:
            status_match = or_(
                status_match,
                and_(
                    KnowledgeFile.status.in_(sorted(missing_markdown_statuses)),
                    KnowledgeFile.is_folder.is_(False),
                    or_(KnowledgeFile.markdown_file.is_(None), KnowledgeFile.markdown_file == ""),
                ),
            )
        filters = [
            KnowledgeFile.kb_id == kb_id,
            KnowledgeFile.file_id == file_id,
            KnowledgeFile.deleted_at.is_(None),
            status_match,
        ]
        if processing_job_id is not None:
            filters.append(KnowledgeFile.processing_job_id == processing_job_id)
        if processing_owner is not None:
            filters.append(KnowledgeFile.processing_owner == processing_owner)
        async with pg_manager.get_async_session_context() as session:
            if data.get("status") in {FileStatus.PARSING, FileStatus.INDEXING}:
                from yuxi.modules.knowledge.repositories.document_resets import DocumentResetRepository

                await DocumentResetRepository.require_processing(
                    session, kb_id, file_id, job_id=lease_job_id if lease_owner is not None else None
                )
            job_record = None
            if lease_job_id is not None and lease_owner is not None:
                # 带租约的更新先锁作业行并确认租约仍存活，过期 worker 的晚写在此被拒绝。
                job_record = await session.scalar(
                    select(BackgroundJobRecord)
                    .where(
                        BackgroundJobRecord.id == lease_job_id,
                        BackgroundJobRecord.status == "running",
                        BackgroundJobRecord.worker_id == lease_owner,
                    )
                    .with_for_update()
                )
                if job_record is None:
                    return None
            file_record = await session.scalar(select(KnowledgeFile).where(*filters).with_for_update())
            if file_record is None:
                return None
            if lease_job_id is not None:
                database_now = await session.scalar(select(func.clock_timestamp()))
                if job_record.lease_expires_at is None or job_record.lease_expires_at <= database_now:
                    return None
            if kb_additional_params is not None:
                from yuxi.modules.knowledge.utils.kb_utils import resolve_processing_params

                # 参数与领取状态在同一行锁和事务内确定，落败请求不能预写参数。
                sanitized_data["processing_params"] = resolve_processing_params(
                    kb_additional_params, file_record.processing_params, processing_params
                )
            for key, value in sanitized_data.items():
                setattr(file_record, key, value)
            await session.flush()
            return file_record

    async def begin_index_generation(
        self, *, kb_id: str, file_id: str, processing_job_id: str | None = None, processing_owner: str | None = None
    ) -> int:
        """每次尝试申请单调新代次，旧 owner 的晚写不能污染重试结果。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_index_owner(
                session,
                kb_id=kb_id,
                file_id=file_id,
                processing_job_id=processing_job_id,
                processing_owner=processing_owner,
            )
            if record.building_generation is not None:
                self._enqueue_generation_cleanup(session, record, int(record.building_generation))
            generation = int(record.generation or 1) + 1
            record.generation = generation
            record.building_generation = generation
            record.projection_status = "building"
            return generation

    async def finish_index_generation(
        self,
        *,
        kb_id: str,
        file_id: str,
        generation: int,
        chunk_count: int,
        token_count: int,
        processing_job_id: str | None = None,
        processing_owner: str | None = None,
    ) -> KnowledgeFile:
        """当前 owner 在同一事务切换版本、终态和旧代次清理意图。"""
        async with pg_manager.get_async_session_context() as session:
            record = await self._lock_index_owner(
                session,
                kb_id=kb_id,
                file_id=file_id,
                processing_job_id=processing_job_id,
                processing_owner=processing_owner,
            )
            if record.building_generation != generation:
                raise ValueError(f"File {file_id} generation {generation} is not the current build")
            self._enqueue_generation_cleanup(session, record, int(record.active_generation))
            record.active_generation = generation
            record.building_generation = None
            record.status = FileStatus.INDEXED
            record.chunk_count = chunk_count
            record.token_count = token_count
            record.projection_status = "ready"
            record.projection_error = None
            record.error_message = None
            record.processing_job_id = None
            record.processing_owner = None
            return record

    @staticmethod
    async def _lock_index_owner(session, *, kb_id, file_id, processing_job_id, processing_owner):
        """按 Job → File 锁序核验执行资格，等文件锁后重新核对租约。"""
        if (processing_job_id is None) != (processing_owner is None):
            raise ValueError("File indexing owner must include job and worker")
        job = None
        if processing_job_id is not None:
            job = await session.scalar(
                select(BackgroundJobRecord)
                .where(
                    BackgroundJobRecord.id == processing_job_id,
                    BackgroundJobRecord.status == "running",
                    BackgroundJobRecord.worker_id == processing_owner,
                )
                .with_for_update()
            )
            if job is None:
                raise ValueError("File indexing owner was lost")
        record = await session.scalar(
            select(KnowledgeFile)
            .where(
                KnowledgeFile.kb_id == kb_id,
                KnowledgeFile.file_id == file_id,
            )
            .with_for_update()
        )
        if record is None or record.deleted_at is not None:
            raise ValueError(f"File {file_id} not found or deleted")
        if record.status != FileStatus.INDEXING or (record.processing_job_id, record.processing_owner) != (
            processing_job_id,
            processing_owner,
        ):
            raise ValueError("File indexing owner was lost")
        if job is not None:
            database_now = await session.scalar(select(func.clock_timestamp()))
            if job.lease_expires_at is None or job.lease_expires_at <= database_now:
                raise ValueError("File indexing owner lease expired")
        return record

    @staticmethod
    def _enqueue_generation_cleanup(session, record, generation):
        """切换事务持有 File 锁时保存唯一旧代次清理意图。"""
        session.add(
            KnowledgeProjectionOutbox(
                event_key=f"file:{record.file_id}:g{generation}:generation_cleanup",
                kb_id=record.kb_id,
                aggregate_id=record.file_id,
                generation=generation,
                operation="generation_cleanup",
            )
        )

    @staticmethod
    async def fail_job_processing_in_session(session, *, job_id: str, error: str) -> int:
        """同事务收敛指定作业拥有的文件中间态与知识库统计。"""
        in_flight_statuses = [FileStatus.PARSING, FileStatus.INDEXING]
        owned_files = select(KnowledgeFile.kb_id).where(
            KnowledgeFile.processing_job_id == job_id,
            KnowledgeFile.status.in_(in_flight_statuses),
        )
        # 与文件写入保持 KB → File 锁序，统计在持有 KB 行锁后重新聚合。
        kb_result = await session.scalars(
            select(KnowledgeBase)
            .where(KnowledgeBase.kb_id.in_(owned_files))
            .order_by(KnowledgeBase.kb_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        knowledge_bases = list(kb_result.all())
        result = await session.execute(
            update(KnowledgeFile)
            .where(
                KnowledgeFile.processing_job_id == job_id,
                KnowledgeFile.status.in_(in_flight_statuses),
            )
            .values(
                status=case(
                    (KnowledgeFile.status == FileStatus.PARSING, FileStatus.ERROR_PARSING),
                    else_=FileStatus.ERROR_INDEXING,
                ),
                error_message=error,
                processing_job_id=None,
                processing_owner=None,
                projection_status="failed",
                projection_error=error,
                updated_at=func.now(),
            )
            .returning(KnowledgeFile.kb_id)
        )
        affected = list(result.scalars().all())
        affected_kb_ids = set(affected)
        for kb in knowledge_bases:
            if kb.kb_id in affected_kb_ids:
                stats = await KnowledgeFileRepository().query_kb_file_stats(kb.kb_id, session=session)
                kb.additional_params = {**(kb.additional_params or {}), "stats": stats}
        await session.flush()
        return len(affected)

    async def mark_deleted(self, *, kb_id: str, file_id: str) -> str | None:
        """串行化目录修改，先提交删除事实和外部清理意图。"""
        from yuxi.modules.knowledge.repositories.directories import DirectoryRepository

        async with pg_manager.get_async_session_context() as session:
            await DirectoryRepository().lock(session, kb_id)
            record = await session.scalar(
                select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id == file_id).with_for_update()
            )
            if record is None:
                return None
            return await self.mark_deleted_in_session(session, record)

    async def mark_deleted_in_session(self, session: AsyncSession, record: KnowledgeFile) -> str:
        """调用方持有树及节点锁，在同一事务登记 tombstone 和幂等 outbox。"""
        file_id, kb_id = record.file_id, record.kb_id

        generation = int(record.generation or 1)
        event_key = f"file:{file_id}:g{generation}:deleted"
        record.deleted_at = utc_now()
        record.status = FileStatus.DELETED
        record.projection_status = "pending"
        record.projection_error = None
        existing = await session.scalar(select(KnowledgeProjectionOutbox).where(KnowledgeProjectionOutbox.event_key == event_key))
        if existing is None:
            session.add(
                KnowledgeProjectionOutbox(
                    event_key=event_key,
                    kb_id=kb_id,
                    aggregate_id=file_id,
                    generation=generation,
                    operation="file_deleted",
                )
            )
        return event_key

    async def delete(self, file_id: str) -> None:
        """禁止绕过删除语义；调用方必须提供知识库并走 mark_deleted。"""
        record = await self.get_by_file_id(file_id, include_deleted=True)
        if record is not None:
            await self.mark_deleted(kb_id=record.kb_id, file_id=file_id)

    async def delete_by_kb_id(self, kb_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id))
            for record in result.scalars().all():
                await session.delete(record)
