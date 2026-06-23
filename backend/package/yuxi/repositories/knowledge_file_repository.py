from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy import DateTime, String, case, cast, func, literal, or_, select, union_all

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeFile


class KnowledgeFileRepository:
    async def get_all(self) -> list[KnowledgeFile]:
        """获取所有文件记录"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile))
            return list(result.scalars().all())

    async def get_by_file_id(self, file_id: str) -> KnowledgeFile | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            return result.scalar_one_or_none()

    async def list_by_kb_id(self, kb_id: str) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id))
            return list(result.scalars().all())

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
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            existing = result.scalar_one_or_none()
            if existing is None:
                record = KnowledgeFile(file_id=file_id, **data)
                session.add(record)
                return record
            for key, value in data.items():
                setattr(existing, key, value)
            return existing

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
