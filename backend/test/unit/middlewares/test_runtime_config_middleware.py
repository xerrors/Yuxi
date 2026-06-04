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


@pytest.mark.asyncio
@pytest.mark.unit
async def test_awrap_model_call_appends_runtime_loaded_mcp_tools(monkeypatch: pytest.MonkeyPatch):
    runtime_tool = SimpleNamespace(name="mcp__financeGateway__query", metadata={})

    monkeypatch.setattr(runtime_config_middleware, "get_all_tool_instances", lambda: [])

    async def fake_get_enabled_mcp_tools(server_name: str, *, auth_context=None, db=None, http_client=None):
        del db, http_client
        assert server_name == "finance-gateway"
        assert auth_context.user_id == "user-1"
        assert auth_context.department_id == "dept-9"
        return [runtime_tool]

    monkeypatch.setattr(runtime_config_middleware, "get_enabled_mcp_tools", fake_get_enabled_mcp_tools)

    class DummyRequest:
        def __init__(self):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    tools=[],
                    mcps=["finance-gateway"],
                    user_id="user-1",
                    department_id="dept-9",
                    model=None,
                    system_prompt="",
                )
            )
            self.tools = []
            self.system_message = None

        def override(self, **kwargs):
            clone = DummyRequest()
            clone.runtime = kwargs.get("runtime", self.runtime)
            clone.tools = kwargs.get("tools", self.tools)
            clone.system_message = kwargs.get("system_message", self.system_message)
            return clone

    middleware = RuntimeConfigMiddleware(enable_model_override=False, enable_system_prompt_override=False)
    request = DummyRequest()

    async def handler(next_request):
        return next_request.tools

    tools = await middleware.awrap_model_call(request, handler)

    assert tools == [runtime_tool]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_awrap_model_call_replaces_stale_managed_tool_with_fresh_runtime_tool(monkeypatch: pytest.MonkeyPatch):
    stale_tool = SimpleNamespace(name="mcp__financeGateway__query", metadata={"version": "stale"})
    fresh_tool = SimpleNamespace(name="mcp__financeGateway__query", metadata={"version": "fresh"})

    monkeypatch.setattr(runtime_config_middleware, "get_all_tool_instances", lambda: [])

    async def fake_get_enabled_mcp_tools(server_name: str, *, auth_context=None, db=None, http_client=None):
        del db, http_client
        assert server_name == "finance-gateway"
        assert auth_context.user_id == "user-1"
        assert auth_context.department_id == "dept-9"
        return [fresh_tool]

    monkeypatch.setattr(runtime_config_middleware, "get_enabled_mcp_tools", fake_get_enabled_mcp_tools)

    class DummyRequest:
        def __init__(self, tools):
            self.runtime = SimpleNamespace(
                context=SimpleNamespace(
                    tools=[],
                    mcps=["finance-gateway"],
                    user_id="user-1",
                    department_id="dept-9",
                    model=None,
                    system_prompt="",
                )
            )
            self.tools = tools
            self.system_message = None

        def override(self, **kwargs):
            clone = DummyRequest(kwargs.get("tools", self.tools))
            clone.runtime = kwargs.get("runtime", self.runtime)
            clone.system_message = kwargs.get("system_message", self.system_message)
            return clone

    middleware = RuntimeConfigMiddleware(enable_model_override=False, enable_system_prompt_override=False)
    middleware.tools = [stale_tool]
    request = DummyRequest([stale_tool])

    async def handler(next_request):
        return next_request.tools

    tools = await middleware.awrap_model_call(request, handler)

    assert tools == [fresh_tool]
