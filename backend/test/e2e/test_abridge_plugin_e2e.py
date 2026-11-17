"""真实设备列表只读链路验证插件加载、worker 与跨 Run 持久激活。"""

import json
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler
from uuid import uuid4

import asyncpg
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from test.e2e.e2e_helpers import (
    archive_public_thread,
    output_text,
    postgres_dsn,
    public_message,
    register_replay_provider,
    wait_for_consumed_input,
    wait_for_terminal_turn,
)
from test.support.openai_replay_server import send_json_response, send_openai_sse_response, serve_replay_handler
from test.support.public_api import delete_agent

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.self_managed_resources, pytest.mark.slow, pytest.mark.timeout(240)]


async def test_abridge_load_opens_readonly_devices_and_restores_next_run(e2e_client, e2e_headers):
    """只调用真实 devices_list，验证插件说明、能力披露和 PostgreSQL 终态。"""
    readiness = await e2e_client.get("/api/devices", headers=e2e_headers)
    if readiness.status_code != 200:
        pytest.fail(f"设备前置检查失败：HTTP {readiness.status_code}，响应正文不输出")
    state = readiness.json().get("state")
    del readiness
    if state == "not_configured":
        pytest.skip("需要已配置的 aBridge 部署；本例不修改部署配置")
    if state == "login_required":
        pytest.skip("需要 E2E 用户已在 aBridge 开户并授权平台连接；本例不修改个人授权")
    if state != "ready":
        pytest.fail("设备前置检查未返回 ready 状态")

    suffix = uuid4().hex[:10]
    provider, agent = f"abridge-replay-{suffix}", f"abridge-agent-{suffix}"
    observed = []
    failures = []

    class Handler(BaseHTTPRequestHandler):
        """只生成加载与只读列表调用，任何失败均不回显设备结果。"""

        def do_POST(self):
            """检查真实模型请求并返回固定、不含设备信息的回复。"""
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                names = {item["function"]["name"] for item in body.get("tools", [])}
                messages = body["messages"]
                last_user = max(
                    i
                    for i, item in enumerate(messages)
                    if item["role"] == "user" and any(marker in str(item["content"]) for marker in ("ABRIDGE_START", "ABRIDGE_RESUME"))
                )
                resumed = "ABRIDGE_RESUME" in str(messages[last_user]["content"])
                phase = "resume" if resumed else "start"
                results = [item for item in messages[last_user + 1 :] if item["role"] == "tool"]
                system = "\n".join(str(item["content"]) for item in messages if item["role"] == "system")
                if not results and not resumed:
                    assert "@plugin:abridge" in str(messages[last_user]["content"])
                    assert "@plugin:<插件ID>" in system
                    assert "load_plugin" in names
                    assert "devices_list" not in names
                    assert "设备控制（aBridge）" not in system
                    observed.append("initial")
                    delta, finish = self.tool_call("load_plugin", {"plugin_id": "abridge"}, "call-load-abridge")
                else:
                    assert "devices_list" in names
                    assert "设备控制（aBridge）" in system
                    assert "沙盒文件系统" in system
                    if results and results[-1]["tool_call_id"] == "call-load-abridge":
                        assert not resumed
                        assert "aBridge 设备操作" in str(results[-1]["content"])
                        assert "chat-widgets" in str(results[-1]["content"])
                        observed.append("loaded")
                        delta, finish = self.tool_call("devices_list", {}, f"call-devices-{phase}")
                    elif resumed and not results:
                        observed.append("restored")
                        delta, finish = self.tool_call("devices_list", {}, f"call-devices-{phase}")
                    else:
                        assert results[-1]["tool_call_id"] == f"call-devices-{phase}"
                        content = results[-1]["content"]
                        if isinstance(content, list):
                            content = "".join(item["text"] for item in content if item.get("type") == "text")
                        payload = json.loads(content)
                        assert isinstance(payload, dict) and isinstance(payload.get("devices"), list)
                        assert not payload.get("isError") and "error" not in payload
                        assert all(isinstance(device, dict) for device in payload["devices"])
                        observed.append(f"executed-{phase}")
                        delta, finish = {"role": "assistant", "content": "ABRIDGE_READONLY_OK"}, "stop"
            except (AssertionError, KeyError, TypeError, ValueError):
                # 仅保留阶段名；不能把真实设备内容、异常值或完整模型请求写入失败报告。
                failures.append("模型能力或只读协议校验失败")
                send_json_response(self, 400, {"error": {"message": "aBridge readonly oracle failed"}})
                return
            common = {"id": "abridge-plugin-test", "object": "chat.completion.chunk", "created": 1, "model": "deterministic-chat"}
            send_openai_sse_response(
                self,
                [
                    {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {
                        **common,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    },
                ],
            )

        def tool_call(self, name, args, call_id):
            """生成固定调用协议，调用方只允许加载插件和读取设备列表。"""
            assert name in {"load_plugin", "devices_list"}
            return {
                "role": "assistant",
                "tool_calls": [{"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
            }, "tool_calls"

        def log_message(self, *_):
            """不记录模型请求、设备结果或响应正文。"""

    with serve_replay_handler(Handler) as replay_url:
        async with AsyncExitStack() as cleanup:
            db = await asyncpg.connect(postgres_dsn())
            cleanup.push_async_callback(db.close)
            cleanup.push_async_callback(e2e_client.delete, f"/api/system/model-providers/{provider}", headers=e2e_headers)
            await register_replay_provider(e2e_client, e2e_headers, base_url=replay_url, provider_id=provider)
            cleanup.push_async_callback(delete_agent, e2e_client, e2e_headers, agent)
            response = await e2e_client.post(
                "/api/agent",
                headers=e2e_headers,
                json={
                    "name": "aBridge readonly plugin validation",
                    "slug": agent,
                    "backend_id": "ChatbotAgent",
                    "config_json": {
                        "context": {
                            "model": f"{provider}:deterministic-chat",
                            "tools": [],
                            "mcps": [],
                            "skills": [],
                            "preload_skills": [],
                            "plugins": ["abridge"],
                        }
                    },
                },
            )
            assert response.status_code == 200, "临时 Agent 创建失败，响应正文不输出"
            response = await e2e_client.post(
                "/api/v1/agents/sessions",
                headers={**e2e_headers, "Idempotency-Key": str(uuid4())},
                json={"agent_id": agent, "title": "pytest-abridge-plugin-readonly"},
            )
            assert response.status_code == 201, "临时 Session 创建失败，响应正文不输出"
            thread = response.json()["id"]
            current_turn = None

            async def cleanup_session():
                """清理时取消最后一轮未完成执行，再归档测试会话。"""
                await archive_public_thread(e2e_client, e2e_headers, thread, turn_id=current_turn)

            cleanup.push_async_callback(cleanup_session)
            for marker, phase in [("@plugin:abridge ABRIDGE_START", "start"), ("ABRIDGE_RESUME", "resume")]:
                response = await e2e_client.post(
                    f"/api/v1/agents/sessions/{thread}/events",
                    headers={**e2e_headers, "Idempotency-Key": str(uuid4())},
                    json={"events": [{"type": "agent.session.input.message", "input": [public_message(marker)]}]},
                )
                assert response.status_code == 202, "测试 Input 提交失败，响应正文不输出"
                consumed = await wait_for_consumed_input(e2e_client, e2e_headers, response.json())
                current_turn = consumed["turn_id"]
                turn = await wait_for_terminal_turn(e2e_client, e2e_headers, thread, current_turn, timeout=60)
                if turn["status"] != "completed" or failures:
                    pytest.fail("aBridge 只读执行失败；检查阶段：" + ",".join(observed))
                if output_text(turn["yuxi"]["output"]) != "ABRIDGE_READONLY_OK":
                    pytest.fail("结果 Run 未返回固定验证标记，实际内容不输出")
                async with await AsyncConnection.connect(postgres_dsn(), autocommit=True, row_factory=dict_row) as connection:
                    saved = await AsyncPostgresSaver(connection).aget_tuple({"configurable": {"thread_id": thread}})
                    if saved is None:
                        pytest.fail("真实 PostgreSQL 中缺少本会话 checkpoint")
                    channels = saved.checkpoint["channel_values"]
                    assert channels["activated_plugins"] == ["abridge"]
                    device_results = [
                        message for message in channels["messages"] if getattr(message, "tool_call_id", None) == f"call-devices-{phase}"
                    ]
                    if len(device_results) != 1 or device_results[0].status != "success":
                        pytest.fail("checkpoint 未保存唯一成功的 devices_list 结果，设备内容不输出")
            runs = await db.fetch("SELECT status, manifest FROM agent_runs WHERE thread_id=$1 ORDER BY created_at", thread)
            assert len(runs) == 2
            for row in runs:
                assert row["status"] == "completed"
                manifest = json.loads(row["manifest"])
                assert [item["id"] for item in manifest["resources"]["plugins"]] == ["abridge"]
                assert manifest["resources"]["mcps"] == []
                assert manifest["resources"]["tools"] == []
            assert observed == ["initial", "loaded", "executed-start", "restored", "executed-resume"]
