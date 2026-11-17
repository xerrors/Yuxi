"""确定性 MCP 故障经过真实 API、worker、模型协议与持久结果验证。"""

import json
import os
from http.server import BaseHTTPRequestHandler
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import asyncpg
import pytest
from cryptography.fernet import Fernet
from redis.asyncio import Redis

from test.e2e.e2e_helpers import (
    archive_public_thread,
    output_text,
    postgres_dsn,
    public_message,
    register_replay_provider,
    wait_for_consumed_input,
    wait_for_terminal_turn,
)
from test.support.openai_replay_server import send_openai_sse_response, serve_replay_handler
from test.support.public_api import delete_agent
from yuxi.modules.extensions.mcp.oauth_config import config_revision
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.self_managed_resources]


@pytest.mark.parametrize("mode", ["unconnected", "wrong_key", "unreachable"])
@pytest.mark.parametrize("with_skill", [False, True])
async def test_unavailable_mcp_preserves_chat_and_reports_reason(e2e_client, e2e_headers, mode, with_skill):
    """直接选择 MCP 的故障不阻断聊天；普通 Skill 仅提供说明，不参与工具授权。"""
    client, headers = e2e_client, e2e_headers
    suffix = uuid4().hex[:10]
    slug, agent, provider = (prefix + suffix for prefix in ("pytest-mcp-failure-", "pytest-agent-", "mcp-failure-"))
    observed, thread, turn_id = [], None, None
    db = await asyncpg.connect(postgres_dsn())

    class Handler(BaseHTTPRequestHandler):
        """独立检查实际模型协议中的能力与原因。"""

        def do_POST(self):
            """只在提示准确且故障工具未暴露时返回正常聊天结果。"""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            system = str([message for message in body["messages"] if message["role"] == "system"])
            assert "secret-canary" not in system and "TaskGroup" not in system
            names = {item["function"]["name"] for item in body.get("tools", [])}
            assert "query_my_attendance" not in names
            if with_skill:
                assert "ocr_parse_file" not in names
            observed.append(system)
            common = {"id": "mcp-failure", "object": "chat.completion.chunk", "created": 1, "model": "deterministic-chat"}
            send_openai_sse_response(
                self,
                [
                    {
                        **common,
                        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "普通聊天正常。"}, "finish_reason": None}],
                    },
                    {**common, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ],
            )

        def log_message(self, *_):
            """不输出请求内容。"""

    with serve_replay_handler(Handler) as url:
        try:
            await register_replay_provider(client, headers, base_url=url, provider_id=provider)
            config = {
                "slug": slug,
                "name": "MCP 故障验收",
                "url": "http://127.0.0.1:1/mcp" if mode == "unreachable" else "https://mcp.example.com/mcp",
                "transport": "streamable_http",
                "auth_mode": "headers" if mode == "unreachable" else "oauth",
                "timeout": 1,
            }
            if mode != "unreachable":
                config["oauth_config"] = {"issuer_url": "https://auth.example.com", "client_id": "fixture-client"}
            response = await client.post("/api/system/mcp-servers", headers=headers, json=config)
            assert response.status_code == 200, response.text
            await db.execute("UPDATE mcp_servers SET enabled=1 WHERE slug=$1", slug)
            if mode == "wrong_key":
                row = dict(await db.fetchrow("SELECT * FROM mcp_servers WHERE slug=$1", slug))
                for field in ("headers", "oauth_config"):
                    if isinstance(row[field], str):
                        row[field] = json.loads(row[field])
                me = await client.get("/api/auth/me", headers=headers)
                assert me.status_code == 200
                credentials = Fernet(Fernet.generate_key()).encrypt(b'{"access_token":"secret-canary"}').decode()
                await db.execute(
                    "INSERT INTO mcp_connections (server_id,user_id,status,generation,config_revision,credentials,updated_at) "
                    "VALUES ($1,(SELECT id FROM users WHERE uid=$2),'connected',1,$3,$4,$5)",
                    row["id"],
                    me.json()["uid"],
                    config_revision(SimpleNamespace(**row)),
                    credentials,
                    utc_now(),
                )
            payload = {
                "slug": agent,
                "name": "MCP 故障隔离",
                "backend_id": "ChatbotAgent",
                "config_json": {
                    "context": {
                        "model": provider + ":deterministic-chat",
                        "mcps": [slug],
                        "tools": [],
                        "knowledges": [],
                        "skills": [],
                        "preload_skills": [],
                        "system_prompt": "正常聊天并告知能力限制。",
                    }
                },
            }
            if with_skill:
                package = BytesIO()
                with ZipFile(package, "w") as archive:
                    archive.writestr(
                        "SKILL.md",
                        "---\nslug: mcp-fixture\nname: MCP fixture\ndescription: MCP failure fixture\n---\n查询 MCP。\n",
                    )
                response = await client.post(
                    "/api/agent/with-skill",
                    headers=headers,
                    data={"agent": json.dumps(payload)},
                    files={"file": ("mcp-fixture.zip", package.getvalue(), "application/zip")},
                )
            else:
                response = await client.post("/api/agent", headers=headers, json=payload)
            assert response.status_code == 200, response.text
            response = await client.post(
                "/api/v1/agents/sessions",
                headers={**headers, "Idempotency-Key": suffix},
                json={"agent_id": agent, "title": "pytest-mcp-failure-" + suffix, "input": [public_message("你好")]},
            )
            assert response.status_code == 201, response.text
            accepted = await wait_for_consumed_input(client, headers, response.json()["yuxi"]["receipt"])
            thread, turn_id = accepted["session_id"], accepted["turn_id"]
            turn = await wait_for_terminal_turn(client, headers, thread, accepted["turn_id"])
            run = accepted["run_id"]
            assert turn["status"] == "completed", turn
            assert turn["yuxi"]["result_run_id"] == run and "普通聊天正常" in output_text(turn["yuxi"]["output"])
            record = await db.fetchrow("SELECT status,error_message FROM agent_runs WHERE id=$1", run)
            assert record["status"] == "completed" and not record["error_message"]
            assert observed
            response = await client.get(f"/api/v1/agents/sessions/{thread}/state", headers=headers)
            assert response.status_code == 200
            warnings = response.json()["agent_state"]["warnings"]
            if mode == "unreachable":
                assert warnings == []
            else:
                assert len(warnings) == 1
                assert warnings[0]["code"] == "mcp_unavailable"
                assert warnings[0]["source"] == f"mcp:{slug}"
                warning = warnings[0]["message"]
                assert slug in warning and "secret-canary" not in warning and "TaskGroup" not in warning
                if mode == "wrong_key":
                    assert "重新连接" in warning
            async with Redis.from_url(os.environ["REDIS_URL"], decode_responses=True) as redis:
                rows = await redis.xrange(f"run:events:v2:{run}")
            events = [json.loads(fields["event"]) for _, fields in rows]
            states = [event["agent_state"] for event in events if event["type"] == "yuxi.session.turn.state"]
            assert any(state.get("warnings") == warnings for state in states)
            assert not any(event["type"] == "yuxi.session.turn.capability_limited" for event in events)
        finally:
            try:
                if thread:
                    await archive_public_thread(client, headers, thread, turn_id=turn_id)
                await delete_agent(client, headers, agent)
                response = await client.delete(f"/api/system/mcp-servers/{slug}", headers=headers)
                assert response.status_code in (200, 404), response.text
                assert not await db.fetchval("SELECT count(*) FROM mcp_servers WHERE slug=$1", slug)
                response = await client.delete(f"/api/system/model-providers/{provider}", headers=headers)
                assert response.status_code in (200, 404), response.text
            finally:
                await db.close()
