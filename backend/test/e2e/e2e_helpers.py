"""e2e 测试共享的 HTTP 辅助函数。

多个 e2e 文件重复的 Agent 清理、Public Thread 归档、SSE 消费与状态轮询集中在此，
避免同构 helper 在多份测试文件中漂移。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest

from test.live_api_cleanup import make_test_session_title
from test.support.postgres import postgres_dsn as postgres_dsn
from test.support.public_api import delete_agent as delete_agent

RUN_TIMEOUT_SECONDS = int(os.getenv("E2E_RUN_TIMEOUT_SECONDS", "240"))
QUOTA_EXHAUSTED_MARKERS = ("Error code: 429", "Token Plan 用量上限")
DETERMINISTIC_OUTPUT = "DETERMINISTIC_AGENT_E2E_OK"
DETERMINISTIC_MODEL = "ci-replay:deterministic-chat"


def output_text(items):
    """从结果 Run 明确绑定的公开 item 读取正文。"""
    return "".join(part["text"] for item in items if item["type"] == "message" for part in item["content"] if part["type"] == "output_text")


def public_message(text: str) -> dict:
    """构建一条 Public 文字消息。"""
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


async def register_replay_provider(
    client: httpx.AsyncClient,
    headers: dict,
    *,
    base_url: str | None = None,
    provider_id: str = "ci-replay",
) -> bool:
    """注册确定性模型重放服务并返回当前测试是否创建了供应商。"""
    response = await client.post(
        "/api/system/model-providers",
        headers=headers,
        json={
            "provider_id": provider_id,
            "display_name": "CI deterministic replay",
            "provider_type": "openai",
            "base_url": base_url or os.environ["OPENAI_REPLAY_URL"],
            "api_key": "ci-replay-key",
            "capabilities": ["chat"],
            "enabled_models": [{"id": "deterministic-chat", "display_name": "Deterministic chat", "type": "chat", "source": "manual"}],
            "is_enabled": True,
        },
    )
    assert response.status_code == 200 or (
        response.status_code == 400 and response.json().get("detail") == f"供应商 {provider_id} 已存在"
    ), response.text

    if response.status_code == 200:
        await wait_model_provider_cache()
        return True
    return False


async def create_lifecycle_agent(
    client: httpx.AsyncClient,
    headers: dict,
    uid: str,
    *,
    tools: list[str] | None = None,
    system_prompt_suffix: str = "",
    slug_prefix: str = "ci-lifecycle-",
    model_spec: str = DETERMINISTIC_MODEL,
    summary_prompt: str | None = None,
    summary_tool_result_token_limit: int | None = None,
    bound_skill: bool = False,
) -> str:
    """创建含预加载技能且只向指定用户共享的测试 Agent。"""
    slug = f"{slug_prefix}{uuid.uuid4().hex[:8]}"
    payload = {
        "name": f"Lifecycle {slug[-8:]}",
        "slug": slug,
        "backend_id": "ChatbotAgent",
        "description": "生命周期 E2E",
        "config_json": {
            "context": {
                "model": model_spec,
                "system_prompt": f"不要调用工具，只输出 {DETERMINISTIC_OUTPUT}。{system_prompt_suffix}",
                "tools": list(dict.fromkeys(["present_artifacts", *(tools or [])])),
                "knowledges": [],
                "mcps": [],
                "skills": [] if bound_skill else ["chat-widgets"],
                "preload_skills": [] if bound_skill else ["chat-widgets"],
                **({"summary_prompt": summary_prompt} if summary_prompt else {}),
                **(
                    {"summary_tool_result_token_limit": summary_tool_result_token_limit}
                    if summary_tool_result_token_limit is not None
                    else {}
                ),
            }
        },
        "visibility": "shared",
        "share_config": {
            "version": 2,
            "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
            "manage_scope": None,
        },
    }
    if bound_skill:
        # 跨用户与 Key 的 fixture 只能依赖 Agent 自己授权的真实 Skill。
        package = BytesIO()
        with ZipFile(package, "w") as archive:
            archive.writestr(
                "SKILL.md",
                "---\nslug: lifecycle-fixture\nname: Lifecycle fixture\ndescription: CI fixture\n---\n\n# 对话组件\n",
            )
        response = await client.post(
            "/api/agent/with-skill",
            headers=headers,
            data={"agent": json.dumps(payload)},
            files={"file": ("lifecycle-fixture.zip", package.getvalue(), "application/zip")},
        )
    else:
        response = await client.post("/api/agent", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return slug


async def wait_for_terminal_turn(client: httpx.AsyncClient, headers: dict, thread_id: str, turn_id: str, *, timeout: float = 30) -> dict:
    """以 Public 持久 Turn 快照等待终态。"""
    last = None
    try:
        async with asyncio.timeout(timeout):
            while True:
                response = await client.get(f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}", headers=headers)
                assert response.status_code == 200, f"Turn 查询失败: thread={thread_id}, turn={turn_id}, HTTP={response.status_code}"
                turn = response.json()
                assert (turn["session_id"], turn["id"]) == (thread_id, turn_id), "Turn 查询返回了其他执行的归属"
                last = {"status": turn["status"], "current_run_id": turn["yuxi"]["current_run_id"]}
                if turn["status"] in {"completed", "failed", "cancelled"}:
                    return turn
                await asyncio.sleep(0.2)
    except TimeoutError:
        pytest.fail(f"Turn 未在 {timeout:g} 秒内终结: thread={thread_id}, turn={turn_id}, last={last}")


async def wait_for_blocked_response(replay: httpx.AsyncClient, token: str, *, timeout: float = 30) -> None:
    """等待本例模型请求进入阻塞点，不把提交成功当作执行已开始。"""
    started = None
    try:
        async with asyncio.timeout(timeout):
            while True:
                response = await replay.get("/blocking-started", params={"token": token})
                assert response.status_code == 200, f"模型阻塞点查询失败: token={token}, HTTP={response.status_code}"
                started = response.json()["started"]
                if started is True:
                    return
                await asyncio.sleep(0.1)
    except TimeoutError:
        pytest.fail(f"模型请求未在 {timeout:g} 秒内进入阻塞点: token={token}, started={started}")


async def create_public_agent(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    uid: str,
    *,
    slug_prefix: str,
    expected_output: str,
    name_prefix: str,
    description: str,
) -> str:
    """创建只输出指定标记的临时 Public Agent。"""
    default_response = await client.get("/api/agent/default", headers=headers)
    assert default_response.status_code == 200, default_response.text
    default_context = ((default_response.json().get("agent") or {}).get("config_json") or {}).get("context") or {}
    slug = f"{slug_prefix}{uuid.uuid4().hex[:8]}"
    context = {
        "system_prompt": f"你是端到端测试专用智能体。不要调用任何工具，只输出 {expected_output}。",
        "tools": [],
        "knowledges": [],
        "mcps": [],
        "skills": [],
    }
    if default_context.get("model"):
        context["model"] = default_context["model"]
    response = await client.post(
        "/api/agent",
        json={
            "name": f"{name_prefix} {slug[-8:]}",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "visibility": "shared",
            "description": description,
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


async def create_public_thread(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    agent_slug: str,
    *,
    tag: str,
) -> str:
    """通过 Public API 创建独立测试 Thread。"""
    response = await client.post(
        "/api/v1/agents/sessions",
        json={"agent_id": agent_slug, "title": make_test_session_title(tag)},
        headers={**headers, "Idempotency-Key": f"{tag}-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    thread_id = response.json()["id"]
    assert thread_id, response.text
    return str(thread_id)


def skip_if_external_quota(payload: object) -> None:
    """真实模型外部配额/限流（HTTP 429）时显式跳过，避免把外部额度当代码回归。

    只匹配明确的 429 措辞；其余失败仍按断言失败处理，不用 skip 掩盖回归。
    """
    text = str(payload or "")
    if any(marker in text for marker in QUOTA_EXHAUSTED_MARKERS):
        pytest.skip(f"外部模型配额/限流（HTTP 429），跳过真实模型断言：{text[:120]}")


async def wait_model_provider_cache() -> None:
    """等待新测试供应商的跨进程缓存传播；不替代模型执行与持久结果验证。"""
    from yuxi.modules.models.providers.cache import _CACHE_TTL_SECONDS

    await asyncio.sleep(_CACHE_TTL_SECONDS + 0.1)


async def cleanup_uploaded_chat_attachment(uid: str, file_id: str) -> None:
    """删除测试上传附件的临时对象与 workspace 副本。"""
    from yuxi.infrastructure.minio import get_minio_client
    from yuxi.modules.workspace.filesystem import Workspace

    minio = get_minio_client()
    prefix = f"tmp/chat_attachments/{uid}/{file_id}"
    await minio.adelete_objects_by_prefix(minio.KB_BUCKETS["documents"], prefix + "/")
    try:
        Workspace(uid).delete_authorized_path("/" + prefix, root="/")
    except FileNotFoundError:
        pass


async def wait_for_consumed_input(client: httpx.AsyncClient, headers: dict, receipt: dict, *, timeout: float = 60) -> dict:
    """校验接收回执不带执行归属，再从真实 Input 查询领取结果。"""
    assert receipt["input_id"] and receipt["turn_id"] is None and receipt["run_id"] is None, receipt
    session_id = receipt["session_id"]
    last = None
    try:
        async with asyncio.timeout(timeout):
            while True:
                response = await client.get(f"/api/v1/agents/sessions/{session_id}/inputs/{receipt['input_id']}", headers=headers)
                assert response.status_code == 200, (
                    f"Input 查询失败: thread={session_id}, input={receipt['input_id']}, HTTP={response.status_code}"
                )
                snapshot = response.json()
                assert (snapshot["thread_id"], snapshot["input_id"]) == (session_id, receipt["input_id"]), "Input 查询返回了其他提交的归属"
                last = {key: snapshot[key] for key in ("status", "turn_id", "run_id")}
                assert snapshot["status"] != "cancelled", f"待消费 Input 被取消: {last}"
                if snapshot["status"] == "consumed":
                    assert snapshot["run_id"] and snapshot["turn_id"] and snapshot["items"], f"消费投影不完整: {last}"
                    return {**snapshot, "session_id": session_id}
                await asyncio.sleep(0.1)
    except TimeoutError:
        pytest.fail(f"Input 未在 {timeout:g} 秒内消费: thread={session_id}, input={receipt['input_id']}, last={last}")


async def iter_public_thread_events(client: httpx.AsyncClient, headers: dict[str, str], thread_id: str):
    """解析 Public Thread SSE 的结构化事件。"""
    async with client.stream("GET", f"/api/v1/agents/sessions/{thread_id}/events", headers=headers) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                yield json.loads(line[6:])


async def archive_public_thread(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    thread_id: str,
    *,
    turn_id: str | None = None,
) -> None:
    """取消仍活跃的测试 Turn，等待收敛后归档 Thread。"""
    if turn_id:
        turn_url = f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}"
        try:
            async with asyncio.timeout(RUN_TIMEOUT_SECONDS):
                snapshot = await client.get(turn_url, headers=headers)
                assert snapshot.status_code == 200, snapshot.text
                if snapshot.json()["status"] not in {"completed", "failed", "cancelled"}:
                    cancel = await client.post(
                        f"/api/v1/agents/sessions/{thread_id}/events",
                        headers={**headers, "Idempotency-Key": f"e2e-cleanup-{turn_id}"},
                        json={"events": [{"type": "agent.session.input.cancel", "yuxi": {"turn_id": turn_id}}]},
                    )
                    assert cancel.status_code in {202, 409}, cancel.text
                    while True:
                        snapshot = await client.get(turn_url, headers=headers)
                        assert snapshot.status_code == 200, snapshot.text
                        if snapshot.json()["status"] in {"completed", "failed", "cancelled"}:
                            break
                        await asyncio.sleep(1)
        except TimeoutError:
            pytest.fail(f"测试 Turn 取消后未收敛: {turn_id}")
    async with asyncio.timeout(30):
        while True:
            archive = await client.post(f"/api/v1/agents/sessions/{thread_id}/archive", headers=headers)
            if archive.status_code != 409 or archive.json().get("detail") != "会话范围内仍有活跃执行或待处理输入，不能归档":
                assert archive.status_code == 200, archive.text
                return
            await asyncio.sleep(0.2)
