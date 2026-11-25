from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from yuxi.modules.knowledge.manager import KnowledgeBaseManager

pytestmark = pytest.mark.unit


class _FakeKnowledgeBase:
    kb_type = "milvus"

    def __init__(self):
        self.queries = []

    async def aquery(self, query: str, kb_id: str, **options):
        config = options.pop("config")
        self.queries.append((query, kb_id, options, config))
        return [{"content": "matched"}]

    def build_search_output(self, kb_id: str, results: list[dict]) -> dict:
        return {"kb_id": kb_id, "results": results}

    @staticmethod
    def normalize_additional_params(params):
        return params or {}

    @staticmethod
    def get_default_query_params():
        return {"options": {}}


def _patch_supported_type(monkeypatch):
    monkeypatch.setattr("yuxi.modules.knowledge.manager.KnowledgeBaseFactory.get_kb_class", lambda _: _FakeKnowledgeBase)
    monkeypatch.setattr(
        "yuxi.modules.knowledge.manager.KnowledgeBaseFactory.is_type_supported",
        classmethod(lambda cls, kb_type: kb_type == "milvus"),
    )


def _kb_row(*, name: str = "Knowledge", query_params: dict | None = None):
    return SimpleNamespace(
        kb_id="kb_1",
        name=name,
        description="Description",
        kb_type="milvus",
        embedding_model_spec="provider:embedding",
        llm_model_spec=None,
        query_params=query_params or {"options": {"top_k": 5}},
        additional_params={"chunking": {"preset_id": "general"}},
        created_at=None,
    )


@pytest.mark.asyncio
async def test_retrieve_uses_cached_config_without_postgres(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))
    fake_kb = _FakeKnowledgeBase()
    _patch_supported_type(monkeypatch)
    cached = {
        "kb_id": "kb_1",
        "kb_type": "milvus",
        "embedding_model_spec": "provider:embedding",
        "query_params": {"options": {"top_k": 3}},
        "additional_params": {
            "chunking": {"preset_id": "general"},
            "stats": {"file_count": 10},
        },
    }

    async def fake_get_cached(kb_id: str):
        assert kb_id == "kb_1"
        return cached

    async def fail_postgres(_self, _kb_id: str):
        raise AssertionError("Redis 命中时不应查询 PostgreSQL")

    monkeypatch.setattr("yuxi.modules.knowledge.manager.get_cached_kb_config", fake_get_cached)
    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository.get_by_kb_id",
        fail_postgres,
    )
    monkeypatch.setattr(manager, "get_executor_for_type", AsyncMock(return_value=fake_kb))

    result = await manager.retrieve("kb_1", "hello", top_k=2)

    assert result == {"kb_id": "kb_1", "results": [{"content": "matched"}]}
    assert fake_kb.queries[0][:3] == ("hello", "kb_1", {"top_k": 2})
    config = fake_kb.queries[0][3]
    assert config.embedding_model_spec == "provider:embedding"
    assert config.query_options == {"top_k": 3}
    assert "stats" not in config.additional_params


@pytest.mark.asyncio
async def test_retrieve_falls_back_to_postgres_and_populates_cache(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))
    fake_kb = _FakeKnowledgeBase()
    _patch_supported_type(monkeypatch)
    row = _kb_row()
    cached_payloads = []

    async def fake_get_cached(_kb_id: str):
        return None

    async def fake_get_by_kb_id(_self, kb_id: str):
        assert kb_id == "kb_1"
        return row

    async def fake_cache_row(cached_row):
        cached_payloads.append(cached_row)

    @asynccontextmanager
    async def fake_cache_lock(kb_id: str):
        assert kb_id == "kb_1"
        yield

    monkeypatch.setattr("yuxi.modules.knowledge.manager.get_cached_kb_config", fake_get_cached)
    monkeypatch.setattr("yuxi.modules.knowledge.manager.cache_kb_config", fake_cache_row)
    monkeypatch.setattr("yuxi.modules.knowledge.manager.kb_config_cache_lock", fake_cache_lock)
    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository.get_by_kb_id",
        fake_get_by_kb_id,
    )
    monkeypatch.setattr(manager, "get_executor_for_type", AsyncMock(return_value=fake_kb))

    result = await manager.retrieve("kb_1", "hello")

    assert result["kb_id"] == "kb_1"
    assert cached_payloads == [row]
    assert fake_kb.queries[0][3].query_options == {"top_k": 5}


@pytest.mark.asyncio
async def test_cache_miss_rechecks_snapshot_after_acquiring_lock(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))
    fake_kb = _FakeKnowledgeBase()
    _patch_supported_type(monkeypatch)
    snapshots = [
        None,
        {
            "kb_id": "kb_1",
            "kb_type": "milvus",
            "embedding_model_spec": "provider:embedding",
            "query_params": {"options": {"top_k": 8}},
            "additional_params": {},
        },
    ]
    lock_events = []

    async def fake_get_cached(_kb_id: str):
        return snapshots.pop(0)

    @asynccontextmanager
    async def fake_cache_lock(kb_id: str):
        lock_events.append(("enter", kb_id))
        yield
        lock_events.append(("exit", kb_id))

    async def fail_postgres(_self, _kb_id: str):
        raise AssertionError("锁内缓存已有新值时不应回源 PostgreSQL")

    monkeypatch.setattr("yuxi.modules.knowledge.manager.get_cached_kb_config", fake_get_cached)
    monkeypatch.setattr("yuxi.modules.knowledge.manager.kb_config_cache_lock", fake_cache_lock)
    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository.get_by_kb_id",
        fail_postgres,
    )
    monkeypatch.setattr(manager, "get_executor_for_type", AsyncMock(return_value=fake_kb))

    config = await manager.get_kb_config("kb_1")

    assert config.query_options == {"top_k": 8}
    assert lock_events == [("enter", "kb_1"), ("exit", "kb_1")]


@pytest.mark.asyncio
async def test_cache_lock_connection_failure_reads_postgres_without_refill(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))
    fake_kb = _FakeKnowledgeBase()
    _patch_supported_type(monkeypatch)
    row = _kb_row(query_params={"options": {"top_k": 9}})

    async def fake_get_cached(_kb_id: str):
        return None

    @asynccontextmanager
    async def fail_cache_lock(_kb_id: str):
        raise RedisConnectionError("redis unavailable")
        yield

    async def fake_get_by_kb_id(_self, _kb_id: str):
        return row

    async def fail_cache_row(_row):
        raise AssertionError("Redis 故障回源时不应尝试回填缓存")

    monkeypatch.setattr("yuxi.modules.knowledge.manager.get_cached_kb_config", fake_get_cached)
    monkeypatch.setattr("yuxi.modules.knowledge.manager.kb_config_cache_lock", fail_cache_lock)
    monkeypatch.setattr("yuxi.modules.knowledge.manager.cache_kb_config", fail_cache_row)
    monkeypatch.setattr(
        "yuxi.modules.knowledge.repositories.bases.KnowledgeBaseRepository.get_by_kb_id",
        fake_get_by_kb_id,
    )
    monkeypatch.setattr(manager, "get_executor_for_type", AsyncMock(return_value=fake_kb))

    config = await manager.get_kb_config("kb_1")

    assert config.query_options == {"top_k": 9}


@pytest.mark.asyncio
async def test_retrieve_refreshes_runtime_config_on_each_call(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))
    fake_kb = _FakeKnowledgeBase()
    _patch_supported_type(monkeypatch)
    snapshots = [
        {
            "kb_id": "kb_1",
            "kb_type": "milvus",
            "embedding_model_spec": "provider:embedding",
            "query_params": {"options": {"top_k": 3}},
            "additional_params": {},
        },
        {
            "kb_id": "kb_1",
            "kb_type": "milvus",
            "embedding_model_spec": "provider:embedding",
            "query_params": {"options": {"top_k": 8}},
            "additional_params": {},
        },
    ]

    async def fake_get_cached(_kb_id: str):
        return snapshots.pop(0)

    monkeypatch.setattr("yuxi.modules.knowledge.manager.get_cached_kb_config", fake_get_cached)
    monkeypatch.setattr(manager, "get_executor_for_type", AsyncMock(return_value=fake_kb))

    await manager.retrieve("kb_1", "first")
    await manager.retrieve("kb_1", "second")

    assert fake_kb.queries[0][3].query_options == {"top_k": 3}
    assert fake_kb.queries[1][3].query_options == {"top_k": 8}
