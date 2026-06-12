import asyncio
import types

import pytest
from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType

import yuxi

if "knowledge_base" not in vars(yuxi):
    yuxi.knowledge_base = types.SimpleNamespace()

from yuxi.knowledge.implementations.milvus import (
    CONTENT_ANALYZER_PARAMS,
    CONTENT_SPARSE_FIELD,
    VECTOR_METRIC_TYPE,
    MilvusKB,
)


class FakeHit:
    def __init__(self, content: str, distance: float):
        self.distance = distance
        self.entity = {
            "content": content,
            "chunk_id": "chunk-1",
            "file_id": "file-1",
            "chunk_index": 0,
        }


class FakeCollection:
    def __init__(self, distance: float = 0.8):
        self.search_calls = []
        self.hybrid_calls = []
        self.insert_calls = []
        self.distance = distance

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
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
    deleted_files = []
    store_calls = []

    async def get_collection(kb_id):
        return collection

    async def forbidden_embedding(texts):
        raise AssertionError("update_content should not embed the whole file directly")

    async def persist_file(file_id):
        persisted_files.append(file_id)

    async def delete_file_chunks_only(kb_id, file_id):
        deleted_files.append((kb_id, file_id))

    async def embed_and_store_chunks(kb_id, file_id, collection_arg, chunks, embedding_function):
        store_calls.append((kb_id, file_id, collection_arg, list(chunks), embedding_function))

    async def parse_file(source, params):
        return "# markdown"

    kb._get_milvus_collection = get_collection
    kb._get_embedding_function = lambda embedding_model_spec: forbidden_embedding
    kb._persist_file = persist_file
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
