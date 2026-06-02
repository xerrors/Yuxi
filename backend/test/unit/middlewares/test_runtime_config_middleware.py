from __future__ import annotations

from types import SimpleNamespace

import pytest

import yuxi.agents.middlewares.runtime_config_middleware as runtime_config_middleware
from yuxi.agents.middlewares.runtime_config_middleware import RuntimeConfigMiddleware


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_tools_from_context_passes_auth_context_to_mcp_loader(monkeypatch: pytest.MonkeyPatch):
    captured: list[tuple[str, str | None, str | None]] = []

    monkeypatch.setattr(runtime_config_middleware, "get_all_tool_instances", lambda: [])

    async def fake_get_enabled_mcp_tools(server_name: str, *, auth_context=None, db=None, http_client=None):
        del db, http_client
        captured.append((server_name, auth_context.user_id, auth_context.department_id))
        return []

    monkeypatch.setattr(runtime_config_middleware, "get_enabled_mcp_tools", fake_get_enabled_mcp_tools)

    middleware = RuntimeConfigMiddleware()
    context = SimpleNamespace(
        tools=[],
        mcps=["finance-gateway"],
        user_id="user-1",
        department_id="dept-9",
    )

    tools = await middleware.get_tools_from_context(context)

    assert tools == []
    assert captured == [("finance-gateway", "user-1", "dept-9")]
