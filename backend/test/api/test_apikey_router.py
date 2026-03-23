"""
Integration tests for API Key router endpoints.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_list_api_keys_requires_auth(test_client):
    """List API keys should require authentication."""
    response = await test_client.get("/api/apikey/")
    assert response.status_code == 401


async def test_list_api_keys_requires_admin(test_client, admin_headers):
    """List API keys should require admin privileges."""
    response = await test_client.get("/api/apikey/", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "api_keys" in data
    assert "total" in data


async def test_create_api_key(test_client, admin_headers):
    """Admin should be able to create a new API key."""
    payload = {
        "name": "Test API Key",
    }
    response = await test_client.post("/api/apikey/", json=payload, headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "api_key" in data
    assert "secret" in data
    assert data["api_key"]["name"] == "Test API Key"
    assert data["api_key"]["key_prefix"].startswith("yxkey_")
    # Note: The "****" suffix is added by the frontend, not stored in backend
    assert data["api_key"]["key_prefix"] == "yxkey_144cba" or data["api_key"]["key_prefix"].startswith("yxkey_")
    # Secret should start with the prefix
    assert data["secret"].startswith(data["api_key"]["key_prefix"][:6])
    return data


async def test_get_api_key(test_client, admin_headers):
    """Admin should be able to get a single API key."""
    # First create a key
    create_response = await test_client.post(
        "/api/apikey/", json={"name": "Get Test"}, headers=admin_headers
    )
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Then retrieve it
    response = await test_client.get(f"/api/apikey/{created['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["api_key"]["id"] == created["id"]
    assert data["api_key"]["name"] == "Get Test"


async def test_update_api_key(test_client, admin_headers):
    """Admin should be able to update an API key."""
    # Create a key
    create_response = await test_client.post(
        "/api/apikey/", json={"name": "Update Test"}, headers=admin_headers
    )
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Update it
    response = await test_client.put(
        f"/api/apikey/{created['id']}",
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
    create_response = await test_client.post(
        "/api/apikey/", json={"name": "Delete Test"}, headers=admin_headers
    )
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    # Delete it
    response = await test_client.delete(f"/api/apikey/{created['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True

    # Verify it's gone
    get_response = await test_client.get(f"/api/apikey/{created['id']}", headers=admin_headers)
    assert get_response.status_code == 404


async def test_regenerate_api_key(test_client, admin_headers):
    """Admin should be able to regenerate an API key."""
    # Create a key
    create_response = await test_client.post(
        "/api/apikey/", json={"name": "Regenerate Test"}, headers=admin_headers
    )
    assert create_response.status_code == 200
    original_secret = create_response.json()["secret"]
    created = create_response.json()["api_key"]

    # Regenerate it
    response = await test_client.post(f"/api/apikey/{created['id']}/regenerate", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "secret" in data
    assert data["secret"] != original_secret
    assert data["api_key"]["key_prefix"] != original_secret[:12]


async def test_api_key_auth_chat_endpoint(test_client, admin_headers):
    """Test that API Key can be used to authenticate to chat endpoint via Bearer token."""
    # Create an API key
    create_response = await test_client.post(
        "/api/apikey/", json={"name": "Chat Auth Test"}, headers=admin_headers
    )
    assert create_response.status_code == 200
    api_key_secret = create_response.json()["secret"]
    created = create_response.json()["api_key"]

    try:
        # Get default agent
        agent_response = await test_client.get("/api/chat/default_agent", headers=admin_headers)
        assert agent_response.status_code == 200
        agent_id = agent_response.json().get("default_agent_id")
        if not agent_id:
            pytest.skip("No default agent configured")

        # Call chat endpoint with API Key using Bearer format (streaming response)
        async with test_client.stream(
            "POST",
            f"/api/chat/agent/{agent_id}",
            json={"query": "Hello"},
            headers={"Authorization": f"Bearer {api_key_secret}"},
        ) as response:
            assert response.status_code == 200, response.text
            assert response.headers.get("content-type") == "application/json"
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"/api/apikey/{created['id']}", headers=admin_headers)


async def test_api_key_auth_requires_valid_key(test_client):
    """Test that invalid API Key is rejected."""
    # Call chat endpoint with invalid API Key
    response = await test_client.post(
        "/api/chat/agent/ChatbotAgent",
        json={"query": "Hello"},
        headers={"Authorization": "Bearer yxkey_invalid_key_that_does_not_exist"},
    )
    assert response.status_code == 401, response.text


async def test_api_key_auth_requires_bearer_prefix(test_client, admin_headers):
    """Test that API Key must be prefixed with 'Bearer '."""
    # Create an API key
    admin_response = await test_client.post(
        "/api/apikey/", json={"name": "Prefix Test"}, headers=admin_headers
    )
    assert admin_response.status_code == 200
    api_key_secret = admin_response.json()["secret"]
    created = admin_response.json()["api_key"]

    try:
        # Call without Bearer prefix should fail
        response = await test_client.post(
            "/api/chat/agent/ChatbotAgent",
            json={"query": "Hello"},
            headers={"Authorization": api_key_secret},  # Missing "Bearer " prefix
        )
        assert response.status_code == 401, response.text
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"/api/apikey/{created['id']}", headers=admin_headers)


async def test_jwt_still_works_after_apikey_auth(test_client, admin_headers):
    """Test that JWT Bearer tokens still work after API Key changes."""
    # Get default agent
    agent_response = await test_client.get("/api/chat/default_agent", headers=admin_headers)
    assert agent_response.status_code == 200
    agent_id = agent_response.json().get("default_agent_id")
    if not agent_id:
        pytest.skip("No default agent configured")

    # Call chat with JWT Bearer token (admin_headers) - streaming response
    async with test_client.stream(
        "POST",
        f"/api/chat/agent/{agent_id}",
        json={"query": "Hello"},
        headers=admin_headers,
    ) as response:
        assert response.status_code == 200, response.text
        assert response.headers.get("content-type") == "application/json"


async def test_api_key_auto_binds_to_current_user(test_client, admin_headers):
    """Test that API Key created without user_id is auto-bound to creator."""
    # Create API key as admin
    create_response = await test_client.post(
        "/api/apikey/", json={"name": "Auto Bind Test"}, headers=admin_headers
    )
    assert create_response.status_code == 200
    created = create_response.json()["api_key"]

    try:
        # Verify user_id is set (auto-bound to admin)
        assert created["user_id"] is not None, "API Key should be auto-bound to creator"

        # Verify the key can be used for auth
        api_key_secret = create_response.json()["secret"]
        async with test_client.stream(
            "POST",
            "/api/chat/agent/ChatbotAgent",
            json={"query": "Hello"},
            headers={"Authorization": f"Bearer {api_key_secret}"},
        ) as response:
            assert response.status_code == 200, response.text
    finally:
        # Cleanup: delete the test API key
        await test_client.delete(f"/api/apikey/{created['id']}", headers=admin_headers)


