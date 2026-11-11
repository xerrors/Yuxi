from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yuxi.modules.extensions.mcp.service import MCPServerNotFoundError
from yuxi.modules.identity.models import User

from yuxi.api.routers.extensions.mcp import mcp
from yuxi.api.dependencies.auth import get_superadmin_user, get_db, get_required_user


def _build_app(*, allow_admin: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(mcp, prefix="/api")

    async def fake_db():
        return None

    async def fake_admin_user():
        if not allow_admin:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="需要管理员权限")
        return User(
            username="admin",
            uid="admin",
            password_hash="x",
            role="superadmin",
        )

    async def fake_required_user():
        return User(
            username="admin" if allow_admin else "user",
            uid="admin" if allow_admin else "user",
            password_hash="x",
            role="superadmin" if allow_admin else "user",
        )

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_superadmin_user] = fake_admin_user
    app.dependency_overrides[get_required_user] = fake_required_user
    return app


def test_update_mcp_server_status(monkeypatch):
    captured = {}

    class DummyServer:
        def __init__(self, enabled):
            self.slug = "demo-mcp"
            self.transport = "streamable_http"
            self.enabled = enabled

        def to_dict(self):
            return {"name": "demo-mcp", "enabled": self.enabled}

    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        captured["name"] = name
        captured["enabled"] = enabled
        captured["updated_by"] = updated_by
        return enabled, DummyServer(enabled)

    monkeypatch.setattr("yuxi.api.routers.extensions.mcp.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/demo-mcp/status", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["enabled"] is False
    assert payload["data"]["enabled"] is False
    assert captured == {"name": "demo-mcp", "enabled": False, "updated_by": "admin"}


def test_update_mcp_server_status_not_found(monkeypatch):
    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        raise MCPServerNotFoundError(f"Server '{name}' does not exist")

    monkeypatch.setattr("yuxi.api.routers.extensions.mcp.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/missing/status", json={"enabled": True})
    assert resp.status_code == 404, resp.text


def test_update_mcp_server_status_rejects_legacy_stdio(monkeypatch):
    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        raise ValueError("历史 stdio MCP 已被禁用")

    monkeypatch.setattr("yuxi.api.routers.extensions.mcp.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/legacy-stdio/status", json={"enabled": True})
    assert resp.status_code == 400, resp.text


def test_get_mcp_servers_normal_user_is_stripped(monkeypatch):
    class DummyServer:
        def __init__(self, slug="test-mcp"):
            self.slug = slug
            self.name = slug
            self.description = "test mcp description"
            self.transport = "streamable_http"
            self.url = "http://localhost:8000"
            self.command = "python"
            self.args = ["-m", "mcp"]
            self.env = {"API_KEY": "secret"}
            self.headers = {"Auth": "Bearer secret"}
            self.enabled = 1
            self.auth_mode = "headers"

        def to_dict(self):
            return {
                "name": self.name,
                "description": self.description,
                "transport": self.transport,
                "url": self.url,
                "command": self.command,
                "args": self.args,
                "env": self.env,
                "headers": self.headers,
                "enabled": bool(self.enabled),
            }

    async def fake_get_all_mcp_servers(db):
        return [DummyServer(), DummyServer("knowledge")]

    monkeypatch.setattr("yuxi.api.routers.extensions.mcp.get_all_mcp_servers", fake_get_all_mcp_servers)

    async def fake_states(db, servers, user_id):
        return {server.slug: "not_required" for server in servers}

    monkeypatch.setattr("yuxi.api.routers.extensions.mcp.personal_connection_states", fake_states)

    async def fake_available(db, slugs, *, user_id):
        """目录只投影执行层解析的可用性。"""
        return {"test-mcp": object()}

    monkeypatch.setattr("yuxi.api.routers.extensions.mcp.available_mcp_servers", fake_available)

    # 1. 管理员请求，应该返回全部字段
    client_admin = TestClient(_build_app(allow_admin=True))
    resp_admin = client_admin.get("/api/system/mcp-servers")
    assert resp_admin.status_code == 200
    data_admin = resp_admin.json()["data"][0]
    assert data_admin["url"] == "http://localhost:8000"
    assert data_admin["command"] == "python"
    assert data_admin["env"] == {"API_KEY": "secret"}
    assert resp_admin.json()["data"][1]["is_builtin"] is True

    # 2. 普通用户请求，敏感字段及一切非安全白名单字段应该被彻底脱敏
    client_user = TestClient(_build_app(allow_admin=False))
    resp_user = client_user.get("/api/system/mcp-servers")
    assert resp_user.status_code == 200
    by_slug = {item["slug"]: item for item in resp_user.json()["data"]}
    data_user = by_slug["test-mcp"]
    assert "url" not in data_user
    assert "command" not in data_user
    assert "env" not in data_user
    assert "headers" not in data_user
    assert "transport" not in data_user  # NOTE: 进一步验证连 transport 等配置层元数据也一并过滤
    assert data_user["name"] == "test-mcp"
    assert data_user["description"] == "test mcp description"
    assert data_user["enabled"] is True
    assert data_user["available"] is True
    assert data_user["is_builtin"] is False
    assert data_user["auth_mode"] == "headers"
    builtin = by_slug["knowledge"]
    assert builtin["is_builtin"] is True
    assert builtin["available"] is False
    assert "url" not in builtin and "headers" not in builtin


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "post",
            "/api/system/mcp-servers",
            {
                "slug": "demo-mcp",
                "name": "Demo MCP",
                "transport": "streamable_http",
                "url": "https://example.com/mcp",
                "enabled": True,
            },
        ),
        (
            "post",
            "/api/system/mcp-servers",
            {
                "slug": "unsafe-mcp",
                "name": "Unsafe MCP",
                "transport": "stdio",
                "command": "python3",
                "args": ["-c", "print('unsafe')"],
            },
        ),
        (
            "put",
            "/api/system/mcp-servers/demo-mcp",
            {
                "name": "Demo MCP",
                "transport": "streamable_http",
                "url": "https://example.com/mcp",
                "slug": "renamed-mcp",
            },
        ),
    ],
    ids=["create-extra-field", "create-stdio", "update-extra-field"],
)
def test_mcp_server_rejects_unsupported_configuration(method, path, payload):
    response = TestClient(_build_app()).request(method, path, json=payload)

    assert response.status_code == 422, response.text


def test_update_builtin_mcp_server_rejects_connection_changes(monkeypatch):
    async def fake_update_mcp_server(db, slug, **kwargs):
        raise PermissionError("系统内置连接器的连接配置由代码管理，无法通过接口修改")

    monkeypatch.setattr("yuxi.api.routers.extensions.mcp.update_mcp_server", fake_update_mcp_server)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/deepwiki-official",
        json={
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
        },
    )

    assert resp.status_code == 403, resp.text


def test_update_mcp_server_not_found(monkeypatch):
    async def fake_update_mcp_server(db, slug, **kwargs):
        raise MCPServerNotFoundError(f"Server '{slug}' does not exist")

    monkeypatch.setattr("yuxi.api.routers.extensions.mcp.update_mcp_server", fake_update_mcp_server)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/missing",
        json={"name": "Missing MCP"},
    )

    assert resp.status_code == 404, resp.text


@pytest.mark.parametrize("field,value", [("icon", "📚"), ("tags", ["docs"])])
@pytest.mark.parametrize("method,path", [("post", "/api/system/mcp-servers"), ("put", "/api/system/mcp-servers/example")])
def test_removed_presentation_fields_are_rejected(field, value, method, path):
    """管理接口拒绝已删除展示字段，不静默兼容旧输入。"""
    client = TestClient(_build_app())
    payload = {"slug": "example", "name": "Example", "transport": "streamable_http", "url": "https://example.com/mcp"}
    if method == "put":
        payload.pop("slug")
    response = getattr(client, method)(path, json={**payload, field: value})
    assert response.status_code == 422
    assert any(error["loc"][-1] == field and error["type"] == "extra_forbidden" for error in response.json()["detail"])
