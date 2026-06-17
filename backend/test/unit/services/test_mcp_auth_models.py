from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.storage.postgres.models_business import MCPConnection, MCPServer


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def mcp_auth_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(MCPServer.__table__.create)
        await conn.run_sync(MCPConnection.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def test_mcp_server_to_dict_and_mcp_config_include_auth_config(mcp_auth_session):
    server = MCPServer(
        name="gateway",
        description="internal gateway",
        transport="streamable_http",
        url="http://gateway.local/mcp",
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
            "refresh_policy": {"pre_refresh_seconds": 600, "retry_once_on_401": True},
            "token_request": {"url": "http://gateway.local/auth/token", "method": "POST"},
        },
        created_by="tester",
        updated_by="tester",
    )
    mcp_auth_session.add(server)
    await mcp_auth_session.commit()

    payload = server.to_dict()
    config = server.to_mcp_config()

    assert payload["auth_config"]["provider"] == "custom_http_token"
    assert config["auth_config"]["binding_scope"] == "department"


async def test_mcp_connection_persists_scoped_binding_and_hides_credentials_by_default(mcp_auth_session):
    server = MCPServer(
        name="finance-gateway",
        description="finance",
        transport="streamable_http",
        url="http://finance.local/mcp",
        created_by="tester",
        updated_by="tester",
    )
    mcp_auth_session.add(server)
    await mcp_auth_session.commit()

    connection = MCPConnection(
        server_name="finance-gateway",
        scope_type="department",
        scope_id="42",
        display_name="财务部共享凭据",
        external_subject="finance-user",
        status="active",
        credential_blob="encrypted-secret",
        meta_json={"last_success_at": "2026-06-02T10:00:00Z"},
        created_by="tester",
        updated_by="tester",
    )
    mcp_auth_session.add(connection)
    await mcp_auth_session.commit()

    result = await mcp_auth_session.execute(select(MCPConnection).where(MCPConnection.server_name == "finance-gateway"))
    saved = result.scalar_one()

    safe_payload = saved.to_dict()
    internal_payload = saved.to_dict(include_credentials=True)

    assert safe_payload["scope_type"] == "department"
    assert safe_payload["has_credentials"] is True
    assert "credential_blob" not in safe_payload
    assert internal_payload["credential_blob"] == "encrypted-secret"
