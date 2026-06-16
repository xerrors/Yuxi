from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services.mcp import connection_service, server_service, tool_registry_service
from yuxi.services.mcp_auth.crypto import decrypt_credential_blob
from yuxi.storage.postgres.models_business import AgentConfig, Department, MCPConnection, MCPServer, Skill, User


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def connection_service_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(MCPServer.__table__.create)
        await conn.run_sync(MCPConnection.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def delete_semantics_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Department.__table__.create)
        await conn.run_sync(MCPServer.__table__.create)
        await conn.run_sync(MCPConnection.__table__.create)
        await conn.run_sync(Skill.__table__.create)
        await conn.run_sync(AgentConfig.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def connection_listing_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Department.__table__.create)
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(MCPServer.__table__.create)
        await conn.run_sync(MCPConnection.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def test_create_and_list_mcp_connections(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="finance-gateway",
            transport="streamable_http",
            url="http://finance.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="finance-gateway",
        scope_type="department",
        scope_id="42",
        display_name="财务部共享连接",
        external_subject="finance-user",
        credential_blob="encrypted-secret",
        meta_json={"tenant": "finance"},
        created_by="tester",
    )

    listed = await connection_service.list_mcp_connections(connection_service_session, server_name="finance-gateway")

    assert created.server_name == "finance-gateway"
    assert created.scope_type == "department"
    assert [item.id for item in listed] == [created.id]


async def test_create_mcp_connection_normalizes_system_scope_to_global(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="global-gateway",
            transport="streamable_http",
            url="http://global.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="global-gateway",
        scope_type="system",
        scope_id="",
        display_name="全局共享连接",
        credential_blob="encrypted-secret",
        created_by="tester",
    )

    assert created.scope_type == "system"
    assert created.scope_id == "global"


async def test_list_mcp_connections_page_filters_health_and_searches_scope_target(
    connection_listing_session,
):
    connection_listing_session.add(Department(id=10, name="财务部"))
    connection_listing_session.add(
        User(
            id=1,
            username="Alice",
            user_id="alice",
            password_hash="x",
            department_id=10,
        )
    )
    connection_listing_session.add(
        User(
            id=2,
            username="Bob",
            user_id="bob",
            password_hash="x",
            department_id=10,
        )
    )
    connection_listing_session.add(
        MCPServer(
            name="listing-gateway",
            transport="streamable_http",
            url="http://listing.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    connection_listing_session.add_all(
        [
            MCPConnection(
                server_name="listing-gateway",
                scope_type="user",
                scope_id="1",
                display_name="Alice 连接",
                status="active",
                credential_blob="encrypted-secret",
            ),
            MCPConnection(
                server_name="listing-gateway",
                scope_type="user",
                scope_id="2",
                display_name="Bob 连接",
                status="active",
                credential_blob=None,
            ),
            MCPConnection(
                server_name="listing-gateway",
                scope_type="system",
                scope_id="global",
                display_name="历史全局连接",
                status="active",
                credential_blob="encrypted-secret",
            ),
            MCPConnection(
                server_name="listing-gateway",
                scope_type="user",
                scope_id="3",
                display_name="过期连接",
                status="reauth_required",
                credential_blob="encrypted-secret",
            ),
            MCPConnection(
                server_name="listing-gateway",
                scope_type="user",
                scope_id="4",
                display_name="停用连接",
                status="disabled",
                credential_blob="encrypted-secret",
            ),
            MCPConnection(
                server_name="listing-gateway",
                scope_type="department",
                scope_id="10",
                display_name="部门异常连接",
                status="invalid",
                credential_blob="encrypted-secret",
            ),
        ]
    )
    await connection_listing_session.commit()

    active_connections, active_total = await connection_service.list_mcp_connections_page(
        connection_listing_session,
        server_name="listing-gateway",
        status_filter="active",
        effective_scope_type="user",
        credentials_required=True,
        page=1,
        page_size=12,
    )

    assert active_total == 1
    assert [item.display_name for item in active_connections] == ["Alice 连接"]

    attention_count = await connection_service.count_mcp_connections(
        connection_listing_session,
        server_name="listing-gateway",
        status_filter="attention",
        effective_scope_type="user",
        credentials_required=True,
    )
    assert attention_count == 4

    searched_connections, searched_total = await connection_service.list_mcp_connections_page(
        connection_listing_session,
        server_name="listing-gateway",
        search="alice",
        page=1,
        page_size=12,
    )

    assert searched_total == 1
    assert searched_connections[0].scope_id == "1"


async def test_create_mcp_connection_duplicate_scope_uses_user_friendly_message(
    connection_service_session, monkeypatch
):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="demo_mcp_server",
            transport="streamable_http",
            url="http://demo.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="demo_mcp_server",
        scope_type="user",
        scope_id="1",
        display_name="个人连接",
        credential_blob="encrypted-secret",
        created_by="tester",
    )

    with pytest.raises(ValueError) as exc_info:
        await connection_service.create_mcp_connection(
            connection_service_session,
            server_name="demo_mcp_server",
            scope_type="user",
            scope_id="1",
            display_name="重复个人连接",
            credential_blob="encrypted-secret",
            created_by="tester",
        )

    message = str(exc_info.value)
    assert message == 'MCP "demo_mcp_server" 的个人专用连接已存在，请直接编辑现有连接。'
    assert "user:1" not in message


async def test_create_mcp_connection_rejects_scope_that_does_not_match_server_binding(
    connection_service_session, monkeypatch
):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="personal-gateway",
            transport="streamable_http",
            url="http://personal.local/mcp",
            auth_config_json={
                "version": 1,
                "provider": "bound_secret",
                "binding_scope": "user",
                "inject": {
                    "target": "headers",
                    "entries": [
                        {"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}
                    ],
                },
            },
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    with pytest.raises(ValueError) as exc_info:
        await connection_service.create_mcp_connection(
            connection_service_session,
            server_name="personal-gateway",
            scope_type="department",
            scope_id="42",
            display_name="部门连接",
            credential_blob="encrypted-secret",
            created_by="tester",
        )

    assert str(exc_info.value) == 'MCP "personal-gateway" 当前绑定类型是个人专用，只能使用个人专用连接。'


async def test_set_mcp_connection_status_updates_status(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="corp-gateway",
            transport="streamable_http",
            url="http://corp.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="corp-gateway",
        scope_type="system",
        scope_id="global",
        display_name="全局共享连接",
        credential_blob="encrypted-secret",
        created_by="tester",
    )

    updated = await connection_service.set_mcp_connection_status(
        connection_service_session,
        created.id,
        status="reauth_required",
        updated_by="admin",
    )

    assert updated.status == "reauth_required"
    assert updated.updated_by == "admin"


async def test_create_mcp_connection_rejects_invalid_scope_type(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="invalid-scope-gateway",
            transport="streamable_http",
            url="http://invalid-scope.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    with pytest.raises(ValueError, match="scope_type"):
        await connection_service.create_mcp_connection(
            connection_service_session,
            server_name="invalid-scope-gateway",
            scope_type="tenant",
            scope_id="x",
            created_by="tester",
        )


async def test_create_mcp_connection_rejects_missing_department_scope_id(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="missing-scope-id-gateway",
            transport="streamable_http",
            url="http://missing-scope-id.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    with pytest.raises(ValueError, match="scope_id"):
        await connection_service.create_mcp_connection(
            connection_service_session,
            server_name="missing-scope-id-gateway",
            scope_type="department",
            scope_id="",
            created_by="tester",
        )


async def test_set_mcp_connection_status_rejects_invalid_status(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="invalid-status-gateway",
            transport="streamable_http",
            url="http://invalid-status.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="invalid-status-gateway",
        scope_type="system",
        scope_id="global",
        created_by="tester",
    )

    with pytest.raises(ValueError, match="status"):
        await connection_service.set_mcp_connection_status(
            connection_service_session,
            created.id,
            status="broken",
            updated_by="admin",
        )


async def test_set_mcp_connection_status_rejects_reactivating_scope_mismatch(
    connection_service_session, monkeypatch
):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="personal-status-gateway",
            transport="streamable_http",
            url="http://personal-status.local/mcp",
            auth_config_json={
                "version": 1,
                "provider": "bound_secret",
                "binding_scope": "user",
                "inject": {
                    "target": "headers",
                    "entries": [
                        {"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}
                    ],
                },
            },
            created_by="tester",
            updated_by="tester",
        )
    )
    connection_service_session.add(
        MCPConnection(
            server_name="personal-status-gateway",
            scope_type="system",
            scope_id="global",
            display_name="历史全局连接",
            status="disabled",
            credential_blob="encrypted-secret",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()
    connection = (
        await connection_service.list_mcp_connections(
            connection_service_session,
            server_name="personal-status-gateway",
        )
    )[0]

    with pytest.raises(ValueError) as exc_info:
        await connection_service.set_mcp_connection_status(
            connection_service_session,
            connection.id,
            status="active",
            updated_by="admin",
        )

    assert str(exc_info.value) == 'MCP "personal-status-gateway" 当前绑定类型是个人专用，只能使用个人专用连接。'


async def test_create_mcp_connection_encrypts_credentials(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="secure-gateway",
            transport="streamable_http",
            url="http://secure.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    plaintext = '{"secrets":{"access_token":"secure-token"}}'
    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="secure-gateway",
        scope_type="system",
        scope_id="global",
        credential_blob=plaintext,
        created_by="tester",
    )

    assert created.credential_blob != plaintext
    assert decrypt_credential_blob(created.credential_blob) == plaintext


async def test_create_mcp_connection_rejects_plaintext_credentials_without_master_key(
    connection_service_session, monkeypatch
):
    monkeypatch.delenv("MCP_CREDENTIALS_MASTER_KEY", raising=False)
    connection_service_session.add(
        MCPServer(
            name="insecure-gateway",
            transport="streamable_http",
            url="http://insecure.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    with pytest.raises(ValueError, match="MCP_CREDENTIALS_MASTER_KEY"):
        await connection_service.create_mcp_connection(
            connection_service_session,
            server_name="insecure-gateway",
            scope_type="system",
            scope_id="global",
            credential_blob='{"secrets":{"access_token":"token"}}',
            created_by="tester",
        )


async def test_get_mcp_server_dependency_summary_reports_runtime_references(delete_semantics_session):
    department = Department(name="研发部", description="dep")
    delete_semantics_session.add(department)
    delete_semantics_session.add(
        MCPServer(
            name="finance-gateway",
            transport="streamable_http",
            url="http://finance.local/mcp",
            enabled=0,
            created_by="tester",
            updated_by="tester",
        )
    )
    delete_semantics_session.add(
        MCPConnection(
            server_name="finance-gateway",
            scope_type="department",
            scope_id="42",
            status="active",
            created_by="tester",
            updated_by="tester",
        )
    )
    delete_semantics_session.add(
        Skill(
            slug="finance-skill",
            name="Finance Skill",
            description="desc",
            tool_dependencies=[],
            mcp_dependencies=["finance-gateway"],
            skill_dependencies=[],
            dir_path="skills/finance",
            created_by="tester",
            updated_by="tester",
        )
    )
    await delete_semantics_session.flush()
    delete_semantics_session.add(
        AgentConfig(
            department_id=department.id,
            agent_id="agent-1",
            name="Finance Agent",
            description="desc",
            config_json={"mcps": ["finance-gateway"]},
            pics=[],
            examples=[],
            created_by="tester",
            updated_by="tester",
        )
    )
    await delete_semantics_session.commit()

    summary = await server_service.get_mcp_server_dependency_summary(delete_semantics_session, "finance-gateway")

    assert summary["has_references"] is True
    assert summary["connections"] == [{"scope_type": "department", "scope_id": "42", "status": "active"}]
    assert summary["skills"] == [{"slug": "finance-skill", "name": "Finance Skill"}]
    assert summary["agent_configs"] == [{"id": 1, "name": "Finance Agent", "agent_id": "agent-1"}]


async def test_update_mcp_connection_reencrypts_credentials(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="update-gateway",
            transport="streamable_http",
            url="http://update.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="update-gateway",
        scope_type="system",
        scope_id="global",
        display_name="old",
        credential_blob='{"secrets":{"access_token":"old-token"}}',
        created_by="tester",
    )

    updated = await connection_service.update_mcp_connection(
        connection_service_session,
        created.id,
        display_name="new",
        credential_blob='{"secrets":{"access_token":"new-token"}}',
        updated_by="admin",
    )

    assert updated.display_name == "new"
    assert decrypt_credential_blob(updated.credential_blob) == '{"secrets":{"access_token":"new-token"}}'
    assert updated.updated_by == "admin"


async def test_delete_mcp_connection_removes_record(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    cleared_connection_ids = []
    released_connection_ids = []

    class DummyTokenCache:
        async def delete_access_token(self, connection_id):
            cleared_connection_ids.append(connection_id)

        async def release_refresh_lock(self, connection_id):
            released_connection_ids.append(connection_id)

    monkeypatch.setattr("yuxi.services.mcp_auth.redis_token_cache.RedisTokenCache", lambda: DummyTokenCache())
    connection_service_session.add(
        MCPServer(
            name="delete-connection-gateway",
            transport="streamable_http",
            url="http://delete.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="delete-connection-gateway",
        scope_type="system",
        scope_id="global",
        credential_blob='{"secrets":{"access_token":"token"}}',
        created_by="tester",
    )

    deleted = await connection_service.delete_mcp_connection(connection_service_session, created.id)

    assert deleted is True
    assert cleared_connection_ids == [created.id]
    assert released_connection_ids == [created.id]
    assert await connection_service.get_mcp_connection(connection_service_session, created.id) is None


async def test_reauthorize_mcp_connection_clears_runtime_error(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    cleared_connection_ids = []
    released_connection_ids = []

    class DummyTokenCache:
        async def delete_access_token(self, connection_id):
            cleared_connection_ids.append(connection_id)

        async def release_refresh_lock(self, connection_id):
            released_connection_ids.append(connection_id)

    monkeypatch.setattr("yuxi.services.mcp_auth.redis_token_cache.RedisTokenCache", lambda: DummyTokenCache())

    connection_service_session.add(
        MCPServer(
            name="reauth-gateway",
            transport="streamable_http",
            url="http://reauth.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="reauth-gateway",
        scope_type="system",
        scope_id="global",
        status="reauth_required",
        credential_blob='{"secrets":{"access_token":"token"}}',
        meta_json={"last_error": {"message": "expired"}},
        created_by="tester",
    )

    updated = await connection_service.reauthorize_mcp_connection(
        connection_service_session,
        created.id,
        updated_by="admin",
    )

    assert cleared_connection_ids == [created.id]
    assert released_connection_ids == [created.id]
    assert updated.status == "active"
    assert updated.meta_json == {}
    assert updated.updated_by == "admin"


async def test_reauthorize_mcp_connection_rejects_scope_that_does_not_match_server_binding(
    connection_service_session, monkeypatch
):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="personal-reauth-gateway",
            transport="streamable_http",
            url="http://personal-reauth.local/mcp",
            auth_config_json={
                "version": 1,
                "provider": "bound_secret",
                "binding_scope": "user",
                "inject": {
                    "target": "headers",
                    "entries": [
                        {"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}
                    ],
                },
            },
            created_by="tester",
            updated_by="tester",
        )
    )
    connection_service_session.add(
        MCPConnection(
            server_name="personal-reauth-gateway",
            scope_type="system",
            scope_id="global",
            display_name="历史全局连接",
            status="reauth_required",
            credential_blob="encrypted-secret",
            meta_json={"last_error": {"message": "expired"}},
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()
    connection = (
        await connection_service.list_mcp_connections(
            connection_service_session,
            server_name="personal-reauth-gateway",
        )
    )[0]

    with pytest.raises(ValueError) as exc_info:
        await connection_service.reauthorize_mcp_connection(
            connection_service_session,
            connection.id,
            updated_by="admin",
        )

    message = str(exc_info.value)
    assert message == 'MCP "personal-reauth-gateway" 当前绑定类型是个人专用，只能使用个人专用连接。'
    assert "user_id is required" not in message


async def test_update_mcp_connection_clears_runtime_auth_cache_on_credential_change(
    connection_service_session, monkeypatch
):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    cleared_connection_ids = []
    released_connection_ids = []

    class DummyTokenCache:
        async def delete_access_token(self, connection_id):
            cleared_connection_ids.append(connection_id)

        async def release_refresh_lock(self, connection_id):
            released_connection_ids.append(connection_id)

    monkeypatch.setattr("yuxi.services.mcp_auth.redis_token_cache.RedisTokenCache", lambda: DummyTokenCache())
    connection_service_session.add(
        MCPServer(
            name="credential-update-gateway",
            transport="streamable_http",
            url="http://credential-update.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="credential-update-gateway",
        scope_type="system",
        scope_id="global",
        credential_blob='{"secrets":{"access_token":"old-token"}}',
        created_by="tester",
    )

    updated = await connection_service.update_mcp_connection(
        connection_service_session,
        created.id,
        credential_blob='{"secrets":{"access_token":"new-token"}}',
        updated_by="admin",
    )

    assert updated.updated_by == "admin"
    assert cleared_connection_ids == [created.id]
    assert released_connection_ids == [created.id]


async def test_set_server_enabled_clears_runtime_auth_cache_when_retiring(
    connection_service_session, monkeypatch
):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    cleared_connection_ids = []
    released_connection_ids = []

    class DummyTokenCache:
        async def delete_access_token(self, connection_id):
            cleared_connection_ids.append(connection_id)

        async def release_refresh_lock(self, connection_id):
            released_connection_ids.append(connection_id)

    monkeypatch.setattr("yuxi.services.mcp_auth.redis_token_cache.RedisTokenCache", lambda: DummyTokenCache())
    connection_service_session.add(
        MCPServer(
            name="retire-gateway",
            transport="streamable_http",
            url="http://retire.local/mcp",
            enabled=1,
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    first = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="retire-gateway",
        scope_type="department",
        scope_id="dep-1",
        credential_blob='{"secrets":{"access_token":"token-1"}}',
        created_by="tester",
    )
    second = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="retire-gateway",
        scope_type="department",
        scope_id="dep-2",
        credential_blob='{"secrets":{"access_token":"token-2"}}',
        created_by="tester",
    )

    enabled, server = await server_service.set_server_enabled(
        connection_service_session,
        "retire-gateway",
        False,
        updated_by="admin",
    )

    assert enabled is False
    assert bool(server.enabled) is False
    assert cleared_connection_ids == [first.id, second.id]
    assert released_connection_ids == [first.id, second.id]


async def test_test_mcp_connection_refreshes_success_metadata(connection_service_session, monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")

    async def fake_get_runtime_mcp_server_config(server_name, *, auth_context=None, db=None, http_client=None):
        del auth_context, db, http_client
        return {"transport": "stdio", "command": f"{server_name}-cmd", "disabled_tools": []}

    async def fake_get_mcp_tools(server_name, additional_servers=None, disabled_tools=None, **kwargs):
        del additional_servers, disabled_tools, kwargs
        return [server_name, "tool-b"]

    monkeypatch.setattr(server_service, "get_runtime_mcp_server_config", fake_get_runtime_mcp_server_config)
    monkeypatch.setattr(tool_registry_service, "get_mcp_tools", fake_get_mcp_tools)

    connection_service_session.add(
        MCPServer(
            name="test-gateway",
            transport="streamable_http",
            url="http://test.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="test-gateway",
        scope_type="department",
        scope_id="dep-9",
        status="reauth_required",
        credential_blob='{"secrets":{"access_token":"token"}}',
        meta_json={"last_error": {"message": "old"}},
        created_by="tester",
    )

    result = await connection_service.test_mcp_connection(
        connection_service_session,
        created.id,
        updated_by="admin",
    )

    assert result["tool_count"] == 2
    assert result["connection"].status == "active"
    assert "last_success_at" in result["connection"].meta_json
    assert "last_error" not in result["connection"].meta_json


async def test_test_mcp_connection_rejects_scope_that_does_not_match_server_binding(
    connection_service_session, monkeypatch
):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")
    connection_service_session.add(
        MCPServer(
            name="personal-runtime-gateway",
            transport="streamable_http",
            url="http://personal-runtime.local/mcp",
            auth_config_json={
                "version": 1,
                "provider": "bound_secret",
                "binding_scope": "user",
                "inject": {
                    "target": "headers",
                    "entries": [
                        {"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}
                    ],
                },
            },
            created_by="tester",
            updated_by="tester",
        )
    )
    connection_service_session.add(
        MCPConnection(
            server_name="personal-runtime-gateway",
            scope_type="system",
            scope_id="global",
            display_name="历史全局连接",
            status="active",
            credential_blob="encrypted-secret",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()
    connection = (
        await connection_service.list_mcp_connections(
            connection_service_session,
            server_name="personal-runtime-gateway",
        )
    )[0]

    with pytest.raises(ValueError) as exc_info:
        await connection_service.test_mcp_connection(
            connection_service_session,
            connection.id,
            updated_by="admin",
        )

    message = str(exc_info.value)
    assert message == 'MCP "personal-runtime-gateway" 当前绑定类型是个人专用，只能使用个人专用连接。'
    assert "user_id is required" not in message


async def test_test_mcp_connection_populates_work_id_for_user_scope(connection_service_session, monkeypatch):
    observed_auth_contexts = []

    async def fake_get_runtime_mcp_server_config(server_name, *, auth_context=None, db=None, http_client=None):
        del db, http_client
        observed_auth_contexts.append(auth_context)
        return {"transport": "stdio", "command": f"{server_name}-cmd", "disabled_tools": []}

    async def fake_get_mcp_tools(server_name, additional_servers=None, disabled_tools=None, **kwargs):
        del server_name, additional_servers, disabled_tools, kwargs
        return ["tool-a"]

    monkeypatch.setattr(server_service, "get_runtime_mcp_server_config", fake_get_runtime_mcp_server_config)
    monkeypatch.setattr(tool_registry_service, "get_mcp_tools", fake_get_mcp_tools)

    connection_service_session.add(
        MCPServer(
            name="user-work-id-gateway",
            transport="streamable_http",
            url="http://test.local/mcp",
            created_by="tester",
            updated_by="tester",
        )
    )
    await connection_service_session.commit()

    created = await connection_service.create_mcp_connection(
        connection_service_session,
        server_name="user-work-id-gateway",
        scope_type="user",
        scope_id="U001",
        created_by="tester",
    )

    result = await connection_service.test_mcp_connection(
        connection_service_session,
        created.id,
        updated_by="admin",
    )

    assert result["tool_count"] == 1
    assert observed_auth_contexts[0].user_id == "U001"
    assert observed_auth_contexts[0].work_id == "U001"
