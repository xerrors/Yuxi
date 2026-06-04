from __future__ import annotations

import json
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services import mcp_service
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


async def test_get_runtime_mcp_server_config_resolves_department_connection(runtime_session):
    server = MCPServer(
        name="finance-gateway",
        transport="streamable_http",
        url="http://finance.local/mcp",
        headers={"X-App": "yuxi"},
        auth_config_json={
            "version": 1,
            "provider": "bound_secret",
            "binding_scope": "department",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
            },
        },
        enabled=1,
        created_by="tester",
        updated_by="tester",
    )
    runtime_session.add(server)
    runtime_session.add(
        MCPConnection(
            server_name="finance-gateway",
            scope_type="department",
            scope_id="42",
            status="active",
            credential_blob=json.dumps({"secrets": {"access_token": "dept-token"}}),
            created_by="tester",
            updated_by="tester",
        )
    )
    await runtime_session.commit()

    config = await mcp_service.get_runtime_mcp_server_config(
        "finance-gateway",
        auth_context=AuthContext(user_id="u-1", department_id="42"),
        db=runtime_session,
    )

    assert config is not None
    assert config["headers"]["Authorization"] == "Bearer dept-token"


async def test_get_enabled_mcp_tools_does_not_reuse_user_connection_for_other_user(runtime_session, monkeypatch):
    server = MCPServer(
        name="personal-gateway",
        transport="streamable_http",
        url="http://personal.local/mcp",
        auth_config_json={
            "version": 1,
            "provider": "bound_secret",
            "binding_scope": "user",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
            },
        },
        enabled=1,
        created_by="tester",
        updated_by="tester",
    )
    runtime_session.add(server)
    runtime_session.add(
        MCPConnection(
            server_name="personal-gateway",
            scope_type="user",
            scope_id="user-1",
            status="active",
            credential_blob=json.dumps({"secrets": {"access_token": "user-1-token"}}),
            created_by="tester",
            updated_by="tester",
        )
    )
    await runtime_session.commit()

    captured_configs: list[dict] = []

    async def fake_get_mcp_tools(server_name: str, additional_servers=None, disabled_tools=None, **kwargs):
        del disabled_tools, kwargs
        assert server_name == "personal-gateway"
        captured_configs.append(additional_servers[server_name])
        return ["private-tool"]

    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    user_1_tools = await mcp_service.get_enabled_mcp_tools(
        "personal-gateway",
        auth_context=AuthContext(user_id="user-1"),
        db=runtime_session,
    )

    with pytest.raises(ValueError, match="Active MCP connection not found"):
        await mcp_service.get_enabled_mcp_tools(
            "personal-gateway",
            auth_context=AuthContext(user_id="user-2"),
            db=runtime_session,
        )

    assert user_1_tools == ["private-tool"]
    assert len(captured_configs) == 1
    assert captured_configs[0]["headers"]["Authorization"] == "Bearer user-1-token"


async def test_get_enabled_mcp_tools_uses_runtime_mcp_config(monkeypatch):
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
        return ["tool-a"]

    monkeypatch.setattr(mcp_service, "get_runtime_mcp_server_config", fake_get_runtime_mcp_server_config)
    monkeypatch.setattr(mcp_service, "get_mcp_tools", fake_get_mcp_tools)

    tools = await mcp_service.get_enabled_mcp_tools(
        "demo",
        auth_context=AuthContext(user_id="u-100", department_id="d-9"),
    )

    assert tools == ["tool-a"]
    assert captured == [
        {
            "server_name": "demo",
            "additional_servers": {
                "demo": {"transport": "stdio", "command": "demo-with-auth", "disabled_tools": ["tool_b"]}
            },
            "disabled_tools": ["tool_b"],
        }
    ]


async def test_get_runtime_mcp_server_config_returns_internal_proxy_for_dynamic_http_provider(
    runtime_session, monkeypatch
):
    monkeypatch.setenv("YUXI_INTERNAL_MCP_PROXY_BASE_URL", "http://internal-api:5050")

    server = MCPServer(
        name="finance-proxy",
        transport="streamable_http",
        url="http://finance.local/mcp",
        headers={"X-App": "yuxi"},
        auth_config_json={
            "version": 1,
            "provider": "custom_http_token",
            "binding_scope": "department",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "token_request": {
                "url": "http://gateway.local/auth/token",
                "method": "POST",
                "response_map": {
                    "access_token": "access_token",
                    "expires_in": "expires_in",
                },
            },
        },
        enabled=1,
        created_by="tester",
        updated_by="tester",
    )
    runtime_session.add(server)
    runtime_session.add(
        MCPConnection(
            id=31,
            server_name="finance-proxy",
            scope_type="department",
            scope_id="dep-88",
            status="active",
            credential_blob=json.dumps({"secrets": {"client_id": "cid", "client_secret": "secret"}}),
            created_by="tester",
            updated_by="tester",
        )
    )
    await runtime_session.commit()

    config = await mcp_service.get_runtime_mcp_server_config(
        "finance-proxy",
        auth_context=AuthContext(user_id="user-1", department_id="dep-88"),
        db=runtime_session,
    )

    assert config is not None
    assert config["url"] == "http://internal-api:5050/api/internal/mcp-proxy/finance-proxy"
    assert config["headers"]["X-App"] == "yuxi"
    assert "X-Yuxi-MCP-Proxy-Token" in config["headers"]
    assert "Authorization" not in config["headers"]
