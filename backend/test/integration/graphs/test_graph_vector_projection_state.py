from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_graph_repository import KnowledgeGraphRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeFile,
    KnowledgeGraphEntity,
    KnowledgeGraphEntityMention,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vector_projection_claim_and_finalize_chunk():
    # The integration schema fixture uses its own anyio loop. Recreate the
    # SQLAlchemy pool in this test loop before exercising repository leases.
    pg_manager._initialized = False
    pg_manager.async_engine = None
    pg_manager.AsyncSession = None
    pg_manager.initialize()
    await pg_manager.ensure_knowledge_schema()
    suffix = uuid4().hex
    kb_id = f"pytest_vector_{suffix}"
    file_id = f"file_{suffix}"
    chunk_id = f"chunk_{suffix}"
    entity_id = f"entity_{suffix}"
    repo = KnowledgeGraphRepository()

    try:
        async with pg_manager.get_async_session_context() as session:
            session.add(KnowledgeBase(kb_id=kb_id, name="vector projection", kb_type="milvus"))
            await session.flush()
            session.add(KnowledgeFile(file_id=file_id, kb_id=kb_id, filename="test.md"))
            await session.flush()
            session.add(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    file_id=file_id,
                    kb_id=kb_id,
                    chunk_index=0,
                    content="entity",
                    graph_structure_indexed=True,
                    graph_indexed=False,
                )
            )
            session.add(
                KnowledgeGraphEntity(
                    entity_id=entity_id,
                    kb_id=kb_id,
                    normalized_name="entity",
                    label="Entity",
                    name="entity",
                    vector_status="pending",
                )
            )
            await session.flush()
            session.add(
                KnowledgeGraphEntityMention(
                    entity_id=entity_id,
                    kb_id=kb_id,
                    file_id=file_id,
                    chunk_id=chunk_id,
                )
            )

        token, records = await repo.claim_vector_records(
            kb_id=kb_id,
            record_type="entity",
            limit=10,
            lease_seconds=300,
        )

        assert records == [{"id": entity_id, "content": "entity"}]
        await repo.mark_vector_records_indexed(
            record_type="entity",
            record_ids=[entity_id],
            lock_token=token,
        )
        assert await repo.finalize_graph_indexed_chunks(kb_id) == 1

        async with pg_manager.get_async_session_context() as session:
            chunk = await session.scalar(select(KnowledgeChunk).where(KnowledgeChunk.chunk_id == chunk_id))
            entity = await session.scalar(
                select(KnowledgeGraphEntity).where(KnowledgeGraphEntity.entity_id == entity_id)
            )
            assert chunk.graph_indexed is True
            assert entity.vector_status == "indexed"
            assert entity.vector_lock_token is None
    finally:
        async with pg_manager.get_async_session_context() as session:
            kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            if kb is not None:
                await session.delete(kb)
        await pg_manager.async_engine.dispose()
        pg_manager._initialized = False
        pg_manager.async_engine = None
        pg_manager.AsyncSession = None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chunk_extraction_details_support_failure_samples():
    pg_manager._initialized = False
    pg_manager.async_engine = None
    pg_manager.AsyncSession = None
    pg_manager.initialize()
    await pg_manager.ensure_knowledge_schema()
    suffix = uuid4().hex
    kb_id = f"pytest_extraction_{suffix}"
    file_id = f"file_{suffix}"
    chunk_id = f"chunk_{suffix}"
    repo = KnowledgeChunkRepository()

    try:
        async with pg_manager.get_async_session_context() as session:
            session.add(KnowledgeBase(kb_id=kb_id, name="extraction details", kb_type="milvus"))
            await session.flush()
            session.add(KnowledgeFile(file_id=file_id, kb_id=kb_id, filename="test.md"))
            await session.flush()
            session.add(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    file_id=file_id,
                    kb_id=kb_id,
                    chunk_index=0,
                    content="failed extraction sample",
                )
            )

        await repo.mark_graph_extraction_failed(chunk_id, 3, "model timeout")

        counts = await repo.count_graph_extraction_statuses_by_kb_id(kb_id)
        samples = await repo.list_graph_extraction_failed_samples(kb_id, limit=10)
        assert counts == {"pending": 0, "succeeded": 0, "failed": 1}
        assert len(samples) == 1
        assert samples[0]["chunk_id"] == chunk_id
        assert samples[0]["file_id"] == file_id
        assert samples[0]["content"] == "failed extraction sample"
        assert samples[0]["details"]["status"] == "failed"
        assert samples[0]["details"]["attempt_count"] == 3
        assert samples[0]["details"]["last_error"] == "model timeout"
        assert samples[0]["details"]["last_attempt_at"]

        await repo.mark_graph_extraction_pending(chunk_id)
        await repo.update_extraction_result(
            chunk_id,
            {"entities": [], "relations": [], "metadata": {"extractor_type": "llm"}},
            2,
        )

        counts = await repo.count_graph_extraction_statuses_by_kb_id(kb_id)
        assert counts == {"pending": 0, "succeeded": 1, "failed": 0}
        assert await repo.list_graph_extraction_failed_samples(kb_id) == []
    finally:
        async with pg_manager.get_async_session_context() as session:
            kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            if kb is not None:
                await session.delete(kb)
        await pg_manager.async_engine.dispose()
        pg_manager._initialized = False
        pg_manager.async_engine = None
        pg_manager.AsyncSession = None
