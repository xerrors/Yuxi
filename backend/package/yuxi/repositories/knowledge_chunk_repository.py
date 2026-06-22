from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import delete, func, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeChunk

SQL_IN_BATCH_SIZE = 10_000


class KnowledgeChunkRepository:
    _writable_fields = {
        "chunk_id",
        "file_id",
        "kb_id",
        "chunk_index",
        "content",
        "start_char_pos",
        "end_char_pos",
        "start_token_pos",
        "end_token_pos",
        "graph_indexed",
        "ent_ids",
        "tags",
        "extraction_result",
    }

    @staticmethod
    def _iter_batches(items: list[str], batch_size: int = SQL_IN_BATCH_SIZE) -> Iterator[list[str]]:
        for index in range(0, len(items), batch_size):
            yield items[index : index + batch_size]

    async def get_by_chunk_id(self, chunk_id: str) -> KnowledgeChunk | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.chunk_id == chunk_id))
            return result.scalar_one_or_none()

    async def list_by_file_id(self, file_id: str) -> list[KnowledgeChunk]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.file_id == file_id)
                .order_by(KnowledgeChunk.chunk_index.asc())
            )
            return list(result.scalars().all())

    async def list_by_file_ids(self, file_ids: list[str]) -> list[KnowledgeChunk]:
        if not file_ids:
            return []

        chunks: list[KnowledgeChunk] = []
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(file_ids):
                result = await session.execute(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.file_id.in_(batch))
                    .order_by(KnowledgeChunk.file_id.asc(), KnowledgeChunk.chunk_index.asc())
                )
                chunks.extend(result.scalars().all())
        return sorted(chunks, key=lambda chunk: (chunk.file_id, chunk.chunk_index))

    async def count_by_file_ids(self, file_ids: list[str]) -> dict[str, int]:
        if not file_ids:
            return {}

        counts: dict[str, int] = {}
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(file_ids):
                result = await session.execute(
                    select(KnowledgeChunk.file_id, func.count())
                    .where(KnowledgeChunk.file_id.in_(batch))
                    .group_by(KnowledgeChunk.file_id)
                )
                counts.update({str(file_id): int(count or 0) for file_id, count in result.all()})
        return counts

    async def list_by_kb_id(self, kb_id: str) -> list[KnowledgeChunk]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id).order_by(KnowledgeChunk.id.asc())
            )
            return list(result.scalars().all())

    async def list_by_chunk_ids(self, chunk_ids: list[str]) -> list[KnowledgeChunk]:
        if not chunk_ids:
            return []
        chunks_by_id: dict[str, KnowledgeChunk] = {}
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(chunk_ids):
                result = await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.chunk_id.in_(batch)))
                chunks_by_id.update({chunk.chunk_id: chunk for chunk in result.scalars().all()})
        return [chunks_by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in chunks_by_id]

    async def batch_upsert(self, chunks: list[dict[str, Any]]) -> list[KnowledgeChunk]:
        if not chunks:
            return []

        sanitized_chunks = [
            {key: value for key, value in chunk.items() if key in self._writable_fields} for chunk in chunks
        ]
        chunk_ids = [chunk["chunk_id"] for chunk in sanitized_chunks]

        async with pg_manager.get_async_session_context() as session:
            existing_by_chunk_id: dict[str, KnowledgeChunk] = {}
            for batch in self._iter_batches(chunk_ids):
                result = await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.chunk_id.in_(batch)))
                existing_by_chunk_id.update({chunk.chunk_id: chunk for chunk in result.scalars().all()})

            records: list[KnowledgeChunk] = []
            for chunk_data in sanitized_chunks:
                chunk_id = chunk_data["chunk_id"]
                record = existing_by_chunk_id.get(chunk_id)
                if record is None:
                    record = KnowledgeChunk(**chunk_data)
                    session.add(record)
                else:
                    for key, value in chunk_data.items():
                        setattr(record, key, value)
                records.append(record)

            return records

    async def delete_by_file_id(self, file_id: str) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.file_id == file_id))
            return int(result.rowcount or 0)

    async def delete_by_kb_id(self, kb_id: str) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id))
            return int(result.rowcount or 0)

    async def count_by_kb_id(self, kb_id: str) -> int:
        return await self._count_by_kb_id(kb_id)

    async def count_graph_indexed_by_kb_id(self, kb_id: str) -> int:
        return await self._count_by_kb_id(kb_id, KnowledgeChunk.graph_indexed.is_(True))

    async def count_graph_indexed_by_file_id(self, file_id: str) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.file_id == file_id, KnowledgeChunk.graph_indexed.is_(True))
            )
            return int(result.scalar() or 0)

    async def count_graph_pending_by_kb_id(self, kb_id: str) -> int:
        return await self._count_by_kb_id(kb_id, KnowledgeChunk.graph_indexed.is_not(True))

    async def _count_by_kb_id(self, kb_id: str, *conditions: Any) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id, *conditions)
            )
            return int(result.scalar() or 0)

    async def list_graph_pending_by_kb_id(self, kb_id: str, limit: int) -> list[KnowledgeChunk]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.kb_id == kb_id, KnowledgeChunk.graph_indexed.is_not(True))
                .order_by(KnowledgeChunk.id.asc())
                .limit(max(limit, 1))
            )
            return list(result.scalars().all())

    async def update_extraction_result(self, chunk_id: str, extraction_result: dict[str, Any]) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.chunk_id == chunk_id)
                .values(extraction_result=extraction_result)
            )

    async def mark_graph_indexed(
        self,
        chunk_id: str,
        ent_ids: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        values: dict[str, Any] = {"graph_indexed": True}
        if ent_ids is not None:
            values["ent_ids"] = ent_ids
        if tags is not None:
            values["tags"] = tags

        async with pg_manager.get_async_session_context() as session:
            await session.execute(update(KnowledgeChunk).where(KnowledgeChunk.chunk_id == chunk_id).values(**values))

    async def reset_graph_state_by_kb_id(self, kb_id: str, clear_extraction_result: bool) -> int:
        values: dict[str, Any] = {"graph_indexed": False}
        if clear_extraction_result:
            values.update({"extraction_result": None, "ent_ids": None, "tags": None})

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(update(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id).values(**values))
            return int(result.rowcount or 0)
