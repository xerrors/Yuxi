from __future__ import annotations

from types import SimpleNamespace

from yuxi.services.mcp import connection_service, server_service, tool_registry_service
from yuxi.services.mcp.client_pool import mcp_client_pool
from yuxi.services.mcp_auth.redis_token_cache import RedisTokenCache
from yuxi.services.mcp_tool_cache import RedisMcpToolCache
from yuxi.services.mcp_auth.proxy_service import INTERNAL_PROXY_TOKEN_HEADER


class _FakeClient:
    def __init__(self, tools):
        self._tools = tools

    async def get_tools(self):
        return self._tools


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.expire_calls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        if ex is not None:
            self.expire_calls[key] = ex

    async def incr(self, key: str) -> int:
        next_value = int(self.data.get(key) or "0") + 1
        self.data[key] = str(next_value)
        return next_value


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

    monkeypatch.setattr(server_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(tool_registry_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await tool_registry_service.get_enabled_mcp_tools("demo")

    assert tools == ["tool-a"]
    assert captured == [
        {
            "server_name": "demo",
            "additional_servers": {"demo": {"transport": "stdio", "command": "demo", "disabled_tools": ["tool_b"]}},
            "disabled_tools": ["tool_b"],
        }
    ]


async def test_get_mcp_tools_rebuilds_cache_when_config_hash_changes(monkeypatch):
    tool_registry_service.clear_mcp_cache()

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

    monkeypatch.setattr(server_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fake_get_mcp_client)

    tools_v1_first = await tool_registry_service.get_mcp_tools("demo")
    tools_v1_second = await tool_registry_service.get_mcp_tools("demo")

    configs[0] = configs[1]
    tools_v2 = await tool_registry_service.get_mcp_tools("demo")

    assert [tool.name for tool in tools_v1_first] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v1_second] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v2] == ["tool_for_demo-v2"]
    assert build_calls == ["demo-v1", "demo-v2"]

    tool_registry_service.clear_mcp_cache()


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

    monkeypatch.setattr(server_service, "_load_enabled_mcp_server_configs", fake_load_enabled_mcp_server_configs)
    monkeypatch.setattr(tool_registry_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await tool_registry_service.get_tools_from_all_servers()

    assert tools == ["alpha", "beta"]
    assert calls == [
        ("alpha", {"alpha": server_configs["alpha"]}),
        ("beta", {"beta": server_configs["beta"]}),
    ]


async def test_get_mcp_tools_sets_handle_tool_error(monkeypatch):
    tool_registry_service.clear_mcp_cache()

    config = {"transport": "stdio", "command": "demo-tool", "disabled_tools": []}

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        return config

    async def fake_get_mcp_client(server_configs):
        tool = SimpleNamespace(name="demo_tool", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(server_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fake_get_mcp_client)

    tools = await tool_registry_service.get_mcp_tools("demo")
    assert len(tools) == 1
    assert tools[0].handle_tool_error is True

    tool_registry_service.clear_mcp_cache()


async def test_get_mcp_tools_keeps_connection_partitions_separate(monkeypatch):
    tool_registry_service.clear_mcp_cache()

    configs = [
        {
            "transport": "streamable_http",
            "url": "http://internal-api:5050/api/internal/mcp-proxy/demo",
            "headers": {
                INTERNAL_PROXY_TOKEN_HEADER: "proxy-token-user-a",
            },
            "__yuxi_cache_partition": "connection:101",
            "__yuxi_allow_global_cache": False,
        },
        {
            "transport": "streamable_http",
            "url": "http://internal-api:5050/api/internal/mcp-proxy/demo",
            "headers": {
                INTERNAL_PROXY_TOKEN_HEADER: "proxy-token-user-b",
            },
            "__yuxi_cache_partition": "connection:202",
            "__yuxi_allow_global_cache": False,
        },
    ]
    build_calls: list[str] = []

    async def fake_get_mcp_client(server_configs):
        token = server_configs["demo"]["headers"][INTERNAL_PROXY_TOKEN_HEADER]
        build_calls.append(token)
        tool = SimpleNamespace(name=f"tool_for_{token}", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fake_get_mcp_client)

    tools_a = await tool_registry_service.get_mcp_tools("demo", additional_servers={"demo": configs[0]})
    tools_b = await tool_registry_service.get_mcp_tools("demo", additional_servers={"demo": configs[1]})

    assert [tool.name for tool in tools_a] == ["tool_for_proxy-token-user-a"]
    assert [tool.name for tool in tools_b] == ["tool_for_proxy-token-user-b"]
    assert build_calls == ["proxy-token-user-a", "proxy-token-user-b"]

    tool_registry_service.clear_mcp_cache()


async def test_get_mcp_tools_does_not_cache_internal_proxy_tool_objects(monkeypatch):
    tool_registry_service.clear_mcp_cache()

    configs = [
        {
            "transport": "streamable_http",
            "url": "http://internal-api:5050/api/internal/mcp-proxy/demo",
            "headers": {
                INTERNAL_PROXY_TOKEN_HEADER: "proxy-token-v1",
            },
            "__yuxi_cache_partition": "connection:101",
            "__yuxi_allow_global_cache": False,
            "__yuxi_disable_tool_object_cache": True,
        },
        {
            "transport": "streamable_http",
            "url": "http://internal-api:5050/api/internal/mcp-proxy/demo",
            "headers": {
                INTERNAL_PROXY_TOKEN_HEADER: "proxy-token-v2",
            },
            "__yuxi_cache_partition": "connection:101",
            "__yuxi_allow_global_cache": False,
            "__yuxi_disable_tool_object_cache": True,
        },
    ]
    build_calls: list[str] = []

    async def fake_get_mcp_client(server_configs):
        token = server_configs["demo"]["headers"][INTERNAL_PROXY_TOKEN_HEADER]
        build_calls.append(token)
        tool = SimpleNamespace(name=f"tool_for_{token}", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fake_get_mcp_client)

    tools_first = await tool_registry_service.get_mcp_tools("demo", additional_servers={"demo": configs[0]})
    tools_second = await tool_registry_service.get_mcp_tools("demo", additional_servers={"demo": configs[1]})

    assert [tool.name for tool in tools_first] == ["tool_for_proxy-token-v1"]
    assert [tool.name for tool in tools_second] == ["tool_for_proxy-token-v2"]
    assert build_calls == ["proxy-token-v1", "proxy-token-v2"]

    tool_registry_service.clear_mcp_cache()


async def test_get_tools_from_all_servers_skips_runtime_auth_servers_without_context(monkeypatch):
    server_configs = {
        "shared": {"transport": "stdio", "command": "cmd-shared", "disabled_tools": []},
        "bound": {
            "transport": "streamable_http",
            "url": "http://bound.local/mcp",
            "auth_config": {
                "version": 1,
                "provider": "custom_http_token",
                "binding_scope": "department",
                "inject": {
                    "target": "headers",
                    "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
                },
                "token_request": {
                    "url": "http://bound.local/token",
                    "method": "POST",
                    "response_map": {"access_token": "access_token"},
                },
            },
            "disabled_tools": [],
        },
    }
    calls: list[tuple[str, dict[str, dict]]] = []

    async def fake_load_enabled_mcp_server_configs(*, names=None, db=None):
        del names, db
        return server_configs

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, **kwargs):
        del kwargs
        calls.append((server_name, additional_servers or {}))
        return [server_name]

    monkeypatch.setattr(server_service, "_load_enabled_mcp_server_configs", fake_load_enabled_mcp_server_configs)
    monkeypatch.setattr(tool_registry_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await tool_registry_service.get_tools_from_all_servers()

    assert tools == ["shared"]
    assert calls == [
        ("shared", {"shared": server_configs["shared"]}),
    ]


async def test_get_mcp_tools_rebuilds_when_redis_server_revision_changes(monkeypatch):
    tool_registry_service.clear_mcp_cache()

    fake_redis = _FakeRedis()

    async def fake_redis_factory():
        return fake_redis

    monkeypatch.setattr(tool_registry_service, "_mcp_tool_cache_store",
        RedisMcpToolCache(redis_client_factory=fake_redis_factory),
    )

    config = {"transport": "stdio", "command": "demo-tool", "disabled_tools": []}
    build_calls: list[str] = []

    async def fake_get_mcp_client(server_configs):
        build_calls.append(server_configs["demo"]["command"])
        tool = SimpleNamespace(name=f"tool_{len(build_calls)}", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fake_get_mcp_client)

    tools_first = await tool_registry_service.get_mcp_tools("demo", additional_servers={"demo": config})
    tools_second = await tool_registry_service.get_mcp_tools("demo", additional_servers={"demo": config})
    await tool_registry_service._mcp_tool_cache_store.bump_server_revision("demo")
    tools_third = await tool_registry_service.get_mcp_tools("demo", additional_servers={"demo": config})

    assert [tool.name for tool in tools_first] == ["tool_1"]
    assert [tool.name for tool in tools_second] == ["tool_1"]
    assert [tool.name for tool in tools_third] == ["tool_2"]
    assert build_calls == ["demo-tool", "demo-tool"]

    tool_registry_service.clear_mcp_cache()


async def test_get_all_mcp_tools_uses_redis_manifest_when_local_cache_is_empty(monkeypatch):
    tool_registry_service.clear_mcp_cache()

    fake_redis = _FakeRedis()

    async def fake_redis_factory():
        return fake_redis

    monkeypatch.setattr(tool_registry_service, "_mcp_tool_cache_store",
        RedisMcpToolCache(redis_client_factory=fake_redis_factory),
    )

    config = {"transport": "stdio", "command": "demo-tool", "disabled_tools": []}

    async def fake_get_mcp_client(server_configs):
        del server_configs
        tool = SimpleNamespace(
            name="alpha_tool",
            description="alpha",
            metadata={},
            args_schema=SimpleNamespace(
                schema=lambda: {
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                }
            ),
        )
        return _FakeClient([tool])

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del server_name, db
        return config

    monkeypatch.setattr(server_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)
    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fake_get_mcp_client)

    tools_first = await tool_registry_service.get_all_mcp_tools("demo")
    assert [tool.name for tool in tools_first] == ["alpha_tool"]

    tool_registry_service.clear_mcp_cache()

    async def fail_get_mcp_client(server_configs):
        raise AssertionError(f"should not fetch live tools when redis manifest is available: {server_configs}")

    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fail_get_mcp_client)

    tools_second = await tool_registry_service.get_all_mcp_tools("demo")

    assert [tool.name for tool in tools_second] == ["alpha_tool"]
    assert tools_second[0].metadata["id"] == "mcp__demo__alphaTool"
