"""真实企业 MCP 经 API、worker 与持久 Run 验证授权缺失及刷新。"""

import json
import os
from datetime import timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
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
from yuxi.modules.extensions.mcp.oauth_config import config_revision, decrypt, encrypt
from yuxi.modules.extensions.plugins.registry import get_plugin
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.self_managed_resources]


@pytest.mark.parametrize(
    "mode,via_plugin",
    [
        ("unconnected", False),
        ("unconnected", True),
        ("disconnect", False),
        ("disconnect", True),
        ("invalid_refresh", True),
        ("refresh", False),
    ],
)
async def test_personal_mcp_availability_through_worker(mode, via_plugin):
    """缺少连接仍有最终答复，旧工具拒绝断开，真实刷新原子落库。"""
    private_file = os.getenv("MCP_LAB_ACCOUNTS_FILE")
    if not private_file:
        pytest.skip("需要企业浏览器授权后的 MCP_LAB_ACCOUNTS_FILE，不以 mock 代替远端刷新")
    account = json.loads(Path(private_file).read_text())["superadmin_employee"]
    root = os.getenv("TEST_BASE_URL", "http://localhost:5050")
    headers = {"Authorization": "Bearer " + account["access_token"]}
    suffix = uuid4().hex[:10]
    server_slug, agent_slug, provider_id = (prefix + suffix for prefix in ("pytest-mcp-", "pytest-mcp-agent-", "mcp-availability-"))
    plugin_id = "mcp-availability-fixture"
    if via_plugin:
        try:
            deployed = get_plugin(plugin_id)
        except ValueError:
            pytest.fail("先将 test/support/mcp_availability_plugin.json 部署到 plugins/deployed 并重启 API/worker")
        server_slug = deployed.mcps[0]
        skill_slugs = list(deployed.skills)
    else:
        skill_slugs = []
    observed, threads = [], []
    revoked = False
    previous_credentials = None
    source_id = None
    db = await asyncpg.connect(postgres_dsn())
    if via_plugin:
        existing = await db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM mcp_servers WHERE slug=$1) OR EXISTS(SELECT 1 FROM skills WHERE slug=ANY($2::text[]))",
            server_slug,
            skill_slugs,
        )
        if existing:
            await db.close()
            pytest.fail("插件验收固定资源已存在，请使用独立实验部署且串行执行，避免覆盖已有资源")

    class Handler(BaseHTTPRequestHandler):
        """模型协议 oracle 检查实际工具，缺少能力仍完成普通聊天。"""

        def do_POST(self):
            """有工具才调用，缺少工具仍返回普通聊天结果。"""
            nonlocal revoked
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            tools = [message for message in body["messages"] if message["role"] == "tool"]
            names = {item["function"]["name"] for item in body.get("tools", [])}
            if via_plugin and not tools:
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "activate-plugin",
                            "type": "function",
                            "function": {"name": "load_plugin", "arguments": json.dumps({"plugin_id": plugin_id})},
                        }
                    ],
                }
                finish = "tool_calls"
            elif via_plugin and tools[-1]["tool_call_id"] == "activate-plugin" and not mode.startswith("disconnect"):
                assert "query_my_attendance" not in names
                assert "未激活" in str(tools[-1]["content"])
                observed.append("ordinary-chat")
                delta, finish = {"role": "assistant", "content": "普通聊天正常。"}, "stop"
            elif tools and tools[-1]["tool_call_id"] != "activate-plugin":
                result = str(tools[-1]["content"])
                if mode == "refresh" and "年假" not in result:
                    send_json_response(self, 400, {"error": {"message": "未取得真实本人考勤"}})
                    return
                observed.append("tool-result")
                delta, finish = {"role": "assistant", "content": "REAL_ATTENDANCE:" + result}, "stop"
            elif "query_my_attendance" in names:
                if mode.startswith("disconnect") and not revoked:
                    response = httpx.delete(root + f"/api/system/mcp-servers/{server_slug}/connection", headers=headers)
                    assert response.status_code == 200
                    revoked = True
                observed.append("tool-visible")
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "availability-call",
                            "type": "function",
                            "function": {
                                "name": "query_my_attendance",
                                "arguments": json.dumps({"start_date": "2026-10-12", "end_date": "2026-10-13"}),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                assert "query_my_attendance" not in names
                observed.append("ordinary-chat")
                delta, finish = {"role": "assistant", "content": "普通聊天正常。"}, "stop"
            common = {"id": "availability", "object": "chat.completion.chunk", "created": 1, "model": "deterministic-chat"}
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
            """不记录输入与凭据。"""

    async with httpx.AsyncClient(base_url=root, timeout=120) as client:
        with serve_replay_handler(Handler) as replay_url:
            try:
                await register_replay_provider(client, headers, base_url=replay_url, provider_id=provider_id)
                source_response = await client.get("/api/system/mcp-servers/enterprise-attendance", headers=headers)
                assert source_response.status_code == 200
                source = source_response.json()["data"]
                source_id = source["id"]
                if mode == "refresh":
                    server_slug = "enterprise-attendance"
                    previous_credentials = await db.fetchval(
                        "SELECT credentials FROM mcp_connections WHERE server_id=$1 AND user_id=(SELECT id FROM users WHERE uid=$2)",
                        source_id,
                        account["uid"],
                    )
                    assert previous_credentials, "真实企业账号必须先完成浏览器授权"
                    await db.execute(
                        "UPDATE mcp_connections SET expires_at=$1 WHERE server_id=$2 AND user_id=(SELECT id FROM users WHERE uid=$3)",
                        utc_now() - timedelta(seconds=1),
                        source_id,
                        account["uid"],
                    )
                else:
                    created = await client.post(
                        "/api/system/mcp-servers",
                        headers=headers,
                        json={
                            "slug": server_slug,
                            "name": "考勤连接验收",
                            "url": source["url"],
                            "transport": source["transport"],
                            "auth_mode": "oauth",
                            "oauth_config": source["oauth_config"],
                        },
                    )
                    assert created.status_code == 200, created.text
                    if mode != "unconnected":
                        if mode.startswith("disconnect"):
                            # 先由真实连接刷新并落库，副本保留实际有效期。
                            discovered = await client.get("/api/system/mcp-servers/enterprise-attendance/tools", headers=headers)
                            assert discovered.status_code == 200
                            assert any(tool["name"] == "query_my_attendance" for tool in discovered.json()["data"])
                        record = await db.fetchrow(
                            "SELECT credentials,expires_at FROM mcp_connections "
                            "WHERE server_id=$1 AND user_id=(SELECT id FROM users WHERE uid=$2)",
                            source_id,
                            account["uid"],
                        )
                        assert record and record["credentials"]
                        if mode.startswith("disconnect"):
                            assert record["expires_at"] > utc_now() + timedelta(seconds=60)
                        credentials = record["credentials"]
                        if mode.startswith("disconnect"):
                            # 副本只测试撤销访问令牌，不能轮换源连接的刷新令牌。
                            payload = decrypt(credentials)
                            payload.pop("refresh_token", None)
                            credentials = encrypt(payload)
                        if mode == "invalid_refresh":
                            payload = decrypt(credentials)
                            payload["refresh_token"] = "invalid-e2e-refresh-token"
                            credentials = encrypt(payload)
                        configured = dict(
                            await db.fetchrow(
                                "SELECT url,transport,headers,auth_mode,oauth_config,oauth_secret FROM mcp_servers WHERE slug=$1",
                                server_slug,
                            )
                        )
                        for field in ("headers", "oauth_config"):
                            if isinstance(configured[field], str):
                                configured[field] = json.loads(configured[field])
                        await db.execute(
                            "INSERT INTO mcp_connections "
                            "(server_id,user_id,status,generation,config_revision,credentials,expires_at,updated_at) "
                            "VALUES ((SELECT id FROM mcp_servers WHERE slug=$1),"
                            "(SELECT id FROM users WHERE uid=$2),'connected',1,$3,$4,$5,$6)",
                            server_slug,
                            account["uid"],
                            config_revision(SimpleNamespace(**configured)),
                            credentials,
                            record["expires_at"] if mode.startswith("disconnect") else utc_now() - timedelta(seconds=1),
                            utc_now(),
                        )
                if via_plugin:
                    for installed_slug in skill_slugs:
                        package = f"---\nname: {installed_slug}\ndescription: 查询考勤\n---\n查询本人考勤。\n"
                        prepared = await client.post(
                            "/api/skills/import/prepare", headers=headers, files={"file": ("SKILL.md", package.encode(), "text/markdown")}
                        )
                        assert prepared.status_code == 200, prepared.text
                        confirmed = await client.post(
                            f"/api/skills/install-drafts/{prepared.json()['data']['draft_id']}/confirm",
                            headers=headers,
                            json={"slugs": [installed_slug]},
                        )
                        assert confirmed.status_code == 200 and confirmed.json()["data"][0]["success"], confirmed.text
                created = await client.post(
                    "/api/agent",
                    headers=headers,
                    json={
                        "slug": agent_slug,
                        "name": "MCP 可用性验收",
                        "backend_id": "ChatbotAgent",
                        "config_json": {
                            "context": {
                                "model": provider_id + ":deterministic-chat",
                                "tools": [],
                                "knowledges": [],
                                "mcps": [] if via_plugin else [server_slug],
                                "skills": [],
                                "plugins": [plugin_id] if via_plugin else [],
                                "preload_skills": [],
                                "system_prompt": "正常聊天；若可用则查询本人考勤。",
                            }
                        },
                    },
                )
                assert created.status_code == 200, created.text
                agent_id = created.json()["agent"]["agent_id"]
                for index in range(2 if mode.startswith("disconnect") else 1):
                    submitted = await client.post(
                        "/api/v1/agents/sessions",
                        headers={**headers, "Idempotency-Key": suffix + str(index)},
                        json={
                            "agent_id": agent_id,
                            "title": "pytest-mcp-availability-" + suffix,
                            "input": [public_message("你好，请查询考勤（如果已连接）")],
                        },
                    )
                    assert submitted.status_code == 201, submitted.text
                    accepted = await wait_for_consumed_input(client, headers, submitted.json()["yuxi"]["receipt"])
                    thread, turn_id, run = accepted["session_id"], accepted["turn_id"], accepted["run_id"]
                    threads.append((thread, turn_id))
                    turn = await wait_for_terminal_turn(client, headers, thread, turn_id)
                    assert turn["yuxi"]["result_run_id"] == run
                    if mode.startswith("disconnect") and index == 0:
                        assert revoked
                        successful_calls = await db.fetchval(
                            "SELECT count(*) FROM messages WHERE run_id=$1 AND message_type='tool_audit' AND execution_status='completed' "
                            "AND operation_id<>'activate-plugin'",
                            run,
                        )
                        assert successful_calls == 0
                        assert turn["status"] == "completed", turn
                        denied = output_text(turn["yuxi"]["output"])
                        assert "能力受限" in denied or "连接" in denied
                        continue
                    assert turn["status"] == "completed", turn
                    final = output_text(turn["yuxi"]["output"])
                    if mode == "refresh":
                        assert "年假" in final and "tool-result" in observed
                        current = await db.fetchval(
                            "SELECT credentials FROM mcp_connections WHERE server_id=$1 AND user_id=(SELECT id FROM users WHERE uid=$2)",
                            source_id,
                            account["uid"],
                        )
                        assert decrypt(current)["refresh_token"] != decrypt(previous_credentials)["refresh_token"]
                        audit = await db.fetchrow(
                            "SELECT turn_id,extra_metadata FROM messages WHERE run_id=$1 "
                            "AND message_type='tool_audit' AND operation_id='availability-call'",
                            run,
                        )
                        assert audit and audit["turn_id"] == turn_id and "年假" in str(audit["extra_metadata"])
                    else:
                        assert "普通聊天正常" in final and "ordinary-chat" in observed
                        if via_plugin:
                            assert (
                                await db.fetchval(
                                    "SELECT count(*) FROM messages WHERE run_id=$1 AND message_type='tool_audit' "
                                    "AND operation_id='activate-plugin'",
                                    run,
                                )
                                == 1
                            )
                            assert not await db.fetchval(
                                "SELECT count(*) FROM messages WHERE run_id=$1 AND message_type='tool_audit' "
                                "AND operation_id<>'activate-plugin' AND execution_status='completed'",
                                run,
                            )
                        else:
                            assert not await db.fetchval("SELECT count(*) FROM messages WHERE run_id=$1 AND message_type='tool_audit'", run)
                if mode == "invalid_refresh":
                    assert (
                        await db.fetchval(
                            "SELECT status FROM mcp_connections WHERE server_id=(SELECT id FROM mcp_servers WHERE slug=$1)",
                            server_slug,
                        )
                        == "reconnect_required"
                    )
            finally:
                cleanup_errors = []
                try:
                    for thread, turn_id in threads:
                        try:
                            await archive_public_thread(client, headers, thread, turn_id=turn_id)
                        except Exception as error:
                            cleanup_errors.append(f"归档测试 Thread：{type(error).__name__}")
                    try:
                        await delete_agent(client, headers, agent_slug)
                    except Exception as error:
                        cleanup_errors.append(f"删除测试 Agent：{type(error).__name__}")
                    if via_plugin:
                        try:
                            response = await client.post("/api/system/skills/delete-batch", headers=headers, json={"slugs": skill_slugs})
                            assert response.status_code == 200
                            assert all(item["success"] for item in response.json()["data"])
                            assert not await db.fetchval("SELECT count(*) FROM skills WHERE slug=ANY($1::text[])", skill_slugs)
                        except Exception as error:
                            cleanup_errors.append(f"删除测试 Skill：{type(error).__name__}")
                    if server_slug != "enterprise-attendance":
                        try:
                            response = await client.delete(f"/api/system/mcp-servers/{server_slug}", headers=headers)
                            assert response.status_code in (200, 404)
                            assert not await db.fetchval("SELECT count(*) FROM mcp_servers WHERE slug=$1", server_slug)
                        except Exception as error:
                            cleanup_errors.append(f"删除测试 MCP：{type(error).__name__}")
                    try:
                        response = await client.delete(f"/api/system/model-providers/{provider_id}", headers=headers)
                        assert response.status_code in (200, 404)
                        assert not await db.fetchval("SELECT count(*) FROM model_providers WHERE provider_id=$1", provider_id)
                    except Exception as error:
                        cleanup_errors.append(f"删除测试供应商：{type(error).__name__}")
                finally:
                    await db.close()
                assert not cleanup_errors, "; ".join(cleanup_errors)
