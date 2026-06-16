from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import yuxi.agents.middlewares.skills_middleware as skills_middleware
from yuxi.agents.middlewares.skills_middleware import SkillsMiddleware, collect_context_mcp_names_for_preload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_mcp_tools_from_context_passes_auth_context_to_mcp_loader(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    captured: list[tuple[str, str | None, str | None]] = []

    async def fake_get_enabled_mcp_tools(server_name: str, *, auth_context=None, db=None, http_client=None):
        del db, http_client
        captured.append((server_name, auth_context.user_id, auth_context.department_id))
        return []

    monkeypatch.setattr(skills_middleware, "get_enabled_mcp_tools", fake_get_enabled_mcp_tools)

    middleware = SkillsMiddleware()
    context = SimpleNamespace(
        mcps=["finance-gateway"],
        user_id="user-1",
        department_id="dept-9",
    )

    with caplog.at_level(logging.WARNING, logger="Yuxi"):
        tools = await middleware._get_mcp_tools_from_context(context)

    assert tools == []
    assert captured == [("finance-gateway", "user-1", "dept-9")]
    assert "mcp dependency unavailable" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_mcp_tools_from_context_uses_work_id_for_user_scoped_auth(monkeypatch: pytest.MonkeyPatch):
    captured: list[tuple[str, str | None, str | None]] = []

    async def fake_get_enabled_mcp_tools(server_name: str, *, auth_context=None, db=None, http_client=None):
        del db, http_client
        captured.append((server_name, auth_context.user_id, auth_context.department_id))
        return []

    monkeypatch.setattr(skills_middleware, "get_enabled_mcp_tools", fake_get_enabled_mcp_tools)

    middleware = SkillsMiddleware()
    context = SimpleNamespace(
        mcps=["dts-mcp_server"],
        user_id="2",
        work_id="login-1001",
        department_id="dept-9",
    )

    tools = await middleware._get_mcp_tools_from_context(context)

    assert tools == []
    assert captured == [("dts-mcp_server", "2", "dept-9")]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_collect_context_mcp_names_for_preload_includes_configured_skill_dependencies(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_get_dependency_map(db=None):
        del db
        return {
            "reporter": {"tools": [], "mcps": ["charts"], "skills": ["common"]},
            "common": {"tools": [], "mcps": ["finance-gateway"], "skills": []},
        }

    monkeypatch.setattr(skills_middleware, "get_dependency_map", fake_get_dependency_map)

    context = SimpleNamespace(mcps=["direct", "charts"], skills=["reporter"])

    names = await collect_context_mcp_names_for_preload(context)

    assert names == ["direct", "charts", "finance-gateway"]
