import types
from collections import Counter
from unittest.mock import AsyncMock

from test.unit.support.knowledge import FakeKnowledgeBase, make_knowledge_file_record
from yuxi.modules.knowledge.manager import KnowledgeBaseManager
from yuxi.modules.knowledge.read_models import KnowledgeBaseDetail
from yuxi.modules.knowledge.services import bases as knowledge_bases
from yuxi.modules.knowledge.services import statistics


class FakeKnowledgeBaseRepository:
    def __init__(self, additional_params: dict | None = None):
        self.row = types.SimpleNamespace(additional_params=additional_params or {})
        self.update_calls = []

    async def get_by_kb_id(self, kb_id: str):
        return self.row if kb_id == "db" else None

    async def refresh_stats(self, kb_id: str):
        assert kb_id == "db"
        from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

        stats = await KnowledgeFileRepository().get_kb_file_stats(kb_id)
        self.update_calls.append({"stats": stats})
        self.row.additional_params = {**self.row.additional_params, "stats": stats}
        return self.row


def make_file_record(file_id: str, meta: dict):
    return make_knowledge_file_record(
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
            "file_status_counts": dict(Counter(record.status for record in files)),
            "row_count": len(records),
            "file_count": len(files),
            "folder_count": len(records) - len(files),
            "total_size": sum(int(record.file_size or 0) for record in files),
            "chunk_count": sum(int(record.chunk_count or 0) for record in files),
            "token_count": sum(int(record.token_count or 0) for record in files),
            "pending_parse_count": sum(1 for record in files if record.status == "uploaded"),
            "pending_index_count": sum(1 for record in files if record.status in {"parsed", "error_indexing"}),
            "processing_count": sum(1 for record in files if record.status in {"processing", "waiting", "parsing", "indexing"}),
        }


def make_file_records(files: dict[str, dict]) -> dict[str, types.SimpleNamespace]:
    return {file_id: make_file_record(file_id, meta) for file_id, meta in files.items()}


async def test_create_knowledge_base_persists_allowed_record_fields(tmp_path, monkeypatch):
    created_payloads = []

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            return None

        async def create(self, payload):
            created_payloads.append(payload)
            return types.SimpleNamespace(**payload)

        async def update(self, kb_id, data):
            raise AssertionError("create_knowledge_base should insert new knowledge_base metadata")

    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository",
        FakeKnowledgeBaseRepository,
    )

    kb = FakeKnowledgeBase(str(tmp_path))
    share_config = {
        "version": 2,
        "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["root"]},
        "manage_scope": None,
    }

    async def knowledge_base_name_available(_name: str) -> bool:
        return False

    monkeypatch.setattr(FakeKnowledgeBaseRepository, "name_exists", staticmethod(knowledge_base_name_available), raising=False)
    monkeypatch.setattr(
        KnowledgeBaseManager, "_get_or_create_kb_instance", AsyncMock(side_effect=AssertionError("metadata must not construct executor"))
    )
    monkeypatch.setattr("yuxi.modules.knowledge.factory.KnowledgeBaseFactory.get_kb_class", lambda _: type(kb))
    monkeypatch.setattr(
        "yuxi.modules.knowledge.factory.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, _kb_type: True),
    )
    monkeypatch.setattr(
        "yuxi.modules.models.providers.cache.model_cache.get_model_info",
        lambda _spec: types.SimpleNamespace(model_type="embedding"),
    )

    async def get_knowledge_base_info(kb_id: str):
        return KnowledgeBaseDetail(
            kb_id=kb_id,
            name="New knowledge_base",
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

    monkeypatch.setattr(knowledge_bases, "get_knowledge_base_info", get_knowledge_base_info)

    result = await knowledge_bases.create_knowledge_base(
        "New knowledge_base",
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


async def test_refresh_knowledge_base_stats_persists_metadata(monkeypatch):
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
        "yuxi.modules.knowledge.repositories.files.KnowledgeFileRepository",
        lambda: file_repo,
    )
    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository",
        lambda: kb_repo,
    )

    stats = await statistics.refresh_stats("db")

    assert stats["file_count"] == 1
    assert stats["chunk_count"] == 2
    assert stats["token_count"] == 10
    assert kb_repo.row.additional_params["stats"] == stats
    assert kb_repo.update_calls == [{"stats": stats}]
