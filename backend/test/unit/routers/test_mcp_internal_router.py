from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers.mcp_internal_router import mcp_internal
from server.utils.auth_middleware import get_db
from yuxi.services.mcp_auth.orchestrator import AuthContext


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(mcp_internal, prefix="/api")

    async def fake_db():
        return None

    app.dependency_overrides[get_db] = fake_db
    return app


def test_internal_proxy_route_forwards_request(monkeypatch):
    class DummyServer:
        name = "finance-proxy"
        transport = "streamable_http"
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

    class DummyConnection:
        status = "active"
        meta_json = {}

    async def fake_get_mcp_server(db, name):
        del db
        assert name == "finance-proxy"
        return DummyServer()

    async def fake_load_connection(db, *, server, auth_context):
        del db
        assert server.name == "finance-proxy"
        assert auth_context.department_id == "dep-1"
        return DummyConnection()

    async def fake_proxy_mcp_request(server, **kwargs):
        del kwargs
        assert server.name == "finance-proxy"
        return httpx.Response(
            200,
            json={"ok": True},
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(
        "server.routers.mcp_internal_router.decode_proxy_access_token",
        lambda token, server_name: AuthContext(user_id="user-1", department_id="dep-1"),
    )
    monkeypatch.setattr("server.routers.mcp_internal_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_internal_router._load_active_connection", fake_load_connection)
    monkeypatch.setattr("server.routers.mcp_internal_router.proxy_mcp_request", fake_proxy_mcp_request)

    client = TestClient(_build_app())
    resp = client.post(
        "/api/internal/mcp-proxy/finance-proxy",
        headers={"X-Yuxi-MCP-Proxy-Token": "test-token", "content-type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


def test_internal_proxy_route_requires_internal_token():
    client = TestClient(_build_app())
    resp = client.post("/api/internal/mcp-proxy/finance-proxy", json={"jsonrpc": "2.0", "id": 1})
    assert resp.status_code == 401, resp.text


def test_internal_proxy_route_rejects_user_scoped_request_without_active_connection(monkeypatch):
    class DummyServer:
        name = "personal-proxy"
        transport = "streamable_http"
        auth_config_json = {
            "version": 1,
            "provider": "custom_http_token",
            "binding_scope": "user",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "token_request": {"url": "http://gateway.local/auth/token", "method": "POST"},
        }

    async def fake_get_mcp_server(db, name):
        del db
        assert name == "personal-proxy"
        return DummyServer()

    async def fake_load_connection(db, *, server, auth_context):
        del db
        assert server.name == "personal-proxy"
        assert auth_context.user_id == "user-2"
        return None

    async def fake_proxy_mcp_request(server, **kwargs):
        del server, kwargs
        raise AssertionError("proxy request should not run without an active user connection")

    monkeypatch.setattr(
        "server.routers.mcp_internal_router.decode_proxy_access_token",
        lambda token, server_name: AuthContext(user_id="user-2", department_id="dep-1"),
    )
    monkeypatch.setattr("server.routers.mcp_internal_router.get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr("server.routers.mcp_internal_router._load_active_connection", fake_load_connection)
    monkeypatch.setattr("server.routers.mcp_internal_router.proxy_mcp_request", fake_proxy_mcp_request)

    client = TestClient(_build_app())
    resp = client.post(
        "/api/internal/mcp-proxy/personal-proxy",
        headers={"X-Yuxi-MCP-Proxy-Token": "test-token", "content-type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1},
    )

    assert resp.status_code == 403, resp.text
