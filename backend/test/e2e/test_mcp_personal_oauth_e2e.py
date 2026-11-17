"""已完成真实浏览器授权的 Keycloak 企业考勤 worker 验收。"""

import asyncio
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest

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
from yuxi.modules.extensions.plugins.registry import get_plugin

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.self_managed_resources]


@pytest.mark.parametrize(
    "employee,via_plugin,expected,forbidden,entrypoint",
    [
        ("superadmin_employee", False, "年假", ("加班", "外勤"), "direct"),
        ("admin_employee", False, "加班", ("年假", "外勤"), "direct"),
        ("user_employee", True, "外勤", ("年假", "加班"), "direct"),
        ("user_employee", False, "外勤", ("年假", "加班"), "cooperation"),
        ("user_employee", False, "外勤", ("年假", "加班"), "scheduled"),
    ],
)
async def test_real_employee_mcp_through_worker(employee, via_plugin, expected, forbidden, entrypoint):
    """模型只能消费真实工具结果，最终 Turn 与 audit 属于同一 result Run。"""
    private_file = os.getenv("MCP_LAB_ACCOUNTS_FILE")
    if not private_file:
        pytest.skip("需要独立企业实验及真实浏览器授权后的 MCP_LAB_ACCOUNTS_FILE；不能由协议 fixture 替代")
    accounts = json.loads(Path(private_file).read_text())
    root = os.getenv("TEST_BASE_URL", "http://localhost:5050")
    admin_headers = {"Authorization": "Bearer " + accounts["admin"]["access_token"]}
    headers = {"Authorization": "Bearer " + accounts[employee]["access_token"]}
    suffix = uuid4().hex[:10]
    provider_id, slug, skill = "mcp-replay-" + suffix, "pytest-mcp-personal-" + suffix, "pytest-mcp-skill-" + suffix
    plugin_id = "employee-attendance-fixture"
    if via_plugin:
        try:
            deployed = get_plugin(plugin_id)
        except ValueError:
            pytest.fail("先部署 test/support/employee_attendance_plugin.json 到 plugins/deployed 并重启 API/worker")
        skill = deployed.entry_skill
        db = await asyncpg.connect(postgres_dsn())
        try:
            assert not await db.fetchval("SELECT count(*) FROM skills WHERE slug=$1", skill), "请使用独立实验部署，避免覆盖已有 Skill"
        finally:
            await db.close()
    observed = []

    class Handler(BaseHTTPRequestHandler):
        """固定模型协议先调用考勤，再原样返回真实 tool 内容。"""

        def do_POST(self):
            """工具未装配或返回业务错误时显式拒绝生成成功回答。"""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            tools = [item for item in body["messages"] if item["role"] == "tool"]
            if entrypoint == "cooperation" and "MCP_ATTENDANCE_PARENT" in str(
                [m for m in body["messages"] if m["role"] in {"system", "developer"}]
            ):
                results = {message["tool_call_id"]: message["content"] for message in tools}
                if "create-employee" not in results:
                    call_id, name, arguments = (
                        "create-employee",
                        "create_session",
                        {"name": "employee", "description": "查询 2026-10-12 至 2026-10-13 本人考勤", "agent_id": slug},
                    )
                elif "wait-employee" not in results:
                    input_id = json.loads(results["create-employee"])["input_id"]
                    call_id, name, arguments = "wait-employee", "wait_inputs", {"input_ids": [input_id], "timeout_seconds": 60}
                elif "get-employee" not in results:
                    input_id = json.loads(results["create-employee"])["input_id"]
                    call_id, name, arguments = "get-employee", "get_result", {"input_id": input_id}
                else:
                    actual = results["get-employee"]
                    if expected not in str(actual) or any(value in str(actual) for value in forbidden):
                        send_json_response(self, 400, {"error": {"message": "协作结果不是实际执行员工的考勤"}})
                        return
                    delta, finish = {"role": "assistant", "content": "MCP_ATTENDANCE_RESULT:" + str(actual)}, "stop"
                if "get-employee" not in results:
                    delta = {
                        "role": "assistant",
                        "tool_calls": [
                            {"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
                        ],
                    }
                    finish = "tool_calls"
            elif via_plugin and not tools:
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "activate-attendance",
                            "type": "function",
                            "function": {"name": "load_plugin", "arguments": json.dumps({"plugin_id": plugin_id})},
                        }
                    ],
                }
                finish = "tool_calls"
            elif tools and tools[-1]["tool_call_id"] != "activate-attendance":
                actual = tools[-1]["content"]
                observed.append(actual)
                if expected not in str(actual) or any(value in str(actual) for value in forbidden):
                    send_json_response(self, 400, {"error": {"message": "真实考勤结果与员工不符"}})
                    return
                delta, finish = {"role": "assistant", "content": "MCP_ATTENDANCE_RESULT:" + str(actual)}, "stop"
            else:
                names = {item["function"]["name"] for item in body["tools"]}
                if "query_my_attendance" not in names:
                    send_json_response(self, 400, {"error": {"message": "个人考勤 MCP 未进入模型工具"}})
                    return
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "attendance-call",
                            "type": "function",
                            "function": {
                                "name": "query_my_attendance",
                                "arguments": json.dumps({"start_date": "2026-10-12", "end_date": "2026-10-13"}),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            common = {"id": "attendance-replay", "object": "chat.completion.chunk", "created": 1, "model": "deterministic-chat"}
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

        def log_message(self, *_):
            """不输出模型输入、用户 token 或请求正文。"""

    thread_id, turn_id, seed_thread, job_id = None, None, None, None
    parent_slug = slug + "-parent"
    async with httpx.AsyncClient(base_url=root, timeout=120) as client:
        with serve_replay_handler(Handler) as replay_url:
            try:
                await register_replay_provider(client, admin_headers, base_url=replay_url, provider_id=provider_id)
                if via_plugin:
                    package = f"---\nname: {skill}\ndescription: 查询本人考勤\n---\n使用考勤工具查询本人记录。\n"
                    prepared = await client.post(
                        "/api/skills/import/prepare", headers=admin_headers, files={"file": ("SKILL.md", package.encode(), "text/markdown")}
                    )
                    assert prepared.status_code == 200, prepared.text
                    draft = prepared.json()["data"]["draft_id"]
                    confirmed = await client.post(
                        f"/api/skills/install-drafts/{draft}/confirm",
                        headers=admin_headers,
                        json={
                            "slugs": [skill],
                            "share_config": {"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
                        },
                    )
                    assert confirmed.status_code == 200 and confirmed.json()["data"][0]["success"], confirmed.text
                created = await client.post(
                    "/api/agent",
                    headers=admin_headers,
                    json={
                        "slug": slug,
                        "name": "个人考勤 E2E",
                        "backend_id": "ChatbotAgent",
                        "visibility": "shared",
                        "share_config": {
                            "version": 2,
                            "read_scope": {"access_level": "user", "user_uids": [accounts[employee]["uid"]]},
                            "manage_scope": None,
                        },
                        "config_json": {
                            "context": {
                                "model": provider_id + ":deterministic-chat",
                                "tools": [],
                                "knowledges": [],
                                "mcps": [] if via_plugin else ["enterprise-attendance"],
                                "skills": [],
                                "plugins": [plugin_id] if via_plugin else [],
                                "preload_skills": [],
                                "system_prompt": "调用考勤工具后返回本人记录。",
                            }
                        },
                    },
                )
                assert created.status_code == 200, created.text
                agent_id = created.json()["agent"]["agent_id"]
                target_agent = agent_id
                if entrypoint == "cooperation":
                    assert accounts[employee]["uid"] != accounts["admin"]["uid"]
                    parent = await client.post(
                        "/api/agent",
                        headers=admin_headers,
                        json={
                            "slug": parent_slug,
                            "name": "个人考勤协作 E2E",
                            "backend_id": "ChatbotAgent",
                            "visibility": "shared",
                            "share_config": {
                                "version": 2,
                                "read_scope": {"access_level": "user", "user_uids": [accounts[employee]["uid"]]},
                                "manage_scope": None,
                            },
                            "config_json": {
                                "context": {
                                    "model": provider_id + ":deterministic-chat",
                                    "tools": [],
                                    "knowledges": [],
                                    "mcps": [],
                                    "skills": [],
                                    "system_prompt": "MCP_ATTENDANCE_PARENT",
                                }
                            },
                        },
                    )
                    assert parent.status_code == 200, parent.text
                    target_agent = parent_slug
                submitted = await client.post(
                    "/api/v1/agents/sessions",
                    headers={**headers, "Idempotency-Key": suffix},
                    json={
                        "agent_id": target_agent,
                        "title": "pytest-mcp-personal-" + suffix,
                        **({"input": [public_message("查询 2026-10-12 至 2026-10-13 本人考勤")]} if entrypoint != "scheduled" else {}),
                    },
                )
                assert submitted.status_code == 201, submitted.text
                if entrypoint == "scheduled":
                    assert accounts[employee]["uid"] != accounts["admin"]["uid"]
                    seed_thread = submitted.json()["id"]
                    db = await asyncpg.connect(postgres_dsn())
                    try:
                        project_id = await db.fetchval("SELECT project_id FROM sessions WHERE thread_id=$1", seed_thread)
                        scheduled = await client.post(
                            "/api/scheduled-tasks",
                            headers=headers,
                            json={
                                "request_id": suffix,
                                "name": "pytest-mcp-scheduled-" + suffix,
                                "project_id": project_id,
                                "agent_slug": slug,
                                "prompt": "查询 2026-10-12 至 2026-10-13 本人考勤",
                                "cron_expression": "0 0 1 1 *",
                                "timezone": "UTC",
                                "model_spec": provider_id + ":deterministic-chat",
                            },
                        )
                        assert scheduled.status_code == 200, scheduled.text
                        job_id = scheduled.json()["id"]
                        await db.execute("UPDATE scheduled_agent_jobs SET next_run_at=$1 WHERE id=$2", datetime(2020, 1, 1), job_id)
                        accepted = None
                        for _ in range(600):
                            occurrence = await db.fetchrow("SELECT * FROM scheduled_agent_runs WHERE job_id=$1", job_id)
                            if occurrence:
                                thread_id = occurrence["thread_id"]
                                assert occurrence["trigger"] == "scheduled" and occurrence["occurrence_key"].startswith("scheduled:")
                                consumed = await db.fetchrow(
                                    "SELECT thread_id,turn_id,consumed_run_id FROM agent_inputs WHERE id=$1", occurrence["input_id"]
                                )
                                if consumed and consumed["consumed_run_id"]:
                                    accepted = {
                                        "session_id": consumed["thread_id"],
                                        "turn_id": consumed["turn_id"],
                                        "run_id": consumed["consumed_run_id"],
                                    }
                                    break
                            await asyncio.sleep(0.1)
                        assert accepted, "真实 worker 未消费到期考勤任务"
                    finally:
                        await db.close()
                else:
                    accepted = await wait_for_consumed_input(client, headers, submitted.json()["yuxi"]["receipt"])
                thread_id, turn_id = accepted["session_id"], accepted["turn_id"]
                turn = await wait_for_terminal_turn(client, headers, thread_id, turn_id)
                assert turn["status"] == "completed", turn
                final_run = turn["yuxi"]["result_run_id"]
                if entrypoint != "cooperation":
                    assert final_run == accepted["run_id"]
                final = output_text(turn["yuxi"]["output"])
                assert "MCP_ATTENDANCE_RESULT:" in final and expected in final and all(value not in final for value in forbidden)
                assert observed and expected in str(observed[-1])
                db = await asyncpg.connect(postgres_dsn())
                try:
                    assert await db.fetchval("SELECT turn_id FROM agent_runs WHERE id=$1", final_run) == turn_id
                    assert await db.fetchval("SELECT uid FROM agent_runs WHERE id=$1", final_run) == accounts[employee]["uid"]
                    audit_run, audit_turn = final_run, turn_id
                    if entrypoint == "cooperation":
                        child = await db.fetchrow(
                            "SELECT r.id,r.turn_id,t.result_run_id FROM sessions s JOIN agent_runs r ON r.thread_id=s.thread_id "
                            "JOIN agent_turns t ON t.id=r.turn_id WHERE s.parent_thread_id=$1 AND r.status='completed'",
                            thread_id,
                        )
                        assert child and child["id"] == child["result_run_id"]
                        audit_run, audit_turn = child["id"], child["turn_id"]
                    assert await db.fetchval("SELECT uid FROM agent_runs WHERE id=$1", audit_run) == accounts[employee]["uid"]
                    assert await db.fetchval("SELECT uid FROM agent_runs WHERE id=$1", accepted["run_id"]) == accounts[employee]["uid"]
                    audit = await db.fetchrow(
                        "SELECT execution_status, extra_metadata, turn_id FROM messages "
                        "WHERE run_id=$1 AND message_type='tool_audit' AND operation_id='attendance-call'",
                        audit_run,
                    )
                    assert audit and audit["turn_id"] == audit_turn and audit["execution_status"] == "completed"
                    assert expected in str(audit["extra_metadata"]) and all(
                        value not in str(audit["extra_metadata"]) for value in forbidden
                    )
                finally:
                    await db.close()
            finally:
                cleanup_errors = []
                db = await asyncpg.connect(postgres_dsn())
                try:
                    if job_id:
                        try:
                            deleted = await client.delete(f"/api/scheduled-tasks/{job_id}", headers=headers)
                            assert deleted.status_code == 200, deleted.text
                            assert await db.fetchval("SELECT deleted_at IS NOT NULL FROM scheduled_agent_jobs WHERE id=$1", job_id)
                        except Exception as error:
                            cleanup_errors.append(f"删除定时任务：{type(error).__name__}")
                    owned_threads = await db.fetch(
                        "SELECT s.thread_id,(SELECT t.id FROM agent_turns t WHERE t.thread_id=s.thread_id "
                        "ORDER BY t.created_at DESC LIMIT 1) AS turn_id "
                        "FROM sessions s WHERE s.agent_id=ANY($1::text[]) ORDER BY s.parent_thread_id NULLS LAST",
                        [slug, parent_slug],
                    )
                    for owned in owned_threads:
                        try:
                            await archive_public_thread(client, headers, owned["thread_id"], turn_id=owned["turn_id"])
                            assert await db.fetchval("SELECT status FROM sessions WHERE thread_id=$1", owned["thread_id"]) == "archived"
                        except Exception as error:
                            cleanup_errors.append(f"归档测试会话：{type(error).__name__}")
                    for agent in [parent_slug, slug] if entrypoint == "cooperation" else [slug]:
                        try:
                            await delete_agent(client, admin_headers, agent)
                        except Exception as error:
                            cleanup_errors.append(f"删除测试智能体：{type(error).__name__}")
                    if via_plugin:
                        try:
                            deleted = await client.post("/api/system/skills/delete-batch", headers=admin_headers, json={"slugs": [skill]})
                            assert deleted.status_code == 200 and all(item["success"] for item in deleted.json()["data"])
                        except Exception as error:
                            cleanup_errors.append(f"删除测试技能：{type(error).__name__}")
                    try:
                        deleted = await client.delete(f"/api/system/model-providers/{provider_id}", headers=admin_headers)
                        assert deleted.status_code in {200, 404}, deleted.text
                    except Exception as error:
                        cleanup_errors.append(f"删除测试供应商：{type(error).__name__}")
                finally:
                    await db.close()
                assert not cleanup_errors, "; ".join(cleanup_errors)
