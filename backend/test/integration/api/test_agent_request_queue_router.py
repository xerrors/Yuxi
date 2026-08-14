"""Integration tests for agent request queue API endpoints."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.storage.postgres.models_business import AgentRun, AgentRunRequest, Conversation, Message

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


async def _cancel_run_and_wait(test_client, headers, run_id: str) -> None:
    """取消测试创建的 Run，并等待数据库终态，避免污染恢复测试。"""
    response = await test_client.post(f"/api/agent/runs/{run_id}/cancel", headers=headers)
    assert response.status_code == 200, response.text
    for _ in range(100):
        run_response = await test_client.get(f"/api/agent/runs/{run_id}", headers=headers)
        assert run_response.status_code == 200, run_response.text
        if run_response.json()["run"]["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return
        await asyncio.sleep(0.1)
    pytest.fail(f"Run {run_id} did not reach a terminal status after cancellation")


@pytest.mark.parametrize("queue_policy", ["enqueue", "steer"])
async def test_create_run_returns_request_info(test_client, admin_headers, queue_policy):
    """创建 run 时返回 request 信息，enqueue 与 steer 共用同一 intake 流程。"""
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)

    resp = await test_client.post(
        "/api/agent/runs",
        json={
            "query": "hello",
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "queue_policy": queue_policy,
            "meta": {},
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "request_id" in data
    assert data["queue_policy"] == queue_policy
    assert data["status"] in ("dispatched", "queued")
    if data.get("run_id"):
        await _cancel_run_and_wait(test_client, admin_headers, data["run_id"])


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
    if request.get("dispatched_run_id"):
        await _cancel_run_and_wait(test_client, admin_headers, request["dispatched_run_id"])
    elif request["status"] == "queued":
        cancel_response = await test_client.post(
            f"/api/agent/requests/{request_id}/cancel",
            headers=admin_headers,
        )
        assert cancel_response.status_code == 200, cancel_response.text


async def test_resume_rejects_steer_policy(test_client, admin_headers):
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)

    response = await test_client.post(
        "/api/agent/runs",
        json={
            "query": None,
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "queue_policy": "steer",
            "resume": {"answer": "ok"},
            "meta": {},
        },
        headers=admin_headers,
    )

    assert response.status_code == 422


async def test_upgrade_queued_chat_request_to_steer(test_client, admin_headers, standard_user):
    """升级接口保持原请求事实，并隐藏其他用户的请求。"""
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)
    active_request_id = f"active-{uuid.uuid4()}"
    queued_request_id = f"queued-{uuid.uuid4()}"
    active_run_id = f"run-{uuid.uuid4()}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        conversation = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id))
        assert conversation is not None
        conversation_id = conversation.id
        active_message = Message(
            conversation_id=conversation.id,
            request_id=active_request_id,
            role="user",
            content="active",
            delivery_status="dispatched",
        )
        queued_message = Message(
            conversation_id=conversation.id,
            request_id=queued_request_id,
            role="user",
            content="queued",
            delivery_status="queued",
        )
        db.add_all([active_message, queued_message])
        await db.flush()
        db.add_all(
            [
                AgentRunRequest(
                    request_id=active_request_id,
                    uid=conversation.uid,
                    agent_slug=agent_slug,
                    conversation_thread_id=thread_id,
                    source="chat",
                    queue_policy="enqueue",
                    status="dispatched",
                    input_message_id=active_message.id,
                    input_payload={},
                ),
                AgentRunRequest(
                    request_id=queued_request_id,
                    uid=conversation.uid,
                    agent_slug=agent_slug,
                    conversation_thread_id=thread_id,
                    source="chat",
                    queue_policy="enqueue",
                    status="queued",
                    input_message_id=queued_message.id,
                    input_payload={"model_spec": "test-model"},
                ),
                AgentRun(
                    id=active_run_id,
                    conversation_thread_id=thread_id,
                    agent_slug=agent_slug,
                    uid=conversation.uid,
                    status="running",
                    request_id=active_request_id,
                    conversation_id=conversation.id,
                    run_type="chat",
                    input_payload={},
                ),
            ]
        )
        await db.commit()
        original_message_id = queued_message.id
        original_created_at = await db.scalar(
            select(AgentRunRequest.created_at).where(AgentRunRequest.request_id == queued_request_id)
        )

    try:
        hidden_response = await test_client.post(
            f"/api/agent/requests/{queued_request_id}/steer",
            headers=standard_user["headers"],
        )
        assert hidden_response.status_code == 404

        response = await test_client.post(
            f"/api/agent/requests/{queued_request_id}/steer",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["queue_policy"] == "steer"
        assert response.json()["status"] == "queued"
        assert response.json()["queue_position"] == 1

        async with session_factory() as db:
            request = await db.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == queued_request_id))
            assert request is not None
            assert request.input_message_id == original_message_id
            assert request.created_at == original_created_at
            assert request.input_payload == {"model_spec": "test-model"}
    finally:
        async with session_factory() as db:
            await db.execute(delete(AgentRunRequest).where(AgentRunRequest.conversation_thread_id == thread_id))
            await db.execute(delete(AgentRun).where(AgentRun.conversation_thread_id == thread_id))
            await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
            await db.commit()
        await engine.dispose()


@pytest.mark.parametrize(
    ("method", "path_template"),
    [
        ("get", "/api/agent/requests/{request_id}"),
        ("post", "/api/agent/requests/{request_id}/cancel"),
    ],
)
async def test_missing_request_returns_404(test_client, admin_headers, method, path_template):
    """不存在的请求在查询与取消接口上都应返回 404。"""
    url = path_template.format(request_id=uuid.uuid4())
    resp = await getattr(test_client, method)(url, headers=admin_headers)
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

