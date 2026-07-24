import asyncio
import threading
import types

import pytest
from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType

from yuxi.knowledge.base import FileStatus, KnowledgeBase
from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
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
    kb._get_query_params = lambda kb_id: {}
    kb._get_embedding_function = lambda embedding_model_spec, **kwargs: lambda texts: [[0.1, 0.2] for _ in texts]

    async def get_collection(kb_id: str):
        return collection

    async def hydrate_chunk_sources(kb_id: str, chunks: list[dict]) -> None:
        for chunk in chunks:
            chunk["metadata"]["source"] = "demo.md"

    kb._get_milvus_collection = get_collection
    kb._hydrate_chunk_sources = hydrate_chunk_sources
    return kb


def make_file_record(**overrides):
    data = {
        "file_id": "file-1",
        "kb_id": "db",
        "parent_id": None,
        "filename": "demo.md",
        "file_type": "md",
        "path": "/tmp/demo.md",
        "minio_url": None,
        "markdown_file": "minio://parsed/db/file-1.md",
        "status": FileStatus.PARSED,
        "content_hash": None,
        "file_size": 0,
        "chunk_count": 0,
        "token_count": 0,
        "content_type": "file",
        "processing_params": {},
        "is_folder": False,
        "error_message": None,
        "created_by": None,
        "updated_by": None,
        "created_at": None,
        "updated_at": None,
        "original_filename": None,
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


class FakeKnowledgeFileRepository:
    def __init__(self, records: dict[str, types.SimpleNamespace]):
        self.records = records
        self.update_calls = []
        self.conditional_update_calls = []
        self.deleted = []

    async def get_by_file_id(self, file_id: str):
        return self.records.get(file_id)

    async def update_fields_if_status(self, *, kb_id: str, file_id: str, allowed_statuses: set[str], data: dict):
        record = self.records.get(file_id)
        self.conditional_update_calls.append((kb_id, file_id, set(allowed_statuses), dict(data)))
        if record is None or record.kb_id != kb_id or record.status not in allowed_statuses:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        return record

    async def update_fields(self, *, file_id: str, data: dict, kb_id: str | None = None):
        await asyncio.sleep(0)
        record = self.records.get(file_id)
        if record is None or (kb_id and record.kb_id != kb_id):
            return None
        for key, value in data.items():
            setattr(record, key, value)
        self.update_calls.append((file_id, kb_id, dict(data)))
        return record

    async def get_filenames_by_file_ids(self, *, kb_id: str, file_ids: list[str]):
        return {
            file_id: record.filename
            for file_id in file_ids
            if (record := self.records.get(file_id)) is not None and record.kb_id == kb_id
        }

    async def list_file_ids_by_filename_contains(self, *, kb_id: str, filename_pattern: str, limit: int = 10_000):
        return [
            file_id
            for file_id, record in self.records.items()
            if record.kb_id == kb_id and filename_pattern.lower() in record.filename.lower()
        ][:limit]

    async def delete(self, file_id: str) -> None:
        self.deleted.append(file_id)
        self.records.pop(file_id, None)


def patch_file_repository(monkeypatch, file_repo: FakeKnowledgeFileRepository) -> None:
    monkeypatch.setattr("yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository", lambda: file_repo)
    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeFileRepository", lambda: file_repo)


def make_chunk(index: int, content: str = "content") -> dict:
    return {
        "id": f"id-{index}",
        "chunk_id": f"chunk-{index}",
        "file_id": "file-1",
        "chunk_index": index,
        "content": content,
    }


async def test_delete_database_offloads_milvus_cleanup(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "test-alias"
    event_loop_thread = threading.get_ident()
    cleanup_threads = []
    calls = []

    def record_cleanup(name):
        cleanup_threads.append(threading.get_ident())
        calls.append(name)

    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.utility.has_collection",
        lambda kb_id, using: record_cleanup("has_collection") or True,
    )
    monkeypatch.setattr(
        "yuxi.knowledge.implementations.milvus.utility.drop_collection",
        lambda kb_id, using: record_cleanup("drop_collection"),
    )

    class FakeGraphVectorStore:
        def __init__(self):
            record_cleanup("graph_init")

        def drop_graph_collections(self, kb_id):
            record_cleanup("drop_graph_collections")

    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_vector_store.MilvusGraphVectorStore",
        FakeGraphVectorStore,
    )

    async def delete_base(self, kb_id):
        calls.append("delete_base")
        return {"message": "删除成功"}

    monkeypatch.setattr(KnowledgeBase, "delete_database", delete_base)

    result = await kb.delete_database("db")

    assert result == {"message": "删除成功"}
    assert calls == ["has_collection", "drop_collection", "graph_init", "drop_graph_collections", "delete_base"]
    assert cleanup_threads
    assert all(thread_id != event_loop_thread for thread_id in cleanup_threads)


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


async def test_index_file_persists_chunk_stats(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.databases_meta = {"db": {"embedding_model_spec": "test-provider:test-embedding", "metadata": {}}}
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record()})
    patch_file_repository(monkeypatch, file_repo)
    collection = FakeCollection()
    deleted_files = []
    store_calls = []
    refreshed_kbs = []
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

    async def refresh_database_stats(kb_id):
        refreshed_kbs.append(kb_id)
        return {}

    kb._get_milvus_collection = get_collection
    kb._read_markdown_from_minio = read_markdown
    kb._split_text_into_chunks = lambda text, file_id, filename, params: chunks
    kb._get_embedding_function = lambda embedding_model_spec: embedding_function
    kb.delete_file_chunks_only = delete_file_chunks_only
    kb._embed_and_store_chunks = embed_and_store_chunks
    kb.refresh_database_stats = refresh_database_stats

    result = await kb.index_file("db", "file-1", operator_id="user-1", params={})

    assert deleted_files == [("db", "file-1")]
    assert len(store_calls) == 1
    assert [chunk["chunk_id"] for chunk in store_calls[0][3]] == ["chunk-0", "chunk-1"]
    assert result["status"] == FileStatus.INDEXED
    assert result["chunk_count"] == 2
    assert result["token_count"] == count_tokens("alpha beta") + count_tokens("中文")
    assert file_repo.records["file-1"].chunk_count == result["chunk_count"]
    assert file_repo.conditional_update_calls[0][3]["status"] == FileStatus.INDEXING
    assert file_repo.update_calls[-1][2]["status"] == FileStatus.INDEXED
    assert refreshed_kbs == ["db"]


async def test_parse_file_cancellation_marks_file_retryable(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.databases_meta = {"db": {"metadata": {}}}
    file_repo = FakeKnowledgeFileRepository(
        {"file-1": make_file_record(markdown_file=None, status=FileStatus.UPLOADED)}
    )
    patch_file_repository(monkeypatch, file_repo)

    parsing = asyncio.Event()

    async def cancelled_parse(*args, **kwargs):
        parsing.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("yuxi.services.ocr_service.parse_document", cancelled_parse)

    task = asyncio.create_task(kb.parse_file("db", "file-1", operator_id="user-1"))
    await asyncio.wait_for(parsing.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = file_repo.records["file-1"]
    assert record.status == FileStatus.ERROR_PARSING
    assert record.error_message == "File parsing was cancelled"


async def test_index_file_cancellation_marks_file_retryable(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.databases_meta = {"db": {"embedding_model_spec": "test-provider:test-embedding", "metadata": {}}}
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record()})
    patch_file_repository(monkeypatch, file_repo)

    async def get_collection(kb_id):
        return FakeCollection()

    reading = asyncio.Event()

    async def cancelled_read(path):
        reading.set()
        await asyncio.Event().wait()

    kb._get_milvus_collection = get_collection
    kb._get_embedding_function = lambda embedding_model_spec: None
    kb._read_markdown_from_minio = cancelled_read

    task = asyncio.create_task(kb.index_file("db", "file-1", operator_id="user-1", params={}))
    await asyncio.wait_for(reading.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = file_repo.records["file-1"]
    assert record.status == FileStatus.ERROR_INDEXING
    assert record.error_message == "File indexing was cancelled"


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
    file_repo = FakeKnowledgeFileRepository(
        {"file-1": make_file_record(chunk_count=2, token_count=10, status=FileStatus.INDEXED)}
    )
    patch_file_repository(monkeypatch, file_repo)
    kb = MilvusKB.__new__(MilvusKB)
    refreshed_kbs = []

    async def get_collection(kb_id):
        return None

    async def refresh_database_stats(kb_id):
        refreshed_kbs.append(kb_id)
        return {}

    kb._get_milvus_collection = get_collection
    kb.refresh_database_stats = refresh_database_stats

    await kb.delete_file_chunks_only("db", "file-1")

    assert repos[0].delete_calls == ["file-1"]
    assert file_repo.records["file-1"].chunk_count == 0
    assert file_repo.records["file-1"].token_count == 0
    assert file_repo.update_calls == [("file-1", "db", {"chunk_count": 0, "token_count": 0})]
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
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record(markdown_file=None, status=FileStatus.INDEXED)})
    patch_file_repository(monkeypatch, file_repo)
    collection = FakeCollection()
    refreshed_kbs = []
    deleted_files = []
    store_calls = []

    async def get_collection(kb_id):
        return collection

    async def forbidden_embedding(texts):
        raise AssertionError("update_content should not embed the whole file directly")

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
    kb.refresh_database_stats = refresh_database_stats
    kb._split_text_into_chunks = lambda text, file_id, filename, params: [make_chunk(0), make_chunk(1)]
    kb.delete_file_chunks_only = delete_file_chunks_only
    kb._embed_and_store_chunks = embed_and_store_chunks
    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.parse_document", parse_file)

    result = await kb.update_content("db", ["file-1"])

    assert deleted_files == [("db", "file-1")]
    assert len(store_calls) == 1
    assert store_calls[0][2] is collection
    assert [chunk["chunk_id"] for chunk in store_calls[0][3]] == ["chunk-0", "chunk-1"]
    assert store_calls[0][4] is forbidden_embedding
    assert result[0]["status"] == FileStatus.INDEXED
    assert file_repo.records["file-1"].status == FileStatus.INDEXED
    assert file_repo.update_calls[0][2]["status"] == FileStatus.INDEXING
    assert file_repo.update_calls[-1][2]["status"] == FileStatus.INDEXED
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
