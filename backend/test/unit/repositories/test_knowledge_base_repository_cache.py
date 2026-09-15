from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import yuxi.repositories.knowledge_base_repository as repository_module
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

pytestmark = pytest.mark.unit


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def scalars(self):
        return self

    def all(self):
        return []

    def scalar_one_or_none(self):
        return self.row


class _FakeSession:
    def __init__(self, row=None, events=None):
        self.row = row
        self.added = []
        self.deleted = []
        self.events = events if events is not None else []
        self.statements = []

    def add(self, row):
        self.added.append(row)

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self.row)

    async def scalar(self, statement):
        self.statements.append(statement)
        return None

    async def delete(self, row):
        self.deleted.append(row)


def _patch_session(monkeypatch, session: _FakeSession):
    @asynccontextmanager
    async def fake_session_context():
        session.events.append("transaction_open")
        yield session
        session.events.append("transaction_commit")

    monkeypatch.setattr(repository_module.pg_manager, "get_async_session_context", fake_session_context)


def _patch_cache_lock(monkeypatch, events: list[str]):
    @asynccontextmanager
    async def fake_cache_lock(_kb_id: str):
        events.append("lock_acquired")
        yield
        events.append("lock_released")

    monkeypatch.setattr(repository_module, "kb_config_cache_lock", fake_cache_lock)


@pytest.mark.asyncio
async def test_update_invalidates_cache_before_database_commit(monkeypatch):
    events = []
    row = SimpleNamespace(kb_id="kb_1", name="Old")
    session = _FakeSession(row, events)
    _patch_session(monkeypatch, session)
    _patch_cache_lock(monkeypatch, events)

    async def fake_delete_cached(_kb_id: str):
        events.append("cache_invalidated")

    monkeypatch.setattr(repository_module, "delete_cached_kb_config", fake_delete_cached)

    result = await KnowledgeBaseRepository().update("kb_1", {"name": "New"})

    assert result is row
    assert row.name == "New"
    assert events == [
        "lock_acquired",
        "cache_invalidated",
        "transaction_open",
        "transaction_commit",
        "lock_released",
    ]


@pytest.mark.asyncio
async def test_cache_invalidation_failure_aborts_before_database_update(monkeypatch):
    events = []
    row = SimpleNamespace(kb_id="kb_1", name="Old")
    session = _FakeSession(row, events)
    _patch_session(monkeypatch, session)
    _patch_cache_lock(monkeypatch, events)

    async def fail_delete_cached(_kb_id: str):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(repository_module, "delete_cached_kb_config", fail_delete_cached)

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await KnowledgeBaseRepository().update("kb_1", {"name": "New"})

    assert row.name == "Old"
    assert events == ["lock_acquired"]


@pytest.mark.asyncio
async def test_delete_holds_cache_lock_through_database_commit(monkeypatch):
    events = []
    row = SimpleNamespace(kb_id="kb_1")
    session = _FakeSession(row, events)
    _patch_session(monkeypatch, session)
    _patch_cache_lock(monkeypatch, events)

    async def fake_delete_cached(_kb_id: str):
        events.append("cache_invalidated")

    monkeypatch.setattr(repository_module, "delete_cached_kb_config", fake_delete_cached)

    await KnowledgeBaseRepository().delete("kb_1")

    assert session.deleted == [row]
    assert events == [
        "lock_acquired",
        "cache_invalidated",
        "transaction_open",
        "transaction_commit",
        "lock_released",
    ]


@pytest.mark.asyncio
async def test_merge_query_params_options_preserves_concurrent_partial_updates(monkeypatch):
    row = SimpleNamespace(kb_id="kb_1", query_params={"options": {}})
    sessions = []

    @asynccontextmanager
    async def fake_session_context():
        session = _FakeSession(row)
        sessions.append(session)
        yield session

    lock = asyncio.Lock()

    @asynccontextmanager
    async def fake_cache_lock(_kb_id: str):
        async with lock:
            yield

    async def fake_delete_cached(_kb_id: str):
        return None

    monkeypatch.setattr(repository_module.pg_manager, "get_async_session_context", fake_session_context)
    monkeypatch.setattr(repository_module, "kb_config_cache_lock", fake_cache_lock)
    monkeypatch.setattr(repository_module, "delete_cached_kb_config", fake_delete_cached)

    await asyncio.gather(
        KnowledgeBaseRepository().merge_query_params_options("kb_1", {"top_k": 5}),
        KnowledgeBaseRepository().merge_query_params_options("kb_1", {"use_reranker": True}),
    )

    assert row.query_params == {"options": {"top_k": 5, "use_reranker": True}}
    assert all("FOR UPDATE" in str(session.statements[0]) for session in sessions)


@pytest.mark.asyncio
async def test_refresh_stats_preserves_concurrent_additional_params(monkeypatch):
    row = SimpleNamespace(kb_id="kb_1", additional_params={"graph_build_config": {"locked": True}})
    session = _FakeSession(row)
    _patch_session(monkeypatch, session)
    _patch_cache_lock(monkeypatch, [])

    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository.query_kb_file_stats",
        AsyncMock(return_value={"file_count": 3}),
    )
    result = await KnowledgeBaseRepository().refresh_stats("kb_1")

    assert result is row
    assert row.additional_params == {
        "graph_build_config": {"locked": True},
        "stats": {"file_count": 3},
    }
    assert "FOR UPDATE" in str(session.statements[0])
