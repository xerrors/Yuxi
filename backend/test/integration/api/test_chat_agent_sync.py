"""
Integration tests for current agent run endpoints.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_agent_run_endpoints_require_authentication(test_client):
    run_id = str(uuid.uuid4())

    create_response = await test_client.post(
        "/api/agent/runs",
        json={"query": "hello", "agent_slug": "default-chatbot", "thread_id": str(uuid.uuid4())},
    )
    assert create_response.status_code == 401
    assert (await test_client.get(f"/api/agent/runs/{run_id}")).status_code == 401
    assert (await test_client.post(f"/api/agent/runs/{run_id}/cancel")).status_code == 401


async def test_agent_run_create_rejects_empty_input(test_client, admin_headers):
    response = await test_client.post(
        "/api/agent/runs",
        json={"query": "", "agent_slug": "default-chatbot", "thread_id": str(uuid.uuid4())},
        headers=admin_headers,
    )
    assert response.status_code == 404


async def test_agent_run_missing_resource_returns_not_found(test_client, admin_headers):
    run_id = str(uuid.uuid4())

    get_response = await test_client.get(f"/api/agent/runs/{run_id}", headers=admin_headers)
    assert get_response.status_code == 404

    cancel_response = await test_client.post(f"/api/agent/runs/{run_id}/cancel", headers=admin_headers)
    assert cancel_response.status_code == 404
