from __future__ import annotations

from types import SimpleNamespace

from yuxi.services.mcp import server_service, tool_registry_service
from yuxi.services.mcp.client_pool import mcp_client_pool
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


def stdio_config(command: str, *, disabled_tools: list[str] | None = None) -> dict:
    return {"transport": "stdio", "command": command, "disabled_tools": disabled_tools or []}


def proxy_config(
    token: str,
    *,
    partition: str,
    disable_tool_object_cache: bool = False,
) -> dict:
    config = {
        "transport": "streamable_http",
        "url": "http://internal-api:5050/api/internal/mcp-proxy/demo",
        "headers": {INTERNAL_PROXY_TOKEN_HEADER: token},
        "__yuxi_cache_partition": partition,
        "__yuxi_allow_global_cache": False,
    }
    if disable_tool_object_cache:
        config["__yuxi_disable_tool_object_cache"] = True
    return config


def patch_enabled_config(monkeypatch, config: dict, *, expected_name: str = "demo") -> None:
    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == expected_name
        return config() if callable(config) else config

    monkeypatch.setattr(server_service, "get_enabled_mcp_server_config", fake_get_enabled_mcp_server_config)


def patch_server_config_loader(monkeypatch, server_configs: dict, *, loaded_names: list | None = None) -> None:
    async def fake_load_enabled_mcp_server_configs(*, names=None, db=None):
        del db
        if loaded_names is not None:
            loaded_names.append(names)
        if names is None:
            return server_configs
        return {name: server_configs[name] for name in names if name in server_configs}

    monkeypatch.setattr(server_service, "_load_enabled_mcp_server_configs", fake_load_enabled_mcp_server_configs)


def patch_recording_tool_loader(monkeypatch) -> list[tuple[str, dict[str, dict]]]:
    calls: list[tuple[str, dict[str, dict]]] = []

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, **kwargs):
        del kwargs
        calls.append((server_name, additional_servers or {}))
        return [server_name]

    monkeypatch.setattr(tool_registry_service, "get_mcp_tools", fake_get_mcp_tools)
    return calls


def patch_redis_tool_cache(monkeypatch) -> _FakeRedis:
    fake_redis = _FakeRedis()

    async def fake_redis_factory():
        return fake_redis

    monkeypatch.setattr(
        tool_registry_service,
        "_mcp_tool_cache_store",
        RedisMcpToolCache(redis_client_factory=fake_redis_factory),
    )
    return fake_redis


def tool_names(tools) -> list[str]:
    return [tool.name for tool in tools]


async def test_get_enabled_mcp_tools_loads_latest_config_from_db(monkeypatch):
    captured: list[dict] = []

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

    patch_enabled_config(monkeypatch, stdio_config("demo", disabled_tools=["tool_b"]))
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
    await tool_registry_service.clear_mcp_cache()

    configs = [stdio_config("demo-v1"), stdio_config("demo-v2")]
    build_calls: list[str] = []

    async def fake_get_mcp_client(server_configs):
        config = server_configs["demo"]
        build_calls.append(config["command"])
        tool = SimpleNamespace(name=f"tool_for_{config['command']}", metadata={})
        return _FakeClient([tool])

    patch_enabled_config(monkeypatch, lambda: configs[0])
    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fake_get_mcp_client)

    tools_v1_first = await tool_registry_service.get_mcp_tools("demo")
    tools_v1_second = await tool_registry_service.get_mcp_tools("demo")

    configs[0] = configs[1]
    tools_v2 = await tool_registry_service.get_mcp_tools("demo")

    assert [tool.name for tool in tools_v1_first] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v1_second] == ["tool_for_demo-v1"]
    assert [tool.name for tool in tools_v2] == ["tool_for_demo-v2"]
    assert build_calls == ["demo-v1", "demo-v2"]

    await tool_registry_service.clear_mcp_cache()


async def test_get_tools_from_all_servers_loads_names_from_db_once(monkeypatch):
    server_configs = {
        "alpha": stdio_config("cmd-a"),
        "beta": stdio_config("cmd-b"),
    }
    patch_server_config_loader(monkeypatch, server_configs)
    calls = patch_recording_tool_loader(monkeypatch)

    tools = await tool_registry_service.get_tools_from_all_servers()

    assert tools == ["alpha", "beta"]
    assert calls == [
        ("alpha", {"alpha": server_configs["alpha"]}),
        ("beta", {"beta": server_configs["beta"]}),
    ]


async def test_get_tools_from_all_servers_limits_preload_to_selected_names(monkeypatch):
    server_configs = {
        "alpha": stdio_config("cmd-a"),
        "beta": stdio_config("cmd-b"),
    }
    loaded_names: list[list[str] | None] = []
    patch_server_config_loader(monkeypatch, server_configs, loaded_names=loaded_names)
    calls = patch_recording_tool_loader(monkeypatch)

    tools = await tool_registry_service.get_tools_from_all_servers(["alpha", "alpha", "missing"])
    empty_tools = await tool_registry_service.get_tools_from_all_servers([])

    assert tools == ["alpha"]
    assert empty_tools == []
    assert loaded_names == [["alpha", "missing"]]
    assert calls == [("alpha", {"alpha": server_configs["alpha"]})]


async def test_get_mcp_tools_sets_handle_tool_error(monkeypatch):
    await tool_registry_service.clear_mcp_cache()

    async def fake_get_mcp_client(server_configs):
        tool = SimpleNamespace(name="demo_tool", metadata={})
        return _FakeClient([tool])

    patch_enabled_config(monkeypatch, stdio_config("demo-tool"))
    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fake_get_mcp_client)

    tools = await tool_registry_service.get_mcp_tools("demo")
    assert len(tools) == 1
    assert tools[0].handle_tool_error is True

    await tool_registry_service.clear_mcp_cache()


async def test_get_mcp_tools_suppresses_retries_during_failure_cooldown(monkeypatch):
    await tool_registry_service.clear_mcp_cache()

    config = stdio_config("offline-demo")
    build_calls: list[dict] = []

    async def fail_get_mcp_client(server_configs):
        build_calls.append(server_configs)
        raise ConnectionError("mcp service offline")

    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fail_get_mcp_client)

    tools_first = await tool_registry_service.get_mcp_tools("offline", additional_servers={"offline": config})
    tools_second = await tool_registry_service.get_mcp_tools("offline", additional_servers={"offline": config})
    tools_forced = await tool_registry_service.get_mcp_tools(
        "offline",
        additional_servers={"offline": config},
        force_refresh=True,
    )

    assert tools_first == []
    assert tools_second == []
    assert tools_forced == []
    assert len(build_calls) == 2

    await tool_registry_service.clear_mcp_cache()


async def test_get_mcp_tools_keeps_connection_partitions_separate(monkeypatch):
    await tool_registry_service.clear_mcp_cache()

    configs = [
        proxy_config("proxy-token-user-a", partition="connection:101"),
        proxy_config("proxy-token-user-b", partition="connection:202"),
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

    assert tool_names(tools_a) == ["tool_for_proxy-token-user-a"]
    assert tool_names(tools_b) == ["tool_for_proxy-token-user-b"]
    assert build_calls == ["proxy-token-user-a", "proxy-token-user-b"]

    await tool_registry_service.clear_mcp_cache()


async def test_get_mcp_tools_does_not_cache_internal_proxy_tool_objects(monkeypatch):
    await tool_registry_service.clear_mcp_cache()

    configs = [
        proxy_config("proxy-token-v1", partition="connection:101", disable_tool_object_cache=True),
        proxy_config("proxy-token-v2", partition="connection:101", disable_tool_object_cache=True),
    ]
    build_calls: list[str] = []
    tool_load_count = 0

    class RefreshingFakeClient:
        async def get_tools(self):
            nonlocal tool_load_count
            tool_load_count += 1
            tool = SimpleNamespace(name=f"tool_for_load_{tool_load_count}", metadata={})
            return [tool]

    async def fake_get_mcp_client(server_configs):
        token = server_configs["demo"]["headers"][INTERNAL_PROXY_TOKEN_HEADER]
        build_calls.append(token)
        return RefreshingFakeClient()

    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fake_get_mcp_client)

    tools_first = await tool_registry_service.get_mcp_tools("demo", additional_servers={"demo": configs[0]})
    tools_second = await tool_registry_service.get_mcp_tools("demo", additional_servers={"demo": configs[1]})

    assert tool_names(tools_first) == ["tool_for_load_1"]
    assert tool_names(tools_second) == ["tool_for_load_2"]
    assert build_calls == ["proxy-token-v1"]

    await tool_registry_service.clear_mcp_cache()


async def test_get_tools_from_all_servers_skips_runtime_auth_servers_without_context(monkeypatch):
    server_configs = {
        "shared": stdio_config("cmd-shared"),
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
    patch_server_config_loader(monkeypatch, server_configs)
    calls = patch_recording_tool_loader(monkeypatch)

    tools = await tool_registry_service.get_tools_from_all_servers()

    assert tools == ["shared"]
    assert calls == [
        ("shared", {"shared": server_configs["shared"]}),
    ]


async def test_get_mcp_tools_rebuilds_when_redis_server_revision_changes(monkeypatch):
    await tool_registry_service.clear_mcp_cache()

    patch_redis_tool_cache(monkeypatch)
    config = stdio_config("demo-tool")
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

    assert tool_names(tools_first) == ["tool_1"]
    assert tool_names(tools_second) == ["tool_1"]
    assert tool_names(tools_third) == ["tool_2"]
    assert build_calls == ["demo-tool", "demo-tool"]

    await tool_registry_service.clear_mcp_cache()


async def test_get_all_mcp_tools_uses_redis_manifest_when_local_cache_is_empty(monkeypatch):
    await tool_registry_service.clear_mcp_cache()

    patch_redis_tool_cache(monkeypatch)
    config = stdio_config("demo-tool")

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

    patch_enabled_config(monkeypatch, config)
    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fake_get_mcp_client)

    tools_first = await tool_registry_service.get_all_mcp_tools("demo")
    assert tool_names(tools_first) == ["alpha_tool"]

    await tool_registry_service.clear_mcp_cache()

    async def fail_get_mcp_client(server_configs):
        raise AssertionError(f"should not fetch live tools when redis manifest is available: {server_configs}")

    monkeypatch.setattr(mcp_client_pool, "_get_mcp_client", fail_get_mcp_client)

    tools_second = await tool_registry_service.get_all_mcp_tools("demo")

    assert tool_names(tools_second) == ["alpha_tool"]
    assert tools_second[0].metadata["id"] == "mcp__demo__alphaTool"
