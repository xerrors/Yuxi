"""真实 PostgreSQL、Cube 和 HTTP 的用户环境接入验收。"""

import hashlib
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx
import pytest
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir
from yuxi.modules.workspace.paths import ensure_user_workspace, user_workspace_dir, workspace_uid_dirname
from yuxi.modules.workspace.runtime_paths import WORKSPACE_PATH, SHARED_SKILLS_PATH
from yuxi.shared.errors import StateConflictError

pytestmark = [pytest.mark.integration, pytest.mark.self_managed_resources]


def run(backend, command, timeout=60):
    """命令必须返回真实退出码，而不是仅 SDK 请求成功。"""
    response = backend.execute(command, timeout=timeout)
    assert response.exit_code == 0, response.output
    return response.output.strip()


def test_concurrent_first_use_and_home_files_wake_original_vm(resources, monkeypatch):
    """独立 provider 通过数据库锁共享同一 VM，Home 文件自动唤醒。"""
    uid, _, provider, backend = resources
    import subprocess
    import sys

    code = (
        "from yuxi.modules.agents.runtime.sandbox.cube_provider import CubeSandboxProvider; "
        f"print(CubeSandboxProvider().get({uid!r}).sandbox_id)"
    )
    processes = [
        subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)
    ]
    try:
        identities = []
        for process in processes:
            output, error = process.communicate(timeout=120)
            assert process.returncode == 0, error
            identities.append(output.strip())
        assert identities[0] == identities[1]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(5)
    reference = provider.get(uid)
    assert reference.sandbox_id == identities[0]
    assert run(backend, "id -u") == str(os.getuid())
    assert backend.execute("sudo -n id").exit_code != 0
    denied = run(
        backend,
        "curl --noproxy '*' --max-time 3 -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:49983/files?path=/etc/passwd&username=root'",
    )
    assert denied == "401"
    denied = run(
        backend,
        "curl --noproxy '*' --max-time 3 -s -o /dev/null -w '%{http_code}' -X POST "
        "-H 'Content-Type: application/json' -d '{\"accessToken\":\"untrusted-reset-attempt\"}' http://127.0.0.1:49983/init",
    )
    assert denied == "401"
    assert backend.write("/etc/cube-test-protected", "forbidden").error
    assert backend.write("/home/user/config-test.txt", "home-persisted").error is None
    provider.adapter.pause(reference)
    assert provider.adapter.state(reference).lower() == "paused"
    assert backend.read("/home/user/config-test.txt").file_data["content"] == "home-persisted"
    assert provider.adapter.state(reference).lower() == "running"
    assert run(backend, "test ! -L ~/workspace && cd ~/workspace && pwd -P") == WORKSPACE_PATH
    assert provider.status(uid)["active_operations"] == 0


def test_shared_workspace_skills_pause_and_rebuild(resources, monkeypatch):
    """宿主字节、软件持久化与显式重建通过同一真实实例验证。"""
    uid, _, provider, backend = resources
    shared = get_user_skills_root_dir(uid) / "shared"
    shared.mkdir()
    (shared / "SKILL.md").write_text("before-sleep")
    personal = user_workspace_dir(uid) / "agents/skills/personal"
    personal.mkdir(parents=True)
    (personal / "SKILL.md").write_text("personal-persisted")
    path = f"{WORKSPACE_PATH}/latest.txt"
    assert backend.write(path, "first").error is None
    run(backend, "python3 -m venv ~/.venv && ~/.venv/bin/pip -q install six==1.17.0", timeout=180)
    assert run(backend, f"cat {SHARED_SKILLS_PATH}/shared/SKILL.md") == "before-sleep"
    assert backend.execute(f"printf forbidden > {SHARED_SKILLS_PATH}/shared/SKILL.md").exit_code != 0
    reference = provider.get(uid)
    provider.adapter.pause(reference)
    assert backend.edit(path, "first", "latest").error is None
    (shared / "SKILL.md").write_text("after-sleep")
    assert backend.read(path).file_data["content"] == "latest"
    assert provider.adapter.state(reference).lower() == "paused"
    time.sleep(45)
    assert run(backend, f"cat {path}; echo; ~/.venv/bin/python -c 'import six; print(six.__version__)'").splitlines() == [
        "latest",
        "1.17.0",
    ]
    before = provider.status(uid)["generation"]
    rebuilt = provider.rebuild(uid)
    assert rebuilt["generation"] == before + 1
    assert run(backend, "test ! -e ~/.venv; cat ~/workspace/latest.txt") == "latest"
    assert run(backend, f"cat {SHARED_SKILLS_PATH}/shared/SKILL.md") == "after-sleep"
    assert (personal / "SKILL.md").read_text() == "personal-persisted"


def test_concurrent_cancel_and_idle_rebuild_guards(resources, monkeypatch):
    """取消一组父子进程，另一组并行完成，忙碌重建明确拒绝。"""
    uid, _, provider, backend = resources
    cancelled = threading.Event()
    a = "(while :; do printf a >> cancelled.txt; sleep .1; done) & wait"
    b = "for i in $(seq 1 35); do printf b >> survivor.txt; sleep .1; done"
    with ThreadPoolExecutor(2) as executor:
        first = executor.submit(provider.execute, uid, a, WORKSPACE_PATH, 60, cancelled)
        second = executor.submit(provider.execute, uid, b, WORKSPACE_PATH, 60)
        deadline = time.monotonic() + 30
        with provider.repository.connect() as conn:
            while time.monotonic() < deadline:
                commands = provider.repository.commands(conn, uid)
                if len(commands) == 2 and all(command["pgid"] for command in commands):
                    break
                time.sleep(0.1)
        assert len(commands) == 2 and all(command["pgid"] for command in commands)
        with pytest.raises(StateConflictError):
            provider.rebuild(uid)
        monkeypatch.setenv("SANDBOX_IDLE_SECONDS", "1")
        with provider.repository.connect() as conn:
            conn.execute("UPDATE user_sandboxes SET last_active_at=now()-interval '10 minutes' WHERE uid=%s", (uid,))
        provider.maintain()
        assert provider.status(uid)["state"] == "running"
        cancelled.set()
        assert first.result(timeout=15)[1] != 0
        after = (user_workspace_dir(uid) / "cancelled.txt").read_bytes()
        time.sleep(0.3)
        assert (user_workspace_dir(uid) / "cancelled.txt").read_bytes() == after
        assert second.result(timeout=20)[1] == 0
    assert (user_workspace_dir(uid) / "survivor.txt").read_bytes() == b"b" * 35
    assert run(backend, "printf third") == "third"
    time.sleep(1.1)
    provider.maintain()
    assert provider.status(uid)["state"] == "paused"


def test_http_status_rebuild_is_authenticated_and_busy_returns_conflict(resources):
    """真实 HTTP 查询不创建 VM，忙碌状态与控制凭据不对外泄漏。"""
    uid, password, provider, backend = resources
    base = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:5050")
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        assert client.get("/api/user/sandbox").status_code == 401
        login = client.post("/api/auth/token", data={"username": uid, "password": password})
        assert login.status_code == 200, login.text
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        response = client.get("/api/user/sandbox", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "not_created"
        assert response.json()["paused_at"] is None
        assert response.json()["idle_deadline_at"] is None
        assert "token" not in response.text
        reference = provider.get(uid)
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(provider.execute, uid, "sleep 2", WORKSPACE_PATH, 10)
            deadline = time.monotonic() + 10
            while not provider.status(uid)["active_operations"] and time.monotonic() < deadline:
                time.sleep(0.05)
            busy = client.post("/api/user/sandbox/rebuild", headers=headers)
            assert busy.status_code == 409, busy.text
            active = client.get("/api/user/sandbox", headers=headers).json()
            assert active["busy"] and active["idle_deadline_at"] is None
            assert provider.get(uid).sandbox_id == reference.sandbox_id
            assert future.result(timeout=10)[1] == 0
        assert client.delete("/api/user/sandbox", headers=headers).status_code == 405


def test_http_sleep_timestamps_follow_confirmed_pause_and_original_resume(resources, monkeypatch):
    """Web 时间来自数据库确认，旧时间未知，状态查询不唤醒，恢复清除旧时间。"""
    uid, password, provider, backend = resources
    base = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:5050")
    reference = provider.get(uid)
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        login = client.post("/api/auth/token", data={"username": uid, "password": password})
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}

        def status():
            """只读取当前测试用户的真实 HTTP 状态。"""
            response = client.get("/api/user/sandbox", headers=headers)
            assert response.status_code == 200
            return response.json()

        running = status()
        assert running["state"] == "running" and running["paused_at"] is None
        active = datetime.fromisoformat(running["last_active_at"])
        deadline = datetime.fromisoformat(running["idle_deadline_at"])
        assert (deadline - active).total_seconds() == running["idle_timeout_seconds"]
        provider.adapter.pause(reference)
        with provider.repository.connect() as conn:
            conn.execute("UPDATE user_sandboxes SET last_active_at=now()-interval '10 minutes' WHERE uid=%s", (uid,))
            candidate = provider.repository.environment(conn, uid)
        # 两个维护阶段都只处理本测试资源，不接管其他用户的残留命令。
        monkeypatch.setattr(provider.repository, "environments", lambda: [candidate])
        monkeypatch.setattr(provider.repository, "stale_commands", lambda: [])
        provider.maintain()
        unknown = status()
        assert unknown["state"] == "paused" and unknown["paused_at"] is None
        assert unknown["idle_deadline_at"] is None
        assert provider.adapter.state(reference).lower() == "paused"
        assert run(backend, "printf before-idle") == "before-idle"
        with provider.repository.connect() as conn:
            conn.execute(
                "UPDATE user_sandboxes SET last_active_at=now()-interval '10 minutes', error=%s WHERE uid=%s",
                ("上次休眠失败", uid),
            )
            candidate = provider.repository.environment(conn, uid)
        before_pause = status()
        assert before_pause["error"] == "上次休眠失败"
        before = datetime.fromisoformat(before_pause["server_time"])
        provider.maintain()
        paused = status()
        after = datetime.fromisoformat(paused["server_time"])
        assert paused["state"] == "paused" and paused["idle_deadline_at"] is None
        assert paused["error"] is None
        assert before <= datetime.fromisoformat(paused["paused_at"]) <= after
        with provider.repository.connect() as conn:
            row = provider.repository.environment(conn, uid)
            assert row["paused_at"] == datetime.fromisoformat(paused["paused_at"])
            assert row["error"] is None
        assert status()["paused_at"] == paused["paused_at"]
        assert provider.adapter.state(reference).lower() == "paused"
        assert run(backend, "printf resumed-original") == "resumed-original"
        resumed = status()
        assert resumed["state"] == "running" and resumed["paused_at"] is None
        assert provider.get(uid).sandbox_id == reference.sandbox_id
        assert resumed["generation"] == running["generation"]


def test_http_disk_usage_wakes_original_vm_and_matches_filesystem(resources):
    """手动查询读取真实文件系统，自动状态查询不恢复，命令句柄清理。"""
    uid, password, provider, backend = resources
    base = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:5050")
    reference = provider.get(uid)
    generation = provider.status(uid)["generation"]
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        path = "/api/user/sandbox/disk-usage"
        assert client.post(path).status_code == 401
        login = client.post("/api/auth/token", data={"username": uid, "password": password})
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        # df 使用文件系统原生统计，独立于产品的 Python statvfs 输出与解析。
        expected = run(backend, "df -B1 --output=used,avail /home/user | tail -n 1").split()
        provider.adapter.pause(reference)
        for _ in range(2):
            assert client.get("/api/user/sandbox", headers=headers).json()["state"] == "paused"
        assert provider.adapter.state(reference).lower() == "paused"
        response = client.post(path, headers=headers)
        assert response.status_code == 200
        usage = response.json()
        assert set(usage) == {"used_bytes", "available_bytes"}
        # 执行通道自身可能生成少量标记文件，允许最多 1 MiB 的文件系统波动。
        assert abs(usage["used_bytes"] - int(expected[0])) < 1048576
        assert abs(usage["available_bytes"] - int(expected[1])) < 1048576
        assert usage["used_bytes"] > 0 and usage["available_bytes"] > 0
        assert provider.status(uid)["state"] == "running"
        assert provider.status(uid)["active_operations"] == 0
        assert provider.status(uid)["generation"] == generation
        assert provider.get(uid).sandbox_id == reference.sandbox_id


def test_http_env_verification_uses_new_values_preserves_existing_process_and_wakes_vm(resources):
    """回读进程和 HTTP 字节证明实时变量、休眠恢复与不匹配反馈。"""
    uid, password, provider, backend = resources
    base = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:5050")
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        path = "/api/user/agent-env"
        assert client.post(path + "/verify").status_code == 401
        login = client.post("/api/auth/token", data={"username": uid, "password": password})
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        first, latest = "original-value", "new-value-with-secret"
        assert client.put(path, headers=headers, json={"env": {"LAB_ENV": first}}).status_code == 200
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(
                provider.execute,
                uid,
                ": > before-env.tmp; touch env-writing-started; "
                "while [ ! -f env-write-release ]; do sleep .1; done; "
                'printf %s "$LAB_ENV" > before-env.tmp && mv before-env.tmp before-env.txt; '
                'while [ ! -f env-release ]; do sleep .1; done; printf %s "$LAB_ENV" > after-env.txt',
                WORKSPACE_PATH,
                30,
            )
            workspace = user_workspace_dir(uid)
            try:
                deadline = time.monotonic() + 20
                while not (workspace / "env-writing-started").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert (workspace / "env-writing-started").exists(), f"环境写入未开始: uid={uid}, state={provider.status(uid)}"
                assert not (workspace / "before-env.txt").exists(), "未写完的文件不能发布为就绪"
                (workspace / "env-write-release").touch()
                while not (workspace / "before-env.txt").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert (workspace / "before-env.txt").exists(), f"环境文件未发布: uid={uid}, state={provider.status(uid)}"
                assert (workspace / "before-env.txt").read_text() == first
                saved = client.put(path, headers=headers, json={"env": {"LAB_ENV": latest}})
                assert saved.status_code == 200
                verified = client.post(path + "/verify", headers=headers)
                assert verified.status_code == 200, verified.text
                assert verified.json()["variables"] == [{"key": "LAB_ENV", "matches": True}]
                assert verified.json()["updated_at"] == saved.json()["updated_at"]
                assert first not in verified.text and latest not in verified.text
            finally:
                (workspace / "env-write-release").touch()
                (workspace / "env-release").touch()
            assert future.result(timeout=10)[1] == 0
            assert (workspace / "after-env.txt").read_text() == first
        assert run(backend, 'printf %s "$LAB_ENV"') == latest
        reference = provider.get(uid)
        provider.adapter.pause(reference)
        assert provider.status(uid)["state"] == "paused"
        assert client.post(path + "/verify", headers=headers).json()["variables"][0]["matches"]
        assert provider.status(uid)["state"] == "running"
        assert provider.get(uid).sandbox_id == reference.sandbox_id
        assert backend.upload_files([("/home/user/.bash_profile", b"export LAB_ENV=overridden\n")])[0].error is None
        mismatch = client.post(path + "/verify", headers=headers)
        assert mismatch.status_code == 200, mismatch.text
        assert mismatch.json()["variables"] == [{"key": "LAB_ENV", "matches": False}]
        assert latest not in mismatch.text and "overridden" not in mismatch.text
        assert backend.upload_files([("/home/user/.bash_profile", b"")])[0].error is None
        assert client.put(path, headers=headers, json={"env": {}}).status_code == 200
        assert run(backend, 'test -z "${LAB_ENV+x}"') == ""
        assert not provider.status(uid)["active_operations"]


def test_run_snapshot_keeps_values_across_commands_while_verification_reads_latest(resources):
    """真实 VM 输出固定快照，HTTP 验证读取新值，退出上下文后读取新配置。"""
    from yuxi.modules.agents.runtime.sandbox import sandbox_run_env
    from yuxi.modules.agents.repositories.environment import AgentEnvRepository

    uid, password, provider, backend = resources
    base = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:5050")
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        login = client.post("/api/auth/token", data={"username": uid, "password": password})
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        path = "/api/user/agent-env"
        assert client.put(path, headers=headers, json={"env": {"LAB_ENV": "before"}}).status_code == 200
        token = sandbox_run_env.set(AgentEnvRepository.load_for_sync_runtime(uid))
        try:
            assert run(backend, 'printf %s "$LAB_ENV"') == "before"
            assert client.put(path, headers=headers, json={"env": {"LAB_ENV": "after"}}).status_code == 200
            # 真实 HTTP 请求由独立 API 上下文处理，必须验证最新已保存值。
            verified = client.post(path + "/verify", headers=headers)
            assert verified.status_code == 200, verified.text
            assert verified.json()["variables"] == [{"key": "LAB_ENV", "matches": True}]
            assert run(backend, 'printf %s "$LAB_ENV"') == "before"
            reference = provider.get(uid)
            provider.adapter.pause(reference)
            assert run(backend, 'printf %s "$LAB_ENV"') == "before"
            assert provider.get(uid).sandbox_id == reference.sandbox_id
        finally:
            sandbox_run_env.reset(token)
        assert run(backend, 'printf %s "$LAB_ENV"') == "after"
        assert not provider.status(uid)["active_operations"]


def test_http_env_verification_rejects_configuration_changed_during_probe(resources):
    """配置在真实 shell 验证期间更新时不能返回过时成功。"""
    uid, password, provider, backend = resources
    base = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:5050")
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        login = client.post("/api/auth/token", data={"username": uid, "password": password})
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        path = "/api/user/agent-env"
        assert client.put(path, headers=headers, json={"env": {"LAB_ENV": "before"}}).status_code == 200
        assert (
            backend.upload_files(
                [
                    (
                        "/home/user/.bash_profile",
                        b"printf ready > ~/workspace/probe-started.tmp && "
                        b"mv ~/workspace/probe-started.tmp ~/workspace/probe-started.txt; "
                        b"while [ ! -f ~/workspace/probe-release ]; do sleep .1; done\n",
                    )
                ]
            )[0].error
            is None
        )
        workspace = user_workspace_dir(uid)
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(client.post, path + "/verify", headers=headers)
            try:
                deadline = time.monotonic() + 15
                while not (workspace / "probe-started.txt").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert (workspace / "probe-started.txt").exists(), f"环境探针未开始: uid={uid}, state={provider.status(uid)}"
                assert (workspace / "probe-started.txt").read_text() == "ready"
                assert client.put(path, headers=headers, json={"env": {"LAB_ENV": "after"}}).status_code == 200
            finally:
                (workspace / "probe-release").touch()
            response = future.result(timeout=10)
        assert response.status_code == 409, response.text
        assert "验证期间环境变量已修改" in response.text
        assert not provider.status(uid)["active_operations"]


def test_cancel_ignores_blocking_user_shell_startup(resources):
    """阻塞的用户 profile 不能阻断目标进程组取消和文件工具。"""
    uid, _, provider, backend = resources
    reference = provider.get(uid)
    cancelled = threading.Event()
    workspace = user_workspace_dir(uid)
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(
            provider.execute,
            uid,
            "while :; do printf x >> blocked-profile.txt; sleep .05; done",
            WORKSPACE_PATH,
            30,
            cancelled,
        )
        deadline = time.monotonic() + 10
        while not (workspace / "blocked-profile.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (workspace / "blocked-profile.txt").exists()
        events = list(
            provider.adapter.stream(
                reference,
                "from pathlib import Path; Path('/home/user/.bash_profile').write_text('sleep 60\\n')",
                "/home/user",
                uuid.uuid4().hex,
                timeout=5,
                python=True,
            )
        )
        assert next(event["end"]["exitCode"] for event in events if "end" in event) == 0
        cancelled.set()
        assert future.result(timeout=10)[1] == 143
    before = (workspace / "blocked-profile.txt").read_bytes()
    time.sleep(0.3)
    assert (workspace / "blocked-profile.txt").read_bytes() == before
    assert not provider.status(uid)["active_operations"]
    assert backend.read("/home/user/.bash_profile").file_data["content"] == "sleep 60\n"
    assert backend.upload_files([("/home/user/.bash_profile", b"")])[0].error is None
    assert run(backend, "printf recovered") == "recovered"


def test_timeout_and_crashed_executor_cleanup_preserve_other_commands(resources):
    """真实子进程崩溃后从持久句柄清理，超时不留下普通子进程。"""
    import subprocess
    import sys
    import signal

    uid, _, provider, backend = resources
    provider.get(uid)
    deadline_response = backend.execute("(while :; do printf t >> timeout.txt; sleep .1; done) & wait", timeout=1)
    assert deadline_response.exit_code != 0
    path = user_workspace_dir(uid) / "timeout.txt"
    before = path.read_bytes()
    time.sleep(0.3)
    assert path.read_bytes() == before
    code = (
        "from yuxi.modules.agents.runtime.sandbox.cube_provider import CubeSandboxProvider; "
        f"CubeSandboxProvider().execute({uid!r}, 'while :; do printf c >> crashed.txt; sleep .1; done', {WORKSPACE_PATH!r}, 120)"
    )
    process = subprocess.Popen([sys.executable, "-c", code])
    with provider.repository.connect() as conn:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            commands = provider.repository.commands(conn, uid)
            if commands and commands[0]["pgid"]:
                break
            time.sleep(0.05)
        assert len(commands) == 1 and commands[0]["pgid"]
        os.kill(process.pid, signal.SIGKILL)
        assert process.wait(timeout=5) == -signal.SIGKILL
        # 控制侧执行者死亡；用过去的 deadline 加速等价的超时恢复边界。
        conn.execute("UPDATE sandbox_commands SET deadline=now()-interval '1 second' WHERE uid=%s", (uid,))
    provider.maintain()
    with provider.repository.connect() as conn:
        assert not provider.repository.commands(conn, uid)
    path = user_workspace_dir(uid) / "crashed.txt"
    before = path.read_bytes()
    time.sleep(0.3)
    assert path.read_bytes() == before
    assert run(backend, "printf still-usable") == "still-usable"


def test_abandoned_lifecycle_can_be_rebuilt_and_stale_generation_handle_converges(resources):
    """用户可确认原故障代次重建，旧代次的确认和执行均不影响新 VM。"""
    uid, password, provider, backend = resources
    base = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:5050")
    path = "/api/user/sandbox"
    workspace_file = f"{WORKSPACE_PATH}/rebuild-persisted.txt"
    home_file = "/home/user/replacement.txt"
    original = provider.get(uid)
    assert backend.write(workspace_file, "workspace-persisted").error is None
    with provider.repository.user_lock(uid) as conn:
        row = provider.repository.environment(conn, uid)
        generation = row["generation"]
        row["state"] = "creating"
        provider.repository.save_environment(conn, row)

    with httpx.Client(base_url=base, timeout=120, trust_env=False) as client:
        login = client.post("/api/auth/token", data={"username": uid, "password": password})
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        failed = response.json()
        assert failed["state"] == "failed" and failed["rebuild_required"] and not failed["busy"]
        assert failed["generation"] == generation

        wrong_generation = client.post(f"{path}/rebuild", headers=headers, params={"failed_generation": generation + 1})
        assert wrong_generation.status_code == 409
        assert wrong_generation.json()["detail"]["code"] == "sandbox_changed"
        rebuilt = client.post(f"{path}/rebuild", headers=headers, params={"failed_generation": generation})
        assert rebuilt.status_code == 200, rebuilt.text
        assert rebuilt.json()["generation"] == generation + 1
        assert not rebuilt.json()["rebuild_required"]
        replacement = provider.get(uid)
        assert replacement.sandbox_id != original.sandbox_id
        assert backend.write(home_file, "replacement-persisted").error is None
        provider.adapter.pause(replacement)

        for rejected_generation in (generation, generation + 1):
            rejected = client.post(f"{path}/rebuild", headers=headers, params={"failed_generation": rejected_generation})
            assert rejected.status_code == 409
            assert rejected.json()["detail"]["code"] == "sandbox_changed"

    with pytest.raises(StateConflictError) as stale:
        provider.prepare_instance(uid, expected=(generation, original.sandbox_id))
    assert stale.value.code == "sandbox_changed"
    assert provider.adapter.state(replacement).lower() == "paused"

    with pytest.raises(StateConflictError) as asleep:
        provider.run_guest_program(uid, generation + 1, user="user", command="/usr/bin/true", arguments=[], timeout=5)
    assert asleep.value.code == "sandbox_changed"
    assert provider.adapter.state(replacement).lower() == "paused"

    command_id = uuid.uuid4().hex
    with provider.repository.connect() as conn:
        row = provider.repository.environment(conn, uid)
        assert (row["generation"], row["sandbox_id"]) == (generation + 1, replacement.sandbox_id)
        conn.execute(
            "INSERT INTO sandbox_commands(id,uid,generation,kind,phase,deadline) VALUES (%s,%s,%s,'process','cleanup',now())",
            (command_id, uid, generation),
        )
        command = provider.repository.commands(conn, uid)[0]
    provider._cleanup_command(command)
    with provider.repository.connect() as conn:
        assert not provider.repository.commands(conn, uid)
    assert provider.adapter.state(replacement).lower() == "paused"
    expected = generation + 1, replacement.sandbox_id
    assert provider.prepare_instance(uid, expected=expected) == expected
    code, output = provider.run_guest_program(
        uid,
        generation + 1,
        user="user",
        command="/usr/bin/python3",
        arguments=["-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.readline()); sys.exit(3)"],
        timeout=5,
        stdin_data=b"private-input-example\n",
    )
    assert (code, output) == (3, b"private-input-example\n")
    assert run(backend, f"cat {workspace_file}") == "workspace-persisted"
    assert backend.read(home_file).file_data["content"] == "replacement-persisted"


def test_guest_program_receives_stdin_eof(resources):
    """在同一真实 VM 核对流末尾、完整数据和提前退出的退出码。"""
    uid, _, provider, _ = resources
    generation, _ = provider.prepare_instance(uid)
    multi_chunk_input = bytes(range(256)) * 520
    for stdin_data in (None, b"", b"binary\0input", multi_chunk_input):
        code, output = provider.run_guest_program(
            uid,
            generation,
            user="user",
            command="/usr/bin/python3",
            arguments=["-I", "-c", "import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest()); sys.exit(3)"],
            timeout=5,
            stdin_data=stdin_data,
        )
        assert (code, output) == (3, hashlib.sha256(stdin_data or b"").hexdigest().encode() + b"\n")

    code, output = provider.run_guest_program(
        uid,
        generation,
        user="user",
        command="/usr/bin/python3",
        arguments=["-I", "-c", "import sys; print(sys.stdin.buffer.read(3).hex()); sys.exit(24)"],
        timeout=5,
        stdin_data=multi_chunk_input,
    )
    assert (code, output) == (24, b"000102\n")

    fixed_length_reader = f"import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read({len(multi_chunk_input)})).hexdigest())"
    output, code, truncated = provider.execute(uid, fixed_length_reader, WORKSPACE_PATH, 5, stdin_data=multi_chunk_input, python=True)
    assert (output, code, truncated) == (hashlib.sha256(multi_chunk_input).hexdigest() + "\n", 0, False)


def test_vm_cannot_read_other_user_or_control_credentials(resources):
    """跨用户宿主目录、envd 控制凭据和私网管理端点均不可达。"""
    uid, _, provider, backend = resources
    other_uid = "cube-isolation-" + uuid.uuid4().hex
    ensure_user_workspace(other_uid)
    secret_path = user_workspace_dir(other_uid) / "secret.txt"
    secret_path.write_bytes(b"another-user-secret")
    try:
        reference = provider.get(uid)
        roots = (os.environ["CUBE_NODE_USER_DATA_DIR"], os.environ["CUBE_NODE_SKILL_PROJECTION_DIR"])
        for path in (
            str(secret_path),
            f"{roots[0]}/shared/{workspace_uid_dirname(other_uid)}/workspace/secret.txt",
            "/var/log/envd.log",
            "/proc/1/environ",
        ):
            result = backend.read(path)
            assert result.error and result.file_data is None, path
        output = run(backend, "env")
        assert reference.envd_token not in output
        if reference.traffic_token:
            assert reference.traffic_token not in output
        assert "POSTGRES_URL=" not in output and "CUBE_API_KEY=" not in output
        assert backend.execute("curl --noproxy '*' --max-time 2 -fsS http://172.17.0.1:13001/health", timeout=5).exit_code != 0
        assert secret_path.read_bytes() == b"another-user-secret"
    finally:
        import shutil

        shutil.rmtree(secret_path.parent.parent)


def test_disconnected_control_keeps_cleanup_and_never_recreates_environment(resources):
    """控制面失败不能假装停止；恢复后核对原实例清理且继续可用。"""
    uid, _, provider, backend = resources
    reference = provider.get(uid)
    cancelled = threading.Event()
    api_url = provider.adapter.config.api_url
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(
            provider.execute, uid, "while :; do printf x >> pending.txt; printf .; sleep .1; done", WORKSPACE_PATH, 15, cancelled
        )
        try:
            deadline = time.monotonic() + 15
            with provider.repository.connect() as conn:
                while time.monotonic() < deadline:
                    commands = provider.repository.commands(conn, uid)
                    if commands and commands[0]["pgid"]:
                        break
                    time.sleep(0.05)
            assert commands and commands[0]["pgid"]
            provider.adapter.config.api_url = "http://127.0.0.1:1"
            cancelled.set()
            assert future.result(timeout=10)[1] != 0
            with provider.repository.connect() as conn:
                row = provider.repository.environment(conn, uid)
                assert row["sandbox_id"] == reference.sandbox_id
                assert provider.repository.commands(conn, uid)[0]["phase"] == "cleanup"
            assert provider.status(uid)["busy"]
            with pytest.raises(StateConflictError):
                provider.rebuild(uid)
        finally:
            provider.adapter.config.api_url = api_url
            cancelled.set()
    provider.maintain()
    with provider.repository.connect() as conn:
        assert not provider.repository.commands(conn, uid)
    path = user_workspace_dir(uid) / "pending.txt"
    before = path.read_bytes()
    time.sleep(0.3)
    assert path.read_bytes() == before
    assert provider.get(uid).sandbox_id == reference.sandbox_id
    assert run(backend, "printf original-restored") == "original-restored"


def test_environment_clearing_children_are_stopped_and_vm_files_use_kernel_permissions(resources):
    """清空环境不脱离普通进程组；远端文件读写不能绕过 Linux 权限。"""
    uid, _, provider, backend = resources
    run(backend, "env -i /bin/bash -c 'while :; do printf x >> cleared.txt; sleep .05; done' >/dev/null 2>&1 & sleep .2")
    path = user_workspace_dir(uid) / "cleared.txt"
    before = path.read_bytes()
    time.sleep(0.3)
    assert path.read_bytes() == before
    assert backend.read("/var/log/envd.log").error
    assert backend.upload_files([("/etc/cube-protected.txt", b"forbidden")])[0].error
    data = b"file-byte-content\n" * 65536
    assert backend.upload_files([("/home/user/large.bin", data)])[0].error is None
    assert backend.download_files(["/home/user/large.bin"])[0].content == data
    assert backend.write("/home/user/large.bin", "must-not-overwrite").error
    assert backend.download_files(["/home/user/large.bin"])[0].content == data
    assert not provider.status(uid)["active_operations"]


def test_confirmed_end_removes_marker_but_uncertain_launch_stays_cancelled(resources, monkeypatch):
    """持久环境不积累普通终态标记，断连晚启动仍被 tombstone 拦截。"""
    uid, _, provider, backend = resources
    execution = run(backend, 'printf %s "$YUXI_EXECUTION_ID"')
    assert len(execution) == 32
    assert run(backend, "test ! -e /tmp/.yuxi-cancel-" + execution) == ""
    reference = provider.get(uid)
    pending = []
    original_stream = provider.adapter.stream

    def disconnected_start(environment, command, cwd, execution_id, *args, **kwargs):
        """模拟请求已发出但未收到启动或终态响应。"""
        if kwargs.get("python"):
            return original_stream(environment, command, cwd, execution_id, *args, **kwargs)
        pending.append(execution_id)
        raise ConnectionError("启动响应断连，远端仍可能迟到执行")

    with monkeypatch.context() as patch:
        patch.setattr(provider.adapter, "stream", disconnected_start)
        with pytest.raises(ConnectionError, match="启动响应断连"):
            provider.execute(uid, "printf forbidden > late-start.txt", WORKSPACE_PATH, 5)
    uncertain = pending[0]
    events = list(provider.adapter.stream(reference, "printf forbidden > late-start.txt", WORKSPACE_PATH, uncertain, timeout=5))
    assert next(event["end"]["exitCode"] for event in events if "end" in event) == 143
    assert not (user_workspace_dir(uid) / "late-start.txt").exists()


@pytest.mark.asyncio
async def test_workspace_usage_follows_real_cube_lifecycle(resources, tmp_path, monkeypatch):
    """空闲且无人读取的 Cube 用户经过最后一次成功计量后退出巡检。"""
    import asyncio

    from yuxi.modules.workspace import usage_changes
    from yuxi.modules.workspace.services import quota

    uid, _, provider, backend = resources
    # 标记索引独立于其他用户，真实目录与 Cube 挂载仍用测试部署的数据卷。
    monkeypatch.setattr(usage_changes, "get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(quota, "get_sandbox_provider", lambda: provider)
    monkeypatch.setattr(provider.repository, "stale_commands", lambda: [])

    def snapshot():
        """从独立连接回读已经提交的成功测量。"""
        with provider.repository.connect() as conn:
            return conn.execute("SELECT used_bytes, measured_at, last_attempt_at FROM workspace_usage WHERE uid=%s", (uid,)).fetchone()

    assert usage_changes.pending_workspace_changes() == []
    reference = await asyncio.to_thread(provider.get, uid)
    await quota.scan_workspace_usage()
    first = snapshot()
    assert first["measured_at"] is not None
    assert [change.uid for change in usage_changes.pending_workspace_changes()] == [uid]

    await asyncio.to_thread(run, backend, "dd if=/dev/zero of=quota-proof.bin bs=64K count=1 status=none")
    assert (user_workspace_dir(uid) / "quota-proof.bin").stat().st_size == 65536
    await quota.scan_workspace_usage()
    active = snapshot()
    assert active["measured_at"] > first["measured_at"] and active["used_bytes"] > first["used_bytes"]
    assert [change.uid for change in usage_changes.pending_workspace_changes()] == [uid]

    with provider.repository.connect() as conn:
        conn.execute("UPDATE user_sandboxes SET last_active_at=now()-interval '10 minutes' WHERE uid=%s", (uid,))
        candidate = provider.repository.environment(conn, uid)
    monkeypatch.setattr(provider.repository, "environments", lambda: [candidate])
    await asyncio.to_thread(provider.maintain)
    assert provider.adapter.state(reference).lower() == "paused"

    async def unavailable(_uid):
        """模拟宿主读目录失败，验证真实数据库不会丢失上一观测。"""
        raise OSError("故障注入：工作区不可读")

    with monkeypatch.context() as failed_scan:
        failed_scan.setattr(quota, "measure_allocated_bytes", unavailable)
        await quota.scan_workspace_usage()
    failed = snapshot()
    assert failed["used_bytes"] == active["used_bytes"] and failed["measured_at"] == active["measured_at"]
    assert failed["last_attempt_at"] > active["last_attempt_at"]
    assert [change.uid for change in usage_changes.pending_workspace_changes()] == [uid]

    await quota.scan_workspace_usage()
    final = snapshot()
    assert final["measured_at"] > active["measured_at"] and final["used_bytes"] == active["used_bytes"]
    assert usage_changes.pending_workspace_changes() == []
    await quota.scan_workspace_usage()
    assert snapshot() == final

    resumed = await asyncio.to_thread(provider.get, uid)
    assert resumed.sandbox_id == reference.sandbox_id
    assert provider.adapter.state(resumed).lower() == "running"
    assert [change.uid for change in usage_changes.pending_workspace_changes()] == [uid]
