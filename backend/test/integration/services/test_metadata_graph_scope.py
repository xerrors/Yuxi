"""真实 PostgreSQL 与 Neo4j 验证范围在 PPR 候选截断前生效。"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select

from test.integration.api.test_knowledge_directory_imports import directory_kb as directory_kb
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.graphs.extractors import normalize_extraction_result
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService
from yuxi.modules.knowledge.models import KnowledgeChunk, KnowledgeFile
from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository
from yuxi.modules.knowledge.repositories.graphs import KnowledgeGraphRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_filtered_graph_does_not_let_outside_high_score_take_top_k(directory_kb, postgres_sessions):
    """范围外文档有更多连边且排名更高，范围内文档仍能获得唯一名额。"""
    kb_id = directory_kb
    wanted, outside = "file_" + uuid4().hex, "file_" + uuid4().hex
    chunks = {wanted: "chunk_" + uuid4().hex, outside: "chunk_" + uuid4().hex}
    async with postgres_sessions() as session:
        session.add_all(
            [KnowledgeFile(kb_id=kb_id, file_id=file_id, filename=file_id + ".md", status="indexed", is_folder=False) for file_id in chunks]
        )
        await session.flush()
        session.add_all(
            [
                KnowledgeChunk(kb_id=kb_id, file_id=file_id, chunk_id=chunk_id, chunk_index=0, content="graph fixture")
                for file_id, chunk_id in chunks.items()
            ]
        )
        await session.commit()
    pg_manager.initialize()
    graph = KnowledgeGraphService()
    repo = KnowledgeGraphRepository()
    try:
        seed_id = None
        async with postgres_sessions() as session:
            for file_id, names in [(wanted, ["Seed"]), (outside, ["Seed", "ExtraOne", "ExtraTwo"])]:
                chunk = await session.scalar(select(KnowledgeChunk).where(KnowledgeChunk.chunk_id == chunks[file_id]))
                payload = normalize_extraction_result({"entities": [{"text": name} for name in names], "relations": []}, "llm")
                entities, triples = await asyncio.to_thread(graph.write_chunk_graph, kb_id, chunk, payload)
                await repo.upsert_chunk_graph(kb_id=kb_id, file_id=file_id, chunk_id=chunk.chunk_id, entities=entities, triples=triples)
                await KnowledgeChunkRepository().mark_graph_structure_indexed(
                    chunk.chunk_id,
                    ent_ids=[item["entity_id"] for item in entities],
                )
                seed_id = next(item["entity_id"] for item in entities if item["normalized_name"].casefold() == "seed")
        unfiltered = await graph.query_and_rank_chunks_by_ppr(kb_id, {seed_id: 1.0}, max_nodes=100, top_k=1, damping=0.85)
        assert unfiltered[0][0] == chunks[outside]
        filtered = await graph.query_and_rank_chunks_by_ppr(
            kb_id,
            {seed_id: 1.0},
            max_nodes=100,
            top_k=1,
            damping=0.85,
            allowed_file_ids={wanted},
        )
        assert [chunk_id for chunk_id, _score in filtered] == [chunks[wanted]]
        empty = await graph.query_and_rank_chunks_by_ppr(
            kb_id, {seed_id: 1.0}, max_nodes=100, top_k=1, damping=0.85, allowed_file_ids=set()
        )
        assert empty == []
    finally:
        with graph.driver.session() as session:
            session.run("MATCH (n:MilvusKB {kb_id:$kb_id}) DETACH DELETE n", kb_id=kb_id).consume()
        await pg_manager.close()
