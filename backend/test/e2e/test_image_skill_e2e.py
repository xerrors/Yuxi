"""真实 HTTP、worker 验证图像说明作为普通 Skill 读取，不激活插件。"""

import json
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

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.self_managed_resources, pytest.mark.slow, pytest.mark.timeout(180)]


@pytest.mark.parametrize("direct_tool", [False, True])
async def test_image_skill_preserves_direct_tool_selection_without_activation(e2e_client, e2e_headers, direct_tool):
    """读取图像说明不改变能力，后续 Run 仍由直接配置控制通用工具。"""
    suffix = uuid4().hex[:10]
    provider, agent = f"plugin-replay-{suffix}", f"plugin-agent-{suffix}"
    observed = []
    failures = []
    sessions = []

    class Handler(BaseHTTPRequestHandler):
        """从真实 provider 请求检查能力，不复用被测授权函数生成预期。"""

        def do_POST(self):
            """依次读取说明、执行工具、验证下一 Run 没有插件激活。"""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            names = {item["function"]["name"] for item in body.get("tools", [])}
            messages = body["messages"]
            last_user = max(
                i
                for i, item in enumerate(messages)
                if item["role"] == "user" and any(marker in str(item["content"]) for marker in ("IMAGE_SKILL_START", "IMAGE_SKILL_RESUME"))
            )
            tool_results = [item for item in messages[last_user + 1 :] if item["role"] == "tool"]
            resumed = "IMAGE_SKILL_RESUME" in str(messages[last_user]["content"])
            system_content = "\n".join(str(item["content"]) for item in messages if item["role"] == "system")
            try:
                assert ("present_artifacts" in names) is direct_tool
                if not tool_results and not resumed:
                    assert "- **image-gen**:" in system_content
                    assert "- **chat-widgets**:" not in system_content
                    observed.append("initial")
                    name, args = "read_file", {"file_path": "/home/user/skills/image-gen/SKILL.md"}
                    delta, finish = self.tool_call(name, args)
                elif tool_results and tool_results[-1]["tool_call_id"] == "call-read_file":
                    assert "图片生成技能" in str(tool_results[-1]["content"])
                    assert "- **chat-widgets**:" not in system_content
                    observed.append("loaded")
                    delta, finish = self.next_response()
                elif resumed and not tool_results:
                    assert any(item["role"] == "tool" and "图片生成技能" in str(item["content"]) for item in messages[:last_user])
                    assert "- **chat-widgets**:" not in system_content
                    observed.append("restored")
                    delta, finish = self.next_response()
                else:
                    assert "已将交付物展示给用户" in str(tool_results[-1]["content"])
                    observed.append("executed")
                    delta, finish = {"role": "assistant", "content": "IMAGE_SKILL_OK"}, "stop"
            except AssertionError:
                failures.append({"names": sorted(names), "results": tool_results, "observed": list(observed), "resumed": resumed})
                send_json_response(self, 400, {"error": {"message": "Plugin capability oracle failed"}})
                return
            common = {"id": "plugin-test", "object": "chat.completion.chunk", "created": 1, "model": "deterministic-chat"}
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

        def next_response(self):
            """只有直接选择工具的用例才调用通用工具。"""
            if direct_tool:
                return self.tool_call("present_artifacts", {"filepaths": []})
            return {"role": "assistant", "content": "IMAGE_SKILL_OK"}, "stop"

        def tool_call(self, name, args):
            """生成模型协议中的确定工具调用。"""
            return {
                "role": "assistant",
                "tool_calls": [
                    {"index": 0, "id": f"call-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                ],
            }, "tool_calls"

        def log_message(self, *_):
            """不记录请求正文。"""

    db = await asyncpg.connect(postgres_dsn())
    with serve_replay_handler(Handler) as replay_url:
        try:
            await register_replay_provider(e2e_client, e2e_headers, base_url=replay_url, provider_id=provider)
            response = await e2e_client.post(
                "/api/agent",
                headers=e2e_headers,
                json={
                    "name": "Plugin validation",
                    "slug": agent,
                    "backend_id": "ChatbotAgent",
                    "config_json": {
                        "context": {
                            "model": f"{provider}:deterministic-chat",
                            "tools": ["present_artifacts"] if direct_tool else [],
                            "mcps": [],
                            "skills": ["image-gen"],
                            "preload_skills": [],
                            "plugins": [],
                        }
                    },
                },
            )
            assert response.status_code == 200, response.text
            response = await e2e_client.post(
                "/api/v1/agents/sessions",
                headers={**e2e_headers, "Idempotency-Key": str(uuid4())},
                json={"agent_id": agent, "title": "pytest-plugin"},
            )
            assert response.status_code == 201, response.text
            thread = response.json()["id"]
            sessions.append(thread)
            for content in ["IMAGE_SKILL_START", "IMAGE_SKILL_RESUME"]:
                response = await e2e_client.post(
                    f"/api/v1/agents/sessions/{thread}/events",
                    headers={**e2e_headers, "Idempotency-Key": str(uuid4())},
                    json={"events": [{"type": "agent.session.input.message", "input": [public_message(content)]}]},
                )
                assert response.status_code == 202, response.text
                consumed = await wait_for_consumed_input(e2e_client, e2e_headers, response.json())
                turn = await wait_for_terminal_turn(e2e_client, e2e_headers, thread, consumed["turn_id"])
                assert turn["status"] == "completed", json.dumps({"turn": turn, "oracle": failures}, ensure_ascii=False)
                assert "IMAGE_SKILL_OK" in output_text(turn["yuxi"]["output"])
                async with await AsyncConnection.connect(postgres_dsn(), autocommit=True, row_factory=dict_row) as connection:
                    saved = await AsyncPostgresSaver(connection).aget_tuple({"configurable": {"thread_id": thread}})
                    assert saved is not None
                    assert not saved.checkpoint["channel_values"].get("activated_plugins")
            runs = await db.fetch("SELECT manifest FROM agent_runs WHERE thread_id=$1 ORDER BY created_at", thread)
            assert len(runs) == 2
            for row in runs:
                manifest = json.loads(row["manifest"])
                assert [item["id"] for item in manifest["resources"]["plugins"]] == []
                assert manifest["resources"]["tools"] == (["present_artifacts"] if direct_tool else [])
            expected = ["initial", "loaded", "executed", "restored", "executed"] if direct_tool else ["initial", "loaded", "restored"]
            assert observed == expected
        finally:
            await db.close()
            for thread in sessions:
                await archive_public_thread(e2e_client, e2e_headers, thread)
            await delete_agent(e2e_client, e2e_headers, agent)
            await e2e_client.delete(f"/api/system/model-providers/{provider}", headers=e2e_headers)
