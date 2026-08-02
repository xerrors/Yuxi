"""Tests for yuxi.agents.toolkits.schedules.tools — read tools (Task 3).

Covers `list_my_schedules` and `get_schedule` LangGraph @tool functions.

所有依赖（pg_manager / ScheduleRepository / AgentConfigRepository）通过
monkeypatch 注入 fake，避免真实数据库。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from yuxi.agents.toolkits.schedules import tools
from yuxi.agents.toolkits.schedules.tools import ListMySchedulesInput


pytestmark = pytest.mark.asyncio


# ========== fake session / repo factory ==========


class _FakeRepo:
    def __init__(self, methods: dict[str, AsyncMock]):
        self._methods = methods
        for name, m in methods.items():
            setattr(self, name, m)


def _make_runtime(user_id: str | None = "u1", is_admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(context=SimpleNamespace(user_id=user_id, is_admin=is_admin))


def _patch_session(monkeypatch, repo: _FakeRepo) -> None:
    @asynccontextmanager
    async def _ctx():
        session = MagicMock()
        # 同时让 _check_agent_ownership 拿到的 repo 也是 stub
        yield session

    # 直接 patch：让工具内"ScheduleRepository(session)"返回我们的 stub
    def _factory(_session):
        return repo

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", _factory)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: MagicMock())


# ========== list_my_schedules ==========


async def test_list_my_schedules_filters_by_current_user(monkeypatch) -> None:
    row = SimpleNamespace(
        id="s1",
        user_id="u1",
        name="n1",
        cron_expr="*/5 * * * *",
        timezone="UTC",
        enabled=True,
        next_run_at=None,
        agent_config_id=1,
    )
    repo = _FakeRepo({"list_schedules": AsyncMock(return_value=[row])})
    _patch_session(monkeypatch, repo)

    result = await tools.list_my_schedules.coroutine(  # type: ignore[attr-defined]
        args=ListMySchedulesInput(),
        runtime=_make_runtime(user_id="u1"),
    )

    repo._methods["list_schedules"].assert_awaited_once()
    kwargs = repo._methods["list_schedules"].await_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["limit"] == 20
    assert kwargs["offset"] == 0
    payload = json.loads(result)
    assert payload[0]["id"] == "s1"


async def test_list_my_schedules_admin_passes_none_user_filter(monkeypatch) -> None:
    repo = _FakeRepo({"list_schedules": AsyncMock(return_value=[])})
    _patch_session(monkeypatch, repo)

    await tools.list_my_schedules.coroutine(  # type: ignore[attr-defined]
        args=ListMySchedulesInput(),
        runtime=_make_runtime(user_id="admin1", is_admin=True),
    )

    kwargs = repo._methods["list_schedules"].await_args.kwargs
    assert kwargs["user_id"] is None


async def test_list_my_schedules_clamps_limit(monkeypatch) -> None:
    repo = _FakeRepo({"list_schedules": AsyncMock(return_value=[])})
    _patch_session(monkeypatch, repo)

    await tools.list_my_schedules.coroutine(  # type: ignore[attr-defined]
        args=ListMySchedulesInput(limit=9999),
        runtime=_make_runtime(user_id="u1"),
    )

    kwargs = repo._methods["list_schedules"].await_args.kwargs
    assert kwargs["limit"] == 100  # LIST_MAX_LIMIT


async def test_list_my_schedules_returns_error_when_user_id_missing(monkeypatch) -> None:
    repo = _FakeRepo({"list_schedules": AsyncMock(return_value=[])})
    _patch_session(monkeypatch, repo)

    result = await tools.list_my_schedules.coroutine(  # type: ignore[attr-defined]
        args=ListMySchedulesInput(),
        runtime=_make_runtime(user_id=None),
    )

    assert result == "无法获取用户信息"
    repo._methods["list_schedules"].assert_not_awaited()


# ========== get_schedule ==========


async def test_get_schedule_returns_row_for_owner(monkeypatch) -> None:
    fake_row = SimpleNamespace(id="s9", user_id="u1", to_dict=lambda: {"id": "s9"})
    repo = _FakeRepo({"get_by_id_for_user": AsyncMock(return_value=fake_row)})
    _patch_session(monkeypatch, repo)

    result = await tools.get_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="s9",
        runtime=_make_runtime(user_id="u1"),
    )

    repo._methods["get_by_id_for_user"].assert_awaited_once_with("s9", "u1", is_admin=False)
    payload = json.loads(result)
    assert payload["id"] == "s9"


async def test_get_schedule_returns_friendly_error_for_other_user(monkeypatch) -> None:
    repo = _FakeRepo({"get_by_id_for_user": AsyncMock(return_value=None)})
    _patch_session(monkeypatch, repo)

    result = await tools.get_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="s9",
        runtime=_make_runtime(user_id="u2"),
    )

    assert result == "未找到该任务"


async def test_get_schedule_admin_can_read_others(monkeypatch) -> None:
    fake_row = SimpleNamespace(id="s9", user_id="u1", to_dict=lambda: {"id": "s9"})
    repo = _FakeRepo({"get_by_id_for_user": AsyncMock(return_value=fake_row)})
    _patch_session(monkeypatch, repo)

    # is_admin=True 时 _is_admin 走 runtime.context.is_admin 分支，不查 DB
    await tools.get_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="s9",
        runtime=_make_runtime(user_id="admin1", is_admin=True),
    )

    repo._methods["get_by_id_for_user"].assert_awaited_once_with("s9", "admin1", is_admin=True)
