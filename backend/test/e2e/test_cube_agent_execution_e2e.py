"""真实 Agent HTTP/worker/Cube 链路的跨会话并发取消。"""

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from yuxi.modules.agents.runtime.sandbox.cube_provider import CubeSandboxProvider
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.workspace.paths import user_workspace_dir
from test.e2e.e2e_helpers import wait_for_consumed_input, wait_model_provider_cache

pytestmark = [pytest.mark.self_managed_resources, pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.timeout(240)]

COMMANDS = {
    "CUBE_CANCEL_A": "(while :; do printf a >> cancelled.txt; sleep .1; done) & wait",
    "CUBE_SURVIVE_B": "for i in $(seq 1 40); do printf b >> survivor.txt; sleep .15; done; printf B_DONE",
    "CUBE_CRASH_A": "(while :; do printf c >> crashed-run.txt; sleep .1; done) & wait",
    "CUBE_THIRD": "printf third > third.txt; printf THIRD_DONE",
}


class CubeReplayHandler(BaseHTTPRequestHandler):
    """模型只输出固定工具调用，文件与数据库独立证明执行结果。"""

    def do_POST(self):
        """经真实 OpenAI HTTP 流触发既有 FilesystemMiddleware。"""
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["Authorization"] == "Bearer cube-test-model-key"
        messages = request["messages"]
        user = next(message for message in reversed(messages) if message["role"] == "user")
        marker = next(key for key in COMMANDS if key in json.dumps(user["content"]))
        last_user = max(index for index, message in enumerate(messages) if message["role"] == "user")
        tool_messages = [message for message in messages[last_user:] if message["role"] == "tool"]
        if marker == "CUBE_THIRD" and len(tool_messages) == 1:
            workdir = re.search(r"当前 Project Workdir 为 ([^，\s]+)", json.dumps(messages, ensure_ascii=False)).group(1)
            assert workdir.startswith("/home/user/workspace/projects/")
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-cube-artifact",
                        "type": "function",
                        "function": {"name": "present_artifacts", "arguments": json.dumps({"filepaths": [f"{workdir}/third.txt"]})},
                    }
                ],
            }
            finish = "tool_calls"
        elif tool_messages:
            delta = {"role": "assistant", "content": "CUBE_DONE"}
            finish = "stop"
        else:
            assert any(item["function"]["name"] == "execute" for item in request["tools"])
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-cube-" + marker,
                        "type": "function",
                        "function": {"name": "execute", "arguments": json.dumps({"command": COMMANDS[marker]})},
                    }
                ],
            }
            finish = "tool_calls"
        common = {"id": "chatcmpl-cube", "object": "chat.completion.chunk", "created": 1, "model": request["model"]}
        payloads = [
            {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {
                **common,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        ]
        body = "".join("data: " + json.dumps(payload) + "\n\n" for payload in payloads) + "data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_args):
        """模型请求不写凭据或 prompt 日志。"""


async def terminal(client, headers, thread_id, turn_id):
    """回读 Turn 持久结果，不依赖日志关键词。"""
    async with asyncio.timeout(90):
        while True:
            response = await client.get(f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}", headers=headers)
            assert response.status_code == 200, response.text
            value = response.json()
            if value["status"] in ("completed", "failed", "cancelled"):
                return value
            await asyncio.sleep(0.1)


def start_test_worker(workers):
    """启动真正 ARQ worker，执行身份落盘用于核对被杀进程。"""
    owner_file = Path("/app/runtime/api") / ("cube-worker-" + uuid.uuid4().hex)
    code = (
        "from pathlib import Path; "
        "from yuxi.workers.main import main; "
        "from yuxi.modules.agents.services.leases import WORKER_ID; "
        f"Path({str(owner_file)!r}).write_text(WORKER_ID); main()"
    )
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    workers.append((process, owner_file))
    return process


@pytest.fixture
def execution_resources():
    """失败路径也收回本次 HTTP server 与 ARQ 子进程。"""
    server = ThreadingHTTPServer(("0.0.0.0", 0), CubeReplayHandler)
    replay = threading.Thread(target=server.serve_forever, daemon=True)
    replay.start()
    workers = []
    try:
        yield server, workers
    finally:
        try:
            for process, path in workers:
                try:
                    if process.poll() is None:
                        os.kill(process.pid, signal.SIGCONT)
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                except ProcessLookupError:
                    process.wait(timeout=5)
                finally:
                    path.unlink(missing_ok=True)
        finally:
            server.shutdown()
            server.server_close()
            replay.join(timeout=5)


async def test_agent_shared_vm_cancellation_preserves_other_run(execution_resources):
    """两个真正 worker Run 在同用户 VM 并行，只取消指定 Turn。"""
    own_workers = os.getenv("CUBE_TEST_OWN_WORKERS") == "1"
    server, workers = execution_resources
    provider = CubeSandboxProvider()
    repo = provider.repository
    uid = "cube-e2e-" + uuid.uuid4().hex
    password = "Test!" + uuid.uuid4().hex
    provider_id = "cube-replay-" + uuid.uuid4().hex[:8]
    slug = "cube-agent-" + uuid.uuid4().hex[:8]
    with repo.connect() as conn:
        department = conn.execute("INSERT INTO departments(name,created_at) VALUES (%s,now()) RETURNING id", (uid,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO users(uid,username,password_hash,role,user_kind,login_failed_count,is_deleted,created_at,department_id) "
            "VALUES (%s,%s,%s,'superadmin','human',0,0,now(),%s)",
            (uid, uid, AuthUtils.hash_password(password), department),
        )
    sessions = []
    headers = {}
    async with httpx.AsyncClient(base_url=os.getenv("TEST_BASE_URL", "http://127.0.0.1:5050"), timeout=120, trust_env=False) as client:
        try:
            if own_workers:
                start_test_worker(workers)
            login = await client.post("/api/auth/token", data={"username": uid, "password": password})
            assert login.status_code == 200, login.text
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            created = await client.post(
                "/api/system/model-providers",
                headers=headers,
                json={
                    "provider_id": provider_id,
                    "display_name": "Cube E2E replay",
                    "provider_type": "openai",
                    "base_url": f"http://api:{server.server_port}/v1",
                    "api_key": "cube-test-model-key",
                    "capabilities": ["chat"],
                    "enabled_models": [{"id": "cube-model", "display_name": "Cube replay", "type": "chat", "source": "manual"}],
                    "is_enabled": True,
                },
            )
            assert created.status_code == 200, created.text
            await wait_model_provider_cache()
            agent = await client.post(
                "/api/agent",
                headers=headers,
                json={
                    "name": "Cube E2E",
                    "slug": slug,
                    "backend_id": "ChatbotAgent",
                    "visibility": "private",
                    "config_json": {
                        "context": {
                            "model": f"{provider_id}:cube-model",
                            "tools": ["present_artifacts"],
                            "skills": [],
                            "preload_skills": [],
                            "knowledges": [],
                            "mcps": [],
                        }
                    },
                },
            )
            assert agent.status_code == 200, agent.text
            for marker in ("CUBE_CANCEL_A", "CUBE_SURVIVE_B"):
                if own_workers and marker == "CUBE_SURVIVE_B":
                    async with asyncio.timeout(60):
                        while True:
                            with repo.connect() as conn:
                                commands = repo.commands(conn, uid)
                            if commands and commands[0]["pgid"]:
                                break
                            await asyncio.sleep(0.1)
                    os.kill(workers[0][0].pid, signal.SIGSTOP)
                    start_test_worker(workers)
                response = await client.post(
                    "/api/v1/agents/sessions",
                    headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
                    json={
                        "agent_id": slug,
                        "tool_approval_mode": "always_trust",
                        "input": marker,
                        "title": "YUXI_TEST_SESSION_CUBE_" + marker,
                    },
                )
                assert response.status_code == 201, response.text
                thread_id = response.json()["id"]
                sessions.append((thread_id, await wait_for_consumed_input(client, headers, response.json()["yuxi"]["receipt"])))
            a, b = sessions
            async with asyncio.timeout(60):
                while True:
                    with repo.connect() as conn:
                        commands = repo.commands(conn, uid)
                    if len(commands) == 2 and all(command["pgid"] for command in commands):
                        break
                    await asyncio.sleep(0.1)
            if own_workers:
                assert len({command["owner"].split(":", 1)[0] for command in commands}) == 2
                os.kill(workers[0][0].pid, signal.SIGCONT)
            assert {command["run_id"] for command in commands} == {a[1]["run_id"], b[1]["run_id"]}
            assert len({command["generation"] for command in commands}) == 1
            busy = await client.post("/api/user/sandbox/rebuild", headers=headers)
            assert busy.status_code == 409, busy.text
            cancel = await client.post(
                f"/api/v1/agents/sessions/{a[0]}/events",
                headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
                json={"events": [{"type": "agent.session.input.cancel", "yuxi": {"turn_id": a[1]["turn_id"]}}]},
            )
            assert cancel.status_code == 202, cancel.text
            final_a = await terminal(client, headers, a[0], a[1]["turn_id"])
            final_b = await terminal(client, headers, b[0], b[1]["turn_id"])
            assert final_a["status"] == "cancelled", final_a
            assert final_b["status"] == "completed", final_b
            with repo.connect() as conn:
                project_paths = conn.execute(
                    "SELECT s.thread_id,p.workdir_path FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.uid=%s", (uid,)
                ).fetchall()
                states = conn.execute("SELECT id,status,runtime_cleanup_pending FROM agent_runs WHERE uid=%s", (uid,)).fetchall()
                assert not repo.commands(conn, uid)
            paths = {item["thread_id"]: user_workspace_dir(uid) / item["workdir_path"] for item in project_paths}
            before = (paths[a[0]] / "cancelled.txt").read_bytes()
            await asyncio.sleep(0.3)
            assert (paths[a[0]] / "cancelled.txt").read_bytes() == before
            assert (paths[b[0]] / "survivor.txt").read_bytes() == b"b" * 40
            assert all(not state["runtime_cleanup_pending"] for state in states)
            response = await client.post(
                "/api/v1/agents/sessions",
                headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
                json={
                    "agent_id": slug,
                    "tool_approval_mode": "always_trust",
                    "input": "CUBE_THIRD",
                    "title": "YUXI_TEST_SESSION_CUBE_THIRD",
                },
            )
            assert response.status_code == 201, response.text
            third = (response.json()["id"], await wait_for_consumed_input(client, headers, response.json()["yuxi"]["receipt"]))
            sessions.append(third)
            assert (await terminal(client, headers, third[0], third[1]["turn_id"]))["status"] == "completed"
            with repo.connect() as conn:
                project = conn.execute(
                    "SELECT p.workdir_path FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.thread_id=%s AND s.uid=%s",
                    (third[0], uid),
                ).fetchone()
            assert (user_workspace_dir(uid) / project["workdir_path"] / "third.txt").read_bytes() == b"third"
            if own_workers:

                async def submit(marker):
                    """提交到真实 Input 消费链路并记录清理归属。"""
                    response = await client.post(
                        "/api/v1/agents/sessions",
                        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
                        json={
                            "agent_id": slug,
                            "tool_approval_mode": "always_trust",
                            "input": marker,
                            "title": "YUXI_TEST_SESSION_CUBE_RECOVERY",
                        },
                    )
                    assert response.status_code == 201, response.text
                    item = (response.json()["id"], await wait_for_consumed_input(client, headers, response.json()["yuxi"]["receipt"]))
                    sessions.append(item)
                    return item

                crashed = await submit("CUBE_CRASH_A")
                async with asyncio.timeout(60):
                    while True:
                        with repo.connect() as conn:
                            commands = repo.commands(conn, uid, run_id=crashed[1]["run_id"])
                        if commands and commands[0]["pgid"]:
                            break
                        await asyncio.sleep(0.1)
                owner = commands[0]["owner"]
                dying = next(process for process, path in workers if owner.split(":", 1)[0] == path.read_text())
                os.kill(dying.pid, signal.SIGKILL)
                assert dying.wait(timeout=5) == -signal.SIGKILL
                survivor = await submit("CUBE_SURVIVE_B")
                with repo.connect() as conn:
                    # 进程已真实死亡；加速它所属租约的过期，交给活着的 worker 正常维护。
                    conn.execute(
                        "UPDATE agent_runs SET lease_expires_at=now()-interval '1 second' WHERE id=%s AND worker_id=%s",
                        (crashed[1]["run_id"], owner),
                    )
                failed = await terminal(client, headers, crashed[0], crashed[1]["turn_id"])
                assert failed["status"] == "failed", failed
                assert (await terminal(client, headers, survivor[0], survivor[1]["turn_id"]))["status"] == "completed"
                async with asyncio.timeout(30):
                    while True:
                        with repo.connect() as conn:
                            run = conn.execute(
                                "SELECT runtime_cleanup_pending,error_type FROM agent_runs WHERE id=%s", (crashed[1]["run_id"],)
                            ).fetchone()
                            remaining = repo.commands(conn, uid)
                        if not run["runtime_cleanup_pending"] and not remaining:
                            break
                        await asyncio.sleep(0.1)
                assert run["error_type"] == "worker_lease_expired"
                with repo.connect() as conn:
                    projects = conn.execute(
                        "SELECT s.thread_id,p.workdir_path FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.uid=%s", (uid,)
                    ).fetchall()
                paths = {item["thread_id"]: user_workspace_dir(uid) / item["workdir_path"] for item in projects}
                before = (paths[crashed[0]] / "crashed-run.txt").read_bytes()
                await asyncio.sleep(0.3)
                assert (paths[crashed[0]] / "crashed-run.txt").read_bytes() == before
                assert (paths[survivor[0]] / "survivor.txt").read_bytes() == b"b" * 40
                recovered = await submit("CUBE_THIRD")
                assert (await terminal(client, headers, recovered[0], recovered[1]["turn_id"]))["status"] == "completed"
                with repo.connect() as conn:
                    project = conn.execute(
                        "SELECT p.workdir_path FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.thread_id=%s", (recovered[0],)
                    ).fetchone()
                assert (user_workspace_dir(uid) / project["workdir_path"] / "third.txt").read_bytes() == b"third"
                artifact_path = f"/home/user/workspace/{project['workdir_path']}/third.txt"
                state = await client.get(f"/api/v1/agents/sessions/{recovered[0]}/state", headers=headers)
                assert state.status_code == 200, state.text
                assert artifact_path in state.json()["agent_state"]["artifacts"]
                downloaded = await client.get(
                    f"/api/v1/agents/sessions/{recovered[0]}/artifacts/{artifact_path.lstrip('/')}?download=true",
                    headers=headers,
                )
                assert downloaded.status_code == 200, downloaded.text
                assert downloaded.content == b"third"
                retired_path = f"home/gem/user-data/{project['workdir_path']}/third.txt"
                retired = await client.get(
                    f"/api/v1/agents/sessions/{recovered[0]}/artifacts/{retired_path}",
                    headers=headers,
                )
                assert retired.status_code == 403, retired.text
        finally:
            if own_workers:
                from yuxi.modules.agents.services.leases import reconcile_expired_run_leases, reconcile_pending_runtime_cleanups

                with repo.connect() as conn:
                    conn.execute(
                        "UPDATE agent_runs SET lease_expires_at=now()-interval '1 second' "
                        "WHERE uid=%s AND status IN ('running','cancel_requested')",
                        (uid,),
                    )
                await reconcile_expired_run_leases()
                await reconcile_pending_runtime_cleanups()
            for thread_id, _ in sessions:
                await client.delete(f"/api/v1/agents/sessions/{thread_id}", headers=headers)
            await client.delete(f"/api/agent/{slug}", headers=headers)
            await client.delete(f"/api/system/model-providers/{provider_id}", headers=headers)
            with repo.user_lock(uid) as conn:
                row = repo.environment(conn, uid)
                if row and row["sandbox_id"]:
                    provider.adapter.destroy(provider._reference(row))
                conn.execute("DELETE FROM sandbox_commands WHERE uid=%s", (uid,))
                conn.execute("DELETE FROM user_sandboxes WHERE uid=%s", (uid,))
                # 会话保留审计事实，账号软删除不影响其历史归属。
                conn.execute("UPDATE users SET is_deleted=1,deleted_at=now() WHERE uid=%s", (uid,))
