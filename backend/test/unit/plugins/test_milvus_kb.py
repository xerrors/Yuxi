import asyncio
import types

import pytest
import yuxi
from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType

if "knowledge_base" not in vars(yuxi):
    yuxi.knowledge_base = types.SimpleNamespace()

from yuxi.knowledge.base import FileStatus
from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.knowledge.implementations.milvus import (
    CONTENT_ANALYZER_PARAMS,
    CONTENT_SPARSE_FIELD,
    VECTOR_METRIC_TYPE,
    MilvusKB,
)


class FakeHit:
    def __init__(self, content: str, distance: float, chunk_id: str = "chunk-1"):
        self.distance = distance
        self.entity = {
            "content": content,
            "chunk_id": chunk_id,
            "file_id": "file-1",
            "chunk_index": 0,
        }


class FakeCollection:
    def __init__(self, distance: float = 0.8, search_results: list | None = None):
        self.search_calls = []
        self.hybrid_calls = []
        self.insert_calls = []
        self.distance = distance
        self._search_results = list(search_results) if search_results else None

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        if self._search_results:
            return self._search_results.pop(0)
        return [[FakeHit("BM25 result", self.distance)]]

    def hybrid_search(self, **kwargs):
        self.hybrid_calls.append(kwargs)
        return [[FakeHit("Hybrid result", self.distance)]]

    def insert(self, entities):
        self.insert_calls.append(entities)


def make_kb(collection: FakeCollection) -> MilvusKB:
    kb = MilvusKB.__new__(MilvusKB)
    kb.databases_meta = {"db": {"embedding_model_spec": "test-provider:test-embedding"}}
    kb.files_meta = {"file-1": {"filename": "demo.md", "kb_id": "db"}}
    kb._get_query_params = lambda kb_id: {}
    kb._get_embedding_function = lambda embedding_model_spec, **kwargs: lambda texts: [[0.1, 0.2] for _ in texts]

    async def get_collection(kb_id: str):
        return collection

    kb._get_milvus_collection = get_collection
    return kb


def make_chunk(index: int, content: str = "content") -> dict:
    return {
        "id": f"id-{index}",
        "chunk_id": f"chunk-{index}",
        "file_id": "file-1",
        "chunk_index": index,
        "content": content,
    }


def test_build_chunk_pg_records_preserves_extraction_result():
    kb = MilvusKB.__new__(MilvusKB)

    records = kb._build_chunk_pg_records(
        "db",
        [
            {
                "chunk_id": "chunk-1",
                "file_id": "file-1",
                "chunk_index": 0,
                "content": "content",
                "extraction_result": {"entities": ["alpha"]},
            }
        ],
    )

    assert records[0]["extraction_result"] == {"entities": ["alpha"]}


async def test_embed_and_store_chunks_batches_embedding_and_insert():
    kb = MilvusKB.__new__(MilvusKB)
    chunks = [make_chunk(index, content=f"text-{index}") for index in range(450)]
    embedding_calls = []
    store_calls = []

    async def embedding_function(texts):
        embedding_calls.append(list(texts))
        return [[float(len(text))] for text in texts]

    async def insert_chunks_to_stores(kb_id, file_id, collection, batch_chunks, embeddings, **kwargs):
        store_calls.append(
            {
                "kb_id": kb_id,
                "file_id": file_id,
                "chunks": list(batch_chunks),
                "embeddings": list(embeddings),
                "kwargs": kwargs,
            }
        )

    kb._insert_chunks_to_stores = insert_chunks_to_stores

    await kb._embed_and_store_chunks(
        "db",
        "file-1",
        FakeCollection(),
        chunks,
        embedding_function,
        chunk_batch_size=200,
    )

    assert [len(call) for call in embedding_calls] == [200, 200, 50]
    assert [len(call["chunks"]) for call in store_calls] == [200, 200, 50]
    assert store_calls[0]["chunks"][0]["chunk_id"] == "chunk-0"
    assert store_calls[1]["chunks"][0]["chunk_id"] == "chunk-200"
    assert store_calls[2]["chunks"][0]["chunk_id"] == "chunk-400"
    assert all(call["kwargs"] == {} for call in store_calls)


def test_calculate_chunk_stats_counts_chunks_and_tokens():
    kb = MilvusKB.__new__(MilvusKB)
    chunks = [make_chunk(0, content="alpha beta"), make_chunk(1, content="中文")]

    stats = kb._calculate_chunk_stats(chunks)

    assert stats == {
        "chunk_count": 2,
        "token_count": count_tokens("alpha beta") + count_tokens("中文"),
    }


async def test_index_file_persists_chunk_stats():
    kb = MilvusKB.__new__(MilvusKB)
    kb.databases_meta = {"db": {"embedding_model_spec": "test-provider:test-embedding", "metadata": {}}}
    kb.files_meta = {
        "file-1": {
            "kb_id": "db",
            "filename": "demo.md",
            "markdown_file": "minio://parsed/db/file-1.md",
            "processing_params": {},
            "status": FileStatus.PARSED,
        }
    }
    kb._metadata_lock = asyncio.Lock()
    collection = FakeCollection()
    deleted_files = []
    store_calls = []
    persisted_files = []
    refreshed_kbs = []
    queue_adds = []
    queue_removes = []
    chunks = [make_chunk(0, content="alpha beta"), make_chunk(1, content="中文")]

    async def get_collection(kb_id):
        return collection

    async def read_markdown(path):
        return "# demo"

    async def embedding_function(texts):
        return [[0.1, 0.2] for _ in texts]

    async def delete_file_chunks_only(kb_id, file_id):
        deleted_files.append((kb_id, file_id))

    async def embed_and_store_chunks(kb_id, file_id, collection_arg, chunk_records, embedding_fn):
        store_calls.append((kb_id, file_id, collection_arg, list(chunk_records), embedding_fn))

    async def persist_file(file_id):
        persisted_files.append((file_id, dict(kb.files_meta[file_id])))

    async def refresh_database_stats(kb_id):
        refreshed_kbs.append(kb_id)
        return {}

    kb._get_milvus_collection = get_collection
    kb._read_markdown_from_minio = read_markdown
    kb._split_text_into_chunks = lambda text, file_id, filename, params: chunks
    kb._get_embedding_function = lambda embedding_model_spec: embedding_function
    kb.delete_file_chunks_only = delete_file_chunks_only
    kb._embed_and_store_chunks = embed_and_store_chunks
    kb._persist_file = persist_file
    kb.refresh_database_stats = refresh_database_stats
    kb._add_to_processing_queue = lambda file_id: queue_adds.append(file_id)
    kb._remove_from_processing_queue = lambda file_id: queue_removes.append(file_id)

    result = await kb.index_file("db", "file-1", operator_id="user-1", params={})

    assert deleted_files == [("db", "file-1")]
    assert len(store_calls) == 1
    assert [chunk["chunk_id"] for chunk in store_calls[0][3]] == ["chunk-0", "chunk-1"]
    assert result["status"] == FileStatus.INDEXED
    assert result["chunk_count"] == 2
    assert result["token_count"] == count_tokens("alpha beta") + count_tokens("中文")
    assert queue_adds == ["file-1"]
    assert queue_removes == ["file-1"]
    assert persisted_files[-1][1]["chunk_count"] == result["chunk_count"]
    assert refreshed_kbs == ["db"]


async def test_delete_file_chunks_only_resets_file_stats(monkeypatch):
    repos = []

    class FakeChunkRepo:
        def __init__(self):
            self.delete_calls = []
            repos.append(self)

        async def count_graph_indexed_by_file_id(self, file_id):
            return 0

        async def delete_by_file_id(self, file_id):
            self.delete_calls.append(file_id)
            return 2

    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
    kb = MilvusKB.__new__(MilvusKB)
    kb.files_meta = {"file-1": {"kb_id": "db", "chunk_count": 2, "token_count": 10}}
    persisted_files = []
    refreshed_kbs = []

    async def get_collection(kb_id):
        return None

    async def persist_file(file_id):
        persisted_files.append((file_id, dict(kb.files_meta[file_id])))

    async def refresh_database_stats(kb_id):
        refreshed_kbs.append(kb_id)
        return {}

    kb._get_milvus_collection = get_collection
    kb._persist_file = persist_file
    kb.refresh_database_stats = refresh_database_stats

    await kb.delete_file_chunks_only("db", "file-1")

    assert repos[0].delete_calls == ["file-1"]
    assert kb.files_meta["file-1"]["chunk_count"] == 0
    assert kb.files_meta["file-1"]["token_count"] == 0
    assert persisted_files == [("file-1", {"kb_id": "db", "chunk_count": 0, "token_count": 0})]
    assert refreshed_kbs == ["db"]


async def test_insert_chunks_to_stores_inserts_current_batch(monkeypatch):
    repos = []

    class FakeChunkRepo:
        def __init__(self):
            self.upsert_calls = []
            self.delete_calls = []
            repos.append(self)

        async def batch_upsert(self, chunks):
            self.upsert_calls.append(chunks)
            return []

        async def delete_by_file_id(self, file_id):
            self.delete_calls.append(file_id)
            return 0

    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
    kb = MilvusKB.__new__(MilvusKB)
    collection = FakeCollection()
    chunks = [make_chunk(index) for index in range(3)]
    embeddings = [[0.1, 0.2] for _ in chunks]

    await kb._insert_chunks_to_stores("db", "file-1", collection, chunks, embeddings)

    assert len(collection.insert_calls) == 1
    assert collection.insert_calls[0][0] == ["id-0", "id-1", "id-2"]
    assert collection.insert_calls[0][5] == embeddings
    assert len(repos[0].upsert_calls) == 1
    assert [record["chunk_id"] for record in repos[0].upsert_calls[0]] == ["chunk-0", "chunk-1", "chunk-2"]


async def test_insert_chunks_to_stores_rolls_back_file_when_milvus_insert_fails(monkeypatch):
    repos = []

    class FakeChunkRepo:
        def __init__(self):
            self.upsert_calls = []
            self.delete_calls = []
            repos.append(self)

        async def batch_upsert(self, chunks):
            self.upsert_calls.append(chunks)
            return []

        async def delete_by_file_id(self, file_id):
            self.delete_calls.append(file_id)
            return 0

    class FailingCollection(FakeCollection):
        def insert(self, entities):
            super().insert(entities)
            raise RuntimeError("milvus boom")

    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
    kb = MilvusKB.__new__(MilvusKB)
    collection = FailingCollection()
    milvus_delete_calls = []

    async def delete_file_chunks_from_milvus(collection_arg, file_id):
        milvus_delete_calls.append((collection_arg, file_id))

    kb._delete_file_chunks_from_milvus = delete_file_chunks_from_milvus
    chunks = [make_chunk(index) for index in range(2)]
    embeddings = [[0.1, 0.2] for _ in chunks]

    with pytest.raises(RuntimeError, match="milvus boom"):
        await kb._insert_chunks_to_stores("db", "file-1", collection, chunks, embeddings)

    assert repos[0].delete_calls == ["file-1"]
    assert milvus_delete_calls == [(collection, "file-1")]


async def test_update_content_uses_streaming_chunk_store(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.databases_meta = {"db": {"embedding_model_spec": "test-provider:test-embedding", "metadata": {}}}
    kb.files_meta = {
        "file-1": {
            "path": "/tmp/demo.md",
            "filename": "demo.md",
            "processing_params": {},
        }
    }
    kb._metadata_lock = asyncio.Lock()
    collection = FakeCollection()
    queue_adds = []
    queue_removes = []
    persisted_files = []
    refreshed_kbs = []
    deleted_files = []
    store_calls = []

    async def get_collection(kb_id):
        return collection

    async def forbidden_embedding(texts):
        raise AssertionError("update_content should not embed the whole file directly")

    async def persist_file(file_id):
        persisted_files.append(file_id)

    async def refresh_database_stats(kb_id):
        refreshed_kbs.append(kb_id)
        return {}

    async def delete_file_chunks_only(kb_id, file_id):
        deleted_files.append((kb_id, file_id))

    async def embed_and_store_chunks(kb_id, file_id, collection_arg, chunks, embedding_function):
        store_calls.append((kb_id, file_id, collection_arg, list(chunks), embedding_function))

    async def parse_file(source, params):
        return "# markdown"

    kb._get_milvus_collection = get_collection
    kb._get_embedding_function = lambda embedding_model_spec: forbidden_embedding
    kb._persist_file = persist_file
    kb.refresh_database_stats = refresh_database_stats
    kb._split_text_into_chunks = lambda text, file_id, filename, params: [make_chunk(0), make_chunk(1)]
    kb.delete_file_chunks_only = delete_file_chunks_only
    kb._embed_and_store_chunks = embed_and_store_chunks
    kb._add_to_processing_queue = lambda file_id: queue_adds.append(file_id)
    kb._remove_from_processing_queue = lambda file_id: queue_removes.append(file_id)
    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.Parser.aparse", parse_file)

    result = await kb.update_content("db", ["file-1"])

    assert deleted_files == [("db", "file-1")]
    assert len(store_calls) == 1
    assert store_calls[0][2] is collection
    assert [chunk["chunk_id"] for chunk in store_calls[0][3]] == ["chunk-0", "chunk-1"]
    assert store_calls[0][4] is forbidden_embedding
    assert result[0]["status"] == "done"
    assert kb.files_meta["file-1"]["status"] == "done"
    assert queue_adds == ["file-1"]
    assert queue_removes == ["file-1"]
    assert persisted_files
    assert refreshed_kbs == ["db"]


async def test_keyword_mode_uses_milvus_bm25_search():
    collection = FakeCollection()
    kb = make_kb(collection)

    chunks = await kb.aquery(
        "alpha beta",
        "db",
        search_mode="keyword",
        bm25_top_k=7,
        bm25_drop_ratio_search=0.2,
    )

    assert chunks[0]["content"] == "BM25 result"
    assert chunks[0]["bm25_score"] == 0.8
    search_call = collection.search_calls[0]
    assert search_call["data"] == ["alpha beta"]
    assert search_call["anns_field"] == CONTENT_SPARSE_FIELD
    assert search_call["param"] == {
        "metric_type": "BM25",
        "params": {"drop_ratio_search": 0.2},
    }
    assert search_call["limit"] == 7


async def test_keyword_mode_precise_match_uses_phrase_match_filter_and_backfill():
    """精准匹配：PHRASE_MATCH 过滤的精准命中在前，BM25 兜底在后，按 chunk_id 去重。"""
    precise_results = [
        [
            FakeHit("precise-1", 0.9, chunk_id="p1"),
            FakeHit("precise-2", 0.7, chunk_id="p2"),
        ]
    ]
    backfill_results = [
        [
            FakeHit("backfill-1", 0.5, chunk_id="b1"),
            FakeHit("backfill-2", 0.3, chunk_id="b2"),
        ]
    ]
    collection = FakeCollection(search_results=[precise_results, backfill_results])
    kb = make_kb(collection)

    chunks = await kb.aquery(
        "扭转减振器",
        "db",
        search_mode="keyword",
        precise_match=True,
        phrase_match_terms=["扭转减振器"],
        final_top_k=10,
    )

    # 第一次 search 带 PHRASE_MATCH 过滤
    precise_call = collection.search_calls[0]
    assert precise_call["anns_field"] == CONTENT_SPARSE_FIELD
    assert 'PHRASE_MATCH(content, "扭转减振器", 0)' in precise_call["expr"]

    # 第二次 search 为纯 BM25 兜底（无 file_name 时 expr 为 None）
    backfill_call = collection.search_calls[1]
    assert backfill_call["expr"] is None

    # 合并顺序：精准在前、兜底在后，去重后共 4 条
    assert [c["metadata"]["chunk_id"] for c in chunks] == ["p1", "p2", "b1", "b2"]
    assert chunks[0]["metadata"]["is_precise_match"] is True
    assert chunks[1]["metadata"]["is_precise_match"] is True
    assert chunks[2]["metadata"]["is_precise_match"] is False
    assert chunks[3]["metadata"]["is_precise_match"] is False


async def test_precise_match_short_circuits_when_enough_hits():
    """精准命中已够 final_top_k 时不再触发兜底查询。"""
    precise_results = [
        [
            FakeHit("precise-1", 0.9, chunk_id="p1"),
            FakeHit("precise-2", 0.7, chunk_id="p2"),
        ]
    ]
    collection = FakeCollection(search_results=[precise_results])
    kb = make_kb(collection)

    chunks = await kb.aquery(
        "term",
        "db",
        search_mode="keyword",
        precise_match=True,
        phrase_match_terms=["term"],
        final_top_k=1,
    )

    assert len(collection.search_calls) == 1
    assert chunks[0]["metadata"]["chunk_id"] == "p1"
    assert chunks[0]["metadata"]["is_precise_match"] is True


async def test_precise_match_degrades_when_no_valid_terms():
    """phrase_match_terms 全为空时降级为纯 BM25，不抛错、不写 is_precise_match。"""
    collection = FakeCollection()
    kb = make_kb(collection)

    chunks = await kb.aquery(
        "fallback query",
        "db",
        search_mode="keyword",
        precise_match=True,
        phrase_match_terms=["", "   "],
        final_top_k=5,
    )

    assert len(collection.search_calls) == 1
    assert collection.search_calls[0]["expr"] is None
    assert "is_precise_match" not in chunks[0]["metadata"]


def test_build_phrase_match_expr_or_joins_and_escapes():
    kb = MilvusKB.__new__(MilvusKB)
    expr = kb._build_phrase_match_expr(["扭转减振器", '含"引号', ""], 0)
    # 多关键词整体加括号，避免与 file_expr 拼 and 时 or 优先级问题
    assert expr == '(PHRASE_MATCH(content, "扭转减振器", 0) or PHRASE_MATCH(content, "含\\"引号", 0))'
    assert kb._build_phrase_match_expr(["", "  "], 0) is None
    assert kb._build_phrase_match_expr(["单关键词"], 2) == 'PHRASE_MATCH(content, "单关键词", 2)'


async def test_vector_mode_ignores_metric_type_override():
    collection = FakeCollection()
    kb = make_kb(collection)

    chunks = await kb.aquery("vector query", "db", search_mode="vector", metric_type="L2")

    assert chunks[0]["content"] == "BM25 result"
    search_call = collection.search_calls[0]
    assert search_call["anns_field"] == "embedding"
    assert search_call["param"]["metric_type"] == VECTOR_METRIC_TYPE


async def test_hybrid_mode_uses_milvus_native_hybrid_search():
    collection = FakeCollection()
    kb = make_kb(collection)

    chunks = await kb.aquery(
        "hybrid query",
        "db",
        search_mode="hybrid",
        final_top_k=3,
        bm25_top_k=8,
        vector_weight=0.6,
        bm25_weight=0.4,
    )

    assert chunks[0]["content"] == "Hybrid result"
    assert chunks[0]["hybrid_score"] == 0.8
    hybrid_call = collection.hybrid_calls[0]
    assert hybrid_call["limit"] == 3
    assert hybrid_call["rerank"]._weights == [0.6, 0.4]

    vector_request, bm25_request = hybrid_call["reqs"]
    assert vector_request.anns_field == "embedding"
    assert vector_request.data == [[0.1, 0.2]]
    assert vector_request.param["metric_type"] == VECTOR_METRIC_TYPE
    assert bm25_request.anns_field == CONTENT_SPARSE_FIELD
    assert bm25_request.data == ["hybrid query"]
    assert bm25_request.limit == 8
    assert bm25_request.param["metric_type"] == "BM25"


async def test_hybrid_mode_filters_scores_below_similarity_threshold():
    collection = FakeCollection(distance=0.1)
    kb = make_kb(collection)

    chunks = await kb.aquery(
        "hybrid query",
        "db",
        search_mode="hybrid",
        final_top_k=3,
        similarity_threshold=0.2,
    )

    assert chunks == []


def test_query_params_config_uses_bm25_parameters():
    kb = MilvusKB.__new__(MilvusKB)

    config = kb.get_query_params_config("db")

    option_keys = {option["key"] for option in config["options"]}
    assert "keyword_top_k" not in option_keys
    assert "metric_type" not in option_keys
    assert {
        "bm25_top_k",
        "vector_weight",
        "bm25_weight",
        "bm25_drop_ratio_search",
    } <= option_keys

    search_mode = next(option for option in config["options"] if option["key"] == "search_mode")
    descriptions = {option["value"]: option["description"] for option in search_mode["options"]}
    assert "BM25" in descriptions["keyword"]
    assert "BM25" in descriptions["hybrid"]


def test_collection_supports_bm25_requires_analyzed_content_sparse_field_and_function():
    kb = MilvusKB.__new__(MilvusKB)
    schema = CollectionSchema(
        fields=[
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=65535,
                enable_analyzer=True,
                analyzer_params=CONTENT_ANALYZER_PARAMS,
                enable_match=True,
            ),
            FieldSchema(name=CONTENT_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR),
        ],
        functions=[
            Function(
                name="content_bm25",
                input_field_names=["content"],
                output_field_names=[CONTENT_SPARSE_FIELD],
                function_type=FunctionType.BM25,
            )
        ],
    )

    collection = type("Collection", (), {"schema": schema})()

    assert kb._collection_supports_bm25(collection)


def test_collection_supports_bm25_requires_enable_match():
    """缺 enable_match 的存量集合应被判为不支持，触发自动重建。"""
    kb = MilvusKB.__new__(MilvusKB)
    schema = CollectionSchema(
        fields=[
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=65535,
                enable_analyzer=True,
                analyzer_params=CONTENT_ANALYZER_PARAMS,
            ),
            FieldSchema(name=CONTENT_SPARSE_FIELD, dtype=DataType.SPARSE_FLOAT_VECTOR),
        ],
        functions=[
            Function(
                name="content_bm25",
                input_field_names=["content"],
                output_field_names=[CONTENT_SPARSE_FIELD],
                function_type=FunctionType.BM25,
            )
        ],
    )

    collection = type("Collection", (), {"schema": schema})()

    assert not kb._collection_supports_bm25(collection)


async def test_migrate_collection_for_match_reuses_embeddings(monkeypatch):
    """向量迁移：旧集合 embedding 原样回灌新集合，不重算；不读 content_sparse。"""
    from yuxi.knowledge.implementations import milvus as milvus_mod

    records = [
        {"id": "id-1", "content": "c1", "chunk_id": "c1", "file_id": "f1", "chunk_index": 0, "embedding": [0.1, 0.2]},
        {"id": "id-2", "content": "c2", "chunk_id": "c2", "file_id": "f1", "chunk_index": 1, "embedding": [0.3, 0.4]},
    ]

    class FakeIterator:
        def __init__(self, batches):
            self._batches = batches
            self._i = 0

        def next(self):
            if self._i >= len(self._batches):
                return []
            batch = self._batches[self._i]
            self._i += 1
            return batch

    class OldCollection:
        def __init__(self):
            self.query_iterator_calls = []

        def load(self):
            pass

        def query_iterator(self, **kwargs):
            self.query_iterator_calls.append(kwargs)
            return FakeIterator([records])

    class NewCollection:
        def __init__(self):
            self.insert_calls = []
            self.flushed = False

        def insert(self, entities):
            self.insert_calls.append(entities)

        def flush(self):
            self.flushed = True

    old = OldCollection()
    new_collection = NewCollection()
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "default"
    kb._create_new_collection = lambda name, info, kb_id: new_collection

    drop_calls = []
    monkeypatch.setattr(milvus_mod.utility, "drop_collection", lambda name, using=None: drop_calls.append(name))
    # __del__ 会对带 connection_alias 的实例调 disconnect，mock 掉避免 pymilvus deprecation 噪音
    monkeypatch.setattr(milvus_mod.connections, "disconnect", lambda *a, **k: None)

    result = await kb._migrate_collection_for_match("col", old, None, "db")

    assert result is new_collection
    assert drop_calls == ["col"]
    assert old.query_iterator_calls[0]["output_fields"] == [
        "id",
        "content",
        "chunk_id",
        "file_id",
        "chunk_index",
        "embedding",
    ]
    assert "content_sparse" not in old.query_iterator_calls[0]["output_fields"]
    assert len(new_collection.insert_calls) == 1
    entities = new_collection.insert_calls[0]
    # 列格式：[ids, contents, chunk_ids, file_ids, chunk_indexes, embeddings]
    assert entities[0] == ["id-1", "id-2"]
    assert entities[2] == ["c1", "c2"]
    assert entities[5] == [[0.1, 0.2], [0.3, 0.4]]
    assert new_collection.flushed is True


async def test_keyword_mode_reranker_keeps_recall_pool(monkeypatch):
    """bug #2: reranker 开启时 keyword 分支不应提前截到 final_top_k，候选池保留 recall_top_k。"""
    precise_results = [
        [
            FakeHit("p1", 0.9, chunk_id="p1"),
            FakeHit("p2", 0.8, chunk_id="p2"),
            FakeHit("p3", 0.7, chunk_id="p3"),
        ]
    ]
    collection = FakeCollection(search_results=[precise_results])
    kb = make_kb(collection)

    captured = {}

    class FakeReranker:
        async def acompute_score(self, pairs, normalize=True):
            _, docs = pairs
            captured["docs"] = list(docs)
            return [0.9, 0.5, 0.7][: len(docs)]

        async def aclose(self):
            pass

    import yuxi.models.rerank as rerank_mod

    monkeypatch.setattr(rerank_mod, "get_reranker", lambda model: FakeReranker())

    chunks = await kb.aquery(
        "term",
        "db",
        search_mode="keyword",
        precise_match=True,
        phrase_match_terms=["term"],
        final_top_k=2,
        use_reranker=True,
        reranker_model="fake",
        recall_top_k=50,
    )

    # precise 命中 3 条短路兜底；reranker 应拿到全部 3 条而非被 final_top_k=2 截断
    assert len(captured["docs"]) == 3
    assert len(chunks) == 2  # 最终截断到 final_top_k


async def test_keyword_mode_precise_match_with_file_name_wraps_or(monkeypatch):
    """bug #3: 多关键词 + file_name 过滤时，or 子句整体加括号，file_name 约束全部关键词。"""
    precise_results = [[FakeHit("precise-1", 0.9, chunk_id="p1")]]
    collection = FakeCollection(search_results=[precise_results])
    kb = make_kb(collection)

    await kb.aquery(
        "kw",
        "db",
        search_mode="keyword",
        precise_match=True,
        phrase_match_terms=["扭转减振器", "减振器"],
        file_name="demo.md",
        final_top_k=5,
    )

    precise_call = collection.search_calls[0]
    expr = precise_call["expr"]
    # file_expr 在前，PHRASE_MATCH 的 or 子句整体被括号包裹
    assert expr.startswith('file_id == "file-1" and (PHRASE_MATCH(content, "扭转减振器", 0) or ')
    assert expr.endswith(")")
