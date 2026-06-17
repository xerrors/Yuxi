from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers.mcp_router import mcp
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.storage.postgres.models_business import User


BOUND_CONNECTION_MISSING = "Active MCP connection not found for server 'gateway' and scope department:42"
SECRET_AUTH_ENTRY = {"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}
TOKEN_AUTH_ENTRY = {"name": "Authorization", "value_template": "Bearer ${access_token}"}


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


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app())


@pytest.fixture
def user_client() -> TestClient:
    return TestClient(_build_app(allow_admin=False))


def _auth_config(binding_scope: str = "user") -> dict:
    return {
        "version": 1,
        "provider": "bound_secret",
        "binding_scope": binding_scope,
        "inject": {"target": "headers", "entries": [SECRET_AUTH_ENTRY]},
    }


def _normalized_auth_config(provider: str, *, token_request: dict | None = None) -> dict:
    entry = TOKEN_AUTH_ENTRY if provider == "custom_http_token" else SECRET_AUTH_ENTRY
    return {
        "version": 1,
        "provider": provider,
        "binding_scope": "department",
        "manifest_scope": "server",
        "inject": {"target": "headers", "entries": [entry]},
        "refresh_policy": {"pre_refresh_seconds": 0, "retry_once_on_401": False},
        "token_request": token_request,
    }


class DictModel(SimpleNamespace):
    def __init__(self, *, to_dict: dict | None = None, **attrs):
        super().__init__(**attrs)
        self._to_dict = to_dict

    def to_dict(self):
        if self._to_dict is not None:
            return self._to_dict
        return {key: value for key, value in vars(self).items() if key != "_to_dict"}


def _server_stub(name: str = "gateway", **attrs) -> DictModel:
    defaults = {"name": name, "enabled": 1, "auth_config_json": _auth_config()}
    defaults.update(attrs)
    return DictModel(**defaults)


def _connection_ref(connection_id: int = 7, server_name: str = "gateway", **attrs) -> DictModel:
    return DictModel(id=connection_id, server_name=server_name, **attrs)


def _patch_get_mcp_server(monkeypatch, server: object | None = None):
    async def fake_get_mcp_server(db, name):
        del db
        return server or _server_stub(name=name)

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)


def _patch_get_mcp_connection(monkeypatch, connection: object | None = None):
    async def fake_get_mcp_connection(db, connection_id):
        del db
        return connection or _connection_ref(connection_id)

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_connection", fake_get_mcp_connection)


def _patch_get_server_or_404(monkeypatch, server: object | None = None):
    async def fake_get_server_or_404(db, name):
        del db, name
        return server or _server_stub()

    monkeypatch.setattr("server.routers.mcp_router.get_server_or_404", fake_get_server_or_404)


def test_update_mcp_server_status(monkeypatch, client):
    captured = {}

    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        captured["name"] = name
        captured["enabled"] = enabled
        captured["updated_by"] = updated_by
        return enabled, DictModel(to_dict={"name": "sequentialthinking", "enabled": enabled})

    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    resp = client.put("/api/system/mcp-servers/sequentialthinking/status", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["enabled"] is False
    assert payload["data"]["enabled"] is False
    assert captured == {"name": "sequentialthinking", "enabled": False, "updated_by": "admin"}


def test_update_mcp_server_status_not_found(monkeypatch, client):
    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        raise ValueError(f"Server '{name}' does not exist")

    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    resp = client.put("/api/system/mcp-servers/missing/status", json={"enabled": True})
    assert resp.status_code == 404, resp.text


def test_get_mcp_servers_normal_user_is_stripped(monkeypatch, client, user_client):
    async def fake_get_all_mcp_servers(db):
        return [
            DictModel(
                name="test-mcp",
                description="test mcp description",
                transport="stdio",
                url="http://localhost:8000",
                command="python",
                args=["-m", "mcp"],
                env={"API_KEY": "secret"},
                headers={"Auth": "Bearer secret"},
                enabled=1,
                to_dict={
                    "name": "test-mcp",
                    "description": "test mcp description",
                    "transport": "stdio",
                    "url": "http://localhost:8000",
                    "command": "python",
                    "args": ["-m", "mcp"],
                    "env": {"API_KEY": "secret"},
                    "headers": {"Auth": "Bearer secret"},
                    "enabled": True,
                },
            )
        ]

    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_servers", fake_get_all_mcp_servers)

    # 1. 管理员请求，应该返回全部字段
    resp_admin = client.get("/api/system/mcp-servers")
    assert resp_admin.status_code == 200
    data_admin = resp_admin.json()["data"][0]
    assert data_admin["url"] == "http://localhost:8000"
    assert data_admin["command"] == "python"
    assert data_admin["env"] == {"API_KEY": "secret"}

    # 2. 普通用户请求，敏感字段及一切非安全白名单字段应该被彻底脱敏
    resp_user = user_client.get("/api/system/mcp-servers")
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


def test_get_mcp_server_normal_user_gets_public_detail(monkeypatch, user_client):
    async def fake_get_mcp_server(db, name):
        del db
        assert name == "personal-gateway"
        return DictModel(
            name="personal-gateway",
            description="personal gateway",
            transport="streamable_http",
            url="http://gateway.local/mcp",
            headers={"Authorization": "Bearer secret"},
            enabled=1,
            tags=["finance"],
            icon="🔐",
            auth_config_json=_auth_config("user"),
            to_dict={
                "name": "personal-gateway",
                "url": "http://gateway.local/mcp",
                "headers": {"Authorization": "Bearer secret"},
            },
        )

    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server", fake_get_mcp_server)

    resp = user_client.get("/api/system/mcp-servers/personal-gateway")

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["name"] == "personal-gateway"
    assert data["auth_config"] == {
        "version": 1,
        "provider": "bound_secret",
        "binding_scope": "user",
        "manifest_scope": "server",
        "secret_fields": ["access_token"],
    }
    assert "url" not in data
    assert "headers" not in data


def test_get_mcp_server_normal_user_cannot_read_disabled_server(monkeypatch, user_client):
    _patch_get_mcp_server(monkeypatch, DictModel(name="disabled-gateway", enabled=0))

    resp = user_client.get("/api/system/mcp-servers/disabled-gateway")

    assert resp.status_code == 404, resp.text


def test_create_mcp_server_forwards_auth_config(monkeypatch, client):
    captured = {}

    async def fake_create_mcp_server(db, **kwargs):
        del db
        captured.update(kwargs)
        return DictModel(to_dict={"name": "gateway", "auth_config": {"provider": "custom_http_token"}})

    monkeypatch.setattr("server.routers.mcp_router.create_mcp_server", fake_create_mcp_server)

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
                "inject": {"target": "headers", "entries": [TOKEN_AUTH_ENTRY]},
                "token_request": {"url": "http://gateway.local/auth/token", "method": "POST"},
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["auth_config"] == _normalized_auth_config(
        "custom_http_token",
        token_request={"url": "http://gateway.local/auth/token", "method": "POST"},
    )


def test_update_mcp_server_forwards_auth_config(monkeypatch, client):
    captured = {}

    async def fake_update_mcp_server(db, name, **kwargs):
        del db
        captured["name"] = name
        captured.update(kwargs)
        return DictModel(to_dict={"name": "gateway", "auth_config": {"provider": "bound_secret"}})

    monkeypatch.setattr("server.routers.mcp_router.update_mcp_server", fake_update_mcp_server)

    resp = client.put(
        "/api/system/mcp-servers/gateway",
        json={
            "description": "updated",
            "auth_config": {
                "version": 1,
                "provider": "bound_secret",
                "binding_scope": "department",
                "inject": {"target": "headers", "entries": [SECRET_AUTH_ENTRY]},
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["name"] == "gateway"
    assert captured["auth_config"] == _normalized_auth_config("bound_secret")


def test_create_mcp_server_rejects_invalid_auth_config(monkeypatch, client):
    async def fake_create_mcp_server(db, **kwargs):
        raise AssertionError("create_mcp_server should not be called when auth_config is invalid")

    monkeypatch.setattr("server.routers.mcp_router.create_mcp_server", fake_create_mcp_server)

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
                "inject": {"target": "headers", "entries": [TOKEN_AUTH_ENTRY]},
            },
        },
    )
    assert resp.status_code == 400, resp.text
    assert "auth_config 配置无效" in resp.json()["detail"]


def test_list_mcp_connections(monkeypatch, client):
    async def fake_list_mcp_connections(db, **kwargs):
        del db, kwargs
        return [
            DictModel(to_dict={"id": 1, "scope_type": "department", "status": "active"}),
            DictModel(to_dict={"id": 2, "scope_type": "department", "status": "active"}),
        ]

    _patch_get_mcp_server(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.list_mcp_connections", fake_list_mcp_connections)

    resp = client.get("/api/system/mcp-servers/gateway/connections")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == [
        {"id": 1, "scope_type": "department", "status": "active"},
        {"id": 2, "scope_type": "department", "status": "active"},
    ]


def test_list_mcp_connections_normal_user_only_lists_own_user_scope(monkeypatch, user_client):
    captured = {}

    async def fake_list_mcp_connections(db, **kwargs):
        del db
        captured.update(kwargs)
        return [DictModel(to_dict={"id": 9, "scope_type": "user", "scope_id": "user", "status": "active"})]

    _patch_get_mcp_server(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.list_mcp_connections", fake_list_mcp_connections)

    resp = user_client.get("/api/system/mcp-servers/gateway/connections")

    assert resp.status_code == 200, resp.text
    assert captured == {"server_name": "gateway", "scope_type": "user", "scope_id": "user"}
    assert resp.json()["data"] == [
        {"id": 9, "scope_type": "user", "scope_id": "user", "status": "active"}
    ]


def test_list_mcp_connections_admin_mine_filters_to_current_user(monkeypatch, client):
    captured = {}

    async def fake_list_mcp_connections(db, **kwargs):
        del db
        captured.update(kwargs)
        return []

    _patch_get_mcp_server(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.list_mcp_connections", fake_list_mcp_connections)

    resp = client.get("/api/system/mcp-servers/gateway/connections?mine=true")

    assert resp.status_code == 200, resp.text
    assert captured == {"server_name": "gateway", "scope_type": "user", "scope_id": "admin"}


def test_list_mcp_connections_paginated_returns_summary(monkeypatch, client):
    captured = {}
    count_filters = []

    async def fake_list_mcp_connections_page(db, **kwargs):
        del db
        captured.update(kwargs)
        return [DictModel(to_dict={"id": 12, "scope_type": "user", "scope_id": "1", "status": "active"})], 17

    async def fake_count_mcp_connections(db, **kwargs):
        del db
        count_filters.append(kwargs.get("status_filter", "all"))
        return {"all": 30, "active": 14, "attention": 3, "disabled": 5}.get(
            kwargs.get("status_filter", "all"), 0
        )

    _patch_get_mcp_server(monkeypatch, _server_stub(auth_config_json=_auth_config("user")))
    monkeypatch.setattr(
        "server.routers.mcp_router.list_mcp_connections_page",
        fake_list_mcp_connections_page,
    )
    monkeypatch.setattr("server.routers.mcp_router.count_mcp_connections", fake_count_mcp_connections)

    resp = client.get(
        "/api/system/mcp-servers/gateway/connections?paginated=true&status=attention&search=alice&page=2&page_size=5"
    )

    assert resp.status_code == 200, resp.text
    assert captured["server_name"] == "gateway"
    assert captured["status_filter"] == "attention"
    assert captured["effective_scope_type"] == "user"
    assert captured["credentials_required"] is True
    assert captured["search"] == "alice"
    assert captured["page"] == 2
    assert captured["page_size"] == 5
    payload = resp.json()["data"]
    assert payload["items"] == [{"id": 12, "scope_type": "user", "scope_id": "1", "status": "active"}]
    assert payload["total"] == 17
    assert payload["summary"] == {"total": 30, "active": 14, "attention": 3, "disabled": 5}
    assert count_filters == ["all", "active", "attention", "disabled"]


def test_create_mcp_connection(monkeypatch, client):
    captured = {}

    async def fake_create_mcp_connection(db, **kwargs):
        del db
        captured.update(kwargs)
        return DictModel(to_dict={"id": 7, "scope_type": "department", "status": "active"})

    _patch_get_mcp_server(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.create_mcp_connection", fake_create_mcp_connection)

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


def test_create_mcp_connection_normal_user_auto_binds_own_scope(monkeypatch, user_client):
    captured = {}

    async def fake_create_mcp_connection(db, **kwargs):
        del db
        captured.update(kwargs)
        return DictModel(to_dict={"id": 11, "scope_type": "user", "scope_id": "user", "status": "active"})

    _patch_get_mcp_server(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.create_mcp_connection", fake_create_mcp_connection)

    resp = user_client.post(
        "/api/system/mcp-servers/gateway/connections",
        json={
            "scope_type": "user",
            "display_name": "我的连接",
            "credential": {"secrets": {"access_token": "token-1"}},
        },
    )

    assert resp.status_code == 200, resp.text
    assert captured["server_name"] == "gateway"
    assert captured["scope_type"] == "user"
    assert captured["scope_id"] == "user"
    assert captured["created_by"] == "user"


def test_create_mcp_connection_normal_user_rejects_non_user_scope(monkeypatch, user_client):
    async def fake_create_mcp_connection(db, **kwargs):
        raise AssertionError("ordinary users must not create shared MCP connections")

    _patch_get_mcp_server(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.create_mcp_connection", fake_create_mcp_connection)

    resp = user_client.post(
        "/api/system/mcp-servers/gateway/connections",
        json={"scope_type": "department", "scope_id": "42"},
    )

    assert resp.status_code == 403, resp.text


def test_update_mcp_connection_normal_user_rejects_non_user_binding(monkeypatch, user_client):
    async def fake_get_mcp_connection(db, connection_id):
        del db, connection_id
        raise AssertionError("ordinary users must not manage connections on shared-bound MCPs")

    _patch_get_mcp_server(monkeypatch, _server_stub(auth_config_json=_auth_config("department")))
    monkeypatch.setattr("server.routers.mcp_router.get_mcp_connection", fake_get_mcp_connection)

    resp = user_client.put(
        "/api/system/mcp-servers/gateway/connections/7",
        json={"display_name": "我的连接"},
    )

    assert resp.status_code == 403, resp.text


def test_update_mcp_connection_status(monkeypatch, client):
    captured = {}

    async def fake_set_mcp_connection_status(db, connection_id, **kwargs):
        del db
        captured["connection_id"] = connection_id
        captured.update(kwargs)
        return DictModel(to_dict={"id": 7, "status": "reauth_required"})

    _patch_get_mcp_server(monkeypatch)
    _patch_get_mcp_connection(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.set_mcp_connection_status", fake_set_mcp_connection_status)

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


def test_update_mcp_connection(monkeypatch, client):
    captured = {}

    async def fake_update_mcp_connection(db, connection_id, **kwargs):
        del db
        captured["connection_id"] = connection_id
        captured.update(kwargs)
        return DictModel(to_dict={"id": 7, "display_name": "新连接名", "status": "active"})

    _patch_get_mcp_server(monkeypatch)
    _patch_get_mcp_connection(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.update_mcp_connection", fake_update_mcp_connection)

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


def test_delete_mcp_connection(monkeypatch, client):
    captured = {}

    async def fake_delete_mcp_connection(db, connection_id):
        del db
        captured["connection_id"] = connection_id
        return True

    _patch_get_mcp_server(monkeypatch)
    _patch_get_mcp_connection(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.delete_mcp_connection", fake_delete_mcp_connection)

    resp = client.delete("/api/system/mcp-servers/gateway/connections/7")
    assert resp.status_code == 200, resp.text
    assert captured == {"connection_id": 7}


def test_delete_mcp_connection_normal_user_cannot_delete_other_user_connection(monkeypatch, user_client):
    async def fake_delete_mcp_connection(db, connection_id):
        raise AssertionError("should not delete another user's MCP connection")

    _patch_get_mcp_server(monkeypatch)
    _patch_get_mcp_connection(monkeypatch, _connection_ref(scope_type="user", scope_id="other-user"))
    monkeypatch.setattr("server.routers.mcp_router.delete_mcp_connection", fake_delete_mcp_connection)

    resp = user_client.delete("/api/system/mcp-servers/gateway/connections/7")

    assert resp.status_code == 404, resp.text


def test_test_mcp_connection_route(monkeypatch, client):
    captured = {}

    async def fake_test_mcp_connection(db, connection_id, *, updated_by=None):
        del db
        captured["connection_id"] = connection_id
        captured["updated_by"] = updated_by
        return {"tool_count": 3}

    _patch_get_mcp_server(monkeypatch)
    _patch_get_mcp_connection(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.test_mcp_connection", fake_test_mcp_connection)

    resp = client.post("/api/system/mcp-servers/gateway/connections/7/test")
    assert resp.status_code == 200, resp.text
    assert resp.json()["tool_count"] == 3
    assert captured == {"connection_id": 7, "updated_by": "admin"}


def test_reauthorize_mcp_connection_route(monkeypatch, client):
    captured = {}

    async def fake_reauthorize_mcp_connection(db, connection_id, *, updated_by=None):
        del db
        captured["connection_id"] = connection_id
        captured["updated_by"] = updated_by
        return DictModel(to_dict={"id": 7, "status": "active"})

    _patch_get_mcp_server(monkeypatch)
    _patch_get_mcp_connection(monkeypatch)
    monkeypatch.setattr("server.routers.mcp_router.reauthorize_mcp_connection", fake_reauthorize_mcp_connection)

    resp = client.post("/api/system/mcp-servers/gateway/connections/7/reauth")
    assert resp.status_code == 200, resp.text
    assert captured == {"connection_id": 7, "updated_by": "admin"}


def test_update_mcp_connection_status_rejects_connection_from_other_server(monkeypatch, client):
    async def fake_set_mcp_connection_status(db, connection_id, **kwargs):
        raise AssertionError("should not update a connection that belongs to another server")

    _patch_get_mcp_server(monkeypatch)
    _patch_get_mcp_connection(monkeypatch, _connection_ref(server_name="other-gateway"))
    monkeypatch.setattr("server.routers.mcp_router.set_mcp_connection_status", fake_set_mcp_connection_status)

    resp = client.put(
        "/api/system/mcp-servers/gateway/connections/7/status",
        json={"status": "reauth_required"},
    )
    assert resp.status_code == 404, resp.text


def test_test_mcp_server_requires_connection_level_test_for_bound_auth(monkeypatch, client):
    server = DictModel(
        auth_config_json={
            "version": 1,
            "provider": "custom_http_token",
            "binding_scope": "department",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
            },
            "token_request": {"url": "http://gateway.local/auth/token", "method": "POST"},
        }
    )

    async def fake_get_all_mcp_tools(server_name, *, auth_context=None, db=None, http_client=None, force_refresh=False):
        del server_name, auth_context, db, http_client, force_refresh
        raise ValueError(BOUND_CONNECTION_MISSING)

    _patch_get_server_or_404(monkeypatch, server)
    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_tools", fake_get_all_mcp_tools)

    resp = client.post("/api/system/mcp-servers/gateway/test", json={})
    assert resp.status_code == 400, resp.text


def test_get_mcp_server_tools_uses_current_admin_auth_context(monkeypatch, client):
    captured = {}

    class DummyArgsSchema:
        @staticmethod
        def schema():
            return {"properties": {"city": {"type": "string"}}, "required": ["city"]}

    class DummyTool:
        name = "tool_a"
        description = "tool a"
        metadata = {"id": "mcp__gateway__toolA"}
        args_schema = DummyArgsSchema()

    async def fake_get_all_mcp_tools(server_name, *, auth_context=None, db=None, http_client=None, force_refresh=False):
        del db, http_client, force_refresh
        assert server_name == "gateway"
        captured["server_name"] = server_name
        captured["user_id"] = auth_context.user_id
        captured["department_id"] = auth_context.department_id
        return [DummyTool()]

    _patch_get_server_or_404(monkeypatch, DictModel(disabled_tools=["tool_b"]))
    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_tools", fake_get_all_mcp_tools)

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


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/system/mcp-servers/gateway/tools"),
        ("post", "/api/system/mcp-servers/gateway/tools/refresh"),
    ],
)
def test_mcp_server_tools_returns_403_when_bound_connection_missing(monkeypatch, client, method, path):
    async def fake_get_all_mcp_tools(server_name, *, auth_context=None, db=None, http_client=None, force_refresh=False):
        del server_name, auth_context, db, http_client, force_refresh
        raise ValueError(BOUND_CONNECTION_MISSING)

    _patch_get_server_or_404(monkeypatch, DictModel(disabled_tools=[]))
    monkeypatch.setattr("server.routers.mcp_router.get_all_mcp_tools", fake_get_all_mcp_tools)

    resp = getattr(client, method)(path)

    assert resp.status_code == 403, resp.text


def test_delete_mcp_server_defaults_to_retire(monkeypatch, client):
    captured = {}
    retired_server = DictModel(created_by="tester", to_dict={"name": "gateway", "enabled": False})

    async def fake_set_server_enabled(db, name, enabled, updated_by=None):
        del db
        captured["name"] = name
        captured["enabled"] = enabled
        captured["updated_by"] = updated_by
        return False, retired_server

    _patch_get_mcp_server(monkeypatch, retired_server)
    monkeypatch.setattr("server.routers.mcp_router.set_server_enabled", fake_set_server_enabled)

    resp = client.delete("/api/system/mcp-servers/gateway")

    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "服务器 'gateway' 已退役"
    assert captured == {
        "name": "gateway",
        "enabled": False,
        "updated_by": "admin",
    }


def test_delete_mcp_server_hard_delete_returns_conflict(monkeypatch, client):
    async def fake_get_dependency_summary(db, name):
        del db, name
        return {
            "has_references": True,
            "connections": [{"scope_type": "department", "scope_id": "42", "status": "active"}],
            "skills": [{"slug": "finance-skill", "name": "Finance Skill"}],
            "agent_configs": [],
        }

    _patch_get_mcp_server(monkeypatch, DictModel(created_by="tester", enabled=0))
    monkeypatch.setattr("server.routers.mcp_router.get_mcp_server_dependency_summary", fake_get_dependency_summary)

    resp = client.delete("/api/system/mcp-servers/gateway?hard=true")

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["connections"] == [
        {"scope_type": "department", "scope_id": "42", "status": "active"}
    ]
