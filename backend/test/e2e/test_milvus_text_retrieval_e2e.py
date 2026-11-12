"""真实 worker、PostgreSQL、MinIO 与 Milvus 3 的检索闭环。"""

from __future__ import annotations

import asyncio
import io
import json
import os
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock
from urllib.parse import quote
from uuid import uuid4

import pymilvus
import pytest
from packaging.version import Version
from PIL import Image
from pymilvus import Collection, connections, utility
from sqlalchemy import select

from test.e2e.e2e_helpers import wait_model_provider_cache
from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.minio.object_urls import parse_minio_url
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.knowledge.graphs.extractors import normalize_extraction_result
from yuxi.modules.knowledge.graphs.graph_utils import graph_entity_collection_name, graph_triple_collection_name
from yuxi.modules.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService
from yuxi.modules.knowledge.models import (
    KnowledgeChunk,
    KnowledgeGraphEntityMention,
    KnowledgeGraphTripleMention,
    KnowledgeProjectionOutbox,
)
from yuxi.modules.knowledge.repositories.chunks import KnowledgeChunkRepository
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.repositories.graphs import KnowledgeGraphRepository
from yuxi.modules.knowledge.repositories.projections import knowledge_projection_lock

load_models()
pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def _wait_job(client, headers, job_id):
    """等待真实 worker 的任务终态，失败直接暴露持久结果。"""
    async with asyncio.timeout(120):
        while True:
            response = await client.get(f"/api/background-jobs/{job_id}", headers=headers)
            assert response.status_code == 200, response.text
            job = response.json()["job"]
            if job["status"] in {"success", "failed", "cancelled"}:
                assert job["status"] == "success", job
                return
            await asyncio.sleep(0.2)


async def _wait_projection_cleanup(kb_id):
    """等待真实 worker 清理意图完成；随后仍需回读外部存储证明结果。"""
    async with asyncio.timeout(120):
        while True:
            async with pg_manager.get_async_session_context() as session:
                pending = await session.scalar(
                    select(KnowledgeProjectionOutbox.id)
                    .where(
                        KnowledgeProjectionOutbox.kb_id == kb_id,
                        KnowledgeProjectionOutbox.status != "applied",
                    )
                    .limit(1)
                )
            if pending is None:
                return
            await asyncio.sleep(0.2)


class EmbeddingReplay(BaseHTTPRequestHandler):
    """提供固定向量与图谱抽取响应，验证真实 worker 的两种模型协议。"""

    def do_POST(self):
        """按实际端点返回确定的嵌入或非流式抽取结果。"""
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/chat/completions"):
            assert payload["model"] == "replay-chat"
            expected_text = getattr(self.server, "expected_graph_text", "Milvus vector database production reference.")
            assert expected_text in json.dumps(payload["messages"])
            extraction = {
                "entities": [{"text": "Rebuilt One"}, {"text": "Rebuilt Two"}],
                "relations": [{"source": "Rebuilt One", "target": "Rebuilt Two", "text": "rebuilt link", "label": "REBUILT"}],
            }
            response = {
                "id": "chatcmpl-graph-rebuild",
                "object": "chat.completion",
                "created": 1,
                "model": "replay-chat",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(extraction)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        else:
            assert self.path.endswith("/embeddings")
            inputs = payload["input"]
            if isinstance(inputs, str):
                inputs = [inputs]
            response = {
                "object": "list",
                "data": [{"object": "embedding", "index": i, "embedding": [1, 0, 0, 0]} for i in range(len(inputs))],
                "model": "replay-embedding",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def test_worker_index_and_all_search_modes_use_text_constraints(e2e_client, e2e_headers, monkeypatch):
    """索引后回读正文，验证三通道、中文、短语、高亮、重建和删除。"""

    replay = ThreadingHTTPServer(("0.0.0.0", 0), EmbeddingReplay)
    thread = threading.Thread(target=replay.serve_forever, daemon=True)
    thread.start()
    provider_id = f"ci-milvus-{uuid4().hex[:8]}"
    kb_id = None
    provider_created = False
    alias = provider_id
    pg_manager.initialize()
    try:
        created = await e2e_client.post(
            "/api/system/model-providers",
            headers=e2e_headers,
            json={
                "provider_id": provider_id,
                "display_name": "Milvus E2E embedding",
                "provider_type": "openai",
                "base_url": f"http://api:{replay.server_port}/v1",
                "embedding_base_url": f"http://api:{replay.server_port}/v1/embeddings",
                "api_key": "ci-replay-key",
                "capabilities": ["embedding", "chat"],
                "is_enabled": True,
                "enabled_models": [
                    {"id": "replay-embedding", "display_name": "Replay embedding", "type": "embedding", "dimension": 4},
                    {"id": "replay-chat", "display_name": "Replay graph extraction", "type": "chat", "source": "manual"},
                ],
            },
        )
        assert created.status_code == 200, created.text
        provider_created = True
        await wait_model_provider_cache()
        created = await e2e_client.post(
            "/api/knowledge/knowledge-bases",
            headers=e2e_headers,
            json={
                "name": f"pytest_milvus3_{uuid4().hex}",
                "description": "Milvus text E2E",
                "kb_type": "milvus",
                "embedding_model_spec": f"{provider_id}:replay-embedding",
                "additional_params": {},
            },
        )
        assert created.status_code == 200, created.text
        kb_id = created.json()["kb_id"]
        samples = {
            "match.md": "Milvus vector database production reference.",
            "reversed.md": "Milvus database vector production reference.",
            "excluded.md": "Milvus vector database legacy reference.",
            "unrelated.md": "PostgreSQL transactional\nstorage reference.",
            "chinese.md": "知识库支持关键词检索以及混合检索。",
        }
        expected_lines = {name: (1, 2) if name == "unrelated.md" else (1, 1) for name in samples}
        file_ids = {}
        for name, text in samples.items():
            uploaded = await e2e_client.post(
                f"/api/knowledge/files/upload?kb_id={kb_id}",
                headers=e2e_headers,
                files={"file": (name, text.encode(), "text/markdown")},
            )
            assert uploaded.status_code == 200, uploaded.text
            info = uploaded.json()
            added = await e2e_client.post(
                f"/api/knowledge/knowledge-bases/{kb_id}/documents/add",
                headers=e2e_headers,
                json={
                    "items": [info["file_path"]],
                    "params": {
                        "content_hashes": {info["file_path"]: info["content_hash"]},
                        "file_sizes": {info["file_path"]: info["size"]},
                    },
                },
            )
            assert added.status_code == 200, added.text
            file_ids[name] = added.json()["items"][0]["file_id"]

        async def index_documents():
            """通过公共入口触发索引并等待 owning worker 的终态。"""
            indexed = await e2e_client.post(
                f"/api/knowledge/knowledge-bases/{kb_id}/documents/index",
                headers=e2e_headers,
                json={"file_ids": list(file_ids.values()), "params": {}},
            )
            assert indexed.status_code == 200, indexed.text
            await _wait_job(e2e_client, e2e_headers, indexed.json()["job_id"])

        parsed = await e2e_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/parse",
            headers=e2e_headers,
            json={"file_ids": list(file_ids.values()), "params": {}},
        )
        assert parsed.status_code == 200, parsed.text
        await _wait_job(e2e_client, e2e_headers, parsed.json()["job_id"])
        await index_documents()
        repository = KnowledgeChunkRepository()
        for name, file_id in file_ids.items():
            record = await KnowledgeFileRepository().get_by_file_id(file_id)
            assert record.status == "indexed", (name, record.status)
            [chunk] = await repository.list_by_file_id(file_id)
            assert chunk.content == samples[name]
            assert (chunk.start_line, chunk.end_line) == expected_lines[name]
            content_response = await e2e_client.get(
                f"/api/knowledge/knowledge-bases/{kb_id}/documents/{file_id}/content",
                headers=e2e_headers,
            )
            assert content_response.status_code == 200, content_response.text
            [preview_chunk] = content_response.json()["lines"]
            assert (preview_chunk["start_line"], preview_chunk["end_line"]) == expected_lines[name]
            assert content_response.json()["line_location"] == {"available": True, "reason": None}
            assert content_response.json()["content"] == samples[name]
        connections.connect(
            alias=alias,
            uri=os.environ["MILVUS_URI"],
            token=os.getenv("MILVUS_TOKEN", ""),
            db_name=os.getenv("MILVUS_DB", "yuxi"),
        )
        collection = Collection(kb_id, using=alias)
        assert Version(pymilvus.__version__) >= Version("3.0.2")
        assert Version(utility.get_server_version(using=alias)) >= Version("3.0.2")
        collection.load()
        stored = collection.query(expr='id != ""', output_fields=["content", "file_id"], consistency_level="Strong")
        assert {row["content"] for row in stored} == set(samples.values())

        graph_store = MilvusGraphVectorStore()
        for record_type, records in (
            ("entity", [{"id": "e1", "content": "Milvus vector database"}]),
            ("triple", [{"id": "t1", "content": "Milvus supports retrieval", "source_id": "e1", "target_id": "e2"}]),
        ):
            await graph_store.upsert_graph_records(
                kb_id=kb_id,
                embedding_model_spec=f"{provider_id}:replay-embedding",
                record_type=record_type,
                records=records,
            )
        [entity] = await graph_store.search_entities(
            kb_id=kb_id,
            query_text="Milvus",
            embedding_model_spec=f"{provider_id}:replay-embedding",
            top_k=10,
        )
        [triple] = await graph_store.search_triples(
            kb_id=kb_id,
            query_text="Milvus",
            embedding_model_spec=f"{provider_id}:replay-embedding",
            top_k=10,
        )
        assert entity["id"] == "e1" and entity["content"] == "Milvus vector database"
        assert triple["id"] == "t1" and triple["source_id"] == "e1" and triple["target_id"] == "e2"
        assert all(
            utility.has_collection(name, using=alias)
            for name in (
                graph_entity_collection_name(kb_id),
                graph_triple_collection_name(kb_id),
            )
        )

        async def query(mode, **options):
            """从 HTTP 返回读取真实服务端检索结果。"""
            response = await e2e_client.post(
                f"/api/knowledge/knowledge-bases/{kb_id}/query-test",
                headers=e2e_headers,
                json={
                    "query": options.pop("query", "Milvus"),
                    "meta": {
                        "search_mode": mode,
                        "final_top_k": 10,
                        "similarity_threshold": 0,
                        "use_reranker": False,
                        "use_graph_retrieval": False,
                        **options,
                    },
                },
            )
            assert response.status_code == 200, response.text
            results = response.json()
            expected_by_file = {file_ids[name]: lines for name, lines in expected_lines.items()}
            assert all(
                (row["metadata"]["start_line"], row["metadata"]["end_line"]) == expected_by_file[row["metadata"]["file_id"]]
                for row in results
            )
            return results

        active_entities = collection.query(
            expr='id != ""',
            output_fields=["id", "chunk_id", "file_id", "generation", "chunk_index", "content", "embedding"],
            consistency_level="Strong",
        )
        building_entities = [
            {
                **row,
                "id": row["id"] + "-building",
                "chunk_id": row["chunk_id"] + "-building",
                "generation": row["generation"] + 1000,
                "content": "Milvus",
                "embedding": [1, 0, 0, 0],
            }
            for row in active_entities
        ]
        try:
            # 非 active 候选在向量和 BM25 通道都更相关，必须在 top-k 截断前排除。
            collection.upsert([{**row, "embedding": [0, 1, 0, 0]} for row in active_entities] + building_entities)
            collection.flush()
            [raw_hits] = collection.search(
                data=[[1, 0, 0, 0]],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"nprobe": 10}},
                limit=1,
                output_fields=["generation"],
                consistency_level="Strong",
            )
            assert raw_hits[0].entity.get("generation") >= 1000
            for mode in ("vector", "keyword", "hybrid"):
                results = await query(mode, final_top_k=1, bm25_top_k=1)
                assert len(results) == 1, "非 active 候选不能挤掉有效 top-k"
                [visible] = results
                assert not visible["metadata"]["chunk_id"].endswith("-building")
                assert visible["content"] in samples.values()
        finally:
            collection.delete(expr=f"id in {json.dumps([row['id'] for row in building_entities])}")
            collection.upsert(active_entities)
            collection.flush()

        assert len(await query("vector")) == 5
        for mode in ("vector", "keyword", "hybrid"):
            [result] = await query(mode, required_terms="Milvus", excluded_terms="legacy", exact_phrase="vector database")
            assert result["metadata"]["file_id"] == file_ids["match.md"]
            assert result["content"] == samples["match.md"]
            assert result["score_type"] == {"vector": "cosine", "keyword": "bm25", "hybrid": "hybrid"}[mode]
            assert any(segment["matched"] for fragment in result["highlights"] for segment in fragment)
            saved = await e2e_client.put(
                f"/api/knowledge/knowledge-bases/{kb_id}/query-params",
                headers=e2e_headers,
                json={
                    "search_mode": mode,
                    "required_terms": "Milvus",
                    "excluded_terms": "legacy",
                    "exact_phrase": "vector database",
                    "use_graph_retrieval": False,
                    "use_reranker": False,
                },
            )
            assert saved.status_code == 200, saved.text
            public = await e2e_client.post(
                "/api/v1/knowledge/tools/query_kb",
                headers=e2e_headers,
                json={"kb_id": kb_id, "query_text": "Milvus"},
            )
            assert public.status_code == 200, public.text
            [public_result] = public.json()["results"]
            restored = await e2e_client.put(
                f"/api/knowledge/knowledge-bases/{kb_id}/query-params",
                headers=e2e_headers,
                json={"search_mode": "vector", "required_terms": "", "excluded_terms": "", "exact_phrase": ""},
            )
            assert restored.status_code == 200, restored.text
            assert public_result["file_id"] == file_ids["match.md"]
            assert public_result["content"] == result["content"]
            assert public_result["metadata"]["score_type"] == result["score_type"]
            assert public_result["metadata"]["highlights"] == result["highlights"]
            assert await query(mode, file_name="unrelated", required_terms="Milvus") == []
            [by_file] = await query(mode, file_name="match.md")
            assert by_file["metadata"]["file_id"] == file_ids["match.md"]
        [chinese] = await query("keyword", query="关键词")
        assert chinese["content"] == samples["chinese.md"]
        assert any(segment["matched"] for fragment in chinese["highlights"] for segment in fragment)
        assert await query("keyword", exact_phrase='absent") or file_id != "') == []
        for result in await query("hybrid", highlight_results=False):
            assert "highlights" not in result
            assert result["content"] in samples.values()
        for options, detail in (
            ({"required_terms": ["bad"]}, "required_terms 必须是长度不超过 500 的字符串"),
            ({"search_mode": "unsupported"}, "search_mode 不是允许的取值"),
        ):
            invalid = await e2e_client.post(
                f"/api/knowledge/knowledge-bases/{kb_id}/query-test",
                headers=e2e_headers,
                json={"query": "Milvus", "meta": options},
            )
            assert invalid.status_code == 422, invalid.text
            assert detail in invalid.json()["detail"]

        graph_repo = KnowledgeGraphRepository()
        graph_service = KnowledgeGraphService(graph_vector_store=graph_store)

        async def seed_file_graph(filename="match.md", attribute="public-A"):
            """为 active Chunk 发布真实 PG/Neo4j/图向量，验证代次与文件删除。"""
            [chunk] = await repository.list_by_file_id(file_ids[filename])
            payload = normalize_extraction_result(
                {
                    "entities": [
                        {"text": "Alpha", "attributes": [{"text": attribute}]},
                        {"text": "Beta"},
                        {"text": " ALPHA ", "attributes": [{"text": attribute}]},
                    ],
                    "relations": [
                        {"source": "Alpha", "target": "Beta", "text": "links"},
                        {"source": " ALPHA ", "target": "Beta", "text": "duplicate links"},
                    ],
                },
                "llm",
            )
            await publish_chunk_graph(chunk, payload)
            assert await graph_repo.count_by_kb_id(kb_id) == (2, 1)

        async def publish_chunk_graph(chunk, payload):
            """发布测试初始图谱，真实写入 PG、Neo4j 与 Milvus。"""
            await repository.update_extraction_result(chunk.chunk_id, payload)
            entities, triples = await asyncio.to_thread(graph_service.write_chunk_graph, kb_id, chunk, payload)
            await graph_repo.upsert_chunk_graph(
                kb_id=kb_id,
                file_id=chunk.file_id,
                chunk_id=chunk.chunk_id,
                entities=entities,
                triples=triples,
            )
            await repository.mark_graph_structure_indexed(chunk.chunk_id, ent_ids=[e["entity_id"] for e in entities])
            for kind in ("entity", "triple"):
                token, records = await graph_repo.claim_vector_records(
                    kb_id=kb_id,
                    record_type=kind,
                    limit=10,
                    lease_seconds=60,
                )
                if not records:
                    continue
                await graph_store.upsert_graph_records(
                    kb_id=kb_id,
                    embedding_model_spec=f"{provider_id}:replay-embedding",
                    record_type=kind,
                    records=records,
                )
                await graph_repo.mark_vector_records_indexed(
                    record_type=kind,
                    record_ids=[r["id"] for r in records],
                    lock_token=token,
                )
            for name, expected_ids in (
                (graph_entity_collection_name(kb_id), {e["entity_id"] for e in entities}),
                (graph_triple_collection_name(kb_id), {t["triple_id"] for t in triples}),
            ):
                projected = Collection(name, using=alias).query(expr='id != ""', output_fields=["id"], consistency_level="Strong")
                assert expected_ids <= {row["id"] for row in projected}

        def graph_node_count():
            """回读 Neo4j，而不是相信清理回调的返回值。"""
            with graph_service.driver.session() as session:
                return session.run("MATCH (n:MilvusKB {kb_id: $kb_id}) RETURN count(n) AS count", kb_id=kb_id).single()["count"]

        await seed_file_graph()
        assert await asyncio.to_thread(graph_node_count) == 3
        [seeded_chunk] = await repository.list_by_file_id(file_ids["match.md"])
        with monkeypatch.context() as scoped:
            scoped.setattr(graph_store, "delete_graph_records", AsyncMock(side_effect=RuntimeError("temporary cleanup failure")))
            with pytest.raises(RuntimeError, match="temporary cleanup failure"):
                await graph_service.delete_file_graph(kb_id, file_ids["match.md"], generation=seeded_chunk.generation)
        assert await graph_repo.count_by_kb_id(kb_id) == (2, 1)
        assert await asyncio.to_thread(graph_node_count) == 3
        # 历史半成品仍有 Neo4j 文件归属与真实图向量，但 PG Chunk/提及已经级联消失。
        lost_generation = seeded_chunk.generation + 1000
        [lost_chunk] = await repository.batch_upsert(
            [
                {
                    "chunk_id": f"lost-{uuid4().hex}",
                    "file_id": seeded_chunk.file_id,
                    "kb_id": kb_id,
                    "generation": lost_generation,
                    "chunk_index": 99,
                    "content": "Historical graph fragment",
                }
            ]
        )
        await publish_chunk_graph(
            lost_chunk,
            normalize_extraction_result(
                {
                    "entities": [{"text": "Lost One"}, {"text": "Lost Two"}],
                    "relations": [{"source": "Lost One", "target": "Lost Two", "text": "lost link"}],
                },
                "llm",
            ),
        )
        assert await repository.delete_by_file_id(lost_chunk.file_id, generation=lost_generation) == 1
        async with pg_manager.get_async_session_context() as session:
            for model in (KnowledgeChunk, KnowledgeGraphEntityMention, KnowledgeGraphTripleMention):
                assert await session.scalar(select(model.id).where(model.chunk_id == lost_chunk.chunk_id)) is None
        assert await graph_repo.count_by_kb_id(kb_id) == (4, 2)
        assert await asyncio.to_thread(graph_node_count) == 6
        assert {"lost one", "lost two"} <= {
            row["content"]
            for row in Collection(graph_entity_collection_name(kb_id), using=alias).query(
                expr='id != ""', output_fields=["content"], consistency_level="Strong"
            )
        }
        # 文件菜单对应的真实 HTTP 清理：半成品 graph_indexed=false 也必须完整撤销。
        target = file_ids["match.md"]
        saved = await KnowledgeFileRepository().get_by_file_id(target)
        original_before = await e2e_client.get(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{target}/download", headers=e2e_headers)
        assert original_before.status_code == 200, original_before.text
        normal_rows_before = collection.query(
            expr='id != ""', output_fields=["id", "file_id", "content", "generation"], consistency_level="Strong"
        )
        before_content = await e2e_client.get(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{target}/content", headers=e2e_headers)
        for _ in range(2):
            cleared = await e2e_client.post(f"/api/knowledge/knowledge-bases/{kb_id}/graph-build/files/{target}/clear", headers=e2e_headers)
            assert cleared.status_code == 200, cleared.text
            await _wait_job(e2e_client, e2e_headers, cleared.json()["job_id"])
        assert await graph_repo.count_by_kb_id(kb_id) == (0, 0)
        assert await asyncio.to_thread(graph_node_count) == 0
        [cleared_chunk] = await repository.list_by_file_id(target)
        assert cleared_chunk.chunk_id == seeded_chunk.chunk_id
        assert cleared_chunk.extraction_result is None
        assert cleared_chunk.graph_structure_indexed is False and cleared_chunk.graph_indexed is False
        assert cleared_chunk.graph_extraction_details == {"status": "pending", "attempt_count": 0}
        restored = await KnowledgeFileRepository().get_by_file_id(target)
        assert (restored.status, restored.markdown_file, restored.path, restored.active_generation) == (
            "indexed",
            saved.markdown_file,
            saved.path,
            saved.active_generation,
        )
        after_content = await e2e_client.get(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{target}/content", headers=e2e_headers)
        assert after_content.json()["content"] == before_content.json()["content"] == samples["match.md"]
        original_after = await e2e_client.get(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{target}/download", headers=e2e_headers)
        assert original_after.status_code == 200 and original_after.content == original_before.content == samples["match.md"].encode()
        normal_rows_after = collection.query(
            expr='id != ""', output_fields=["id", "file_id", "content", "generation"], consistency_level="Strong"
        )
        assert sorted(normal_rows_after, key=lambda row: row["id"]) == sorted(normal_rows_before, key=lambda row: row["id"])
        for name in (graph_entity_collection_name(kb_id), graph_triple_collection_name(kb_id)):
            assert {
                row["id"] for row in Collection(name, using=alias).query(expr='id != ""', output_fields=["id"], consistency_level="Strong")
            } == {"e1" if name == graph_entity_collection_name(kb_id) else "t1"}
        for mode in ("vector", "keyword", "hybrid"):
            assert target in {row["metadata"]["file_id"] for row in await query(mode)}
        # 清除后经 HTTP 配置与单文件任务，由真实 worker 重新抽取同一正文。
        configured = await e2e_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/graph-build/config",
            headers=e2e_headers,
            json={"extractor_type": "llm", "extractor_options": {"model_spec": f"{provider_id}:replay-chat", "concurrency_count": 1}},
        )
        assert configured.status_code == 200, configured.text
        assert configured.json()["config"]["locked"] is True
        rebuilt = await e2e_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/graph-build/index",
            headers=e2e_headers,
            json={"file_id": target},
        )
        assert rebuilt.status_code == 200, rebuilt.text
        await _wait_job(e2e_client, e2e_headers, rebuilt.json()["job_id"])
        [rebuilt_chunk] = await repository.list_by_file_id(target)
        assert rebuilt_chunk.chunk_id == cleared_chunk.chunk_id
        assert rebuilt_chunk.graph_structure_indexed is True and rebuilt_chunk.graph_indexed is True
        assert {entity["text"] for entity in rebuilt_chunk.extraction_result["entities"]} == {"Rebuilt One", "Rebuilt Two"}
        assert all(chunk.extraction_result is None for chunk in await repository.list_by_kb_id(kb_id) if chunk.file_id != target)
        assert await graph_repo.count_by_kb_id(kb_id) == (2, 1)
        assert await asyncio.to_thread(graph_node_count) == 3
        with graph_service.driver.session() as session:
            assert {row["name"] for row in session.run("MATCH (e:Entity:MilvusKB {kb_id: $kb_id}) RETURN e.name AS name", kb_id=kb_id)} == {
                "Rebuilt One",
                "Rebuilt Two",
            }
            assert [
                row["text"]
                for row in session.run(
                    "MATCH ()-[r:RELATION {kb_id: $kb_id, file_id: $file_id}]->() RETURN r.text AS text",
                    kb_id=kb_id,
                    file_id=target,
                )
            ] == ["rebuilt link"]
        for name, expected_content in (
            (graph_entity_collection_name(kb_id), {"Milvus vector database", "rebuilt one", "rebuilt two"}),
            (graph_triple_collection_name(kb_id), {"Milvus supports retrieval", "rebuilt one → REBUILT → rebuilt two"}),
        ):
            assert {
                row["content"]
                for row in Collection(name, using=alias).query(expr='id != ""', output_fields=["content"], consistency_level="Strong")
            } == expected_content
        # 正文重新入库后，旧代次图谱仍由既有投影清理链路撤销。
        await index_documents()
        assert len(await repository.list_by_kb_id(kb_id)) == 5
        await _wait_projection_cleanup(kb_id)
        assert await graph_repo.count_by_kb_id(kb_id) == (0, 0)
        assert await asyncio.to_thread(graph_node_count) == 0
        assert (
            len(
                await graph_store.search_entities(
                    kb_id=kb_id,
                    query_text="Milvus",
                    embedding_model_spec=f"{provider_id}:replay-embedding",
                    top_k=10,
                )
            )
            == 1
        )
        assert (
            len(
                await graph_store.search_triples(
                    kb_id=kb_id,
                    query_text="Milvus",
                    embedding_model_spec=f"{provider_id}:replay-embedding",
                    top_k=10,
                )
            )
            == 1
        )
        await seed_file_graph("chinese.md")
        await seed_file_graph("match.md", "PRIVATE_SOURCE_B")
        assert len(collection.query(expr='id != ""', output_fields=["file_id"], consistency_level="Strong")) == 5
        removed_file = file_ids["match.md"]
        before = await e2e_client.get("/api/graph/subgraph", headers=e2e_headers, params={"kb_id": kb_id})
        assert before.status_code == 200, before.text
        assert len(before.json()["data"]["nodes"]) == 4
        assert "PRIVATE_SOURCE_B" in json.dumps(before.json())
        async with knowledge_projection_lock(kb_id, shared=True):
            deleted = await e2e_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{removed_file}", headers=e2e_headers)
            assert deleted.status_code == 200, deleted.text
            assert await asyncio.to_thread(graph_node_count) == 4, "清理被锁阻塞，外部旧内容确实还在"
            hidden = await e2e_client.get("/api/graph/subgraph", headers=e2e_headers, params={"kb_id": kb_id})
            assert hidden.status_code == 200, hidden.text
            assert len(hidden.json()["data"]["nodes"]) == 3, "保留共享实体和可见文件的 Chunk"
            assert "PRIVATE_SOURCE_B" not in json.dumps(hidden.json()), "不可返回已删除来源的实体属性"
        assert await repository.list_by_file_id(removed_file) == []
        assert await KnowledgeFileRepository().get_by_file_id(removed_file) is None
        for mode in ("vector", "keyword", "hybrid"):
            assert all(result["metadata"]["file_id"] != removed_file for result in await query(mode))
        await _wait_projection_cleanup(kb_id)
        assert await graph_repo.count_by_kb_id(kb_id) == (2, 1)
        assert await asyncio.to_thread(graph_node_count) == 3
        shared = await e2e_client.get("/api/graph/subgraph", headers=e2e_headers, params={"kb_id": kb_id})
        assert len(shared.json()["data"]["nodes"]) == 3
        assert "PRIVATE_SOURCE_B" not in json.dumps(shared.json())
        assert collection.query(expr=f'file_id == "{removed_file}"', output_fields=["file_id"], consistency_level="Strong") == []
        deleted = await e2e_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=e2e_headers)
        assert deleted.status_code == 200, deleted.text
        assert await repository.list_by_kb_id(kb_id) == []
        await _wait_projection_cleanup(kb_id)
        assert not utility.has_collection(kb_id, using=alias)
        assert not utility.has_collection(graph_entity_collection_name(kb_id), using=alias)
        assert not utility.has_collection(graph_triple_collection_name(kb_id), using=alias)
        assert await repository.list_by_kb_id(kb_id) == []
        kb_id = None
    finally:
        if kb_id:
            await e2e_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=e2e_headers)
        if provider_created:
            await e2e_client.delete(f"/api/system/model-providers/{provider_id}", headers=e2e_headers)
        connections.disconnect(alias)
        await asyncio.to_thread(replay.shutdown)
        thread.join(timeout=5)
        replay.server_close()
        await pg_manager.close()


@pytest.mark.self_managed_resources
async def test_worker_ingest_preserves_submitted_order_and_persisted_parsed_content(e2e_client, e2e_headers):
    """真实 ingest worker 按提交顺序发布结果，正文由 PostgreSQL 与对象读取证明。"""
    from test.support.knowledge import default_embedding_model_spec

    kb_id = None
    samples = [("ingest-first.md", "# First\nFirst ingest proof.\n"), ("ingest-second.md", "# Second\nSecond ingest proof.\n")]
    pg_manager.initialize()
    try:
        created = await e2e_client.post(
            "/api/knowledge/knowledge-bases",
            headers=e2e_headers,
            json={
                "name": f"pytest_ingest_{uuid4().hex}",
                "description": "Worker ingest persistence proof",
                "kb_type": "milvus",
                "embedding_model_spec": default_embedding_model_spec(),
            },
        )
        assert created.status_code == 200, created.text
        kb_id = created.json()["kb_id"]
        uploads = []
        for filename, content in samples:
            response = await e2e_client.post(
                "/api/knowledge/files/upload",
                params={"kb_id": kb_id},
                headers=e2e_headers,
                files={"file": (filename, content.encode(), "text/markdown")},
            )
            assert response.status_code == 200, response.text
            uploads.append(response.json())
        submitted = await e2e_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents",
            headers=e2e_headers,
            json={
                "items": [item["file_path"] for item in uploads],
                "params": {"auto_index": False, "content_hashes": {item["file_path"]: item["content_hash"] for item in uploads}},
            },
        )
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["job_id"]
        await _wait_job(e2e_client, e2e_headers, job_id)
        persisted = await e2e_client.get(f"/api/background-jobs/{job_id}", headers=e2e_headers)
        assert persisted.status_code == 200, persisted.text
        result = persisted.json()["job"]["result"]
        assert result["processed"] == result["succeeded"] == result["submitted"] == 2
        assert result["failed"] == 0 and result["result_truncated"] is False
        assert len(result["items"]) == 2
        for item, (filename, expected_content) in zip(result["items"], samples, strict=True):
            file = await KnowledgeFileRepository().get_by_file_id(item["file_id"])
            assert file.kb_id == kb_id and file.filename == filename and file.status == "parsed"
            assert item["status"] == "parsed" and file.markdown_file
            content = await e2e_client.get(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{file.file_id}/content", headers=e2e_headers)
            assert content.status_code == 200, content.text
            assert content.json()["content"] == expected_content
    finally:
        if kb_id:
            removed = await e2e_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=e2e_headers)
            assert removed.status_code in {200, 404}, removed.text
        await pg_manager.close()


@pytest.mark.self_managed_resources
async def test_worker_reparse_preserves_original_and_rebuilds_artifacts(e2e_client, e2e_headers):
    """真实 DOCX 重解析和 Markdown 保存撤销下游、保留原件图片，并可重新入库构图。"""
    original, embedded_image = _docx_with_embedded_image()
    replay = ThreadingHTTPServer(("0.0.0.0", 0), EmbeddingReplay)
    thread = threading.Thread(target=replay.serve_forever, daemon=True)
    thread.start()
    provider_id = f"ci-document-reset-{uuid4().hex[:8]}"
    kb_id = None
    provider_created = False
    alias = provider_id
    pg_manager.initialize()
    client = get_minio_client()
    files = KnowledgeFileRepository()
    chunks = KnowledgeChunkRepository()
    graphs = KnowledgeGraphRepository()
    graph_service = KnowledgeGraphService()
    try:
        created = await e2e_client.post(
            "/api/system/model-providers",
            headers=e2e_headers,
            json={
                "provider_id": provider_id,
                "display_name": "Document reset E2E replay",
                "provider_type": "openai",
                "base_url": f"http://api:{replay.server_port}/v1",
                "embedding_base_url": f"http://api:{replay.server_port}/v1/embeddings",
                "api_key": "ci-replay-key",
                "capabilities": ["embedding", "chat"],
                "is_enabled": True,
                "enabled_models": [
                    {"id": "replay-embedding", "display_name": "Replay embedding", "type": "embedding", "dimension": 4},
                    {"id": "replay-chat", "display_name": "Replay graph extraction", "type": "chat", "source": "manual"},
                ],
            },
        )
        assert created.status_code == 200, created.text
        provider_created = True
        await wait_model_provider_cache()
        created = await e2e_client.post(
            "/api/knowledge/knowledge-bases",
            headers=e2e_headers,
            json={
                "name": f"pytest_document_reset_{uuid4().hex}",
                "description": "Native DOCX reset lifecycle",
                "kb_type": "milvus",
                "embedding_model_spec": f"{provider_id}:replay-embedding",
            },
        )
        assert created.status_code == 200, created.text
        kb_id = created.json()["kb_id"]
        uploaded = await e2e_client.post(
            f"/api/knowledge/files/upload?kb_id={kb_id}",
            headers=e2e_headers,
            files={"file": ("native-image.docx", original, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert uploaded.status_code == 200, uploaded.text
        upload = uploaded.json()
        added = await e2e_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/add",
            headers=e2e_headers,
            json={
                "items": [upload["file_path"]],
                "params": {
                    "content_hashes": {upload["file_path"]: upload["content_hash"]},
                    "file_sizes": {upload["file_path"]: upload["size"]},
                },
            },
        )
        assert added.status_code == 200, added.text
        file_id = added.json()["items"][0]["file_id"]
        connections.connect(
            alias=alias,
            uri=os.environ["MILVUS_URI"],
            token=os.getenv("MILVUS_TOKEN", ""),
            db_name=os.getenv("MILVUS_DB", "yuxi"),
        )

        async def run_job(path, payload=None):
            """经真实 HTTP 提交用例，等待同一持久任务的 worker 终态。"""
            response = await e2e_client.post(f"/api/knowledge/knowledge-bases/{kb_id}/{path}", headers=e2e_headers, json=payload)
            assert response.status_code == 200, response.text
            await _wait_job(e2e_client, e2e_headers, response.json()["job_id"])

        async def assert_original():
            """同时回读原件对象与授权下载，不能以文件行存在替代内容保留。"""
            record = await files.get_by_file_id(file_id)
            bucket, name = parse_minio_url(record.minio_url or record.path)
            assert await client.adownload_file(bucket, name) == original
            downloaded = await e2e_client.get(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{file_id}/download", headers=e2e_headers)
            assert downloaded.status_code == 200, downloaded.text
            assert downloaded.content == original

        async def parsed_artifacts():
            """回读可信 Markdown 与图片字节，并证明图片链接仍可授权读取。"""
            record = await files.get_by_file_id(file_id)
            assert record.status in {"parsed", "indexed"}
            bucket, name = parse_minio_url(record.markdown_file)
            markdown = await client.adownload_file(bucket, name)
            images = await client.alist_object_metadata(client.KB_BUCKETS["images"], f"{kb_id}/kb-images/{file_id}/")
            assert len(images) == 1
            image_name = images[0]["object_name"]
            image = await client.adownload_file(client.KB_BUCKETS["images"], image_name)
            assert image == embedded_image
            image_url = f"/api/knowledge/knowledge-bases/{kb_id}/images/{quote(image_name.split('/', 1)[1], safe='/')}"
            text = markdown.decode()
            assert text.index("Milvus vector database production reference.") < text.index(image_url) < text.index("After embedded image.")
            preview = await e2e_client.get(image_url, headers=e2e_headers)
            assert preview.status_code == 200 and preview.content == embedded_image
            return record.markdown_file, markdown, image_name, image

        async def query(mode):
            """检索经过真实 HTTP 与 Milvus，不以空计数冒充不可检索。"""
            response = await e2e_client.post(
                f"/api/knowledge/knowledge-bases/{kb_id}/query-test",
                headers=e2e_headers,
                json={
                    "query": "Milvus",
                    "meta": {"search_mode": mode, "use_graph_retrieval": False, "use_reranker": False, "similarity_threshold": 0},
                },
            )
            assert response.status_code == 200, response.text
            return response.json()

        async def assert_parsed_without_index():
            """核对 PG 全代次、普通向量、图谱三种投影以及真实检索的空结果。"""
            record = await files.get_by_file_id(file_id)
            assert record.status == "parsed"
            assert record.chunk_count == record.token_count == 0
            async with pg_manager.get_async_session_context() as session:
                assert await session.scalar(select(KnowledgeChunk.id).where(KnowledgeChunk.file_id == file_id)) is None
            stats = await files.get_kb_file_stats(kb_id)
            assert stats["chunk_count"] == stats["token_count"] == 0
            assert await graphs.count_by_kb_id(kb_id) == (0, 0)
            with graph_service.driver.session() as session:
                assert session.run("MATCH (n:MilvusKB {kb_id: $kb_id}) RETURN count(n) AS count", kb_id=kb_id).single()["count"] == 0
            for name in (kb_id, graph_entity_collection_name(kb_id), graph_triple_collection_name(kb_id)):
                collection = Collection(name, using=alias)
                collection.load()
                assert collection.query(expr='id != ""', output_fields=["id"], consistency_level="Strong") == []
            for mode in ("vector", "keyword", "hybrid"):
                assert await query(mode) == []

        async def index_and_read(expected_text="Milvus vector database production reference."):
            """索引提交后回读新代次，验证正文检索与物理行只属于当前代次。"""
            await run_job("documents/index", {"file_ids": [file_id], "params": {}})
            await _wait_projection_cleanup(kb_id)
            record = await files.get_by_file_id(file_id)
            assert record.status == "indexed"
            [chunk] = await chunks.list_by_file_id(file_id)
            assert chunk.generation == record.active_generation
            collection = Collection(kb_id, using=alias)
            collection.load()
            rows = collection.query(expr='id != ""', output_fields=["chunk_id", "generation", "content"], consistency_level="Strong")
            assert len(rows) == 1 and rows[0]["chunk_id"] == chunk.chunk_id and rows[0]["generation"] == record.active_generation
            assert expected_text in rows[0]["content"] and expected_text in chunk.content
            for mode in ("vector", "keyword", "hybrid"):
                assert {row["metadata"]["file_id"] for row in await query(mode)} == {file_id}
            return record.active_generation, chunk.chunk_id

        async def build_graph():
            """图谱也经真实 worker 构建，确保后续清理拥有非空的外部 oracle。"""
            await run_job("graph-build/index", {"file_id": file_id})
            assert await graphs.count_by_kb_id(kb_id) == (2, 1)
            with graph_service.driver.session() as session:
                assert session.run("MATCH (n:MilvusKB {kb_id: $kb_id}) RETURN count(n) AS count", kb_id=kb_id).single()["count"] == 3
            for name, count in ((graph_entity_collection_name(kb_id), 2), (graph_triple_collection_name(kb_id), 1)):
                collection = Collection(name, using=alias)
                collection.load()
                assert len(collection.query(expr='id != ""', output_fields=["id"], consistency_level="Strong")) == count

        await run_job("documents/parse", {"file_ids": [file_id], "params": {"ocr_engine": "disable"}})
        initial_artifacts = await parsed_artifacts()
        await assert_original()
        initial_generation, _ = await index_and_read()
        configured = await e2e_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/graph-build/config",
            headers=e2e_headers,
            json={"extractor_type": "llm", "extractor_options": {"model_spec": f"{provider_id}:replay-chat", "concurrency_count": 1}},
        )
        assert configured.status_code == 200 and configured.json()["config"]["locked"] is True
        await build_graph()

        await run_job(f"documents/{file_id}/reparse", {"params": {"ocr_engine": "disable"}})
        reparsed = await parsed_artifacts()
        assert reparsed[0] != initial_artifacts[0] and reparsed[2] != initial_artifacts[2]
        assert reparsed[3] == initial_artifacts[3]
        original_bucket, original_markdown = parse_minio_url(initial_artifacts[0])
        assert await client.alist_object_metadata(original_bucket, original_markdown) == []
        assert await client.alist_object_metadata(client.KB_BUCKETS["images"], initial_artifacts[2]) == []
        await assert_parsed_without_index()
        await assert_original()
        reparsed_record = await files.get_by_file_id(file_id)
        assert reparsed_record.active_generation > initial_generation
        indexed_generation, old_chunk = await index_and_read()
        assert indexed_generation > reparsed_record.active_generation
        replacement_generation, replacement_chunk = await index_and_read()
        assert replacement_generation > indexed_generation and replacement_chunk != old_chunk
        assert await parsed_artifacts() == reparsed
        await build_graph()
        await run_job(f"documents/{file_id}/reparse", {"params": {}})
        replacement = await parsed_artifacts()
        assert replacement[0] != reparsed[0] and replacement[2] != reparsed[2]
        for old in (initial_artifacts, reparsed):
            bucket, name = parse_minio_url(old[0])
            assert await client.alist_object_metadata(bucket, name) == []
            assert await client.alist_object_metadata(client.KB_BUCKETS["images"], old[2]) == []
        await assert_parsed_without_index()
        await assert_original()
        await index_and_read()
        await build_graph()

        # 编辑从严格读取获得 CAS 凭据；保存必须清理已存在的检索和图谱。
        markdown_path = f"/api/knowledge/knowledge-bases/{kb_id}/documents/{file_id}/markdown"
        readable = await e2e_client.get(markdown_path, headers=e2e_headers)
        assert readable.status_code == 200, readable.text
        snapshot = readable.json()
        assert snapshot["has_parsed_markdown"] is True
        assert snapshot["content"] == replacement[1].decode()
        marker = "Milvus manually revised Markdown is the current retrieval source."
        edited_content = f"{snapshot['content']}\n\n{marker}\n"
        saved = await e2e_client.post(
            markdown_path, headers=e2e_headers, json={"expected_revision": snapshot["revision"], "content": edited_content}
        )
        assert saved.status_code == 200 and saved.json()["status"] == "queued", saved.text
        await _wait_job(e2e_client, e2e_headers, saved.json()["job_id"])
        await assert_parsed_without_index()
        await assert_original()
        edited = await parsed_artifacts()
        assert edited[0] != replacement[0] and edited[1] == edited_content.encode()
        assert edited[2:] == replacement[2:]
        old_bucket, old_markdown = parse_minio_url(replacement[0])
        assert await client.alist_object_metadata(old_bucket, old_markdown) == []
        current = await e2e_client.get(markdown_path, headers=e2e_headers)
        assert current.status_code == 200, current.text
        assert current.json()["content"] == edited_content and current.json()["has_parsed_markdown"] is True
        assert current.json()["revision"] != snapshot["revision"]

        async with pg_manager.get_async_session_context() as session:
            before_jobs = set(
                await session.scalars(select(BackgroundJobRecord.id).where(BackgroundJobRecord.payload["kb_id"].as_string() == kb_id))
            )
        stale = await e2e_client.post(
            markdown_path, headers=e2e_headers, json={"expected_revision": snapshot["revision"], "content": "stale overwrite"}
        )
        assert stale.status_code == 409, stale.text
        async with pg_manager.get_async_session_context() as session:
            after_jobs = set(
                await session.scalars(select(BackgroundJobRecord.id).where(BackgroundJobRecord.payload["kb_id"].as_string() == kb_id))
            )
        assert after_jobs == before_jobs
        assert await parsed_artifacts() == edited
        replay.expected_graph_text = marker
        await index_and_read(marker)
        await build_graph()
        assert await parsed_artifacts() == edited

        # 同一知识库的未解析原件可从明确空稿人工生成解析结果。
        manual_archive = io.BytesIO(original)
        with zipfile.ZipFile(manual_archive, "a") as archive:
            archive.comment = b"manual-markdown-case"
        manual_original = manual_archive.getvalue()
        uploaded = await e2e_client.post(
            f"/api/knowledge/files/upload?kb_id={kb_id}",
            headers=e2e_headers,
            files={
                "file": ("manual-markdown.docx", manual_original, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        manual_upload = uploaded.json()
        added = await e2e_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/add",
            headers=e2e_headers,
            json={
                "items": [manual_upload["file_path"]],
                "params": {"content_hashes": {manual_upload["file_path"]: manual_upload["content_hash"]}},
            },
        )
        assert added.status_code == 200, added.text
        manual_id = added.json()["items"][0]["file_id"]
        manual_path = f"/api/knowledge/knowledge-bases/{kb_id}/documents/{manual_id}/markdown"
        empty = await e2e_client.get(manual_path, headers=e2e_headers)
        assert empty.status_code == 200, empty.text
        assert empty.json()["has_parsed_markdown"] is False and empty.json()["content"] == ""
        manual_content = "# 人工解析\n\n这是从未解析原件创建的完整 Markdown。\n"
        submitted = await e2e_client.post(
            manual_path, headers=e2e_headers, json={"expected_revision": empty.json()["revision"], "content": manual_content}
        )
        assert submitted.status_code == 200, submitted.text
        await _wait_job(e2e_client, e2e_headers, submitted.json()["job_id"])
        manual_file = await files.get_by_file_id(manual_id)
        assert manual_file.status == "parsed" and manual_file.chunk_count == 0
        bucket, name = parse_minio_url(manual_file.markdown_file)
        assert await client.adownload_file(bucket, name) == manual_content.encode()
        assert await chunks.list_by_file_id(manual_id) == []
        original_download = await e2e_client.get(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/{manual_id}/download", headers=e2e_headers
        )
        assert original_download.status_code == 200 and original_download.content == manual_original
        manual_read = await e2e_client.get(manual_path, headers=e2e_headers)
        assert manual_read.status_code == 200 and manual_read.json()["content"] == manual_content
        assert manual_read.json()["has_parsed_markdown"] is True and manual_read.json()["revision"] != empty.json()["revision"]
        assert await graphs.count_by_kb_id(kb_id) == (2, 1)
        assert await parsed_artifacts() == edited
        for mode in ("vector", "keyword", "hybrid"):
            assert {row["metadata"]["file_id"] for row in await query(mode)} == {file_id}
    finally:
        if kb_id:
            deleted = await e2e_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=e2e_headers)
            assert deleted.status_code == 200, deleted.text
            await _wait_projection_cleanup(kb_id)
        if provider_created:
            await e2e_client.delete(f"/api/system/model-providers/{provider_id}", headers=e2e_headers)
        connections.disconnect(alias)
        await asyncio.to_thread(replay.shutdown)
        thread.join(timeout=5)
        replay.server_close()
        await pg_manager.close()


def _docx_with_embedded_image():
    """仅用现有 Pillow 与 ZIP 生成含正文、内嵌图片和后文的最小 DOCX。"""
    image = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(image, format="PNG")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        output.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        output.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rImage1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/image.png"/>'
            "</Relationships>",
        )
        output.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            "<w:body><w:p><w:r><w:t>Milvus vector database production reference.</w:t></w:r></w:p>"
            '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="190500" cy="190500"/>'
            '<wp:docPr id="1" name="embedded image"/><a:graphic>'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
            '<pic:nvPicPr><pic:cNvPr id="1" name="image.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="rImage1"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="190500" cy="190500"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
            "<w:p><w:r><w:t>After embedded image.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>",
        )
        output.writestr("word/media/image.png", image.getvalue())
    return archive.getvalue(), image.getvalue()
