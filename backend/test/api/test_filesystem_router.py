"""Integration tests for filesystem router endpoints."""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _create_thread_for_user(test_client, headers: dict[str, str]) -> str:
    agents_resp = await test_client.get("/api/chat/agent", headers=headers)
    assert agents_resp.status_code == 200, agents_resp.text
    agents = agents_resp.json().get("agents", [])
    if not agents:
        pytest.skip("No agents available for filesystem integration tests.")

    agent_id = agents[0].get("id")
    if not agent_id:
        pytest.skip("Agent payload missing id field.")

    create_resp = await test_client.post(
        "/api/chat/thread",
        json={
            "agent_id": agent_id,
            "title": f"filesystem-test-{uuid.uuid4().hex[:8]}",
            "metadata": {},
        },
        headers=headers,
    )
    assert create_resp.status_code == 200, create_resp.text
    payload = create_resp.json()
    thread_id = payload.get("thread_id") or payload.get("id")
    assert thread_id, f"Create thread response missing thread identifier: {payload}"
    return thread_id


async def test_filesystem_ls_requires_authentication(test_client):
    response = await test_client.get("/api/filesystem/ls", params={"thread_id": "x", "path": "/"})
    assert response.status_code == 401


async def test_filesystem_ls_root_lists_mount_namespaces(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    response = await test_client.get(
        "/api/filesystem/ls",
        params={"thread_id": thread_id, "path": "/"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    entries = response.json().get("entries", [])
    paths = {entry.get("path") for entry in entries}
    assert "/mnt/skills/" in paths
    assert "/mnt/user-data/" in paths


async def test_filesystem_ls_rejects_outside_mnt_paths(test_client, standard_user):
    headers = standard_user["headers"]
    thread_id = await _create_thread_for_user(test_client, headers)

    response = await test_client.get(
        "/api/filesystem/ls",
        params={"thread_id": thread_id, "path": "/etc"},
        headers=headers,
    )
    assert response.status_code == 400, response.text
    detail = response.json().get("detail", "")
    assert "outside" in detail or "Access denied" in detail


async def test_filesystem_cat_requires_authentication(test_client):
    response = await test_client.get("/api/filesystem/cat", params={"thread_id": "x", "path": "/"})
    assert response.status_code == 401


async def test_filesystem_ls_requires_thread_ownership(test_client, standard_user, admin_headers):
    owner_headers = standard_user["headers"]
    owner_thread_id = await _create_thread_for_user(test_client, owner_headers)

    intruder_resp = await test_client.post(
        "/api/auth/users",
        json={"username": f"pytest_user_{uuid.uuid4().hex[:8]}", "password": "Pw!filesystem123", "role": "user"},
        headers=admin_headers,
    )
    assert intruder_resp.status_code == 200, intruder_resp.text
    intruder = intruder_resp.json()

    login_resp = await test_client.post(
        "/api/auth/token",
        data={"username": intruder["user_id"], "password": "Pw!filesystem123"},
    )
    assert login_resp.status_code == 200, login_resp.text
    intruder_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    try:
        response = await test_client.get(
            "/api/filesystem/ls",
            params={"thread_id": owner_thread_id, "path": "/"},
            headers=intruder_headers,
        )
        assert response.status_code == 404, response.text
    finally:
        delete_resp = await test_client.delete(f"/api/auth/users/{intruder['id']}", headers=admin_headers)
        assert delete_resp.status_code == 200, delete_resp.text
