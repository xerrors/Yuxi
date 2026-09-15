"""Integration tests for agent request queue API endpoints."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from test.live_api_cleanup import make_test_conversation_metadata, make_test_conversation_title, make_test_resource_id
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.workspace.paths import user_workdir_host_dir
from yuxi.storage.postgres.models_business import AgentRun, AgentRunRequest, Conversation, Message, Project
from yuxi.utils.datetime_utils import utc_now_naive

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
        json={
            "agent_id": agent_slug,
            "title": make_test_conversation_title("agent-request-queue"),
            "metadata": make_test_conversation_metadata("agent-request-queue"),
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    return payload.get("thread_id") or payload.get("id")


async def _cancel_run_and_wait(test_client, headers, run_id: str) -> dict:
    """取消测试创建的 Run，并等待数据库终态，避免污染恢复测试。"""
    response = await test_client.post(f"/api/agent/runs/{run_id}/cancel", headers=headers)
    assert response.status_code == 200, response.text
    for _ in range(100):
        run_response = await test_client.get(f"/api/agent/runs/{run_id}", headers=headers)
        assert run_response.status_code == 200, run_response.text
        run = run_response.json()["run"]
        if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return run
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


async def test_compress_thread_rejects_active_run(test_client, admin_headers):
    """主动压缩通过真实 HTTP 和 PostgreSQL busy guard 拒绝同线程并发写入。"""
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)
    request_id = f"busy-compress-{uuid.uuid4()}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id))
            assert conversation is not None
            message = Message(
                conversation_id=conversation.id,
                role="user",
                content="busy",
                request_id=request_id,
            )
            db.add(message)
            await db.flush()
            run = await AgentRunRepository(db).create_run(
                run_id=str(uuid.uuid4()),
                conversation_thread_id=thread_id,
                agent_slug=agent_slug,
                uid=conversation.uid,
                request_id=request_id,
                input_payload={"model_spec": "test:model"},
                conversation_id=conversation.id,
                input_message_id=message.id,
            )
            await AgentRunRepository(db).mark_running(
                run.id,
                worker_id="pytest-compression-busy",
                lease_seconds=60,
            )
            await db.commit()

        response = await test_client.post(
            f"/api/chat/thread/{thread_id}/compress",
            json={},
            headers=admin_headers,
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "thread_busy"
    finally:
        async with session_factory() as db:
            run = await db.scalar(select(AgentRun).where(AgentRun.request_id == request_id))
            if run is not None:
                run.status = "cancelled"
                run.finished_at = utc_now_naive()
                run.updated_at = utc_now_naive()
                await db.commit()
        await engine.dispose()


async def test_async_agent_call_uses_request_intake(test_client, admin_headers):
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    request_id = make_test_resource_id("agent-call-queue")

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
    payload = response.json()
    assert payload["request_id"] == request_id
    thread_id = payload["thread_id"]

    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id))
            assert conversation is not None
            project = await db.get(Project, conversation.project_id)
            assert project is not None
            assert user_workdir_host_dir(str(conversation.uid), project.workdir_path).is_dir()
    finally:
        await engine.dispose()

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
        run = await _cancel_run_and_wait(test_client, admin_headers, request["dispatched_run_id"])
        assert run["status"] != "failed" or "invalid_runtime_scope" not in " ".join(
            str(run.get(field) or "") for field in ("error_type", "error_message")
        )
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
                    runtime_scope_id=thread_id,
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

        replay = await test_client.post(
            "/api/agent/runs",
            headers=admin_headers,
            json={
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "query": "changed replay",
                "model_spec": "missing:ignored",
                "queue_policy": "enqueue",
                "meta": {"request_id": queued_request_id},
            },
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["queue_policy"] == "steer"
        assert replay.json()["message_id"] == original_message_id
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


async def test_continue_paused_queue_materializes_and_dispatches(test_client, admin_headers):
    agent_slug = await _get_default_agent_slug(test_client, admin_headers)
    thread_id = await _create_thread(test_client, admin_headers, agent_slug)
    terminal_request_id = make_test_resource_id("continue-terminal")
    queued_request_id = make_test_resource_id("continue-queued")
    terminal_run_id = str(uuid.uuid4())
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.thread_id == thread_id))
            assert conversation is not None
            terminal_message = Message(
                conversation_id=conversation.id,
                request_id=terminal_request_id,
                role="user",
                content="terminal",
                delivery_status="dispatched",
            )
            queued_message = Message(
                conversation_id=conversation.id,
                request_id=queued_request_id,
                role="user",
                content="continue",
                delivery_status="queued",
            )
            db.add_all([terminal_message, queued_message])
            await db.flush()
            now = utc_now_naive()
            db.add_all(
                [
                    AgentRunRequest(
                        request_id=terminal_request_id,
                        uid=conversation.uid,
                        agent_slug=agent_slug,
                        conversation_thread_id=thread_id,
                        source="chat",
                        channel="web",
                        queue_policy="enqueue",
                        status="dispatched",
                        input_message_id=terminal_message.id,
                        input_payload={},
                        created_at=now - timedelta(seconds=2),
                    ),
                    AgentRunRequest(
                        request_id=queued_request_id,
                        uid=conversation.uid,
                        agent_slug=agent_slug,
                        conversation_thread_id=thread_id,
                        source="chat",
                        channel="web",
                        queue_policy="enqueue",
                        status="queued",
                        input_message_id=queued_message.id,
                        input_payload={"model_spec": "test-model", "tool_approval_mode": "auto"},
                        created_at=now - timedelta(seconds=1),
                    ),
                    AgentRun(
                        id=terminal_run_id,
                        conversation_thread_id=thread_id,
                        runtime_scope_id=thread_id,
                        agent_slug=agent_slug,
                        uid=conversation.uid,
                        status="failed",
                        request_id=terminal_request_id,
                        conversation_id=conversation.id,
                        run_type="chat",
                        input_payload={},
                        created_at=now - timedelta(seconds=2),
                        finished_at=now,
                    ),
                ]
            )
            await db.commit()
            uid = str(conversation.uid)
            project = await db.get(Project, conversation.project_id)
            assert project is not None
            workdir_path = project.workdir_path

        workdir_dir = user_workdir_host_dir(uid, workdir_path)
        workdir_dir.rmdir()
        assert not workdir_dir.exists()

        response = await test_client.post(
            f"/api/agent/thread/{thread_id}/requests/continue",
            params={"agent_slug": agent_slug},
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["request_id"] == queued_request_id
        assert user_workdir_host_dir(uid, workdir_path).is_dir()
        await _cancel_run_and_wait(test_client, admin_headers, payload["run_id"])
    finally:
        await engine.dispose()
