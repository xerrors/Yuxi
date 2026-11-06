"""个人静态凭证经过实际 API、worker 和工具协议的确定性验收。"""

import json
from http.server import BaseHTTPRequestHandler
from uuid import uuid4

import asyncpg
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

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.self_managed_resources]


@pytest.mark.parametrize("mode", ["bearer", "api_key"])
async def test_personal_credentials_reach_worker_with_own_saved_secret(e2e_client, e2e_headers, mode):
    """工具独立核对秘密，模型只转述真实返回，最终结果绑定 result Run。"""
    client, headers = e2e_client, e2e_headers
    suffix = uuid4().hex[:10]
    slug, agent, provider = ("pytest-personal-" + suffix, "pytest-personal-agent-" + suffix, "personal-replay-" + suffix)
    secret, identity = "fixture-personal-secret-" + suffix, "fixture-personal-identity-" + suffix
    observed, thread, turn_id = [], None, None
    db = await asyncpg.connect(postgres_dsn())

    class Handler(BaseHTTPRequestHandler):
        """远端凭证 oracle 与模型 wire oracle 共享一次性端口。"""

        def do_POST(self):
            """MCP 核对认证头，模型核对实际工具结果且不能看到秘密。"""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/mcp"):
                header = "Authorization" if mode == "bearer" else "X-API-Key"
                expected = "Bearer " + secret if mode == "bearer" else secret
                if self.headers.get(header) != expected:
                    send_json_response(self, 401, {})
                    return
                if "id" not in body:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                method = body["method"]
                if method == "initialize":
                    result = {
                        "protocolVersion": body["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "personal-fixture", "version": "1"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "personal_identity",
                                "description": "读取本人身份",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                        ]
                    }
                else:
                    assert method == "tools/call" and body["params"]["name"] == "personal_identity"
                    observed.append(identity)
                    result = {"content": [{"type": "text", "text": identity}], "isError": False}
                send_json_response(self, 200, {"jsonrpc": "2.0", "id": body["id"], "result": result})
                return
            assert secret not in json.dumps(body)
            tools = [message for message in body["messages"] if message["role"] == "tool"]
            if tools:
                assert identity in str(tools[-1]["content"])
                delta, finish = {"role": "assistant", "content": "PERSONAL_MCP_OK:" + identity}, "stop"
            else:
                name = next(item["function"]["name"] for item in body["tools"] if "personal_identity" in item["function"]["name"])
                delta = {
                    "role": "assistant",
                    "tool_calls": [{"index": 0, "id": "personal-call", "type": "function", "function": {"name": name, "arguments": "{}"}}],
                }
                finish = "tool_calls"
            common = {"id": "personal", "object": "chat.completion.chunk", "created": 1, "model": "deterministic-chat"}
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
            """测试不输出身份、凭据或请求正文。"""

    with serve_replay_handler(Handler) as replay:
        try:
            await register_replay_provider(client, headers, base_url=replay, provider_id=provider)
            # 一次性 HTTP fixture 直接登记可信连接，不修改部署的实验来源白名单。
            # HTTPS 配置边界由 integration 单独证明；此处验证跨 worker 凭证与结果。
            await db.execute(
                "INSERT INTO mcp_servers (slug,name,url,transport,auth_mode,api_key_header,headers,enabled,created_by,updated_by) "
                "VALUES ($1,'个人凭证验收',$2,'streamable_http',$3,$4,'{}',1,'fixture','fixture')",
                slug,
                replay + "/mcp",
                mode,
                "X-API-Key" if mode == "api_key" else None,
            )
            configured = await client.get("/api/system/mcp-servers/" + slug, headers=headers)
            assert configured.status_code == 200
            saved = await client.put(
                "/api/system/mcp-servers/" + slug + "/connection",
                headers=headers,
                json={
                    "secret": secret,
                    "config_revision": configured.json()["data"]["config_revision"],
                },
            )
            assert saved.status_code == 200
            created = await client.post(
                "/api/agent",
                headers=headers,
                json={
                    "slug": agent,
                    "name": "个人凭证验收",
                    "backend_id": "ChatbotAgent",
                    "config_json": {
                        "context": {
                            "model": provider + ":deterministic-chat",
                            "mcps": [slug],
                            "tools": [],
                            "knowledges": [],
                            "skills": [],
                            "preload_skills": [],
                        }
                    },
                },
            )
            assert created.status_code == 200, created.text
            response = await client.post(
                "/api/v1/agents/sessions",
                headers={**headers, "Idempotency-Key": suffix},
                json={
                    "agent_id": agent,
                    "title": "pytest-personal-" + suffix,
                    "input": [public_message("读取本人身份")],
                },
            )
            assert response.status_code == 201, response.text
            accepted = await wait_for_consumed_input(client, headers, response.json()["yuxi"]["receipt"])
            thread, turn_id = accepted["session_id"], accepted["turn_id"]
            turn = await wait_for_terminal_turn(client, headers, thread, turn_id)
            assert turn["status"] == "completed" and identity in output_text(turn["yuxi"]["output"])
            run = turn["yuxi"]["result_run_id"]
            assert run == accepted["run_id"] and observed == [identity]
            record = await db.fetchrow("SELECT status,error_message FROM agent_runs WHERE id=$1", run)
            assert record["status"] == "completed" and record["error_message"] is None
        finally:
            try:
                if thread:
                    await archive_public_thread(client, headers, thread, turn_id=turn_id)
                await delete_agent(client, headers, agent)
                await client.delete("/api/system/mcp-servers/" + slug, headers=headers)
                await client.delete("/api/system/model-providers/" + provider, headers=headers)
            finally:
                await db.close()
