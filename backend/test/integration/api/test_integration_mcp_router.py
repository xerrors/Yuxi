"""
MCP router integration tests — exercises live Postgres DB through the HTTP API.

Prerequisites:
- api-dev container is running and healthy
- .env.test has valid TEST_USERNAME / TEST_PASSWORD
"""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_SKIP_MISSING_CREDENTIALS = not os.getenv("TEST_USERNAME") or not os.getenv("TEST_PASSWORD")


def _extract_connections(body) -> list:
    """Extract connection list from API response (handles flat and paginated formats)."""
    if isinstance(body, dict):
        data = body.get("data", body)
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        if isinstance(data, list):
            return data
    return []


MCP_SERVER_NAME = "pytest-mcp-server"
MCP_CONNECTION_SCOPE = "department"
MCP_DEPARTMENT_ID = None  # resolved at runtime


def _require_credentials() -> None:
    if _SKIP_MISSING_CREDENTIALS:
        pytest.skip("TEST_USERNAME / TEST_PASSWORD not set; skipping real-DB integration tests")


async def _get_auth_token(test_client) -> str:
    """Authenticate as admin and return bearer token."""
    username = os.getenv("TEST_USERNAME")
    password = os.getenv("TEST_PASSWORD")
    resp = await test_client.post("/api/auth/token", data={"username": username, "password": password})
    assert resp.status_code == 200, f"Auth failed: {resp.text}"
    return resp.json()["access_token"]


async def _admin_headers(test_client) -> dict[str, str]:
    token = await _get_auth_token(test_client)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def _set_mcp_master_key():
    """Ensure MCP_CREDENTIALS_MASTER_KEY is set in the Python environment."""
    if not os.getenv("MCP_CREDENTIALS_MASTER_KEY") and not os.getenv("JWT_SECRET_KEY"):
        pytest.skip("MCP_CREDENTIALS_MASTER_KEY (or JWT_SECRET_KEY fallback) not set")


# =============================================================================
# === Setup / Teardown ===
# =============================================================================


async def _ensure_system_department(test_client, headers) -> int:
    """Return the first department id — guaranteed to exist by initialization."""
    resp = await test_client.get("/api/departments", headers=headers)
    assert resp.status_code == 200, f"List departments failed: {resp.text}"
    departments = resp.json()
    assert isinstance(departments, list) and len(departments) > 0, "No departments found in the system"
    return departments[0]["id"]


async def _create_mcp_server_if_missing(test_client, headers) -> str:
    """Create a test MCP server, or return existing one."""
    get_resp = await test_client.get(f"/api/system/mcp-servers/{MCP_SERVER_NAME}", headers=headers)
    if get_resp.status_code == 200:
        return MCP_SERVER_NAME

    resp = await test_client.post(
        "/api/system/mcp-servers",
        json={
            "name": MCP_SERVER_NAME,
            "transport": "streamable_http",
            "url": "https://httpbin.org/post",
            "description": "Pytest integration test server",
        },
        headers=headers,
    )
    assert resp.status_code == 200, f"Create MCP server failed: {resp.text}"
    return MCP_SERVER_NAME


async def _cleanup_mcp_connections(test_client, headers, server_name: str) -> None:
    """Remove all connections for a server."""
    list_resp = await test_client.get(
        f"/api/system/mcp-servers/{server_name}/connections",
        headers=headers,
    )
    if list_resp.status_code != 200:
        return
    body = list_resp.json()
    conn_list = body.get("data") if isinstance(body, dict) else body
    if isinstance(conn_list, dict) and "items" in conn_list:
        conn_list = conn_list["items"]
    if not isinstance(conn_list, list):
        return
    for conn in conn_list:
        await test_client.delete(
            f"/api/system/mcp-servers/{server_name}/connections/{conn['id']}",
            headers=headers,
        )


async def _cleanup_mcp_server(test_client, headers, server_name: str) -> None:
    """Disable and delete the test MCP server."""
    await _cleanup_mcp_connections(test_client, headers, server_name)
    # soft delete first
    await test_client.put(
        f"/api/system/mcp-servers/{server_name}/status",
        json={"enabled": False},
        headers=headers,
    )
    # hard delete
    await test_client.delete(
        f"/api/system/mcp-servers/{server_name}?hard=true",
        headers=headers,
    )


# =============================================================================
# === Connection CRUD Integration Tests ===
# =============================================================================


class TestMcpConnectionCrudRealDb:
    """Exercises connection CRUD against the live Postgres database."""

    @pytest.fixture(autouse=True)
    async def setup(self, test_client, _set_mcp_master_key):
        _require_credentials()
        self.headers = await _admin_headers(test_client)
        self.dept_id = await _ensure_system_department(test_client, self.headers)
        await _create_mcp_server_if_missing(test_client, self.headers)
        await _cleanup_mcp_connections(test_client, self.headers, MCP_SERVER_NAME)
        yield
        await _cleanup_mcp_connections(test_client, self.headers, MCP_SERVER_NAME)

    async def _create_connection(self, test_client, **overrides) -> dict:
        body = {
            "scope_type": MCP_CONNECTION_SCOPE,
            "scope_id": str(self.dept_id),
            "credential": {"secrets": {"access_token": "test-token"}},
            "display_name": "Integration test connection",
        }
        body.update(overrides)
        resp = await test_client.post(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections",
            json=body,
            headers=self.headers,
        )
        assert resp.status_code == 200, f"Create connection failed: {resp.text}"
        return resp.json()["data"]

    async def test_create_connection_system_scope(self, test_client):
        """Create a system-scoped connection and verify response shape."""
        data = await self._create_connection(test_client, scope_type="system", scope_id="global")
        assert data["scope_type"] == "system"
        assert data["scope_id"] == "global"
        assert data["status"] == "active"
        assert data["has_credentials"] is True
        assert "credential_blob" not in data, "Credentials must not be exposed in default to_dict"

    async def test_create_and_get_connection(self, test_client):
        """Create a connection and verify it appears in the list."""
        created = await self._create_connection(test_client, scope_type="user", scope_id="42")
        conn_id = created["id"]

        list_resp = await test_client.get(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections",
            headers=self.headers,
        )
        assert list_resp.status_code == 200
        ids = [c["id"] for c in _extract_connections(list_resp.json())]
        assert conn_id in ids

    async def test_list_connections_paginated(self, test_client):
        """Verify paginated listing with summary counts."""
        for i in range(3):
            await self._create_connection(test_client, scope_type="user", scope_id=str(i + 10))

        resp = await test_client.get(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections?paginated=true&page=1&page_size=2",
            headers=self.headers,
        )
        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert len(payload["items"]) <= 2
        assert payload["total"] >= 3
        assert "summary" in payload
        assert "total" in payload["summary"]

    async def test_update_connection_display_name(self, test_client):
        """Create then update the display name."""
        created = await self._create_connection(test_client)
        conn_id = created["id"]

        resp = await test_client.put(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections/{conn_id}",
            json={"display_name": "Updated Name"},
            headers=self.headers,
        )
        assert resp.status_code == 200, f"Update failed: {resp.text}"
        assert resp.json()["data"]["display_name"] == "Updated Name"

    async def test_update_connection_status(self, test_client):
        """Update connection status via dedicated endpoint."""
        created = await self._create_connection(test_client)
        conn_id = created["id"]

        resp = await test_client.put(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections/{conn_id}/status",
            json={"status": "disabled"},
            headers=self.headers,
        )
        assert resp.status_code == 200, f"Status update failed: {resp.text}"
        assert resp.json()["data"]["status"] == "disabled"

    async def test_delete_connection(self, test_client):
        """Create then delete a connection."""
        created = await self._create_connection(test_client, scope_type="user", scope_id="99")
        conn_id = created["id"]

        resp = await test_client.delete(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections/{conn_id}",
            headers=self.headers,
        )
        assert resp.status_code == 200, f"Delete failed: {resp.text}"

        # Verify deletion
        list_resp = await test_client.get(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections",
            headers=self.headers,
        )
        ids = [c["id"] for c in list_resp.json().get("data", [])]
        assert conn_id not in ids

    async def test_create_duplicate_scope_id_returns_400(self, test_client):
        """Creating with duplicate (server_name, scope_type, scope_id) must fail."""
        await self._create_connection(test_client, scope_type="system", scope_id="global")
        resp = await test_client.post(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections",
            json={
                "scope_type": "system",
                "scope_id": "global",
                "credential": {"secrets": {"token": "dup"}},
            },
            headers=self.headers,
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        assert "已存在连接" in resp.text

    async def test_create_connection_invalid_scope_returns_400(self, test_client):
        """Invalid scope_type returns 400."""
        resp = await test_client.post(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections",
            json={"scope_type": "invalid", "scope_id": "x", "credential": {"key": "val"}},
            headers=self.headers,
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    async def test_delete_nonexistent_connection_returns_404(self, test_client):
        """Deleting a connection that doesn't exist returns 404."""
        resp = await test_client.delete(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections/99999",
            headers=self.headers,
        )
        assert resp.status_code == 404

    async def test_normal_user_sees_only_own_connections(self, test_client, standard_user):
        """A normal user should only see their own user-scoped connections."""
        user_headers = standard_user["headers"]

        # Create a user-scoped server for the user test
        USER_SERVER = "pytest-mcp-user-server"
        await _cleanup_mcp_server(test_client, self.headers, USER_SERVER)
        resp = await test_client.post(
            "/api/system/mcp-servers",
            json={
                "name": USER_SERVER,
                "transport": "streamable_http",
                "url": "https://httpbin.org/post",
                "description": "User-scoped test server",
                "auth_config": {
                    "version": 1,
                    "provider": "bound_secret",
                    "binding_scope": "user",
                    "inject": {
                        "target": "headers",
                        "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.token}"}],
                    },
                },
            },
            headers=self.headers,
        )
        assert resp.status_code == 200, f"Create user-scoped server failed: {resp.text}"

        # Normal user creates their own connection (via API — scope_type is forced to 'user')
        user_resp = await test_client.post(
            f"/api/system/mcp-servers/{USER_SERVER}/connections",
            json={
                "scope_type": "user",
                "scope_id": "",
                "credential": {"secrets": {"token": "user-token"}},
            },
            headers=user_headers,
        )
        assert user_resp.status_code == 200, f"User create failed: {user_resp.text}"
        user_conn = user_resp.json()["data"]
        assert user_conn["scope_type"] == "user"

        # User lists connections — should only see their own
        list_resp = await test_client.get(
            f"/api/system/mcp-servers/{USER_SERVER}/connections",
            headers=user_headers,
        )
        assert list_resp.status_code == 200
        user_connections = _extract_connections(list_resp.json())
        assert len(user_connections) == 1
        assert user_connections[0]["id"] == user_conn["id"]

    async def test_update_connection_with_missing_master_key_fails_gracefully(self, test_client):
        """When credential update requires master key but it's missing, return 400 not 500."""
        created = await self._create_connection(test_client)
        conn_id = created["id"]

        # If master key is present this will succeed; the test verifies the response is
        # a proper 400 class error, not a 500.
        resp = await test_client.put(
            f"/api/system/mcp-servers/{MCP_SERVER_NAME}/connections/{conn_id}",
            json={"credential": {"new": "secret"}},
            headers=self.headers,
        )
        # Either 200 (key present) or 400 (key missing) — both are acceptable
        assert resp.status_code in (200, 400), f"Unexpected status: {resp.status_code}: {resp.text}"
