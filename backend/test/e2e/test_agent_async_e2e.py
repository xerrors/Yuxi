from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg
import httpx
import pytest

from e2e_helpers import cancel_run, consume_events, delete_agent, postgres_dsn, wait_for_run

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]

EXPECTED_OUTPUT = "ASYNC_AGENT_E2E_OK"


async def _create_agent(client: httpx.AsyncClient, headers: dict[str, str], uid: str) -> str:
    default_response = await client.get("/api/agent/default", headers=headers)
    assert default_response.status_code == 200, default_response.text
    default_context = ((default_response.json().get("agent") or {}).get("config_json") or {}).get("context") or {}

    slug = f"e2e-async-agent-{uuid.uuid4().hex[:8]}"
    context: dict[str, Any] = {
        "system_prompt": f"你是端到端测试专用智能体。不要调用任何工具，只输出 {EXPECTED_OUTPUT}。",
        "tools": [],
        "knowledges": [],
        "mcps": [],
        "skills": [],
        "subagents": [],
    }
    if default_context.get("model"):
        context["model"] = default_context["model"]

    response = await client.post(
        "/api/agent",
        json={
            "name": f"E2E 异步 Agent {slug[-8:]}",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "description": "真实异步 Agent E2E 临时智能体",
            "config_json": {"context": context},
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
                "manage_scope": None,
            },
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert (response.json().get("agent") or {}).get("slug") == slug
    return slug


async def _create_thread(client: httpx.AsyncClient, headers: dict[str, str], agent_slug: str) -> str:
    response = await client.post(
        "/api/chat/thread",
        json={
            "agent_id": agent_slug,
            "title": f"agent-async-e2e-{uuid.uuid4().hex[:8]}",
            "metadata": {"_yuxi_e2e": True, "test": "agent-async-e2e"},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    thread_id = payload.get("thread_id") or payload.get("id")
    assert thread_id, payload
    return str(thread_id)


async def _create_run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    agent_slug: str,
    thread_id: str,
) -> tuple[str, str]:
    request_id = f"agent-async-e2e-{uuid.uuid4()}"
    response = await client.post(
        "/api/agent/runs",
        json={
            "query": f"请只回复 {EXPECTED_OUTPUT}，不要添加任何解释。",
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "meta": {"request_id": request_id},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    run_id = response.json().get("run_id")
    assert run_id, response.text
    assert response.json().get("stream_url") == f"/api/agent/runs/{run_id}/events"
    assert response.json().get("request_id") == request_id
    return str(run_id), request_id


async def _assert_run_persisted(
    *,
    run_id: str,
    request_id: str,
    thread_id: str,
    agent_slug: str,
    uid: str,
) -> None:
    conn = await asyncpg.connect(postgres_dsn())
    try:
        row = await conn.fetchrow(
            """
            SELECT
                ar.id,
                ar.request_id,
                ar.status,
                ar.run_type,
                ar.agent_slug,
                ar.uid,
                ar.conversation_thread_id,
                ar.conversation_id,
                ar.input_message_id,
                ar.output_message_id,
                input_msg.role AS input_role,
                input_msg.request_id AS input_request_id,
                output_msg.role AS output_role,
                output_msg.run_id AS output_run_id,
                output_msg.content AS output_content,
                conv.thread_id AS persisted_thread_id
            FROM agent_runs ar
            JOIN conversations conv ON conv.id = ar.conversation_id
            LEFT JOIN messages input_msg ON input_msg.id = ar.input_message_id
            LEFT JOIN messages output_msg ON output_msg.id = ar.output_message_id
            WHERE ar.id = $1
            """,
            run_id,
        )
        assert row, f"agent_runs row missing for {run_id}"
        assert row["request_id"] == request_id
        assert row["status"] == "completed"
        assert row["run_type"] == "chat"
        assert row["agent_slug"] == agent_slug
        assert row["uid"] == uid
        assert row["conversation_thread_id"] == thread_id
        assert row["conversation_id"] is not None
        assert row["input_message_id"] is not None
        assert row["output_message_id"] is not None
        assert row["input_role"] == "user"
        assert row["input_request_id"] == request_id
        assert row["output_role"] == "assistant"
        assert row["output_run_id"] == run_id
        assert EXPECTED_OUTPUT in row["output_content"]
        assert row["persisted_thread_id"] == thread_id
    finally:
        await conn.close()


async def test_async_agent_run_stream_result_and_persistence(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
):
    uid = e2e_agent_context["uid"]
    agent_slug = await _create_agent(e2e_client, e2e_headers, uid)
    run_id: str | None = None
    run_completed = False

    try:
        thread_id = await _create_thread(e2e_client, e2e_headers, agent_slug)
        run_id, request_id = await _create_run(
            e2e_client,
            e2e_headers,
            agent_slug=agent_slug,
            thread_id=thread_id,
        )

        event_counts = await consume_events(e2e_client, e2e_headers, run_id)
        assert event_counts.get("messages", 0) > 0, event_counts
        assert event_counts.get("end", 0) == 1, event_counts

        run_payload = await wait_for_run(e2e_client, e2e_headers, run_id)
        assert run_payload.get("status") == "completed", run_payload
        assert run_payload.get("request_id") == request_id

        result_response = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=e2e_headers)
        assert result_response.status_code == 200, result_response.text
        result_payload = result_response.json()
        assert result_payload.get("status") == "completed", result_payload
        assert result_payload.get("agent_run_id") == run_id
        assert result_payload.get("thread_id") == thread_id
        assert result_payload.get("request_id") == request_id
        assert EXPECTED_OUTPUT in str(result_payload.get("output") or ""), result_payload

        history_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/history", headers=e2e_headers)
        assert history_response.status_code == 200, history_response.text
        history_text = json.dumps(history_response.json(), ensure_ascii=False)
        assert request_id in history_text, history_text
        assert EXPECTED_OUTPUT in history_text, history_text

        await _assert_run_persisted(
            run_id=run_id,
            request_id=request_id,
            thread_id=thread_id,
            agent_slug=agent_slug,
            uid=uid,
        )
        run_completed = True
    finally:
        if not run_completed:
            await cancel_run(e2e_client, e2e_headers, run_id)
        await delete_agent(e2e_client, e2e_headers, agent_slug)
