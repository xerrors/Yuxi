from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from sqlalchemy import DateTime, String, case, cast, func, literal, or_, select, union_all, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import TaskRecord
from yuxi.storage.postgres.models_knowledge import KnowledgeFile
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_now_naive

# asyncpg 单条 SQL 参数上限为 32767；按 file_id 批量查询时统一分批，避免
# mindmap_file_ids 等大尺寸传入触发 `too many parameters` 报错。
SQL_IN_BATCH_SIZE = 10_000

# 文件统计聚合缓存 TTL：列表页高频请求时避免反复全表聚合；文件增删后最多延迟该时长更新
KB_FILE_STATS_CACHE_TTL = 10


class KnowledgeFileRepository:
    @asynccontextmanager
    async def lock_file_tree(self, kb_id: str) -> AsyncIterator[None]:
        """按知识库串行化目录树结构修改。"""
        async with pg_manager.get_async_session_context() as session:
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(kb_id))))
            yield

    async def detect_virtual_folder_data(self, kb_id: str) -> dict[str, int | bool]:
        """检测仍以相对路径保存的历史文件记录。"""
        path_record = KnowledgeFile.filename.contains("/")
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(
                    func.count(KnowledgeFile.file_id),
                    func.coalesce(
                        func.sum(
                            func.length(KnowledgeFile.filename)
                            - func.length(func.replace(KnowledgeFile.filename, "/", ""))
                        ),
                        0,
                    ),
                ).where(
                    KnowledgeFile.kb_id == kb_id,
                    or_(KnowledgeFile.is_folder.is_(False), KnowledgeFile.is_folder.is_(None)),
                    path_record,
                )
            )
            file_count, remaining_steps = result.one()
        count = int(file_count or 0)
        return {
            "has_virtual_folders": count > 0,
            "file_count": count,
            "remaining_steps": int(remaining_steps or 0),
        }

    async def migrate_virtual_folder_batch(
        self,
        session,
        *,
        kb_id: str,
        operator_id: str,
        after_file_id: str | None,
        batch_size: int = 500,
    ) -> dict[str, Any]:
        """在调用方拥有的 Task attempt 事务内迁移一批路径。"""
        filters = [
            KnowledgeFile.kb_id == kb_id,
            or_(KnowledgeFile.is_folder.is_(False), KnowledgeFile.is_folder.is_(None)),
            KnowledgeFile.filename.contains("/"),
        ]
        if after_file_id:
            filters.append(KnowledgeFile.file_id > after_file_id)

        await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(kb_id))))
        records = list(
            (
                await session.execute(
                    select(KnowledgeFile).where(*filters).order_by(KnowledgeFile.file_id).limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        if not records:
            return {"scanned": 0, "processed": 0, "created_folders": 0, "conflict_file_ids": []}

        groups: dict[tuple[str | None, str], list[KnowledgeFile]] = {}
        conflict_file_ids: list[str] = []
        for record in records:
            segment, remainder = record.filename.split("/", 1)
            if not segment or segment in {".", ".."} or not remainder:
                conflict_file_ids.append(record.file_id)
                continue
            groups.setdefault((record.parent_id, segment), []).append(record)

        processed = 0
        created_folders = 0
        for (parent_id, segment), group in groups.items():
            siblings = list(
                (
                    await session.execute(
                        select(KnowledgeFile).where(
                            KnowledgeFile.kb_id == kb_id,
                            self._parent_condition(parent_id),
                            KnowledgeFile.filename == segment,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(siblings) == 1 and siblings[0].is_folder:
                folder = siblings[0]
            elif siblings:
                conflict_file_ids.extend(record.file_id for record in group)
                continue
            else:
                folder = KnowledgeFile(
                    file_id=f"folder-{uuid.uuid4()}",
                    kb_id=kb_id,
                    parent_id=parent_id,
                    filename=segment,
                    path=segment,
                    file_type="folder",
                    status="done",
                    is_folder=True,
                    file_size=0,
                    chunk_count=0,
                    token_count=0,
                    created_by=operator_id,
                )
                session.add(folder)
                await session.flush()
                created_folders += 1

            for record in group:
                record.parent_id = folder.file_id
                record.filename = record.filename.split("/", 1)[1]
                processed += 1

        return {
            "scanned": len(records),
            "processed": processed,
            "created_folders": created_folders,
            "conflict_file_ids": conflict_file_ids,
            "last_file_id": records[-1].file_id,
        }

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
                .where(or_(KnowledgeFile.is_folder.is_(False), KnowledgeFile.is_folder.is_(None)))
                .group_by(KnowledgeFile.file_type)
            )
            return [
                (str(file_type or "unknown"), int(count or 0), int(size or 0), int(nodes or 0))
                for file_type, count, size, nodes in result.all()
            ]

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
        "processing_task_id",
        "processing_owner",
        "created_by",
        "updated_by",
    }

    @staticmethod
    def _iter_batches(items: list[str], batch_size: int = SQL_IN_BATCH_SIZE) -> Iterator[list[str]]:
        for index in range(0, len(items), batch_size):
            yield items[index : index + batch_size]

    @classmethod
    def _sanitize_data(cls, data: dict[str, Any]) -> dict[str, Any]:
        sanitized = {key: value for key, value in data.items() if key in cls._writable_fields}
        if sanitized:
            sanitized["updated_at"] = utc_now_naive()
        return sanitized

    async def get_all(self) -> list[KnowledgeFile]:
        """获取所有文件记录"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile))
            return list(result.scalars().all())

    async def get_by_file_id(self, file_id: str) -> KnowledgeFile | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            return result.scalar_one_or_none()

    async def list_by_file_ids(self, file_ids: list[str]) -> list[KnowledgeFile]:
        normalized_ids = [file_id for file_id in file_ids if file_id]
        if not normalized_ids:
            return []

        records_by_id: dict[str, KnowledgeFile] = {}
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(normalized_ids):
                result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id.in_(batch)))
                records_by_id.update({record.file_id: record for record in result.scalars().all()})
        return [records_by_id[file_id] for file_id in normalized_ids if file_id in records_by_id]

    async def list_by_kb_id(self, kb_id: str) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id))
            return list(result.scalars().all())

    async def list_by_kb_id_after(
        self,
        kb_id: str,
        *,
        after_file_id: str | None = None,
        limit: int = 500,
        files_only: bool = False,
    ) -> list[KnowledgeFile]:
        filters = [KnowledgeFile.kb_id == kb_id]
        if after_file_id:
            filters.append(KnowledgeFile.file_id > after_file_id)
        if files_only:
            filters.append(KnowledgeFile.is_folder.is_(False))

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(*filters)
                .order_by(KnowledgeFile.file_id.asc())
                .limit(min(max(int(limit or 100), 1), 1000))
            )
            return list(result.scalars().all())

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
        filters = [KnowledgeFile.kb_id == kb_id]
        if files_only:
            filters.append(KnowledgeFile.is_folder.is_(False))
        if statuses is not None:
            filters.append(KnowledgeFile.status.in_(statuses))

        normalized_query = (filename_query or "").strip().lower()
        if normalized_query:
            escaped_query = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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

    async def get_filenames_by_file_ids(self, *, kb_id: str, file_ids: list[str]) -> dict[str, str]:
        normalized_ids = [file_id for file_id in file_ids if file_id]
        if not normalized_ids:
            return {}

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id, KnowledgeFile.filename).where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.file_id.in_(normalized_ids),
                )
            )
            return {str(file_id): str(filename or "") for file_id, filename in result.all()}

    async def list_children(self, *, kb_id: str, parent_id: str | None) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(KnowledgeFile.kb_id == kb_id, self._parent_condition(parent_id))
                .order_by(KnowledgeFile.is_folder.desc(), func.lower(KnowledgeFile.filename).asc())
            )
            return list(result.scalars().all())

    async def list_same_name_files(self, *, kb_id: str, filename: str) -> list[KnowledgeFile]:
        normalized_filename = filename.strip()
        if not normalized_filename:
            return []

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    func.lower(KnowledgeFile.filename) == normalized_filename.lower(),
                    or_(KnowledgeFile.status.is_(None), KnowledgeFile.status != "failed"),
                )
                .order_by(KnowledgeFile.created_at.desc())
            )
            return list(result.scalars().all())

    async def list_file_ids_by_filename_contains(
        self,
        *,
        kb_id: str,
        filename_pattern: str,
        limit: int = 10_000,
    ) -> list[str]:
        normalized_pattern = filename_pattern.replace("%", "").strip().lower()
        if not normalized_pattern:
            return []

        escaped_pattern = normalized_pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    func.lower(KnowledgeFile.filename).like(f"%{escaped_pattern}%", escape="\\"),
                )
                .order_by(KnowledgeFile.file_id.asc())
                .limit(min(max(int(limit or 100), 1), 10_000))
            )
            return [str(file_id) for file_id in result.scalars().all()]

    async def exists_by_content_hash(self, *, kb_id: str, content_hash: str) -> bool:
        normalized_hash = content_hash.strip()
        if not normalized_hash:
            return False

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    KnowledgeFile.content_hash == normalized_hash,
                    or_(KnowledgeFile.status.is_(None), KnowledgeFile.status != "failed"),
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def count_all(self) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(func.count()).select_from(KnowledgeFile))
            return int(result.scalar() or 0)

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
                select(KnowledgeFile.file_id)
                .where(*filters)
                .order_by(KnowledgeFile.file_id.asc())
                .limit(normalized_limit)
            )
            return [str(file_id) for file_id in result.scalars().all()]

    async def exists_by_filename(self, *, kb_id: str, filename: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.filename == filename,
                    KnowledgeFile.is_folder.is_not(True),
                    or_(KnowledgeFile.status.is_(None), KnowledgeFile.status != "failed"),
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

    @staticmethod
    def _status_condition(status: str | None):
        if not status or status == "all":
            return None
        if status == "indexed":
            return KnowledgeFile.status.in_(["indexed", "done"])
        if status == "error_indexing":
            return KnowledgeFile.status.in_(["error_indexing", "failed"])
        return KnowledgeFile.status == status

    @staticmethod
    def _parent_condition(parent_id: str | None):
        if parent_id:
            return KnowledgeFile.parent_id == parent_id
        return KnowledgeFile.parent_id.is_(None)

    @staticmethod
    def _normalize_path_prefix(path_prefix: str | None) -> str:
        if not path_prefix:
            return ""
        normalized = path_prefix.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.startswith("/"):
            raise ValueError("path_prefix must be relative")

        parts = [part for part in normalized.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise ValueError("path_prefix must not contain parent directory references")
        if not parts:
            return ""
        return "/".join(parts) + "/"

    @staticmethod
    def _like_prefix(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"{escaped}%"

    def _document_filters(
        self,
        *,
        kb_id: str,
        parent_id: str | None,
        status: str | None,
        recursive: bool,
        files_only: bool,
    ) -> list:
        filters = [KnowledgeFile.kb_id == kb_id]
        if not recursive:
            filters.append(self._parent_condition(parent_id))
        if files_only:
            filters.append(KnowledgeFile.is_folder.is_(False))

        status_condition = self._status_condition(status)
        if status_condition is not None:
            filters.append(KnowledgeFile.is_folder.is_(False))
            filters.append(status_condition)

        return filters

    async def _list_directory_documents(
        self,
        *,
        kb_id: str,
        parent_id: str | None,
        path_prefix: str,
        page: int,
        page_size: int,
        files_only: bool,
    ) -> tuple[list[Any], int]:
        offset = (page - 1) * page_size
        parent_condition = self._parent_condition(parent_id)
        base_filters = [KnowledgeFile.kb_id == kb_id, parent_condition, KnowledgeFile.filename.is_not(None)]
        if path_prefix:
            base_filters.append(KnowledgeFile.filename.like(self._like_prefix(path_prefix), escape="\\"))
            remainder = func.substr(KnowledgeFile.filename, len(path_prefix) + 1)
        else:
            # 根目录直接使用 filename 表达式，匹配部分索引 idx_kf_kb_parent_segment/idx_kf_kb_parent_flat
            remainder = KnowledgeFile.filename
        immediate_name = remainder.label("filename")
        segment = func.split_part(remainder, "/", 1)
        virtual_path_prefix = (literal(path_prefix) + segment + literal("/")).label("path_prefix")
        virtual_file_id = (
            literal("__virtual_folder__:") + literal(parent_id or "root") + literal(":") + virtual_path_prefix
        ).label(
            "file_id",
        )

        real_select = select(
            KnowledgeFile.file_id.label("file_id"),
            immediate_name,
            KnowledgeFile.file_type.label("file_type"),
            KnowledgeFile.status.label("status"),
            KnowledgeFile.created_at.label("created_at"),
            KnowledgeFile.updated_at.label("updated_at"),
            KnowledgeFile.file_size.label("file_size"),
            KnowledgeFile.chunk_count.label("chunk_count"),
            KnowledgeFile.token_count.label("token_count"),
            KnowledgeFile.created_by.label("created_by"),
            KnowledgeFile.is_folder.label("is_folder"),
            KnowledgeFile.parent_id.label("parent_id"),
            KnowledgeFile.path.label("path"),
            KnowledgeFile.minio_url.label("minio_url"),
            KnowledgeFile.markdown_file.label("markdown_file"),
            literal(False).label("is_virtual_folder"),
            cast(literal(None), String).label("path_prefix"),
            literal(0).label("virtual_children_count"),
        ).where(*base_filters, remainder != "", func.strpos(remainder, "/") == 0)

        virtual_select = (
            select(
                virtual_file_id,
                segment.label("filename"),
                literal("folder").label("file_type"),
                literal("done").label("status"),
                cast(literal(None), DateTime).label("created_at"),
                cast(literal(None), DateTime).label("updated_at"),
                literal(0).label("file_size"),
                literal(0).label("chunk_count"),
                literal(0).label("token_count"),
                cast(literal(None), String).label("created_by"),
                literal(True).label("is_folder"),
                cast(literal(parent_id), String).label("parent_id"),
                cast(literal(None), String).label("path"),
                cast(literal(None), String).label("minio_url"),
                cast(literal(None), String).label("markdown_file"),
                literal(True).label("is_virtual_folder"),
                virtual_path_prefix,
                func.count().label("virtual_children_count"),
            )
            .where(*base_filters, remainder != "", func.strpos(remainder, "/") > 0)
            .group_by(segment)
        )

        if files_only:
            directory_query = real_select.where(KnowledgeFile.is_folder.is_(False)).subquery()
        else:
            directory_query = union_all(real_select, virtual_select).subquery()

        async with pg_manager.get_async_session_context() as session:
            total_result = await session.execute(select(func.count()).select_from(directory_query))
            total = int(total_result.scalar_one() or 0)
            result = await session.execute(
                select(directory_query)
                .order_by(
                    directory_query.c.is_folder.desc(),
                    func.lower(directory_query.c.filename).asc(),
                    directory_query.c.created_at.desc().nullslast(),
                    directory_query.c.file_id.asc(),
                )
                .offset(offset)
                .limit(page_size)
            )
            return [SimpleNamespace(**dict(row)) for row in result.mappings().all()], total

    async def list_documents(
        self,
        *,
        kb_id: str,
        parent_id: str | None = None,
        path_prefix: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
        recursive: bool = False,
        files_only: bool = False,
    ) -> tuple[list[KnowledgeFile], int]:
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 100), 1), 500)
        offset = (page - 1) * page_size
        normalized_path_prefix = self._normalize_path_prefix(path_prefix)
        has_status_filter = self._status_condition(status) is not None
        effective_recursive = recursive and has_status_filter
        if not effective_recursive and not has_status_filter:
            return await self._list_directory_documents(
                kb_id=kb_id,
                parent_id=parent_id,
                path_prefix=normalized_path_prefix,
                page=page,
                page_size=page_size,
                files_only=files_only,
            )

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

            result = await session.execute(
                select(KnowledgeFile)
                .where(*filters)
                .order_by(
                    KnowledgeFile.is_folder.desc(),
                    func.lower(KnowledgeFile.filename).asc(),
                    KnowledgeFile.created_at.desc(),
                    KnowledgeFile.file_id.asc(),
                )
                .offset(offset)
                .limit(page_size)
            )
            return list(result.scalars().all()), total

    async def count_children_by_parent_ids(self, *, kb_id: str, parent_ids: list[str]) -> dict[str, int]:
        if not parent_ids:
            return {}

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.parent_id, func.count())
                .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.parent_id.in_(parent_ids))
                .group_by(KnowledgeFile.parent_id)
            )
            return {str(parent_id): int(count or 0) for parent_id, count in result.all() if parent_id}

    async def get_kb_file_stats(self, kb_id: str) -> dict[str, int]:
        """获取知识库文件统计；结果带短 TTL 缓存，避免高频列表请求反复全表聚合。"""
        from yuxi.storage.redis import get_async_redis_client

        cache_key = f"yuxi:kb_file_stats:{kb_id}"
        redis_client = await get_async_redis_client()
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as exc:
            logger.warning(f"Failed to load kb file stats cache {cache_key}: {exc}")

        stats = await self._query_kb_file_stats(kb_id)
        try:
            await redis_client.set(cache_key, json.dumps(stats), ex=KB_FILE_STATS_CACHE_TTL)
        except Exception as exc:
            logger.warning(f"Failed to store kb file stats cache {cache_key}: {exc}")
        return stats

    async def _query_kb_file_stats(self, kb_id: str) -> dict[str, int]:
        """直接查询数据库计算知识库文件统计。"""
        non_folder = KnowledgeFile.is_folder.is_(False)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(
                    func.count(KnowledgeFile.file_id).label("row_count"),
                    func.sum(case((non_folder, 1), else_=0)).label("file_count"),
                    func.sum(case((KnowledgeFile.is_folder.is_(True), 1), else_=0)).label("folder_count"),
                    func.coalesce(func.sum(case((non_folder, KnowledgeFile.file_size), else_=0)), 0).label(
                        "total_size"
                    ),
                    func.coalesce(func.sum(case((non_folder, KnowledgeFile.chunk_count), else_=0)), 0).label(
                        "chunk_count"
                    ),
                    func.coalesce(func.sum(case((non_folder, KnowledgeFile.token_count), else_=0)), 0).label(
                        "token_count"
                    ),
                    func.sum(case((non_folder & (KnowledgeFile.status == "uploaded"), 1), else_=0)).label(
                        "pending_parse_count"
                    ),
                    func.sum(
                        case((non_folder & KnowledgeFile.status.in_(["parsed", "error_indexing"]), 1), else_=0)
                    ).label("pending_index_count"),
                    func.sum(
                        case(
                            (
                                non_folder & KnowledgeFile.status.in_(["processing", "waiting", "parsing", "indexing"]),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("processing_count"),
                ).where(KnowledgeFile.kb_id == kb_id)
            )
            row = result.one()

        return {
            "row_count": int(row.row_count or 0),
            "file_count": int(row.file_count or 0),
            "folder_count": int(row.folder_count or 0),
            "total_size": int(row.total_size or 0),
            "chunk_count": int(row.chunk_count or 0),
            "token_count": int(row.token_count or 0),
            "pending_parse_count": int(row.pending_parse_count or 0),
            "pending_index_count": int(row.pending_index_count or 0),
            "processing_count": int(row.processing_count or 0),
        }

    async def upsert(self, file_id: str, data: dict[str, Any]) -> KnowledgeFile:
        sanitized_data = self._sanitize_data(data)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            existing = result.scalar_one_or_none()
            if existing is None:
                record = KnowledgeFile(file_id=file_id, **sanitized_data)
                session.add(record)
                return record
            for key, value in sanitized_data.items():
                setattr(existing, key, value)
            return existing

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
        processing_task_id: str | None = None,
        processing_owner: str | None = None,
    ) -> KnowledgeFile | None:
        lease_task_id = processing_task_id or data.get("processing_task_id")
        lease_owner = processing_owner or data.get("processing_owner")
        sanitized_data = self._sanitize_data(data)
        if not sanitized_data:
            return await self.get_by_file_id(file_id)

        filters = [
            KnowledgeFile.kb_id == kb_id,
            KnowledgeFile.file_id == file_id,
            KnowledgeFile.status.in_(sorted(allowed_statuses)),
        ]
        if processing_task_id is not None:
            filters.append(KnowledgeFile.processing_task_id == processing_task_id)
        if processing_owner is not None:
            filters.append(KnowledgeFile.processing_owner == processing_owner)
        async with pg_manager.get_async_session_context() as session:
            if lease_task_id is not None and lease_owner is not None:
                task_record = await session.scalar(
                    select(TaskRecord)
                    .where(
                        TaskRecord.id == lease_task_id,
                        TaskRecord.status == "running",
                        TaskRecord.worker_id == lease_owner,
                    )
                    .with_for_update()
                )
                if task_record is None:
                    return None
                file_record = await session.scalar(select(KnowledgeFile).where(*filters).with_for_update())
                if file_record is None:
                    return None
                database_now = await session.scalar(select(func.timezone("utc", func.clock_timestamp())))
                if task_record.lease_expires_at is None or task_record.lease_expires_at <= database_now:
                    return None
                for key, value in sanitized_data.items():
                    setattr(file_record, key, value)
                await session.flush()
                return file_record

            result = await session.execute(
                update(KnowledgeFile).where(*filters).values(**sanitized_data).returning(KnowledgeFile)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def fail_task_processing_in_session(session, *, task_id: str, error: str) -> int:
        """仅收敛仍由指定 Durable Task 拥有的文件中间态。"""
        result = await session.execute(
            update(KnowledgeFile)
            .where(
                KnowledgeFile.processing_task_id == task_id,
                KnowledgeFile.status.in_(["parsing", "indexing"]),
            )
            .values(
                status=case(
                    (KnowledgeFile.status == "parsing", "error_parsing"),
                    else_="error_indexing",
                ),
                error_message=error,
                processing_task_id=None,
                processing_owner=None,
                updated_at=func.now(),
            )
        )
        return int(result.rowcount or 0)

    async def delete(self, file_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            record = result.scalar_one_or_none()
            if record is not None:
                await session.delete(record)

    async def delete_by_kb_id(self, kb_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id))
            for record in result.scalars().all():
                await session.delete(record)
