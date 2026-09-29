"""真实 HTTP 与 PostgreSQL 下按请求读取 Agent 结果。"""

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_request_result_follows_only_bound_run(test_client, admin_headers, standard_user):
    """同线程相邻 Run 的结果不能代替尚在排队的请求结果。"""
    agents = await test_client.get("/api/agent", headers=admin_headers)
    assert agents.status_code == 200, agents.text
    agent = agents.json()["agents"][0]
    agent_slug = agent.get("agent_id") or agent["slug"]
    created = await test_client.post(
        "/api/chat/thread",
        json={"agent_id": agent_slug, "title": "pytest-request-result"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    thread_id = created.json().get("thread_id") or created.json()["id"]
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    uid = str(me.json()["uid"])
    first_request_id, second_request_id = (str(uuid.uuid4()), f"req/?#{uuid.uuid4()}")
    first_run_id, second_run_id = (str(uuid.uuid4()), str(uuid.uuid4()))
    dsn = os.getenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi").replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        conversation_id = await conn.fetchval("SELECT id FROM conversations WHERE thread_id = $1", thread_id)
        assert conversation_id

        async def add_run(request_id: str, run_id: str, output: str, status: str):
            await conn.execute(
                "INSERT INTO agent_runs "
                "(id, conversation_thread_id, runtime_scope_id, agent_slug, uid, request_id, "
                "status, run_type, source, channel, input_payload, token_usage, origin_metadata, conversation_id, "
                "worker_id, heartbeat_at, lease_expires_at) "
                "VALUES ($1, $2, $2, $3, $4, $5, $7, 'chat', 'chat', 'web', '{}'::jsonb, "
                "'{}'::jsonb, '{}'::jsonb, $6, 'integration-test', NOW(), NOW() + INTERVAL '1 hour')",
                run_id,
                thread_id,
                agent_slug,
                uid,
                request_id,
                conversation_id,
                status,
            )
            output_id = await conn.fetchval(
                "INSERT INTO messages (conversation_id, role, content, run_id, request_id, delivery_status) "
                "VALUES ($1, 'assistant', $2, $3, $4, 'complete') RETURNING id",
                conversation_id,
                output,
                run_id,
                request_id,
            )
            await conn.execute("UPDATE agent_runs SET output_message_id = $2 WHERE id = $1", run_id, output_id)
            await conn.execute(
                "UPDATE agent_run_requests SET status = 'dispatched', dispatched_run_id = $2 WHERE request_id = $1",
                request_id,
                run_id,
            )

        async with conn.transaction():
            for request_id, content in ((first_request_id, "first input"), (second_request_id, "second input")):
                message_id = await conn.fetchval(
                    "INSERT INTO messages (conversation_id, role, content, request_id, delivery_status) "
                    "VALUES ($1, 'user', $2, $3, 'queued') RETURNING id",
                    conversation_id,
                    content,
                    request_id,
                )
                await conn.execute(
                    "INSERT INTO agent_run_requests "
                    "(request_id, uid, agent_slug, conversation_thread_id, input_message_id, status, "
                    "source, channel, origin_metadata, input_payload, queue_policy, created_at, updated_at) "
                    "VALUES ($1, $2, $3, $4, $5, 'queued', 'chat', 'web', '{}'::jsonb, '{}'::jsonb, "
                    "'enqueue', NOW(), NOW())",
                    request_id,
                    uid,
                    agent_slug,
                    thread_id,
                    message_id,
                )
            await add_run(first_request_id, first_run_id, "first output", "running")
        queued = await test_client.get(
            "/api/agent/request-result", params={"request_id": second_request_id}, headers=admin_headers
        )
        assert queued.status_code == 200, queued.text
        assert queued.json()["run_id"] is None
        assert queued.json()["output"] is None
        assert queued.json()["usage"] is None
        assert queued.json()["status"] == "queued"

        denied = await test_client.get(
            "/api/agent/request-result",
            params={"request_id": second_request_id},
            headers=standard_user["headers"],
        )
        assert denied.status_code == 404

        async with conn.transaction():
            await conn.execute("UPDATE agent_runs SET status = 'completed' WHERE id = $1", first_run_id)
            await add_run(second_request_id, second_run_id, "second output", "completed")
        result = await test_client.get(
            "/api/agent/request-result", params={"request_id": second_request_id}, headers=admin_headers
        )
        assert result.status_code == 200, result.text
        assert (result.json()["request_id"], result.json()["run_id"], result.json()["output"]) == (
            second_request_id,
            second_run_id,
            "second output",
        )
        assert result.json()["usage"] is None
    finally:
        await conn.execute(
            "DELETE FROM agent_run_requests WHERE request_id = ANY($1::text[])",
            [first_request_id, second_request_id],
        )
        await conn.execute(
            "DELETE FROM messages WHERE request_id = ANY($1::text[])",
            [first_request_id, second_request_id],
        )
        await conn.execute("DELETE FROM agent_runs WHERE id = ANY($1::text[])", [first_run_id, second_run_id])
        await conn.close()
        await test_client.delete(f"/api/chat/thread/{thread_id}", headers=admin_headers)
