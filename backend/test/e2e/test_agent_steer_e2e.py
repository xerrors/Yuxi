"""真实模型与 execute 工具的主会话 Steer E2E。"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


async def _create_thread(client: httpx.AsyncClient, headers: dict[str, str], agent_slug: str) -> str:
    """创建本次 E2E 独占线程。"""
    response = await client.post(
        "/api/chat/thread",
        json={
            "agent_id": agent_slug,
            "title": f"agent-steer-e2e-{uuid.uuid4().hex[:8]}",
            "metadata": {"test": "agent-steer-e2e"},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return str(payload.get("thread_id") or payload.get("id"))


async def _create_steer_agent(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    uid: str,
) -> str:
    """创建只开放沙盒基础能力的临时真实模型 Agent。"""
    slug = f"e2e-steer-agent-{uuid.uuid4().hex[:8]}"
    context: dict[str, Any] = {
        "system_prompt": (
            "你是 Steer 端到端测试智能体。用户消息以 SLOW_TOOL 开头时，必须立即且仅调用一次 execute，"
            "command 必须是 `sleep 12 && echo TOOL_FINISHED`；工具结束后原任务本应回答 OLD_SHOULD_NOT_COMPLETE。"
            "用户消息以 STEER 开头时，禁止调用工具；如果上下文中能看到工具结果 TOOL_FINISHED，"
            "只回答 STEER_COMPLETE TOOL_CONTEXT_OK，否则只回答 STEER_COMPLETE TOOL_CONTEXT_MISSING。"
        ),
        "tools": [],
        "knowledges": [],
        "mcps": [],
        "skills": [],
        "subagents": [],
        "tool_approval_mode": "always_trust",
    }
    response = await client.post(
        "/api/agent",
        json={
            "name": f"E2E Steer Agent {slug[-8:]}",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "description": "真实 Steer E2E 临时智能体",
            "config_json": {"context": context},
            "share_config": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return slug


async def _watch_run_until_end(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    run_id: str,
    tool_started: asyncio.Event,
) -> list[str]:
    """消费真实 Run SSE，并在 execute tool call 出现时通知测试主协程。"""
    data_lines: list[str] = []
    async with client.stream("GET", f"/api/agent/runs/{run_id}/events", headers=headers) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            data_lines.append(payload)
            if "execute" in payload and "sleep 12" in payload:
                tool_started.set()
    return data_lines


async def _wait_request_dispatched(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    request_id: str,
) -> dict:
    """等待 Steer request 派发，返回持久化 request 事实。"""
    for _ in range(180):
        response = await client.get(f"/api/agent/requests/{request_id}", headers=headers)
        assert response.status_code == 200, response.text
        request = response.json()["request"]
        if request["status"] == "dispatched":
            return request
        if request["status"] in {"failed", "cancelled", "rejected"}:
            pytest.fail(f"Steer request terminated before dispatch: {request}")
        await asyncio.sleep(1)
    pytest.fail("Steer request was not dispatched within 180 seconds")


async def _wait_run_terminal(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    run_id: str,
) -> dict:
    """等待 Run 进入数据库终态。"""
    for _ in range(180):
        response = await client.get(f"/api/agent/runs/{run_id}", headers=headers)
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return run
        await asyncio.sleep(1)
    pytest.fail(f"Run {run_id} was not terminal within 180 seconds")


async def test_real_tool_steer_handoff(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
):
    """工具不中断，安全点后旧 Run 终结且 replacement 完成。"""
    uid = e2e_agent_context["uid"]
    agent_slug = await _create_steer_agent(e2e_client, e2e_headers, uid)
    thread_id = await _create_thread(e2e_client, e2e_headers, agent_slug)

    try:
        initial_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": "SLOW_TOOL：执行慢工具",
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "queue_policy": "enqueue",
                "tool_approval_mode": "always_trust",
                "meta": {"request_id": f"e2e-target-{uuid.uuid4()}"},
            },
            headers=e2e_headers,
        )
        assert initial_response.status_code == 200, initial_response.text
        target_run_id = initial_response.json()["run_id"]
        assert target_run_id

        tool_started = asyncio.Event()
        target_stream_task = asyncio.create_task(
            _watch_run_until_end(e2e_client, e2e_headers, target_run_id, tool_started)
        )
        await asyncio.wait_for(tool_started.wait(), timeout=120)

        steer_request_id = f"e2e-steer-{uuid.uuid4()}"
        steer_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": "STEER：改为直接确认引导成功",
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "queue_policy": "steer",
                "tool_approval_mode": "always_trust",
                "meta": {"request_id": steer_request_id},
            },
            headers=e2e_headers,
        )
        assert steer_response.status_code == 200, steer_response.text
        assert steer_response.json()["target_run_id"] == target_run_id

        request = await _wait_request_dispatched(e2e_client, e2e_headers, steer_request_id)
        target_run = await _wait_run_terminal(e2e_client, e2e_headers, target_run_id)
        replacement_run = await _wait_run_terminal(
            e2e_client,
            e2e_headers,
            request["dispatched_run_id"],
        )
        target_events = await asyncio.wait_for(target_stream_task, timeout=30)

        assert target_run["status"] == "cancelled"
        assert target_run["error_type"] == "steered"
        assert replacement_run["status"] == "completed"
        assert "TOOL_FINISHED" in "\n".join(target_events)
        assert "OLD_SHOULD_NOT_COMPLETE" not in "\n".join(target_events)

        history_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/history", headers=e2e_headers)
        assert history_response.status_code == 200, history_response.text
        history_text = json.dumps(history_response.json(), ensure_ascii=False)
        assert "STEER_COMPLETE" in history_text
        assert "TOOL_CONTEXT_OK" in history_text
        assert "TOOL_CONTEXT_MISSING" not in history_text
        assert "STEER：改为直接确认引导成功" in history_text
    finally:
        await e2e_client.delete(f"/api/agent/{agent_slug}", headers=e2e_headers)
