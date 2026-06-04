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
            department_id=42,
        )

    async def fake_required_user():
        return User(
            username="admin" if allow_admin else "user",
            user_id="admin" if allow_admin else "user",
            password_hash="x",
            role="admin" if allow_admin else "user",
            department_id=42,
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


def test_create_mcp_server_forwards_auth_config(monkeypatch):
    captured = {}

    class DummyServer:
        def to_dict(self):
            return {"name": "gateway", "auth_config": {"provider": "custom_http_token"}}

    async def fake_create_mcp_server(db, **kwargs):
        del db
        captured.update(kwargs)
        return DummyServer()

    monkeypatch.setattr("server.routers.mcp_router.create_mcp_server", fake_create_mcp_server)

    client = TestClient(_build_app())
    resp = client.post(
        "/api/system/mcp-servers",
        json={
            "name": "gateway",
            "transport": "streamable_http",
            "url": "http://gateway.local/mcp",
            "auth_config": {
                "version": 1,
                "provider": "custom_http_token",
                "binding_scope": "department",
                "inject": {
                    "target": "headers",
                    "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
                },
                "token_request": {"url": "http://gateway.local/auth/token", "method": "POST"},
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["auth_config"] == {
        "version": 1,
        "provider": "custom_http_token",
        "binding_scope": "department",
        "manifest_scope": "server",
        "inject": {
            "target": "headers",
            "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
        },
        "refresh_policy": {"pre_refresh_seconds": 0, "retry_once_on_401": False},
        "token_request": {"url": "http://gateway.local/auth/token", "method": "POST"},
    }


def test_update_mcp_server_forwards_auth_config(monkeypatch):
    captured = {}

    class DummyServer:
        def to_dict(self):
            return {"name": "gateway", "auth_config": {"provider": "bound_secret"}}

    async def fake_update_mcp_server(db, name, **kwargs):
        del db
        captured["name"] = name
        captured.update(kwargs)
        return DummyServer()

    monkeypatch.setattr("server.routers.mcp_router.update_mcp_server", fake_update_mcp_server)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/gateway",
        json={
            "description": "updated",
            "auth_config": {
                "version": 1,
                "provider": "bound_secret",
                "binding_scope": "department",
                "inject": {
                    "target": "headers",
                    "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
                },
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["name"] == "gateway"
    assert captured["auth_config"] == {
        "version": 1,
        "provider": "bound_secret",
        "binding_scope": "department",
        "manifest_scope": "server",
        "inject": {
            "target": "headers",
            "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
        },
        "refresh_policy": {"pre_refresh_seconds": 0, "retry_once_on_401": False},
        "token_request": None,
    }


def test_create_mcp_server_rejects_invalid_auth_config(monkeypatch):
    async def fake_create_mcp_server(db, **kwargs):
        raise AssertionError("create_mcp_server should not be called when auth_config is invalid")

    monkeypatch.setattr("server.routers.mcp_router.create_mcp_server", fake_create_mcp_server)

    client = TestClient(_build_app())
    resp = client.post(
        "/api/system/mcp-servers",
        json={
            "name": "gateway",
            "transport": "streamable_http",
            "url": "http://gateway.local/mcp",
            "auth_config": {
                "version": 1,
                "provider": "custom_http_token",
                "binding_scope": "department",
                "inject": {
                    "target": "headers",
                    "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
                },
            },
        },
    )
    assert resp.status_code == 400, resp.text
    assert "auth_config 配置无效" in resp.json()["detail"]


def test_list_mcp_connections(monkeypatch):
    class DummyConnection:
        def __init__(self, connection_id):
            self.connection_id = connection_id

        def to_dict(self):
            return {"id": self.connection_id, "scope_type": "department", "status": "active"}

    async def fake_get_mcp_server(db, name):
        del db
        return type("DummyServer", (), {"name": name})()

    async def fake_list_mcp_connections(db, **kwargs):
        del db, kwargs
        return [DummyConnection(1), DummyConnection(2)]

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_router.list_mcp_connections", fake_list_mcp_connections)

    client = TestClient(_build_app())
    resp = client.get("/api/system/mcp-servers/gateway/connections")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == [
        {"id": 1, "scope_type": "department", "status": "active"},
        {"id": 2, "scope_type": "department", "status": "active"},
    ]


def test_create_mcp_connection(monkeypatch):
    captured = {}

    class DummyConnection:
        def to_dict(self):
            return {"id": 7, "scope_type": "department", "status": "active"}

    async def fake_get_mcp_server(db, name):
        del db
        return type("DummyServer", (), {"name": name})()

    async def fake_create_mcp_connection(db, **kwargs):
        del db
        captured.update(kwargs)
        return DummyConnection()

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_router.create_mcp_connection", fake_create_mcp_connection)

    client = TestClient(_build_app())
    resp = client.post(
        "/api/system/mcp-servers/gateway/connections",
        json={
            "scope_type": "department",
            "scope_id": "42",
            "display_name": "财务部共享连接",
            "external_subject": "finance-user",
            "credential": {"secrets": {"access_token": "token-1"}},
            "meta_json": {"tenant": "finance"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["server_name"] == "gateway"
    assert captured["scope_type"] == "department"
    assert captured["scope_id"] == "42"
    assert captured["credential_blob"] == '{"secrets": {"access_token": "token-1"}}'
    assert captured["created_by"] == "admin"


def test_update_mcp_connection_status(monkeypatch):
    captured = {}

    class DummyConnection:
        def to_dict(self):
            return {"id": 7, "status": "reauth_required"}

    async def fake_get_mcp_server(db, name):
        del db
        return type("DummyServer", (), {"name": name})()

    async def fake_get_mcp_connection(db, connection_id):
        del db
        return type("DummyConnectionRef", (), {"id": connection_id, "server_name": "gateway"})()

    async def fake_set_mcp_connection_status(db, connection_id, **kwargs):
        del db
        captured["connection_id"] = connection_id
        captured.update(kwargs)
        return DummyConnection()

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_router.get_mcp_connection", fake_get_mcp_connection)
    monkeypatch.setattr("server.routers.mcp_router.set_mcp_connection_status", fake_set_mcp_connection_status)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/gateway/connections/7/status",
        json={"status": "reauth_required"},
    )
    assert resp.status_code == 200, resp.text
    assert captured == {
        "connection_id": 7,
        "status": "reauth_required",
        "updated_by": "admin",
    }


def test_update_mcp_connection(monkeypatch):
    captured = {}

    class DummyConnection:
        def to_dict(self):
            return {"id": 7, "display_name": "新连接名", "status": "active"}

    async def fake_get_mcp_server(db, name):
        del db
        return type("DummyServer", (), {"name": name})()

    async def fake_get_mcp_connection(db, connection_id):
        del db
        return type("DummyConnectionRef", (), {"id": connection_id, "server_name": "gateway"})()

    async def fake_update_mcp_connection(db, connection_id, **kwargs):
        del db
        captured["connection_id"] = connection_id
        captured.update(kwargs)
        return DummyConnection()

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_router.get_mcp_connection", fake_get_mcp_connection)
    monkeypatch.setattr("server.routers.mcp_router.update_mcp_connection", fake_update_mcp_connection)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/gateway/connections/7",
        json={
            "display_name": "新连接名",
            "credential": {"secrets": {"access_token": "token-2"}},
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["connection_id"] == 7
    assert captured["display_name"] == "新连接名"
    assert captured["credential_blob"] == '{"secrets": {"access_token": "token-2"}}'
    assert captured["updated_by"] == "admin"


def test_delete_mcp_connection(monkeypatch):
    captured = {}

    async def fake_get_mcp_server(db, name):
        del db
        return type("DummyServer", (), {"name": name})()

    async def fake_get_mcp_connection(db, connection_id):
        del db
        return type("DummyConnectionRef", (), {"id": connection_id, "server_name": "gateway"})()

    async def fake_delete_mcp_connection(db, connection_id):
        del db
        captured["connection_id"] = connection_id
        return True

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_router.get_mcp_connection", fake_get_mcp_connection)
    monkeypatch.setattr("server.routers.mcp_router.delete_mcp_connection", fake_delete_mcp_connection)

    client = TestClient(_build_app())
    resp = client.delete("/api/system/mcp-servers/gateway/connections/7")
    assert resp.status_code == 200, resp.text
    assert captured == {"connection_id": 7}


def test_test_mcp_connection_route(monkeypatch):
    captured = {}

    async def fake_get_mcp_server(db, name):
        del db
        return type("DummyServer", (), {"name": name})()

    async def fake_get_mcp_connection(db, connection_id):
        del db
        return type("DummyConnectionRef", (), {"id": connection_id, "server_name": "gateway"})()

    async def fake_test_mcp_connection(db, connection_id, *, updated_by=None):
        del db
        captured["connection_id"] = connection_id
        captured["updated_by"] = updated_by
        return {"tool_count": 3}

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_router.get_mcp_connection", fake_get_mcp_connection)
    monkeypatch.setattr("server.routers.mcp_router.test_mcp_connection", fake_test_mcp_connection)

    client = TestClient(_build_app())
    resp = client.post("/api/system/mcp-servers/gateway/connections/7/test")
    assert resp.status_code == 200, resp.text
    assert resp.json()["tool_count"] == 3
    assert captured == {"connection_id": 7, "updated_by": "admin"}


def test_reauthorize_mcp_connection_route(monkeypatch):
    captured = {}

    class DummyConnection:
        def to_dict(self):
            return {"id": 7, "status": "active"}

    async def fake_get_mcp_server(db, name):
        del db
        return type("DummyServer", (), {"name": name})()

    async def fake_get_mcp_connection(db, connection_id):
        del db
        return type("DummyConnectionRef", (), {"id": connection_id, "server_name": "gateway"})()

    async def fake_reauthorize_mcp_connection(db, connection_id, *, updated_by=None):
        del db
        captured["connection_id"] = connection_id
        captured["updated_by"] = updated_by
        return DummyConnection()

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_router.get_mcp_connection", fake_get_mcp_connection)
    monkeypatch.setattr("server.routers.mcp_router.reauthorize_mcp_connection", fake_reauthorize_mcp_connection)

    client = TestClient(_build_app())
    resp = client.post("/api/system/mcp-servers/gateway/connections/7/reauth")
    assert resp.status_code == 200, resp.text
    assert captured == {"connection_id": 7, "updated_by": "admin"}


def test_update_mcp_connection_status_rejects_connection_from_other_server(monkeypatch):
    async def fake_get_mcp_server(db, name):
        del db
        return type("DummyServer", (), {"name": name})()

    async def fake_get_mcp_connection(db, connection_id):
        del db
        return type("DummyConnectionRef", (), {"id": connection_id, "server_name": "other-gateway"})()

    async def fake_set_mcp_connection_status(db, connection_id, **kwargs):
        raise AssertionError("should not update a connection that belongs to another server")

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_router.get_mcp_connection", fake_get_mcp_connection)
    monkeypatch.setattr("server.routers.mcp_router.set_mcp_connection_status", fake_set_mcp_connection_status)

    client = TestClient(_build_app())
    resp = client.put(
        "/api/system/mcp-servers/gateway/connections/7/status",
        json={"status": "reauth_required"},
    )
    assert resp.status_code == 404, resp.text


def test_test_mcp_server_requires_connection_level_test_for_bound_auth(monkeypatch):
    class DummyServer:
        auth_config_json = {
            "version": 1,
            "provider": "custom_http_token",
            "binding_scope": "department",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "token_request": {"url": "http://gateway.local/auth/token", "method": "POST"},
        }

    async def fake_get_server_or_404(db, name):
        del db, name
        return DummyServer()

    monkeypatch.setattr("server.routers.mcp_router.get_server_or_404", fake_get_server_or_404)

    client = TestClient(_build_app())
    resp = client.post("/api/system/mcp-servers/gateway/test", json={})
    assert resp.status_code == 400, resp.text


def test_get_mcp_server_tools_uses_current_admin_auth_context(monkeypatch):
    captured = {}

    class DummyServer:
        disabled_tools = ["tool_b"]

    class DummyArgsSchema:
        @staticmethod
        def schema():
            return {"properties": {"city": {"type": "string"}}, "required": ["city"]}

    class DummyTool:
        name = "tool_a"
        description = "tool a"
        metadata = {"id": "mcp__gateway__toolA"}
        args_schema = DummyArgsSchema()

    async def fake_get_server_or_404(db, name):
        del db
        assert name == "gateway"
        return DummyServer()

    async def fake_get_all_mcp_tools(server_name, *, auth_context=None, db=None, http_client=None, force_refresh=False):
        del db, http_client, force_refresh
        captured["server_name"] = server_name
        captured["user_id"] = auth_context.user_id
        captured["department_id"] = auth_context.department_id
        return [DummyTool()]

    monkeypatch.setattr("server.routers.mcp_router.get_server_or_404", fake_get_server_or_404)
    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_tools", fake_get_all_mcp_tools)

    client = TestClient(_build_app())
    resp = client.get("/api/system/mcp-servers/gateway/tools")

    assert resp.status_code == 200, resp.text
    assert captured == {
        "server_name": "gateway",
        "user_id": "admin",
        "department_id": "42",
    }
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["data"][0]["required"] == ["city"]
    assert payload["data"][0]["enabled"] is True


def test_get_mcp_server_tools_returns_403_when_bound_connection_missing(monkeypatch):
    class DummyServer:
        disabled_tools = []

    async def fake_get_server_or_404(db, name):
        del db, name
        return DummyServer()

    async def fake_get_all_mcp_tools(server_name, *, auth_context=None, db=None, http_client=None, force_refresh=False):
        del server_name, auth_context, db, http_client, force_refresh
        raise ValueError("Active MCP connection not found for server 'gateway' and scope department:42")

    monkeypatch.setattr("server.routers.mcp_router.get_server_or_404", fake_get_server_or_404)
    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_tools", fake_get_all_mcp_tools)

    client = TestClient(_build_app())
    resp = client.get("/api/system/mcp-servers/gateway/tools")

    assert resp.status_code == 403, resp.text


def test_refresh_mcp_server_tools_returns_403_when_bound_connection_missing(monkeypatch):
    async def fake_get_server_or_404(db, name):
        del db, name
        return type("DummyServer", (), {})()

    async def fake_get_all_mcp_tools(server_name, *, auth_context=None, db=None, http_client=None, force_refresh=False):
        del server_name, auth_context, db, http_client, force_refresh
        raise ValueError("Active MCP connection not found for server 'gateway' and scope department:42")

    monkeypatch.setattr("server.routers.mcp_router.get_server_or_404", fake_get_server_or_404)
    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_tools", fake_get_all_mcp_tools)

    client = TestClient(_build_app())
    resp = client.post("/api/system/mcp-servers/gateway/tools/refresh")

    assert resp.status_code == 403, resp.text


def test_delete_mcp_server_defaults_to_retire(monkeypatch):
    captured = {}

    class DummyServer:
        created_by = "tester"

        def to_dict(self):
            return {"name": "gateway", "enabled": False}

    async def fake_get_mcp_server(db, name):
        del db
        return DummyServer()

    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        del db
        captured["name"] = name
        captured["enabled"] = enabled
        captured["updated_by"] = updated_by
        return False, DummyServer()

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    client = TestClient(_build_app())
    resp = client.delete("/api/system/mcp-servers/gateway")

    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "服务器 'gateway' 已退役"
    assert captured == {
        "name": "gateway",
        "enabled": False,
        "updated_by": "admin",
    }


def test_delete_mcp_server_hard_delete_returns_conflict(monkeypatch):
    class DummyServer:
        created_by = "tester"
        enabled = 0

    async def fake_get_mcp_server(db, name):
        del db
        return DummyServer()

    async def fake_get_dependency_summary(db, name):
        del db, name
        return {
            "has_references": True,
            "connections": [{"scope_type": "department", "scope_id": "42", "status": "active"}],
            "skills": [{"slug": "finance-skill", "name": "Finance Skill"}],
            "agent_configs": [],
        }

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server_dependency_summary", fake_get_dependency_summary)

    client = TestClient(_build_app())
    resp = client.delete("/api/system/mcp-servers/gateway?hard=true")

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["connections"] == [
        {"scope_type": "department", "scope_id": "42", "status": "active"}
    ]
