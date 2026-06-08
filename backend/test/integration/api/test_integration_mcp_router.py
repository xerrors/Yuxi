from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


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


async def _create_server(test_client, admin_headers: dict[str, str], name: str) -> None:
    response = await test_client.post(
        "/api/system/mcp-servers",
        json={
            "name": name,
            "transport": "streamable_http",
            "url": "http://mcp-upstream.local/mcp",
            "description": "pytest mcp auth server",
            "auth_config": _build_auth_config(),
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text


async def _cleanup_server(test_client, admin_headers: dict[str, str], name: str) -> None:
    list_response = await test_client.get(f"/api/system/mcp-servers/{name}/connections", headers=admin_headers)
    if list_response.status_code == 200:
        for connection in list_response.json().get("data", []):
            await test_client.delete(
                f"/api/system/mcp-servers/{name}/connections/{connection['id']}",
                headers=admin_headers,
            )

    await test_client.delete(f"/api/system/mcp-servers/{name}", headers=admin_headers)
    await test_client.delete(
        f"/api/system/mcp-servers/{name}",
        params={"hard": "true"},
        headers=admin_headers,
    )


async def test_admin_can_manage_mcp_server_connections_via_real_api(test_client, admin_headers):
    server_name = _build_server_name("pytest-mcp-auth")
    await _create_server(test_client, admin_headers, server_name)

    try:
        get_response = await test_client.get(f"/api/system/mcp-servers/{server_name}", headers=admin_headers)
        assert get_response.status_code == 200, get_response.text
        get_payload = get_response.json()["data"]
        assert get_payload["name"] == server_name
        assert get_payload["auth_config"]["provider"] == "custom_http_token"

        update_response = await test_client.put(
            f"/api/system/mcp-servers/{server_name}",
            json={
                "description": "updated auth server",
                "auth_config": {
                    **_build_auth_config(),
                    "refresh_policy": {
                        "pre_refresh_seconds": 120,
                        "retry_once_on_401": True,
                    },
                },
            },
            headers=admin_headers,
        )
        assert update_response.status_code == 200, update_response.text
        updated_payload = update_response.json()["data"]
        assert updated_payload["name"] == server_name
        assert updated_payload["description"] == "updated auth server"
        assert updated_payload["auth_config"]["refresh_policy"]["pre_refresh_seconds"] == 120

        create_connection_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections",
            json={
                "scope_type": "department",
                "scope_id": "finance-dept",
                "display_name": "财务共享连接",
                "external_subject": "finance-bot",
                "credential": {
                    "secrets": {
                        "client_id": "cid-1",
                        "client_secret": "secret-1",
                    },
                    "refresh_token": "refresh-1",
                },
                "meta_json": {"tenant": "finance"},
            },
            headers=admin_headers,
        )
        assert create_connection_response.status_code == 200, create_connection_response.text
        connection_payload = create_connection_response.json()["data"]
        connection_id = connection_payload["id"]
        assert connection_payload["scope_type"] == "department"
        assert connection_payload["display_name"] == "财务共享连接"
        assert connection_payload["has_credentials"] is True
        assert "credential_blob" not in connection_payload

        list_connections_response = await test_client.get(
            f"/api/system/mcp-servers/{server_name}/connections",
            headers=admin_headers,
        )
        assert list_connections_response.status_code == 200, list_connections_response.text
        listed_connections = list_connections_response.json()["data"]
        assert len(listed_connections) == 1
        assert listed_connections[0]["id"] == connection_id
        assert listed_connections[0]["has_credentials"] is True
        assert "credential_blob" not in listed_connections[0]

        update_connection_response = await test_client.put(
            f"/api/system/mcp-servers/{server_name}/connections/{connection_id}",
            json={
                "display_name": "财务共享连接-更新",
                "meta_json": {"tenant": "finance", "stage": "updated"},
            },
            headers=admin_headers,
        )
        assert update_connection_response.status_code == 200, update_connection_response.text
        assert update_connection_response.json()["data"]["display_name"] == "财务共享连接-更新"
        assert update_connection_response.json()["data"]["meta_json"]["stage"] == "updated"

        status_response = await test_client.put(
            f"/api/system/mcp-servers/{server_name}/connections/{connection_id}/status",
            json={"status": "reauth_required"},
            headers=admin_headers,
        )
        assert status_response.status_code == 200, status_response.text
        assert status_response.json()["data"]["status"] == "reauth_required"

        reauth_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections/{connection_id}/reauth",
            headers=admin_headers,
        )
        assert reauth_response.status_code == 200, reauth_response.text
        assert reauth_response.json()["data"]["status"] == "active"

        delete_connection_response = await test_client.delete(
            f"/api/system/mcp-servers/{server_name}/connections/{connection_id}",
            headers=admin_headers,
        )
        assert delete_connection_response.status_code == 200, delete_connection_response.text

        retire_response = await test_client.delete(f"/api/system/mcp-servers/{server_name}", headers=admin_headers)
        assert retire_response.status_code == 200, retire_response.text

        hard_delete_response = await test_client.delete(
            f"/api/system/mcp-servers/{server_name}",
            params={"hard": "true"},
            headers=admin_headers,
        )
        assert hard_delete_response.status_code == 200, hard_delete_response.text
    finally:
        await _cleanup_server(test_client, admin_headers, server_name)


async def test_hard_delete_mcp_server_returns_dependency_summary_when_connections_exist(
    test_client, admin_headers
):
    server_name = _build_server_name("pytest-mcp-delete")
    await _create_server(test_client, admin_headers, server_name)

    try:
        create_connection_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections",
            json={
                "scope_type": "department",
                "scope_id": "finance-dept",
                "display_name": "财务共享连接",
                "credential": {
                    "secrets": {
                        "client_id": "cid-1",
                        "client_secret": "secret-1",
                    }
                },
            },
            headers=admin_headers,
        )
        assert create_connection_response.status_code == 200, create_connection_response.text
        connection_id = create_connection_response.json()["data"]["id"]

        retire_response = await test_client.delete(f"/api/system/mcp-servers/{server_name}", headers=admin_headers)
        assert retire_response.status_code == 200, retire_response.text

        hard_delete_response = await test_client.delete(
            f"/api/system/mcp-servers/{server_name}",
            params={"hard": "true"},
            headers=admin_headers,
        )
        assert hard_delete_response.status_code == 409, hard_delete_response.text
        detail = hard_delete_response.json()["detail"]
        assert detail["has_references"] is True
        assert detail["connections"] == [
            {
                "scope_type": "department",
                "scope_id": "finance-dept",
                "status": "active",
            }
        ]

        delete_connection_response = await test_client.delete(
            f"/api/system/mcp-servers/{server_name}/connections/{connection_id}",
            headers=admin_headers,
        )
        assert delete_connection_response.status_code == 200, delete_connection_response.text

        hard_delete_after_cleanup_response = await test_client.delete(
            f"/api/system/mcp-servers/{server_name}",
            params={"hard": "true"},
            headers=admin_headers,
        )
        assert hard_delete_after_cleanup_response.status_code == 200, hard_delete_after_cleanup_response.text
    finally:
        await _cleanup_server(test_client, admin_headers, server_name)


async def test_bound_auth_server_test_endpoint_requires_connection_level_testing(test_client, admin_headers):
    server_name = _build_server_name("pytest-mcp-bound-test")
    await _create_server(test_client, admin_headers, server_name)

    try:
        response = await test_client.post(f"/api/system/mcp-servers/{server_name}/test", headers=admin_headers)
        assert response.status_code == 400, response.text
        assert "需要绑定连接" in response.json()["detail"]
    finally:
        await _cleanup_server(test_client, admin_headers, server_name)


async def test_bound_auth_server_test_endpoint_succeeds_with_connection(test_client, admin_headers):
    me_response = await test_client.get("/api/auth/me", headers=admin_headers)
    assert me_response.status_code == 200, me_response.text
    me_data = me_response.json()
    admin_dept_id = str(me_data["department_id"])

    server_name = _build_server_name("pytest-mcp-bound-test-ok")

    response = await test_client.post(
        "/api/system/mcp-servers",
        json={
            "name": server_name,
            "transport": "sse",
            "url": "http://mcp-demo-server:8999/sse",
            "description": "pytest mcp auth server ok",
            "auth_config": {
                "version": 1,
                "provider": "bound_secret",
                "binding_scope": "department",
                "inject": {
                    "target": "headers",
                    "entries": [
                        {
                            "name": "Authorization",
                            "value_template": "Bearer ${secret.access_token}",
                        }
                    ],
                },
            },
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    try:
        test_fail_response = await test_client.post(f"/api/system/mcp-servers/{server_name}/test", headers=admin_headers)
        assert test_fail_response.status_code == 400, test_fail_response.text
        assert "需要绑定连接" in test_fail_response.json()["detail"]

        conn_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections",
            json={
                "scope_type": "department",
                "scope_id": admin_dept_id,
                "display_name": "Dept Scope Test OK",
                "credential": {"secrets": {"access_token": "dummy_dept_token"}},
            },
            headers=admin_headers,
        )
        assert conn_response.status_code == 200, conn_response.text
        conn_id = conn_response.json()["data"]["id"]

        test_ok_response = await test_client.post(f"/api/system/mcp-servers/{server_name}/test", headers=admin_headers)
        assert test_ok_response.status_code == 200, test_ok_response.text
        assert test_ok_response.json()["success"] is True
        assert test_ok_response.json()["tool_count"] > 0
    finally:
        await _cleanup_server(test_client, admin_headers, server_name)



async def test_bound_auth_tools_endpoint_requires_current_admin_connection(test_client, admin_headers):
    server_name = _build_server_name("pytest-mcp-bound-tools")
    await _create_server(test_client, admin_headers, server_name)

    try:
        response = await test_client.get(f"/api/system/mcp-servers/{server_name}/tools", headers=admin_headers)
        assert response.status_code == 403, response.text
        assert "Active MCP connection not found" in response.json()["detail"]

        refresh_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/tools/refresh",
            headers=admin_headers,
        )
        assert refresh_response.status_code == 403, refresh_response.text
        assert "Active MCP connection not found" in refresh_response.json()["detail"]
    finally:
        await _cleanup_server(test_client, admin_headers, server_name)


async def test_create_mcp_server_rejects_invalid_auth_config_via_real_api(test_client, admin_headers):
    server_name = _build_server_name("pytest-mcp-invalid-auth")

    response = await test_client.post(
        "/api/system/mcp-servers",
        json={
            "name": server_name,
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
        headers=admin_headers,
    )

    assert response.status_code == 400, response.text
    assert "auth_config 配置无效" in response.json()["detail"]


async def test_create_system_connection_defaults_scope_id_to_global_via_real_api(test_client, admin_headers):
    server_name = _build_server_name("pytest-mcp-system-scope")
    await _create_server(test_client, admin_headers, server_name)

    try:
        response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections",
            json={
                "scope_type": "system",
                "scope_id": "",
                "display_name": "全局共享连接",
                "credential": {"secrets": {"client_id": "cid-1", "client_secret": "secret-1"}},
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()["data"]
        assert payload["scope_type"] == "system"
        assert payload["scope_id"] == "global"
    finally:
        await _cleanup_server(test_client, admin_headers, server_name)


async def test_mcp_connections_all_scopes_e2e(test_client, admin_headers):
    # 1. 获取当前管理员的用户信息以获取正确的用户 ID 和部门 ID
    me_response = await test_client.get("/api/auth/me", headers=admin_headers)
    assert me_response.status_code == 200, me_response.text
    me_data = me_response.json()
    admin_db_id = str(me_data["id"])
    admin_dept_id = str(me_data["department_id"])

    server_name = _build_server_name("pytest-mcp-scopes")

    try:
        # A. 测试个人 (User) 范围
        # 创建一个 binding_scope="user" 的服务器
        create_response = await test_client.post(
            "/api/system/mcp-servers",
            json={
                "name": server_name,
                "transport": "sse",
                "url": "http://mcp-demo-server:8999/sse",  # 使用启动的 mock server sse 端口
                "description": "pytest scopes user test",
                "auth_config": {
                    "version": 1,
                    "provider": "bound_secret",
                    "binding_scope": "user",
                    "inject": {
                        "target": "headers",
                        "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
                    },
                },
            },
            headers=admin_headers,
        )
        assert create_response.status_code == 200, create_response.text

        # 创建对应的个人连接，scope_id 必须与当前用户的 db_id (主键数字字符串) 一致
        conn_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections",
            json={
                "scope_type": "user",
                "scope_id": admin_db_id,
                "display_name": "User Scope Test",
                "credential": {"secrets": {"access_token": "dummy_user_token"}},
            },
            headers=admin_headers,
        )
        assert conn_response.status_code == 200, conn_response.text
        conn_id = conn_response.json()["data"]["id"]

        # 测试该连接的可用性，测试时会根据 auth_context 自动解析并匹配 scope_id
        test_conn_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections/{conn_id}/test",
            headers=admin_headers,
        )
        assert test_conn_response.status_code == 200, test_conn_response.text
        assert test_conn_response.json()["tool_count"] > 0

        # 清理该连接
        del_response = await test_client.delete(
            f"/api/system/mcp-servers/{server_name}/connections/{conn_id}",
            headers=admin_headers,
        )
        assert del_response.status_code == 200, del_response.text

        # 清理服务器 (软删除)
        retire_response = await test_client.delete(f"/api/system/mcp-servers/{server_name}", headers=admin_headers)
        assert retire_response.status_code == 200, retire_response.text
        hard_del_response = await test_client.delete(f"/api/system/mcp-servers/{server_name}?hard=true", headers=admin_headers)
        assert hard_del_response.status_code == 200, hard_del_response.text

        # B. 测试部门 (Department) 范围
        create_response = await test_client.post(
            "/api/system/mcp-servers",
            json={
                "name": server_name,
                "transport": "sse",
                "url": "http://mcp-demo-server:8999/sse",
                "description": "pytest scopes dept test",
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
            headers=admin_headers,
        )
        assert create_response.status_code == 200, create_response.text

        # 创建对应的部门连接
        conn_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections",
            json={
                "scope_type": "department",
                "scope_id": admin_dept_id,
                "display_name": "Dept Scope Test",
                "credential": {"secrets": {"access_token": "dummy_dept_token"}},
            },
            headers=admin_headers,
        )
        assert conn_response.status_code == 200, conn_response.text
        conn_id = conn_response.json()["data"]["id"]

        # 测试连接
        test_conn_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections/{conn_id}/test",
            headers=admin_headers,
        )
        assert test_conn_response.status_code == 200, test_conn_response.text
        assert test_conn_response.json()["tool_count"] > 0

        # 清理
        await test_client.delete(f"/api/system/mcp-servers/{server_name}/connections/{conn_id}", headers=admin_headers)
        await test_client.delete(f"/api/system/mcp-servers/{server_name}", headers=admin_headers)
        await test_client.delete(f"/api/system/mcp-servers/{server_name}?hard=true", headers=admin_headers)

        # C. 测试系统 (System) 范围
        create_response = await test_client.post(
            "/api/system/mcp-servers",
            json={
                "name": server_name,
                "transport": "sse",
                "url": "http://mcp-demo-server:8999/sse",
                "description": "pytest scopes system test",
                "auth_config": {
                    "version": 1,
                    "provider": "bound_secret",
                    "binding_scope": "system",
                    "inject": {
                        "target": "headers",
                        "entries": [{"name": "Authorization", "value_template": "Bearer ${secret.access_token}"}],
                    },
                },
            },
            headers=admin_headers,
        )
        assert create_response.status_code == 200, create_response.text

        # 创建对应的全局连接
        conn_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections",
            json={
                "scope_type": "system",
                "scope_id": "global",
                "display_name": "Global Scope Test",
                "credential": {"secrets": {"access_token": "dummy_global_token"}},
            },
            headers=admin_headers,
        )
        assert conn_response.status_code == 200, conn_response.text
        conn_id = conn_response.json()["data"]["id"]

        # 测试连接
        test_conn_response = await test_client.post(
            f"/api/system/mcp-servers/{server_name}/connections/{conn_id}/test",
            headers=admin_headers,
        )
        assert test_conn_response.status_code == 200, test_conn_response.text
        assert test_conn_response.json()["tool_count"] > 0

        # 清理
        await test_client.delete(f"/api/system/mcp-servers/{server_name}/connections/{conn_id}", headers=admin_headers)
        await test_client.delete(f"/api/system/mcp-servers/{server_name}", headers=admin_headers)
        await test_client.delete(f"/api/system/mcp-servers/{server_name}?hard=true", headers=admin_headers)

    finally:
        await _cleanup_server(test_client, admin_headers, server_name)

