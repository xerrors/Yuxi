from __future__ import annotations


def make_mock_response(status_code, content):
    import httpx
    resp = httpx.Response(status_code, content=content)
    async def fake_aiter_raw():
        yield content
    resp.aiter_raw = fake_aiter_raw
    return resp

import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services.mcp_auth.orchestrator import AuthContext
from yuxi.services.mcp_auth.proxy_service import _proxy_mcp_request_stream
from starlette.requests import Request


def make_mock_response(status_code, content):
    import httpx
    resp = httpx.Response(status_code, content=content)
    async def fake_aiter_raw():
        yield content
    resp.aiter_raw = fake_aiter_raw
    return resp

import json
async def get_response_json(response):
    if hasattr(response, "body_iterator"):
        body = b"".join([chunk async for chunk in response.body_iterator])
    else:
        body = response.body
    return json.loads(body)

from fastapi.responses import StreamingResponse
from yuxi.storage.postgres.models_business import MCPConnection, MCPServer


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class DummyTokenCache:
    def __init__(self, token_payload: dict | None = None):
        self.token_payload = token_payload
        self.deleted_connection_ids: list[int] = []
        self.set_calls: list[tuple[int, dict]] = []

    async def get_access_token(self, connection_id: int) -> dict | None:
        del connection_id
        return self.token_payload

    async def delete_access_token(self, connection_id: int) -> None:
        self.deleted_connection_ids.append(connection_id)
        self.token_payload = None

    async def set_access_token(self, connection_id: int, token_payload: dict) -> None:
        self.set_calls.append((connection_id, token_payload))
        self.token_payload = token_payload


async def test_proxy_mcp_request_retries_once_after_401_with_refreshed_token():
    observed_authorizations: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "http://gateway.local/auth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-token",
                    "refresh_token": "refresh-next",
                    "expires_in": 3600,
                },
            )

        if str(request.url) == "http://upstream.local/mcp":
            observed_authorizations.append(request.headers.get("Authorization"))
            if request.headers.get("Authorization") == "Bearer stale-token":
                return make_mock_response(401, b'{"error": "expired"}')
            if request.headers.get("Authorization") == "Bearer fresh-token":
                resp = make_mock_response(200, b'{"result": "ok"}')
        resp.is_stream_consumed = False
        return resp

        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server = MCPServer(
        name="proxy-retry",
        transport="streamable_http",
        url="http://upstream.local/mcp",
        auth_config_json={
            "version": 1,
            "provider": "custom_http_token",
            "binding_scope": "department",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "refresh_policy": {"pre_refresh_seconds": 60, "retry_once_on_401": True},
            "token_request": {
                "url": "http://gateway.local/auth/token",
                "method": "POST",
                "body_type": "json",
                "body_template": {
                    "client_id": "${secret.client_id}",
                    "client_secret": "${secret.client_secret}",
                },
                "response_map": {
                    "access_token": "access_token",
                    "refresh_token": "refresh_token",
                    "expires_in": "expires_in",
                },
            },
        },
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(
        id=41,
        server_name="proxy-retry",
        scope_type="department",
        scope_id="dep-1",
        status="active",
        credential_blob=json.dumps({"secrets": {"client_id": "cid", "client_secret": "secret"}}),
        meta_json={},
        created_by="tester",
        updated_by="tester",
    )
    token_cache = DummyTokenCache(
        {
            "access_token": "stale-token",
            "refresh_token": "refresh-old",
            "expires_at": (datetime.now(tz=UTC) + timedelta(minutes=30)).isoformat(),
        }
    )

    req = Request({"type": "http", "method": "POST", "headers": [(b"content-type", b"application/json")], "query_string": b""})
    
    class DummyDB:
        async def commit(self): pass
        
    response = await _proxy_mcp_request_stream(
        server,
        connection=connection,
        auth_context=AuthContext(user_id="user-1", department_id="dep-1"),
        request=req,
        body=b'{"jsonrpc":"2.0","id":1}',
        db=DummyDB(),
        _http_client=http_client,
        _token_cache=token_cache,
    )

    await http_client.aclose()

    assert response.status_code == 200
    assert await get_response_json(response) == {"result": "ok"}
    assert observed_authorizations == ["Bearer stale-token", "Bearer fresh-token"]
    assert token_cache.deleted_connection_ids == [41]
    assert token_cache.set_calls and token_cache.set_calls[0][0] == 41
    assert connection.status == "active"


async def test_proxy_mcp_request_marks_reauth_required_after_final_401():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if str(request.url) == "http://gateway.local/auth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": f"fresh-token-{attempts}",
                    "refresh_token": "refresh-next",
                    "expires_in": 3600,
                },
            )
        if str(request.url) == "http://upstream.local/mcp":
            attempts += 1
            return make_mock_response(401, b'{"error": "expired"}')
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server = MCPServer(
        name="proxy-fail-401",
        transport="streamable_http",
        url="http://upstream.local/mcp",
        auth_config_json={
            "version": 1,
            "provider": "custom_http_token",
            "binding_scope": "department",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "refresh_policy": {"pre_refresh_seconds": 60, "retry_once_on_401": True},
            "token_request": {
                "url": "http://gateway.local/auth/token",
                "method": "POST",
                "body_type": "json",
                "body_template": {
                    "client_id": "${secret.client_id}",
                    "client_secret": "${secret.client_secret}",
                },
                "response_map": {
                    "access_token": "access_token",
                    "refresh_token": "refresh_token",
                    "expires_in": "expires_in",
                },
            },
        },
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(
        id=42,
        server_name="proxy-fail-401",
        scope_type="department",
        scope_id="dep-1",
        status="active",
        credential_blob=json.dumps({"secrets": {"client_id": "cid", "client_secret": "secret"}}),
        meta_json={},
        created_by="tester",
        updated_by="tester",
    )
    token_cache = DummyTokenCache(
        {
            "access_token": "stale-token",
            "refresh_token": "refresh-old",
            "expires_at": (datetime.now(tz=UTC) + timedelta(minutes=30)).isoformat(),
        }
    )

    req = Request({"type": "http", "method": "POST", "headers": [(b"content-type", b"application/json")], "query_string": b""})
    
    class DummyDB:
        async def commit(self): pass
        
    response = await _proxy_mcp_request_stream(
        server,
        connection=connection,
        auth_context=AuthContext(user_id="user-1", department_id="dep-1"),
        request=req,
        body=b'{"jsonrpc":"2.0","id":1}',
        db=DummyDB(),
        _http_client=http_client,
        _token_cache=token_cache,
    )

    await http_client.aclose()

    assert response.status_code == 424
    assert (await get_response_json(response))["error"] == "reauth_required"
    assert connection.status == "reauth_required"
    assert connection.meta_json["last_error"]["code"] == "unauthorized"


async def test_proxy_mcp_request_records_scope_error_on_403():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "http://upstream.local/mcp":
            return make_mock_response(403, b'{"error": "forbidden"}')
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server = MCPServer(
        name="proxy-403",
        transport="streamable_http",
        url="http://upstream.local/mcp",
        headers={"Authorization": "Bearer static-token"},
        auth_config_json={
            "version": 1,
            "provider": "bound_secret",
            "binding_scope": "department",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
            },
        },
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(
        id=43,
        server_name="proxy-403",
        scope_type="department",
        scope_id="dep-1",
        status="active",
        credential_blob=json.dumps({"secrets": {"access_token": "static-token"}}),
        meta_json={},
        created_by="tester",
        updated_by="tester",
    )

    req = Request({"type": "http", "method": "POST", "headers": [(b"content-type", b"application/json")], "query_string": b""})
    class DummyDB:
        async def commit(self): pass
    response = await _proxy_mcp_request_stream(
        server,
        connection=connection,
        auth_context=AuthContext(user_id="user-1", department_id="dep-1"),
        request=req,
        body=b'{"jsonrpc":"2.0","id":1}',
        db=DummyDB(),
        _http_client=http_client,
        _token_cache=None,
    )

    await http_client.aclose()

    assert response.status_code == 403
    assert (await get_response_json(response))["error"] == "insufficient_scope"
    assert connection.status == "active"
    assert connection.meta_json["last_error"]["code"] == "insufficient_scope"
