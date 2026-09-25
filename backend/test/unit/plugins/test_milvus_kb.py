import asyncio
import threading
import types
from unittest.mock import AsyncMock, Mock

import pytest
from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType

import yuxi.knowledge.implementations.milvus as milvus_module
from yuxi.knowledge.base import FileStatus, KnowledgeBase
from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.knowledge.implementations.milvus import (
    CONTENT_ANALYZER_PARAMS,
    CONTENT_SPARSE_FIELD,
    VECTOR_METRIC_TYPE,
    MilvusKB,
)
from yuxi.knowledge.read_models import KnowledgeBaseConfig

EMBEDDING_MODEL_SPEC = "test-provider:test-embedding"


def make_query_config() -> KnowledgeBaseConfig:
    return KnowledgeBaseConfig(
        kb_id="db",
        kb_type="milvus",
        embedding_model_spec=EMBEDDING_MODEL_SPEC,
    )


class FakeHit:
    def __init__(self, content: str, distance: float, file_id: str = "file-1"):
        self.distance = distance
        self.entity = {
            "content": content,
            "chunk_id": "chunk-1",
            "file_id": file_id,
            "chunk_index": 0,
        }


class FakeCollection:
    def __init__(self, distance: float = 0.8):
        self.search_calls = []
        self.hybrid_calls = []
        self.insert_calls = []
        self.upsert_calls = []
        self.distance = distance

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return [[FakeHit("BM25 result", self.distance)]]

    def hybrid_search(self, **kwargs):
        self.hybrid_calls.append(kwargs)
        return [[FakeHit("Hybrid result", self.distance)]]

    def insert(self, entities):
        self.insert_calls.append(entities)

    def upsert(self, entities):
        self.upsert_calls.append(entities)


def make_kb(collection: FakeCollection) -> MilvusKB:
    kb = MilvusKB.__new__(MilvusKB)
    kb._get_embedding_function = lambda embedding_model_spec, **kwargs: lambda texts: [[0.1, 0.2] for _ in texts]

    async def get_collection(kb_id: str, embedding_model_spec: str | None):
        del kb_id, embedding_model_spec
        return collection

    async def hydrate_chunk_sources(kb_id: str, chunks: list[dict]) -> list[dict]:
        for chunk in chunks:
            chunk["metadata"]["source"] = "demo.md"
        return chunks

    kb._get_or_create_milvus_collection = get_collection
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

    async def get_chunk_sources_by_file_ids(self, *, kb_id: str, file_ids: list[str]):
        return {
            file_id: {"source": record.filename, "chunk_count": record.chunk_count}
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


async def test_cleanup_database_resources_offloads_milvus_cleanup(monkeypatch):
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

    monkeypatch.setattr(KnowledgeBase, "cleanup_database_resources", delete_base)

    result = await kb.cleanup_database_resources("db")

    assert result == {"message": "删除成功"}
    assert calls == ["has_collection", "drop_collection", "graph_init", "drop_graph_collections", "delete_base"]
    assert cleanup_threads
    assert all(thread_id != event_loop_thread for thread_id in cleanup_threads)


async def test_detect_data_inconsistencies_stays_in_milvus_executor(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "test-alias"

    class FakeCollection:
        def __init__(self, name: str, using: str):
            assert using == "test-alias"
            self.num_entities = 4 if name == "kb_managed" else 2
            self.description = f"collection:{name}"

    class FakeRepository:
        async def get_kb_file_stats(self, kb_id: str):
            assert kb_id == "kb_managed"
            return {"file_count": 0}

    monkeypatch.setattr(milvus_module.utility, "list_collections", lambda using: ["kb_managed", "kb_orphan"])
    monkeypatch.setattr(milvus_module.utility, "has_collection", lambda name, using: name == "kb_managed")
    monkeypatch.setattr(milvus_module, "Collection", FakeCollection)
    monkeypatch.setattr(milvus_module, "KnowledgeFileRepository", FakeRepository)

    result = await kb.detect_data_inconsistencies({"kb_managed"}, {"kb_managed"})

    assert [item["collection_name"] for item in result["missing_collections"]] == ["kb_orphan"]
    assert result["missing_files"] == [
        {
            "kb_id": "kb_managed",
            "vector_count": 4,
            "metadata_files_count": 0,
            "detected_at": result["missing_files"][0]["detected_at"],
        }
    ]


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
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record()})
    patch_file_repository(monkeypatch, file_repo)
    collection = FakeCollection()
    deleted_files = []
    store_calls = []
    chunks = [make_chunk(0, content="alpha beta"), make_chunk(1, content="中文")]

    async def get_collection(kb_id, embedding_model_spec):
        del kb_id, embedding_model_spec
        return collection

    async def read_markdown(path):
        return "# demo"

    async def embedding_function(texts):
        return [[0.1, 0.2] for _ in texts]

    async def delete_file_chunks_only(kb_id, file_id):
        deleted_files.append((kb_id, file_id))

    async def embed_and_store_chunks(kb_id, file_id, collection_arg, chunk_records, embedding_fn):
        store_calls.append((kb_id, file_id, collection_arg, list(chunk_records), embedding_fn))

    kb._get_or_create_milvus_collection = get_collection
    kb._read_markdown_from_minio = read_markdown
    kb._split_text_into_chunks = lambda text, file_id, filename, params: chunks
    kb._get_embedding_function = lambda embedding_model_spec: embedding_function
    kb.delete_file_chunks_only = delete_file_chunks_only
    kb._embed_and_store_chunks = embed_and_store_chunks

    async def get_system_options(_option, _db=None):
        return {"embed_model": EMBEDDING_MODEL_SPEC}

    monkeypatch.setattr(type(milvus_module.system_options), "get", get_system_options)

    result = await kb.index_file(
        "db",
        "file-1",
        operator_id="user-1",
        params={},
        embedding_model_spec=EMBEDDING_MODEL_SPEC,
        additional_params={},
    )

    assert deleted_files == [("db", "file-1")]
    assert len(store_calls) == 1
    assert [chunk["chunk_id"] for chunk in store_calls[0][3]] == ["chunk-0", "chunk-1"]
    assert result["status"] == FileStatus.INDEXED
    assert result["chunk_count"] == 2
    assert result["token_count"] == count_tokens("alpha beta") + count_tokens("中文")
    assert file_repo.records["file-1"].chunk_count == result["chunk_count"]
    assert file_repo.conditional_update_calls[0][3]["status"] == FileStatus.INDEXING
    assert file_repo.conditional_update_calls[-1][3]["status"] == FileStatus.INDEXED


@pytest.mark.parametrize(
    ("operation", "expected_status", "expected_message"),
    [
        ("parse", FileStatus.ERROR_PARSING, "File parsing was cancelled"),
        ("index", FileStatus.ERROR_INDEXING, "File indexing was cancelled"),
    ],
)
async def test_cancellation_marks_file_retryable(monkeypatch, operation, expected_status, expected_message):
    kb = MilvusKB.__new__(MilvusKB)
    if operation == "parse":
        file_repo = FakeKnowledgeFileRepository(
            {"file-1": make_file_record(markdown_file=None, status=FileStatus.UPLOADED)}
        )
        patch_file_repository(monkeypatch, file_repo)
        started = asyncio.Event()

        async def cancelled_step(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr("yuxi.services.ocr_service.parse_document", cancelled_step)
        task = asyncio.create_task(
            kb.parse_file(
                "db",
                "file-1",
                operator_id="user-1",
                additional_params={},
            )
        )
    else:
        file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record()})
        patch_file_repository(monkeypatch, file_repo)
        started = asyncio.Event()

        async def get_collection(kb_id, embedding_model_spec):
            del kb_id, embedding_model_spec
            return FakeCollection()

        async def cancelled_step(path):
            started.set()
            await asyncio.Event().wait()

        kb._get_or_create_milvus_collection = get_collection
        kb._get_embedding_function = lambda embedding_model_spec: None
        kb._read_markdown_from_minio = cancelled_step

        async def get_system_options(_option, _db=None):
            return {"embed_model": EMBEDDING_MODEL_SPEC}

        monkeypatch.setattr(type(milvus_module.system_options), "get", get_system_options)
        task = asyncio.create_task(
            kb.index_file(
                "db",
                "file-1",
                operator_id="user-1",
                params={},
                embedding_model_spec=EMBEDDING_MODEL_SPEC,
                additional_params={},
            )
        )

    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    record = file_repo.records["file-1"]
    assert record.status == expected_status
    assert record.error_message == expected_message


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

    async def get_collection(kb_id):
        del kb_id
        return None

    kb._get_existing_milvus_collection = get_collection

    await kb.delete_file_chunks_only("db", "file-1")

    assert repos[0].delete_calls == ["file-1"]
    assert file_repo.records["file-1"].chunk_count == 0
    assert file_repo.records["file-1"].token_count == 0
    assert file_repo.update_calls == [("file-1", "db", {"chunk_count": 0, "token_count": 0})]


@pytest.mark.parametrize("failure", ["graph", "vector"])
async def test_delete_file_keeps_metadata_when_external_deletion_fails(monkeypatch, failure):
    """外部删除失败必须显式失败，并保留文件与 chunk 的重试依据。"""
    error = RuntimeError("external storage unavailable")
    chunk_repo = types.SimpleNamespace(
        count_graph_indexed_by_file_id=AsyncMock(return_value=1 if failure == "graph" else 0),
        delete_by_file_id=AsyncMock(),
    )
    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", lambda: chunk_repo)
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record(chunk_count=2, token_count=10)})
    patch_file_repository(monkeypatch, file_repo)
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_service.MilvusGraphService.delete_file_graph", AsyncMock(side_effect=error)
    )
    kb = make_kb(FakeCollection())
    kb._get_existing_milvus_collection = AsyncMock(return_value=FakeCollection())
    kb._delete_file_chunks_from_milvus = AsyncMock(side_effect=error)

    with pytest.raises(RuntimeError) as caught:
        await kb.delete_file("db", "file-1")

    assert caught.value is error
    assert "file-1" in file_repo.records
    assert file_repo.records["file-1"].chunk_count == 2
    assert file_repo.records["file-1"].token_count == 10
    chunk_repo.delete_by_file_id.assert_not_awaited()


async def test_database_cleanup_stops_when_collection_drop_fails(monkeypatch):
    """集合删除失败时不能继续清理并报告知识库已删除。"""
    error = RuntimeError("Milvus unavailable")
    monkeypatch.setattr(milvus_module.utility, "has_collection", lambda *args, **kwargs: True)
    monkeypatch.setattr(milvus_module.utility, "drop_collection", Mock(side_effect=error))
    graph_cleanup = Mock()
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.milvus_graph_vector_store.MilvusGraphVectorStore",
        lambda: types.SimpleNamespace(drop_graph_collections=graph_cleanup),
    )
    base_cleanup = AsyncMock(return_value={"message": "success"})
    monkeypatch.setattr(KnowledgeBase, "cleanup_database_resources", base_cleanup)
    kb = make_kb(FakeCollection())
    kb.connection_alias = "test-connection"

    with pytest.raises(RuntimeError) as caught:
        await kb.cleanup_database_resources("db")

    assert caught.value is error
    graph_cleanup.assert_not_called()
    base_cleanup.assert_not_awaited()


async def test_collection_lifecycle_calls_are_offloaded_from_event_loop(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.collections = {}
    kb.connection_alias = "test-alias"
    event_loop_thread = threading.get_ident()
    call_threads: list[int] = []
    collection = object()

    def create_collection(_kb_id, _embedding_model_spec):
        call_threads.append(threading.get_ident())
        return collection

    class LoadableCollection:
        def load(self):
            call_threads.append(threading.get_ident())

    def has_collection(*_args, **_kwargs):
        call_threads.append(threading.get_ident())
        return False

    monkeypatch.setattr(kb, "_create_kb_instance_sync", create_collection)
    monkeypatch.setattr(milvus_module.utility, "has_collection", has_collection)

    assert await kb._create_kb_instance("db", EMBEDDING_MODEL_SPEC) is collection
    await kb._initialize_kb_instance(LoadableCollection())
    assert await kb._get_existing_milvus_collection("db") is None

    assert len(call_threads) == 3
    assert all(thread_id != event_loop_thread for thread_id in call_threads)


async def test_milvus_chunk_delete_is_offloaded_from_event_loop():
    kb = MilvusKB.__new__(MilvusKB)
    event_loop_thread = threading.get_ident()
    call_threads: list[int] = []

    class FakeCollection:
        def query(self, **_kwargs):
            call_threads.append(threading.get_ident())
            return [{"id": "chunk-1"}]

        def delete(self, _expr):
            call_threads.append(threading.get_ident())

    await kb._delete_file_chunks_from_milvus(FakeCollection(), "file-1")

    assert len(call_threads) == 2
    assert all(thread_id != event_loop_thread for thread_id in call_threads)


async def test_insert_chunks_to_stores_writes_pg_first_then_milvus_upsert(monkeypatch):
    event_log = []

    class FakeChunkRepo:
        async def batch_upsert(self, chunks):
            event_log.append("pg")
            return []

    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
    kb = MilvusKB.__new__(MilvusKB)
    collection = FakeCollection()

    original_upsert = collection.upsert

    def upsert(entities):
        event_log.append("milvus")
        original_upsert(entities)

    collection.upsert = upsert
    chunks = [make_chunk(index) for index in range(3)]
    embeddings = [[0.1, 0.2] for _ in chunks]

    await kb._insert_chunks_to_stores("db", "file-1", collection, chunks, embeddings)

    # PG 为权威源先行落库，Milvus 只做派生索引
    assert event_log == ["pg", "milvus"]
    assert len(collection.upsert_calls) == 1
    assert collection.upsert_calls[0][0] == ["id-0", "id-1", "id-2"]
    assert collection.upsert_calls[0][5] == embeddings
    assert collection.insert_calls == []


async def test_insert_chunks_to_stores_retries_milvus_upsert_and_keeps_pg_facts(monkeypatch):
    monkeypatch.setattr(milvus_module, "MILVUS_CHUNK_UPSERT_RETRY_DELAY_SECONDS", 0)
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

    class FlakyCollection(FakeCollection):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def upsert(self, entities):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("milvus transient")
            self.upsert_calls.append(entities)

    collection = FlakyCollection()
    chunks = [make_chunk(index) for index in range(2)]
    embeddings = [[0.1, 0.2] for _ in chunks]

    await kb._insert_chunks_to_stores("db", "file-1", collection, chunks, embeddings)

    # 重试成功即收敛：PG 事实保留，不触发回滚
    assert collection.attempts == 2
    assert len(collection.upsert_calls) == 1
    assert repos[0].delete_calls == []


async def test_insert_chunks_to_stores_raises_after_retry_exhaustion_without_rollback(monkeypatch):
    monkeypatch.setattr(milvus_module, "MILVUS_CHUNK_UPSERT_RETRY_DELAY_SECONDS", 0)
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

    class FailingCollection(FakeCollection):
        def upsert(self, entities):
            raise RuntimeError("milvus boom")

    collection = FailingCollection()
    milvus_delete_calls = []

    async def delete_file_chunks_from_milvus(collection_arg, file_id):
        milvus_delete_calls.append((collection_arg, file_id))

    kb._delete_file_chunks_from_milvus = delete_file_chunks_from_milvus
    chunks = [make_chunk(index) for index in range(2)]
    embeddings = [[0.1, 0.2] for _ in chunks]

    with pytest.raises(RuntimeError, match="milvus boom"):
        await kb._insert_chunks_to_stores("db", "file-1", collection, chunks, embeddings)

    # 重试耗尽后抛错且不回滚：PG 事实保留，等待重新索引幂等收敛
    assert repos[0].delete_calls == []
    assert milvus_delete_calls == []


async def test_insert_chunks_to_stores_propagates_pg_failure_without_milvus_write(monkeypatch):
    class FakeChunkRepo:
        async def batch_upsert(self, chunks):
            raise RuntimeError("pg boom")

    monkeypatch.setattr("yuxi.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
    kb = MilvusKB.__new__(MilvusKB)
    collection = FakeCollection()
    chunks = [make_chunk(index) for index in range(2)]
    embeddings = [[0.1, 0.2] for _ in chunks]

    with pytest.raises(RuntimeError, match="pg boom"):
        await kb._insert_chunks_to_stores("db", "file-1", collection, chunks, embeddings)

    # PG 失败时 Milvus 不应被写入
    assert collection.upsert_calls == []


async def test_keyword_mode_uses_milvus_bm25_search():
    collection = FakeCollection()
    kb = make_kb(collection)
    config = make_query_config()

    chunks = await kb.aquery(
        "alpha beta",
        "db",
        config=config,
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


@pytest.mark.parametrize("search_mode", ["vector", "keyword", "hybrid"])
async def test_query_failure_is_not_an_empty_result(search_mode):
    """主检索失败必须传给调用方，不能伪装成没有命中。"""
    error = RuntimeError("Milvus unavailable")

    class FailingCollection(FakeCollection):
        """返回确定性的检索故障。"""

        def search(self, **kwargs):
            """拒绝单路检索。"""
            raise error

        def hybrid_search(self, **kwargs):
            """拒绝混合检索。"""
            raise error

    with pytest.raises(RuntimeError) as caught:
        await make_kb(FailingCollection()).aquery("query", "db", config=make_query_config(), search_mode=search_mode)

    assert caught.value is error


async def test_query_without_matches_still_returns_empty_list():
    """成功检索但低于阈值仍属于正常空结果。"""
    result = await make_kb(FakeCollection(distance=0.1)).aquery("query", "db", config=make_query_config())

    assert result == []


async def test_vector_mode_ignores_metric_type_override():
    collection = FakeCollection()
    kb = make_kb(collection)
    config = make_query_config()

    chunks = await kb.aquery(
        "vector query",
        "db",
        config=config,
        search_mode="vector",
        metric_type="L2",
    )

    assert chunks[0]["content"] == "BM25 result"
    search_call = collection.search_calls[0]
    assert search_call["anns_field"] == "embedding"
    assert search_call["param"]["metric_type"] == VECTOR_METRIC_TYPE


async def test_hybrid_mode_uses_milvus_native_hybrid_search():
    collection = FakeCollection()
    kb = make_kb(collection)
    config = make_query_config()

    chunks = await kb.aquery(
        "hybrid query",
        "db",
        config=config,
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
    config = make_query_config()

    chunks = await kb.aquery(
        "hybrid query",
        "db",
        config=config,
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


@pytest.mark.parametrize("chunk_count", [0, 1, 4])
async def test_hydrate_chunk_sources_filters_orphaned_file_chunks(monkeypatch, chunk_count):
    """已从 PG 删除的文件（孤儿向量）不能出现在检索结果中。"""
    file_repo = FakeKnowledgeFileRepository(
        {
            "file-live": make_file_record(file_id="file-live", filename="live.md", chunk_count=chunk_count),
        }
    )
    patch_file_repository(monkeypatch, file_repo)
    kb = MilvusKB.__new__(MilvusKB)

    chunks = [
        {"metadata": {"file_id": "file-live", "chunk_index": 0}, "content": "live content", "score": 0.9},
        {"metadata": {"file_id": "file-deleted"}, "content": "orphan content", "score": 0.8},
    ]

    result = await kb._hydrate_chunk_sources("db", chunks)

    assert len(result) == 1
    assert result[0]["metadata"]["file_id"] == "file-live"
    assert result[0]["metadata"]["source"] == "live.md"
    assert result[0]["metadata"]["chunk_count"] == chunk_count
    assert result[0]["metadata"]["chunk_index"] == 0


async def test_hydrate_chunk_sources_returns_all_chunks_when_no_orphans(monkeypatch):
    """所有 file_id 都在 PG 中时行为不变，只补充 source 字段。"""
    file_repo = FakeKnowledgeFileRepository(
        {
            "file-a": make_file_record(file_id="file-a", filename="a.md"),
            "file-b": make_file_record(file_id="file-b", filename="b.md"),
        }
    )
    patch_file_repository(monkeypatch, file_repo)
    kb = MilvusKB.__new__(MilvusKB)

    chunks = [
        {"metadata": {"file_id": "file-a"}, "content": "a", "score": 0.9},
        {"metadata": {"file_id": "file-b"}, "content": "b", "score": 0.8},
    ]

    result = await kb._hydrate_chunk_sources("db", chunks)

    assert len(result) == 2
    assert result[0]["metadata"]["source"] == "a.md"
    assert result[1]["metadata"]["source"] == "b.md"


async def test_query_filters_orphaned_chunks_from_search_results(monkeypatch):
    """端到端：Milvus 返回孤儿向量时，aquery 最终结果不包含已删除文件的内容。"""
    file_repo = FakeKnowledgeFileRepository(
        {
            "file-live": make_file_record(file_id="file-live", filename="live.md"),
        }
    )
    patch_file_repository(monkeypatch, file_repo)

    class OrphanCollection(FakeCollection):
        """模拟 Milvus 中残留已删除文件的向量。"""

        def search(self, **kwargs):
            self.search_calls.append(kwargs)
            return [
                [
                    FakeHit("live content", 0.9, file_id="file-live"),
                    FakeHit("orphan content", 0.85, file_id="file-deleted"),
                ]
            ]

    kb = MilvusKB.__new__(MilvusKB)
    kb._get_embedding_function = lambda embedding_model_spec, **kwargs: lambda texts: [[0.1, 0.2] for _ in texts]

    async def get_collection(kb_id: str, embedding_model_spec: str | None):
        del kb_id, embedding_model_spec
        return OrphanCollection()

    kb._get_or_create_milvus_collection = get_collection

    chunks = await kb.aquery("query", "db", config=make_query_config())

    assert len(chunks) == 1
    assert chunks[0]["content"] == "live content"
