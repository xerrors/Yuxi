"""回收站文件的读取隔离与严格产物清理。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.knowledge.base import KnowledgeBase
from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService


async def test_hydrate_removes_missing_and_trashed_sources(monkeypatch):
    """普通仓储只返回活动文件，缺失来源也不可流出。"""
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    monkeypatch.setattr(
        KnowledgeFileRepository, "get_filenames_by_file_ids", AsyncMock(return_value={"live": "live.md"})
    )
    chunks = [{"metadata": {"file_id": value}, "content": value} for value in ["live", "trash", "missing"]]
    chunks.append({"content": "unattributed"})
    await MilvusKB._hydrate_chunk_sources(object.__new__(MilvusKB), "kb", chunks)
    assert chunks == [{"metadata": {"file_id": "live", "source": "live.md"}, "content": "live"}]


async def test_load_meta_rejects_deleted_record_even_if_repository_returns_it(monkeypatch):
    """替代仓储路径也必须拒绝回收站元数据。"""
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    monkeypatch.setattr(
        KnowledgeFileRepository,
        "get_by_file_id",
        AsyncMock(return_value=SimpleNamespace(kb_id="kb", deleted_at=object())),
    )
    with pytest.raises(ValueError, match="not found"):
        await KnowledgeBase._load_file_meta(object.__new__(MilvusKB), "kb", "trash")


async def test_graph_filters_sources_and_shared_attributes(monkeypatch):
    """共享实体保留活动出处，删除文件的边及混合属性不出站。"""
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    monkeypatch.setattr(
        KnowledgeFileRepository, "get_filenames_by_file_ids", AsyncMock(return_value={"live": "live.md"})
    )
    service = object.__new__(MilvusGraphService)
    service._get_node_file_origins = lambda *_: {"shared": ["live", "trash"], "dead": ["trash"], "chunk": ["live"]}
    graph = {
        "nodes": [{"id": value, "properties": {"attributes": "secret"}} for value in ["shared", "dead", "chunk"]],
        "edges": [
            {"source_id": "shared", "target_id": "chunk", "properties": {"file_id": "live"}},
            {"source_id": "shared", "target_id": "chunk", "properties": {"file_id": "trash"}},
        ],
    }
    result = await service._filter_active_sources("kb", graph)
    assert [node["id"] for node in result["nodes"]] == ["shared", "chunk"]
    assert result["nodes"][0]["properties"] == {}
    assert [edge["properties"]["file_id"] for edge in result["edges"]] == ["live"]


@pytest.mark.parametrize("fail_at", ["graph", "vector", None])
async def test_purge_preserves_pg_chunks_until_external_success(monkeypatch, fail_at):
    """外部失败传播，重试所需PG分块和文件元数据不提前删除。"""
    from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    events = []

    async def graph(*_):
        events.append("graph")
        if fail_at == "graph":
            raise RuntimeError("graph unavailable")

    def vector(**_):
        events.append("vector")
        if fail_at == "vector":
            raise RuntimeError("vector unavailable")

    async def delete_chunks(*_):
        events.append("chunks")

    monkeypatch.setattr(MilvusGraphService, "__init__", lambda self: None)
    monkeypatch.setattr(MilvusGraphService, "delete_file_graph", graph)
    monkeypatch.setattr(KnowledgeChunkRepository, "delete_by_file_id", delete_chunks)
    delete_meta = AsyncMock()
    monkeypatch.setattr(KnowledgeFileRepository, "delete", delete_meta)
    kb = object.__new__(MilvusKB)
    kb._get_existing_milvus_collection = AsyncMock(return_value=SimpleNamespace(delete=vector))
    if fail_at:
        with pytest.raises(RuntimeError, match="unavailable"):
            await kb.purge_file_artifacts("kb", "file")
        assert "chunks" not in events
    else:
        await kb.purge_file_artifacts("kb", "file")
        assert events == ["graph", "vector", "chunks"]
    delete_meta.assert_not_called()


async def test_graph_cleanup_callback_runs_before_reference_commit():
    """外部错误穿过事务回调，不被图谱服务吞掉。"""
    events = []

    async def delete_refs(file_id, *, before_commit):
        events.append("transaction")
        await before_commit(["entity"], ["triple"])
        events.append("commit")

    service = object.__new__(MilvusGraphService)
    service.graph_repo = SimpleNamespace(delete_file_references=delete_refs)
    service._graph_vector_store = SimpleNamespace(
        delete_graph_records=AsyncMock(side_effect=RuntimeError("vector failed"))
    )
    service._delete_file_graph_from_neo4j = lambda *_: events.append("neo4j")
    with pytest.raises(RuntimeError, match="vector failed"):
        await service.delete_file_graph("kb", "file")
    assert events == ["transaction"]


async def test_rerank_rechecks_file_visibility_after_wait(monkeypatch):
    """重排进行期间被删除的文件，在结果出口再次剔除。"""
    from test.unit.plugins.test_milvus_kb import FakeCollection, make_kb, make_query_config
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    visible = True

    async def filenames(self, **_):
        return {"file-1": "live.md"} if visible else {}

    async def rerank(*_, **__):
        nonlocal visible
        visible = False
        return [0.9]

    monkeypatch.setattr(KnowledgeFileRepository, "get_filenames_by_file_ids", filenames)
    monkeypatch.setattr(
        "yuxi.models.rerank.get_reranker", lambda _: SimpleNamespace(acompute_score=rerank, aclose=AsyncMock())
    )
    kb = make_kb(FakeCollection())
    kb._hydrate_chunk_sources = MilvusKB._hydrate_chunk_sources.__get__(kb)
    result = await kb.aquery(
        "query", "db", config=make_query_config(), search_mode="keyword", use_reranker=True, reranker_model="test"
    )
    assert visible is False
    assert result == []
