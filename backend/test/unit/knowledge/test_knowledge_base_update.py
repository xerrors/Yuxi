import asyncio
import types

from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.knowledge.base import KnowledgeBase


class FakeKnowledgeBase(KnowledgeBase):
    @property
    def kb_type(self) -> str:
        return "fake"

    async def _create_kb_instance(self, slug: str, config: dict):
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

    async def _save_metadata(self) -> None:
        pass


def make_kb(tmp_path):
    kb = FakeKnowledgeBase(str(tmp_path))
    kb.databases_meta = {
        "db": {
            "name": "Old name",
            "description": "Old description",
            "kb_type": "fake",
            "llm_model_spec": "provider:model-a",
        }
    }
    return kb


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

    kb = FakeKnowledgeBase(str(tmp_path))
    share_config = {"access_level": "user", "department_ids": [], "user_uids": ["root"]}

    await kb.create_database(
        "New database",
        "New description",
        embedding_model_spec="provider:embedding",
        record_fields={
            "share_config": share_config,
            "created_by": "root",
            "unexpected_field": "ignored",
        },
        auto_generate_questions=False,
    )

    assert len(created_payloads) == 1
    payload = created_payloads[0]
    assert payload["share_config"] == share_config
    assert payload["created_by"] == "root"
    assert "unexpected_field" not in payload
    assert "share_config" not in payload["additional_params"]
    assert "created_by" not in payload["additional_params"]


async def test_update_database_keeps_llm_spec_when_field_is_omitted(tmp_path):
    kb = make_kb(tmp_path)

    result = kb.update_database("db", "New name", "New description")
    await asyncio.sleep(0)

    assert result["llm_model_spec"] == "provider:model-a"
    assert kb.databases_meta["db"]["llm_model_spec"] == "provider:model-a"


async def test_update_database_clears_llm_spec_when_field_is_explicit(tmp_path):
    kb = make_kb(tmp_path)

    result = kb.update_database("db", "New name", "New description", None, update_llm_model_spec=True)
    await asyncio.sleep(0)

    assert result["llm_model_spec"] is None
    assert kb.databases_meta["db"]["llm_model_spec"] is None


def test_get_database_info_returns_persisted_content_stats(tmp_path):
    kb = make_kb(tmp_path)
    kb.files_meta = {
        "file-1": {
            "kb_id": "db",
            "filename": "alpha.md",
            "status": "indexed",
            "chunk_count": 2,
            "token_count": 10,
        },
        "file-2": {
            "kb_id": "db",
            "filename": "beta.md",
            "status": "indexed",
            "chunk_count": 3,
            "token_count": 15,
        },
        "folder-1": {
            "kb_id": "db",
            "filename": "folder",
            "is_folder": True,
            "status": "done",
            "chunk_count": 99,
            "token_count": 99,
        },
    }

    result = kb.get_database_info("db")

    assert result["row_count"] == 3
    assert result["stats"]["file_count"] == 2
    assert result["stats"]["chunk_count"] == 5
    assert result["stats"]["token_count"] == 25
    assert result["files"]["file-1"]["chunk_count"] == 2
    assert result["files"]["file-1"]["token_count"] == 10


def test_get_database_info_prefers_metadata_stats(tmp_path):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {"stats": {"file_count": 2, "chunk_count": 8, "token_count": 40}}
    kb.files_meta = {
        "file-1": {"kb_id": "db", "filename": "alpha.md", "chunk_count": 1, "token_count": 1},
        "file-2": {"kb_id": "db", "filename": "beta.md", "chunk_count": 1, "token_count": 1},
    }

    result = kb.get_database_info("db")

    assert result["stats"]["file_count"] == 2
    assert result["stats"]["chunk_count"] == 8
    assert result["stats"]["token_count"] == 40


async def test_refresh_database_stats_persists_metadata(tmp_path):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {}
    kb.files_meta = {
        "file-1": {"kb_id": "db", "filename": "alpha.md", "chunk_count": 2, "token_count": 10},
        "folder-1": {
            "kb_id": "db",
            "filename": "folder",
            "is_folder": True,
            "chunk_count": 99,
            "token_count": 99,
        },
    }
    persisted_kbs = []

    async def persist_kb(kb_id):
        persisted_kbs.append((kb_id, dict(kb.databases_meta[kb_id]["metadata"])))

    kb._persist_kb = persist_kb

    stats = await kb.refresh_database_stats("db")

    assert stats["file_count"] == 1
    assert stats["chunk_count"] == 2
    assert stats["token_count"] == 10
    assert kb.databases_meta["db"]["metadata"]["stats"] == stats
    assert persisted_kbs == [("db", {"stats": stats})]


async def test_repair_missing_file_stats_updates_files_and_database_metadata(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {}
    kb.files_meta = {
        "file-1": {"kb_id": "db", "filename": "alpha.md", "chunk_count": 0, "token_count": 0},
        "file-2": {"kb_id": "db", "filename": "beta.md", "chunk_count": 1, "token_count": 7},
        "folder-1": {
            "kb_id": "db",
            "filename": "folder",
            "is_folder": True,
            "chunk_count": 99,
            "token_count": 99,
        },
    }
    persisted_files = []
    persisted_kbs = []

    class FakeChunkRepo:
        async def count_by_file_ids(self, file_ids):
            assert file_ids == ["file-1", "file-2"]
            return {"file-1": 2, "file-2": 3}

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["file-1"]
            return [
                types.SimpleNamespace(file_id="file-1", content="alpha beta"),
                types.SimpleNamespace(file_id="file-1", content="中文"),
            ]

    async def persist_file(file_id):
        persisted_files.append((file_id, dict(kb.files_meta[file_id])))

    async def persist_kb(kb_id):
        persisted_kbs.append((kb_id, dict(kb.databases_meta[kb_id]["metadata"])))

    monkeypatch.setattr("yuxi.repositories.knowledge_chunk_repository.KnowledgeChunkRepository", FakeChunkRepo)
    kb._persist_file = persist_file
    kb._persist_kb = persist_kb

    result = await kb.repair_missing_file_stats("db")

    expected_token_count = count_tokens("alpha beta") + count_tokens("中文")
    expected_stats = {"file_count": 2, "chunk_count": 5, "token_count": expected_token_count + 7}
    assert kb.files_meta["file-1"]["chunk_count"] == 2
    assert kb.files_meta["file-1"]["token_count"] == expected_token_count
    assert kb.files_meta["file-2"]["chunk_count"] == 3
    assert kb.files_meta["file-2"]["token_count"] == 7
    for key, value in expected_stats.items():
        assert result["stats"][key] == value
    assert result["scanned_token_files"] == 1
    assert result["updated_chunk_files"] == 2
    assert result["updated_token_files"] == 1
    assert {file_id for file_id, _ in persisted_files} == {"file-1", "file-2"}
    persisted_stats = persisted_kbs[0][1]["stats"]
    for key, value in expected_stats.items():
        assert persisted_stats[key] == value


async def test_repair_missing_file_stats_skips_unindexed_files(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {}
    kb.files_meta = {
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
    }
    persisted_files = []

    class FakeChunkRepo:
        async def count_by_file_ids(self, file_ids):
            assert file_ids == ["file-indexed"]
            return {"file-indexed": 2}

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["file-indexed"]
            return [types.SimpleNamespace(file_id="file-indexed", content="alpha beta")]

    async def persist_file(file_id):
        persisted_files.append((file_id, dict(kb.files_meta[file_id])))

    async def persist_kb(kb_id):
        pass

    monkeypatch.setattr("yuxi.repositories.knowledge_chunk_repository.KnowledgeChunkRepository", FakeChunkRepo)
    kb._persist_file = persist_file
    kb._persist_kb = persist_kb

    result = await kb.repair_missing_file_stats("db")

    expected_token_count = count_tokens("alpha beta")
    assert kb.files_meta["file-indexed"]["chunk_count"] == 2
    assert kb.files_meta["file-indexed"]["token_count"] == expected_token_count
    assert kb.files_meta["file-uploaded"]["chunk_count"] == 0
    assert kb.files_meta["file-uploaded"]["token_count"] == 0
    assert kb.files_meta["file-parsed"]["chunk_count"] == 0
    assert kb.files_meta["file-parsed"]["token_count"] == 0
    assert result["stats"]["file_count"] == 3
    assert result["stats"]["chunk_count"] == 2
    assert result["stats"]["token_count"] == expected_token_count
    assert result["scanned_files"] == 3
    assert result["scanned_indexed_files"] == 1
    assert result["skipped_unindexed_files"] == 2
    assert result["updated_files"] == 3
    assert {file_id for file_id, _ in persisted_files} == {"file-indexed", "file-uploaded", "file-parsed"}
