from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services.mcp_auth.crypto import encrypt_credential_blob
from yuxi.services.mcp_auth.orchestrator import AuthContext, _normalize_token_payload, resolve_runtime_mcp_config
from yuxi.storage.postgres.models_business import MCPConnection, MCPServer


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.fixture(autouse=True)
def mcp_credentials_master_key():
    os.environ["MCP_CREDENTIALS_MASTER_KEY"] = "local-test-master-key"


AUTH_HEADER_FROM_SECRET = {
    "target": "headers",
    "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
}
AUTH_HEADER_FROM_TOKEN = {
    "target": "headers",
    "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
}


def make_mcp_server(name: str, *, auth_config: dict, **overrides) -> MCPServer:
    transport = overrides.pop("transport", "streamable_http")
    payload = {
        "name": name,
        "transport": transport,
        "auth_config_json": auth_config,
        "created_by": "tester",
        "updated_by": "tester",
    }
    if transport != "stdio":
        payload["url"] = overrides.pop("url", f"http://{name}.local/mcp")
    elif "url" in overrides:
        payload["url"] = overrides.pop("url")
    payload.update(overrides)
    return MCPServer(**payload)


def make_mcp_connection(
    server_name: str,
    *,
    credential_blob: str | None = None,
    credential: dict | None = None,
    connection_id: int | None = None,
    scope_type: str = "department",
    scope_id: str = "finance",
) -> MCPConnection:
    payload = {
        "server_name": server_name,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "credential_blob": credential_blob if credential_blob is not None else json.dumps(credential or {}),
        "created_by": "tester",
        "updated_by": "tester",
    }
    if connection_id is not None:
        payload["id"] = connection_id
    return MCPConnection(**payload)


def bound_secret_auth_config(*, binding_scope: str, manifest_scope: str = "server") -> dict:
    return {
        "version": 1,
        "provider": "bound_secret",
        "binding_scope": binding_scope,
        "manifest_scope": manifest_scope,
        "inject": AUTH_HEADER_FROM_SECRET,
    }


def token_auth_config(
    *,
    provider: str = "custom_http_token",
    binding_scope: str = "department",
    manifest_scope: str = "server",
    pre_refresh_seconds: int = 0,
    token_request: dict | None = None,
) -> dict:
    return {
        "version": 1,
        "provider": provider,
        "binding_scope": binding_scope,
        "manifest_scope": manifest_scope,
        "inject": AUTH_HEADER_FROM_TOKEN,
        "refresh_policy": {
            "pre_refresh_seconds": pre_refresh_seconds,
            "retry_once_on_401": True,
        },
        "token_request": token_request or {
            "url": "http://gateway.local/auth/token",
            "method": "POST",
            "body_type": "json",
            "response_map": {"access_token": "access_token", "expires_in": "expires_in"},
        },
    }


def future_token_payload(access_token: str, *, minutes: int = 30, **extra) -> dict:
    return {
        "access_token": access_token,
        "expires_at": (datetime.now(tz=UTC) + timedelta(minutes=minutes)).isoformat(),
        **extra,
    }


class DummyTokenCache:
    def __init__(self, token_payload: dict | None = None):
        self.token_payload = token_payload
        self.token_payloads = None
        self.set_calls: list[tuple[int, dict]] = []
        self.deleted_connection_ids: list[int] = []
        self.acquire_calls: list[int] = []
        self.release_calls: list[int] = []
        self.acquire_result = True

    async def get_access_token(self, connection_id: int) -> dict | None:
        del connection_id
        if self.token_payloads is not None:
            if self.token_payloads:
                self.token_payload = self.token_payloads.pop(0)
            else:
                self.token_payload = None
        return self.token_payload

    async def set_access_token(self, connection_id: int, token_payload: dict) -> None:
        self.set_calls.append((connection_id, token_payload))
        self.token_payload = token_payload

    async def delete_access_token(self, connection_id: int) -> None:
        self.deleted_connection_ids.append(connection_id)
        self.token_payload = None

    async def acquire_refresh_lock(self, connection_id: int, *, ttl_seconds: int = 30) -> bool:
        del ttl_seconds
        self.acquire_calls.append(connection_id)
        return self.acquire_result

    async def release_refresh_lock(self, connection_id: int) -> None:
        self.release_calls.append(connection_id)


@pytest.mark.parametrize(
    ("server_name", "server_headers", "binding_scope", "scope_id", "credential_text", "expected_headers"),
    [
        (
            "finance-gateway",
            {"X-App": "yuxi"},
            "department",
            "42",
            json.dumps({"secrets": {"access_token": "dept-token"}}),
            {"X-App": "yuxi", "Authorization": "Bearer dept-token"},
        ),
        (
            "raw-token-gateway",
            {},
            "system",
            "global",
            "raw-token-value",
            {"Authorization": "Bearer raw-token-value"},
        ),
    ],
)
async def test_resolve_runtime_mcp_config_injects_bound_secret_header(
    server_name, server_headers, binding_scope, scope_id, credential_text, expected_headers
):
    server = make_mcp_server(
        name=server_name,
        headers=server_headers,
        auth_config=bound_secret_auth_config(binding_scope=binding_scope),
    )
    connection = make_mcp_connection(
        server_name=server_name,
        scope_type=binding_scope,
        scope_id=scope_id,
        credential_blob=encrypt_credential_blob(credential_text),
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="u-1", department_id=scope_id),
        connection=connection,
    )

    assert resolved["transport"] == "streamable_http"
    assert resolved["headers"] == expected_headers
    assert "auth_config" not in resolved


async def test_resolve_runtime_mcp_config_fetches_custom_http_token_with_user_context():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "data": {
                    "access_token": "fresh-token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server = make_mcp_server(
        name="corp-gateway",
        headers={"X-App": "yuxi"},
        auth_config=token_auth_config(
            pre_refresh_seconds=600,
            token_request={
                "url": "http://gateway.local/auth/token",
                "method": "POST",
                "body_type": "json",
                "headers": {
                    "Content-Type": "application/json",
                    "X-Client-Id": "${secret.client_id}",
                },
                "body_template": {
                    "client_id": "${secret.client_id}",
                    "client_secret": "${secret.client_secret}",
                    "user_id": "${context.user_id}",
                    "department_id": "${context.department_id}",
                },
                "response_map": {
                    "access_token": "data.access_token",
                    "refresh_token": "data.refresh_token",
                    "expires_in": "data.expires_in",
                },
            },
        ),
    )
    connection = make_mcp_connection(
        server_name="corp-gateway",
        credential={"secrets": {"client_id": "cid-1", "client_secret": "secret-1"}},
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="user-9", department_id="finance"),
        connection=connection,
        http_client=http_client,
        token_cache=DummyTokenCache(),
    )

    await http_client.aclose()

    assert captured["url"] == "http://gateway.local/auth/token"
    assert captured["body"] == {
        "client_id": "cid-1",
        "client_secret": "secret-1",
        "user_id": "user-9",
        "department_id": "finance",
    }
    assert resolved["headers"] == {
        "X-App": "yuxi",
        "Authorization": "Bearer fresh-token",
    }


async def test_resolve_runtime_mcp_config_fetches_client_credentials_token():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "access_token": "client-token",
                "expires_in": 1800,
                "token_type": "Bearer",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server = make_mcp_server(
        name="client-credentials-mcp",
        auth_config=token_auth_config(
            provider="client_credentials",
            binding_scope="system",
            token_request={
                "url": "http://gateway.local/oauth/token",
                "method": "POST",
                "body_type": "json",
                "body_template": {
                    "grant_type": "client_credentials",
                    "client_id": "${secret.client_id}",
                    "client_secret": "${secret.client_secret}",
                },
                "response_map": {
                    "access_token": "access_token",
                    "expires_in": "expires_in",
                    "token_type": "token_type",
                },
            },
        ),
    )
    connection = make_mcp_connection(
        server_name="client-credentials-mcp",
        connection_id=11,
        scope_type="system",
        scope_id="global",
        credential={"secrets": {"client_id": "cid-cc", "client_secret": "secret-cc"}},
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="u-1", department_id="d-1"),
        connection=connection,
        http_client=http_client,
        token_cache=DummyTokenCache(),
    )

    await http_client.aclose()

    assert captured["url"] == "http://gateway.local/oauth/token"
    assert captured["body"] == {
        "grant_type": "client_credentials",
        "client_id": "cid-cc",
        "client_secret": "secret-cc",
    }
    assert resolved["headers"] == {"Authorization": "Bearer client-token"}


async def test_resolve_runtime_mcp_config_injects_stdio_env_from_secret_binding():
    server = make_mcp_server(
        name="stdio-auth-mcp",
        transport="stdio",
        command="demo-server",
        env={"LOG_LEVEL": "info"},
        auth_config={
            "version": 1,
            "provider": "stdio_env",
            "binding_scope": "user",
            "manifest_scope": "binding",
            "inject": {
                "target": "env",
                "entries": [
                    {"name": "API_TOKEN", "value_template": "${secret.access_token}"},
                    {"name": "YUXI_USER_ID", "value_template": "${context.user_id}"},
                ],
            },
        },
    )
    connection = make_mcp_connection(
        server_name="stdio-auth-mcp",
        scope_type="user",
        scope_id="user-1",
        credential={"secrets": {"access_token": "stdio-token"}},
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="user-1", department_id="dep-1"),
        connection=connection,
    )

    assert resolved["command"] == "demo-server"
    assert resolved["env"] == {
        "LOG_LEVEL": "info",
        "API_TOKEN": "stdio-token",
        "YUXI_USER_ID": "user-1",
    }


async def test_resolve_runtime_mcp_config_uses_cached_custom_http_token_before_fetching():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected token request: {request.method} {request.url}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server = make_mcp_server(
        name="corp-cache-mcp",
        auth_config=token_auth_config(pre_refresh_seconds=120),
    )
    connection = make_mcp_connection(
        server_name="corp-cache-mcp",
        connection_id=21,
        credential={"secrets": {"client_id": "cid", "client_secret": "secret"}},
    )
    token_cache = DummyTokenCache(
        future_token_payload("cached-token", minutes=10, token_type="Bearer")
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="u-1", department_id="finance"),
        connection=connection,
        http_client=http_client,
        token_cache=token_cache,
    )

    await http_client.aclose()

    assert resolved["headers"] == {"Authorization": "Bearer cached-token"}
    assert token_cache.set_calls == []


async def test_resolve_runtime_mcp_config_refreshes_cached_token_when_expiring_soon():
    captured: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        captured.append((str(request.url), payload))
        return httpx.Response(
            200,
            json={
                "access_token": "refreshed-token",
                "refresh_token": "refresh-next",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server = make_mcp_server(
        name="corp-refresh-mcp",
        auth_config=token_auth_config(
            pre_refresh_seconds=300,
            token_request={
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
                    "token_type": "token_type",
                },
                "refresh": {
                    "url": "http://gateway.local/auth/refresh",
                    "method": "POST",
                    "body_type": "json",
                    "body_template": {
                        "refresh_token": "${token.refresh_token}",
                    },
                },
            },
        ),
    )
    connection = make_mcp_connection(
        server_name="corp-refresh-mcp",
        connection_id=22,
        credential={
            "secrets": {"client_id": "cid", "client_secret": "secret"},
            "refresh_token": "refresh-old",
        },
    )
    token_cache = DummyTokenCache(
        future_token_payload("stale-token", minutes=1, refresh_token="refresh-old")
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="u-1", department_id="finance"),
        connection=connection,
        http_client=http_client,
        token_cache=token_cache,
    )

    await http_client.aclose()

    assert captured == [
        (
            "http://gateway.local/auth/refresh",
            {
                "refresh_token": "refresh-old",
            },
        )
    ]
    assert resolved["headers"] == {"Authorization": "Bearer refreshed-token"}
    assert token_cache.set_calls and token_cache.set_calls[0][0] == 22
    assert token_cache.set_calls[0][1]["access_token"] == "refreshed-token"
    assert token_cache.acquire_calls == [22]
    assert token_cache.release_calls == [22]


async def test_resolve_runtime_mcp_config_waits_for_refresh_lock_owner_to_publish_token():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected token request: {request.method} {request.url}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server = make_mcp_server(
        name="corp-lock-mcp",
        auth_config=token_auth_config(pre_refresh_seconds=300),
    )
    connection = make_mcp_connection(
        server_name="corp-lock-mcp",
        connection_id=24,
        credential={"secrets": {"client_id": "cid", "client_secret": "secret"}},
    )
    token_cache = DummyTokenCache()
    token_cache.acquire_result = False
    token_cache.token_payloads = [
        future_token_payload("stale-token", minutes=0),
        future_token_payload("fresh-from-other-worker"),
    ]

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="u-1", department_id="finance"),
        connection=connection,
        http_client=http_client,
        token_cache=token_cache,
    )

    await http_client.aclose()

    assert resolved["headers"] == {"Authorization": "Bearer fresh-from-other-worker"}
    assert token_cache.acquire_calls == [24]
    assert token_cache.release_calls == []
    assert token_cache.set_calls == []


async def test_resolve_runtime_mcp_config_refreshes_authorization_code_token():
    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.method, str(request.url)))
        if str(request.url) == "https://id.example.com/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "token_endpoint": "https://id.example.com/oauth/token",
                },
            )
        if str(request.url) == "https://id.example.com/oauth/token":
            body_text = request.content.decode("utf-8")
            assert "grant_type=refresh_token" in body_text
            assert "refresh_token=refresh-old" in body_text
            assert "client_id=oidc-client" in body_text
            assert "client_secret=oidc-secret" in body_text
            return httpx.Response(
                200,
                json={
                    "access_token": "oidc-access-token",
                    "refresh_token": "refresh-next",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    server = make_mcp_server(
        name="oidc-mcp",
        auth_config=token_auth_config(
            provider="authorization_code",
            binding_scope="user",
            manifest_scope="binding",
            pre_refresh_seconds=120,
            token_request={
                "issuer_url": "https://id.example.com",
                "client_id": "${secret.client_id}",
                "client_secret": "${secret.client_secret}",
            },
        ),
    )
    connection = make_mcp_connection(
        server_name="oidc-mcp",
        connection_id=23,
        scope_type="user",
        scope_id="user-1",
        credential={
            "secrets": {
                "client_id": "oidc-client",
                "client_secret": "oidc-secret",
            },
            "refresh_token": "refresh-old",
        },
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="user-1", department_id="dep-1"),
        connection=connection,
        http_client=http_client,
        token_cache=DummyTokenCache(),
    )

    await http_client.aclose()

    assert captured == [
        ("GET", "https://id.example.com/.well-known/openid-configuration"),
        ("POST", "https://id.example.com/oauth/token"),
    ]
    assert resolved["headers"] == {"Authorization": "Bearer oidc-access-token"}


@pytest.mark.parametrize(
    "expires_at",
    [
        datetime(2026, 6, 5, 12, 0, 0),
        datetime(2026, 6, 5, 20, 0, 0, tzinfo=timezone(timedelta(hours=8))),
    ],
)
async def test_normalize_token_payload_datetime_to_utc(expires_at):
    normalized = _normalize_token_payload({"expires_at": expires_at})

    assert normalized["expires_at"] == datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC).isoformat()
