from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import yuxi.modules.agents.services.run_lifecycle as lifecycle
import yuxi.modules.agents.services.leases as leases


@pytest.mark.asyncio
async def test_cleanup_reconciler_keeps_pending_run_for_dispatch_recovery(monkeypatch: pytest.MonkeyPatch):
    """pending Run 只释放旧 runtime，派发与终态仍由各自 owner 处理。"""
    run = SimpleNamespace(
        id="run-1",
        status="pending",
        runtime_cleanup_pending=True,
        thread_id="thread-1",
        uid="user-1",
        agent_slug="chat",
    )

    @asynccontextmanager
    async def session_context():
        yield object()

    class Repo:
        def __init__(self, _db):
            pass

        async def list_pending_runtime_cleanups(self):
            return [run]

        async def get_run(self, _run_id):
            return run

    cleanup = AsyncMock(return_value=True)
    dispatch = AsyncMock()
    settlement = AsyncMock()
    monkeypatch.setattr(leases.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(leases, "AgentRunRepository", Repo)
    monkeypatch.setattr(leases, "complete_run_runtime_cleanup", cleanup)
    monkeypatch.setattr(lifecycle, "dispatch_next_input", dispatch)
    monkeypatch.setattr(leases, "publish_run_settlement", settlement)
    monkeypatch.setattr(leases, "reconcile_cancelling_turns", AsyncMock())

    cleaned = await leases.reconcile_pending_runtime_cleanups()

    assert cleaned == ["run-1"]
    cleanup.assert_awaited_once_with(run)
    dispatch.assert_not_awaited()
    settlement.assert_not_awaited()


async def test_stale_cleanup_does_not_touch_commands_of_new_attempt(monkeypatch, tmp_path):
    """清理候选已被处理并重新领取时，不得终止新 attempt 的命令。"""
    marker = tmp_path / "new-attempt-command"
    marker.write_text("running")
    current = SimpleNamespace(id="run-1", uid="user-1", status="running", worker_id="w-1", updated_at=None, runtime_cleanup_pending=False)
    db = SimpleNamespace(scalar=AsyncMock(return_value=current), flush=AsyncMock())

    @asynccontextmanager
    async def session_context():
        yield db

    provider = SimpleNamespace(cleanup_run=lambda *_: marker.unlink())
    monkeypatch.setattr(leases.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(lifecycle, "get_sandbox_provider", lambda: provider)
    stale = SimpleNamespace(id="run-1", uid="user-1", runtime_cleanup_pending=True)
    assert await leases.complete_run_runtime_cleanup(stale)
    assert marker.read_text() == "running"
