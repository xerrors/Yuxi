from __future__ import annotations

from types import SimpleNamespace

import pytest

import yuxi.agents.middlewares.skills_middleware as skills_middleware
from yuxi.agents.middlewares.skills_middleware import SkillsMiddleware


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_mcp_tools_from_context_passes_auth_context_to_mcp_loader(monkeypatch: pytest.MonkeyPatch):
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

    tools = await middleware._get_mcp_tools_from_context(context)

    assert tools == []
    assert captured == [("finance-gateway", "user-1", "dept-9")]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_mcp_tools_from_context_uses_mcp_user_id_for_user_scoped_auth(monkeypatch: pytest.MonkeyPatch):
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
        mcp_user_id="login-1001",
        department_id="dept-9",
    )

    tools = await middleware._get_mcp_tools_from_context(context)

    assert tools == []
    assert captured == [("dts-mcp_server", "login-1001", "dept-9")]
