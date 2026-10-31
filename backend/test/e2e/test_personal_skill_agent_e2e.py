from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from http.server import BaseHTTPRequestHandler
from typing import Any

import asyncpg
import httpx
import pytest
from e2e_helpers import (
    RUN_TIMEOUT_SECONDS,
    archive_public_thread,
    iter_public_thread_events,
    postgres_dsn,
    skip_if_external_quota,
    wait_for_consumed_input,
    wait_for_terminal_turn,
)

from test.e2e.e2e_helpers import output_text, register_replay_provider
from test.live_api_cleanup import (
    make_test_session_title,
)
from test.support.openai_replay_server import send_json_response, send_openai_sse_response, serve_replay_handler
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir
from yuxi.modules.workspace.paths import user_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture
def personal_replay():
    """固定模型只在真实 read_file 返回标记后给出最终回答。"""
    scenario = {"requests": []}

    class Handler(BaseHTTPRequestHandler):
        """验收实际请求中的 Skill 可发现性与读取结果。"""

        def do_POST(self):
            """先调用文件工具，再消费实际返回的 Skill 正文。"""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            scenario["requests"].append(body)
            tools = [item for item in body["messages"] if item["role"] == "tool"]
            if scenario.get("install_command"):
                if tools:
                    if scenario["marker"] not in json.dumps(tools[-1]["content"]):
                        send_json_response(self, 400, {"error": {"message": "个人目录写入未返回实际文件标记"}})
                        return
                    delta, finish = {"role": "assistant", "content": "installed"}, "stop"
                else:
                    delta = {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "personal-install",
                                "type": "function",
                                "function": {"name": "execute", "arguments": json.dumps({"command": scenario["install_command"]})},
                            }
                        ],
                    }
                    finish = "tool_calls"
            elif tools:
                marker = re.search(r"PERSONAL_SKILL_E2E_[A-F0-9]{10}", json.dumps(tools[-1]["content"]))
                if marker is None:
                    send_json_response(self, 400, {"error": {"message": "真实文件读取未返回 Skill 标记"}})
                    return
                delta, finish = {"role": "assistant", "content": marker.group()}, "stop"
            else:
                names = {item["function"]["name"] for item in body["tools"]}
                if (
                    f"/home/user/workspace/agents/skills/{scenario['slug']}/SKILL.md" not in json.dumps(body["messages"])
                    or "read_file" not in names
                ):
                    send_json_response(self, 400, {"error": {"message": "个人 Skill 或合法文件工具未进入模型请求"}})
                    return
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "personal-read",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"file_path": f"/home/user/workspace/agents/skills/{scenario['slug']}/SKILL.md"}),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            common = {"id": "personal-replay", "object": "chat.completion.chunk", "created": 1, "model": "deterministic-chat"}
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

        def log_message(self, *args):
            """测试不输出模型输入。"""

    with serve_replay_handler(Handler) as url:
        yield {"url": url, **scenario, "scenario": scenario}


@pytest.mark.parametrize("install_method", ["web", "standard_directory"])
async def test_main_agent_reads_personal_skill_from_web_or_standard_directory(
    e2e_client: httpx.AsyncClient,
    e2e_headers: dict[str, str],
    e2e_agent_context: dict[str, str],
    personal_replay,
    install_method,
):
    """网页安装与沙盒标准入口写入后，主 Agent 均能发现并读取个人技能。"""
    uid = e2e_agent_context["uid"]
    marker = f"PERSONAL_SKILL_E2E_{uuid.uuid4().hex[:10].upper()}"
    slug = f"pytest-personal-agent-{uuid.uuid4().hex[:8]}"
    agent_slug = f"e2e-personal-skill-{uuid.uuid4().hex[:8]}"
    skill_md = (
        f"---\nname: {slug}\ndescription: Return the verification marker when explicitly requested.\n---\n"
        f"# Verification\nWhen the user asks for the personal Skill marker, reply with exactly `{marker}`.\n"
    )
    run_id: str | None = None
    turn_id: str | None = None
    thread_id: str | None = None
    agent_created = False
    provider_created = False
    provider_id = f"ci-personal-{uuid.uuid4().hex[:8]}"
    personal_replay["scenario"]["slug"] = slug

    try:
        await register_replay_provider(e2e_client, e2e_headers, base_url=personal_replay["url"], provider_id=provider_id)
        provider_created = True
        await asyncio.sleep(6)
        context: dict[str, Any] = {
            "system_prompt": (
                f"收到请求后从可用 Skills 中找到 {slug} 并读取其 SKILL.md，然后严格遵循其中的 Verification 指令，不要添加解释。"
            ),
            "model": f"{provider_id}:deterministic-chat",
            "tools": [],
            "tool_approval_mode": "always_trust",
            "knowledges": [],
            "mcps": [],
            "skills": [],
        }

        agent_response = await e2e_client.post(
            "/api/agent",
            headers=e2e_headers,
            json={
                "name": f"E2E Personal Skill {slug[-8:]}",
                "slug": agent_slug,
                "backend_id": "ChatbotAgent",
                "visibility": "shared",
                "description": "真实个人 Skill Agent E2E 临时智能体",
                "config_json": {"context": context},
                "share_config": {
                    "version": 2,
                    "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
                    "manage_scope": None,
                },
            },
        )
        assert agent_response.status_code == 200, agent_response.text
        agent_created = True

        if install_method == "web":
            prepare_response = await e2e_client.post(
                "/api/skills/import/prepare",
                headers=e2e_headers,
                files={"file": ("SKILL.md", skill_md.encode(), "text/markdown")},
            )
            assert prepare_response.status_code == 200, prepare_response.text
            draft = prepare_response.json()["data"]
            confirm_response = await e2e_client.post(
                f"/api/skills/personal/install-drafts/{draft['draft_id']}/confirm",
                headers=e2e_headers,
                json={"slugs": [draft["items"][0]["slug"]]},
            )
            assert confirm_response.status_code == 200, confirm_response.text
        else:
            # 首个真实 Run 通过标准入口写文件；CLI 自身由真实沙盒安装探针独立验证。
            scenario = personal_replay["scenario"]
            encoded = base64.b64encode(skill_md.encode()).decode()
            scenario["marker"] = marker
            scenario["install_command"] = (
                f"mkdir -p ~/.agents/skills/{slug} && "
                f"printf %s {encoded} | base64 -d > ~/.agents/skills/{slug}/SKILL.md && "
                f"cat ~/.agents/skills/{slug}/SKILL.md"
            )
            installed_thread = await e2e_client.post(
                "/api/v1/agents/sessions",
                headers={**e2e_headers, "Idempotency-Key": f"install-{uuid.uuid4().hex}"},
                json={"agent_id": agent_slug, "title": make_test_session_title("personal-install-e2e")},
            )
            assert installed_thread.status_code == 201, installed_thread.text
            thread_id = str(installed_thread.json()["id"])
            installed_input = await e2e_client.post(
                f"/api/v1/agents/sessions/{thread_id}/events",
                headers={**e2e_headers, "Idempotency-Key": f"install-input-{uuid.uuid4().hex}"},
                json={
                    "events": [
                        {
                            "type": "agent.session.input.message",
                            "input": [
                                {
                                    "role": "user",
                                    "content": [{"type": "input_text", "text": "将个人 Skill 写入标准目录。"}],
                                }
                            ],
                            "yuxi": {"mode": "follow_up"},
                        }
                    ]
                },
            )
            assert installed_input.status_code == 202, installed_input.text
            installed_run = await wait_for_consumed_input(e2e_client, e2e_headers, installed_input.json())
            turn_id = installed_run["turn_id"]
            installed_turn = await wait_for_terminal_turn(e2e_client, e2e_headers, thread_id, turn_id, timeout=RUN_TIMEOUT_SECONDS)
            assert installed_turn["status"] == "completed", installed_turn
            assert (user_workspace_dir(uid) / "agents" / "skills" / slug / "SKILL.md").read_text() == skill_md
            await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
            thread_id, turn_id = None, None
            scenario.pop("install_command")

        thread_response = await e2e_client.post(
            "/api/v1/agents/sessions",
            headers={**e2e_headers, "Idempotency-Key": f"personal-skill-create-{uuid.uuid4().hex}"},
            json={
                "agent_id": agent_slug,
                "title": make_test_session_title("personal-skill-e2e"),
            },
        )
        assert thread_response.status_code == 201, thread_response.text
        thread_id = str(thread_response.json()["id"])

        run_response = await e2e_client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            headers={**e2e_headers, "Idempotency-Key": f"personal-skill-input-{uuid.uuid4().hex}"},
            json={
                "events": [
                    {
                        "type": "agent.session.input.message",
                        "input": [
                            {
                                "role": "user",
                                "content": [{"type": "input_text", "text": "请读取并返回 personal Skill marker。"}],
                            }
                        ],
                        "yuxi": {"mode": "follow_up"},
                    }
                ],
            },
        )
        assert run_response.status_code == 202, run_response.text
        consumed = await wait_for_consumed_input(e2e_client, e2e_headers, run_response.json())
        run_id, turn_id = consumed["run_id"], consumed["turn_id"]

        async def consume_output() -> int:
            """观察本 Run 的模型增量直到同一 Turn 终态。"""
            message_events = 0
            async for event in iter_public_thread_events(e2e_client, e2e_headers, thread_id):
                if event.get("yuxi", {}).get("run_id") == run_id and event["type"] == "agent.session.turn.output_text.delta":
                    message_events += 1
                if event.get("turn_id") == turn_id and event["type"] in {
                    "agent.session.turn.completed",
                    "agent.session.turn.failed",
                    "agent.session.turn.cancelled",
                }:
                    return message_events
            pytest.fail("个人 Skill 的 Thread SSE 在终态前断开")

        event_count = await asyncio.wait_for(consume_output(), timeout=RUN_TIMEOUT_SECONDS)
        assert event_count > 0, event_count
        turn_response = await e2e_client.get(f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}", headers=e2e_headers)
        assert turn_response.status_code == 200, turn_response.text
        turn = turn_response.json()
        if turn["status"] != "completed":
            skip_if_external_quota((turn.get("error") or {}).get("message"))
        assert turn["status"] == "completed", turn
        assert turn["yuxi"]["result_run_id"] == run_id, turn

        assert marker in output_text(turn["yuxi"]["output"] or []), turn

        conn = await asyncpg.connect(postgres_dsn())
        try:
            raw_manifest = await conn.fetchval("SELECT manifest FROM agent_runs WHERE id = $1", run_id)
            manifest = json.loads(raw_manifest) if isinstance(raw_manifest, str) else raw_manifest
            assert slug in {item["slug"] for item in manifest["resources"]["skills"]}
        finally:
            await conn.close()

        assert len(personal_replay["requests"]) == (2 if install_method == "web" else 4)
        # 移除真实首请求的发现路径，保留测试原提示中的 slug 与工具 schema。
        first_request = personal_replay["requests"][-2]
        discovered_path = f"/home/user/workspace/agents/skills/{slug}/SKILL.md"
        missing_discovery = json.loads(json.dumps(first_request).replace(discovered_path, ""))
        async with httpx.AsyncClient() as replay_client:
            rejected = await replay_client.post(f"{personal_replay['url']}/chat/completions", json=missing_discovery)
        assert rejected.status_code == 400
        assert rejected.json()["error"]["message"] == "个人 Skill 或合法文件工具未进入模型请求"
        personal_skill = user_workspace_dir(uid) / "agents" / "skills" / slug / "SKILL.md"
        assert personal_skill.read_text(encoding="utf-8") == skill_md
        projected_skill = get_user_skills_root_dir(uid) / slug / "SKILL.md"
        assert not projected_skill.exists()
    finally:
        if thread_id:
            await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
        if agent_created:
            agent_delete = await e2e_client.delete(f"/api/agent/{agent_slug}", headers=e2e_headers)
            assert agent_delete.status_code in {200, 404}, agent_delete.text
        skill_delete = await e2e_client.delete(f"/api/skills/personal/{slug}", headers=e2e_headers)
        assert skill_delete.status_code in {200, 404}, skill_delete.text
        if provider_created:
            response = await e2e_client.delete(f"/api/system/model-providers/{provider_id}", headers=e2e_headers)
            assert response.status_code in {200, 404}, response.text
