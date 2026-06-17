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


BOUND_SECRET_USER_AUTH_CONFIG = {
    "version": 1,
    "provider": "bound_secret",
    "binding_scope": "user",
    "inject": {
        "target": "headers",
        "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
    },
}


@pytest.fixture(autouse=True)
def mcp_credentials_master_key(monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")


def make_mcp_server(name: str, **overrides) -> MCPServer:
    payload = {
        "name": name,
        "transport": "streamable_http",
        "url": f"http://{name}.local/mcp",
        "created_by": "tester",
        "updated_by": "tester",
    }
    payload.update(overrides)
    return MCPServer(**payload)


def make_mcp_connection(server_name: str, **overrides) -> MCPConnection:
    payload = {
        "server_name": server_name,
        "scope_type": "system",
        "scope_id": "global",
        "status": "active",
        "credential_blob": "encrypted-secret",
        "created_by": "tester",
        "updated_by": "tester",
    }
    payload.update(overrides)
    return MCPConnection(**payload)


async def add_mcp_server(session, name: str, **overrides) -> None:
    session.add(make_mcp_server(name, **overrides))
    await session.commit()


async def create_connection(session, server_name: str, **overrides) -> MCPConnection:
    payload = {
        "server_name": server_name,
        "scope_type": "system",
        "scope_id": "global",
        "credential_blob": '{"secrets":{"access_token":"token"}}',
        "created_by": "tester",
    }
    payload.update(overrides)
    return await connection_service.create_mcp_connection(session, **payload)


async def add_scope_mismatched_connection(session, server_name: str, **connection_overrides):
    session.add(
        make_mcp_server(
            server_name,
            auth_config_json=BOUND_SECRET_USER_AUTH_CONFIG,
        )
    )
    session.add(
        make_mcp_connection(
            server_name,
            display_name="历史全局连接",
            **connection_overrides,
        )
    )
    await session.commit()
    return (
        await connection_service.list_mcp_connections(
            session,
            server_name=server_name,
        )
    )[0]


def patch_token_cache(monkeypatch):
    cleared_connection_ids = []
    released_connection_ids = []

    class DummyTokenCache:
        async def delete_access_token(self, connection_id):
            cleared_connection_ids.append(connection_id)

        async def release_refresh_lock(self, connection_id):
            released_connection_ids.append(connection_id)

    monkeypatch.setattr("yuxi.services.mcp_auth.redis_token_cache.RedisTokenCache", lambda: DummyTokenCache())
    return cleared_connection_ids, released_connection_ids


def patch_test_connection_runtime(monkeypatch, tools):
    observed_auth_contexts = []

    async def fake_get_runtime_mcp_server_config(server_name, *, auth_context=None, db=None, http_client=None):
        del db, http_client
        observed_auth_contexts.append(auth_context)
        return {"transport": "stdio", "command": f"{server_name}-cmd", "disabled_tools": []}

    async def fake_get_mcp_tools(server_name, additional_servers=None, disabled_tools=None, **kwargs):
        del additional_servers, disabled_tools, kwargs
        return tools(server_name) if callable(tools) else tools

    monkeypatch.setattr(server_service, "get_runtime_mcp_server_config", fake_get_runtime_mcp_server_config)
    monkeypatch.setattr(tool_registry_service, "get_mcp_tools", fake_get_mcp_tools)
    return observed_auth_contexts


@pytest_asyncio.fixture
async def connection_service_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Department.__table__.create)
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(MCPServer.__table__.create)
        await conn.run_sync(MCPConnection.__table__.create)
        await conn.run_sync(Skill.__table__.create)
        await conn.run_sync(AgentConfig.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def test_create_and_list_mcp_connections(connection_service_session, monkeypatch):
    await add_mcp_server(connection_service_session, "finance-gateway")

    created = await create_connection(
        connection_service_session,
        "finance-gateway",
        scope_type="department",
        scope_id="42",
        display_name="财务部共享连接",
        external_subject="finance-user",
        meta_json={"tenant": "finance"},
    )

    listed = await connection_service.list_mcp_connections(connection_service_session, server_name="finance-gateway")

    assert created.server_name == "finance-gateway"
    assert created.scope_type == "department"
    assert [item.id for item in listed] == [created.id]


async def test_create_mcp_connection_normalizes_system_scope_to_global(connection_service_session, monkeypatch):
    await add_mcp_server(connection_service_session, "global-gateway")

    created = await create_connection(
        connection_service_session,
        "global-gateway",
        scope_type="system",
        scope_id="",
        display_name="全局共享连接",
    )

    assert created.scope_type == "system"
    assert created.scope_id == "global"


async def test_list_mcp_connections_page_filters_health_and_searches_scope_target(
    connection_service_session,
):
    connection_service_session.add(Department(id=10, name="财务部"))
    connection_service_session.add(
        User(
            id=1,
            username="Alice",
            user_id="alice",
            password_hash="x",
            department_id=10,
        )
    )
    connection_service_session.add(
        User(
            id=2,
            username="Bob",
            user_id="bob",
            password_hash="x",
            department_id=10,
        )
    )
    connection_service_session.add(make_mcp_server("listing-gateway"))
    connection_service_session.add_all(
        [
            make_mcp_connection(
                "listing-gateway",
                scope_type="user",
                scope_id="1",
                display_name="Alice 连接",
            ),
            make_mcp_connection(
                "listing-gateway",
                scope_type="user",
                scope_id="2",
                display_name="Bob 连接",
                credential_blob=None,
            ),
            make_mcp_connection(
                "listing-gateway",
                display_name="历史全局连接",
            ),
            make_mcp_connection(
                "listing-gateway",
                scope_type="user",
                scope_id="3",
                display_name="过期连接",
                status="reauth_required",
            ),
            make_mcp_connection(
                "listing-gateway",
                scope_type="user",
                scope_id="4",
                display_name="停用连接",
                status="disabled",
            ),
            make_mcp_connection(
                "listing-gateway",
                scope_type="department",
                scope_id="10",
                display_name="部门异常连接",
                status="invalid",
            ),
        ]
    )
    await connection_service_session.commit()

    active_connections, active_total = await connection_service.list_mcp_connections_page(
        connection_service_session,
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
        connection_service_session,
        server_name="listing-gateway",
        status_filter="attention",
        effective_scope_type="user",
        credentials_required=True,
    )
    assert attention_count == 4

    searched_connections, searched_total = await connection_service.list_mcp_connections_page(
        connection_service_session,
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
    await add_mcp_server(connection_service_session, "demo_mcp_server")

    await create_connection(
        connection_service_session,
        "demo_mcp_server",
        scope_type="user",
        scope_id="1",
        display_name="个人连接",
    )

    with pytest.raises(ValueError) as exc_info:
        await create_connection(
            connection_service_session,
            "demo_mcp_server",
            scope_type="user",
            scope_id="1",
            display_name="重复个人连接",
        )

    message = str(exc_info.value)
    assert message == 'MCP "demo_mcp_server" 的个人专用连接已存在，请直接编辑现有连接。'
    assert "user:1" not in message


async def test_create_mcp_connection_rejects_scope_that_does_not_match_server_binding(
    connection_service_session, monkeypatch
):
    await add_mcp_server(
        connection_service_session,
        "personal-gateway",
        auth_config_json=BOUND_SECRET_USER_AUTH_CONFIG,
    )

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
    await add_mcp_server(connection_service_session, "corp-gateway")

    created = await create_connection(
        connection_service_session,
        "corp-gateway",
        display_name="全局共享连接",
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
    await add_mcp_server(connection_service_session, "invalid-scope-gateway")

    with pytest.raises(ValueError, match="scope_type"):
        await connection_service.create_mcp_connection(
            connection_service_session,
            server_name="invalid-scope-gateway",
            scope_type="tenant",
            scope_id="x",
            created_by="tester",
        )


async def test_create_mcp_connection_rejects_missing_department_scope_id(connection_service_session, monkeypatch):
    await add_mcp_server(connection_service_session, "missing-scope-id-gateway")

    with pytest.raises(ValueError, match="scope_id"):
        await connection_service.create_mcp_connection(
            connection_service_session,
            server_name="missing-scope-id-gateway",
            scope_type="department",
            scope_id="",
            created_by="tester",
        )


async def test_set_mcp_connection_status_rejects_invalid_status(connection_service_session, monkeypatch):
    await add_mcp_server(connection_service_session, "invalid-status-gateway")

    created = await create_connection(connection_service_session, "invalid-status-gateway")

    with pytest.raises(ValueError, match="status"):
        await connection_service.set_mcp_connection_status(
            connection_service_session,
            created.id,
            status="broken",
            updated_by="admin",
        )


@pytest.mark.parametrize(
    ("operation", "server_name", "connection_overrides"),
    [
        ("set_status", "personal-status-gateway", {"status": "disabled"}),
        (
            "reauthorize",
            "personal-reauth-gateway",
            {"status": "reauth_required", "meta_json": {"last_error": {"message": "expired"}}},
        ),
        ("test", "personal-runtime-gateway", {}),
    ],
)
async def test_mcp_connection_operations_reject_scope_mismatch(
    connection_service_session, operation, server_name, connection_overrides
):
    connection = await add_scope_mismatched_connection(
        connection_service_session,
        server_name,
        **connection_overrides,
    )

    with pytest.raises(ValueError) as exc_info:
        if operation == "set_status":
            await connection_service.set_mcp_connection_status(
                connection_service_session,
                connection.id,
                status="active",
                updated_by="admin",
            )
        elif operation == "reauthorize":
            await connection_service.reauthorize_mcp_connection(
                connection_service_session,
                connection.id,
                updated_by="admin",
            )
        else:
            await connection_service.test_mcp_connection(
                connection_service_session,
                connection.id,
                updated_by="admin",
            )

    message = str(exc_info.value)
    assert message == f'MCP "{server_name}" 当前绑定类型是个人专用，只能使用个人专用连接。'
    assert "user_id is required" not in message


async def test_create_mcp_connection_encrypts_credentials(connection_service_session, monkeypatch):
    await add_mcp_server(connection_service_session, "secure-gateway")

    plaintext = '{"secrets":{"access_token":"secure-token"}}'
    created = await create_connection(
        connection_service_session,
        "secure-gateway",
        credential_blob=plaintext,
    )

    assert created.credential_blob != plaintext
    assert decrypt_credential_blob(created.credential_blob) == plaintext


async def test_create_mcp_connection_rejects_plaintext_credentials_without_master_key(
    connection_service_session, monkeypatch
):
    monkeypatch.delenv("MCP_CREDENTIALS_MASTER_KEY", raising=False)
    await add_mcp_server(connection_service_session, "insecure-gateway")

    with pytest.raises(ValueError, match="MCP_CREDENTIALS_MASTER_KEY"):
        await connection_service.create_mcp_connection(
            connection_service_session,
            server_name="insecure-gateway",
            scope_type="system",
            scope_id="global",
            credential_blob='{"secrets":{"access_token":"token"}}',
            created_by="tester",
        )


async def test_get_mcp_server_dependency_summary_reports_runtime_references(connection_service_session):
    department = Department(name="研发部", description="dep")
    connection_service_session.add(department)
    connection_service_session.add(
        make_mcp_server(
            "finance-gateway",
            enabled=0,
        )
    )
    connection_service_session.add(
        make_mcp_connection(
            "finance-gateway",
            scope_type="department",
            scope_id="42",
            credential_blob=None,
        )
    )
    connection_service_session.add(
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
    await connection_service_session.flush()
    connection_service_session.add(
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
    await connection_service_session.commit()

    summary = await server_service.get_mcp_server_dependency_summary(connection_service_session, "finance-gateway")

    assert summary["has_references"] is True
    assert summary["connections"] == [{"scope_type": "department", "scope_id": "42", "status": "active"}]
    assert summary["skills"] == [{"slug": "finance-skill", "name": "Finance Skill"}]
    assert summary["agent_configs"] == [{"id": 1, "name": "Finance Agent", "agent_id": "agent-1"}]


async def test_update_mcp_connection_reencrypts_credentials(connection_service_session, monkeypatch):
    await add_mcp_server(connection_service_session, "update-gateway")

    created = await create_connection(
        connection_service_session,
        "update-gateway",
        display_name="old",
        credential_blob='{"secrets":{"access_token":"old-token"}}',
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
    cleared_connection_ids, released_connection_ids = patch_token_cache(monkeypatch)
    await add_mcp_server(connection_service_session, "delete-connection-gateway")

    created = await create_connection(connection_service_session, "delete-connection-gateway")

    deleted = await connection_service.delete_mcp_connection(connection_service_session, created.id)

    assert deleted is True
    assert cleared_connection_ids == [created.id]
    assert released_connection_ids == [created.id]
    assert await connection_service.get_mcp_connection(connection_service_session, created.id) is None


async def test_reauthorize_mcp_connection_clears_runtime_error(connection_service_session, monkeypatch):
    cleared_connection_ids, released_connection_ids = patch_token_cache(monkeypatch)
    await add_mcp_server(connection_service_session, "reauth-gateway")

    created = await create_connection(
        connection_service_session,
        "reauth-gateway",
        status="reauth_required",
        meta_json={"last_error": {"message": "expired"}},
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


async def test_update_mcp_connection_clears_runtime_auth_cache_on_credential_change(
    connection_service_session, monkeypatch
):
    cleared_connection_ids, released_connection_ids = patch_token_cache(monkeypatch)
    await add_mcp_server(connection_service_session, "credential-update-gateway")

    created = await create_connection(
        connection_service_session,
        "credential-update-gateway",
        credential_blob='{"secrets":{"access_token":"old-token"}}',
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
    cleared_connection_ids, released_connection_ids = patch_token_cache(monkeypatch)
    await add_mcp_server(connection_service_session, "retire-gateway", enabled=1)

    connections = []
    for scope_id in ("dep-1", "dep-2"):
        connections.append(
            await create_connection(
                connection_service_session,
                "retire-gateway",
                scope_type="department",
                scope_id=scope_id,
            )
        )

    enabled, server = await server_service.set_server_enabled(
        connection_service_session,
        "retire-gateway",
        False,
        updated_by="admin",
    )

    assert enabled is False
    assert bool(server.enabled) is False
    assert cleared_connection_ids == [connection.id for connection in connections]
    assert released_connection_ids == [connection.id for connection in connections]


async def test_test_mcp_connection_refreshes_success_metadata(connection_service_session, monkeypatch):
    patch_test_connection_runtime(monkeypatch, lambda server_name: [server_name, "tool-b"])
    await add_mcp_server(connection_service_session, "test-gateway")

    created = await create_connection(
        connection_service_session,
        "test-gateway",
        scope_type="department",
        scope_id="dep-9",
        status="reauth_required",
        meta_json={"last_error": {"message": "old"}},
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


async def test_test_mcp_connection_populates_work_id_for_user_scope(connection_service_session, monkeypatch):
    observed_auth_contexts = patch_test_connection_runtime(monkeypatch, ["tool-a"])
    await add_mcp_server(connection_service_session, "user-work-id-gateway")

    created = await create_connection(
        connection_service_session,
        "user-work-id-gateway",
        scope_type="user",
        scope_id="U001",
    )

    result = await connection_service.test_mcp_connection(
        connection_service_session,
        created.id,
        updated_by="admin",
    )

    assert result["tool_count"] == 1
    assert observed_auth_contexts[0].user_id == "U001"
    assert observed_auth_contexts[0].work_id == "U001"
