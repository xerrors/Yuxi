"""真实 container 后端的 API/worker/runner E2E。

确定性 OpenAI replay 让真实 worker 创建 Run 并调用 `execute`；宿主文件通过公开
Viewer API 回读，Run 终态后用户沙盒状态必须回到无容器状态。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import asyncpg
import httpx
import pytest

from test.e2e.conftest import E2E_BASE_URL, E2E_PASSWORD, E2E_USERNAME
from test.support.public_api import delete_agent
from test.support.postgres import postgres_dsn

pytestmark = [pytest.mark.self_managed_resources, pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.timeout(240)]

MARKER = "container-e2e-ok"
COMMAND = (
    "test ! -e /home/user/previous-run && touch /home/user/previous-run "
    f"&& hostname >> containers.txt && printf '{MARKER}' > e2e.txt; pwd; id"
)
DONE = "CONTAINER_E2E_DONE"


class ReplayHandler(BaseHTTPRequestHandler):
    """主 Run 返回 execute 工具调用；标题等其它请求返回普通文本。"""

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = payload.get("messages") or []
        tool_names = {item.get("function", {}).get("name") for item in payload.get("tools") or [] if isinstance(item, dict)}
        user_text = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content")
                user_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                break
        last_user = max((index for index, message in enumerate(messages) if message.get("role") == "user"), default=-1)
        tool_messages = [message for message in messages[last_user + 1 :] if message.get("role") == "tool"]
        if "execute" in tool_names and MARKER in user_text and not tool_messages:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-container-" + uuid.uuid4().hex,
                        "type": "function",
                        "function": {"name": "execute", "arguments": json.dumps({"command": COMMAND})},
                    }
                ],
            }
            finish = "tool_calls"
        elif "execute" in tool_names and MARKER in user_text:
            if self.server.handoff and not self.server.started.is_set():
                self.server.started.set()
                if not self.server.release.wait(90):
                    self.send_error(504, "steer gate timed out")
                    return
            delta = {"role": "assistant", "content": DONE}
            finish = "stop"
        else:
            delta = {"role": "assistant", "content": "Container E2E"}
            finish = "stop"
        common = {
            "id": "chatcmpl-container-e2e",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": payload.get("model", "container-chat"),
        }
        events = [
            {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {
                **common,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        ]
        raw = ("".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        """replay 不记录模型请求。"""


@pytest.fixture
def replay_server():
    """启动确定性 OpenAI HTTP 服务，并给出 API 容器可达的地址。"""
    server = ThreadingHTTPServer(("0.0.0.0", 0), ReplayHandler)
    server.handoff = False
    server.started = threading.Event()
    server.release = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    template = os.getenv("CONTAINER_E2E_REPLAY_URL_TEMPLATE", "http://host.docker.internal:{port}/v1")
    try:
        yield template.format(port=server.server_port), server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def wait_terminal(client: httpx.AsyncClient, headers: dict, thread_id: str, turn_id: str) -> dict:
    """等待 Turn 终态并返回持久结果。"""
    async with asyncio.timeout(180):
        while True:
            response = await client.get(f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}", headers=headers)
            assert response.status_code == 200, response.text
            value = response.json()
            if value["status"] in {"completed", "failed", "cancelled"}:
                return value
            await asyncio.sleep(0.1)


async def wait_input(client: httpx.AsyncClient, headers: dict, receipt: dict) -> dict:
    """等待真实 Input 被 worker 领取。"""
    async with asyncio.timeout(90):
        while True:
            response = await client.get(
                f"/api/v1/agents/sessions/{receipt['session_id']}/inputs/{receipt['input_id']}",
                headers=headers,
            )
            assert response.status_code == 200, response.text
            value = response.json()
            if value["status"] == "consumed":
                return value
            assert value["status"] != "cancelled", value
            await asyncio.sleep(0.1)


async def wait_no_container(client: httpx.AsyncClient, headers: dict) -> dict:
    """Run 终态后等待用户沙盒回到无活动容器的状态。"""
    async with asyncio.timeout(60):
        while True:
            response = await client.get("/api/user/sandbox", headers=headers)
            assert response.status_code == 200, response.text
            value = response.json()
            assert value.get("backend") == "container", value
            if not value.get("active_runs") and not value.get("busy") and value.get("state") == "not_created":
                return value
            await asyncio.sleep(0.25)


@pytest.mark.parametrize("handoff", [False, True])
async def test_agent_execute_runs_in_ephemeral_container(replay_server, handoff):
    """真实 Agent Run 通过 execute 写文件，Viewer API 回读，容器随终态清理。"""
    if not E2E_USERNAME or not E2E_PASSWORD:
        pytest.skip("需要 E2E_USERNAME / E2E_PASSWORD")
    replay_url, replay = replay_server
    replay.handoff = handoff
    base_url = E2E_BASE_URL.rstrip("/")
    provider_id = "container-e2e-" + uuid.uuid4().hex[:8]
    slug = "container-e2e-" + uuid.uuid4().hex[:8]
    async with httpx.AsyncClient(base_url=base_url, timeout=120, trust_env=False) as client:
        login = await client.post("/api/auth/token", data={"username": E2E_USERNAME, "password": E2E_PASSWORD})
        assert login.status_code == 200, login.text
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        try:
            status = await client.get("/api/user/sandbox", headers=headers)
            assert status.status_code == 200, status.text
            assert status.json().get("backend") == "container", status.text

            provider = await client.post(
                "/api/system/model-providers",
                headers=headers,
                json={
                    "provider_id": provider_id,
                    "display_name": "Container E2E replay",
                    "provider_type": "openai",
                    "base_url": replay_url,
                    "api_key": "container-test-key",
                    "capabilities": ["chat"],
                    "enabled_models": [{"id": "container-chat", "display_name": "Container chat", "type": "chat", "source": "manual"}],
                    "is_enabled": True,
                },
            )
            assert provider.status_code == 200, provider.text
            await asyncio.sleep(6)
            agent = await client.post(
                "/api/agent",
                headers=headers,
                json={
                    "name": "Container E2E",
                    "slug": slug,
                    "backend_id": "ChatbotAgent",
                    "visibility": "private",
                    "config_json": {
                        "context": {
                            "model": f"{provider_id}:container-chat",
                            "tools": [],
                            "skills": [],
                            "preload_skills": [],
                            "knowledges": [],
                            "mcps": [],
                        }
                    },
                },
            )
            assert agent.status_code == 200, agent.text
            created = await client.post(
                "/api/v1/agents/sessions",
                headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
                json={
                    "agent_id": slug,
                    "tool_approval_mode": "always_trust",
                    "input": f"Use the terminal to run exactly this command: `{COMMAND}`. Then reply {DONE}.",
                    "title": "YUXI_TEST_SESSION_CONTAINER_E2E",
                },
            )
            assert created.status_code == 201, created.text
            session = created.json()
            thread_id = session["id"]
            consumed = await wait_input(client, headers, session["yuxi"]["receipt"])
            if handoff:
                assert await asyncio.to_thread(replay.started.wait, 60), "旧 Run 未执行命令并到达模型安全点"
                response = await client.post(
                    f"/api/v1/agents/sessions/{thread_id}/events",
                    headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
                    json={
                        "events": [
                            {
                                "type": "agent.session.input.message",
                                "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": MARKER}]}],
                                "yuxi": {"mode": "steer"},
                            }
                        ]
                    },
                )
                assert response.status_code == 202, response.text
                replay.release.set()
            turn = await wait_terminal(client, headers, thread_id, consumed["turn_id"])
            assert turn["status"] == "completed", turn
            output = turn["yuxi"]["output"]
            assert any(part.get("text") == DONE for item in output for part in item.get("content", []) if item.get("type") == "message"), (
                output
            )

            file_response = await client.get(
                "/api/viewer/filesystem/file",
                headers=headers,
                params={"thread_id": thread_id, "path": "/e2e.txt"},
            )
            assert file_response.status_code == 200, file_response.text
            assert file_response.json()["content"] == MARKER
            await wait_no_container(client, headers)
            containers = await client.get(
                "/api/viewer/filesystem/file", headers=headers, params={"thread_id": thread_id, "path": "/containers.txt"}
            )
            assert containers.status_code == 200, containers.text
            identities = containers.json()["content"].splitlines()
            assert len(identities) == len(set(identities)) == (2 if handoff else 1), identities
            connection = await asyncpg.connect(postgres_dsn())
            try:
                runs = await connection.fetch(
                    "SELECT id,status,resume_from_run_id,runtime_cleanup_pending FROM agent_runs WHERE thread_id=$1 ORDER BY created_at",
                    thread_id,
                )
                assert [row["status"] for row in runs] == (["yielded", "completed"] if handoff else ["completed"])
                assert all(not row["runtime_cleanup_pending"] for row in runs)
                assert turn["yuxi"]["result_run_id"] == runs[-1]["id"]
                if handoff:
                    assert runs[1]["resume_from_run_id"] == runs[0]["id"]
            finally:
                await connection.close()
        finally:
            replay.release.set()
            try:
                await delete_agent(client, headers, slug)
            except Exception:
                pass
            await client.delete(f"/api/system/model-providers/{provider_id}", headers=headers)
