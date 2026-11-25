"""Run 终态状态投影：写主、新鲜度快路径与懒回填的行为钉桩。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import yuxi.modules.agents.services.checkpoint_state as svc

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

PROJECTION = {
    "values": {"warnings": [{"code": "w"}], "artifacts": ["saved.txt"], "token_usage": {"total": 9}},
    "checkpoint_id": "checkpoint-9",
    "run_id": "run-9",
}


def _session(**overrides):
    base = dict(
        id=1,
        uid="user-1",
        app_id=None,
        status="active",
        parent_thread_id=None,
        project_id="p1",
        agent_state_view=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_repos(monkeypatch, *, session, latest_run=None, save=None):
    monkeypatch.setattr(
        svc,
        "SessionRepository",
        lambda _db: SimpleNamespace(
            get_session_by_thread_id=AsyncMock(return_value=session),
            save_agent_state_view=save or AsyncMock(return_value=True),
        ),
    )
    monkeypatch.setattr(
        svc,
        "AgentRunRepository",
        lambda _db: SimpleNamespace(get_latest_run_by_thread_for_user=AsyncMock(return_value=latest_run)),
    )


def _forbid_checkpoint(monkeypatch):
    monkeypatch.setattr(svc, "get_latest_root_checkpoint_id", AsyncMock(return_value="checkpoint-9"))
    monkeypatch.setattr(svc, "get_langgraph_checkpointer", lambda _manager: pytest.fail("快路径不得读取 checkpoint"))


async def test_projection_keeps_only_consumed_channels_and_json_safe():
    payload = svc.build_agent_state_projection(
        {
            "messages": ["must-not-project"],
            "files": {"big": "must-not-project"},
            "todos": [{"content": "x"}],
            "warnings": [{"code": "mcp_unavailable"}],
            "artifacts": ("a.txt",),
            "token_usage": {"total": 3},
        },
        {"kind": "approval", "waits": [{"id": 1}]},
        checkpoint_id="checkpoint-9",
        run_id=7,
    )

    assert payload["run_id"] == "7"
    assert payload["checkpoint_id"] == "checkpoint-9"
    assert payload["values"] == {
        "warnings": [{"code": "mcp_unavailable"}],
        "artifacts": ["a.txt"],
        "token_usage": {"total": 3},
    }
    assert payload["interrupt"] == {"kind": "approval", "waits": [{"id": 1}]}


async def test_missing_channels_are_normalized_before_storage():
    """空 checkpoint 的通道按 extract_agent_state 的默认形态落库。"""
    payload = svc.build_agent_state_projection({}, None, checkpoint_id="checkpoint-r", run_id="r")

    assert payload["values"] == {"warnings": [], "artifacts": [], "token_usage": None}


async def test_fast_path_serves_from_projection_without_checkpoint_or_write(monkeypatch):
    latest_run = SimpleNamespace(id="run-9", turn_id="t", status="completed")
    session = _session(agent_state_view=PROJECTION)
    save = AsyncMock()
    _patch_repos(monkeypatch, session=session, latest_run=latest_run, save=save)
    _forbid_checkpoint(monkeypatch)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    response = await svc.get_agent_state_view(
        thread_id="thread-1",
        current_user=SimpleNamespace(uid="user-1"),
        db=db,
        include_relations=False,
    )

    assert response == {
        "agent_state": {
            "warnings": [{"code": "w"}],
            "artifacts": ["saved.txt"],
            "cooperation": {"sessions": []},
            "token_usage": {"total": 9},
        }
    }
    save.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_interrupt_payload_served_from_projection_under_turn_gate(monkeypatch):
    latest_run = SimpleNamespace(id="run-9", turn_id="t", status="interrupted")
    session = _session(agent_state_view={**PROJECTION, "interrupt": {"kind": "ask_user_question"}})
    turn = SimpleNamespace(status="waiting", current_run_id="run-9", waitpoint={"run_id": "run-9"})
    _patch_repos(monkeypatch, session=session, latest_run=latest_run)
    monkeypatch.setattr(svc, "AgentTurnRepository", lambda _db: SimpleNamespace(get_for_scope=AsyncMock(return_value=turn)))
    _forbid_checkpoint(monkeypatch)

    response = await svc.get_agent_state_view(
        thread_id="thread-1",
        current_user=SimpleNamespace(uid="user-1"),
        db=SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
        include_relations=False,
    )

    assert response["interrupt"] == {"kind": "ask_user_question", "run_id": "run-9"}


async def test_stale_projection_falls_back_and_backfills_latest_run(monkeypatch):
    latest_run = SimpleNamespace(id="run-9", turn_id="t", status="completed")
    session = _session(agent_state_view={**PROJECTION, "run_id": "run-old"})
    save = AsyncMock(return_value=True)
    _patch_repos(monkeypatch, session=session, latest_run=latest_run, save=save)
    read = AsyncMock(
        return_value=("checkpoint-9", {"warnings": [], "artifacts": ["fresh.txt"], "token_usage": None, "messages": ["x"]}, None)
    )
    monkeypatch.setattr(svc, "_read_checkpoint_state", read)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    response = await svc.get_agent_state_view(
        thread_id="thread-1",
        current_user=SimpleNamespace(uid="user-1"),
        db=db,
        include_relations=False,
    )

    read.assert_awaited_once()
    assert response["agent_state"]["artifacts"] == ["fresh.txt"]
    payload = save.await_args.kwargs["payload"]
    assert payload["run_id"] == "run-9"
    assert "messages" not in payload["values"]
    # 两次提交：读事务在 checkpoint 读取前释放 + 懒回填写入
    assert db.commit.await_count == 2


async def test_running_run_falls_back_without_freezing_projection(monkeypatch):
    latest_run = SimpleNamespace(id="run-9", turn_id="t", status="running")
    session = _session()
    save = AsyncMock()
    _patch_repos(monkeypatch, session=session, latest_run=latest_run, save=save)
    monkeypatch.setattr(svc, "_read_checkpoint_state", AsyncMock(return_value=(None, {}, None)))
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    await svc.get_agent_state_view(thread_id="thread-1", current_user=SimpleNamespace(uid="user-1"), db=db, include_relations=False)

    save.assert_not_awaited()
    # 非终态只允许 checkpoint 读取前的读事务释放，不得产生定格投影的写提交
    db.commit.assert_awaited_once()


def _fake_session_context():
    @asynccontextmanager
    async def _ctx():
        yield SimpleNamespace()

    return SimpleNamespace(get_async_session_context=staticmethod(_ctx))


async def test_terminal_hook_writes_projection_in_own_session(monkeypatch):
    save = AsyncMock(return_value=True)
    monkeypatch.setattr(svc, "SessionRepository", lambda _db: SimpleNamespace(save_agent_state_view=save))
    monkeypatch.setattr(svc, "pg_manager", _fake_session_context())

    await svc.project_agent_state_after_terminal(
        uid="user-1",
        thread_id="thread-1",
        run_id="run-9",
        checkpoint_id="checkpoint-9",
        values={"warnings": [], "artifacts": ["a"], "token_usage": None},
    )

    assert save.await_args.kwargs["payload"]["run_id"] == "run-9"


async def test_terminal_hook_failure_never_escapes(monkeypatch):
    async def _boom(**_kwargs):
        raise RuntimeError("checkpoint 不可用")

    monkeypatch.setattr(svc, "_read_checkpoint_state", _boom)

    await svc.project_agent_state_after_terminal(uid="u", thread_id="t", run_id="r")


async def test_compression_refresh_retags_latest_run(monkeypatch):
    latest = SimpleNamespace(id="run-9", status="completed")

    class _RunRepo:
        def __init__(self, _db):
            pass

        async def get_latest_run_by_thread_for_user(self, thread_id, uid):
            return latest

    @asynccontextmanager
    async def _ctx():
        yield SimpleNamespace()

    monkeypatch.setattr(svc, "pg_manager", SimpleNamespace(get_async_session_context=_ctx))
    monkeypatch.setattr(svc, "AgentRunRepository", _RunRepo)
    project = AsyncMock()
    monkeypatch.setattr(svc, "project_agent_state_after_terminal", project)

    await svc.refresh_state_projection_after_compression(uid="user-1", thread_id="thread-1")

    assert project.await_args.kwargs["run_id"] == "run-9"


async def test_compression_refresh_degrades_when_run_lookup_fails(monkeypatch):
    @asynccontextmanager
    async def _boom():
        raise RuntimeError("pg 不可用")
        yield None

    monkeypatch.setattr(svc, "pg_manager", SimpleNamespace(get_async_session_context=_boom))
    project = AsyncMock()
    monkeypatch.setattr(svc, "project_agent_state_after_terminal", project)

    await svc.refresh_state_projection_after_compression(uid="user-1", thread_id="thread-1")

    assert project.await_args.kwargs["run_id"] is None
