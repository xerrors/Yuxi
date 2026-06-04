from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services.mcp_auth.crypto import encrypt_credential_blob
from yuxi.services.mcp_auth.orchestrator import AuthContext, resolve_runtime_mcp_config
from yuxi.storage.postgres.models_business import MCPConnection, MCPServer


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


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


async def test_resolve_runtime_mcp_config_injects_bound_secret_header():
    os.environ["MCP_CREDENTIALS_MASTER_KEY"] = "local-test-master-key"
    server = MCPServer(
        name="finance-gateway",
        transport="streamable_http",
        url="http://finance.local/mcp",
        headers={"X-App": "yuxi"},
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
        server_name="finance-gateway",
        scope_type="department",
        scope_id="42",
        credential_blob=encrypt_credential_blob(json.dumps({"secrets": {"access_token": "dept-token"}})),
        created_by="tester",
        updated_by="tester",
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="u-1", department_id="42"),
        connection=connection,
    )

    assert resolved["transport"] == "streamable_http"
    assert resolved["headers"] == {
        "X-App": "yuxi",
        "Authorization": "Bearer dept-token",
    }
    assert "auth_config" not in resolved


async def test_resolve_runtime_mcp_config_supports_raw_token_string_binding():
    os.environ["MCP_CREDENTIALS_MASTER_KEY"] = "local-test-master-key"
    server = MCPServer(
        name="raw-token-gateway",
        transport="streamable_http",
        url="http://raw.local/mcp",
        auth_config_json={
            "version": 1,
            "provider": "bound_secret",
            "binding_scope": "system",
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
        server_name="raw-token-gateway",
        scope_type="system",
        scope_id="global",
        credential_blob=encrypt_credential_blob("raw-token-value"),
        created_by="tester",
        updated_by="tester",
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(),
        connection=connection,
    )

    assert resolved["headers"] == {"Authorization": "Bearer raw-token-value"}


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
    server = MCPServer(
        name="corp-gateway",
        transport="streamable_http",
        url="http://corp.local/mcp",
        headers={"X-App": "yuxi"},
        auth_config_json={
            "version": 1,
            "provider": "custom_http_token",
            "binding_scope": "department",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "refresh_policy": {"pre_refresh_seconds": 600, "retry_once_on_401": True},
            "token_request": {
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
        },
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(
        server_name="corp-gateway",
        scope_type="department",
        scope_id="finance",
        credential_blob=json.dumps({"secrets": {"client_id": "cid-1", "client_secret": "secret-1"}}),
        created_by="tester",
        updated_by="tester",
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="user-9", department_id="finance"),
        connection=connection,
        http_client=http_client,
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
    server = MCPServer(
        name="client-credentials-mcp",
        transport="streamable_http",
        url="http://client.local/mcp",
        auth_config_json={
            "version": 1,
            "provider": "client_credentials",
            "binding_scope": "system",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "token_request": {
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
        },
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(
        id=11,
        server_name="client-credentials-mcp",
        scope_type="system",
        scope_id="global",
        credential_blob=json.dumps({"secrets": {"client_id": "cid-cc", "client_secret": "secret-cc"}}),
        created_by="tester",
        updated_by="tester",
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="u-1", department_id="d-1"),
        connection=connection,
        http_client=http_client,
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
    server = MCPServer(
        name="stdio-auth-mcp",
        transport="stdio",
        command="demo-server",
        env={"LOG_LEVEL": "info"},
        auth_config_json={
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
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(
        server_name="stdio-auth-mcp",
        scope_type="user",
        scope_id="user-1",
        credential_blob=json.dumps({"secrets": {"access_token": "stdio-token"}}),
        created_by="tester",
        updated_by="tester",
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
    server = MCPServer(
        name="corp-cache-mcp",
        transport="streamable_http",
        url="http://corp-cache.local/mcp",
        auth_config_json={
            "version": 1,
            "provider": "custom_http_token",
            "binding_scope": "department",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "refresh_policy": {"pre_refresh_seconds": 120, "retry_once_on_401": True},
            "token_request": {
                "url": "http://gateway.local/auth/token",
                "method": "POST",
                "body_type": "json",
                "response_map": {
                    "access_token": "access_token",
                    "expires_in": "expires_in",
                },
            },
        },
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(
        id=21,
        server_name="corp-cache-mcp",
        scope_type="department",
        scope_id="finance",
        credential_blob=json.dumps({"secrets": {"client_id": "cid", "client_secret": "secret"}}),
        created_by="tester",
        updated_by="tester",
    )
    token_cache = DummyTokenCache(
        {
            "access_token": "cached-token",
            "expires_at": (datetime.now(tz=UTC) + timedelta(minutes=10)).isoformat(),
            "token_type": "Bearer",
        }
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
    server = MCPServer(
        name="corp-refresh-mcp",
        transport="streamable_http",
        url="http://corp-refresh.local/mcp",
        auth_config_json={
            "version": 1,
            "provider": "custom_http_token",
            "binding_scope": "department",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "refresh_policy": {"pre_refresh_seconds": 300, "retry_once_on_401": True},
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
        },
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(
        id=22,
        server_name="corp-refresh-mcp",
        scope_type="department",
        scope_id="finance",
        credential_blob=json.dumps(
            {
                "secrets": {"client_id": "cid", "client_secret": "secret"},
                "refresh_token": "refresh-old",
            }
        ),
        created_by="tester",
        updated_by="tester",
    )
    token_cache = DummyTokenCache(
        {
            "access_token": "stale-token",
            "refresh_token": "refresh-old",
            "expires_at": (datetime.now(tz=UTC) + timedelta(seconds=60)).isoformat(),
        }
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
    server = MCPServer(
        name="corp-lock-mcp",
        transport="streamable_http",
        url="http://corp-lock.local/mcp",
        auth_config_json={
            "version": 1,
            "provider": "custom_http_token",
            "binding_scope": "department",
            "manifest_scope": "server",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "refresh_policy": {"pre_refresh_seconds": 300, "retry_once_on_401": True},
            "token_request": {
                "url": "http://gateway.local/auth/token",
                "method": "POST",
                "body_type": "json",
                "response_map": {
                    "access_token": "access_token",
                    "expires_in": "expires_in",
                },
            },
        },
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(
        id=24,
        server_name="corp-lock-mcp",
        scope_type="department",
        scope_id="finance",
        credential_blob=json.dumps({"secrets": {"client_id": "cid", "client_secret": "secret"}}),
        created_by="tester",
        updated_by="tester",
    )
    token_cache = DummyTokenCache()
    token_cache.acquire_result = False
    token_cache.token_payloads = [
        {
            "access_token": "stale-token",
            "expires_at": (datetime.now(tz=UTC) + timedelta(seconds=10)).isoformat(),
        },
        {
            "access_token": "fresh-from-other-worker",
            "expires_at": (datetime.now(tz=UTC) + timedelta(minutes=30)).isoformat(),
        },
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
    server = MCPServer(
        name="oidc-mcp",
        transport="streamable_http",
        url="http://oidc.local/mcp",
        auth_config_json={
            "version": 1,
            "provider": "authorization_code",
            "binding_scope": "user",
            "manifest_scope": "binding",
            "inject": {
                "target": "headers",
                "entries": [{"name": "Authorization", "value_template": "Bearer ${access_token}"}],
            },
            "refresh_policy": {"pre_refresh_seconds": 120, "retry_once_on_401": True},
            "token_request": {
                "issuer_url": "https://id.example.com",
                "client_id": "${secret.client_id}",
                "client_secret": "${secret.client_secret}",
            },
        },
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(
        id=23,
        server_name="oidc-mcp",
        scope_type="user",
        scope_id="user-1",
        credential_blob=json.dumps(
            {
                "secrets": {
                    "client_id": "oidc-client",
                    "client_secret": "oidc-secret",
                },
                "refresh_token": "refresh-old",
            }
        ),
        created_by="tester",
        updated_by="tester",
    )

    resolved = await resolve_runtime_mcp_config(
        server,
        auth_context=AuthContext(user_id="user-1", department_id="dep-1"),
        connection=connection,
        http_client=http_client,
    )

    await http_client.aclose()

    assert captured == [
      ("GET", "https://id.example.com/.well-known/openid-configuration"),
      ("POST", "https://id.example.com/oauth/token"),
    ]
    assert resolved["headers"] == {"Authorization": "Bearer oidc-access-token"}


async def test_normalize_token_payload_naive_datetime():
    """测试 _normalize_token_payload 对 naive datetime 默认填充 UTC 时区"""
    from yuxi.services.mcp_auth.orchestrator import _normalize_token_payload
    from datetime import datetime, UTC
    
    # 构造 naive datetime (无 tzinfo)
    naive_dt = datetime(2026, 6, 5, 12, 0, 0)
    payload = {"expires_at": naive_dt}
    
    normalized = _normalize_token_payload(payload)
    # 期望转换后有时区，并且值为 2026-06-05T12:00:00+00:00 (ISO格式)
    expected_iso = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC).isoformat()
    assert normalized["expires_at"] == expected_iso


async def test_normalize_token_payload_aware_datetime():
    """测试 _normalize_token_payload 对于带时区的 datetime 维持原时区对应 UTC 时间"""
    from yuxi.services.mcp_auth.orchestrator import _normalize_token_payload
    from datetime import datetime, timezone, timedelta
    
    # 构造带时区的 datetime (比如东八区)
    shanghai_tz = timezone(timedelta(hours=8))
    aware_dt = datetime(2026, 6, 5, 20, 0, 0, tzinfo=shanghai_tz)
    payload = {"expires_at": aware_dt}
    
    normalized = _normalize_token_payload(payload)
    # 转换为 UTC 后应该为 2026-06-05T12:00:00+00:00
    expected_iso = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc).isoformat()
    assert normalized["expires_at"] == expected_iso
