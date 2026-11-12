import asyncio
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType

import yuxi.modules.knowledge.implementations.milvus as milvus_module
from test.unit.support.knowledge import make_knowledge_file_record
from test.unit.support.milvus import (
    EMBEDDING_MODEL_SPEC,
    FakeCollection,
    FakeHit,
    make_kb,
    make_query_config,
)
from test.unit.support.milvus import fake_milvus_embedding as fake_milvus_embedding
from yuxi.modules.knowledge.base import FileStatus, KnowledgeExecutor
from yuxi.modules.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.modules.knowledge.implementations.milvus import (
    CONTENT_SPARSE_FIELD,
    VECTOR_METRIC_TYPE,
    MilvusKB,
    _retrieval_config_options,
)
from yuxi.modules.knowledge.manager import KnowledgeBaseManager
from yuxi.modules.knowledge.services import documents
from yuxi.modules.knowledge.storage.milvus import CONTENT_ANALYZER_PARAMS


def test_milvus_retrieval_config_exposes_graph_and_dependencies(monkeypatch):
    monkeypatch.setattr(milvus_module.model_cache, "get_all_specs", lambda _type=None: [])
    options = _retrieval_config_options()
    by_key = {option["key"]: option for option in options}

    assert by_key["use_graph_retrieval"]["default"] is False
    assert by_key["graph_max_nodes"]["default"] == 10000
    assert by_key["graph_max_nodes"]["depend_on"] == ("use_graph_retrieval", True)
    assert by_key["graph_top_k"]["depend_on"] == ("use_graph_retrieval", True)
    assert by_key["reranker_model"]["depend_on"] == ("use_reranker", True)


def make_file_record(**overrides):
    values = {
        "path": "/tmp/demo.md",
        "markdown_file": "minio://parsed/db/file-1.md",
        "status": FileStatus.PARSED,
        "content_hash": None,
        "file_size": 0,
        "processing_params": {},
        "created_by": None,
    }
    values.update(overrides)
    return make_knowledge_file_record(**values)


class FakeKnowledgeFileRepository:
    def __init__(self, records: dict[str, types.SimpleNamespace]):
        self.records = records
        self.update_calls = []
        self.conditional_update_calls = []
        self.deleted = []

    async def get_by_file_id(self, file_id: str, *, include_deleted: bool = False):
        return self.records.get(file_id)

    async def update_fields_if_status(
        self,
        *,
        kb_id: str,
        file_id: str,
        allowed_statuses: set[str],
        data: dict,
        processing_params: dict | None = None,
        kb_additional_params: dict | None = None,
    ):
        """模拟已取得文件执行权后的参数合并，真实并发由 PG 测试验证。"""
        record = self.records.get(file_id)
        self.conditional_update_calls.append((kb_id, file_id, set(allowed_statuses), dict(data)))
        if record is None or record.kb_id != kb_id or record.status not in allowed_statuses:
            return None
        if kb_additional_params is not None:
            from yuxi.modules.knowledge.utils.kb_utils import resolve_processing_params

            record.processing_params = resolve_processing_params(kb_additional_params, record.processing_params, processing_params)
        for key, value in data.items():
            setattr(record, key, value)
        return record

    async def begin_index_generation(self, *, kb_id: str, file_id: str):
        record = self.records[file_id]
        record.generation += 1
        record.building_generation = record.generation
        record.projection_status = "building"
        return record.building_generation

    async def finish_index_generation(self, *, kb_id: str, file_id: str, generation: int, chunk_count, token_count):
        record = self.records[file_id]
        record.active_generation = generation
        record.generation = generation
        record.building_generation = None
        record.projection_status = "ready"
        record.status = FileStatus.INDEXED
        record.chunk_count = chunk_count
        record.token_count = token_count
        return record

    async def mark_deleted(self, *, kb_id: str, file_id: str):
        record = self.records[file_id]
        record.deleted_at = "deleted"
        record.status = "deleted"
        return f"file:{file_id}:g{record.generation}:deleted"

    async def update_fields(self, *, file_id: str, data: dict, kb_id: str | None = None):
        await asyncio.sleep(0)
        record = self.records.get(file_id)
        if record is None or (kb_id and record.kb_id != kb_id):
            return None
        for key, value in data.items():
            setattr(record, key, value)
        self.update_calls.append((file_id, kb_id, dict(data)))
        return record

    async def list_active_generations(self, kb_id):
        """提供实际 Repository 的可见代次接口。"""
        return [(file_id, row.active_generation) for file_id, row in self.records.items() if row.kb_id == kb_id and row.deleted_at is None]

    async def get_chunk_sources_by_file_ids(self, *, kb_id: str, file_ids: list[str]):
        return {
            file_id: {
                "source": record.filename,
                "chunk_count": record.chunk_count,
                "active_generation": record.active_generation,
            }
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
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def projection_lock(kb_id, *, shared=False):
        """unit 只隔离锁；并发排他性由真实 PG integration 拥有。"""
        yield

    monkeypatch.setattr("yuxi.modules.knowledge.repositories.projections.knowledge_projection_lock", projection_lock)
    monkeypatch.setattr("yuxi.modules.knowledge.repositories.files.KnowledgeFileRepository", lambda: file_repo)
    monkeypatch.setattr("yuxi.modules.knowledge.implementations.milvus.KnowledgeFileRepository", lambda: file_repo)


def patch_chunk_records(monkeypatch, records):
    """检索测试显式提供 PG 权威 chunk，而非复用向量正文。"""
    repository = types.SimpleNamespace(
        list_by_chunk_ids=AsyncMock(
            return_value=[
                types.SimpleNamespace(kb_id="db", generation=1, chunk_index=0, **{"start_line": None, "end_line": None, **record})
                for record in records
            ]
        )
    )
    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", lambda: repository)


def make_chunk(index: int, content: str = "content") -> dict:
    return {
        "chunk_id": f"chunk-{index}",
        "file_id": "file-1",
        "chunk_index": index,
        "content": content,
    }


async def test_cleanup_knowledge_base_resources_offloads_milvus_cleanup(monkeypatch):
    kb = MilvusKB.__new__(MilvusKB)
    kb.connection_alias = "test-alias"
    event_loop_thread = threading.get_ident()
    cleanup_threads = []
    calls = []

    def record_cleanup(name):
        cleanup_threads.append(threading.get_ident())
        calls.append(name)

    monkeypatch.setattr(
        "yuxi.modules.knowledge.implementations.milvus.utility.has_collection",
        lambda kb_id, using: record_cleanup("has_collection") or True,
    )
    monkeypatch.setattr(
        "yuxi.modules.knowledge.implementations.milvus.utility.drop_collection",
        lambda kb_id, using: record_cleanup("drop_collection"),
    )

    class FakeGraphVectorStore:
        def __init__(self):
            record_cleanup("graph_init")

        def drop_graph_collections(self, kb_id):
            record_cleanup("drop_graph_collections")

    monkeypatch.setattr(
        "yuxi.modules.knowledge.graphs.service.MilvusGraphVectorStore",
        FakeGraphVectorStore,
    )

    async def delete_base(self, kb_id):
        calls.append("delete_base")
        return {"message": "删除成功"}

    monkeypatch.setattr(KnowledgeExecutor, "cleanup_knowledge_base_resources", delete_base)

    from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService

    kb.graph_service = KnowledgeGraphService()
    result = await kb.cleanup_knowledge_base_resources("db")

    assert result == {"message": "删除成功"}
    assert calls == ["graph_init", "has_collection", "drop_collection", "drop_graph_collections", "delete_base"]
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

    async def delete_file_chunks_only(kb_id, file_id, generation=None):
        deleted_files.append((kb_id, file_id, generation))

    async def embed_and_store_chunks(kb_id, file_id, collection_arg, chunk_records, embedding_fn):
        store_calls.append((kb_id, file_id, collection_arg, list(chunk_records), embedding_fn))

    kb._get_or_create_milvus_collection = get_collection
    monkeypatch.setattr(documents, "read_markdown_from_minio", read_markdown)
    kb._split_text_into_chunks = lambda text, file_id, filename, params: chunks
    monkeypatch.setattr(milvus_module, "get_embedding_function", lambda embedding_model_spec: embedding_function)
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

    assert deleted_files == []
    assert len(store_calls) == 1
    assert [chunk["chunk_id"] for chunk in store_calls[0][3]] == ["chunk-0-g2", "chunk-1-g2"]
    assert result["status"] == FileStatus.INDEXED
    assert result["chunk_count"] == 2
    assert result["token_count"] == count_tokens("alpha beta") + count_tokens("中文")
    assert file_repo.records["file-1"].chunk_count == result["chunk_count"]
    assert file_repo.conditional_update_calls[0][3]["status"] == FileStatus.INDEXING
    assert result["active_generation"] == 2


async def test_deleted_file_index_removes_chunks_written_before_finish(monkeypatch):
    """删除发生在 chunk 写入之后、入库完成之前时，本次代次必须删掉，不能留给检索。"""

    kb = MilvusKB.__new__(MilvusKB)
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record()})
    patch_file_repository(monkeypatch, file_repo)
    deleted = []

    async def get_collection(kb_id, embedding_model_spec):
        del kb_id, embedding_model_spec
        return FakeCollection()

    async def read_markdown(_path):
        return "# demo"

    async def embed_and_store_chunks(*_args, **_kwargs):
        file_repo.records["file-1"].deleted_at = "deleted"

    async def finish_index_generation(**kwargs):
        if file_repo.records[kwargs["file_id"]].deleted_at is not None:
            raise ValueError(f"File {kwargs['file_id']} not found or deleted")
        raise AssertionError("deleted file must not finish indexing")

    async def delete_file_chunks_only(kb_id, file_id, generation=None, *, update_file_stats=True):
        deleted.append((kb_id, file_id, generation, update_file_stats))

    file_repo.finish_index_generation = finish_index_generation
    kb._get_or_create_milvus_collection = get_collection
    kb._embed_and_store_chunks = embed_and_store_chunks
    kb.delete_file_chunks_only = delete_file_chunks_only
    kb._split_text_into_chunks = lambda text, file_id, filename, params: [make_chunk(0)]
    monkeypatch.setattr(documents, "read_markdown_from_minio", read_markdown)
    monkeypatch.setattr(milvus_module, "get_embedding_function", lambda embedding_model_spec: None)

    async def get_system_options(_option, _db=None):
        return {"embed_model": EMBEDDING_MODEL_SPEC}

    monkeypatch.setattr(type(milvus_module.system_options), "get", get_system_options)

    with pytest.raises(ValueError, match="deleted"):
        await kb.index_file(
            "db",
            "file-1",
            operator_id="user-1",
            params={},
            embedding_model_spec=EMBEDDING_MODEL_SPEC,
            additional_params={},
        )

    assert deleted == [("db", "file-1", 2, False)]


async def test_index_file_rejects_folder_before_claim(monkeypatch):
    """文件夹 done 与文件 done 同字面量，但不能进入入库声明。"""
    kb = MilvusKB.__new__(MilvusKB)
    record = make_file_record(file_id="folder-1", is_folder=True, status="done")
    file_repo = FakeKnowledgeFileRepository({"folder-1": record})
    patch_file_repository(monkeypatch, file_repo)

    async def fail_collection(*_args, **_kwargs):
        raise AssertionError("文件夹不能创建外部集合")

    kb._get_or_create_milvus_collection = fail_collection

    with pytest.raises(ValueError, match="Cannot index folder"):
        await kb.index_file(
            "db",
            "folder-1",
            operator_id="user-1",
            params={},
            embedding_model_spec=EMBEDDING_MODEL_SPEC,
            additional_params={},
        )

    assert file_repo.conditional_update_calls == []
    assert record.status == "done"


async def test_index_without_markdown_does_not_claim(monkeypatch):
    """没有正文时不领取、不创建集合，状态保持原样。"""

    kb = MilvusKB.__new__(MilvusKB)
    record = make_file_record(status=FileStatus.PARSED, markdown_file=None)
    file_repo = FakeKnowledgeFileRepository({"file-1": record})
    patch_file_repository(monkeypatch, file_repo)

    async def fail_collection(*_args, **_kwargs):
        raise AssertionError("没有正文不能创建外部集合")

    kb._get_or_create_milvus_collection = fail_collection

    with pytest.raises(ValueError, match="no markdown_file"):
        await kb.index_file(
            "db",
            "file-1",
            operator_id="user-1",
            params={},
            embedding_model_spec=EMBEDDING_MODEL_SPEC,
            additional_params={},
        )

    assert file_repo.conditional_update_calls == []
    assert record.status == FileStatus.PARSED


@pytest.mark.parametrize("status", ["done", "failed"])
async def test_legacy_file_status_enters_index_claim(monkeypatch, status):
    """历史文件 done 可重新入库，failed 可重试；声明发生后才读取 Markdown。"""
    kb = MilvusKB.__new__(MilvusKB)
    record = make_file_record(status=status, markdown_file="minio://parsed/db/file-1.md")
    file_repo = FakeKnowledgeFileRepository({"file-1": record})
    patch_file_repository(monkeypatch, file_repo)

    async def get_collection(*_args, **_kwargs):
        return FakeCollection()

    async def read_markdown(_path):
        assert record.status == FileStatus.INDEXING
        raise RuntimeError("stop after claim")

    kb._get_or_create_milvus_collection = get_collection
    monkeypatch.setattr(documents, "read_markdown_from_minio", read_markdown)
    monkeypatch.setattr(milvus_module, "get_embedding_function", lambda _spec: lambda _texts: [])

    async def get_system_options(_option, _db=None):
        return {"embed_model": EMBEDDING_MODEL_SPEC}

    monkeypatch.setattr(type(milvus_module.system_options), "get", get_system_options)

    with pytest.raises(RuntimeError, match="stop after claim"):
        await kb.index_file(
            "db",
            "file-1",
            operator_id="user-1",
            params={},
            embedding_model_spec=EMBEDDING_MODEL_SPEC,
            additional_params={},
        )

    allowed_statuses, claim = file_repo.conditional_update_calls[0][2:]
    assert status in allowed_statuses
    assert {"done", "failed"} <= allowed_statuses
    assert claim["status"] == FileStatus.INDEXING


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
        file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record(markdown_file=None, status=FileStatus.UPLOADED)})
        patch_file_repository(monkeypatch, file_repo)
        started = asyncio.Event()

        async def cancelled_step(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr("yuxi.modules.documents.service.parse_to_hosted_markdown", cancelled_step)
        monkeypatch.setattr(kb, "_cleanup_parse_attempt", AsyncMock())
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
        monkeypatch.setattr(milvus_module, "get_embedding_function", lambda embedding_model_spec: None)
        monkeypatch.setattr(documents, "read_markdown_from_minio", cancelled_step)

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

        async def count_graph_indexed_by_file_id(self, file_id, *, generation=None):
            return 0

        async def delete_by_file_id(self, file_id):
            self.delete_calls.append(file_id)
            return 2

    monkeypatch.setattr("yuxi.modules.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record(chunk_count=2, token_count=10, status=FileStatus.INDEXED)})
    patch_file_repository(monkeypatch, file_repo)
    kb = MilvusKB.__new__(MilvusKB)

    async def get_collection(kb_id):
        del kb_id
        return None

    kb._get_existing_milvus_collection = get_collection
    kb.graph_service = SimpleNamespace(delete_file_graph=AsyncMock())

    await kb.delete_file_chunks_only("db", "file-1")
    kb.graph_service.delete_file_graph.assert_awaited_once_with("db", "file-1", generation=None)

    assert repos[0].delete_calls == ["file-1"]
    assert file_repo.records["file-1"].chunk_count == 0
    assert file_repo.records["file-1"].token_count == 0
    assert file_repo.update_calls == [("file-1", "db", {"chunk_count": 0, "token_count": 0})]


@pytest.mark.parametrize("failure", ["graph", "vector"])
async def test_delete_file_commits_invisibility_without_external_cleanup(monkeypatch, failure):
    """外部不可用不阻止 PG 删除；请求线程不能先调用外部清理。"""
    error = RuntimeError("external storage unavailable")
    chunk_repo = types.SimpleNamespace(
        delete_by_file_id=AsyncMock(),
    )
    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", lambda: chunk_repo)
    file_repo = FakeKnowledgeFileRepository({"file-1": make_file_record(chunk_count=2, token_count=10)})
    patch_file_repository(monkeypatch, file_repo)
    monkeypatch.setattr(
        "yuxi.modules.knowledge.graphs.service.KnowledgeGraphService.delete_file_graph",
        AsyncMock(side_effect=error),
    )
    kb = make_kb(FakeCollection())
    kb._get_existing_milvus_collection = AsyncMock(return_value=FakeCollection())
    kb._delete_file_chunks_from_milvus = AsyncMock(side_effect=error)

    manager = object.__new__(KnowledgeBaseManager)
    monkeypatch.setattr(manager, "_require_kb_supports_documents", AsyncMock())
    monkeypatch.setattr(manager, "_refresh_knowledge_base_stats", AsyncMock())
    await manager.delete_file("db", "file-1")

    assert file_repo.records["file-1"].status == "deleted"
    assert file_repo.records["file-1"].deleted_at is not None
    kb._delete_file_chunks_from_milvus.assert_not_awaited()
    assert "file-1" in file_repo.records
    assert file_repo.records["file-1"].chunk_count == 2
    assert file_repo.records["file-1"].token_count == 10
    chunk_repo.delete_by_file_id.assert_not_awaited()


async def test_knowledge_base_cleanup_stops_when_collection_drop_fails(monkeypatch):
    """集合删除失败时不能继续清理并报告知识库已删除。"""
    error = RuntimeError("Milvus unavailable")
    monkeypatch.setattr(milvus_module.utility, "has_collection", lambda *args, **kwargs: True)
    monkeypatch.setattr(milvus_module.utility, "drop_collection", Mock(side_effect=error))
    graph_cleanup = Mock()
    monkeypatch.setattr(
        "yuxi.modules.knowledge.graphs.milvus_graph_vector_store.MilvusGraphVectorStore",
        lambda: types.SimpleNamespace(drop_graph_collections=graph_cleanup),
    )
    base_cleanup = AsyncMock(return_value={"message": "success"})
    monkeypatch.setattr(KnowledgeExecutor, "cleanup_knowledge_base_resources", base_cleanup)
    kb = make_kb(FakeCollection())
    kb.connection_alias = "test-connection"

    with pytest.raises(RuntimeError) as caught:
        await kb.cleanup_knowledge_base_resources("db")

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

    monkeypatch.setattr("yuxi.modules.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
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
    assert collection.upsert_calls[0][0] == ["chunk-0", "chunk-1", "chunk-2"]
    assert collection.upsert_calls[0][5] == embeddings
    assert collection.insert_calls == []


@pytest.mark.parametrize(
    ("failure_mode", "expected_error", "expected_attempts"),
    [
        pytest.param("transient_milvus", None, 2, id="transient-milvus-retry"),
        pytest.param(
            "retry_exhaustion",
            "milvus boom",
            milvus_module.MILVUS_CHUNK_UPSERT_ATTEMPTS,
            id="milvus-retry-exhaustion",
        ),
        pytest.param("postgres", "pg boom", 0, id="postgres-write-failure"),
    ],
)
async def test_insert_chunks_to_stores_failure_paths_preserve_pg_facts(
    monkeypatch,
    failure_mode,
    expected_error,
    expected_attempts,
):
    """Milvus 重试成功或耗尽都不回滚 PG；PG 失败则不触碰 Milvus。"""
    monkeypatch.setattr(milvus_module, "MILVUS_CHUNK_UPSERT_RETRY_DELAY_SECONDS", 0)
    repositories = []

    class FakeChunkRepo:
        def __init__(self):
            self.upsert_calls = []
            self.delete_calls = []
            repositories.append(self)

        async def batch_upsert(self, chunks):
            self.upsert_calls.append(chunks)
            if failure_mode == "postgres":
                raise RuntimeError("pg boom")
            return []

        async def delete_by_file_id(self, file_id):
            self.delete_calls.append(file_id)
            return 0

    monkeypatch.setattr("yuxi.modules.knowledge.implementations.milvus.KnowledgeChunkRepository", FakeChunkRepo)
    kb = MilvusKB.__new__(MilvusKB)

    class RetryCollection(FakeCollection):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def upsert(self, entities):
            self.attempts += 1
            if failure_mode == "transient_milvus" and self.attempts == 1:
                raise RuntimeError("milvus transient")
            if failure_mode == "retry_exhaustion":
                raise RuntimeError("milvus boom")
            self.upsert_calls.append(entities)

    collection = RetryCollection()
    milvus_delete_calls = []

    async def delete_file_chunks_from_milvus(collection_arg, file_id):
        milvus_delete_calls.append((collection_arg, file_id))

    kb = MilvusKB.__new__(MilvusKB)
    kb._delete_file_chunks_from_milvus = delete_file_chunks_from_milvus
    chunks = [make_chunk(index) for index in range(2)]
    embeddings = [[0.1, 0.2] for _ in chunks]

    if expected_error is None:
        await kb._insert_chunks_to_stores("db", "file-1", collection, chunks, embeddings)
    else:
        with pytest.raises(RuntimeError, match=expected_error):
            await kb._insert_chunks_to_stores("db", "file-1", collection, chunks, embeddings)

    assert len(repositories) == 1
    assert len(repositories[0].upsert_calls) == 1
    persisted = repositories[0].upsert_calls[0]
    assert [(row["chunk_id"], row["file_id"], row["content"]) for row in persisted] == [
        (chunk["chunk_id"], chunk["file_id"], chunk["content"]) for chunk in chunks
    ]
    # 无论派生写入成功或失败，都不能撤销已写入的 PG 事实。
    assert repositories[0].delete_calls == []
    assert collection.attempts == expected_attempts
    if failure_mode == "transient_milvus":
        assert len(collection.upsert_calls) == 1
        assert collection.upsert_calls[0][5] == embeddings
    else:
        assert collection.upsert_calls == []
    assert milvus_delete_calls == []


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
@pytest.mark.parametrize("use_graph_retrieval", [False, True])
async def test_retrieval_limits_are_enforced_at_runtime(search_mode, use_graph_retrieval):
    """超限配置在向量、关键词和混合检索中限制召回及最终结果。"""
    collection = FakeCollection()
    hits = [FakeHit(f"chunk {index}", 0.8) for index in range(200)]
    collection.search = Mock(return_value=[hits])
    collection.hybrid_search = Mock(return_value=[hits])
    kb = make_kb(collection)
    kb._retrieve_graph_chunks = AsyncMock(return_value=[])

    chunks = await kb.aquery(
        "bounded query",
        "db",
        config=make_query_config(),
        search_mode=search_mode,
        use_graph_retrieval=use_graph_retrieval,
        recall_top_k=1_000_000,
        bm25_top_k=1_000_000,
        final_top_k=1_000_000,
        highlight_results=False,
    )

    assert [chunk["content"] for chunk in chunks] == [f"chunk {index}" for index in range(100)]
    recall_limit = 200 if use_graph_retrieval else 100
    if search_mode == "hybrid":
        request = collection.hybrid_search.call_args.kwargs
        assert request["limit"] == recall_limit
        assert [item.limit for item in request["reqs"]] == [recall_limit, 200]
    else:
        assert collection.search.call_args.kwargs["limit"] == (200 if search_mode == "keyword" else recall_limit)


@pytest.mark.parametrize("exceeds_limit", [False, True])
async def test_graph_retrieval_limits_reach_query_executors(monkeypatch, exceeds_limit):
    """图检索执行器接收公开上限，范围内配置保持原值。"""
    vector_store = types.SimpleNamespace(
        search_entities=AsyncMock(return_value=[{"id": "entity-1", "score": 1.0}]),
        search_triples=AsyncMock(return_value=[]),
    )
    graph_service = types.SimpleNamespace(query_and_rank_chunks_by_ppr=AsyncMock(return_value=[("chunk-1", 0.5)]))
    monkeypatch.setattr("yuxi.modules.knowledge.graphs.milvus_graph_vector_store.MilvusGraphVectorStore", lambda: vector_store)
    chunk = types.SimpleNamespace(chunk_id="chunk-1", file_id="file-1", chunk_index=0, content="graph result")
    repository = types.SimpleNamespace(list_by_chunk_ids=AsyncMock(return_value=[chunk]))
    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", lambda: repository)
    kb = make_kb(FakeCollection())
    kb.graph_service = graph_service
    graph_service.get_graph_vector_store = AsyncMock(return_value=vector_store)
    params = {
        "graph_entity_top_k": 1_000_000 if exceeds_limit else 7,
        "graph_triple_top_k": 1_000_000 if exceeds_limit else 8,
        "graph_top_k": 1_000_000 if exceeds_limit else 9,
        "graph_max_nodes": 1_000_000 if exceeds_limit else 1000,
    }

    chunks = await kb._retrieve_graph_chunks("query", "db", [], params, EMBEDDING_MODEL_SPEC)

    assert vector_store.search_entities.await_args.kwargs["top_k"] == (100 if exceeds_limit else 7)
    assert vector_store.search_triples.await_args.kwargs["top_k"] == (100 if exceeds_limit else 8)
    graph_query = graph_service.query_and_rank_chunks_by_ppr.await_args.kwargs
    assert graph_query["top_k"] == (200 if exceeds_limit else 9)
    assert graph_query["max_nodes"] == (50_000 if exceeds_limit else 1000)
    assert chunks[0]["content"] == "graph result"
    assert chunks[0]["metadata"]["chunk_id"] == "chunk-1"


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
    result = await make_kb(FakeCollection(distance=0.1)).aquery("query", "db", config=make_query_config(), similarity_threshold=0.2)

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


async def test_hybrid_mode_does_not_apply_cosine_threshold_to_fusion_score():
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

    assert chunks[0]["hybrid_score"] == 0.1


def test_query_params_config_uses_bm25_parameters(monkeypatch):
    monkeypatch.setattr(milvus_module.model_cache, "get_all_specs", lambda _type=None: [])
    kb = MilvusKB.__new__(MilvusKB)

    config = kb.get_query_params_config()

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


def test_collection_supports_text_retrieval_requires_match_and_bm25():
    kb = MilvusKB.__new__(MilvusKB)
    schema = CollectionSchema(
        fields=[
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
            FieldSchema(name="generation", dtype=DataType.INT64),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=65535,
                enable_analyzer=True,
                enable_match=True,
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

    assert kb._collection_supports_text_retrieval(collection)


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

    patch_chunk_records(
        monkeypatch,
        [
            {"chunk_id": "live", "file_id": "file-live", "content": "PG live content", "start_line": 4, "end_line": 8},
        ],
    )
    chunks = [
        {"metadata": {"file_id": "file-live", "chunk_id": "live", "chunk_index": 0}, "content": "stale", "score": 0.9},
        {"metadata": {"file_id": "file-deleted"}, "content": "orphan content", "score": 0.8},
    ]

    result = await kb._hydrate_chunk_sources("db", chunks)

    assert len(result) == 1
    assert result[0]["metadata"]["file_id"] == "file-live"
    assert result[0]["metadata"]["source"] == "live.md"
    assert result[0]["metadata"]["chunk_count"] == chunk_count
    assert result[0]["metadata"]["chunk_index"] == 0
    assert result[0]["content"] == "PG live content"
    assert result[0]["metadata"]["start_line"] == 4
    assert result[0]["metadata"]["end_line"] == 8


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

    patch_chunk_records(
        monkeypatch,
        [
            {"chunk_id": "a", "file_id": "file-a", "content": "PG a"},
            {"chunk_id": "b", "file_id": "file-b", "content": "PG b"},
        ],
    )
    chunks = [
        {"metadata": {"file_id": "file-a", "chunk_id": "a"}, "content": "a", "score": 0.9},
        {"metadata": {"file_id": "file-b", "chunk_id": "b"}, "content": "b", "score": 0.8},
    ]

    result = await kb._hydrate_chunk_sources("db", chunks)

    assert len(result) == 2
    assert result[0]["metadata"]["source"] == "a.md"
    assert result[1]["metadata"]["source"] == "b.md"


@pytest.mark.parametrize(
    "status, generation, content, reason",
    [
        ("indexed", 1, "原文", None),
        ("parsed", 1, "原文", "document_not_indexed"),
        ("indexed", 2, "原文", "index_changed"),
        ("indexed", 1, "", "content_unavailable"),
    ],
)
async def test_document_preview_location_requires_matching_index_snapshot(monkeypatch, status, generation, content, reason):
    """完整原文和 Chunk 代次不自洽时，预览不得沿用片段行号高亮。"""
    kb = MilvusKB.__new__(MilvusKB)
    chunk = types.SimpleNamespace(
        chunk_id="chunk",
        chunk_index=0,
        generation=generation,
        content="原文",
        start_line=1,
        end_line=1,
        start_char_pos=0,
        end_char_pos=2,
        start_token_pos=None,
        end_token_pos=None,
        graph_indexed=False,
        ent_ids=None,
        tags=None,
        extraction_result=None,
    )
    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.chunks.KnowledgeChunkRepository",
        lambda: types.SimpleNamespace(
            list_by_file_id=AsyncMock(return_value=[chunk]),
        ),
    )
    monkeypatch.setattr(documents, "read_markdown_from_minio", AsyncMock(return_value=content))
    result = await documents.get_file_content_from_meta(
        "file",
        {
            "status": status,
            "active_generation": 1,
            "markdown_file": "minio://parsed/document.md",
        },
    )
    assert result["line_location"] == {"available": reason is None, "reason": reason}
    assert result["content"] == content


@pytest.mark.parametrize("visible_chunks", [0, 1])
async def test_query_does_not_create_missing_vector_projection(monkeypatch, tmp_path, visible_chunks):
    """读请求不创建集合；丢失 active 投影时显式失败。"""
    artifact = tmp_path / "collection"
    kb = make_kb(FakeCollection())
    kb._get_existing_milvus_collection = AsyncMock(return_value=None)

    async def create(*args):
        """旧读路径会留下新的外部集合。"""
        artifact.write_text("created by query")
        return FakeCollection()

    kb._get_or_create_milvus_collection = create
    monkeypatch.setattr(
        "yuxi.modules.knowledge.implementations.milvus.KnowledgeChunkRepository",
        lambda: SimpleNamespace(
            count_by_kb_id=AsyncMock(return_value=visible_chunks),
        ),
    )
    if visible_chunks:
        with pytest.raises(ValueError, match="no vector projection"):
            await kb.aquery("query", "db", config=make_query_config())
    else:
        assert await kb.aquery("query", "db", config=make_query_config()) == []
    assert not artifact.exists()


async def test_query_filters_orphaned_chunks_from_search_results(monkeypatch):
    """端到端：Milvus 返回孤儿向量时，aquery 最终结果不包含已删除文件的内容。"""
    file_repo = FakeKnowledgeFileRepository(
        {
            "file-live": make_file_record(file_id="file-live", filename="live.md"),
        }
    )
    patch_file_repository(monkeypatch, file_repo)
    patch_chunk_records(
        monkeypatch,
        [
            {"chunk_id": "chunk-1", "file_id": "file-live", "content": "live content"},
        ],
    )

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

    async def get_collection(kb_id: str):
        del kb_id
        return OrphanCollection()

    kb._get_existing_milvus_collection = get_collection

    chunks = await kb.aquery("query", "db", config=make_query_config())

    assert len(chunks) == 1
    assert chunks[0]["content"] == "live content"


async def test_cancelled_old_attempt_cleanup_preserves_published_result(monkeypatch):
    """PG 发布成功后取消旧资源回收，不回滚当前解析产物。"""
    kb = MilvusKB.__new__(MilvusKB)
    previous_url = "http://minio:9000/knowledgebases/db/parsed/file-1/old-attempt/document.md"
    current_url = "http://minio:9000/knowledgebases/db/parsed/file-1/new-attempt/document.md"
    repository = FakeKnowledgeFileRepository({"file-1": make_file_record(status=FileStatus.UPLOADED, markdown_file=previous_url)})
    patch_file_repository(monkeypatch, repository)
    monkeypatch.setattr("yuxi.modules.documents.service.parse_to_hosted_markdown", AsyncMock(return_value="text"))
    monkeypatch.setattr(kb, "save_markdown_artifact", AsyncMock(return_value=current_url))
    cleanup = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(kb, "_cleanup_parse_attempt", cleanup)
    with pytest.raises(asyncio.CancelledError):
        await kb.parse_file("db", "file-1", additional_params={})
    assert repository.records["file-1"].status == FileStatus.PARSED
    assert repository.records["file-1"].markdown_file == current_url
    assert [call.args[2] for call in cleanup.await_args_list] == ["old-attempt"]


def test_rrf_fusion_merges_chunk_and_graph_rankings():
    kb = object.__new__(MilvusKB)
    base_chunks = [
        {"content": "base a", "metadata": {"chunk_id": "a"}, "score": 0.9},
        {"content": "base b", "metadata": {"chunk_id": "b"}, "score": 0.8},
    ]
    graph_chunks = [
        {"content": "graph b", "metadata": {"chunk_id": "b"}, "score": 0.7, "graph_score": 0.7},
        {"content": "graph c", "metadata": {"chunk_id": "c"}, "score": 0.6, "graph_score": 0.6},
    ]

    fused = kb._fuse_chunk_rankings(base_chunks, graph_chunks, graph_weight=1.0)

    assert [chunk["metadata"]["chunk_id"] for chunk in fused] == ["b", "a", "c"]
    assert fused[0]["graph_score"] == 0.7
    assert fused[0]["fusion_sources"] == ["chunk", "graph"]


async def test_collection_load_failure_is_observable():
    """加载失败不得被当作可查询的集合。"""

    def fail_load():
        raise RuntimeError("load unavailable")

    with pytest.raises(RuntimeError, match="load unavailable"):
        await object.__new__(MilvusKB)._initialize_kb_instance(SimpleNamespace(load=fail_load))


@pytest.mark.parametrize("mismatch", ["model", "dimension", "missing_embedding", "text_schema"])
def test_incompatible_collection_is_rejected_without_deletion(monkeypatch, mismatch):
    """不匹配的集合显式失败，查询入口不能删除已存在的数据。"""
    collection = SimpleNamespace(
        description="Knowledge base collection for db using model",
        schema=SimpleNamespace(fields=[SimpleNamespace(name="embedding", params={"dim": 4})]),
    )
    if mismatch == "model":
        collection.description = "different model"
    elif mismatch == "dimension":
        collection.schema.fields[0].params["dim"] = 8
    elif mismatch == "missing_embedding":
        collection.schema.fields = []
    monkeypatch.setattr(milvus_module.utility, "has_collection", lambda *args, **kwargs: True)
    monkeypatch.setattr(milvus_module, "Collection", lambda **kwargs: collection)
    monkeypatch.setattr(
        milvus_module.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(
            model_type="embedding",
            model_id="model",
            dimension=4,
        ),
    )

    def reject_drop(*args, **kwargs):
        pytest.fail("collection was deleted")

    monkeypatch.setattr(milvus_module.utility, "drop_collection", reject_drop)
    kb = object.__new__(MilvusKB)
    kb.connection_alias = "own"
    kb._collection_supports_text_retrieval = lambda candidate: mismatch != "text_schema"
    kb._create_new_collection = reject_drop
    with pytest.raises(RuntimeError, match="does not match"):
        kb._create_kb_instance_sync("db", "provider:model")


async def test_hybrid_highlighting_preserves_ranking_and_scores():
    """补取高亮只能附加片段，不能用第二次搜索的分数覆盖融合结果。"""
    fusion_hits = [FakeHit("first", 0.7), FakeHit("second", 0.5)]
    highlighted_hits = [FakeHit("second", 1), FakeHit("first", 1)]
    for hit, chunk_id in zip(fusion_hits, ["a", "b"]):
        hit.entity["chunk_id"] = chunk_id
    for hit, chunk_id in zip(highlighted_hits, ["b", "a"]):
        hit.entity["chunk_id"] = chunk_id
        hit.highlight = {"content": {"fragments": [f"\ue000{hit.entity.get('content')}\ue001"], "scores": []}}
    collection = FakeCollection()
    collection.search = lambda **kwargs: [highlighted_hits]
    collection.hybrid_search = lambda **kwargs: [fusion_hits]

    chunks = await make_kb(collection).aquery("query", "db", config=make_query_config(), search_mode="hybrid")

    assert [chunk["metadata"]["chunk_id"] for chunk in chunks] == ["a", "b"]
    assert [chunk["score"] for chunk in chunks] == [0.7, 0.5]
    assert all(chunk["score_type"] == "hybrid" for chunk in chunks)
    assert chunks[0]["highlights"] == [[{"text": "first", "matched": True}]]
