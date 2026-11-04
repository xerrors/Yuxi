"""真实 HTTP、worker 与 Cube 验证知识库失败和搜索结果语义。"""

import asyncio
import json
import os
import re
import shlex
import uuid
from contextlib import suppress
from http.server import BaseHTTPRequestHandler

import asyncpg
import pytest

from test.e2e.e2e_helpers import (
    archive_public_thread,
    output_text,
    postgres_dsn,
    register_replay_provider,
    wait_for_consumed_input,
    wait_for_terminal_turn,
)
from test.integration.api.helpers import get_thread_workdir_path
from test.live_api_cleanup import make_test_session_title
from test.support.openai_replay_server import serve_replay_handler
from yuxi.modules.agents.runtime.sandbox.backend import UserSandboxBackend
from yuxi.modules.workspace.filesystem import Workspace

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.self_managed_resources, pytest.mark.timeout(240)]


async def test_worker_persists_knowledge_error_and_consistent_grep(e2e_client, e2e_headers):
    """确定性模型只指定调用，文件、公开结果与审计独立证明最终行为。"""
    suffix = uuid.uuid4().hex
    provider_id = f"p2-replay-{suffix[:8]}"
    slug = f"p2-tools-{suffix[:8]}"
    guest_path = f"/tmp/yuxi-p2-{suffix}.txt"
    observed = {}

    class Handler(BaseHTTPRequestHandler):
        """经真实模型协议发出工具调用并保留模型实际接收的结果。"""

        def do_POST(self):
            """使用调用 ID 推进固定脚本，结果断言留给测试主任务。"""
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            for message in request["messages"]:
                if message.get("role") == "tool":
                    observed[message["tool_call_id"]] = message["content"]
            workdir = re.search(r"当前 Project Workdir 为 ([^，\s]+)", json.dumps(request["messages"], ensure_ascii=False)).group(1)
            mounted_path = f"{workdir}/matches.txt"
            script = f"from pathlib import Path; [Path(p).write_text('needle\\nneedle\\n') for p in {[mounted_path, guest_path]!r}]"
            calls = [
                ("execute", {"command": "python3 -c " + shlex.quote(script)}),
                ("grep", {"pattern": "needle", "path": mounted_path, "max_count": 2, "output_mode": "content"}),
                ("grep", {"pattern": "needle", "path": guest_path, "max_count": 2, "output_mode": "content"}),
                ("grep", {"pattern": "needle", "path": mounted_path, "max_count": 1, "output_mode": "content"}),
                ("grep", {"pattern": "needle", "path": guest_path, "max_count": 1, "output_mode": "content"}),
                ("query_kb", {"kb_id": "p2-unavailable-kb", "query_text": "needle"}),
            ]
            step = len(observed)
            if step < len(calls):
                name, args = calls[step]
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call-p2-{step}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "P2_CONSISTENCY_OK"}
                finish = "stop"
            common = {"id": "chatcmpl-p2", "object": "chat.completion.chunk", "created": 1, "model": request["model"]}
            chunks = [
                {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {**common, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            ]
            body = ("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            """不记录请求中的用户上下文。"""

    thread_id = turn_id = workdir = None
    me = await e2e_client.get("/api/auth/me", headers=e2e_headers)
    assert me.status_code == 200, me.text
    uid = str(me.json()["uid"])
    with serve_replay_handler(Handler) as replay_url:
        try:
            await register_replay_provider(e2e_client, e2e_headers, base_url=replay_url, provider_id=provider_id)
            agent = await e2e_client.post(
                "/api/agent",
                headers=e2e_headers,
                json={
                    "name": slug,
                    "slug": slug,
                    "backend_id": "ChatbotAgent",
                    "visibility": "private",
                    "config_json": {
                        "context": {
                            "model": f"{provider_id}:deterministic-chat",
                            "tools": [],
                            "knowledges": [],
                            "mcps": [],
                            "skills": ["knowledge-base"],
                            "preload_skills": ["knowledge-base"],
                        }
                    },
                },
            )
            assert agent.status_code == 200, agent.text
            response = await e2e_client.post(
                "/api/v1/agents/sessions",
                headers={**e2e_headers, "Idempotency-Key": suffix},
                json={
                    "agent_id": slug,
                    "tool_approval_mode": "always_trust",
                    "input": "P2_TOOL_CONSISTENCY",
                    "title": make_test_session_title("p2-tools"),
                },
            )
            assert response.status_code == 201, response.text
            thread_id = response.json()["id"]
            workdir = await get_thread_workdir_path(thread_id)
            accepted = await wait_for_consumed_input(e2e_client, e2e_headers, response.json()["yuxi"]["receipt"])
            turn_id = accepted["turn_id"]
            turn = await wait_for_terminal_turn(e2e_client, e2e_headers, thread_id, turn_id)
            assert turn["status"] == "completed", turn
            assert "P2_CONSISTENCY_OK" in output_text(turn["yuxi"]["output"])
            assert set(observed) == {f"call-p2-{index}" for index in range(6)}
            for index, count, truncated in [(1, 2, False), (2, 2, False), (3, 1, True), (4, 1, True)]:
                text = str(observed[f"call-p2-{index}"])
                assert text.count("needle") == count, text
                assert ("matches above are valid but incomplete" in text) is truncated, text
            knowledge_error = str(observed["call-p2-5"])
            assert "Knowledge tool failed (404)" in knowledge_error, knowledge_error
            assert "资源不存在或不可见" in knowledge_error, knowledge_error
            conn = await asyncpg.connect(postgres_dsn())
            try:
                audit = await conn.fetchrow(
                    "SELECT execution_status,extra_metadata,turn_id FROM messages "
                    "WHERE run_id=$1 AND message_type='tool_audit' AND operation_id='call-p2-5'",
                    accepted["run_id"],
                )
            finally:
                await conn.close()
            assert audit and audit["execution_status"] == "failed"
            assert audit["turn_id"] == turn_id
            assert json.loads(audit["extra_metadata"])["output"]["status"] == "error"
            downloaded = await e2e_client.get(
                f"/api/v1/agents/sessions/{thread_id}/artifacts/home/user/workspace/{workdir}/matches.txt?download=true",
                headers=e2e_headers,
            )
            assert downloaded.status_code == 200, downloaded.text
            assert downloaded.content == b"needle\nneedle\n"
        finally:
            if thread_id:
                await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
                if os.environ.get("SANDBOX_BACKEND", "cube") == "cube":
                    cleanup = UserSandboxBackend(uid=uid).execute(
                        f"rm -f -- {shlex.quote(guest_path)} && test ! -e {shlex.quote(guest_path)}"
                    )
                    assert cleanup.exit_code == 0, cleanup.output
                else:
                    # container 的 guest 文件属于已终态 Run，持久 Project 文件归 Workspace 清理。
                    async with asyncio.timeout(30):
                        while True:
                            status = await e2e_client.get("/api/user/sandbox", headers=e2e_headers)
                            assert status.status_code == 200, status.text
                            state = status.json()
                            assert state["backend"] == "container", state
                            if not state["active_runs"] and not state["busy"] and state["state"] == "not_created":
                                break
                            await asyncio.sleep(0.1)
                if workdir:
                    filesystem = Workspace(uid)
                    root = f"/{workdir.strip('/')}"
                    path = f"{root}/matches.txt"
                    with suppress(FileNotFoundError):
                        filesystem.delete_authorized_path(path, root=root)
                    with pytest.raises(FileNotFoundError):
                        filesystem.stat_authorized_path(path, root=root)
            removed = await e2e_client.delete(f"/api/agent/{slug}", headers=e2e_headers)
            assert removed.status_code in {200, 404}, removed.text
            removed = await e2e_client.delete(f"/api/system/model-providers/{provider_id}", headers=e2e_headers)
            assert removed.status_code in {200, 404}, removed.text
