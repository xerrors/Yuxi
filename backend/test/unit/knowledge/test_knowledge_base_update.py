import types

import pytest

from yuxi.knowledge.base import KnowledgeBase
from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.knowledge.manager import KnowledgeBaseManager
from yuxi.knowledge.read_models import KnowledgeBaseDetail


class FakeKnowledgeBase(KnowledgeBase):
    @property
    def kb_type(self) -> str:
        return "fake"

    async def _create_kb_instance(self, slug: str, embedding_model_spec: str | None):
        return None

    async def _initialize_kb_instance(self, instance) -> None:
        pass

    async def index_file(self, slug: str, file_id: str, operator_id: str | None = None) -> dict:
        return {}

    async def update_content(self, slug: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        return []

    async def aquery(self, query_text: str, slug: str, **kwargs) -> list[dict]:
        return []

    def get_query_params_config(self, slug: str, **kwargs) -> dict:
        return {"options": []}

    async def delete_file(self, slug: str, file_id: str) -> None:
        pass

    async def get_file_basic_info(self, slug: str, file_id: str) -> dict:
        return {}

    async def get_file_content(self, slug: str, file_id: str) -> dict:
        return {}

    async def get_file_info(self, slug: str, file_id: str) -> dict:
        return {}


def make_kb(tmp_path):
    return FakeKnowledgeBase(str(tmp_path))


class FakeKnowledgeBaseRepository:
    def __init__(self, additional_params: dict | None = None):
        self.row = types.SimpleNamespace(additional_params=additional_params or {})
        self.update_calls = []

    async def get_by_kb_id(self, kb_id: str):
        return self.row if kb_id == "db" else None

    async def update_stats(self, kb_id: str, stats: dict[str, int]):
        assert kb_id == "db"
        self.update_calls.append({"stats": stats})
        self.row.additional_params = {**self.row.additional_params, "stats": stats}
        return self.row


def make_file_record(file_id: str, meta: dict):
    return types.SimpleNamespace(
        file_id=file_id,
        kb_id=meta.get("kb_id"),
        parent_id=meta.get("parent_id"),
        filename=meta.get("filename", ""),
        file_type=meta.get("file_type"),
        path=meta.get("path"),
        minio_url=meta.get("minio_url"),
        markdown_file=meta.get("markdown_file"),
        status=meta.get("status"),
        content_hash=meta.get("content_hash"),
        file_size=meta.get("size", meta.get("file_size")),
        chunk_count=meta.get("chunk_count", 0),
        token_count=meta.get("token_count", 0),
        content_type=meta.get("content_type"),
        processing_params=meta.get("processing_params"),
        is_folder=meta.get("is_folder", False),
        error_message=meta.get("error"),
        created_by=meta.get("created_by"),
        updated_by=meta.get("updated_by"),
        created_at=None,
        updated_at=None,
        original_filename=meta.get("original_filename"),
    )


class FakeFileRepository:
    def __init__(self, records: dict[str, types.SimpleNamespace]):
        self.records = records
        self.update_calls = []

    async def list_by_kb_id(self, kb_id: str):
        return [record for record in self.records.values() if record.kb_id == kb_id]

    async def list_by_kb_id_after(
        self,
        kb_id: str,
        *,
        after_file_id: str | None = None,
        limit: int = 500,
        files_only: bool = False,
    ):
        records = [
            record
            for record in self.records.values()
            if record.kb_id == kb_id
            and (not after_file_id or record.file_id > after_file_id)
            and (not files_only or not record.is_folder)
        ]
        records.sort(key=lambda record: record.file_id)
        return records[:limit]

    async def update_fields(self, *, file_id: str, data: dict, kb_id: str | None = None):
        record = self.records.get(file_id)
        if record is None or (kb_id and record.kb_id != kb_id):
            return None
        for key, value in data.items():
            setattr(record, key, value)
        self.update_calls.append((file_id, kb_id, dict(data)))
        return record

    async def get_kb_file_stats(self, kb_id: str):
        records = [record for record in self.records.values() if record.kb_id == kb_id]
        files = [record for record in records if not record.is_folder]
        return {
            "row_count": len(records),
            "file_count": len(files),
            "folder_count": len(records) - len(files),
            "total_size": sum(int(record.file_size or 0) for record in files),
            "chunk_count": sum(int(record.chunk_count or 0) for record in files),
            "token_count": sum(int(record.token_count or 0) for record in files),
            "pending_parse_count": sum(1 for record in files if record.status == "uploaded"),
            "pending_index_count": sum(1 for record in files if record.status in {"parsed", "error_indexing"}),
            "processing_count": sum(
                1 for record in files if record.status in {"processing", "waiting", "parsing", "indexing"}
            ),
        }


def make_file_records(files: dict[str, dict]) -> dict[str, types.SimpleNamespace]:
    return {file_id: make_file_record(file_id, meta) for file_id, meta in files.items()}


async def test_create_database_persists_allowed_record_fields(tmp_path, monkeypatch):
    created_payloads = []

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            return None

        async def create(self, payload):
            created_payloads.append(payload)
            return types.SimpleNamespace(**payload)

        async def update(self, kb_id, data):
            raise AssertionError("create_database should insert new database metadata")

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        FakeKnowledgeBaseRepository,
    )

    manager = KnowledgeBaseManager(str(tmp_path))
    kb = FakeKnowledgeBase(str(tmp_path))
    share_config = {
        "version": 2,
        "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["root"]},
        "manage_scope": None,
    }

    async def database_name_available(_database_name: str) -> bool:
        return False

    monkeypatch.setattr(manager, "database_name_exists", database_name_available)
    monkeypatch.setattr(manager, "_get_or_create_kb_instance", lambda _kb_type: kb)
    monkeypatch.setattr(
        "yuxi.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, _kb_type: True),
    )
    monkeypatch.setattr(
        "yuxi.models.providers.cache.model_cache.get_model_info",
        lambda _spec: types.SimpleNamespace(model_type="embedding"),
    )

    async def get_database_info(kb_id: str):
        return KnowledgeBaseDetail(
            kb_id=kb_id,
            name="New database",
            description="New description",
            kb_type="fake",
            embedding_model_spec="provider:embedding",
            llm_model_spec=None,
            query_params={},
            additional_params={"auto_generate_questions": False},
            share_config=share_config,
            created_by="root",
            created_at=None,
        )

    monkeypatch.setattr(manager, "get_database_info", get_database_info)

    result = await manager.create_database(
        "New database",
        "New description",
        kb_type="fake",
        embedding_model_spec="provider:embedding",
        share_config=share_config,
        created_by="root",
        auto_generate_questions=False,
    )

    assert len(created_payloads) == 1
    payload = created_payloads[0]
    assert payload["share_config"] == share_config
    assert payload["created_by"] == "root"
    assert "share_config" not in payload["additional_params"]
    assert "created_by" not in payload["additional_params"]
    assert result.kb_id.startswith("kb_")
    assert not hasattr(kb, "_runtime_configs")


async def test_manager_refresh_database_stats_persists_metadata(tmp_path, monkeypatch):
    manager = KnowledgeBaseManager(str(tmp_path))
    records = make_file_records(
        {
            "file-1": {"kb_id": "db", "filename": "alpha.md", "chunk_count": 2, "token_count": 10},
            "folder-1": {
                "kb_id": "db",
                "filename": "folder",
                "is_folder": True,
                "chunk_count": 99,
                "token_count": 99,
            },
        }
    )
    file_repo = FakeFileRepository(records)
    kb_repo = FakeKnowledgeBaseRepository()

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda: file_repo,
    )
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        lambda: kb_repo,
    )

    stats = await manager._refresh_database_stats("db")

    assert stats["file_count"] == 1
    assert stats["chunk_count"] == 2
    assert stats["token_count"] == 10
    assert kb_repo.row.additional_params["stats"] == stats
    assert kb_repo.update_calls == [{"stats": stats}]


@pytest.mark.parametrize(
    (
        "file_records",
        "chunk_counts",
        "expected_count_file_ids",
        "token_chunks",
        "expected_token_file_ids",
        "expected_file_stats",
        "expected_stats",
        "expected_counters",
        "expected_updated_files",
    ),
    [
        (
            {
                "file-1": {"kb_id": "db", "filename": "alpha.md", "chunk_count": 0, "token_count": 0},
                "file-2": {"kb_id": "db", "filename": "beta.md", "chunk_count": 1, "token_count": 7},
                "folder-1": {
                    "kb_id": "db",
                    "filename": "folder",
                    "is_folder": True,
                    "chunk_count": 99,
                    "token_count": 99,
                },
            },
            {"file-1": 2, "file-2": 3},
            ["file-1", "file-2"],
            [("file-1", "alpha beta"), ("file-1", "中文")],
            ["file-1"],
            {
                "file-1": (2, count_tokens("alpha beta") + count_tokens("中文")),
                "file-2": (3, 7),
            },
            {
                "file_count": 2,
                "chunk_count": 5,
                "token_count": count_tokens("alpha beta") + count_tokens("中文") + 7,
            },
            {"scanned_token_files": 1, "updated_chunk_files": 2, "updated_token_files": 1},
            {"file-1", "file-2"},
        ),
        (
            {
                "file-indexed": {
                    "kb_id": "db",
                    "filename": "alpha.md",
                    "status": "indexed",
                    "chunk_count": 0,
                    "token_count": 0,
                },
                "file-uploaded": {
                    "kb_id": "db",
                    "filename": "beta.md",
                    "status": "uploaded",
                    "chunk_count": 9,
                    "token_count": 90,
                },
                "file-parsed": {
                    "kb_id": "db",
                    "filename": "gamma.md",
                    "status": "parsed",
                    "chunk_count": 3,
                    "token_count": 30,
                },
            },
            {"file-indexed": 2},
            ["file-indexed"],
            [("file-indexed", "alpha beta")],
            ["file-indexed"],
            {
                "file-indexed": (2, count_tokens("alpha beta")),
                "file-uploaded": (0, 0),
                "file-parsed": (0, 0),
            },
            {"file_count": 3, "chunk_count": 2, "token_count": count_tokens("alpha beta")},
            {
                "scanned_files": 3,
                "scanned_indexed_files": 1,
                "skipped_unindexed_files": 2,
                "updated_files": 3,
            },
            {"file-indexed", "file-uploaded", "file-parsed"},
        ),
    ],
)
async def test_repair_missing_file_stats_updates_indexed_and_skips_unindexed_files(
    tmp_path,
    monkeypatch,
    file_records,
    chunk_counts,
    expected_count_file_ids,
    token_chunks,
    expected_token_file_ids,
    expected_file_stats,
    expected_stats,
    expected_counters,
    expected_updated_files,
):
    kb = make_kb(tmp_path)
    manager = KnowledgeBaseManager(str(tmp_path))
    records = make_file_records(file_records)
    file_repo = FakeFileRepository(records)
    kb_repo = FakeKnowledgeBaseRepository()

    class FakeChunkRepo:
        async def count_by_file_ids(self, file_ids):
            assert file_ids == expected_count_file_ids
            return {file_id: chunk_counts[file_id] for file_id in file_ids}

        async def list_by_file_ids(self, file_ids):
            assert file_ids == expected_token_file_ids
            return [types.SimpleNamespace(file_id=file_id, content=content) for file_id, content in token_chunks]

    monkeypatch.setattr("yuxi.repositories.knowledge_chunk_repository.KnowledgeChunkRepository", FakeChunkRepo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda: file_repo,
    )
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        lambda: kb_repo,
    )

    async def get_kb_executor(_kb_id: str):
        return kb

    monkeypatch.setattr(manager, "get_kb_executor", get_kb_executor)

    result = await manager.repair_missing_file_stats("db")

    for file_id, (chunk_count, token_count) in expected_file_stats.items():
        assert records[file_id].chunk_count == chunk_count
        assert records[file_id].token_count == token_count
    for counter, value in expected_counters.items():
        assert result[counter] == value
    for key, value in expected_stats.items():
        assert result["stats"][key] == value
        assert kb_repo.row.additional_params["stats"][key] == value
    assert {file_id for file_id, _, _ in file_repo.update_calls} == expected_updated_files
