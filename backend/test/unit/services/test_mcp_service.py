from __future__ import annotations

import asyncio
from types import SimpleNamespace

from yuxi.services.mcp import tool_registry_service as mcp_service


class _FakeClient:
    def __init__(self, tools):
        self._tools = tools

    async def get_tools(self):
        return self._tools


async def test_get_enabled_mcp_tools_loads_latest_config_from_db(monkeypatch):
    captured: list[dict] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return {"transport": "stdio", "command": "demo", "disabled_tools": ["tool_b"]}

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, disabled_tools=None, **kwargs):
        del kwargs
        captured.append(
            {
                "server_name": server_name,
                "additional_servers": additional_servers,
                "disabled_tools": list(disabled_tools or []),
            }
        )
        return ["tool-a"]

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_enabled_mcp_tools("demo")

    assert tools == ["tool-a"]
    assert captured == [
        {
            "server_name": "demo",
            "additional_servers": {"demo": {"transport": "stdio", "command": "demo", "disabled_tools": ["tool_b"]}},
            "disabled_tools": ["tool_b"],
        }
    ]


async def test_get_enabled_mcp_tools_times_out_slow_runtime_discovery(monkeypatch):
    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "slow"
        return {"transport": "streamable_http", "url": "http://slow.example/mcp", "disabled_tools": []}

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, disabled_tools=None, **kwargs):
        del additional_servers, disabled_tools, kwargs
        assert server_name == "slow"
        await asyncio.sleep(0.05)
        return ["late-tool"]

    monkeypatch.setattr(mcp_service, "MCP_TOOLS_DISCOVERY_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_enabled_mcp_tools("slow")

    assert tools == []


async def test_get_mcp_tools_rebuilds_cache_when_config_hash_changes(monkeypatch):
    mcp_service.clear_mcp_cache()

    configs = [
        {"transport": "stdio", "command": "demo-v1", "disabled_tools": []},
        {"transport": "stdio", "command": "demo-v2", "disabled_tools": []},
    ]
    build_calls: list[str] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return configs[0]

    async def fake_get_mcp_client(server_configs):
        config = server_configs["demo"]
        build_calls.append(config["command"])
        tool = SimpleNamespace(name=f"tool_for_{config['command']}", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    tools_v1_first = await mcp_service.get_mcp_tools("demo")
    tools_v1_second = await mcp_service.get_mcp_tools("demo")

    configs[0] = configs[1]
    tools_v2 = await mcp_service.get_mcp_tools("demo")

    assert [tool.name for tool in tools_v1_first] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v1_second] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v2] == ["tool_for_demo-v2"]
    assert build_calls == ["demo-v1", "demo-v2"]

    mcp_service.clear_mcp_cache()


async def test_get_tools_from_all_servers_loads_names_from_db_once(monkeypatch):
    server_configs = {
        "alpha": {"transport": "stdio", "command": "cmd-a", "disabled_tools": []},
        "beta": {"transport": "stdio", "command": "cmd-b", "disabled_tools": []},
    }
    calls: list[tuple[str, dict[str, dict]]] = []

    async def fake_load_enabled_mcp_server_configs(*, names=None, db=None):
        del names, db
        return server_configs

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, **kwargs):
        del kwargs
        calls.append((server_name, additional_servers or {}))
        return [server_name]

    monkeypatch.setattr(mcp_service, "_load_enabled_mcp_server_configs", fake_load_enabled_mcp_server_configs)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_tools_from_all_servers()

    assert tools == ["alpha", "beta"]
    assert calls == [
        ("alpha", server_configs),
        ("beta", server_configs),
    ]


async def test_get_mcp_tools_sets_handle_tool_error(monkeypatch):
    mcp_service.clear_mcp_cache()

    config = {"transport": "stdio", "command": "demo-tool", "disabled_tools": []}

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        return config

    async def fake_get_mcp_client(server_configs):
        tool = SimpleNamespace(name="demo_tool", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)

    tools = await mcp_service.get_mcp_tools("demo")
    assert len(tools) == 1
    assert tools[0].handle_tool_error is True

    mcp_service.clear_mcp_cache()
