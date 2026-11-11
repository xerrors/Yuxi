"""连接器选择经过 Public 接收、worker、真实 MCP 协议与后续会话。"""

import json
from http.server import BaseHTTPRequestHandler
from uuid import uuid4

import asyncpg
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from test.e2e.e2e_helpers import (
    archive_public_thread,
    output_text,
    postgres_dsn,
    public_message,
    register_replay_provider,
    wait_for_consumed_input,
    wait_for_terminal_turn,
)
from test.integration.api.helpers import create_api_user, delete_created_threads, delete_user_by_id
from test.support.openai_replay_server import send_json_response, send_openai_sse_response, serve_replay_handler
from test.support.public_api import delete_agent

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.self_managed_resources]


async def test_session_connectors_persist_and_execute_with_current_users_credentials(e2e_client, e2e_headers, standard_user):
    """预配置与追加工具共同调用，持久选择隔离会话且不固化授权。"""
    client, root = e2e_client, e2e_headers
    owner, owner_id = None, None
    other = standard_user["headers"]
    suffix = uuid4().hex[:10]
    base_slug, extra_slug, agent, provider = [
        prefix + suffix
        for prefix in (
            "pytest-configured-",
            "pytest-selected-",
            "pytest-session-mcp-",
            "session-mcp-replay-",
        )
    ]
    owner_secret, other_secret = "fixture-owner-" + suffix, "fixture-other-" + suffix
    owner_identity, other_identity = "owner-" + suffix, "other-" + suffix
    observed, threads = [], {}
    expected_identity, round_id, key_id = None, "", None
    db = await asyncpg.connect(postgres_dsn())

    class Handler(BaseHTTPRequestHandler):
        """MCP 核对独立凭据，模型只使用当前轮实际可见工具与返回值。"""

        def do_POST(self):
            """固定协议 oracle 验证工具集合、调用身份与当前轮结果。"""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/mcp"):
                personal = self.path.endswith("/personal/mcp")
                if personal:
                    identity = {"Bearer " + owner_secret: owner_identity, "Bearer " + other_secret: other_identity}.get(
                        self.headers.get("Authorization")
                    )
                    if identity is None:
                        send_json_response(self, 401, {})
                        return
                else:
                    assert self.headers.get("Authorization") is None
                    identity = "configured"
                if "id" not in body:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                name = "selected_identity" if personal else "configured_identity"
                method = body["method"]
                if method == "initialize":
                    result = {
                        "protocolVersion": body["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": name, "version": "1"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": name,
                                "description": "返回调用者身份与本轮标记",
                                "inputSchema": {"type": "object", "properties": {"round": {"type": "string"}}, "required": ["round"]},
                            }
                        ]
                    }
                else:
                    assert method == "tools/call" and body["params"]["name"] == name
                    marker = body["params"]["arguments"]["round"]
                    observed.append((identity, marker))
                    result = {"content": [{"type": "text", "text": f"{identity}:{marker}"}], "isError": False}
                send_json_response(self, 200, {"jsonrpc": "2.0", "id": body["id"], "result": result})
                return

            assert owner_secret not in json.dumps(body) and other_secret not in json.dumps(body)
            names = {item["function"]["name"] for item in body.get("tools", [])}
            configured = next((name for name in names if "configured_identity" in name), None)
            selected = next((name for name in names if "selected_identity" in name), None)
            if configured is None or bool(selected) != bool(expected_identity):
                send_json_response(self, 400, {"error": {"message": "当前工具集合与会话选择或本人连接不符"}})
                return
            last_user = max(index for index, message in enumerate(body["messages"]) if message["role"] == "user")
            tools = [message for message in body["messages"][last_user + 1 :] if message["role"] == "tool"]
            if tools:
                actual = " ".join(str(message["content"]) for message in tools)
                assert "configured:" + round_id in actual
                assert expected_identity is None or expected_identity + ":" + round_id in actual
                delta, finish = {"role": "assistant", "content": "CONNECTORS_OK:" + actual}, "stop"
            else:
                calls = [configured, *([selected] if selected else [])]
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": index,
                            "id": f"connector-{round_id}-{index}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps({"round": round_id})},
                        }
                        for index, name in enumerate(calls)
                    ],
                }
                finish = "tool_calls"
            common = {"id": "connectors", "object": "chat.completion.chunk", "created": 1, "model": "deterministic-chat"}
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
            """不输出请求、身份与凭据。"""

    async def submit(headers, *, thread=None, mcps=None, identity=None):
        """通过真实 Public 请求执行一轮，并核对目标 Run 和远端调用。"""
        nonlocal expected_identity, round_id
        expected_identity, round_id = identity, uuid4().hex[:8]
        before = len(observed)
        options = {} if mcps is None else {"mcps": mcps}
        if thread is None:
            response = await client.post(
                "/api/v1/agents/sessions",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json={
                    "agent_id": agent,
                    "title": "pytest-session-connectors-" + suffix,
                    "input": [public_message("读取本轮身份 " + round_id)],
                    "yuxi": options,
                    "tool_approval_mode": "always_trust",
                },
            )
            assert response.status_code == 201, response.text
            thread = response.json()["id"]
            threads[thread] = (headers, None)
            receipt = response.json()["yuxi"]["receipt"]
        else:
            response = await client.post(
                f"/api/v1/agents/sessions/{thread}/events",
                headers={**headers, "Idempotency-Key": str(uuid4())},
                json={
                    "events": [
                        {
                            "type": "agent.session.input.message",
                            "input": [public_message("读取本轮身份 " + round_id)],
                            "yuxi": {"mode": "follow_up", **options},
                        }
                    ]
                },
            )
            assert response.status_code == 202, response.text
            receipt = response.json()
        accepted = await wait_for_consumed_input(client, headers, receipt)
        threads[thread] = (headers, accepted["turn_id"])
        turn = await wait_for_terminal_turn(client, headers, thread, accepted["turn_id"])
        assert turn["status"] == "completed", turn
        assert turn["yuxi"]["result_run_id"] == accepted["run_id"]
        assert "configured:" + round_id in output_text(turn["yuxi"]["output"])
        expected = [("configured", round_id), *([(identity, round_id)] if identity else [])]
        assert sorted(observed[before:]) == sorted(expected)
        record = await db.fetchrow("SELECT status,error_message FROM agent_runs WHERE id=$1", accepted["run_id"])
        assert record["status"] == "completed" and record["error_message"] is None
        return thread

    with serve_replay_handler(Handler) as replay:
        try:
            password = "Fixture-" + suffix + "!"
            user = await create_api_user(
                client,
                root,
                {
                    "username": "mcpowner" + suffix,
                    "password": password,
                    "role": "user",
                    "department_id": standard_user["user"]["department_id"],
                },
            )
            owner_id = user["id"]
            login = await client.post("/api/auth/token", data={"username": user["uid"], "password": password})
            assert login.status_code == 200
            owner = {"Authorization": "Bearer " + login.json()["access_token"]}
            await register_replay_provider(client, root, base_url=replay, provider_id=provider)
            for slug, path, mode in ((base_slug, "/configured/mcp", "none"), (extra_slug, "/personal/mcp", "bearer")):
                # 本例的 HTTP fixture 只验证真实运行协议；管理入口的 HTTPS 边界由集成测试拥有。
                await db.execute(
                    "INSERT INTO mcp_servers (slug,name,url,transport,auth_mode,headers,enabled,created_by,updated_by) "
                    "VALUES ($1,$1,$2,'streamable_http',$3,'{}',1,'fixture','fixture')",
                    slug,
                    replay + path,
                    mode,
                )
            configured = await client.get("/api/system/mcp-servers/" + extra_slug, headers=root)
            revision = configured.json()["data"]["config_revision"]
            saved = await client.put(
                f"/api/system/mcp-servers/{extra_slug}/connection",
                headers=owner,
                json={"secret": owner_secret, "config_revision": revision},
            )
            assert saved.status_code == 200, saved.text
            created = await client.post(
                "/api/agent",
                headers=root,
                json={
                    "slug": agent,
                    "name": "会话连接器验收",
                    "backend_id": "ChatbotAgent",
                    "visibility": "shared",
                    "share_config": {"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
                    "config_json": {
                        "context": {
                            "model": provider + ":deterministic-chat",
                            "mcps": [base_slug],
                            "tools": [],
                            "knowledges": [],
                            "skills": [],
                            "preload_skills": [],
                        }
                    },
                },
            )
            assert created.status_code == 200, created.text
            issued = await client.post(
                "/api/user/apikey/",
                headers=owner,
                json={"request_id": str(uuid4()), "name": "Session connectors", "access_level": "agents"},
            )
            assert issued.status_code == 200, issued.text
            key_id = issued.json()["api_key"]["id"]
            personal_key = {"Authorization": "Bearer " + issued.json()["secret"]}
            thread = await submit(personal_key)
            await submit(owner, thread=thread, mcps=[extra_slug, base_slug, extra_slug], identity=owner_identity)
            await submit(owner, thread=thread, identity=owner_identity)
            await submit(owner)
            other_thread = await submit(other, mcps=[extra_slug])
            saved = await client.put(
                f"/api/system/mcp-servers/{extra_slug}/connection",
                headers=other,
                json={"secret": other_secret, "config_revision": revision},
            )
            assert saved.status_code == 200, saved.text
            await submit(other, thread=other_thread, identity=other_identity)
            assert (await client.delete(f"/api/system/mcp-servers/{extra_slug}/connection", headers=owner)).status_code == 200
            await submit(owner, thread=thread)
            assert (
                await client.put(f"/api/system/mcp-servers/{extra_slug}/status", headers=root, json={"enabled": False})
            ).status_code == 200
            await submit(other, thread=other_thread)
            stored = await db.fetchval("SELECT config_snapshot FROM sessions WHERE thread_id=$1", thread)
            assert json.loads(stored)["mcps"] == [base_slug, extra_slug]
            definition = json.loads(await db.fetchval("SELECT config_json FROM agents WHERE slug=$1", agent))
            assert definition["context"]["mcps"] == [base_slug]
        finally:
            try:
                for thread, (headers, turn_id) in threads.items():
                    await archive_public_thread(client, headers, thread, turn_id=turn_id)
                await delete_created_threads(*threads)
                async with AsyncPostgresSaver.from_conn_string(postgres_dsn()) as saver:
                    for thread in threads:
                        await saver.adelete_thread(thread)
                assert not await db.fetchval("SELECT count(*) FROM sessions WHERE thread_id=ANY($1::text[])", list(threads))
                assert not await db.fetchval("SELECT count(*) FROM checkpoints WHERE thread_id=ANY($1::text[])", list(threads))
                await delete_agent(client, root, agent)
                if key_id is not None:
                    assert (await client.delete(f"/api/user/apikey/{key_id}", headers=owner)).status_code in {200, 404}
                for slug in (extra_slug, base_slug):
                    assert (await client.delete("/api/system/mcp-servers/" + slug, headers=root)).status_code in {200, 404}
                assert (await client.delete("/api/system/model-providers/" + provider, headers=root)).status_code in {200, 404}
                if owner_id is not None:
                    await delete_user_by_id(client, root, owner_id)
            finally:
                await db.close()
