"""Integration tests for agent request queue API endpoints."""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _get_default_agent_slug(test_client, headers) -> str:
    resp = await test_client.get("/api/agent", headers=headers)
    assert resp.status_code == 200, resp.text
    agents = resp.json().get("agents", [])
    assert agents, "No agents available"
    return agents[0].get("slug") or agents[0].get("agent_id")


async def _create_thread(test_client, headers, agent_slug) -> str:
    resp = await test_client.post(
        "/api/chat/thread",
        json={"agent_id": agent_slug, "title": f"pytest-queue-{uuid.uuid4().hex[:8]}", "metadata": {}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    return payload.get("thread_id") or payload.get("id")


async def test_create_run_returns_request_info(test_client, admin_headers):
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)

    resp = await test_client.post(
        "/api/agent/runs",
        json={
            "query": "hello",
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "queue_policy": "enqueue",
            "meta": {},
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "request_id" in data
    assert data["queue_policy"] == "enqueue"
    assert data["status"] in ("dispatched", "queued")


async def test_async_agent_call_uses_request_intake(test_client, admin_headers):
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    request_id = f"agent-call-queue-{uuid.uuid4()}"

    response = await test_client.post(
        "/api/agent-invocation/agent-call/runs",
        json={
            "agent_slug": agent_slug,
            "messages": [{"role": "user", "content": "queue integration"}],
            "request_id": request_id,
            "async_mode": True,
        },
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["request_id"] == request_id

    request_response = await test_client.get(
        f"/api/agent/requests/{request_id}",
        headers=admin_headers,
    )
    assert request_response.status_code == 200, request_response.text
    request = request_response.json()["request"]
    assert request["source"] == "agent_call"
    assert request["queue_policy"] == "enqueue"
    assert request["status"] in {"queued", "dispatched"}


async def test_steer_without_active_run_returns_stable_conflict(test_client, admin_headers):
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)

    resp = await test_client.post(
        "/api/agent/runs",
        json={"query": "hi", "agent_slug": agent_slug, "thread_id": thread_id, "queue_policy": "steer", "meta": {}},
        headers=admin_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "steer_target_missing"


async def test_resume_rejects_steer_queue_policy(test_client, admin_headers):
    """Resume 不能绕过仅主 Chat 支持 Steer 的门禁。"""
    response = await test_client.post(
        "/api/agent/runs",
        json={
            "agent_slug": "unused",
            "thread_id": "unused",
            "resume": {"answer": "yes"},
            "queue_policy": "steer",
            "meta": {},
        },
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "queue_policy 仅支持普通 Chat 请求"


async def test_get_request_returns_404_for_missing(test_client, admin_headers):
    resp = await test_client.get(f"/api/agent/requests/{uuid.uuid4()}", headers=admin_headers)
    assert resp.status_code == 404


async def test_list_thread_requests_returns_list(test_client, admin_headers):
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)

    resp = await test_client.get(
        f"/api/agent/thread/{thread_id}/requests",
        params={"agent_slug": agent_slug},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    snapshot = resp.json()
    assert "requests" in snapshot
    assert snapshot["queue"]["status"] == "idle"


async def test_continue_empty_queue_returns_stable_conflict(test_client, admin_headers):
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)

    resp = await test_client.post(
        f"/api/agent/thread/{thread_id}/requests/continue",
        params={"agent_slug": agent_slug},
        headers=admin_headers,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "queue_empty"


async def test_cancel_missing_request_returns_404(test_client, admin_headers):
    resp = await test_client.post(f"/api/agent/requests/{uuid.uuid4()}/cancel", headers=admin_headers)
    assert resp.status_code == 404


async def test_concurrent_requests_maintain_fifo_order(test_client, admin_headers):
    """验证并发创建多个请求时维持FIFO顺序"""
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)

    # 并发创建几个请求
    import asyncio

    request_ids = []

    async def create_request(i):
        resp = await test_client.post(
            "/api/agent/runs",
            json={
                "query": f"message {i}",
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "queue_policy": "enqueue",
                "meta": {},
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        request_ids.append((i, data["request_id"]))

    # 并发创建3个请求
    await asyncio.gather(
        create_request(0),
        create_request(1),
        create_request(2),
    )

    # 获取请求列表验证排序（可能有部分已 dispatched）
    resp = await test_client.get(
        f"/api/agent/thread/{thread_id}/requests",
        params={"agent_slug": agent_slug},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    requests = resp.json()["requests"]

    # 验证返回的请求按创建时间排序
    assert len(requests) >= 1
    for i in range(len(requests) - 1):
        assert requests[i]["created_at"] <= requests[i + 1]["created_at"]


async def test_cancel_queued_request_success(test_client, admin_headers):
    """验证成功取消排队中的请求"""
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)

    # 创建第一个请求（可能立即派发）
    resp = await test_client.post(
        "/api/agent/runs",
        json={
            "query": "test message that hopefully won't finish instantly",
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "queue_policy": "enqueue",
            "meta": {},
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200
    first_data = resp.json()

    # 如果第一个已派发，创建第二个来排队
    if first_data.get("run_id"):
        resp2 = await test_client.post(
            "/api/agent/runs",
            json={
                "query": "second queued message",
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "queue_policy": "enqueue",
                "meta": {},
            },
            headers=admin_headers,
        )
        assert resp2.status_code == 200
        request_data = resp2.json()
    else:
        request_data = first_data

    request_id = request_data["request_id"]

    # 如果请求是 queued 状态，可以取消
    if request_data["status"] == "queued":
        cancel_resp = await test_client.post(
            f"/api/agent/requests/{request_id}/cancel",
            headers=admin_headers,
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"
    else:
        # 已 dispatched，取消应返回 200 或 409
        cancel_resp = await test_client.post(
            f"/api/agent/requests/{request_id}/cancel",
            headers=admin_headers,
        )
        assert cancel_resp.status_code in (200, 409)
