from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Exists, Select, delete, exists, func, select, update

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.models import KnowledgeChunk, KnowledgeFile
from yuxi.shared.datetime import utc_isoformat

SQL_IN_BATCH_SIZE = 10_000


class KnowledgeChunkRepository:
    """知识库 chunk 的 PG 分块投影仓储。

    只承载 chunk 投影的持久化查询与写入：读统一限定所属文件未删除且
    generation 等于 active_generation 的当前代次，旧代次行由投影切换方按代次
    删除；图谱两阶段构建状态列（graph_structure_indexed / graph_indexed /
    graph_extraction_details）的读写入口也集中在本类。
    """

    # 抽取失败样本单次返回上限：默认值与入参 clamp 共用同一约束。
    GRAPH_FAILED_SAMPLES_MAX = 10

    # batch_upsert 允许写入的列白名单，id 与时间戳列由 ORM/数据库维护。
    _writable_fields = {
        "chunk_id",
        "file_id",
        "kb_id",
        "chunk_index",
        "generation",
        "content",
        "start_line",
        "end_line",
        "start_char_pos",
        "end_char_pos",
        "start_token_pos",
        "end_token_pos",
        "graph_indexed",
        "graph_extraction_details",
        "ent_ids",
        "tags",
        "extraction_result",
    }

    # ─── 写入 ────────────────────────────────────────────────────────

    async def batch_upsert(self, chunks: list[dict[str, Any]]) -> list[KnowledgeChunk]:
        """按 chunk_id 批量新增或覆盖 chunk，单个事务内完成并按入参顺序返回记录。

        入参先过滤到 _writable_fields 白名单，未知键直接丢弃。
        """
        if not chunks:
            return []

        sanitized_chunks = [{key: value for key, value in chunk.items() if key in self._writable_fields} for chunk in chunks]
        chunk_ids = [chunk["chunk_id"] for chunk in sanitized_chunks]

        async with pg_manager.get_async_session_context() as session:
            existing_by_chunk_id: dict[str, KnowledgeChunk] = {}
            for batch in self._iter_batches(chunk_ids):
                result = await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.chunk_id.in_(batch)))
                existing_by_chunk_id.update({chunk.chunk_id: chunk for chunk in result.scalars().all()})

            records: list[KnowledgeChunk] = []
            for chunk_data in sanitized_chunks:
                record = existing_by_chunk_id.get(chunk_data["chunk_id"])
                if record is None:
                    record = KnowledgeChunk(**chunk_data)
                    session.add(record)
                else:
                    for key, value in chunk_data.items():
                        setattr(record, key, value)
                records.append(record)

            return records

    # ─── 按代次查询 ──────────────────────────────────────────────────

    @staticmethod
    def visible_scope() -> Exists:
        """普通 Chunk 查询统一使用文件的当前可见代次；EXISTS 形式供 join 不适用的统计与子查询复用。"""
        return exists().where(
            KnowledgeFile.file_id == KnowledgeChunk.file_id,
            KnowledgeFile.kb_id == KnowledgeChunk.kb_id,
            KnowledgeFile.deleted_at.is_(None),
            KnowledgeFile.active_generation == KnowledgeChunk.generation,
        )

    async def get_by_chunk_id(self, chunk_id: str) -> KnowledgeChunk | None:
        """按 chunk_id 查询当前可见代次的单个 chunk。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(self._visible_chunks().where(KnowledgeChunk.chunk_id == chunk_id))
            return result.scalar_one_or_none()

    async def list_by_file_id(self, file_id: str) -> list[KnowledgeChunk]:
        """列出文件当前可见代次的全部 chunk，按 chunk_index 升序。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                self._visible_chunks().where(KnowledgeChunk.file_id == file_id).order_by(KnowledgeChunk.chunk_index.asc())
            )
            return list(result.scalars().all())

    async def list_ids_by_file_generation(self, file_id: str, generation: int) -> list[str]:
        """列出待清理代次的原始 ID，不套用当前可见性过滤。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.scalars(
                select(KnowledgeChunk.chunk_id).where(KnowledgeChunk.file_id == file_id, KnowledgeChunk.generation == generation)
            )
            return list(result)

    async def list_by_kb_id(self, kb_id: str) -> list[KnowledgeChunk]:
        """列出知识库当前可见代次的全部 chunk，按主键升序保证顺序稳定。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(self._visible_chunks().where(KnowledgeChunk.kb_id == kb_id).order_by(KnowledgeChunk.id.asc()))
            return list(result.scalars().all())

    async def list_chunk_ids_by_kb_id(self, kb_id: str) -> list[str]:
        """只列出知识库当前可见代次的 chunk_id，供评估核对参考块是否仍存在。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.scalars(select(KnowledgeChunk.chunk_id).where(KnowledgeChunk.kb_id == kb_id, self.visible_scope()))
            return list(result)

    async def list_by_chunk_ids(self, chunk_ids: list[str]) -> list[KnowledgeChunk]:
        """按 chunk_id 批量查询可见 chunk；IN 参数按 SQL_IN_BATCH_SIZE 分批，结果按入参顺序返回并跳过未命中的 id。"""
        if not chunk_ids:
            return []
        chunks_by_id: dict[str, KnowledgeChunk] = {}
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(chunk_ids):
                result = await session.execute(self._visible_chunks().where(KnowledgeChunk.chunk_id.in_(batch)))
                chunks_by_id.update({chunk.chunk_id: chunk for chunk in result.scalars().all()})
        return [chunks_by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in chunks_by_id]

    async def count_by_kb_id(self, kb_id: str) -> int:
        """统计知识库当前可见代次的 chunk 总量。"""
        return await self._count_by_kb_id(kb_id)

    async def count_by_file_id(self, file_id: str) -> int:
        """统计文件当前可见代次的分块数量。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.scalar(
                select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.file_id == file_id, self.visible_scope())
            )
            return int(result or 0)

    # ─── 图谱构建查询 ────────────────────────────────────────────────

    async def count_graph_indexed_by_kb_id(self, kb_id: str, *, file_id: str | None = None) -> int:
        """统计向量阶段完成的可见 chunk。"""
        return await self._count_by_kb_id(kb_id, KnowledgeChunk.graph_indexed.is_(True), *self._file_filter(file_id))

    async def count_graph_structure_indexed_by_kb_id(self, kb_id: str) -> int:
        """统计结构阶段完成的可见 chunk。"""
        return await self._count_by_kb_id(kb_id, KnowledgeChunk.graph_structure_indexed.is_(True))

    async def count_graph_pending_by_kb_id(self, kb_id: str, *, file_id: str | None = None) -> int:
        """统计向量阶段未完成的可见 chunk，即图谱构建剩余量。"""
        return await self._count_by_kb_id(kb_id, KnowledgeChunk.graph_indexed.is_not(True), *self._file_filter(file_id))

    async def count_graph_extraction_statuses_by_kb_id(self, kb_id: str, *, file_id: str | None = None) -> dict[str, int]:
        """按抽取状态统计可见 chunk 数量，键覆盖 pending/succeeded/failed。"""
        counts = {"pending": 0, "succeeded": 0, "failed": 0}
        # 缺失 status 的旧行按 pending 统计，保证三个键始终完整。
        status = func.coalesce(KnowledgeChunk.graph_extraction_details["status"].as_string(), "pending")
        async with pg_manager.get_async_session_context() as session:
            rows = (
                await session.execute(
                    select(status, func.count())
                    .where(KnowledgeChunk.kb_id == kb_id, self.visible_scope(), *self._file_filter(file_id))
                    .group_by(status)
                )
            ).all()
        for value, count in rows:
            counts[str(value)] = int(count or 0)
        return counts

    async def list_graph_pending_by_kb_id(
        self,
        kb_id: str,
        limit: int,
        *,
        after_id: int = 0,
        file_id: str | None = None,
    ) -> list[KnowledgeChunk]:
        """按主键游标分页拉取向量阶段未完成的可见 chunk。

        after_id 传上一页最后一条的自增 id，构建循环用它跳过已入队的 chunk。
        """
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.kb_id == kb_id,
                    KnowledgeChunk.graph_indexed.is_not(True),
                    self.visible_scope(),
                    KnowledgeChunk.id > after_id,
                    *self._file_filter(file_id),
                )
                .order_by(KnowledgeChunk.id.asc())
                .limit(max(limit, 1))
            )
            return list(result.scalars().all())

    async def list_graph_extraction_failed_samples(self, kb_id: str, limit: int = GRAPH_FAILED_SAMPLES_MAX) -> list[dict[str, Any]]:
        """按 id 倒序返回最近的抽取失败样本摘要，limit 夹在 [1, GRAPH_FAILED_SAMPLES_MAX]。"""
        status = KnowledgeChunk.graph_extraction_details["status"].as_string()
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.kb_id == kb_id, status == "failed", self.visible_scope())
                .order_by(KnowledgeChunk.id.desc())
                .limit(max(1, min(limit, self.GRAPH_FAILED_SAMPLES_MAX)))
            )
            chunks = list(result.scalars().all())
        return [
            {
                "chunk_id": chunk.chunk_id,
                "file_id": chunk.file_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "details": chunk.graph_extraction_details or {},
            }
            for chunk in chunks
        ]

    # ─── 图谱构建状态更新 ────────────────────────────────────────────

    async def mark_graph_extraction_pending(self, chunk_id: str) -> None:
        """抽取开始前把状态重置为 pending 并清零尝试计数。"""
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.chunk_id == chunk_id)
                .values(graph_extraction_details={"status": "pending", "attempt_count": 0})
            )

    async def update_extraction_result(
        self,
        chunk_id: str,
        extraction_result: dict[str, Any],
        attempt_count: int = 1,
    ) -> None:
        """写入抽取成功结果并把状态置为 succeeded。"""
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.chunk_id == chunk_id)
                .values(
                    extraction_result=extraction_result,
                    graph_extraction_details={
                        "status": "succeeded",
                        "attempt_count": attempt_count,
                    },
                )
            )

    async def mark_graph_extraction_failed(self, chunk_id: str, attempt_count: int, error: str) -> None:
        """记录抽取失败状态、尝试次数与截断后的错误信息。"""
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.chunk_id == chunk_id)
                .values(
                    graph_extraction_details={
                        "status": "failed",
                        "attempt_count": attempt_count,
                        # 截断错误文本，避免超大异常字符串撑爆 JSON 详情列。
                        "last_error": error[:4000],
                        "last_attempt_at": utc_isoformat(),
                    }
                )
            )

    async def mark_graph_structure_indexed(self, chunk_id: str, ent_ids: list[str]) -> None:
        """结构写入完成后置位并记录实体 id；向量阶段完成后才置 graph_indexed。"""
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeChunk).where(KnowledgeChunk.chunk_id == chunk_id).values(graph_structure_indexed=True, ent_ids=ent_ids)
            )

    async def reset_graph_state_by_kb_id(self, kb_id: str, clear_extraction_result: bool) -> int:
        """整库重置图谱构建状态，返回受影响行数。

        clear_extraction_result 为 True 时连同抽取结果与实体、标签一起清空，
        chunk 回到全新待抽取状态；为 False 时仅清除两个索引标记，保留抽取产物。
        """
        values: dict[str, Any] = {"graph_structure_indexed": False, "graph_indexed": False}
        if clear_extraction_result:
            values.update(
                {
                    "extraction_result": None,
                    "graph_extraction_details": {"status": "pending", "attempt_count": 0},
                    "ent_ids": None,
                    "tags": None,
                }
            )

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(update(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id).values(**values))
            return int(result.rowcount or 0)

    # ─── 删除 ────────────────────────────────────────────────────────

    async def delete_by_file_id(self, file_id: str, *, generation: int | None = None) -> int:
        """删除文件的 chunk 投影并返回行数；不传 generation 删除全部代次，传入时仅清理该代次（失败构建回滚场景）。"""
        filters = [KnowledgeChunk.file_id == file_id]
        if generation is not None:
            filters.append(KnowledgeChunk.generation == generation)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(delete(KnowledgeChunk).where(*filters))
            return int(result.rowcount or 0)

    # ─── 内部工具 ────────────────────────────────────────────────────

    @staticmethod
    def _iter_batches(items: list[str], batch_size: int = SQL_IN_BATCH_SIZE) -> Iterator[list[str]]:
        """把长 id 列表切成不超过 batch_size 的批，避免超长 IN 参数。"""
        for index in range(0, len(items), batch_size):
            yield items[index : index + batch_size]

    @staticmethod
    def _visible_chunks() -> Select:
        """当前可见代次 chunk 的基础 SELECT；与 visible_scope() 是同一可见性判定的 join 形式。"""
        return (
            select(KnowledgeChunk)
            .join(
                KnowledgeFile,
                (KnowledgeFile.file_id == KnowledgeChunk.file_id) & (KnowledgeFile.kb_id == KnowledgeChunk.kb_id),
            )
            .where(KnowledgeFile.deleted_at.is_(None), KnowledgeChunk.generation == KnowledgeFile.active_generation)
        )

    @staticmethod
    def _file_filter(file_id: str | None) -> list:
        """可选文件范围用于构建查询。"""
        return [KnowledgeChunk.file_id == file_id] if file_id is not None else []

    async def _count_by_kb_id(self, kb_id: str, *conditions: Any) -> int:
        """按 kb_id 叠加附加条件统计可见 chunk，供各计数入口复用。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id, self.visible_scope(), *conditions)
            )
            return int(result.scalar() or 0)
