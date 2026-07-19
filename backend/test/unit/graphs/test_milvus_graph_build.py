from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from yuxi.knowledge.graphs.extractors import (
    GraphExtractorFactory,
    LLMGraphExtractor,
    normalize_extraction_result,
)
from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore


def _raw_graph_node(node_id: str, *, labels: list[str] | None = None, name: str | None = None) -> dict:
    return {
        "id": node_id,
        "labels": labels or ["MilvusKB", "Entity"],
        "properties": {"name": name or node_id, "kb_id": "kb_test"},
    }


def _raw_graph_edge(edge_id: str, source_id: str, target_id: str) -> dict:
    return {
        "id": edge_id,
        "type": "RELATED_TO",
        "source_id": source_id,
        "target_id": target_id,
        "properties": {},
    }


def test_normalize_extraction_result_defaults_and_validates_refs():
    result = normalize_extraction_result(
        {
            "entities": [{"text": "张三"}, {"text": "公司"}],
            "relations": [{"source": "张三", "target": "公司", "text": "任职于"}],
        },
        "llm",
    )

    assert result["entities"][0]["label"] == "Entity"
    assert result["relations"][0]["label"] == "RELATED_TO"
    assert result["relations"][0]["source"] == {"text": "张三", "label": "Entity", "attributes": []}
    assert result["metadata"] == {"extractor_type": "llm", "schema_version": 1}


def test_normalize_extraction_result_accepts_llm_nested_relation_entities():
    result = normalize_extraction_result(
        {
            "relations": [
                {
                    "source": {
                        "text": "张三",
                        "label": "Person",
                        "attributes": [{"text": "工程师", "label": "Occupation"}],
                    },
                    "target": {"text": "公司", "label": "Organization"},
                    "text": "任职于",
                    "label": "WORKS_AT",
                }
            ]
        },
        "llm",
    )

    assert result["entities"] == [
        {"text": "张三", "label": "Person", "attributes": [{"text": "工程师", "label": "Occupation"}]},
        {"text": "公司", "label": "Organization", "attributes": []},
    ]
    assert result["relations"][0]["source"]["attributes"] == [{"text": "工程师", "label": "Occupation"}]
    assert result["relations"][0]["target"] == {"text": "公司", "label": "Organization", "attributes": []}


@pytest.mark.parametrize(
    "payload",
    [
        {"entities": [{"text": "张三"}], "relations": [{"source": "张三", "target": "不存在", "text": "关系"}]},
        {"entities": [{"text": ""}], "relations": []},
    ],
)
def test_normalize_extraction_result_rejects_invalid_payload(payload):
    with pytest.raises(ValueError):
        normalize_extraction_result(payload, "llm")


def test_llm_graph_extractor_rejects_custom_prompt():
    extractor = LLMGraphExtractor({"model_spec": "test/model", "prompt": "custom"})

    with pytest.raises(ValueError, match="不支持自定义完整 Prompt"):
        extractor.validate_options()


def test_llm_graph_extractor_appends_schema_to_fixed_prompt():
    extractor = LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "schema": "实体类型只能是 Person 或 Organization",
            "concurrency_count": 5,
            "model_params": {"temperature": 0.1},
        }
    )

    prompt = extractor._build_prompt("张三任职于公司")

    assert "请从下面文本中抽取实体和实体关系" in prompt
    assert "抽取 Schema 约束" in prompt
    assert "实体类型只能是 Person 或 Organization" in prompt
    assert "文本：\n张三任职于公司" in prompt


def test_graph_extractor_factory_supports_only_llm():
    assert GraphExtractorFactory.supported_types() == ["llm"]


def test_graph_extractor_factory_rejects_spacy():
    with pytest.raises(ValueError, match="spacy"):
        GraphExtractorFactory.create("spacy", {"model": "zh_core_web_sm"})


@pytest.mark.asyncio
async def test_graph_extraction_retries_twice_before_third_attempt_succeeds(monkeypatch):
    chunk = SimpleNamespace(
        chunk_id="chunk_1",
        file_id="file_1",
        chunk_index=0,
        content="张三任职于公司",
        extraction_result=None,
        graph_extraction_details={"status": "failed", "attempt_count": 3},
    )
    extractor = SimpleNamespace(
        extractor_type="llm",
        extract=AsyncMock(
            side_effect=[
                RuntimeError("timeout"),
                ValueError("invalid json"),
                {"entities": [], "relations": []},
            ]
        ),
    )
    chunk_repo = SimpleNamespace(
        mark_graph_extraction_pending=AsyncMock(),
        mark_graph_extraction_failed=AsyncMock(),
        update_extraction_result=AsyncMock(),
    )
    service = MilvusGraphService(chunk_repo=chunk_repo)
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.GRAPH_EXTRACTION_RETRY_DELAYS_SECONDS",
        (0, 0),
    )

    result = await service._get_chunk_extraction_result("kb_test", chunk, extractor)

    assert result["metadata"]["extractor_type"] == "llm"
    assert extractor.extract.await_count == 3
    chunk_repo.mark_graph_extraction_pending.assert_awaited_once_with("chunk_1")
    chunk_repo.update_extraction_result.assert_awaited_once_with("chunk_1", result, 3)
    chunk_repo.mark_graph_extraction_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_build_keeps_extraction_concurrency_full_while_writes_are_blocked(monkeypatch):
    chunks = [
        SimpleNamespace(
            id=index,
            chunk_id=f"chunk_{index}",
            file_id="file_1",
            kb_id="kb_test",
            chunk_index=index,
            content=f"content {index}",
            start_char_pos=0,
            end_char_pos=10,
            extraction_result=None,
            graph_extraction_details={"status": "pending", "attempt_count": 0},
            graph_indexed=False,
        )
        for index in range(1, 7)
    ]
    chunks[0].extraction_result = {"entities": [], "relations": []}
    chunks[0].graph_extraction_details = {"status": "succeeded"}
    kb = SimpleNamespace(
        kb_type="milvus",
        embedding_model_spec="test/embedding",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 2},
            }
        },
    )
    all_extractions_finished = asyncio.Event()
    release_writes = asyncio.Event()

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

    class ChunkRepo:
        async def count_graph_pending_by_kb_id(self, kb_id):
            return sum(not chunk.graph_indexed for chunk in chunks)

        async def count_graph_indexed_by_kb_id(self, kb_id):
            return sum(chunk.graph_indexed for chunk in chunks)

        async def count_graph_extraction_statuses_by_kb_id(self, kb_id):
            counts = {"pending": 0, "succeeded": 0, "failed": 0}
            for chunk in chunks:
                counts[chunk.graph_extraction_details["status"]] += 1
            return counts

        async def list_graph_pending_by_kb_id(self, kb_id, limit, *, after_id=0):
            return [chunk for chunk in chunks if chunk.id > after_id and not chunk.graph_indexed][:limit]

        async def update_extraction_result(self, chunk_id, extraction_result, attempt_count=1):
            chunk = next(chunk for chunk in chunks if chunk.chunk_id == chunk_id)
            chunk.extraction_result = extraction_result
            chunk.graph_extraction_details = {"status": "succeeded", "attempt_count": attempt_count}

        async def mark_graph_extraction_pending(self, chunk_id):
            chunk = next(chunk for chunk in chunks if chunk.chunk_id == chunk_id)
            chunk.graph_extraction_details = {"status": "pending", "attempt_count": 0}

        async def mark_graph_extraction_failed(self, chunk_id, attempt_count, error):
            chunk = next(chunk for chunk in chunks if chunk.chunk_id == chunk_id)
            chunk.graph_extraction_details = {
                "status": "failed",
                "attempt_count": attempt_count,
                "last_error": error,
            }

        async def get_by_chunk_id(self, chunk_id):
            return next(chunk for chunk in chunks if chunk.chunk_id == chunk_id)

        async def mark_graph_indexed(self, chunk_id, ent_ids=None):
            chunk = next(chunk for chunk in chunks if chunk.chunk_id == chunk_id)
            chunk.graph_indexed = True

        async def mark_graph_structure_indexed(self, chunk_id, ent_ids=None):
            chunk = next(chunk for chunk in chunks if chunk.chunk_id == chunk_id)
            chunk.graph_structure_indexed = True

    class Extractor:
        extractor_type = "llm"
        active = 0
        max_active = 0
        calls = 0
        completed = 0

        async def extract(self, text, *, chunk_metadata=None):
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.01)
                if text == "content 6":
                    raise RuntimeError("extract failed")
                return {"entities": [], "relations": []}
            finally:
                self.active -= 1
                self.completed += 1
                if self.completed == 7:
                    all_extractions_finished.set()

    extractor = Extractor()
    monkeypatch.setattr(GraphExtractorFactory, "create", lambda extractor_type, options: extractor)
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.GRAPH_EXTRACTION_RETRY_DELAYS_SECONDS",
        (0, 0),
    )

    async def wait_for_writes(**kwargs):
        await release_writes.wait()

    class GraphRepo:
        upsert_chunk_graph = AsyncMock(side_effect=wait_for_writes)

        async def claim_vector_records(self, **kwargs):
            return "token", []

        async def count_vector_statuses_by_kb_id(self, kb_id):
            return {"pending": 0, "processing": 0, "indexed": 0, "failed": 0}

        async def finalize_graph_indexed_chunks(self, kb_id):
            finalized = 0
            for chunk in chunks:
                if getattr(chunk, "graph_structure_indexed", False) and not chunk.graph_indexed:
                    chunk.graph_indexed = True
                    finalized += 1
            return finalized

    graph_repo = GraphRepo()
    graph_vector_store = SimpleNamespace(upsert_graph_records=AsyncMock())
    service = MilvusGraphService(
        kb_repo=Repo(),
        chunk_repo=ChunkRepo(),
        graph_repo=graph_repo,
        graph_vector_store=graph_vector_store,
    )
    monkeypatch.setattr(service, "write_chunk_graph", lambda kb_id, chunk, result: ([], []))

    build_task = asyncio.create_task(service.build_pending_chunks("kb_test"))
    await asyncio.wait_for(all_extractions_finished.wait(), timeout=1)

    assert extractor.max_active == 2
    assert extractor.calls == 7
    assert not build_task.done()

    release_writes.set()
    result = await asyncio.wait_for(build_task, timeout=1)

    assert result["success"] == 5
    assert result["extraction_failed"] == 1
    assert result["remaining"] == 1
    assert chunks[-1].graph_extraction_details["status"] == "failed"
    assert chunks[-1].graph_extraction_details["attempt_count"] == 3


@pytest.mark.asyncio
async def test_graph_build_cancellation_stops_backpressured_extraction_queue(monkeypatch):
    chunks = [
        SimpleNamespace(
            id=index,
            chunk_id=f"chunk_{index}",
            file_id="file_1",
            kb_id="kb_test",
            chunk_index=index,
            content=f"content {index}",
            extraction_result=None,
            graph_indexed=False,
        )
        for index in range(1, 10)
    ]
    kb = SimpleNamespace(
        kb_type="milvus",
        embedding_model_spec="test/embedding",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 1},
            }
        },
    )
    extraction_started = asyncio.Event()
    never_finish = asyncio.Event()

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

    class ChunkRepo:
        async def count_graph_pending_by_kb_id(self, kb_id):
            return len(chunks)

        async def count_graph_indexed_by_kb_id(self, kb_id):
            return 0

        async def list_graph_pending_by_kb_id(self, kb_id, limit, *, after_id=0):
            return [chunk for chunk in chunks if chunk.id > after_id][:limit]

    class Extractor:
        extractor_type = "llm"

        async def extract(self, text, *, chunk_metadata=None):
            extraction_started.set()
            await never_finish.wait()

    class Context:
        cancel_requested = False

        async def raise_if_cancelled(self):
            if self.cancel_requested:
                raise asyncio.CancelledError

        async def set_progress(self, progress, message):
            return None

    context = Context()
    monkeypatch.setattr(GraphExtractorFactory, "create", lambda extractor_type, options: Extractor())
    service = MilvusGraphService(
        kb_repo=Repo(),
        chunk_repo=ChunkRepo(),
        graph_repo=SimpleNamespace(),
        graph_vector_store=SimpleNamespace(),
    )

    build_task = asyncio.create_task(service.build_pending_chunks("kb_test", context=context))
    await asyncio.wait_for(extraction_started.wait(), timeout=1)
    context.cancel_requested = True

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(build_task, timeout=1.5)


@pytest.mark.asyncio
async def test_graph_build_indexes_vectors_after_structure_write(monkeypatch):
    chunk = SimpleNamespace(
        id=1,
        chunk_id="chunk_1",
        file_id="file_1",
        kb_id="kb_test",
        chunk_index=1,
        content="张三任职于公司",
        start_char_pos=0,
        end_char_pos=8,
        extraction_result=normalize_extraction_result(
            {
                "relations": [
                    {
                        "source": {"text": "张三", "label": "Person"},
                        "target": {"text": "公司", "label": "Organization"},
                        "text": "任职于",
                        "label": "WORKS_AT",
                    }
                ]
            },
            "llm",
        ),
        graph_structure_indexed=False,
        graph_indexed=False,
    )
    kb = SimpleNamespace(
        kb_type="milvus",
        embedding_model_spec="test/embedding",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 2},
            }
        },
    )

    class ChunkRepo:
        async def count_graph_pending_by_kb_id(self, kb_id):
            return int(not chunk.graph_indexed)

        async def count_graph_indexed_by_kb_id(self, kb_id):
            return int(chunk.graph_indexed)

        async def count_graph_extraction_statuses_by_kb_id(self, kb_id):
            return {"pending": 0, "succeeded": 1, "failed": 0}

        async def list_graph_pending_by_kb_id(self, kb_id, limit, *, after_id=0):
            return [chunk] if chunk.id > after_id and not chunk.graph_indexed else []

        async def get_by_chunk_id(self, chunk_id):
            return chunk

        async def mark_graph_structure_indexed(self, chunk_id, ent_ids):
            chunk.graph_structure_indexed = True

    class GraphRepo:
        def __init__(self):
            self.pending = False
            self.indexed = False

        async def upsert_chunk_graph(self, **kwargs):
            self.pending = True

        async def claim_vector_records(self, *, record_type, **kwargs):
            if record_type == "entity" and self.pending:
                self.pending = False
                return "token", [{"id": "entity_1", "content": "张三"}]
            return "token", []

        async def mark_vector_records_indexed(self, **kwargs):
            self.indexed = True

        async def mark_vector_records_failed(self, **kwargs):
            raise AssertionError("vector indexing should succeed")

        async def count_vector_statuses_by_kb_id(self, kb_id):
            return {
                "pending": int(self.pending),
                "processing": 0,
                "indexed": int(self.indexed),
                "failed": 0,
            }

        async def finalize_graph_indexed_chunks(self, kb_id):
            if chunk.graph_structure_indexed and self.indexed:
                chunk.graph_indexed = True
                return 1
            return 0

    graph_repo = GraphRepo()
    vector_store = SimpleNamespace(upsert_graph_records=AsyncMock())
    service = MilvusGraphService(
        kb_repo=SimpleNamespace(get_by_kb_id=AsyncMock(return_value=kb)),
        chunk_repo=ChunkRepo(),
        graph_repo=graph_repo,
        graph_vector_store=vector_store,
    )
    monkeypatch.setattr(
        service,
        "write_chunk_graph",
        lambda kb_id, chunk, result: ([{"entity_id": "entity_1"}], []),
    )

    result = await service.build_pending_chunks("kb_test")

    assert result == {
        "kb_id": "kb_test",
        "success": 1,
        "failed": 0,
        "extraction_failed": 0,
        "write_failed": 0,
        "remaining": 0,
        "vector_failed": 0,
    }
    vector_store.upsert_graph_records.assert_awaited_once()
    assert chunk.graph_structure_indexed is True
    assert chunk.graph_indexed is True


@pytest.mark.asyncio
async def test_graph_build_fails_after_three_vector_attempts(monkeypatch):
    chunk = SimpleNamespace(
        id=1,
        chunk_id="chunk_1",
        file_id="file_1",
        kb_id="kb_test",
        chunk_index=1,
        content="content",
        start_char_pos=0,
        end_char_pos=7,
        extraction_result={"entities": [], "relations": [], "metadata": {"extractor_type": "llm"}},
        graph_structure_indexed=False,
        graph_indexed=False,
    )
    kb = SimpleNamespace(
        kb_type="milvus",
        embedding_model_spec="test/embedding",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 1},
            }
        },
    )

    class ChunkRepo:
        async def count_graph_pending_by_kb_id(self, kb_id):
            return 1

        async def count_graph_indexed_by_kb_id(self, kb_id):
            return 0

        async def count_graph_extraction_statuses_by_kb_id(self, kb_id):
            return {"pending": 0, "succeeded": 1, "failed": 0}

        async def list_graph_pending_by_kb_id(self, kb_id, limit, *, after_id=0):
            return [chunk] if chunk.id > after_id else []

        async def get_by_chunk_id(self, chunk_id):
            return chunk

        async def mark_graph_structure_indexed(self, chunk_id, ent_ids):
            chunk.graph_structure_indexed = True

    class GraphRepo:
        attempts = 0
        status = "pending"

        async def upsert_chunk_graph(self, **kwargs):
            return None

        async def claim_vector_records(self, *, record_type, **kwargs):
            if record_type != "entity" or self.status != "pending":
                return "token", []
            self.status = "processing"
            self.attempts += 1
            return "token", [{"id": "entity_1", "content": "entity"}]

        async def mark_vector_records_indexed(self, **kwargs):
            raise AssertionError("vector indexing should fail")

        async def mark_vector_records_failed(self, **kwargs):
            self.status = "failed" if self.attempts >= 3 else "pending"

        async def count_vector_statuses_by_kb_id(self, kb_id):
            return {
                "pending": int(self.status == "pending"),
                "processing": int(self.status == "processing"),
                "indexed": 0,
                "failed": int(self.status == "failed"),
            }

        async def finalize_graph_indexed_chunks(self, kb_id):
            return 0

    graph_repo = GraphRepo()
    service = MilvusGraphService(
        kb_repo=SimpleNamespace(get_by_kb_id=AsyncMock(return_value=kb)),
        chunk_repo=ChunkRepo(),
        graph_repo=graph_repo,
        graph_vector_store=SimpleNamespace(upsert_graph_records=AsyncMock(side_effect=RuntimeError("embed failed"))),
    )
    monkeypatch.setattr(service, "write_chunk_graph", lambda kb_id, chunk, result: ([{"entity_id": "e"}], []))

    with pytest.raises(RuntimeError, match="vector_failed=1"):
        await service.build_pending_chunks("kb_test")

    assert graph_repo.attempts == 3


@pytest.mark.asyncio
async def test_graph_reconcile_skips_completed_structure(monkeypatch):
    chunk = SimpleNamespace(
        id=1,
        chunk_id="chunk_1",
        graph_structure_indexed=True,
        graph_indexed=False,
        extraction_result={"entities": [], "relations": []},
    )
    kb = SimpleNamespace(
        kb_type="milvus",
        embedding_model_spec="test/embedding",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 1},
            }
        },
    )

    class ChunkRepo:
        async def count_graph_pending_by_kb_id(self, kb_id):
            return int(not chunk.graph_indexed)

        async def count_graph_indexed_by_kb_id(self, kb_id):
            return int(chunk.graph_indexed)

        async def count_graph_extraction_statuses_by_kb_id(self, kb_id):
            return {"pending": 0, "succeeded": 1, "failed": 0}

        async def list_graph_pending_by_kb_id(self, kb_id, limit, *, after_id=0):
            return [chunk] if chunk.id > after_id else []

    class GraphRepo:
        pending = True

        async def upsert_chunk_graph(self, **kwargs):
            raise AssertionError("reconcile must not rewrite graph structure")

        async def claim_vector_records(self, *, record_type, **kwargs):
            if record_type == "entity" and self.pending:
                self.pending = False
                return "token", [{"id": "entity_1", "content": "entity"}]
            return "token", []

        async def mark_vector_records_indexed(self, **kwargs):
            return None

        async def mark_vector_records_failed(self, **kwargs):
            raise AssertionError("vector indexing should succeed")

        async def count_vector_statuses_by_kb_id(self, kb_id):
            return {"pending": int(self.pending), "processing": 0, "indexed": 1, "failed": 0}

        async def finalize_graph_indexed_chunks(self, kb_id):
            chunk.graph_indexed = True
            return 1

    service = MilvusGraphService(
        kb_repo=SimpleNamespace(get_by_kb_id=AsyncMock(return_value=kb)),
        chunk_repo=ChunkRepo(),
        graph_repo=GraphRepo(),
        graph_vector_store=SimpleNamespace(upsert_graph_records=AsyncMock()),
    )
    monkeypatch.setattr(service, "write_chunk_graph", MagicMock(side_effect=AssertionError("must not write")))

    result = await service.build_pending_chunks("kb_test")

    assert result["success"] == 1
    service.write_chunk_graph.assert_not_called()


@pytest.mark.asyncio
async def test_milvus_graph_service_configure_rejects_spacy():
    kb = SimpleNamespace(kb_type="milvus", additional_params={})

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

        async def update(self, kb_id, data):
            raise AssertionError("unsupported extractor should not be persisted")

    service = MilvusGraphService(kb_repo=Repo())

    with pytest.raises(ValueError, match="不支持的图谱抽取器类型"):
        await service.configure(
            "kb_test",
            extractor_type="spacy",
            extractor_options={"model": "zh_core_web_sm"},
            created_by="user_1",
        )


@pytest.mark.asyncio
async def test_milvus_graph_service_configure_persists_updated_concurrency():
    kb = SimpleNamespace(
        kb_type="milvus",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 5},
            }
        },
    )

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

        async def update(self, kb_id, data):
            kb.additional_params = data["additional_params"]
            return kb

    chunk_repo = SimpleNamespace(
        count_by_kb_id=AsyncMock(return_value=0),
        count_graph_pending_by_kb_id=AsyncMock(return_value=0),
        count_graph_indexed_by_kb_id=AsyncMock(return_value=0),
        count_graph_structure_indexed_by_kb_id=AsyncMock(return_value=0),
        count_graph_extraction_statuses_by_kb_id=AsyncMock(return_value={"pending": 0, "succeeded": 0, "failed": 0}),
    )
    graph_repo = SimpleNamespace(
        count_by_kb_id=AsyncMock(return_value=(3, 2)),
        count_vector_statuses_by_kb_id=AsyncMock(
            return_value={"pending": 0, "processing": 0, "indexed": 5, "failed": 0}
        ),
    )
    service = MilvusGraphService(kb_repo=Repo(), chunk_repo=chunk_repo, graph_repo=graph_repo)

    await service.configure(
        "kb_test",
        extractor_type="llm",
        extractor_options={"model_spec": "test/model", "concurrency_count": 9},
        created_by="user_1",
    )
    status = await service.get_status("kb_test")

    assert status["config"]["extractor_options"]["concurrency_count"] == 9
    assert status["entity_count"] == 3
    assert status["relationship_count"] == 2


@pytest.mark.asyncio
async def test_graph_status_reports_latest_successful_run_as_completed():
    kb = SimpleNamespace(
        kb_type="milvus",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 5},
            }
        },
    )

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

    class Tasker:
        async def find_task_by_payload(self, *, task_type, payload_match, statuses):
            assert statuses is None
            return SimpleNamespace(status="success", progress=100)

    chunk_repo = SimpleNamespace(
        count_by_kb_id=AsyncMock(return_value=10),
        count_graph_pending_by_kb_id=AsyncMock(return_value=0),
        count_graph_indexed_by_kb_id=AsyncMock(return_value=10),
        count_graph_structure_indexed_by_kb_id=AsyncMock(return_value=10),
        count_graph_extraction_statuses_by_kb_id=AsyncMock(return_value={"pending": 0, "succeeded": 10, "failed": 0}),
    )
    graph_repo = SimpleNamespace(
        count_by_kb_id=AsyncMock(return_value=(3, 2)),
        count_vector_statuses_by_kb_id=AsyncMock(
            return_value={"pending": 0, "processing": 0, "indexed": 5, "failed": 0}
        ),
    )
    service = MilvusGraphService(kb_repo=Repo(), chunk_repo=chunk_repo, graph_repo=graph_repo)

    status = await service.get_status("kb_test", tasker=Tasker())

    assert status["build_task_status"] == "completed"
    assert status["build_task_progress"] == 100


def test_milvus_graph_service_writes_chunk_entity_and_relation():
    tx = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute_write.side_effect = lambda func: func(tx)
    driver = MagicMock()
    driver.session.return_value = session
    connection = SimpleNamespace(driver=driver)
    service = MilvusGraphService(neo4j_connection=connection)
    chunk = SimpleNamespace(
        chunk_id="chunk_1",
        file_id="file_1",
        kb_id="kb_test",
        chunk_index=1,
        content="张三任职于公司",
        start_char_pos=0,
        end_char_pos=8,
    )

    entities, triples = service.write_chunk_graph(
        "kb_test",
        chunk,
        normalize_extraction_result(
            {
                "relations": [
                    {
                        "source": {
                            "text": "张三",
                            "label": "Person",
                            "attributes": [{"text": "工程师", "label": "Occupation"}],
                        },
                        "target": {"text": "公司", "label": "Organization"},
                        "text": "任职于",
                        "label": "WORKS_AT",
                    }
                ],
            },
            "llm",
        ),
    )

    assert [entity["name"] for entity in entities] == ["张三", "公司"]
    assert {entity["label"] for entity in entities} == {"Person", "Organization"}
    assert triples[0]["relation_type"] == "WORKS_AT"
    queries = [call.args[0] for call in tx.run.call_args_list]
    assert any("MERGE (c:Chunk:MilvusKB:`kb_test`" in query for query in queries)
    assert any("MERGE (e:Entity:MilvusKB:`kb_test`" in query for query in queries)
    assert any("MERGE (source)-[r:RELATION" in query for query in queries)
    entity_call = next(call for call in tx.run.call_args_list if "MERGE (e:Entity" in call.args[0])
    assert entity_call.kwargs["attributes"] == '[{"text": "工程师", "label": "Occupation"}]'


def test_graph_vector_store_uses_idempotent_upsert():
    collection = MagicMock()
    store = MilvusGraphVectorStore.__new__(MilvusGraphVectorStore)

    store._upsert_entities(
        collection,
        [{"id": "entity_1", "content": "entity"}],
        [[0.1, 0.2]],
    )

    collection.upsert.assert_called_once_with([["entity_1"], ["entity"], [[0.1, 0.2]]])
    collection.insert.assert_not_called()


def test_milvus_graph_service_delete_file_graph_uses_scoped_streaming_queries():
    tx = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute_write.side_effect = lambda func: func(tx)
    driver = MagicMock()
    driver.session.return_value = session
    service = MilvusGraphService(neo4j_connection=SimpleNamespace(driver=driver))

    service._delete_file_graph_from_neo4j("kb_test", "file_1")

    queries = [call.args[0] for call in tx.run.call_args_list]
    assert len(queries) == 3
    cleanup_query = queries[1]
    assert "file_id: $file_id" in cleanup_query
    assert "DELETE m" in cleanup_query
    assert "WITH DISTINCT e" in cleanup_query
    assert "collect(" not in cleanup_query
    assert "MATCH (e:Entity:MilvusKB:`kb_test` {kb_id: $kb_id})" not in cleanup_query
    assert "DETACH DELETE c" in queries[2]


def test_milvus_graph_service_process_query_result_keeps_complete_edges():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=2,
        kb_id="kb_test",
    )

    assert [node["id"] for node in result["nodes"]] == ["node-a", "node-b"]
    assert [edge["id"] for edge in result["edges"]] == ["edge-a-b"]


def test_milvus_graph_service_process_query_result_filters_edges_after_node_limit():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=1,
        kb_id="kb_test",
    )

    assert [node["id"] for node in result["nodes"]] == ["node-a"]
    assert result["edges"] == []


def test_milvus_graph_service_process_query_result_filters_edges_to_excluded_chunk_nodes():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("entity-a"),
                "t": _raw_graph_node("chunk-a", labels=["MilvusKB", "Chunk"]),
                "r": _raw_graph_edge("edge-entity-chunk", "entity-a", "chunk-a"),
            }
        ],
        limit=2,
        kb_id="kb_test",
        exclude_chunk=True,
    )

    assert [node["id"] for node in result["nodes"]] == ["entity-a"]
    assert result["edges"] == []


def test_milvus_graph_service_process_query_result_clamps_negative_limit():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=-1,
        kb_id="kb_test",
    )

    assert result == {"nodes": [], "edges": []}


@pytest.mark.parametrize("max_depth", [1, 2, 3])
def test_milvus_graph_service_build_query_uses_requested_depth(max_depth):
    service = MilvusGraphService()

    query = service._build_query("kb_test", "entity", limit=20, max_depth=max_depth)

    assert f"[*1..{max_depth}]" in query
    assert "nodes(path)" in query
    assert "relationships(path)" in query
    assert "nodes(path)) + seeds AS nodes" in query


def test_milvus_graph_service_build_query_excludes_chunks_from_entire_path():
    service = MilvusGraphService()

    query = service._build_query("kb_test", "entity", limit=20, max_depth=3, exclude_chunk=True)

    assert "NOT n:Chunk" in query
    assert "NOT path_node:Chunk" in query


def test_milvus_graph_service_query_nodes_sync_returns_complete_multi_hop_path():
    query_result = MagicMock()
    query_result.single.return_value = {
        "nodes": [_raw_graph_node("node-a"), _raw_graph_node("node-b"), _raw_graph_node("node-c")],
        "edges": [
            _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            _raw_graph_edge("edge-b-c", "node-b", "node-c"),
        ],
    }
    session = MagicMock()
    session.__enter__.return_value = session
    session.run.return_value = query_result
    driver = MagicMock()
    driver.session.return_value = session
    service = MilvusGraphService(neo4j_connection=SimpleNamespace(driver=driver))

    result = service._query_nodes_sync(
        "kb_test",
        "kb_test",
        "node-a",
        limit=3,
        max_depth=2,
        exclude_chunk=False,
    )

    assert [node["id"] for node in result["nodes"]] == ["node-a", "node-b", "node-c"]
    assert [edge["id"] for edge in result["edges"]] == ["edge-a-b", "edge-b-c"]
    query, query_params = session.run.call_args
    assert "[*1..2]" in query[0]
    assert query_params["path_limit"] == 30


def test_milvus_graph_service_query_nodes_sync_caps_max_depth():
    query_result = MagicMock()
    query_result.single.return_value = None
    session = MagicMock()
    session.__enter__.return_value = session
    session.run.return_value = query_result
    driver = MagicMock()
    driver.session.return_value = session
    service = MilvusGraphService(neo4j_connection=SimpleNamespace(driver=driver))

    service._query_nodes_sync(
        "kb_test",
        "kb_test",
        "node-a",
        limit=3,
        max_depth=100,
        exclude_chunk=False,
    )

    query, _ = session.run.call_args
    assert "[*1..3]" in query[0]


@pytest.mark.asyncio
async def test_milvus_graph_service_query_nodes_empty_kb_id():
    service = MilvusGraphService()
    result = await service.query_nodes(kb_id=None, keyword="test")
    assert result == {"nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_milvus_graph_service_get_labels_empty_kb_id():
    service = MilvusGraphService()
    result = await service.get_labels(kb_id=None)
    assert result == []


@pytest.mark.asyncio
async def test_milvus_graph_service_get_stats_empty_kb_id():
    service = MilvusGraphService()
    result = await service.get_stats(kb_id=None)
    assert result == {"total_nodes": 0, "total_edges": 0, "entity_types": []}
