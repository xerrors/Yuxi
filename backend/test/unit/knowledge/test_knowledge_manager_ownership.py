from types import SimpleNamespace

import pytest

from yuxi.knowledge.manager import KnowledgeBaseManager
from yuxi.knowledge.read_models import KnowledgeBaseConfig

pytestmark = pytest.mark.asyncio


async def test_delete_database_cleans_resources_before_deleting_record(tmp_path, monkeypatch):
    calls = []
    manager = KnowledgeBaseManager(str(tmp_path))

    class FakeExecutor:
        async def cleanup_database_resources(self, kb_id: str) -> dict:
            calls.append(("cleanup", kb_id))
            return {"message": "删除成功"}

    class FakeRepository:
        async def delete(self, kb_id: str) -> None:
            calls.append(("delete_record", kb_id))

    async def get_kb_executor(_kb_id: str):
        return FakeExecutor()

    monkeypatch.setattr(manager, "get_kb_executor", get_kb_executor)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        FakeRepository,
    )

    result = await manager.delete_database("kb_1")

    assert result == {"message": "删除成功"}
    assert calls == [("cleanup", "kb_1"), ("delete_record", "kb_1")]


@pytest.mark.parametrize("refresh_stats_fails", [False, True])
async def test_parse_file_refreshes_stats_and_keeps_original_error_after_executor_failure(
    tmp_path, monkeypatch, refresh_stats_fails
):
    manager = KnowledgeBaseManager(str(tmp_path))
    refreshed = []

    class FakeExecutor:
        async def parse_file(self, *args, **kwargs):
            raise ValueError("parse failed")

    async def get_kb_config(_kb_id: str):
        return KnowledgeBaseConfig(kb_id="kb_1", kb_type="fake")

    if refresh_stats_fails:
        async def refresh_database_stats(_kb_id: str):
            raise RuntimeError("stats failed")
    else:
        async def refresh_database_stats(kb_id: str):
            refreshed.append(kb_id)
            return {}

    monkeypatch.setattr(manager, "get_kb_config", get_kb_config)
    monkeypatch.setattr(manager, "_get_or_create_kb_instance", lambda _kb_type: FakeExecutor())
    monkeypatch.setattr(manager, "_refresh_database_stats", refresh_database_stats)

    with pytest.raises(ValueError, match="parse failed"):
        await manager.parse_file("kb_1", "file_1")

    if not refresh_stats_fails:
        assert refreshed == ["kb_1"]


async def test_update_query_params_delegates_persistence_to_manager(tmp_path, monkeypatch):
    manager = KnowledgeBaseManager(str(tmp_path))
    calls = []

    class FakeRepository:
        async def merge_query_params_options(self, kb_id: str, params: dict):
            calls.append((kb_id, params))
            return object()

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        FakeRepository,
    )

    await manager.update_kb_query_params("kb_1", {"top_k": 5})

    assert calls == [("kb_1", {"top_k": 5})]


async def test_consistency_check_delegates_type_resources_to_executor(tmp_path, monkeypatch):
    manager = KnowledgeBaseManager(str(tmp_path))
    calls = []

    class FakeExecutor:
        async def detect_data_inconsistencies(self, known_kb_ids: set[str], managed_kb_ids: set[str]):
            calls.append((known_kb_ids, managed_kb_ids))
            return {"missing_collections": [], "missing_files": []}

    class FakeRepository:
        async def get_all(self):
            return [
                SimpleNamespace(kb_id="kb_1", kb_type="milvus"),
                SimpleNamespace(kb_id="kb_2", kb_type="dify"),
            ]

    manager.kb_instances["milvus"] = FakeExecutor()
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        FakeRepository,
    )

    result = await manager.detect_data_inconsistencies()

    assert calls == [({"kb_1", "kb_2"}, {"kb_1"})]
    assert result["total_missing_collections"] == 0
    assert result["total_missing_files"] == 0
