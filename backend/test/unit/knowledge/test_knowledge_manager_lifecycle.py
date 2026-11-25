import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.modules.knowledge.manager import KnowledgeBaseManager
from yuxi.modules.knowledge.services import bases as knowledge_bases

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_executor_construction_is_offloaded_from_event_loop(tmp_path, monkeypatch):
    manager = KnowledgeBaseManager(str(tmp_path))
    event_loop_thread = threading.get_ident()
    construction_threads: list[int] = []
    executor = object()

    def create_executor(_kb_type: str, _work_dir: str):
        construction_threads.append(threading.get_ident())
        return executor

    monkeypatch.setattr("yuxi.modules.knowledge.manager.KnowledgeBaseFactory.create", create_executor)

    assert await manager.get_executor_for_type("fake") is executor
    assert construction_threads and construction_threads[0] != event_loop_thread


async def test_concurrent_executor_construction_is_deduplicated(tmp_path, monkeypatch):
    manager = KnowledgeBaseManager(str(tmp_path))
    construction_count = 0
    executor = object()

    def create_executor(_kb_type: str, _work_dir: str):
        nonlocal construction_count
        construction_count += 1
        return executor

    monkeypatch.setattr("yuxi.modules.knowledge.manager.KnowledgeBaseFactory.create", create_executor)

    results = await asyncio.gather(
        manager.get_executor_for_type("fake"),
        manager.get_executor_for_type("fake"),
    )

    assert results == [executor, executor]
    assert construction_count == 1


async def test_delete_knowledge_base_commits_tombstone_before_any_external_cleanup(tmp_path, monkeypatch):
    """删除请求只提交事实；外部清理通过 outbox，不阻塞不可见提交。"""
    repository = SimpleNamespace(mark_deleted=AsyncMock(return_value=["knowledge_base:kb_1:deleted"]))
    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository",
        lambda: repository,
    )
    executor = AsyncMock(side_effect=RuntimeError("Milvus offline"))
    monkeypatch.setattr(KnowledgeBaseManager, "get_executor_for_type", executor)
    result = await knowledge_bases.delete_knowledge_base("kb_1")
    assert result == {"message": "删除成功"}
    repository.mark_deleted.assert_awaited_once_with("kb_1")
    executor.assert_not_awaited()


async def test_update_query_params_merges_options_in_repository(monkeypatch):
    calls = []

    class FakeRepository:
        async def merge_query_params_options(self, kb_id: str, params: dict):
            calls.append((kb_id, params))
            return object()

    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository",
        FakeRepository,
    )

    await knowledge_bases.update_kb_query_params("kb_1", {"top_k": 5})

    assert calls == [("kb_1", {"top_k": 5})]


@pytest.mark.asyncio
async def test_initialize_creates_executors_for_types_in_use(monkeypatch, tmp_path):
    """initialize() 只为数据库中实际使用的知识库类型创建执行器。"""
    manager = KnowledgeBaseManager(str(tmp_path))

    async def fake_get_all(_self):
        return [
            SimpleNamespace(kb_id="kb_1", kb_type="milvus"),
        ]

    fake_instance = SimpleNamespace()

    def fake_create(_kb_type, _work_dir):
        return fake_instance

    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository.get_all",
        fake_get_all,
    )
    monkeypatch.setattr(
        "yuxi.modules.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, _kb_type: True),
    )
    monkeypatch.setattr(
        "yuxi.modules.knowledge.manager.KnowledgeBaseFactory.create",
        staticmethod(fake_create),
    )

    await manager.initialize()

    assert manager.kb_instances == {"milvus": fake_instance}


@pytest.mark.asyncio
async def test_initialize_propagates_failure_from_backend_already_in_use(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))

    async def fake_get_all(_self):
        return [SimpleNamespace(kb_id="kb_1", kb_type="milvus")]

    def fail_create(_kb_type, _work_dir):
        raise ConnectionError("milvus unavailable")

    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository.get_all",
        fake_get_all,
    )
    monkeypatch.setattr(
        "yuxi.modules.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, _kb_type: True),
    )
    monkeypatch.setattr(
        "yuxi.modules.knowledge.manager.KnowledgeBaseFactory.create",
        staticmethod(fail_create),
    )

    with pytest.raises(RuntimeError, match="milvus:ConnectionError"):
        await manager.initialize()


@pytest.mark.asyncio
async def test_initialize_rejects_persisted_unsupported_backend(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))

    async def fake_get_all(_self):
        return [SimpleNamespace(kb_id="kb_legacy", kb_type="removed-backend")]

    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository.get_all",
        fake_get_all,
    )

    with pytest.raises(RuntimeError, match="removed-backend:unsupported"):
        await manager.initialize()
