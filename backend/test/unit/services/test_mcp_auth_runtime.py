from __future__ import annotations

import json
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services.mcp import server_service, tool_registry_service
from yuxi.services.mcp_auth.orchestrator import AuthContext
from yuxi.storage.postgres.models_business import MCPConnection, MCPServer


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def runtime_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(MCPServer.__table__.create)
        await conn.run_sync(MCPConnection.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


def bound_secret_auth_config(binding_scope: str, *, manifest_scope: str | None = "server") -> dict:
    config = {
        "version": 1,
        "provider": "bound_secret",
        "binding_scope": binding_scope,
        "inject": {
            "target": "headers",
            "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
        },
    }
    if manifest_scope is not None:
        config["manifest_scope"] = manifest_scope
    return config


def token_auth_config(
    binding_scope: str,
    *,
    manifest_scope: str | None = "server",
    token_request: dict | None = None,
) -> dict:
    config = {
        "version": 1,
        "provider": "custom_http_token",
        "binding_scope": binding_scope,
        "inject": {
            "target": "headers",
            "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
        },
        "token_request": token_request
        or {
            "url": "http://gateway.local/auth/token",
            "method": "POST",
            "response_map": {"access_token": "access_token", "expires_in": "expires_in"},
        },
    }
    if manifest_scope is not None:
        config["manifest_scope"] = manifest_scope
    return config


def make_mcp_server(
    name: str,
    *,
    auth_config: dict,
    headers: dict | None = None,
    url: str = "http://finance.local/mcp",
) -> MCPServer:
    return MCPServer(
        name=name,
        transport="streamable_http",
        url=url,
        headers=headers,
        auth_config_json=auth_config,
        enabled=1,
        created_by="tester",
        updated_by="tester",
    )


def make_mcp_connection(
    server_name: str,
    *,
    scope_type: str,
    scope_id: str,
    secrets: dict,
    connection_id: int | None = None,
) -> MCPConnection:
    values = {
        "server_name": server_name,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "status": "active",
        "credential_blob": json.dumps({"secrets": secrets}),
        "created_by": "tester",
        "updated_by": "tester",
    }
    if connection_id is not None:
        values["id"] = connection_id
    return MCPConnection(**values)


async def test_get_runtime_mcp_server_config_resolves_department_connection(runtime_session):
    runtime_session.add(
        make_mcp_server(
            "finance-gateway",
            headers={"X-App": "yuxi"},
            auth_config=bound_secret_auth_config("department"),
        )
    )
    runtime_session.add(
        make_mcp_connection(
            "finance-gateway",
            scope_type="department",
            scope_id="42",
            secrets={"access_token": "dept-token"},
        )
    )
    await runtime_session.commit()

    config = await server_service.get_runtime_mcp_server_config(
        "finance-gateway",
        auth_context=AuthContext(user_id="u-1", department_id="42"),
        db=runtime_session,
    )

    assert config is not None
    assert config["headers"]["Authorization"] == "Bearer dept-token"


async def test_get_enabled_mcp_tools_does_not_reuse_user_connection_for_other_user(runtime_session, monkeypatch):
    runtime_session.add(make_mcp_server("personal-gateway", auth_config=bound_secret_auth_config("user")))
    runtime_session.add(
        make_mcp_connection(
            "personal-gateway",
            scope_type="user",
            scope_id="user-1",
            secrets={"access_token": "user-1-token"},
        )
    )
    await runtime_session.commit()

    captured_configs: list[dict] = []

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, disabled_tools=None, **kwargs):
        del disabled_tools, kwargs
        assert server_name == "personal-gateway"
        captured_configs.append(additional_servers[server_name])
        return ["private-tool"]

    monkeypatch.setattr(tool_registry_service, "get_mcp_tools", fake_get_mcp_tools)

    user_1_tools = await tool_registry_service.get_enabled_mcp_tools(
        "personal-gateway",
        auth_context=AuthContext(user_id="user-1"),
        db=runtime_session,
    )

    with pytest.raises(ValueError, match="Active MCP connection not found"):
        await tool_registry_service.get_enabled_mcp_tools(
            "personal-gateway",
            auth_context=AuthContext(user_id="user-2"),
            db=runtime_session,
        )

    assert user_1_tools == ["private-tool"]
    assert len(captured_configs) == 1
    assert captured_configs[0]["headers"]["Authorization"] == "Bearer user-1-token"


@pytest.mark.parametrize(
    ("service_method", "expected_tools", "expected_disabled_tools"),
    [
        ("get_enabled_mcp_tools", ["tool-a"], ["tool_b"]),
        ("get_all_mcp_tools", ["tool-a", "tool-b"], []),
    ],
)
async def test_tool_registry_uses_runtime_mcp_config(
    monkeypatch,
    service_method: str,
    expected_tools: list[str],
    expected_disabled_tools: list[str],
):
    captured: list[dict] = []

    async def fake_get_runtime_mcp_server_config(server_name: str, *, auth_context=None, db=None, http_client=None):
        del db, http_client
        assert server_name == "demo"
        assert auth_context is not None
        return {
            "transport": "stdio",
            "command": "demo-with-auth",
            "disabled_tools": ["tool_b"],
        }

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, disabled_tools=None, **kwargs):
        del kwargs
        captured.append(
            {
                "server_name": server_name,
                "additional_servers": additional_servers,
                "disabled_tools": list(disabled_tools or []),
            }
        )
        return expected_tools

    monkeypatch.setattr(server_service, "get_runtime_mcp_server_config", fake_get_runtime_mcp_server_config)
    monkeypatch.setattr(tool_registry_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await getattr(tool_registry_service, service_method)(
        "demo",
        auth_context=AuthContext(user_id="u-100", department_id="d-9"),
    )

    assert tools == expected_tools
    assert captured == [
        {
            "server_name": "demo",
            "additional_servers": {
                "demo": {"transport": "stdio", "command": "demo-with-auth", "disabled_tools": ["tool_b"]}
            },
            "disabled_tools": expected_disabled_tools,
        }
    ]


async def test_get_runtime_mcp_server_config_returns_internal_proxy_for_dynamic_http_provider(
    runtime_session, monkeypatch
):
    monkeypatch.setenv("YUXI_INTERNAL_MCP_PROXY_BASE_URL", "http://internal-api:5050")

    runtime_session.add(
        make_mcp_server(
            "finance-proxy",
            headers={"X-App": "yuxi"},
            auth_config=token_auth_config("department"),
        )
    )
    runtime_session.add(
        make_mcp_connection(
            "finance-proxy",
            scope_type="department",
            scope_id="dep-88",
            secrets={"client_id": "cid", "client_secret": "secret"},
            connection_id=31,
        )
    )
    await runtime_session.commit()

    config = await server_service.get_runtime_mcp_server_config(
        "finance-proxy",
        auth_context=AuthContext(user_id="user-1", department_id="dep-88"),
        db=runtime_session,
    )

    assert config is not None
    assert config["url"] == "http://internal-api:5050/api/internal/mcp-proxy/finance-proxy"
    assert config["headers"]["X-App"] == "yuxi"
    assert "X-Yuxi-MCP-Proxy-Token" in config["headers"]
    assert "Authorization" not in config["headers"]
    assert config["__yuxi_cache_partition"] == "connection:31"
    assert config["__yuxi_allow_global_cache"] is False
    assert config["__yuxi_disable_tool_object_cache"] is True


async def test_update_mcp_server_auth_config_clears_runtime_auth_cache(runtime_session, monkeypatch):
    runtime_session.add(
        make_mcp_server(
            "finance-gateway",
            auth_config=bound_secret_auth_config("system", manifest_scope=None),
        )
    )
    await runtime_session.commit()

    calls = {"runtime_auth_cache": 0, "tools_cache": 0}

    async def fake_clear_runtime_auth_cache(db, server_name):
        assert db is runtime_session
        assert server_name == "finance-gateway"
        calls["runtime_auth_cache"] += 1

    async def fake_invalidate_tools_cache(server_name):
        assert server_name == "finance-gateway"
        calls["tools_cache"] += 1

    monkeypatch.setattr(tool_registry_service, "_clear_mcp_server_runtime_auth_cache", fake_clear_runtime_auth_cache)
    monkeypatch.setattr(tool_registry_service, "invalidate_mcp_server_tools_cache", fake_invalidate_tools_cache)

    await server_service.update_mcp_server(
        runtime_session,
        "finance-gateway",
        auth_config=token_auth_config(
            "system",
            manifest_scope=None,
            token_request={"url": "http://gateway.local/auth/token", "method": "POST"},
        ),
        updated_by="tester",
    )

    assert calls == {"runtime_auth_cache": 1, "tools_cache": 1}
