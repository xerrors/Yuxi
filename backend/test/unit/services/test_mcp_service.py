from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test.support.sqlite import create_utc_sqlite_engine
from yuxi.modules.extensions.mcp import service as mcp_service
from yuxi.modules.extensions.mcp import runtime as mcp_runtime
import yuxi.infrastructure.postgres.manager as postgres_manager
from yuxi.modules.extensions.mcp.models import MCPServer


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
    engine = create_utc_sqlite_engine()
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


async def test_ensure_builtin_mcp_servers_preserves_other_system_server(monkeypatch, mcp_session):
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
    assert retired is not None
    assert deepwiki is not None


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
    servers = await mcp_service.get_enabled_mcp_servers(db=mcp_session)
    configs = {server.slug: await mcp_service.get_enabled_mcp_server_config(server.slug, db=mcp_session) for server in servers}
    assert configs[slug]["url"] == "https://example.com/mcp"


async def test_ensure_builtin_mcp_servers_does_not_migrate_unsupported_transport(monkeypatch, mcp_session):
    legacy_server = MCPServer(
        slug="legacy-stdio",
        name="历史 stdio",
        transport="websocket",
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
    assert legacy_server.enabled == 1


async def test_builtin_mcp_initialization_propagates_failure_to_entrypoint(monkeypatch):
    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: _FailingSessionContext(),
    )

    with pytest.raises(RuntimeError, match="must-not-be-swallowed"):
        await mcp_service.ensure_builtin_mcp_servers_in_db()


async def test_runtime_configs_exclude_unsupported_transports(mcp_session):
    mcp_session.add_all(
        [
            MCPServer(
                slug="mcp-server-chart",
                name="不支持的传输",
                transport="websocket",
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="user-websocket",
                name="用户 WebSocket",
                transport="websocket",
                enabled=1,
                created_by="admin",
                updated_by="admin",
            ),
            MCPServer(
                slug="forged-system-websocket",
                name="伪造系统 WebSocket",
                transport="websocket",
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="remote-http",
                name="远程 HTTP",
                transport="streamable_http",
                url="https://example.com/mcp",
                enabled=1,
                created_by="admin",
                updated_by="admin",
            ),
        ]
    )
    await mcp_session.commit()

    servers = await mcp_service.get_enabled_mcp_servers(db=mcp_session)
    configs = {server.slug: await mcp_service.get_enabled_mcp_server_config(server.slug, db=mcp_session) for server in servers}
    slugs = await mcp_service.get_enabled_mcp_server_slugs(db=mcp_session)

    assert set(configs) == {"remote-http"}
    assert set(slugs) == {"remote-http"}
    assert "command" not in configs["remote-http"]
    assert "args" not in configs["remote-http"]


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


async def test_delete_builtin_mcp_server_rejects_service_call(mcp_session):
    server = MCPServer(
        slug="deepwiki-official",
        name="内置 MCP",
        transport="streamable_http",
        url="https://trusted.example/mcp",
        enabled=1,
        created_by="system",
        updated_by="system",
    )
    mcp_session.add(server)
    await mcp_session.commit()

    with pytest.raises(PermissionError, match="系统内置"):
        await mcp_service.delete_mcp_server(mcp_session, "deepwiki-official")

    assert await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "deepwiki-official")) is not None


async def test_update_builtin_mcp_server_rejects_connection_changes(mcp_session):
    server = MCPServer(
        slug="deepwiki-official",
        name="DeepWiki",
        transport="streamable_http",
        url="https://trusted.example/mcp",
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
    assert server.transport == "streamable_http"
    assert server.url == "https://trusted.example/mcp"


async def test_update_unsupported_transport_requires_remote_url(mcp_session):
    legacy_server = MCPServer(
        slug="legacy-stdio",
        name="历史 stdio",
        transport="websocket",
        enabled=0,
        created_by="admin",
        updated_by="admin",
    )
    mcp_session.add(legacy_server)
    await mcp_session.commit()

    with pytest.raises(ValueError, match="url"):
        await mcp_service.update_mcp_server(
            mcp_session,
            slug="legacy-stdio",
            transport="streamable_http",
            updated_by="admin",
        )

    await mcp_session.refresh(legacy_server)
    assert legacy_server.transport == "websocket"


async def test_get_enabled_mcp_tools_loads_latest_config_from_db(monkeypatch):
    captured: list[dict] = []

    async def fake_get_enabled_mcp_servers(*, names, db=None):
        """服务配置只读一次，底层直接消费该结果。"""
        assert names == ["demo"]
        return [MCPServer(slug="demo", transport="streamable_http", url="demo", auth_mode="headers", disabled_tools=["tool_b"])]

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

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_servers", fake_get_enabled_mcp_servers)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_enabled_mcp_tools("demo")

    assert tools == ["tool-a"]
    assert captured == [
        {
            "server_name": "demo",
            "additional_servers": {"demo": {"transport": "streamable_http", "url": "demo", "disabled_tools": ["tool_b"]}},
            "disabled_tools": ["tool_b"],
        }
    ]


async def test_each_independent_discovery_reads_current_remote_tools(monkeypatch):
    """相同配置的新发现也读取远端目录，不依赖进程缓存失效。"""
    tools = [SimpleNamespace(name="first", metadata={})]
    monkeypatch.setattr(mcp_runtime, "get_mcp_client", AsyncMock(side_effect=lambda _: _FakeClient(list(tools))))
    config = {"transport": "streamable_http", "url": "https://example.com/mcp"}
    first = await mcp_runtime.get_mcp_tools("demo", config)
    tools[:] = [SimpleNamespace(name="second", metadata={})]
    second = await mcp_runtime.get_mcp_tools("demo", config)
    assert [tool.name for tool in first] == ["first"]
    assert [tool.name for tool in second] == ["second"]


async def test_get_mcp_tools_sets_handle_tool_error(monkeypatch):

    config = {"transport": "streamable_http", "url": "demo-tool", "disabled_tools": []}
    server = SimpleNamespace(
        slug="demo",
        auth_mode="headers",
        disabled_tools=[],
        to_mcp_config=lambda: config,
    )

    async def fake_get_enabled_mcp_servers(*, names=None, db=None):
        del names, db
        return [server]

    async def fake_get_mcp_client(server_configs):
        tool = SimpleNamespace(name="demo_tool", metadata={})
        return _FakeClient([tool])

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_servers", fake_get_enabled_mcp_servers)
    monkeypatch.setattr(mcp_runtime, "get_mcp_client", fake_get_mcp_client)

    tools = await mcp_service.get_mcp_tools("demo")
    assert len(tools) == 1
    assert tools[0].handle_tool_error is True


@pytest.mark.parametrize("transport", ["stdio", "websocket", None])
async def test_client_rejects_unsupported_transport_before_adapter(monkeypatch, transport):
    """直接客户端入口不能绕过远程传输限制。"""

    def forbidden_client(*_args, **_kwargs):
        pytest.fail("unsupported transport reached MCP adapter")

    monkeypatch.setattr(mcp_runtime, "MultiServerMCPClient", forbidden_client)
    with pytest.raises(ValueError, match="仅支持 sse 或 streamable_http"):
        await mcp_runtime.get_mcp_client({"deepwiki-official": {"transport": transport}})


async def test_additional_unsupported_config_is_rejected_before_discovery(monkeypatch):
    """直传配置也不能绕过传输校验。"""
    forbidden = AsyncMock(side_effect=AssertionError("unsupported transport reached discovery"))
    monkeypatch.setattr(mcp_runtime, "get_mcp_client", forbidden)
    with pytest.raises(ValueError, match="仅支持 sse 或 streamable_http"):
        await mcp_service.get_mcp_tools("deepwiki-official", additional_servers={"deepwiki-official": {"transport": "websocket"}})


async def test_shared_get_mcp_tools_rejects_carried_auth(monkeypatch):
    """共享 headers 入口不允许调用方自带 auth 静默跳过出站策略。"""
    forbidden = AsyncMock(side_effect=AssertionError("carried auth reached discovery"))
    monkeypatch.setattr(mcp_service, "load_mcp_tools", forbidden)
    carried = {"transport": "streamable_http", "url": "demo", "auth": object()}
    with pytest.raises(ValueError, match="auth"):
        await mcp_service.get_mcp_tools("demo", additional_servers={"demo": carried})


async def test_get_enabled_mcp_tools_rejects_delegation_mode(monkeypatch):
    """委托 MCP 只能经运行装配持有可信上下文，单服务入口不得直接加载。"""

    async def fake_get_enabled_mcp_servers(*, names, db=None):
        return [MCPServer(slug="knowledge", transport="streamable_http", url="k", auth_mode="delegation", enabled=1)]

    monkeypatch.setattr(mcp_service, "get_enabled_mcp_servers", fake_get_enabled_mcp_servers)
    with pytest.raises(ValueError, match="平台委托"):
        await mcp_service.get_enabled_mcp_tools("knowledge", uid="u")


async def test_get_all_mcp_tools_connects_disabled_server(monkeypatch, mcp_session):
    """管理页工具清单对停用服务仍建连，运行目录继续排除停用服务。"""
    mcp_session.add(
        MCPServer(
            slug="offline",
            name="停用",
            transport="streamable_http",
            url="https://offline.example/mcp",
            enabled=0,
            created_by="admin",
            updated_by="admin",
        )
    )
    await mcp_session.commit()
    monkeypatch.setattr(postgres_manager.pg_manager, "get_async_session_context", lambda: _AsyncSessionContext(mcp_session))

    async def enabled_filter_must_not_gate_catalog(**_kwargs):
        raise AssertionError("enabled filter blocked management catalog")

    discovered = [SimpleNamespace(name="ask")]
    inspect = AsyncMock(return_value=discovered)
    monkeypatch.setattr(mcp_service, "get_enabled_mcp_servers", enabled_filter_must_not_gate_catalog)
    monkeypatch.setattr(mcp_service, "inspect_mcp_server_tools", inspect)

    assert await mcp_service.get_all_mcp_tools("offline", uid="admin") == discovered
    inspected = inspect.await_args.args[0]
    assert inspected.slug == "offline" and inspected.enabled == 0
    assert inspect.await_args.kwargs["uid"] == "admin"
    from yuxi.modules.extensions.mcp.repository import list_enabled_mcp_servers

    assert await list_enabled_mcp_servers(mcp_session, names=["offline"]) == []


async def test_disabled_personal_catalog_reads_own_credentials(monkeypatch):
    """停用的个人服务仍按本人凭证列目录，工具调用和运行策略继续拒绝。"""
    import json

    import httpx
    from cryptography.fernet import Fernet

    from yuxi.modules.extensions.mcp.oauth_config import config_revision, encrypt
    from yuxi.modules.extensions.mcp.oauth_runtime import PersonalMCPAuth, personal_mcp_tools
    from yuxi.modules.extensions.mcp.oauth_service import MCPOAuthService
    from yuxi.shared.errors import ResourceNotFoundError, StateConflictError

    monkeypatch.setenv("YUXI_ENCRYPTION_KEY", Fernet.generate_key().decode())
    server = MCPServer(
        id=1,
        slug="notes",
        name="笔记",
        transport="streamable_http",
        url="https://notes.example/mcp",
        auth_mode="bearer",
        enabled=0,
        disabled_tools=["hidden"],
        created_by="admin",
        updated_by="admin",
    )
    user = SimpleNamespace(id=4, uid="admin")
    revision = config_revision(server)
    connection = SimpleNamespace(
        status="connected",
        config_revision=revision,
        credentials=encrypt({"secret": "token-value"}),
        generation=2,
        expires_at=None,
    )
    state = {"connection": connection}

    async def policy(_db, slug, uid):
        assert (slug, uid) == ("notes", "admin")
        return user, server

    async def lookup(_db, server_id, user_id, lock=False, create=False):
        assert (server_id, user_id, create) == (server.id, user.id, False)
        assert lock is True
        return state["connection"]

    monkeypatch.setattr("yuxi.modules.extensions.mcp.oauth_service.runtime_mcp_policy", policy)
    monkeypatch.setattr("yuxi.modules.extensions.mcp.oauth_service.mcp_connection", lookup)
    db = SimpleNamespace(commit=AsyncMock())
    oauth = MCPOAuthService(db)
    with pytest.raises(ResourceNotFoundError, match="已停用"):
        await oauth.require_runtime_policy("notes", "admin", None, revision)
    header, value, generation = await oauth.static_credentials("notes", "admin", expected_revision=revision, catalog=True)
    assert (header, value, generation, server.enabled) == ("Authorization", "Bearer token-value", 2, 0)

    state["connection"] = None
    with pytest.raises(StateConflictError, match="个人凭证"):
        await oauth.static_credentials("notes", "admin", expected_revision=revision, catalog=True)
    assert server.enabled == 0

    server.auth_mode = "oauth"
    revision = config_revision(server)
    state["connection"] = SimpleNamespace(
        status="connected",
        config_revision=revision,
        credentials=encrypt({"access_token": "oauth-token"}),
        generation=3,
        expires_at=None,
    )
    assert await oauth.access_token("notes", "admin", expected_revision=revision, catalog=True) == "oauth-token"
    with pytest.raises(ResourceNotFoundError, match="已停用"):
        await oauth.access_token("notes", "admin", expected_revision=revision)

    monkeypatch.setattr(postgres_manager.pg_manager, "get_async_session_context", lambda: _AsyncSessionContext(db))
    server.auth_mode = "bearer"
    revision = config_revision(server)
    state["connection"] = connection
    connection.config_revision = revision

    def request(method_name):
        body = {"jsonrpc": "2.0", "id": 1, "method": method_name}
        if method_name == "tools/call":
            body["params"] = {"name": "ask"}
        return httpx.Request("POST", server.url, content=json.dumps(body).encode())

    catalog_auth = PersonalMCPAuth("notes", "admin", revision, auth_mode="bearer", url=server.url, management=True)
    listed = await anext(catalog_auth.async_auth_flow(request("tools/list")))
    assert listed.headers["authorization"] == "Bearer token-value"
    with pytest.raises(ResourceNotFoundError, match="已停用"):
        await anext(catalog_auth.async_auth_flow(request("tools/call")))
    runtime_auth = PersonalMCPAuth("notes", "admin", revision, auth_mode="bearer", url=server.url)
    with pytest.raises(ResourceNotFoundError, match="已停用"):
        await anext(runtime_auth.async_auth_flow(request("tools/list")))

    class CatalogClient:
        def __init__(self, configs):
            self.auth = configs["notes"]["auth"]

        async def get_tools(self):
            assert self.auth.management is True
            return [SimpleNamespace(name="ask", metadata=None)]

    monkeypatch.setattr("yuxi.modules.extensions.mcp.oauth_runtime.MultiServerMCPClient", CatalogClient)
    discovered = await personal_mcp_tools(server, "admin", include_disabled=True, management=True)
    assert [tool.name for tool in discovered] == ["ask"] and server.enabled == 0
    tools = await mcp_service.inspect_mcp_server_tools(server, uid="admin")
    assert [tool.name for tool in tools] == ["ask"]

    class DownClient:
        def __init__(self, _configs):
            return None

        async def get_tools(self):
            raise ConnectionError("remote down")

    monkeypatch.setattr("yuxi.modules.extensions.mcp.oauth_runtime.MultiServerMCPClient", DownClient)
    with pytest.raises(ConnectionError, match="remote down"):
        await mcp_service.inspect_mcp_server_tools(server, uid="admin")
    assert server.enabled == 0


def test_model_cannot_serialize_stdio_for_execution():
    """持久化模型不再生成本地进程配置。"""
    server = MCPServer(slug="unsupported", transport="stdio")
    with pytest.raises(ValueError, match="仅支持 sse 或 streamable_http"):
        server.to_mcp_config()


async def test_sync_deepwiki_idempotently_preserves_other_servers(monkeypatch, mcp_session):
    """同步仅收敛当前内置定义，保留其他服务器与管理员启停。"""
    mcp_session.add_all(
        [
            MCPServer(
                slug="mcp-server-chart",
                name="Chart",
                transport="streamable_http",
                url="https://retired.example/mcp",
                enabled=1,
                created_by="system",
                updated_by="system",
            ),
            MCPServer(
                slug="deepwiki-official",
                name="DeepWiki",
                transport="streamable_http",
                url="https://tampered.example/mcp",
                enabled=0,
                created_by="system",
                updated_by="system",
            ),
        ]
    )
    await mcp_session.commit()
    monkeypatch.setattr(postgres_manager.pg_manager, "get_async_session_context", lambda: _AsyncSessionContext(mcp_session))
    await mcp_service.ensure_builtin_mcp_servers_in_db()
    await mcp_service.ensure_builtin_mcp_servers_in_db()
    assert await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "mcp-server-chart")) is not None
    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "deepwiki-official"))
    assert server.transport == "streamable_http"
    assert server.url == "https://mcp.deepwiki.com/mcp"
    assert server.enabled == 0
    server.url = "https://tampered.example/mcp"
    assert mcp_service._to_runtime_mcp_config(server) == {
        "transport": "streamable_http",
        "url": "https://mcp.deepwiki.com/mcp",
    }


async def test_inspection_rejects_unsupported_transport_even_for_builtin_slug():
    """内置标识不再提供 stdio 豁免。"""
    server = MCPServer(slug="unsupported", transport="websocket")
    with pytest.raises(ValueError, match="仅支持 sse 或 streamable_http"):
        await mcp_service.inspect_mcp_server_tools(server)


@pytest.mark.parametrize("outcome", ["empty", "tool", "error"])
async def test_inspection_connects_disabled_server_without_mutating_runtime(monkeypatch, outcome):
    """管理检查不继承运行时停用与降级语义。"""
    server = MCPServer(
        slug="fixture-inspect",
        name="fixture",
        transport="streamable_http",
        url="http://127.0.0.1:12345/mcp",
        enabled=0,
        disabled_tools=["fixture_tool"],
        headers={"Authorization": "Bearer fixture"},
        timeout=1,
        sse_read_timeout=1,
    )
    tool = SimpleNamespace(name="fixture_tool", metadata={"retained": "value"})
    get_tools = AsyncMock(return_value=[] if outcome == "empty" else [tool])
    if outcome == "error":
        get_tools.side_effect = RuntimeError("fixture-credential-must-not-be-swallowed")
    configs = []

    def client(config):
        configs.append(config)
        return SimpleNamespace(get_tools=get_tools)

    monkeypatch.setattr(mcp_runtime, "MultiServerMCPClient", client)
    if outcome == "error":
        with pytest.raises(RuntimeError, match="工具加载失败") as error:
            await mcp_service.inspect_mcp_server_tools(server)
        assert "fixture-credential" in str(error.value.__cause__)
    else:
        tools = await mcp_service.inspect_mcp_server_tools(server)
        assert len(tools) == (0 if outcome == "empty" else 1)
        if tools:
            assert tool.metadata == {"retained": "value", "id": "mcp__fixtureInspect__fixtureTool"}
    get_tools.assert_awaited_once_with()
    assert configs[0][server.slug]["url"] == server.url
    assert configs[0][server.slug]["headers"] == server.headers
    assert "disabled_tools" not in configs[0][server.slug]
    assert server.enabled == 0 and server.disabled_tools == ["fixture_tool"]


async def test_inspection_rejects_unmigrated_stdio_before_connecting(monkeypatch):
    """自定义旧 stdio 不应启动子进程。"""

    def client(_):
        pytest.fail("自定义旧 stdio 不应启动子进程")

    monkeypatch.setattr(mcp_runtime, "MultiServerMCPClient", client)
    with pytest.raises(ValueError, match="stdio"):
        await mcp_service.inspect_mcp_server_tools(MCPServer(slug="fixture-stdio", transport="stdio", enabled=0))


@pytest.mark.parametrize("phase", ["client", "tools", "group"])
async def test_runtime_connection_failure_remains_explicit(monkeypatch, phase):
    """配置的 MCP 建连或工具加载失败不能被当作合法空列表。"""
    failure = RuntimeError("remote unavailable")
    if phase == "group":
        failure = ExceptionGroup("remote unavailable", [failure])

    def client(_configs):
        """分别在建连或工具读取阶段注入远程故障。"""
        if phase == "client":
            raise failure
        return SimpleNamespace(get_tools=AsyncMock(side_effect=failure))

    monkeypatch.setattr(mcp_runtime, "MultiServerMCPClient", client)
    with pytest.raises(RuntimeError, match="MCP.*demo.*失败") as caught:
        await mcp_runtime.get_mcp_tools("demo", {"transport": "streamable_http", "url": "https://example.com/mcp"})
    assert caught.value.__cause__ is failure
    monkeypatch.setattr(mcp_runtime, "MultiServerMCPClient", lambda _: _FakeClient([]))
    assert await mcp_runtime.get_mcp_tools("demo", {"transport": "streamable_http", "url": "https://example.com/mcp"}) == []


@pytest.mark.parametrize("auth_mode", ["oauth", "headers"])
@pytest.mark.parametrize("failure_kind", ["missing_key", "wrong_key", "unreachable", "nested", "mixed"])
async def test_runtime_isolates_failed_mcp_and_retains_healthy_tools(monkeypatch, auth_mode, failure_kind):
    """单服务失败保留安全原因，健康工具和正常聊天不受影响。"""
    from yuxi.modules.extensions.mcp import repository
    from yuxi.shared.errors import StateConflictError

    failure = {
        "missing_key": StateConflictError("secret-must-not-leak", code="mcp_oauth_unconfigured"),
        "wrong_key": StateConflictError("secret-must-not-leak", code="mcp_reconnect_required"),
        "unreachable": ConnectionError("secret-must-not-leak"),
        "nested": ExceptionGroup("TaskGroup", [ExceptionGroup("inner", [StateConflictError("secret", code="mcp_reconnect_required")])]),
        "mixed": ExceptionGroup("TaskGroup", [StateConflictError("secret", code="mcp_reconnect_required"), RuntimeError("secret")]),
    }[failure_kind]
    servers = {
        slug: MCPServer(slug=slug, auth_mode=auth_mode, transport="streamable_http", url="https://example.com/mcp")
        for slug in ["broken", "healthy"]
    }
    tool = SimpleNamespace(name="healthy_tool")
    monkeypatch.setattr(postgres_manager.pg_manager, "get_async_session_context", lambda: _AsyncSessionContext(None))
    monkeypatch.setattr(repository, "active_mcp_user", AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(mcp_service, "available_mcp_servers", AsyncMock(return_value=servers))

    async def load(server, *_args, **_kwargs):
        """从单服务发现边界注入故障。"""
        if (server if isinstance(server, str) else server.slug) == "broken":
            raise failure
        return [tool]

    monkeypatch.setattr(mcp_service, "personal_mcp_tools", load)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", load)
    unavailable, run_tools = {}, {}
    logs = []
    sink = mcp_service.logger.add(lambda message: logs.append(str(message)))
    try:
        result = await mcp_service.resolve_runtime_mcp_tools(
            ["broken", "healthy", "unconnected"], uid="user", run_tools=run_tools, unavailable=unavailable
        )
    finally:
        mcp_service.logger.remove(sink)
    assert "secret" not in "".join(logs)
    assert "server=broken" in "".join(logs) and "reason=" in "".join(logs)
    assert result == {"healthy": [tool]}
    assert run_tools == {"healthy": [tool]}
    assert set(unavailable) == ({"unconnected"} if failure_kind == "unreachable" else {"broken", "unconnected"})
    assert "secret" not in str(unavailable) and "TaskGroup" not in str(unavailable)
    assert "扩展页面" in unavailable["unconnected"]
    if failure_kind == "missing_key":
        assert "加密密钥" in unavailable["broken"]
    elif failure_kind in {"wrong_key", "nested", "mixed"}:
        assert "重新连接" in unavailable["broken"]
    else:
        assert "reason=服务连接或调用失败" in "".join(logs)


async def test_runtime_does_not_swallow_identity_or_database_failure(monkeypatch):
    """共享持久化失败不能冒充单 MCP 不可用。"""
    monkeypatch.setattr(postgres_manager.pg_manager, "get_async_session_context", _FailingSessionContext)
    with pytest.raises(RuntimeError, match="database-secret"):
        await mcp_service.resolve_runtime_mcp_tools(["demo"], uid="user", unavailable={})


async def test_builtin_sync_rejects_user_slug_without_overwriting_configuration(monkeypatch, mcp_session):
    """新内置 slug 碰撞必须明确拒绝，不能接管用户连接或认证模式。"""
    server = MCPServer(
        slug="knowledge",
        name="用户同名服务",
        transport="streamable_http",
        url="https://user.example/mcp",
        auth_mode="headers",
        headers={"X-Custom": "user"},
        enabled=1,
        disabled_tools=["user_tool"],
        created_by="admin",
        updated_by="admin",
    )
    mcp_session.add(server)
    await mcp_session.commit()
    monkeypatch.setattr(postgres_manager.pg_manager, "get_async_session_context", lambda: _AsyncSessionContext(mcp_session))
    with pytest.raises(ValueError, match="slug 冲突.*knowledge"):
        await mcp_service.ensure_builtin_mcp_servers_in_db()
    await mcp_session.refresh(server)
    assert (server.created_by, server.url, server.auth_mode) == ("admin", "https://user.example/mcp", "headers")
    assert server.headers == {"X-Custom": "user"} and server.disabled_tools == ["user_tool"]


async def test_deployment_switch_keeps_builtin_identity_and_admin_intent(monkeypatch, mcp_session):
    """部署关闭过滤候选但不覆盖原行，恢复后继续使用管理员工具开关。"""
    from yuxi.modules.extensions.mcp.builtin import builtin_mcp_available

    monkeypatch.setattr(postgres_manager.pg_manager, "get_async_session_context", lambda: _AsyncSessionContext(mcp_session))
    await mcp_service.ensure_builtin_mcp_servers_in_db()
    server = await mcp_session.scalar(select(MCPServer).where(MCPServer.slug == "knowledge"))
    server.disabled_tools = ["query_kb"]
    await mcp_session.commit()
    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", "false")
    await mcp_service.ensure_builtin_mcp_servers_in_db()
    await mcp_session.refresh(server)
    assert server.enabled == 1 and server.disabled_tools == ["query_kb"]
    assert mcp_service.is_builtin_mcp_server(server) and not builtin_mcp_available(server.slug)
    assert "knowledge" not in await mcp_service.get_enabled_mcp_server_slugs(db=mcp_session)
    with pytest.raises(ValueError, match="当前部署不可用"):
        await mcp_service.inspect_mcp_server_tools(server, uid="viewer")
    with pytest.raises(PermissionError, match="系统内置"):
        await mcp_service.delete_mcp_server(mcp_session, server.slug)
    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", "true")
    await mcp_service.ensure_builtin_mcp_servers_in_db()
    assert "knowledge" in await mcp_service.get_enabled_mcp_server_slugs(db=mcp_session)
    server.enabled = 0
    await mcp_session.commit()
    await mcp_service.ensure_builtin_mcp_servers_in_db()
    await mcp_session.refresh(server)
    assert server.enabled == 0 and server.disabled_tools == ["query_kb"]
