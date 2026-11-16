"""真实模型协议、worker、成果目录和文件副本的端到端证据。"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shlex
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import PurePosixPath

import asyncpg
import pytest
import pytest_asyncio

from test.e2e.e2e_helpers import (
    archive_public_thread,
    create_lifecycle_agent,
    delete_agent,
    postgres_dsn,
    register_replay_provider,
    wait_for_consumed_input,
    wait_for_terminal_turn,
)
from test.live_api_cleanup import cleanup_test_chat_resources, make_test_session_title
from test.support.openai_replay_server import send_openai_sse_response, serve_replay_handler
from test.support.public_events import read_events
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.workspace.filesystem import Workspace
from yuxi.modules.workspace.paths import global_user_data_dir, user_workspace_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(180), pytest.mark.self_managed_resources]

EXPECTED_FILES = {
    "report.md": b"# Saved report\n\nIndependent deliverable bytes.\n",
    "chart.png": base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN7sAAAAASUVORK5CYII="),
    "clip.mp4": b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
}


@pytest_asyncio.fixture
async def e2e_headers(e2e_client):
    """创建临时管理员并正常登录，不依赖部署账号或输出凭据。"""
    uid = f"pytest_deliverables_admin_{uuid.uuid4().hex}"
    department_name = f"deliverables_{uuid.uuid4().hex[:16]}"
    password = f"Test!{uuid.uuid4().hex}"
    connection = await asyncpg.connect(postgres_dsn())
    department_id = user_id = None
    headers = None
    try:
        department_id = await connection.fetchval("INSERT INTO departments(name,created_at) VALUES($1,now()) RETURNING id", department_name)
        user_id = await connection.fetchval(
            "INSERT INTO users(uid,username,password_hash,role,user_kind,login_failed_count,is_deleted,created_at,department_id) "
            "VALUES($1,$1,$2,'superadmin','human',0,0,now(),$3) RETURNING id",
            uid,
            AuthUtils.hash_password(password),
            department_id,
        )
        login = await e2e_client.post("/api/auth/token", data={"username": uid, "password": password})
        assert login.status_code == 200, f"临时管理员登录失败: HTTP={login.status_code}"
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        yield headers
    finally:
        if headers:
            await cleanup_test_chat_resources(e2e_client, headers, owner_uid=uid)
        if user_id:
            await connection.execute("UPDATE users SET department_id=NULL WHERE id=$1 AND uid=$2", user_id, uid)
            await connection.execute("DELETE FROM users WHERE id=$1 AND uid=$2", user_id, uid)
        if department_id:
            if not await connection.fetchval("SELECT 1 FROM users WHERE department_id=$1 LIMIT 1", department_id):
                await connection.execute("DELETE FROM departments WHERE id=$1", department_id)
        await connection.close()


class DeliverablesReplay(BaseHTTPRequestHandler):
    """生成工具调用而不替代生产执行器、成果用例或持久化。"""

    def do_POST(self):
        """依次通过真实执行工具生成文件，再正式交付三个结果。"""
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["Authorization"] == "Bearer ci-replay-key"
        messages = request["messages"]
        last_user = max(index for index, message in enumerate(messages) if message["role"] == "user")
        tools = [message for message in messages[last_user:] if message["role"] == "tool"]
        tool_names = {item["function"]["name"] for item in request["tools"]}
        assert {"execute", "present_artifacts"} <= tool_names
        if "DELIVERABLES_FOLLOWUP" in json.dumps(messages, ensure_ascii=False) or len(tools) >= 2:
            delta = {"role": "assistant", "content": "DELIVERABLES_SAVED"}
            finish = "stop"
        else:
            if tools:
                workdir = re.search(r"当前 Project Workdir 为 ([^，\s]+)", json.dumps(messages, ensure_ascii=False)).group(1)
                name, call_id = "present_artifacts", "call-deliverables-publish"
                arguments = {"filepaths": [f"{workdir}/{name}" for name in EXPECTED_FILES]}
            else:
                encoded = {name: base64.b64encode(content).decode() for name, content in EXPECTED_FILES.items()}
                script = (
                    "import base64,json,pathlib; "
                    f"files=json.loads({json.dumps(json.dumps(encoded))}); "
                    "[pathlib.Path(name).write_bytes(base64.b64decode(content)) for name,content in files.items()]; "
                    "pathlib.Path('intermediate.tmp').write_text('not a deliverable')"
                )
                name, call_id = "execute", "call-deliverables-generate"
                arguments = {"command": f"python -c {shlex.quote(script)}"}
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
            finish = "tool_calls"
        common = {"id": "chatcmpl-deliverables", "object": "chat.completion.chunk", "created": 1, "model": request["model"]}
        send_openai_sse_response(
            self,
            [
                {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {
                    **common,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            ],
        )

    def log_message(self, *_args):
        """模型重放不记录请求正文与凭据。"""


@pytest.fixture
def deliverables_replay_url():
    """独立重放监听真实 HTTP，worker 通过 Compose 网络访问。"""
    with serve_replay_handler(DeliverablesReplay) as url:
        yield url


async def test_worker_publishes_independent_deliverables_and_followup_does_not_recopy(
    e2e_client, e2e_headers, standard_user, deliverables_replay_url
):
    """源文件改删、后续 Run 和会话归档都不改变已保存成果。"""
    uid = standard_user["user"]["uid"]
    headers = standard_user["headers"]
    provider_id = f"ci-deliverables-{uuid.uuid4().hex[:8]}"
    provider_created = await register_replay_provider(e2e_client, e2e_headers, base_url=deliverables_replay_url, provider_id=provider_id)
    slug = thread_id = turn_id = None
    snapshot_paths = []
    try:
        slug = await create_lifecycle_agent(
            e2e_client,
            e2e_headers,
            uid,
            tools=["present_artifacts"],
            model_spec=f"{provider_id}:deterministic-chat",
            bound_skill=True,
        )
        async with e2e_client.stream(
            "POST",
            "/api/v1/agents/sessions",
            headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
            json={
                "agent_id": slug,
                "input": "DELIVERABLES_BUILD",
                "title": make_test_session_title("deliverables-worker"),
                "tool_approval_mode": "always_trust",
                "stream": True,
            },
        ) as response:
            assert response.status_code == 201
            events = read_events(response)
            _, created = await anext(events)
            assert created["type"] == "agent.session.created"
            thread_id = created["session"]["id"]
            async with asyncio.timeout(90):
                async for _, event in events:
                    if event["type"] in {"agent.session.turn.completed", "agent.session.turn.failed", "agent.session.turn.cancelled"}:
                        turn = event["turn"]
                        turn_id = turn["id"]
                        assert turn["status"] == "completed", turn
                        break
                else:
                    pytest.fail("成果生成流未发布 Turn 终态")

        turn = await wait_for_terminal_turn(e2e_client, headers, thread_id, turn_id)
        run_id = turn["yuxi"]["result_run_id"]
        connection = await asyncpg.connect(postgres_dsn())
        try:
            rows = await connection.fetch("SELECT * FROM workspace_deliverables WHERE thread_id=$1 ORDER BY name", thread_id)
            assert len(rows) == len(EXPECTED_FILES)
            assert {row["name"] for row in rows} == set(EXPECTED_FILES)
            assert {(row["uid"], row["thread_id"], row["run_id"], row["turn_id"]) for row in rows} == {(uid, thread_id, run_id, turn_id)}
            assert {row["operation_id"] for row in rows} == {"call-deliverables-publish"}
            assert all(row["app_id"] is None for row in rows)
            workdir = await connection.fetchval(
                "SELECT p.workdir_path FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.thread_id=$1", thread_id
            )
            snapshot_paths = [row["saved_path"] for row in rows]
        finally:
            await connection.close()
        source_root = user_workspace_dir(uid) / workdir
        assert (source_root / "intermediate.tmp").read_bytes() == b"not a deliverable"
        for name, content in EXPECTED_FILES.items():
            assert (source_root / name).read_bytes() == content
        (source_root / "report.md").write_text("Changed after publication", encoding="utf-8")
        (source_root / "chart.png").unlink()

        listing = await e2e_client.get("/api/workspace/deliverables", headers=headers)
        assert listing.status_code == 200, listing.text
        own_items = [item for item in listing.json()["items"] if item["thread_id"] == thread_id]
        assert len(own_items) == len(EXPECTED_FILES)
        by_name = {item["name"]: item for item in own_items}
        for name, content in EXPECTED_FILES.items():
            item = by_name[name]
            assert item["size"] == len(content) and item["thread_id"] == thread_id and item["run_id"] == run_id
            downloaded = await e2e_client.get(f"/api/workspace/deliverables/{item['id']}/download", headers=headers)
            assert downloaded.status_code == 200, downloaded.text
            assert downloaded.content == content
            assert "attachment" in downloaded.headers["content-disposition"]
        forbidden = await e2e_client.get(f"/api/workspace/deliverables/{by_name['report.md']['id']}/download", headers=e2e_headers)
        assert forbidden.status_code == 404, forbidden.text

        followup = await e2e_client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
            json={
                "events": [
                    {
                        "type": "agent.session.input.message",
                        "input": [{"role": "user", "content": [{"type": "input_text", "text": "DELIVERABLES_FOLLOWUP"}]}],
                        "yuxi": {"mode": "follow_up"},
                    }
                ]
            },
        )
        assert followup.status_code == 202, followup.text
        receipt = followup.json()
        consumed = await wait_for_consumed_input(e2e_client, headers, receipt)
        final = await wait_for_terminal_turn(e2e_client, headers, thread_id, consumed["turn_id"])
        assert final["status"] == "completed", final
        assert final["yuxi"]["result_run_id"] != run_id
        await archive_public_thread(e2e_client, headers, thread_id, turn_id=consumed["turn_id"])
        listing = await e2e_client.get("/api/workspace/deliverables", headers=headers)
        own_items = [item for item in listing.json()["items"] if item["thread_id"] == thread_id]
        assert {item["id"] for item in own_items} == {item["id"] for item in by_name.values()}
        preview = await e2e_client.get(f"/api/workspace/deliverables/{by_name['report.md']['id']}/preview", headers=headers)
        assert preview.status_code == 200 and preview.json()["content"].encode() == EXPECTED_FILES["report.md"]
    finally:
        if thread_id:
            connection = await asyncpg.connect(postgres_dsn())
            try:
                snapshot_paths = (
                    await connection.fetchval("SELECT array_agg(saved_path) FROM workspace_deliverables WHERE thread_id=$1", thread_id)
                    or snapshot_paths
                )
                await connection.execute("DELETE FROM workspace_deliverables WHERE thread_id=$1", thread_id)
            finally:
                await connection.close()
            workspace = Workspace(uid, root_path=global_user_data_dir(uid))
            for path in snapshot_paths:
                workspace.delete_authorized_path(str(PurePosixPath(path).parent.parent), root="/")
            await archive_public_thread(e2e_client, headers, thread_id, turn_id=turn_id)
        if slug:
            await delete_agent(e2e_client, e2e_headers, slug)
        if provider_created:
            removed = await e2e_client.delete(f"/api/system/model-providers/{provider_id}", headers=e2e_headers)
            assert removed.status_code in {200, 404}, removed.text
