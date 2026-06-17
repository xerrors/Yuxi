from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import Response
from starlette.requests import Request
from yuxi.services.mcp import server_service
from yuxi.services.mcp_auth import proxy_service
from yuxi.services.mcp_auth.orchestrator import AuthContext
from yuxi.services.mcp_auth.proxy_service import (
    _proxy_mcp_request_stream,
    create_proxy_access_token,
    handle_mcp_proxy_request,
)
from yuxi.storage.postgres.models_business import MCPConnection, MCPServer


os.environ.setdefault("OPENAI_API_KEY", "test-key")


def make_mock_response(status_code, content):
    resp = httpx.Response(status_code, content=content)

    async def fake_aiter_raw():
        yield content

    resp.aiter_raw = fake_aiter_raw
    return resp


async def get_response_json(response):
    if hasattr(response, "body_iterator"):
        body = b"".join([chunk async for chunk in response.body_iterator])
    else:
        body = response.body
    return json.loads(body)


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


TOKEN_INJECT = {
    "target": "headers",
    "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
}
SECRET_INJECT = {
    "target": "headers",
    "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
}


def token_auth_config(*, binding_scope="department", body_template=None, response_map=None):
    return {
        "version": 1,
        "provider": "custom_http_token",
        "binding_scope": binding_scope,
        "manifest_scope": "server",
        "inject": TOKEN_INJECT,
        "refresh_policy": {"pre_refresh_seconds": 60, "retry_once_on_401": True},
        "token_request": {
            "url": "http://gateway.local/auth/token",
            "method": "POST",
            "body_type": "json",
            "body_template": body_template
            or {
                "client_id": "${secret.client_id}",
                "client_secret": "${secret.client_secret}",
            },
            "response_map": response_map
            or {
                "access_token": "access_token",
                "refresh_token": "refresh_token",
                "expires_in": "expires_in",
            },
        },
    }


def make_mcp_server(name: str, *, auth_config: dict, **overrides) -> MCPServer:
    payload = {
        "name": name,
        "transport": "streamable_http",
        "url": "http://upstream.local/mcp",
        "auth_config_json": auth_config,
        "created_by": "tester",
        "updated_by": "tester",
    }
    payload.update(overrides)
    return MCPServer(**payload)


def make_mcp_connection(connection_id: int, server_name: str, *, credential: dict, **overrides):
    payload = {
        "id": connection_id,
        "server_name": server_name,
        "scope_type": "department",
        "scope_id": "dep-1",
        "status": "active",
        "credential_blob": json.dumps(credential),
        "meta_json": {},
        "created_by": "tester",
        "updated_by": "tester",
    }
    payload.update(overrides)
    return MCPConnection(**payload)


def future_token_payload(access_token: str, *, minutes: int = 30, **extra) -> dict:
    return {
        "access_token": access_token,
        "expires_at": (datetime.now(tz=UTC) + timedelta(minutes=minutes)).isoformat(),
        **extra,
    }


def make_json_request(receive=None):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        },
        receive,
    )


class EmptyResult:
    def scalar_one_or_none(self):
        return None


class DummyDB:
    async def commit(self):
        pass

    async def execute(self, stmt):
        del stmt
        return EmptyResult()


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
    from yuxi.services.mcp.client_pool import clear_resolved_headers_cache, _resolved_headers_cache

    clear_resolved_headers_cache()
    _resolved_headers_cache[("proxy-retry", "user-1", "dep-1")] = {"Authorization": "Bearer stale-token"}

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
    server = make_mcp_server(
        "proxy-retry",
        auth_config=token_auth_config(),
    )
    connection = make_mcp_connection(
        41,
        "proxy-retry",
        credential={"secrets": {"client_id": "cid", "client_secret": "secret"}},
    )
    token_cache = DummyTokenCache(
        future_token_payload("stale-token", refresh_token="refresh-old")
    )

    response = await _proxy_mcp_request_stream(
        server,
        connection=connection,
        auth_context=AuthContext(user_id="user-1", department_id="dep-1"),
        request=make_json_request(),
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
    assert ("proxy-retry", "user-1", "dep-1") not in _resolved_headers_cache
    assert connection.status == "active"
    clear_resolved_headers_cache()


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
    server = make_mcp_server(
        "proxy-fail-401",
        auth_config=token_auth_config(),
    )
    connection = make_mcp_connection(
        42,
        "proxy-fail-401",
        credential={"secrets": {"client_id": "cid", "client_secret": "secret"}},
    )
    token_cache = DummyTokenCache(
        future_token_payload("stale-token", refresh_token="refresh-old")
    )

    response = await _proxy_mcp_request_stream(
        server,
        connection=connection,
        auth_context=AuthContext(user_id="user-1", department_id="dep-1"),
        request=make_json_request(),
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
    server = make_mcp_server(
        "proxy-403",
        headers={"Authorization": "Bearer static-token"},
        auth_config={
            "version": 1,
            "provider": "bound_secret",
            "binding_scope": "department",
            "manifest_scope": "server",
            "inject": SECRET_INJECT,
        },
    )
    connection = make_mcp_connection(
        43,
        "proxy-403",
        credential={"secrets": {"access_token": "static-token"}},
    )

    response = await _proxy_mcp_request_stream(
        server,
        connection=connection,
        auth_context=AuthContext(user_id="user-1", department_id="dep-1"),
        request=make_json_request(),
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


async def test_handle_mcp_proxy_request_allows_no_secret_dynamic_config_without_connection(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "unit-test-jwt-secret-with-at-least-32-bytes")
    monkeypatch.setenv("YUXI_INSTANCE_ID", "unit-test-instance")

    server = make_mcp_server(
        "proxy-no-secret",
        auth_config=token_auth_config(
            binding_scope="user",
            body_template={"work_id": "${context.work_id}"},
            response_map={"access_token": "access_token", "expires_in": "expires_in"},
        ),
        enabled=1,
    )

    async def fake_get_mcp_server(db, server_name):
        del db
        assert server_name == "proxy-no-secret"
        return server

    observed = {}

    async def fake_proxy_mcp_request_stream(**kwargs):
        assert kwargs["server"] is server
        observed["connection"] = kwargs["connection"]
        observed["auth_context"] = kwargs["auth_context"]
        observed["body"] = kwargs["body"]
        return Response(status_code=204)

    monkeypatch.setattr(server_service, "get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr(proxy_service, "_proxy_mcp_request_stream", fake_proxy_mcp_request_stream)

    token = create_proxy_access_token(
        "proxy-no-secret",
        AuthContext(user_id="user-1", department_id="dep-1", work_id="W001"),
    )
    async def receive():
        return {"type": "http.request", "body": b'{"jsonrpc":"2.0","id":1}', "more_body": False}

    response = await handle_mcp_proxy_request(
        "proxy-no-secret",
        request=make_json_request(receive),
        path="",
        internal_token=token,
        db=DummyDB(),
    )

    assert response.status_code == 204
    assert observed["connection"] is None
    assert observed["auth_context"].work_id == "W001"
    assert observed["body"] == b'{"jsonrpc":"2.0","id":1}'
