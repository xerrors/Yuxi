from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services.mcp import connection_service
from yuxi.services.mcp_auth.crypto import decrypt_credential_blob
from yuxi.storage.postgres.models_business import Department, MCPConnection, MCPServer, User

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

USER_BOUND_AUTH_CONFIG = {
    "version": 1,
    "provider": "bound_secret",
    "binding_scope": "user",
    "inject": {
        "target": "headers",
        "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
    },
}

SYSTEM_BOUND_AUTH_CONFIG = {
    "version": 1,
    "provider": "bound_secret",
    "binding_scope": "system",
    "inject": {
        "target": "headers",
        "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
    },
}


@pytest.fixture(autouse=True)
def mcp_credentials_master_key(monkeypatch):
    monkeypatch.setenv("MCP_CREDENTIALS_MASTER_KEY", "local-test-master-key")


@pytest_asyncio.fixture
async def conn_session():
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


async def _add_server(session, name: str, **overrides) -> MCPServer:
    server = MCPServer(
        name=name,
        transport="streamable_http",
        url=f"http://{name}.local/mcp",
        created_by="tester",
        updated_by="tester",
        **overrides,
    )
    session.add(server)
    await session.commit()
    return server


async def _ensure_user(session, scope_id: str) -> User:
    conditions = [User.user_id == scope_id]
    if scope_id.isdigit():
        conditions.append(User.id == int(scope_id))
    existing = (await session.execute(select(User).where(or_(*conditions)))).scalar_one_or_none()
    if existing is not None:
        return existing

    user_kwargs = {
        "username": f"user-{scope_id}",
        "user_id": scope_id,
        "password_hash": "x",
        "role": "user",
    }
    if scope_id.isdigit():
        user_kwargs["id"] = int(scope_id)
    user = User(**user_kwargs)
    session.add(user)
    await session.commit()
    return user


async def _create_connection(session, server_name: str, **overrides) -> MCPConnection:
    params = {
        "server_name": server_name,
        "scope_type": "system",
        "scope_id": "global",
        "credential_blob": '{"secrets":{"access_token":"token"}}',
        "created_by": "tester",
    }
    params.update(overrides)
    if params["scope_type"] == "user" and str(params["scope_id"] or "").strip():
        await _ensure_user(session, str(params["scope_id"]))
    return await connection_service.create_mcp_connection(session, **params)


# =============================================================================
# === CRUD ===
# =============================================================================


async def test_create_and_list_mcp_connections(conn_session):
    await _add_server(conn_session, "test-gateway")

    c1 = await _create_connection(
        conn_session,
        "test-gateway",
        scope_type="system",
        scope_id="global",
        display_name="全局连接",
    )
    c2 = await _create_connection(
        conn_session,
        "test-gateway",
        scope_type="user",
        scope_id="42",
        display_name="用户连接",
        credential_blob='{"secrets":{"token":"my-token"}}',
    )

    rows = await connection_service.list_mcp_connections(conn_session, server_name="test-gateway")
    assert len(rows) == 2
    assert [r.id for r in rows] == [c1.id, c2.id]
    assert rows[0].display_name == "全局连接"
    assert rows[1].display_name == "用户连接"


async def test_get_mcp_connection_by_id(conn_session):
    await _add_server(conn_session, "srv")
    created = await _create_connection(conn_session, "srv", scope_type="user", scope_id="1")
    fetched = await connection_service.get_mcp_connection(conn_session, created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.server_name == "srv"

    missing = await connection_service.get_mcp_connection(conn_session, 9999)
    assert missing is None


async def test_update_mcp_connection(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv", scope_type="user", scope_id="1", display_name="旧名称")

    updated = await connection_service.update_mcp_connection(
        conn_session,
        c.id,
        display_name="新名称",
        external_subject="sub-123",
        updated_by="admin",
    )

    assert updated.display_name == "新名称"
    assert updated.external_subject == "sub-123"
    assert updated.updated_by == "admin"

    fetched = await connection_service.get_mcp_connection(conn_session, c.id)
    assert fetched.display_name == "新名称"


async def test_delete_mcp_connection(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv")

    deleted = await connection_service.delete_mcp_connection(conn_session, c.id)
    assert deleted is True

    assert await connection_service.get_mcp_connection(conn_session, c.id) is None

    # deleting again returns False
    assert await connection_service.delete_mcp_connection(conn_session, c.id) is False


# =============================================================================
# === Scope Normalization ===
# =============================================================================


async def test_system_scope_normalizes_to_global(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv", scope_type="system", scope_id="")
    assert c.scope_type == "system"
    assert c.scope_id == "global"


async def test_invalid_scope_type_raises(conn_session):
    await _add_server(conn_session, "srv")
    with pytest.raises(ValueError, match="scope_type must be one of"):
        await _create_connection(conn_session, "srv", scope_type="invalid", scope_id="x")


async def test_empty_scope_id_for_non_system_raises(conn_session):
    await _add_server(conn_session, "srv")
    with pytest.raises(ValueError, match="scope_id is required for"):
        await _create_connection(conn_session, "srv", scope_type="user", scope_id="")


async def test_user_scope_requires_existing_user(conn_session):
    await _add_server(conn_session, "srv")
    with pytest.raises(ValueError, match="User '404' does not exist"):
        await connection_service.create_mcp_connection(
            conn_session,
            server_name="srv",
            scope_type="user",
            scope_id="404",
            credential_blob='{"secrets":{"token":"my-token"}}',
            created_by="tester",
        )


# =============================================================================
# === Status Management ===
# =============================================================================


async def test_set_mcp_connection_status(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv")
    assert c.status == "active"

    updated = await connection_service.set_mcp_connection_status(
        conn_session, c.id, status="disabled", updated_by="admin"
    )
    assert updated.status == "disabled"
    assert updated.updated_by == "admin"

    fetched = await connection_service.get_mcp_connection(conn_session, c.id)
    assert fetched.status == "disabled"


async def test_set_mcp_connection_status_not_found(conn_session):
    with pytest.raises(ValueError, match="does not exist"):
        await connection_service.set_mcp_connection_status(conn_session, 9999, status="disabled")


async def test_invalid_status_raises(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv")
    with pytest.raises(ValueError, match="status must be one of"):
        await connection_service.set_mcp_connection_status(conn_session, c.id, status="bogus")


async def test_set_status_valid_transitions(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv")

    for status in ("disabled", "reauth_required", "invalid", "active"):
        updated = await connection_service.set_mcp_connection_status(conn_session, c.id, status=status)
        assert updated.status == status


# =============================================================================
# === Pagination & Search ===
# =============================================================================


async def test_list_mcp_connections_page(conn_session):
    await _add_server(conn_session, "srv")
    ids = []
    for i in range(5):
        c = await _create_connection(conn_session, "srv", scope_type="user", scope_id=str(i), display_name=f"连接{i}")
        ids.append(c.id)

    page1, total = await connection_service.list_mcp_connections_page(
        conn_session, server_name="srv", page=1, page_size=2
    )
    assert total == 5
    assert len(page1) == 2
    assert [r.id for r in page1] == ids[:2]

    page2, total = await connection_service.list_mcp_connections_page(
        conn_session, server_name="srv", page=2, page_size=2
    )
    assert total == 5
    assert len(page2) == 2
    assert [r.id for r in page2] == ids[2:4]

    # page_size 上限
    page_big, total = await connection_service.list_mcp_connections_page(
        conn_session, server_name="srv", page=1, page_size=999
    )
    assert total == 5
    assert len(page_big) == 5


async def test_count_mcp_connections(conn_session):
    await _add_server(conn_session, "srv")
    for i in range(3):
        await _create_connection(conn_session, "srv", scope_type="user", scope_id=str(i))

    count = await connection_service.count_mcp_connections(conn_session, server_name="srv")
    assert count == 3

    count_filtered = await connection_service.count_mcp_connections(
        conn_session, server_name="srv", scope_type="system"
    )
    assert count_filtered == 0


# =============================================================================
# === Duplicate Detection ===
# =============================================================================


async def test_create_duplicate_scope_raises(conn_session):
    await _add_server(conn_session, "srv")
    await _create_connection(conn_session, "srv", scope_type="user", scope_id="1")

    with pytest.raises(ValueError, match="每个作用域只允许一个连接"):
        await _create_connection(conn_session, "srv", scope_type="user", scope_id="1")


# =============================================================================
# === Scope Mismatch ===
# =============================================================================


async def test_create_connection_with_wrong_scope_raises(conn_session):
    await _add_server(conn_session, "srv", auth_config_json=USER_BOUND_AUTH_CONFIG)

    # system scope 创建 user-bound server 的连接应该失败
    with pytest.raises(ValueError, match="无法创建"):
        await _create_connection(conn_session, "srv", scope_type="system", scope_id="global")


async def test_create_connection_with_matching_scope_succeeds(conn_session):
    await _add_server(conn_session, "srv", auth_config_json=SYSTEM_BOUND_AUTH_CONFIG)

    c = await _create_connection(conn_session, "srv", scope_type="system", scope_id="global")
    assert c.scope_type == "system"
    assert c.scope_id == "global"


async def test_create_connection_with_inline_scope_allows_any_scope(conn_session):
    """legacy_static 服务器的 binding_scope 为 inline，应允许任意 scope"""
    await _add_server(
        conn_session,
        "srv",
        auth_config_json={
            "version": 1,
            "provider": "legacy_static",
            "inject": {"target": "headers", "entries": []},
        },
    )
    c = await _create_connection(conn_session, "srv", scope_type="user", scope_id="1")
    assert c.scope_type == "user"


async def test_create_connection_server_not_exist_raises(conn_session):
    with pytest.raises(ValueError, match="does not exist"):
        await _create_connection(conn_session, "nonexistent", scope_type="user", scope_id="1")


# =============================================================================
# === Credential Encryption ===
# =============================================================================


async def test_credential_is_encrypted_on_create(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv", credential_blob='{"secrets":{"token":"my-token"}}')
    assert c.credential_blob != '{"secrets":{"token":"my-token"}}'
    decrypted = decrypt_credential_blob(c.credential_blob)
    assert decrypted == '{"secrets":{"token":"my-token"}}'


async def test_credential_is_encrypted_on_update(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv", credential_blob="initial")

    updated = await connection_service.update_mcp_connection(
        conn_session, c.id, credential_blob='{"secrets":{"new":"value"}}'
    )
    decrypted = decrypt_credential_blob(updated.credential_blob)
    assert decrypted == '{"secrets":{"new":"value"}}'


async def test_credential_can_be_cleared(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv", credential_blob="data")

    updated = await connection_service.update_mcp_connection(conn_session, c.id, credential_blob=None)
    assert updated.credential_blob is None


# =============================================================================
# === to_dict 不泄露凭据 ===
# =============================================================================


async def test_to_dict_excludes_credentials_by_default(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv", credential_blob='{"secret":"data"}')

    d = c.to_dict()
    assert "credential_blob" not in d
    assert d["has_credentials"] is True


async def test_to_dict_includes_credentials_when_requested(conn_session):
    await _add_server(conn_session, "srv")
    c = await _create_connection(conn_session, "srv", credential_blob='{"secret":"data"}')

    d = c.to_dict(include_credentials=True)
    assert "credential_blob" in d
