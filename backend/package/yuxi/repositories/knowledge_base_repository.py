from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import func, select

from yuxi.knowledge.cache import cache_kb_config, delete_cached_kb_config, kb_config_cache_lock
from yuxi.repositories.knowledge_file_repository import STORAGE_REFERENCE_LOCK_NAMESPACE
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile


class KnowledgeBaseRepository:
    async def count_by_type(self) -> list[tuple[str, int]]:
        """按知识库类型聚合数量。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeBase.kb_type, func.count(KnowledgeBase.id)).group_by(KnowledgeBase.kb_type)
            )
            return [(str(kb_type or "unknown"), int(count or 0)) for kb_type, count in result.all()]

    async def get_all(self) -> list[KnowledgeBase]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeBase))
            return list(result.scalars().all())

    async def get_by_kb_id(self, kb_id: str) -> KnowledgeBase | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            return result.scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> KnowledgeBase:
        kb = KnowledgeBase(**data)
        async with pg_manager.get_async_session_context() as session:
            session.add(kb)
        await cache_kb_config(kb)
        return kb

    async def update(self, kb_id: str, data: dict[str, Any]) -> KnowledgeBase | None:
        async with kb_config_cache_lock(kb_id):
            # 先可靠清除旧值；失败时不进入数据库事务。
            await delete_cached_kb_config(kb_id)
            async with pg_manager.get_async_session_context() as session:
                result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
                kb = result.scalar_one_or_none()
                if kb is None:
                    return None
                for key, value in data.items():
                    setattr(kb, key, value)
            return kb

    async def merge_query_params_options(self, kb_id: str, params: dict[str, Any]) -> KnowledgeBase | None:
        """在行锁内合并知识库查询参数，避免并发部分更新互相覆盖。"""
        async with kb_config_cache_lock(kb_id):
            await delete_cached_kb_config(kb_id)
            async with pg_manager.get_async_session_context() as session:
                statement = select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id).with_for_update()
                result = await session.execute(statement)
                kb = result.scalar_one_or_none()
                if kb is None:
                    return None

                query_params = dict(kb.query_params or {})
                options = dict(query_params.get("options") or {})
                options.update(params)
                query_params["options"] = options
                kb.query_params = query_params
            return kb

    async def refresh_stats(self, kb_id: str) -> KnowledgeBase | None:
        """在行锁内从文件聚合刷新统计，保留其他附加参数。"""
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        async with kb_config_cache_lock(kb_id):
            async with pg_manager.get_async_session_context() as session:
                statement = select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id).with_for_update()
                result = await session.execute(statement)
                kb = result.scalar_one_or_none()
                if kb is None:
                    return None

                # 聚合必须在取得行锁后执行，避免较早的快照晚写覆盖新结果。
                stats = await KnowledgeFileRepository().query_kb_file_stats(kb_id, session=session)
                additional_params = dict(kb.additional_params or {})
                additional_params["stats"] = stats
                kb.additional_params = additional_params
            return kb

    async def delete(self, kb_id: str, *, before_commit: Callable[[], Awaitable[None]] | None = None) -> None:
        """树锁内确认没有回收站项后清理资源，最后删除知识库。"""
        async with kb_config_cache_lock(kb_id):
            await delete_cached_kb_config(kb_id)
            async with pg_manager.get_async_session_context() as session:
                await session.execute(select(func.pg_advisory_xact_lock(STORAGE_REFERENCE_LOCK_NAMESPACE, 0)))
                await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(kb_id))))
                trashed = await session.scalar(
                    select(KnowledgeFile.file_id)
                    .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.deleted_at.is_not(None))
                    .limit(1)
                )
                if trashed is not None:
                    raise ValueError("Knowledge base contains trashed files")
                result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
                kb = result.scalar_one_or_none()
                if kb is not None:
                    rows = (await session.execute(select(KnowledgeFile))).scalars().all()

                    def objects(row):
                        result = set()
                        for value in (row.path, row.minio_url, row.markdown_file):
                            if not value:
                                continue
                            parsed = urlparse(value)
                            path = unquote(parsed.path).lstrip("/")
                            if parsed.scheme == "minio":
                                result.add((parsed.netloc, path))
                            elif parsed.scheme in {"http", "https"} and "/" in path:
                                result.add(tuple(path.split("/", 1)))
                        return result

                    owned_objects = set().union(*(objects(row) for row in rows if row.kb_id == kb_id))
                    for row in rows:
                        if row.kb_id == kb_id:
                            continue
                        referenced = objects(row)
                        if referenced & owned_objects or any(
                            bucket in {"knowledgebases", "kb-images"} and key.startswith(f"{kb_id}/")
                            for bucket, key in referenced
                        ):
                            raise ValueError("Knowledge base storage is referenced by another knowledge base")
                    if before_commit is not None:
                        await before_commit()
                    await session.delete(kb)
