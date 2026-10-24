from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.agents.mcp import service as mcp_service
from yuxi.storage.postgres import manager as postgres_manager
from yuxi.storage.postgres.models_business import MCPServer


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class _FailingSessionContext:
    async def __aenter__(self):
        raise RuntimeError("database-secret-must-not-be-swallowed")

    async def __aexit__(self, *_args):
        return False


@pytest_asyncio.fixture
async def mcp_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(MCPServer.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


class _FakeClient:
    def __init__(self, tools):
        self._tools = tools

    async def get_tools(self):
        return self._tools


@pytest.fixture(autouse=True)
def mcp_fake_redis(monkeypatch):
    """本文件所有单测注入内存失效代数客户端；用例内再次 setattr 可覆盖为专用替身。"""
    client = _VersionRedis()
    monkeypatch.setattr(mcp_service, "get_async_redis_client", _fake_redis_client(client))
    return client


async def test_ensure_builtin_mcp_servers_removes_retired_system_server(monkeypatch, mcp_session):
    retired_server = MCPServer(
        slug="sequentialthinking",
        name="sequentialthinking",
        description="old builtin",
        transport="streamable_http",
        url="https://remote.mcpservers.org/sequentialthinking/mcp",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(retired_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    retired = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "sequentialthinking"))
    deepwiki = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "deepwiki-official"))
    assert retired is None
    assert deepwiki is not None


async def test_ensure_builtin_invalidates_after_commit(monkeypatch, mcp_session):
    """跨进程失效通知必须晚于事务 commit point，避免其他进程按未提交状态重建快照。"""
    retired_server = MCPServer(
        slug="sequentialthinking",
        name="sequentialthinking",
        description="old builtin",
        transport="streamable_http",
        url="https://remote.mcpservers.org/sequentialthinking/mcp",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(retired_server)
    await mcp_session.commit()

    events: list[str] = []

    class _RecordingRedis:
        async def get(self, key: str):
            return None

        async def incr(self, key: str):
            events.append("incr")
            return 1

    real_commit = mcp_session.commit

    async def logged_commit():
        events.append("commit")
        await real_commit()

    monkeypatch.setattr(mcp_session, "commit", logged_commit)
    monkeypatch.setattr(mcp_service, "get_async_redis_client", _fake_redis_client(_RecordingRedis()))
    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    assert events[0] == "commit"
    assert "incr" in events


@pytest.mark.parametrize("slug", ["sequentialthinking", "deepwiki"])
async def test_ensure_builtin_mcp_servers_preserves_user_slug(monkeypatch, mcp_session, slug):
    user_server = MCPServer(
        slug=slug,
        name="用户自定义 MCP",
        description="user managed",
        transport="streamable_http",
        url="https://example.com/mcp",
        enabled=1,
        created_by="admin",
        updated_by="admin",
    )
    mcp_session.add(user_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == slug))
    assert server is not None
    assert server.created_by == "admin"
    assert server.url == "https://example.com/mcp"
    assert server.enabled == 1
    assert not mcp_service.is_builtin_mcp_server(server)
    configs = await mcp_service._load_enabled_mcp_server_configs(db=mcp_session)
    assert configs[slug]["url"] == "https://example.com/mcp"


async def test_ensure_builtin_mcp_servers_disables_legacy_user_stdio(monkeypatch, mcp_session):
    legacy_server = MCPServer(
        slug="legacy-stdio",
        name="历史 stdio",
        transport="stdio",
        command="python3",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(legacy_server)
    await mcp_session.commit()

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _AsyncSessionContext(mcp_session),
    )

    await mcp_service.ensure_builtin_mcp_servers_in_db()

    await mcp_session.refresh(legacy_server)
    assert legacy_server.enabled == 0


async def test_builtin_mcp_initialization_propagates_failure_to_entrypoint(monkeypatch):
    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _FailingSessionContext(),
    )

    with pytest.raises(RuntimeError, match="must-not-be-swallowed"):
        await mcp_service.ensure_builtin_mcp_servers_in_db()


async def test_runtime_configs_exclude_user_created_stdio_servers(mcp_session):
    mcp_session.add_all(
        [
            MCPServer(
                slug="mcp-server-chart",
                name="内置 stdio",
                transport="stdio",
                command="tampered-command",
                args=["--unsafe"],
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="user-stdio",
                name="用户 stdio",
                transport="stdio",
                command="python3",
                args=["-c", "print('unsafe')"],
                enabled=1,
                created_by="admin",
                updated_by="admin",
            ),
            MCPServer(
                slug="forged-system-stdio",
                name="伪造系统 stdio",
                transport="stdio",
                command="python3",
                args=["-c", "print('unsafe')"],
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="remote-http",
                name="远程 HTTP",
                transport="streamable_http",
                url="https://example.com/mcp",
                command="python3",
                args=["-c", "print('stale')"],
                enabled=1,
                created_by="admin",
                updated_by="admin",
            ),
        ]
    )
    await mcp_session.commit()

    configs = await mcp_service._load_enabled_mcp_server_configs(db=mcp_session)
    slugs = await mcp_service.get_enabled_mcp_server_slugs(db=mcp_session)

    assert set(configs) == {"remote-http"}
    assert set(slugs) == {"remote-http"}
    assert "command" not in configs["remote-http"]
    assert "args" not in configs["remote-http"]


async def test_create_mcp_server_rejects_user_created_stdio(mcp_session):
    with pytest.raises(ValueError, match="stdio"):
        await mcp_service.create_mcp_server(
            mcp_session,
            slug="unsafe-mcp",
            name="Unsafe MCP",
            transport="stdio",
            created_by="admin",
        )

    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "unsafe-mcp"))
    assert server is None


async def test_create_mcp_server_rejects_builtin_slug(mcp_session):
    with pytest.raises(ValueError, match="slug"):
        await mcp_service.create_mcp_server(
            mcp_session,
            slug="deepwiki-official",
            name="伪造内置 MCP",
            transport="streamable_http",
            url="https://example.com/mcp",
            created_by="admin",
        )


async def test_update_builtin_mcp_server_rejects_connection_changes(mcp_session):
    server = MCPServer(
        slug="deepwiki-official",
        name="内置 stdio",
        transport="stdio",
        command="trusted-command",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(server)
    await mcp_session.commit()

    with pytest.raises(PermissionError, match="系统内置"):
        await mcp_service.update_mcp_server(
            mcp_session,
            slug="deepwiki-official",
            transport="streamable_http",
            url="https://example.com/mcp",
            updated_by="admin",
        )

    await mcp_session.refresh(server)
    assert server.transport == "stdio"
    assert server.command == "trusted-command"


async def test_update_legacy_stdio_requires_remote_url(mcp_session):
    legacy_server = MCPServer(
        slug="legacy-stdio",
        name="历史 stdio",
        transport="stdio",
        command="python3",
        enabled=0,
        created_by="admin",
        updated_by="admin",
    )
    mcp_session.add(legacy_server)
    await mcp_session.commit()

    with pytest.raises(ValueError, match="url 必填"):
        await mcp_service.update_mcp_server(
            mcp_session,
            slug="legacy-stdio",
            transport="streamable_http",
            updated_by="admin",
        )

    await mcp_session.refresh(legacy_server)
    assert legacy_server.transport == "stdio"
    assert legacy_server.command == "python3"


async def test_get_enabled_mcp_tools_loads_latest_config_from_db(monkeypatch):
    captured: list[dict] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return {"transport": "streamable_http", "url": "demo", "disabled_tools": ["tool_b"]}

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
            "additional_servers": {
                "demo": {"transport": "streamable_http", "url": "demo", "disabled_tools": ["tool_b"]}
            },
            "disabled_tools": ["tool_b"],
        }
    ]


async def test_get_mcp_tools_rebuilds_cache_when_config_hash_changes(monkeypatch):
    mcp_service.clear_mcp_cache()

    configs = [
        {"transport": "streamable_http", "url": "demo-v1", "disabled_tools": []},
        {"transport": "streamable_http", "url": "demo-v2", "disabled_tools": []},
    ]
    build_calls: list[str] = []

    async def fake_get_enabled_mcp_server_config(server_name: str, db=None):
        del db
        assert server_name == "demo"
        return configs[0]

    async def fake_get_mcp_client(server_configs):
        config = server_configs["demo"]
        build_calls.append(config["url"])
        tool = SimpleNamespace(name=f"tool_for_{config['url']}", metadata={})
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


class _VersionRedis:
    """内存版失效代数客户端，支持 get/incr，供单测隔离真实 Redis。"""

    def __init__(self):
        self.values: dict[str, str] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def incr(self, key: str):
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value


def _fake_redis_client(client):
    """把已建好的替身包装成 get_async_redis_client。"""

    async def _get_client():
        return client

    return _get_client


async def _stub_demo(monkeypatch, remote_tools, build_calls):
    """stub demo server 的配置与建连；remote_tools 为可变列表以模拟远端 tool 集演化。"""

    async def fake_config(server_name: str, db=None):
        del server_name, db
        return {"transport": "streamable_http", "url": "demo", "disabled_tools": []}

    async def fake_get_mcp_client(server_configs):
        del server_configs
        build_calls.append("build")
        return _FakeClient([SimpleNamespace(name=name, metadata={}) for name in remote_tools])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_server_config", fake_config)
    monkeypatch.setattr(mcp_service, "get_mcp_client", fake_get_mcp_client)


async def test_get_mcp_tools_rebuilds_after_other_process_invalidates(monkeypatch):
    """远端新增 tool 不改 config_hash 时，其他进程失效（版本号递增）必须使本进程重建。"""
    mcp_service.clear_mcp_cache()
    fake_redis = _VersionRedis()
    monkeypatch.setattr(mcp_service, "get_async_redis_client", _fake_redis_client(fake_redis))
    remote_tools = ["tool_1"]
    build_calls: list[str] = []
    await _stub_demo(monkeypatch, remote_tools, build_calls)

    first = await mcp_service.get_mcp_tools("demo")
    assert [tool.name for tool in first] == ["tool_1"]

    remote_tools.append("tool_2")
    cached = await mcp_service.get_mcp_tools("demo")
    assert [tool.name for tool in cached] == ["tool_1"]

    # 模拟另一进程（API）管理端刷新：只递增全局代数，不动本进程 dict
    await fake_redis.incr(mcp_service.MCP_TOOLS_VERSION_KEY)

    refreshed = await mcp_service.get_mcp_tools("demo")
    assert sorted(tool.name for tool in refreshed) == ["tool_1", "tool_2"]
    assert len(build_calls) == 2

    mcp_service.clear_mcp_cache()


async def test_invalidate_mcp_tools_cache_bumps_version_and_clears_local(monkeypatch):
    mcp_service.clear_mcp_cache()
    fake_redis = _VersionRedis()
    monkeypatch.setattr(mcp_service, "get_async_redis_client", _fake_redis_client(fake_redis))
    build_calls: list[str] = []
    await _stub_demo(monkeypatch, ["tool_1"], build_calls)

    await mcp_service.get_mcp_tools("demo")
    assert fake_redis.values.get(mcp_service.MCP_TOOLS_VERSION_KEY) is None

    # 自失效只清该 slug，其他 server 的本地快照保留
    mcp_service._mcp_tools_cache["other:deadbeef"] = [SimpleNamespace(name="other_tool")]
    await mcp_service.invalidate_mcp_tools_cache("demo")
    assert fake_redis.values[mcp_service.MCP_TOOLS_VERSION_KEY] == "1"
    assert mcp_service._mcp_tools_cache_generation == 1
    assert all(key.startswith("other:") for key in mcp_service._mcp_tools_cache)

    mcp_service.clear_mcp_cache()


async def test_mcp_tools_cache_degrades_when_redis_unavailable(monkeypatch):
    """Redis 不可读时保留进程内缓存（与改造前行为一致），失效调用不抛异常。"""
    mcp_service.clear_mcp_cache()

    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(mcp_service, "get_async_redis_client", _boom)
    remote_tools = ["tool_1"]
    build_calls: list[str] = []
    await _stub_demo(monkeypatch, remote_tools, build_calls)

    await mcp_service.get_mcp_tools("demo")
    remote_tools.append("tool_2")
    cached = await mcp_service.get_mcp_tools("demo")
    assert [tool.name for tool in cached] == ["tool_1"]
    assert len(build_calls) == 1

    await mcp_service.invalidate_mcp_tools_cache("demo")
    assert mcp_service._mcp_tools_cache == {}

    mcp_service.clear_mcp_cache()


async def test_get_tools_from_all_servers_loads_names_from_db_once(monkeypatch):
    server_configs = {
        "alpha": {"transport": "streamable_http", "url": "cmd-a", "disabled_tools": []},
        "beta": {"transport": "streamable_http", "url": "cmd-b", "disabled_tools": []},
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

    config = {"transport": "streamable_http", "url": "demo-tool", "disabled_tools": []}

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


@pytest.mark.parametrize("transport", ["stdio", "websocket", None])
async def test_client_rejects_unsupported_transport_before_adapter(monkeypatch, transport):
    """直接客户端入口不能绕过远程传输限制。"""

    def forbidden_client(*_args, **_kwargs):
        pytest.fail("unsupported transport reached MCP adapter")

    monkeypatch.setattr(mcp_service, "MultiServerMCPClient", forbidden_client)
    with pytest.raises(ValueError, match="仅支持 sse 或 streamable_http"):
        await mcp_service.get_mcp_client({"deepwiki-official": {"transport": transport, "command": "sh"}})


async def test_additional_stdio_config_cannot_reuse_cached_tools():
    """历史缓存和内置 slug 都不能使直接 stdio 配置生效。"""
    import hashlib
    import json

    config = {"transport": "stdio", "command": "sh"}
    digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    key = f"deepwiki-official:{digest}"
    mcp_service._mcp_tools_cache[key] = [SimpleNamespace(name="unsafe_cached_tool")]
    try:
        with pytest.raises(ValueError, match="不支持 stdio"):
            await mcp_service.get_mcp_tools("deepwiki-official", additional_servers={"deepwiki-official": config})
    finally:
        mcp_service.clear_mcp_cache()


def test_model_cannot_serialize_stdio_for_execution():
    """持久化模型不再生成本地进程配置。"""
    server = MCPServer(slug="legacy", transport="stdio", command="sh", args=["-c", "exit"])
    with pytest.raises(ValueError, match="不支持 stdio"):
        server.to_mcp_config()


async def test_retire_chart_and_sync_deepwiki_idempotently(monkeypatch, mcp_session):
    """退役图表且固定 DeepWiki 连接，不恢复历史启用状态。"""
    mcp_session.add_all(
        [
            MCPServer(
                slug="mcp-server-chart",
                name="Chart",
                transport="stdio",
                command="npx",
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="deepwiki-official",
                name="DeepWiki",
                transport="stdio",
                command="sh",
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
        ]
    )
    await mcp_session.commit()
    monkeypatch.setattr(
        postgres_manager.pg_manager, "get_async_session_context", lambda: _AsyncSessionContext(mcp_session)
    )
    await mcp_service.ensure_builtin_mcp_servers_in_db()
    await mcp_service.ensure_builtin_mcp_servers_in_db()
    assert await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "mcp-server-chart")) is None
    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "deepwiki-official"))
    assert server.transport == "streamable_http"
    assert server.url == "https://mcp.deepwiki.com/mcp"
    assert server.command is None
    assert server.enabled == 0
    server.url = "https://tampered.example/mcp"
    assert mcp_service._to_runtime_mcp_config(server) == {
        "transport": "streamable_http",
        "url": "https://mcp.deepwiki.com/mcp",
    }


async def test_inspection_rejects_stdio_even_for_builtin_slug():
    """内置标识不再提供 stdio 豁免。"""
    server = MCPServer(slug="deepwiki-official", transport="stdio", command="sh")
    with pytest.raises(ValueError, match="stdio"):
        await mcp_service.inspect_mcp_server_tools(server)
