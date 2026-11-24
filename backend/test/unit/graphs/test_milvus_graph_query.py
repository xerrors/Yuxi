from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from yuxi.modules.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService

pytestmark = pytest.mark.usefixtures("milvus_graph_projection_lock")


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


def test_milvus_graph_service_delete_file_graph_uses_scoped_streaming_queries():
    tx = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute_write.side_effect = lambda func: func(tx)
    driver = MagicMock()
    driver.session.return_value = session
    service = KnowledgeGraphService(neo4j_connection=SimpleNamespace(driver=driver))

    service._delete_file_graph_from_neo4j("kb_test", "file_1", ["chunk_1"])

    queries = [call.args[0] for call in tx.run.call_args_list]
    assert len(queries) == 3
    cleanup_query = queries[1]
    assert "file_id: $file_id" in cleanup_query
    assert "DELETE m" in cleanup_query
    assert "WITH DISTINCT e" in cleanup_query
    assert "collect(" not in cleanup_query
    assert "MATCH (e:Entity:MilvusKB:`kb_test` {kb_id: $kb_id})" not in cleanup_query
    assert "DETACH DELETE c" in queries[2]
    assert all("IN $chunk_ids" in query for query in queries)
    assert all(call.kwargs["chunk_ids"] == ["chunk_1"] for call in tx.run.call_args_list)


@pytest.mark.parametrize(
    ("payload", "limit", "exclude_chunk", "expected_node_ids", "expected_edge_ids"),
    [
        (
            [
                {
                    "h": _raw_graph_node("node-a"),
                    "t": _raw_graph_node("node-b"),
                    "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
                }
            ],
            2,
            False,
            ["node-a", "node-b"],
            ["edge-a-b"],
        ),
        (
            [
                {
                    "h": _raw_graph_node("node-a"),
                    "t": _raw_graph_node("node-b"),
                    "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
                }
            ],
            1,
            False,
            ["node-a"],
            [],
        ),
        (
            [
                {
                    "h": _raw_graph_node("entity-a"),
                    "t": _raw_graph_node("chunk-a", labels=["MilvusKB", "Chunk"]),
                    "r": _raw_graph_edge("edge-entity-chunk", "entity-a", "chunk-a"),
                }
            ],
            2,
            True,
            ["entity-a"],
            [],
        ),
        (
            [
                {
                    "h": _raw_graph_node("node-a"),
                    "t": _raw_graph_node("node-b"),
                    "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
                }
            ],
            -1,
            False,
            [],
            [],
        ),
    ],
)
def test_milvus_graph_service_process_query_result(payload, limit, exclude_chunk, expected_node_ids, expected_edge_ids):
    service = KnowledgeGraphService()
    result = service._process_query_result(payload, limit=limit, kb_id="kb_test", exclude_chunk=exclude_chunk)

    assert [node["id"] for node in result["nodes"]] == expected_node_ids
    assert [edge["id"] for edge in result["edges"]] == expected_edge_ids


@pytest.mark.parametrize("max_depth", [1, 2, 3])
def test_milvus_graph_service_build_query_uses_requested_depth(max_depth):
    service = KnowledgeGraphService()

    query = service._build_query("kb_test", "entity", max_depth=max_depth)

    assert f"[*1..{max_depth}]" in query
    assert "nodes(path)" in query
    assert "relationships(path)" in query
    assert "nodes(path)) + seeds AS nodes" in query


def test_milvus_graph_service_build_query_excludes_chunks_from_entire_path():
    service = KnowledgeGraphService()

    query = service._build_query("kb_test", "entity", max_depth=3, exclude_chunk=True)

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
    service = KnowledgeGraphService(neo4j_connection=SimpleNamespace(driver=driver))

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
    service = KnowledgeGraphService(neo4j_connection=SimpleNamespace(driver=driver))

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
@pytest.mark.parametrize(
    ("method", "kwargs", "expected"),
    [
        ("query_nodes", {"keyword": "test"}, {"nodes": [], "edges": []}),
        ("get_labels", {}, []),
        ("get_stats", {}, {"total_nodes": 0, "total_edges": 0, "entity_types": []}),
    ],
)
async def test_milvus_graph_service_early_returns_empty_for_missing_kb_id(method, kwargs, expected):
    service = KnowledgeGraphService()
    assert await getattr(service, method)(kb_id=None, **kwargs) == expected


@pytest.mark.parametrize("operation", ["has_collection", "drop_collection"])
def test_drop_graph_collections_propagates_storage_failure(monkeypatch, operation):
    """图集合检查或删除失败必须向上传播。"""
    from yuxi.modules.knowledge.graphs import milvus_graph_vector_store as module

    store = MilvusGraphVectorStore.__new__(MilvusGraphVectorStore)
    store.connection_alias = "test"
    error = RuntimeError("Milvus unavailable")
    monkeypatch.setattr(module.utility, "has_collection", MagicMock(return_value=True))
    monkeypatch.setattr(module.utility, operation, MagicMock(side_effect=error))

    with pytest.raises(RuntimeError) as caught:
        store.drop_graph_collections("kb_test")

    assert caught.value is error


@pytest.mark.asyncio
async def test_seed_subgraph_limits_nodes_in_cypher_before_returning(monkeypatch):
    """种子子图在 Neo4j 内截断节点，查询参数带上节点上限。"""
    captured = {}

    async def fake_io(func, kb_id, cypher, entity_ids, max_nodes):
        del func, kb_id
        captured["cypher"] = cypher
        captured["entity_ids"] = entity_ids
        captured["max_nodes"] = max_nodes
        return {"nodes": [], "edges": []}

    monkeypatch.setattr("yuxi.modules.knowledge.graphs.service.run_neo4j_query_io", fake_io)
    service = KnowledgeGraphService()

    async def passthrough(kb_id, result):
        del kb_id
        return result

    service._filter_visible_subgraph = passthrough
    await service.query_seed_subgraph("kb_test", entity_ids=["seed", "seed"], max_nodes=10)

    assert "LIMIT $max_nodes" in captured["cypher"]
    assert "collect(p)" not in captured["cypher"]
    assert "AND rel IS NOT NULL" not in captured["cypher"]
    assert "item IS NOT NULL" in captured["cypher"]
    assert "ORDER BY seed_rank" in captured["cypher"]
    assert captured["entity_ids"] == ["seed"]
    assert captured["max_nodes"] == 10

    query_result = MagicMock()
    query_result.single.return_value = {"nodes": [], "edges": []}
    session = MagicMock()
    session.__enter__.return_value = session
    session.run.return_value = query_result
    driver = MagicMock()
    driver.session.return_value = session
    sync_service = KnowledgeGraphService(neo4j_connection=SimpleNamespace(driver=driver))
    sync_service._query_seed_subgraph_sync("kb_test", captured["cypher"], ["seed"], 10)
    _, query_params = session.run.call_args
    assert query_params["max_nodes"] == 10
    assert query_params["path_limit"] == 40


@pytest.mark.asyncio
async def test_ppr_ranking_runs_outside_event_loop(monkeypatch):
    """PageRank 在线程池执行，不占用正在处理请求的事件循环。"""
    import threading

    loop_thread = threading.get_ident()
    seen = []

    def rank(subgraph, seed_weights, *, top_k, damping):
        del subgraph, seed_weights, top_k, damping
        seen.append(threading.get_ident())
        return [("chunk", 1.0)]

    monkeypatch.setattr(KnowledgeGraphService, "rank_chunks_by_ppr", staticmethod(rank))
    service = KnowledgeGraphService()
    service.query_seed_subgraph = MagicMock()

    async def subgraph(*args, **kwargs):
        del args, kwargs
        return {"nodes": [], "edges": []}

    service.query_seed_subgraph = subgraph
    result = await service.query_and_rank_chunks_by_ppr("kb", {"seed": 1.0}, max_nodes=10, top_k=1, damping=0.85)

    assert result == [("chunk", 1.0)]
    assert seen == [seen[0]]
    assert seen[0] != loop_thread


def test_graph_ppr_ranks_chunk_nodes_from_seed_entities():
    """查询图中的实体种子能够按 PPR 找到关联 chunk。"""
    subgraph = {
        "nodes": [
            {"id": "e1", "type": "Entity", "properties": {"entity_id": "seed"}},
            {"id": "c1", "type": "Chunk", "properties": {"chunk_id": "chunk_a"}},
            {"id": "e2", "type": "Entity", "properties": {"entity_id": "other"}},
            {"id": "c2", "type": "Chunk", "properties": {"chunk_id": "chunk_b"}},
        ],
        "edges": [
            {"source_id": "e1", "target_id": "c1"},
            {"source_id": "e1", "target_id": "e2"},
            {"source_id": "e2", "target_id": "c2"},
        ],
    }

    ranked = KnowledgeGraphService.rank_chunks_by_ppr(subgraph, {"seed": 1.0}, top_k=2, damping=0.85)

    assert [chunk_id for chunk_id, _ in ranked] == ["chunk_a", "chunk_b"]


async def test_delete_file_graph_initializes_vector_store_outside_event_loop(monkeypatch):
    """首次删除将同步客户端初始化移出事件循环，完成两种投影清理。"""
    import threading
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    import yuxi.modules.knowledge.graphs.service as module

    loop_thread = threading.get_ident()
    store = SimpleNamespace(delete_graph_records=AsyncMock())
    initialized_threads = []

    def create_store():
        """记录构造线程，拒绝事件循环内初始化。"""
        initialized_threads.append(threading.get_ident())
        assert threading.get_ident() != loop_thread, "网络客户端在事件循环内初始化"
        return store

    @asynccontextmanager
    async def references(_file_id, *, generation, restored_entities, additional_entity_ids, additional_triple_ids):
        """提供本次删除的文件图谱引用。"""
        assert generation == 2
        yield ["entity-1"], ["triple-1"], ["chunk-1"]

    monkeypatch.setattr(module, "MilvusGraphVectorStore", create_store)
    service = KnowledgeGraphService(
        graph_repo=SimpleNamespace(
            delete_file_references=references, file_entity_sources=AsyncMock(return_value=[]), delete_orphan_records=AsyncMock()
        ),
        chunk_repo=SimpleNamespace(list_ids_by_file_generation=AsyncMock(return_value=["chunk-1"])),
    )
    service._file_graph_record_ids = lambda *args: ([], [])
    deleted = []
    service._delete_file_graph_from_neo4j = lambda *args: deleted.append(args)
    await service.delete_file_graph("kb-1", "file-1", generation=2)
    assert initialized_threads and initialized_threads[0] != loop_thread
    store.delete_graph_records.assert_awaited_once_with("kb-1", entity_ids=["entity-1"], triple_ids=["triple-1"])
    assert deleted == [("kb-1", "file-1", ["chunk-1"], [])]
