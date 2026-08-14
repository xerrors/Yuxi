"""e2e 测试共享的 HTTP 辅助函数。

多个 e2e 文件重复的 agent 清理、取消 run、SSE 消费与 run 状态轮询集中在此，
避免同构 helper 在多份测试文件中漂移。
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

POLL_INTERVAL_SECONDS = float(os.getenv("E2E_RUN_POLL_INTERVAL_SECONDS", "2"))
RUN_TIMEOUT_SECONDS = int(os.getenv("E2E_RUN_TIMEOUT_SECONDS", "240"))


def postgres_dsn() -> str:
    return os.getenv("POSTGRES_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi").replace(
        "+asyncpg", ""
    )


async def delete_agent(client: httpx.AsyncClient, headers: dict[str, str], slug: str) -> None:
    response = await client.delete(f"/api/agent/{slug}", headers=headers)
    assert response.status_code in {200, 404}, response.text


async def cancel_run(client: httpx.AsyncClient, headers: dict[str, str], run_id: str | None) -> None:
    if not run_id:
        return
    response = await client.post(f"/api/agent/runs/{run_id}/cancel", headers=headers)
    assert response.status_code < 500, response.text


async def iter_sse(client: httpx.AsyncClient, headers: dict[str, str], run_id: str):
    """按事件流解析 /api/agent/runs/{run_id}/events，产出 (event, payload)。"""
    async with client.stream("GET", f"/api/agent/runs/{run_id}/events?verbose=false", headers=headers) as response:
        assert response.status_code == 200, response.text
        event = "message"
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if not line:
                if data_lines:
                    yield event, json.loads("\n".join(data_lines))
                event = "message"
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[len("event:") :].strip() or "message"
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())


async def consume_events(client: httpx.AsyncClient, headers: dict[str, str], run_id: str) -> dict[str, int]:
    event_counts: dict[str, int] = {}

    async def consume() -> None:
        async for event, payload in iter_sse(client, headers, run_id):
            event_counts[event] = event_counts.get(event, 0) + 1
            if event == "end" or payload.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
                return

    await asyncio.wait_for(consume(), timeout=RUN_TIMEOUT_SECONDS)
    return event_counts


async def wait_for_run(client: httpx.AsyncClient, headers: dict[str, str], run_id: str) -> dict:
    deadline = asyncio.get_running_loop().time() + RUN_TIMEOUT_SECONDS
    last_payload: dict | None = None

    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/agent/runs/{run_id}", headers=headers)
        assert response.status_code == 200, response.text

        last_payload = response.json().get("run") or {}
        status = str(last_payload.get("status") or "")
        if status in {"completed", "failed", "cancelled", "interrupted"}:
            return last_payload

        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail("Run timed out: " + json.dumps(last_payload or {}, ensure_ascii=False))
