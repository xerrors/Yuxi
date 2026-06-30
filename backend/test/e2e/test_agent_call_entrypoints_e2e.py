from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

import asyncpg
import httpx
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]

POLL_INTERVAL_SECONDS = float(os.getenv("E2E_RUN_POLL_INTERVAL_SECONDS", "2"))
RUN_TIMEOUT_SECONDS = int(os.getenv("E2E_RUN_TIMEOUT_SECONDS", "240"))
EVAL_EXPECTED_OUTPUT = "AGENT_EVAL_E2E_OK"
CALL_EXPECTED_OUTPUT = "AGENT_CALL_E2E_OK"


def _postgres_dsn() -> str:
    return os.getenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi").replace(
        "+asyncpg", ""
    )


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        assert isinstance(parsed, dict), parsed
        return parsed
    return {}


async def _create_agent(client: httpx.AsyncClient, headers: dict[str, str], uid: str) -> str:
    default_response = await client.get("/api/agent/default", headers=headers)
    assert default_response.status_code == 200, default_response.text
    default_context = ((default_response.json().get("agent") or {}).get("config_json") or {}).get("context") or {}

    slug = f"e2e-agent-call-{uuid.uuid4().hex[:8]}"
    context: dict[str, Any] = {
        "system_prompt": (
            "你是端到端测试专用智能体。不要调用任何工具。如果用户要求输出一个 AGENT_*_E2E_OK 标记，只输出该标记本身。"
        ),
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
            "name": f"E2E Agent Call {slug[-8:]}",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "description": "真实 Agent Call/Eval E2E 临时智能体",
            "config_json": {"context": context},
            "share_config": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert (response.json().get("agent") or {}).get("slug") == slug
    return slug


async def _delete_agent(client: httpx.AsyncClient, headers: dict[str, str], slug: str) -> None:
    response = await client.delete(f"/api/agent/{slug}", headers=headers)
    assert response.status_code in {200, 404}, response.text


async def _cancel_run(client: httpx.AsyncClient, headers: dict[str, str], run_id: str | None) -> None:
    if not run_id:
        return
    response = await client.post(f"/api/agent/runs/{run_id}/cancel", headers=headers)
    assert response.status_code < 500, response.text


async def _wait_agent_call_result(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    run_id: str,
    agent_slug: str,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + RUN_TIMEOUT_SECONDS
    last_payload: dict[str, Any] | None = None

    while asyncio.get_running_loop().time() < deadline:
        response = await client.post(
            "/api/agent-invocation/agent-call/runs/result",
            json={"run_id": run_id, "agent_slug": agent_slug},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        last_payload = response.json()
        if last_payload.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
            return last_payload
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail("Agent Call run timed out: " + json.dumps(last_payload or {}, ensure_ascii=False))


async def _load_run_metadata(run_id: str) -> dict[str, Any]:
    conn = await asyncpg.connect(_postgres_dsn())
    try:
        row = await conn.fetchrow(
            """
            SELECT
                ar.id,
                ar.request_id,
                ar.status,
                ar.run_type,
                ar.agent_slug,
                conv.extra_metadata AS conversation_metadata,
                input_msg.extra_metadata AS input_metadata,
                input_msg.content AS input_content
            FROM agent_runs ar
            JOIN conversations conv ON conv.id = ar.conversation_id
            LEFT JOIN messages input_msg ON input_msg.id = ar.input_message_id
            WHERE ar.id = $1
            """,
            run_id,
        )
        assert row, f"agent run row missing for {run_id}"
        return {
            "request_id": row["request_id"],
            "status": row["status"],
            "run_type": row["run_type"],
            "agent_slug": row["agent_slug"],
            "conversation_metadata": _json_dict(row["conversation_metadata"]),
            "input_metadata": _json_dict(row["input_metadata"]),
            "input_content": row["input_content"],
        }
    finally:
        await conn.close()


async def test_agent_eval_and_agent_call_entrypoints_share_run_invocation_flow(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
):
    uid = e2e_agent_context["uid"]
    agent_slug = await _create_agent(e2e_client, e2e_headers, uid)
    agent_call_run_id: str | None = None
    agent_call_completed = False

    try:
        eval_request_id = f"agent-eval-e2e-{uuid.uuid4()}"
        eval_metadata = {
            "dataset_name": "agent-entrypoint-e2e",
            "dataset_item_id": f"item-{uuid.uuid4().hex[:8]}",
            "experiment_name": "agent-entrypoint-e2e",
        }
        eval_response = await e2e_client.post(
            "/api/agent-invocation/eval/runs",
            json={
                "query": f"请只输出 {EVAL_EXPECTED_OUTPUT}，不要添加任何解释。",
                "agent_slug": agent_slug,
                "evaluation": eval_metadata,
                "meta": {"request_id": eval_request_id},
            },
            headers=e2e_headers,
        )
        assert eval_response.status_code == 200, eval_response.text
        eval_payload = eval_response.json()
        assert eval_payload.get("status") == "completed", eval_payload
        assert eval_payload.get("request_id") == eval_request_id
        assert EVAL_EXPECTED_OUTPUT in str(eval_payload.get("output") or ""), eval_payload

        eval_run_id = eval_payload.get("agent_run_id")
        assert eval_run_id, eval_payload
        eval_run = await _load_run_metadata(str(eval_run_id))
        assert eval_run["status"] == "completed"
        assert eval_run["run_type"] == "chat"
        assert eval_run["conversation_metadata"]["source"] == "agent_evaluation"
        assert eval_run["conversation_metadata"]["agent_invocation_meta"] == {"evaluation": eval_metadata}
        assert eval_run["input_metadata"]["source"] == "agent_evaluation"
        assert eval_run["input_metadata"]["agent_invocation_meta"] == {"evaluation": eval_metadata}
        assert "evaluation" not in eval_run["input_metadata"]

        agent_call_request_id = f"agent-call-e2e-{uuid.uuid4()}"
        create_response = await e2e_client.post(
            "/api/agent-invocation/agent-call/runs",
            json={
                "agent_slug": agent_slug,
                "messages": [{"role": "user", "content": f"请只输出 {CALL_EXPECTED_OUTPUT}，不要添加任何解释。"}],
                "request_id": agent_call_request_id,
                "async_mode": True,
            },
            headers=e2e_headers,
        )
        assert create_response.status_code == 200, create_response.text
        create_payload = create_response.json()
        agent_call_run_id = create_payload.get("run_id")
        assert agent_call_run_id, create_payload
        assert create_payload.get("request_id") == agent_call_request_id
        assert create_payload.get("status") == "pending"

        call_payload = await _wait_agent_call_result(
            e2e_client,
            e2e_headers,
            run_id=str(agent_call_run_id),
            agent_slug=agent_slug,
        )
        assert call_payload.get("status") == "completed", call_payload
        assert call_payload.get("request_id") == agent_call_request_id
        assert CALL_EXPECTED_OUTPUT in str(call_payload.get("output") or ""), call_payload
        assert call_payload["choices"][0]["messages"] == [
            {"role": "assistant", "content": call_payload.get("output") or ""}
        ]
        assert call_payload["choices"][0]["finish_reason"] == "stop"
        agent_call_completed = True

        agent_call_run = await _load_run_metadata(str(agent_call_run_id))
        assert agent_call_run["status"] == "completed"
        assert agent_call_run["run_type"] == "chat"
        assert agent_call_run["conversation_metadata"]["source"] == "agent_call"
        assert "agent_invocation_meta" not in agent_call_run["conversation_metadata"]
        assert agent_call_run["input_metadata"]["source"] == "agent_call"
        assert "agent_invocation_meta" not in agent_call_run["input_metadata"]
        assert "custom_variables" not in agent_call_run["input_metadata"]
    finally:
        if not agent_call_completed:
            await _cancel_run(e2e_client, e2e_headers, agent_call_run_id)
        await _delete_agent(e2e_client, e2e_headers, agent_slug)
