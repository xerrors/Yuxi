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


# ========== create_schedule ==========


async def test_create_schedule_succeeds_when_agent_belongs_to_user(monkeypatch) -> None:
    fake_schedule = SimpleNamespace(id="new-1", to_dict=lambda: {"id": "new-1", "name": "demo"})
    sched_repo = _FakeRepo({"create_schedule": AsyncMock(return_value=fake_schedule)})
    agent_repo = _FakeRepo({"get_by_id": AsyncMock(return_value=SimpleNamespace(user_id="u1"))})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    def _sched_factory(_s):
        return sched_repo

    def _agent_factory(_s):
        return agent_repo

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", _sched_factory)
    monkeypatch.setattr(tools, "AgentConfigRepository", _agent_factory)

    result = await tools.create_schedule.coroutine(  # type: ignore[attr-defined]
        name="demo",
        description=None,
        agent_config_id=42,
        cron_expr="0 * * * *",
        timezone="Asia/Shanghai",
        query="hi",
        image_content=None,
        config={},
        enabled=True,
        runtime=_make_runtime(user_id="u1"),
    )

    payload = json.loads(result)
    assert payload["id"] == "new-1"
    sched_repo._methods["create_schedule"].assert_awaited_once()


async def test_create_schedule_rejects_foreign_agent(monkeypatch) -> None:
    sched_repo = _FakeRepo({"create_schedule": AsyncMock()})
    agent_repo = _FakeRepo({"get_by_id": AsyncMock(return_value=SimpleNamespace(user_id="other_user"))})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: agent_repo)

    result = await tools.create_schedule.coroutine(  # type: ignore[attr-defined]
        name="demo",
        description=None,
        agent_config_id=42,
        cron_expr="0 * * * *",
        timezone="Asia/Shanghai",
        query="hi",
        image_content=None,
        config={},
        enabled=True,
        runtime=_make_runtime(user_id="u1"),
    )

    assert result == "无权使用该 agent"
    sched_repo._methods["create_schedule"].assert_not_awaited()


async def test_create_schedule_admin_bypasses_agent_ownership(monkeypatch) -> None:
    fake_schedule = SimpleNamespace(id="new-2", to_dict=lambda: {"id": "new-2"})
    sched_repo = _FakeRepo({"create_schedule": AsyncMock(return_value=fake_schedule)})
    # admin 路径下不应调用 AgentConfigRepository
    agent_repo = _FakeRepo({"get_by_id": AsyncMock()})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: agent_repo)

    await tools.create_schedule.coroutine(  # type: ignore[attr-defined]
        name="demo",
        description=None,
        agent_config_id=42,
        cron_expr="0 * * * *",
        timezone="Asia/Shanghai",
        query="hi",
        image_content=None,
        config={},
        enabled=True,
        runtime=_make_runtime(user_id="admin1", is_admin=True),
    )

    agent_repo._methods["get_by_id"].assert_not_awaited()
    sched_repo._methods["create_schedule"].assert_awaited_once()


# ========== update_schedule ==========


async def test_update_schedule_rejects_foreign_agent(monkeypatch) -> None:
    sched_repo = _FakeRepo({"get_by_id_for_user": AsyncMock(return_value=SimpleNamespace(id="sx"))})
    agent_repo = _FakeRepo({"get_by_id": AsyncMock(return_value=SimpleNamespace(user_id="other"))})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: agent_repo)

    result = await tools.update_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        name=None,
        description=None,
        agent_config_id=99,
        cron_expr=None,
        timezone=None,
        query=None,
        image_content=None,
        config=None,
        enabled=None,
        runtime=_make_runtime(user_id="u1"),
    )

    assert result == "无权使用该 agent"
    sched_repo._methods["get_by_id_for_user"].assert_not_awaited()  # 校验在 update 前


async def test_update_schedule_succeeds_when_owner_and_agent_match(monkeypatch) -> None:
    existing = SimpleNamespace(id="sx", user_id="u1", to_dict=lambda: {"id": "sx"})
    updated = SimpleNamespace(id="sx", name="new", to_dict=lambda: {"id": "sx", "name": "new"})
    sched_repo = _FakeRepo(
        {
            "get_by_id_for_user": AsyncMock(return_value=existing),
            "update_for_user": AsyncMock(return_value=updated),
        }
    )
    agent_repo = _FakeRepo({"get_by_id": AsyncMock(return_value=SimpleNamespace(user_id="u1"))})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: agent_repo)

    result = await tools.update_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        name="new",
        description=None,
        agent_config_id=42,
        cron_expr=None,
        timezone=None,
        query=None,
        image_content=None,
        config=None,
        enabled=None,
        runtime=_make_runtime(user_id="u1"),
    )

    payload = json.loads(result)
    assert payload["name"] == "new"
    sched_repo._methods["update_for_user"].assert_awaited_once()


async def test_update_schedule_skips_agent_check_when_agent_id_not_provided(monkeypatch) -> None:
    """update_schedule 若 agent_config_id=None，应跳过 _check_agent_ownership。"""
    existing = SimpleNamespace(id="sx", user_id="u1", to_dict=lambda: {"id": "sx"})
    updated = SimpleNamespace(id="sx", name="x", to_dict=lambda: {"id": "sx", "name": "x"})
    sched_repo = _FakeRepo(
        {
            "get_by_id_for_user": AsyncMock(return_value=existing),
            "update_for_user": AsyncMock(return_value=updated),
        }
    )
    agent_repo = _FakeRepo({"get_by_id": AsyncMock()})

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    monkeypatch.setattr(tools, "pg_manager", MagicMock(get_async_session_context=_ctx))
    monkeypatch.setattr(tools, "ScheduleRepository", lambda _s: sched_repo)
    monkeypatch.setattr(tools, "AgentConfigRepository", lambda _s: agent_repo)

    result = await tools.update_schedule.coroutine(  # type: ignore[attr-defined]
        schedule_id="sx",
        name="x",
        description=None,
        agent_config_id=None,
        cron_expr=None,
        timezone=None,
        query=None,
        image_content=None,
        config=None,
        enabled=None,
        runtime=_make_runtime(user_id="u1"),
    )

    assert json.loads(result)["name"] == "x"
    agent_repo._methods["get_by_id"].assert_not_awaited()
