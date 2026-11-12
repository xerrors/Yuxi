import json
from unittest.mock import AsyncMock

import pytest

import yuxi.modules.knowledge.graphs.milvus_graph_vector_store as graph_module
import yuxi.modules.knowledge.implementations.milvus as milvus_module
from test.unit.support.milvus import FakeCollection, FakeHit, make_kb, make_query_config
from test.unit.support.milvus import fake_milvus_embedding as fake_milvus_embedding
from yuxi.modules.knowledge.implementations.milvus import MilvusKB


@pytest.mark.parametrize("search_mode", ["vector", "keyword", "hybrid"])
async def test_text_constraints_reach_every_retrieval_channel(search_mode):
    """每个召回通道都受必含、排除和短语约束。"""
    collection = FakeCollection()
    kb = make_kb(collection)
    await kb.aquery(
        "query",
        "db",
        config=make_query_config(),
        search_mode=search_mode,
        required_terms="Milvus LangGraph",
        excluded_terms="legacy",
        exact_phrase="知识 检索",
    )
    calls = collection.search_calls if search_mode != "hybrid" else collection.hybrid_calls[0]["reqs"]
    for call in calls:
        expression = call["expr"] if isinstance(call, dict) else call.expr
        assert 'TEXT_MATCH(content, "Milvus") and TEXT_MATCH(content, "LangGraph")' in expression
        assert 'not TEXT_MATCH(content, "legacy")' in expression
        assert 'PHRASE_MATCH(content, "知识 检索", 0)' in expression


async def test_explicit_file_ids_only_narrow_the_active_generation_filter():
    """调用方 file_ids 不能替换活动代次条件。空列表不返回任何文件。"""

    collection = FakeCollection()
    kb = make_kb(collection)
    await kb.aquery("query", "db", config=make_query_config(), file_ids=["file-9"])
    expression = collection.search_calls[0]["expr"]
    assert 'generation == 1 and file_id in ["file-1"]' in expression
    assert 'file_id in ["file-9"]' in expression

    empty = FakeCollection()
    await make_kb(empty).aquery("query", "db", config=make_query_config(), file_ids=[])
    assert "file_id in []" in empty.search_calls[0]["expr"]


def test_filter_literals_escape_expression_syntax():
    """用户文本保持一个字面值，不能变成另一条过滤条件。"""
    kb = object.__new__(MilvusKB)
    phrase = 'a"\\\n) or file_id != ""'
    expression, _ = kb._build_text_search({"exact_phrase": phrase}, "keyword")
    literal = expression.removeprefix("PHRASE_MATCH(content, ").removesuffix(", 0)")
    assert json.loads(literal) == phrase
    assert "\n" not in expression


@pytest.mark.parametrize(
    "options",
    [
        {"required_terms": ["bad"]},
        {"excluded_terms": 1},
        {"exact_phrase": None},
        {"highlight_results": "false"},
    ],
)
def test_invalid_text_options_fail_explicitly(options):
    """非法协议类型不得静默扩大查询范围。"""
    with pytest.raises(ValueError):
        object.__new__(MilvusKB)._build_text_search(options, "keyword")


def test_highlight_segments_preserve_full_content_and_literal_markup():
    """服务端标记被转换为文字段，正文不受高亮处理影响。"""
    hit = FakeHit("<script>source</script>完整正文", 3.5)
    hit.highlight = {"content": {"fragments": ["<img src=x>\ue000Milvus\ue001 matches"], "scores": []}}
    chunk = object.__new__(MilvusKB)._build_chunk_from_hit(hit, 3.5, True, "bm25_score")
    assert chunk["content"] == "<script>source</script>完整正文"
    assert chunk["score_type"] == "bm25"
    assert chunk["highlights"] == [
        [
            {"text": "<img src=x>", "matched": False},
            {"text": "Milvus", "matched": True},
            {"text": " matches", "matched": False},
        ]
    ]


async def test_graph_candidates_cannot_bypass_text_constraints():
    """图谱候选必须再次匹配同一集合中的文本与文件条件。"""
    collection = FakeCollection()
    collection.query = lambda **kwargs: [{"chunk_id": "allowed"}]
    kb = make_kb(collection)
    kb._retrieve_graph_chunks = AsyncMock(
        return_value=[
            {"metadata": {"chunk_id": "allowed"}, "content": "Milvus", "score": 1},
            {"metadata": {"chunk_id": "excluded"}, "content": "other", "score": 1},
        ]
    )
    chunks = await kb.aquery(
        "query",
        "db",
        config=make_query_config(),
        search_mode="keyword",
        required_terms="Milvus",
        use_graph_retrieval=True,
    )
    assert "excluded" not in [chunk["metadata"]["chunk_id"] for chunk in chunks]
    assert "allowed" in [chunk["metadata"]["chunk_id"] for chunk in chunks]
    assert all(chunk["score_type"] == "fusion" for chunk in chunks)


@pytest.mark.parametrize("module,executor", [(milvus_module, MilvusKB), (graph_module, graph_module.MilvusGraphVectorStore)])
def test_database_operations_use_own_connection_and_fail_closed(monkeypatch, module, executor):
    """数据库切换失败传给调用方，不退回默认库。"""
    selected = {}

    def select_database(name, *, using):
        """模拟数据库选择失败。"""
        selected[using] = name
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(module.connections, "connect", lambda **kwargs: None)
    monkeypatch.setattr(module.connections, "has_connection", lambda alias: True)
    monkeypatch.setattr(module.db, "list_database", lambda *, using: ["target"] if using == "own" else [])
    monkeypatch.setattr(module.db, "using_database", select_database)
    instance = object.__new__(executor)
    instance.connection_alias = "own"
    instance.milvus_uri = "http://milvus:19530"
    instance.milvus_token = ""
    instance.milvus_db = "target"
    with pytest.raises(RuntimeError, match="database unavailable"):
        instance._init_connection()
    assert selected == {"own": "target"}


async def test_legacy_empty_options_use_panel_defaults_and_preserve_saved_overrides(monkeypatch):
    """存量空配置与面板一致，显式保存值继续覆盖默认值。"""
    collection = FakeCollection(distance=0.1)
    kb = make_kb(collection)
    monkeypatch.setattr(milvus_module.model_cache, "get_all_specs", lambda kind: [])
    panel = {item["key"]: item["default"] for item in kb.get_query_params_config()["options"]}
    assert panel["similarity_threshold"] == 0.0
    assert panel["bm25_top_k"] == 50
    config = make_query_config()
    assert len(await kb.aquery("query", "db", config=config)) == 1
    await kb.aquery("query", "db", config=config, search_mode="keyword")
    assert collection.search_calls[-1]["limit"] == 50
    config.query_params["options"] = {"similarity_threshold": 0.2, "bm25_top_k": 7}
    assert await kb.aquery("query", "db", config=config) == []
    await kb.aquery("query", "db", config=config, search_mode="keyword")
    assert collection.search_calls[-1]["limit"] == 7
