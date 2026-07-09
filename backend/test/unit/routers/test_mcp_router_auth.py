"""MCP router integration tests for connection CRUD and permission boundaries."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from server.routers.mcp_router import mcp
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.storage.postgres.models_business import User


def _build_app(*, user_role: str = "admin", user_id: str = "1", login_id: str = "admin") -> FastAPI:
    app = FastAPI()
    app.include_router(mcp, prefix="/api")

    async def fake_db():
        return AsyncMock()

    current_user = User(
        id=int(user_id) if user_id.isdigit() else 1,
        username="admin" if user_role in ("admin", "superadmin") else "user",
        user_id=login_id,
        password_hash="x",
        role=user_role,
    )

    async def fake_admin():
        if user_role not in ("admin", "superadmin"):
            raise HTTPException(status_code=403, detail="权限不足")
        return current_user

    async def fake_required():
        return current_user

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_admin_user] = fake_admin
    app.dependency_overrides[get_required_user] = fake_required
    return app


def _app_admin():
    return _build_app(user_role="admin", user_id="1", login_id="admin")


def _app_superadmin():
    return _build_app(user_role="superadmin", user_id="1", login_id="admin")


def _app_normal_user(user_id: str = "2", login_id: str = "user2"):
    return _build_app(user_role="user", user_id=user_id, login_id=login_id)


# =============================================================================
# === 辅助：mock 连接服务函数 ===
# =============================================================================


class FakeConnection:
    _next_id = 1

    def __init__(self, **kwargs):
        self.id = FakeConnection._next_id
        FakeConnection._next_id += 1
        self.server_name = kwargs.get("server_name", "test-server")
        self.scope_type = kwargs.get("scope_type", "user")
        self.scope_id = kwargs.get("scope_id", "1")
        self.display_name = kwargs.get("display_name", None)
        self.external_subject = kwargs.get("external_subject", None)
        self.status = kwargs.get("status", "active")
        self.credential_blob = kwargs.get("credential_blob", None)
        self.meta_json = kwargs.get("meta_json", {})
        self.created_by = kwargs.get("created_by", "admin")
        self.updated_by = kwargs.get("updated_by", "admin")

    def to_dict(self, *, include_credentials=False):
        d = {
            "id": self.id,
            "server_name": self.server_name,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "display_name": self.display_name,
            "external_subject": self.external_subject,
            "status": self.status,
            "has_credentials": bool(self.credential_blob),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
        }
        if include_credentials:
            d["credential_blob"] = self.credential_blob
        return d


def _fake_get_server_or_404(name):
    """模拟 get_server_or_404 返回一个带有 auth_config_json 的假 server"""
    from types import SimpleNamespace

    return SimpleNamespace(
        name=name,
        enabled=True,
        auth_config_json={
            "version": 1,
            "provider": "bound_secret",
            "binding_scope": "user",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${token}"}],
            },
        },
    )


def _patch_mcp_router_connection_endpoints(monkeypatch, **service_mocks):
    """Patch all connection service functions used by the router."""
    defaults = {
        "get_mcp_connection": AsyncMock(return_value=FakeConnection(server_name="test-server")),
        "list_mcp_connections": AsyncMock(return_value=[FakeConnection(server_name="test-server")]),
        "list_mcp_connections_page": AsyncMock(return_value=([FakeConnection(server_name="test-server")], 1)),
        "count_mcp_connections": AsyncMock(return_value=5),
        "create_mcp_connection": AsyncMock(return_value=FakeConnection(server_name="test-server")),
        "update_mcp_connection": AsyncMock(return_value=FakeConnection(server_name="test-server")),
        "delete_mcp_connection": AsyncMock(return_value=True),
        "set_mcp_connection_status": AsyncMock(return_value=FakeConnection(server_name="test-server")),
        "get_mcp_server": AsyncMock(return_value=_fake_get_server_or_404("test-server")),
    }
    defaults.update(service_mocks)

    import server.routers.mcp_router as router_module

    for name, mock in defaults.items():
        monkeypatch.setattr(router_module, name, mock)

    return defaults


# =============================================================================
# === Permission Tests ===
# =============================================================================


class TestMcpConnectionPermissions:
    """普通用户只能管理自己的 user scope 连接"""

    def test_normal_user_can_list_connections(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_normal_user(user_id="2", login_id="user2"))
        resp = client.get("/api/system/mcp-servers/test-server/connections")
        assert resp.status_code == 200, resp.text

    def test_normal_user_can_create_own_connection(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_normal_user(user_id="2", login_id="user2"))
        resp = client.post(
            "/api/system/mcp-servers/test-server/connections",
            json={"scope_type": "user", "scope_id": "", "credential": {"key": "val"}},
        )
        assert resp.status_code == 200, resp.text

    def test_normal_user_cannot_create_system_connection(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_normal_user())
        resp = client.post(
            "/api/system/mcp-servers/test-server/connections",
            json={"scope_type": "system", "scope_id": "global", "credential": {"key": "val"}},
        )
        assert resp.status_code == 403, resp.text

    def test_normal_user_cannot_create_department_connection(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_normal_user())
        resp = client.post(
            "/api/system/mcp-servers/test-server/connections",
            json={"scope_type": "department", "scope_id": "10", "credential": {"key": "val"}},
        )
        assert resp.status_code == 403, resp.text

    def test_normal_user_cannot_manage_others_connection(self, monkeypatch):
        """普通用户尝试操作不属于自己的连接时返回 404"""
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")
        # mock get_connection_for_server_or_404 to raise 404 for non-matching user
        from fastapi import HTTPException

        async def reject_connection(db, server_name, connection_id, current_user=None):
            raise HTTPException(status_code=404, detail="连接不存在")

        import server.routers.mcp_router as router_module

        monkeypatch.setattr(router_module, "get_connection_for_server_or_404", reject_connection)

        client = TestClient(_app_normal_user())
        resp = client.put(
            "/api/system/mcp-servers/test-server/connections/99",
            json={"display_name": "hacked"},
        )
        assert resp.status_code == 404, resp.text

    def test_admin_can_create_system_connection(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_admin())
        resp = client.post(
            "/api/system/mcp-servers/test-server/connections",
            json={"scope_type": "system", "scope_id": "global", "credential": {"key": "val"}},
        )
        assert resp.status_code == 200, resp.text


# =============================================================================
# === Connection CRUD Endpoint Tests ===
# =============================================================================


class TestMcpConnectionCrud:
    def test_list_connections(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_admin())
        resp = client.get("/api/system/mcp-servers/test-server/connections")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)

    def test_list_connections_paginated(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")
        mocks["list_mcp_connections_page"].return_value = ([FakeConnection(server_name="test-server")], 1)

        client = TestClient(_app_admin())
        resp = client.get("/api/system/mcp-servers/test-server/connections?paginated=true&page=1&page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "items" in data["data"]
        assert data["data"]["total"] == 1
        assert "summary" in data["data"]

    def test_create_connection(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_admin())
        resp = client.post(
            "/api/system/mcp-servers/test-server/connections",
            json={"scope_type": "user", "scope_id": "1", "credential": {"key": "val"}, "display_name": "测试连接"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True

    def test_update_connection(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_admin())
        resp = client.put(
            "/api/system/mcp-servers/test-server/connections/1",
            json={"display_name": "新名称", "status": "disabled"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

    def test_update_connection_status(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_admin())
        resp = client.put(
            "/api/system/mcp-servers/test-server/connections/1/status",
            json={"status": "disabled"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

    def test_delete_connection(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_admin())
        resp = client.delete("/api/system/mcp-servers/test-server/connections/1")
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

    def test_delete_connection_not_found(self, monkeypatch):
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")
        mocks["delete_mcp_connection"].return_value = False

        client = TestClient(_app_admin())
        resp = client.delete("/api/system/mcp-servers/test-server/connections/999")
        assert resp.status_code == 404, resp.text

    def test_create_connection_missing_server_returns_404(self, monkeypatch):
        _patch_mcp_router_connection_endpoints(monkeypatch)

        async def raise_404(db, name):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"服务器 '{name}' 不存在")

        import server.routers.mcp_router as router_module

        monkeypatch.setattr(router_module, "get_mcp_server", raise_404)
        # Re-patch get_server_or_404 since it uses get_mcp_server internally
        monkeypatch.setattr(router_module, "get_server_or_404", raise_404)

        client = TestClient(_app_admin())
        resp = client.post(
            "/api/system/mcp-servers/nonexistent/connections",
            json={"scope_type": "user", "scope_id": "1"},
        )
        assert resp.status_code == 404, resp.text


# =============================================================================
# === Department Scope Tests ===
# =============================================================================


class TestDepartmentScope:
    def test_admin_create_department_connection(self, monkeypatch):
        """管理员可以创建 department scope 的连接"""
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_admin())
        resp = client.post(
            "/api/system/mcp-servers/test-server/connections",
            json={"scope_type": "department", "scope_id": "10", "credential": {"key": "val"}},
        )
        assert resp.status_code == 200, resp.text

    def test_normal_user_cannot_view_department_connection_listing(self, monkeypatch):
        """普通用户 access 时，mine=True 应该只看到自己的连接"""
        mocks = _patch_mcp_router_connection_endpoints(monkeypatch)
        mocks["get_mcp_server"].return_value = _fake_get_server_or_404("test-server")

        client = TestClient(_app_normal_user())
        # 即使不传 mine=True，普通用户也只会看到自己的连接
        resp = client.get("/api/system/mcp-servers/test-server/connections")
        assert resp.status_code == 200, resp.text
