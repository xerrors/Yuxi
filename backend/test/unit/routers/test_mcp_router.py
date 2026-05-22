from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers.mcp_router import mcp
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.storage.postgres.models_business import User


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
            user_id="admin",
            password_hash="x",
            role="admin",
        )

    async def fake_required_user():
        return User(
            username="admin" if allow_admin else "user",
            user_id="admin" if allow_admin else "user",
            password_hash="x",
            role="admin" if allow_admin else "user",
        )

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_admin_user] = fake_admin_user
    app.dependency_overrides[get_required_user] = fake_required_user
    return app


def test_update_mcp_server_status(monkeypatch):
    captured = {}

    class DummyServer:
        def __init__(self, enabled):
            self.enabled = enabled

        def to_dict(self):
            return {"name": "sequentialthinking", "enabled": self.enabled}

    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        captured["name"] = name
        captured["enabled"] = enabled
        captured["updated_by"] = updated_by
        return enabled, DummyServer(enabled)

    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/sequentialthinking/status", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["enabled"] is False
    assert payload["data"]["enabled"] is False
    assert captured == {"name": "sequentialthinking", "enabled": False, "updated_by": "admin"}


def test_update_mcp_server_status_not_found(monkeypatch):
    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        raise ValueError(f"Server '{name}' does not exist")

    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.put("/api/system/mcp-servers/missing/status", json={"enabled": True})
    assert resp.status_code == 404, resp.text


def test_get_mcp_servers_normal_user_is_stripped(monkeypatch):
    class DummyServer:
        def __init__(self):
            self.name = "test-mcp"
            self.description = "test mcp description"
            self.transport = "stdio"
            self.url = "http://localhost:8000"
            self.command = "python"
            self.args = ["-m", "mcp"]
            self.env = {"API_KEY": "secret"}
            self.headers = {"Auth": "Bearer secret"}
            self.enabled = 1

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
        return [DummyServer()]

    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_servers", fake_get_all_mcp_servers)

    # 1. 管理员请求，应该返回全部字段
    client_admin = TestClient(_build_app(allow_admin=True))
    resp_admin = client_admin.get("/api/system/mcp-servers")
    assert resp_admin.status_code == 200
    data_admin = resp_admin.json()["data"][0]
    assert data_admin["url"] == "http://localhost:8000"
    assert data_admin["command"] == "python"
    assert data_admin["env"] == {"API_KEY": "secret"}

    # 2. 普通用户请求，敏感字段及一切非安全白名单字段应该被彻底脱敏
    client_user = TestClient(_build_app(allow_admin=False))
    resp_user = client_user.get("/api/system/mcp-servers")
    assert resp_user.status_code == 200
    data_user = resp_user.json()["data"][0]
    assert "url" not in data_user
    assert "command" not in data_user
    assert "env" not in data_user
    assert "headers" not in data_user
    assert "transport" not in data_user  # NOTE: 进一步验证连 transport 等配置层元数据也一并过滤
    assert data_user["name"] == "test-mcp"
    assert data_user["description"] == "test mcp description"
    assert data_user["enabled"] is True
