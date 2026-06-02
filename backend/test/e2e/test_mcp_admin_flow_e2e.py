from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


def _build_server_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _build_auth_config() -> dict:
    return {
        "version": 1,
        "provider": "custom_http_token",
        "binding_scope": "department",
        "manifest_scope": "binding",
        "inject": {
            "target": "headers",
            "entries": [
                {
                    "name": "Authorization",
                    "value_template": "Bearer ${access_token}",
                },
                {
                    "name": "X-Yuxi-User",
                    "value_template": "${context.user_id}",
                },
                {
                    "name": "X-Yuxi-Department",
                    "value_template": "${context.department_id}",
                },
            ],
        },
        "refresh_policy": {
            "pre_refresh_seconds": 300,
            "retry_once_on_401": True,
        },
        "token_request": {
            "url": "http://internal-gateway.local/token",
            "method": "POST",
            "body_type": "json",
            "headers": {
                "Content-Type": "application/json",
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
    }


async def _cleanup_server(client: httpx.AsyncClient, headers: dict[str, str], server_name: str) -> None:
    list_response = await client.get(f"/api/system/mcp-servers/{server_name}/connections", headers=headers)
    if list_response.status_code == 200:
        for connection in list_response.json().get("data", []):
            await client.delete(
                f"/api/system/mcp-servers/{server_name}/connections/{connection['id']}",
                headers=headers,
            )

    await client.delete(f"/api/system/mcp-servers/{server_name}", headers=headers)
    await client.delete(
        f"/api/system/mcp-servers/{server_name}",
        params={"hard": "true"},
        headers=headers,
    )


async def test_mcp_admin_flow_e2e_supports_dynamic_auth_connections(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
):
    invalid_server_name = _build_server_name("e2e-mcp-invalid-auth")
    invalid_response = await e2e_client.post(
        "/api/system/mcp-servers",
        json={
            "name": invalid_server_name,
            "transport": "streamable_http",
            "url": "http://mcp-upstream.local/mcp",
            "auth_config": {
                "version": 1,
                "provider": "custom_http_token",
                "binding_scope": "department",
                "inject": {
                    "target": "headers",
                    "entries": [
                        {
                            "name": "Authorization",
                            "value_template": "Bearer ${access_token}",
                        }
                    ],
                },
            },
        },
        headers=e2e_headers,
    )
    assert invalid_response.status_code == 400, invalid_response.text
    assert "auth_config 配置无效" in invalid_response.json()["detail"]

    server_name = _build_server_name("e2e-mcp-auth")
    create_server_response = await e2e_client.post(
        "/api/system/mcp-servers",
        json={
            "name": server_name,
            "transport": "streamable_http",
            "url": "http://mcp-upstream.local/mcp",
            "description": "e2e mcp auth server",
            "auth_config": _build_auth_config(),
        },
        headers=e2e_headers,
    )
    assert create_server_response.status_code == 200, create_server_response.text

    try:
        server_payload = create_server_response.json()["data"]
        assert server_payload["name"] == server_name
        assert server_payload["auth_config"]["provider"] == "custom_http_token"

        create_connection_response = await e2e_client.post(
            f"/api/system/mcp-servers/{server_name}/connections",
            json={
                "scope_type": "system",
                "scope_id": "ignored-by-normalization",
                "display_name": "全局共享连接",
                "external_subject": "gateway-service-account",
                "credential": {
                    "secrets": {
                        "client_id": "cid-1",
                        "client_secret": "secret-1",
                    },
                    "refresh_token": "refresh-1",
                },
                "meta_json": {"tenant": "shared"},
            },
            headers=e2e_headers,
        )
        assert create_connection_response.status_code == 200, create_connection_response.text
        connection_payload = create_connection_response.json()["data"]
        connection_id = connection_payload["id"]
        assert connection_payload["scope_type"] == "system"
        assert connection_payload["scope_id"] == "global"
        assert connection_payload["status"] == "active"
        assert connection_payload["has_credentials"] is True

        list_connections_response = await e2e_client.get(
            f"/api/system/mcp-servers/{server_name}/connections",
            headers=e2e_headers,
        )
        assert list_connections_response.status_code == 200, list_connections_response.text
        assert list_connections_response.json()["data"] == [connection_payload]

        retire_response = await e2e_client.delete(
            f"/api/system/mcp-servers/{server_name}",
            headers=e2e_headers,
        )
        assert retire_response.status_code == 200, retire_response.text

        hard_delete_conflict_response = await e2e_client.delete(
            f"/api/system/mcp-servers/{server_name}",
            params={"hard": "true"},
            headers=e2e_headers,
        )
        assert hard_delete_conflict_response.status_code == 409, hard_delete_conflict_response.text
        dependency_payload = hard_delete_conflict_response.json()["detail"]
        assert dependency_payload["has_references"] is True
        assert dependency_payload["connections"] == [
            {
                "scope_type": "system",
                "scope_id": "global",
                "status": "active",
            }
        ]

        delete_connection_response = await e2e_client.delete(
            f"/api/system/mcp-servers/{server_name}/connections/{connection_id}",
            headers=e2e_headers,
        )
        assert delete_connection_response.status_code == 200, delete_connection_response.text

        hard_delete_response = await e2e_client.delete(
            f"/api/system/mcp-servers/{server_name}",
            params={"hard": "true"},
            headers=e2e_headers,
        )
        assert hard_delete_response.status_code == 200, hard_delete_response.text
    finally:
        await _cleanup_server(e2e_client, e2e_headers, server_name)
