from __future__ import annotations

import httpx
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from server.routers.mcp_internal_router import mcp_internal
from server.utils.auth_middleware import get_db


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(mcp_internal, prefix="/api")

    async def fake_db():
        return None

    app.dependency_overrides[get_db] = fake_db
    return app


def test_internal_proxy_route_forwards_request(monkeypatch):
    async def fake_handle_mcp_proxy_request(server_name, request, path, internal_token, db):
        assert server_name == "finance-proxy"
        assert path == "some/path"
        assert internal_token == "test-token"
        return Response(content='{"ok": true}', media_type="application/json")

    monkeypatch.setattr(
        "server.routers.mcp_internal_router.handle_mcp_proxy_request", 
        fake_handle_mcp_proxy_request
    )

    client = TestClient(_build_app())
    resp = client.post(
        "/api/internal/mcp-proxy/finance-proxy/some/path",
        headers={"X-Yuxi-MCP-Proxy-Token": "test-token", "content-type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


def test_internal_proxy_route_requires_internal_token():
    client = TestClient(_build_app())
    resp = client.post("/api/internal/mcp-proxy/finance-proxy", json={"jsonrpc": "2.0", "id": 1})
    assert resp.status_code == 401, resp.text  # Missing header raises 401 Unauthorized
