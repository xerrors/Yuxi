"""真实 PG/Milvus 验证超过旧文件名上限的范围不会静默截断。"""

import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pymilvus import connections, utility
from sqlalchemy import text

from test.integration.api.test_knowledge_directory_imports import directory_kb as directory_kb
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.metadata import MetadataField, validate_filter
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_large_file_scope_preserves_last_document_in_real_milvus(directory_kb, postgres_sessions, record_property):
    """最后一个匹配文件位于旧 10000 条上限之外，仍进入真实向量检索。"""
    count, kb_id, suffix = 10005, directory_kb, uuid4().hex[:12]
    last_id = f"scale_{suffix}_10004"
    async with postgres_sessions() as session:
        await session.execute(
            text(
                "INSERT INTO knowledge_files(file_id,kb_id,filename,is_folder,status,attributes) "
                "SELECT 'scale_' || :suffix || '_' || lpad(n::text,5,'0'), :kb_id, 'manual_' || lpad(n::text,5,'0') || '.md', "
                "false, 'indexed', jsonb_build_object('model', CASE WHEN n=10004 THEN 'last' ELSE 'other' END) "
                "FROM generate_series(0,10004) AS n"
            ),
            {"suffix": suffix, "kb_id": kb_id},
        )
        await session.commit()
    pg_manager.initialize()
    alias = "metadata_scale_" + suffix
    connections.connect(
        alias=alias, uri=os.environ["MILVUS_URI"], token=os.getenv("MILVUS_TOKEN", ""), db_name=os.getenv("MILVUS_DB", "yuxi")
    )
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = alias
    try:
        started = time.monotonic()
        expression, candidates = await kb._build_retrieval_scope(kb_id, file_name="manual_")
        pg_ms = (time.monotonic() - started) * 1000
        assert len(candidates) == count and (last_id, 1) in candidates
        selected = await KnowledgeFileRepository().list_retrieval_file_generations(
            kb_id,
            validate_filter({"field": "model", "operator": "eq", "value": "last"}, [MetadataField(key="model", name="型号", type="text")]),
            "manual_",
        )
        assert selected == [(last_id, 1)]
        collection = kb._create_new_collection(kb_id, SimpleNamespace(dimension=4, model_id="capacity-fixture"), kb_id)
        collection.insert([["scale_chunk"], ["last document"], ["scale_chunk"], [last_id], [0], [[1.0, 0.0, 0.0, 0.0]], [1]])
        collection.flush()
        collection.load()
        started = time.monotonic()
        result = collection.search(
            data=[[1.0, 0.0, 0.0, 0.0]],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 10}},
            limit=1,
            expr=expression,
            output_fields=["file_id"],
            consistency_level="Strong",
        )
        search_ms = (time.monotonic() - started) * 1000
        assert [hit.entity.get("file_id") for hit in result[0]] == [last_id]
        record_property(
            "metadata_scope_capacity",
            {"files": count, "bytes": len(expression.encode()), "pg_ms": round(pg_ms, 2), "milvus_ms": round(search_ms, 2)},
        )
        print(f"metadata capacity: files={count}, bytes={len(expression.encode())}, pg_ms={pg_ms:.2f}, milvus_ms={search_ms:.2f}")
    finally:
        if utility.has_collection(kb_id, using=alias):
            utility.drop_collection(kb_id, using=alias)
        connections.disconnect(alias)
        await pg_manager.close()
