"""
Integration tests for API Key router endpoints.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

API_KEYS_PATH = "/api/user/apikey/"
# 受登录保护的轻量端点，用于验证 Bearer 鉴权（API Key / JWT）是否生效，无需执行智能体
PROTECTED_PATH = "/api/agent"


async def test_list_api_keys_requires_auth(test_client):
    """List API keys should require authentication."""
    response = await test_client.get(API_KEYS_PATH)
    assert response.status_code == 401


async def test_list_api_keys_requires_admin(test_client, admin_headers):
    """List API keys should require admin privileges."""
    response = await test_client.get(API_KEYS_PATH, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "api_keys" in data
    assert "total" in data


async def test_create_api_key(test_client, admin_headers):
    """Admin should be able to create a new API key."""
    payload = {
        "name": "Test API Key",
    }
    response = await test_client.post(API_KEYS_PATH, json=payload, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "api_key" in data
    assert "secret" in data
    assert data["api_key"]["name"] == "Test API Key"
    assert data["api_key"]["key_prefix"].startswith("yxkey_")
    assert data["secret"].startswith(data["api_key"]["key_prefix"])


async def test_get_api_key(test_client, admin_headers):
    """Admin should be able to get a single API key."""
    # First create a key
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Get Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Then retrieve it
    response = await test_client.get(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["api_key"]["id"] == created["id"]
    assert data["api_key"]["name"] == "Get Test"


async def test_update_api_key(test_client, admin_headers):
    """Admin should be able to update an API key."""
    # Create a key
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Update Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Update it
    response = await test_client.put(
        f"{API_KEYS_PATH}{created['id']}",
        json={"name": "Updated Name", "is_enabled": False},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["api_key"]["name"] == "Updated Name"
    assert data["api_key"]["is_enabled"] is False


async def test_delete_api_key(test_client, admin_headers):
    """Admin should be able to delete an API key."""
    # Create a key
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Delete Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Delete it
    response = await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True

    # Verify it's gone
    get_response = await test_client.get(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
    assert get_response.status_code == 404


async def test_regenerate_api_key_endpoint_is_removed(test_client, admin_headers):
    response = await test_client.post(f"{API_KEYS_PATH}1/regenerate", headers=admin_headers)
    assert response.status_code == 404, response.text


async def test_api_key_auth_protected_endpoint(test_client, admin_headers):
    """Test that API Key can be used to authenticate to a protected endpoint via Bearer token."""
    # Create an API key
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Auth Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    api_key_secret = create_response.json()["secret"]
    created = create_response.json()["api_key"]

    try:
        response = await test_client.get(
            PROTECTED_PATH,
            headers={"Authorization": f"Bearer {api_key_secret}"},
        )
        assert response.status_code == 200, response.text
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)


async def test_api_key_auth_requires_valid_key(test_client):
    """Test that invalid API Key is rejected."""
    # Call protected endpoint with invalid API Key
    response = await test_client.get(
        PROTECTED_PATH,
        headers={"Authorization": "Bearer yxkey_invalid_key_that_does_not_exist"},
    )
    assert response.status_code == 401, response.text


async def test_api_key_auth_requires_bearer_prefix(test_client, admin_headers):
    """Test that API Key must be prefixed with 'Bearer '."""
    # Create an API key
    admin_response = await test_client.post(API_KEYS_PATH, json={"name": "Prefix Test"}, headers=admin_headers)
    assert admin_response.status_code == 200
    api_key_secret = admin_response.json()["secret"]
    created = admin_response.json()["api_key"]

    try:
        # Call without Bearer prefix should fail
        response = await test_client.get(
            PROTECTED_PATH,
            headers={"Authorization": api_key_secret},  # Missing "Bearer " prefix
        )
        assert response.status_code == 401, response.text
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)


async def test_api_key_auto_binds_to_current_user(test_client, admin_headers):
    """Test that API Key created without user_id is auto-bound to creator."""
    # Create API key as admin
    create_response = await test_client.post(API_KEYS_PATH, json={"name": "Auto Bind Test"}, headers=admin_headers)
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    try:
        # Verify user_id is set (auto-bound to admin)
        assert created["user_id"] is not None, "API Key should be auto-bound to creator"
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"{API_KEYS_PATH}{created['id']}", headers=admin_headers)
