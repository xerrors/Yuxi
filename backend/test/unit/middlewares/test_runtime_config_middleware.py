from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage

import yuxi.agents.middlewares.runtime_config_middleware as runtime_config_middleware
from yuxi.agents.middlewares.runtime_config_middleware import RuntimeConfigMiddleware


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_tools_from_context_passes_auth_context_to_mcp_loader(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
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

    with caplog.at_level(logging.WARNING, logger="Yuxi"):
        tools = await middleware.get_tools_from_context(context)

    assert tools == []
    assert captured == [("finance-gateway", "user-1", "dept-9")]
    assert "mcp dependency unavailable" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_tools_from_context_uses_work_id_for_user_scoped_auth(monkeypatch: pytest.MonkeyPatch):
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
        mcps=["dts-mcp_server"],
        user_id="2",
        work_id="login-1001",
        department_id="dept-9",
    )

    tools = await middleware.get_tools_from_context(context)

    assert tools == []
    assert captured == [("dts-mcp_server", "2", "dept-9")]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runtime_loaded_mcp_tool_can_be_executed_when_tool_node_did_not_pre_register_it(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime_tool = SimpleNamespace(name="getTicket")

    monkeypatch.setattr(runtime_config_middleware, "get_all_tool_instances", lambda: [])

    async def fake_get_enabled_mcp_tools(server_name: str, *, auth_context=None, db=None, http_client=None):
        del auth_context, db, http_client
        assert server_name == "dts-mcp_server"
        return [runtime_tool]

    monkeypatch.setattr(runtime_config_middleware, "get_enabled_mcp_tools", fake_get_enabled_mcp_tools)

    middleware = RuntimeConfigMiddleware(enable_model_override=False, enable_system_prompt_override=False)
    context = SimpleNamespace(
        tools=[],
        mcps=["dts-mcp_server"],
        user_id="2",
        work_id="login-1001",
        department_id="dept-9",
        model=None,
        system_prompt="",
    )

    class DummyRequest:
        def __init__(self, tools=None):
            self.runtime = SimpleNamespace(context=context)
            self.tools = tools or []
            self.system_message = None

        def override(self, **kwargs):
            clone = DummyRequest(kwargs.get("tools", self.tools))
            clone.runtime = kwargs.get("runtime", self.runtime)
            clone.system_message = kwargs.get("system_message", self.system_message)
            return clone

    async def model_handler(next_request):
        return next_request.tools

    await middleware.awrap_model_call(DummyRequest(), model_handler)

    tool_request = ToolCallRequest(
        tool_call={"name": "getTicket", "args": {"arg0": "DTS2026012932159"}, "id": "call-1"},
        tool=None,
        state={},
        runtime=SimpleNamespace(context=context),
    )
    captured = {}

    async def tool_handler(next_request):
        captured["tool"] = next_request.tool
        return ToolMessage(content="ok", name=next_request.tool_call["name"], tool_call_id=next_request.tool_call["id"])

    result = await middleware.awrap_tool_call(tool_request, tool_handler)

    assert result.content == "ok"
    assert captured["tool"] is runtime_tool


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
