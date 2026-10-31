"""用真实 Docker 证明 container 后端的隔离、结果与清理契约。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import subprocess
import threading
import time
import uuid
from contextvars import copy_context
from dataclasses import replace
from datetime import timedelta

import pytest

from yuxi.modules.agents.repositories.sandbox import SandboxRepository
from yuxi.modules.agents.runtime.sandbox.container import ContainerSandboxProvider
from yuxi.sandbox_runner import engine as engine_module
from yuxi.sandbox_runner.engine import EngineError
from yuxi.sandbox_runner.manager import ContainerManager, SandboxConflictError, SandboxError
from yuxi.sandbox_runner.settings import RunnerSettings
from yuxi.shared.datetime import utc_now

pytestmark = [
    pytest.mark.integration,
    pytest.mark.self_managed_resources,
    pytest.mark.skipif(
        os.environ.get("SANDBOX_CONTAINER_TEST") != "1",
        reason="需要 Docker 与预构建 sandbox 镜像；设置 SANDBOX_CONTAINER_TEST=1 后运行",
    ),
]


@pytest.fixture(autouse=True)
def run_owner(monkeypatch):
    """Docker 专项固定业务准入；真实 PostgreSQL 接线由完整 E2E 验证。"""
    row = {
        "uid": "one",
        "status": "running",
        "worker_id": "integration-owner",
        "lease_expires_at": utc_now() + timedelta(hours=1),
        "runtime_cleanup_pending": False,
    }
    monkeypatch.setattr(SandboxRepository, "run_owner", lambda _self, _run_id: dict(row))
    return row


@pytest.fixture(scope="module")
def manager(tmp_path_factory):
    """建立隔离的宿主数据根、镜像配置与清理责任。"""
    root = tmp_path_factory.mktemp("container-sandbox")
    workdir = root / "shared" / "one" / "workspace" / "projects" / "demo"
    workdir.mkdir(parents=True)
    (workdir / "a.txt").write_text("workspace-a\n", encoding="utf-8")
    other = root / "shared" / "two" / "workspace" / "secret.txt"
    other.parent.mkdir(parents=True)
    other.write_text("other-user-secret\n", encoding="utf-8")
    (root / "skills" / "one" / "demo").mkdir(parents=True)
    (root / "skills" / "one" / "demo" / "SKILL.md").write_text("shared-skill\n", encoding="utf-8")
    image = os.environ.get("SANDBOX_CONTAINER_IMAGE", "yuxi-sandbox:0.8.0.beta6")
    digest = os.environ.get("SANDBOX_CONTAINER_IMAGE_DIGEST", "")
    run_uid = os.getuid() or 65534
    run_gid = os.getgid() or 65534
    if os.getuid() == 0:
        for path in (root, *root.rglob("*")):
            os.chown(path, run_uid, run_gid)
    value = ContainerManager(
        RunnerSettings(
            token="integration-token-1234567890",
            socket_path=os.environ.get("SANDBOX_CONTAINER_SOCKET", "/var/run/docker.sock"),
            image=image,
            image_digest=digest,
            network="none",
            memory_bytes=256 * 1024 * 1024,
            cpus=0.5,
            pids_limit=64,
            home_bytes=16 * 1024 * 1024,
            tmp_bytes=16 * 1024 * 1024,
            max_containers=8,
            max_execs=4,
            max_lifetime_seconds=600,
            host_data_dir=str(root),
            host_skill_dir=str(root / "skills"),
            data_mount=str(root),
            skill_mount=str(root / "skills"),
            run_uid=run_uid,
            run_gid=run_gid,
        )
    )
    try:
        yield value, root, workdir
    finally:
        for container in value.list_containers()["containers"]:
            try:
                value.remove_container(container["id"])
            except Exception:
                pass


def run(manager, run_id, command, *, timeout=30, budget=65536, stdin=None, workdir="projects/demo", python=False):
    """执行一条测试命令并返回结构化结果。"""
    return manager.execute(
        uid="one",
        run_id=run_id,
        owner="integration-owner",
        command_id=uuid.uuid4().hex,
        command=command,
        cwd=f"/home/user/workspace/{workdir}" if workdir else "/home/user",
        workdir=workdir,
        timeout_seconds=timeout,
        output_budget=budget,
        stdin_b64=base64.b64encode(stdin).decode() if stdin is not None else None,
        python=python,
        envs={"TEST_ENV": "value"},
    )


def test_workspace_roundtrip_isolation_and_fail_closed_canaries(manager):
    """双向字节一致，工作区外与其他用户、socket、元数据均不可达。"""
    value, _, workdir = manager
    run_id = str(uuid.uuid4())
    value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")

    command = r"""
set +e
cat a.txt
printf 'container-bytes' > b.txt
printf 'container-c\n' > c.txt
echo "--- canaries ---"
cat /home/user/shared/two/workspace/secret.txt 2>&1
cat /home/user/workspace/../../shared/two/workspace/secret.txt 2>&1
cat /var/run/docker.sock 2>&1
cat /.env 2>&1
ls /sandbox-data 2>&1
timeout 2 bash -c 'exec 3<>/dev/tcp/169.254.169.254/80' 2>&1
ps -eo pid,user,args
"""
    result = run(value, run_id, command)
    assert result["exit_code"] == 0
    assert "workspace-a" in result["output"]
    assert "other-user-secret" not in result["output"]
    assert "No such file or directory" in result["output"]
    assert "Network is unreachable" in result["output"] or "Connection refused" in result["output"]
    assert "docker-init" in result["output"]
    assert (workdir / "b.txt").read_bytes() == b"container-bytes"
    assert hashlib.sha256((workdir / "b.txt").read_bytes()).hexdigest() == hashlib.sha256(b"container-bytes").hexdigest()

    (workdir / "host.txt").write_text("host-bytes\n", encoding="utf-8")
    echoed = run(value, run_id, "cat host.txt")
    assert echoed["output"] == "host-bytes\n"
    assert hashlib.sha256(echoed["output"].encode()).hexdigest() == hashlib.sha256(b"host-bytes\n").hexdigest()

    skills = run(value, run_id, "cat /home/user/skills/demo/SKILL.md")
    assert skills["output"] == "shared-skill\n"
    value.remove_run(uid="one", run_id=run_id)


def test_inspect_proves_user_network_caps_and_resource_limits(manager):
    """Docker inspect 与容器内 cgroup 共同证明隔离配置。"""
    value, _, _ = manager
    run_id = str(uuid.uuid4())
    created = value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
    inspected = json.loads(subprocess.check_output(["docker", "inspect", created["id"]], text=True))[0]
    host = inspected["HostConfig"]
    assert inspected["Config"]["User"] == f"{value.settings.run_uid}:{value.settings.run_gid}"
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"]
    assert "no-new-privileges" in host["SecurityOpt"]
    assert host["NetworkMode"] == "none"
    assert host["PidsLimit"] == 64
    assert host["Memory"] == 256 * 1024 * 1024
    assert host["NanoCpus"] == 500_000_000
    assert host["Init"] is True
    assert set(host["Tmpfs"]) == {"/home/user", "/tmp"}
    mounts = {item["Destination"]: item for item in inspected["Mounts"]}
    assert set(mounts) == {"/home/user/workspace/projects/demo", "/home/user/skills", "/home/user/workspace/agents/skills"}
    assert mounts["/home/user/workspace/agents/skills"]["RW"] is True
    assert mounts["/home/user/workspace/projects/demo"]["RW"] is True
    assert mounts["/home/user/skills"]["RW"] is False

    result = run(
        value,
        run_id,
        "id; cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/pids.max; cat /sys/fs/cgroup/cpu.max; "
        "touch /etc/should-fail 2>&1; echo rootfs=$?",
        budget=65536,
    )
    assert f"uid={value.settings.run_uid} gid={value.settings.run_gid}" in result["output"]
    assert str(256 * 1024 * 1024) in result["output"]
    assert "64" in result["output"]
    assert "50000 100000" in result["output"]
    assert "Read-only file system" in result["output"]
    value.remove_run(uid="one", run_id=run_id)


def test_timeout_cancel_output_budget_and_cleanup(manager):
    """超时与取消杀掉命令组，输出截断显式可见，删除后无容器残留。"""
    value, _, _ = manager
    run_id = str(uuid.uuid4())
    value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
    timeout = run(value, run_id, "sleep 30; echo late", timeout=2)
    assert timeout["exit_code"] == 124
    assert "late" not in timeout["output"]
    assert "sleep 30" not in run(value, run_id, "ps -eo args || true")["output"]

    truncated = run(value, run_id, "printf 'x%.0s' {1..5000}", budget=100)
    assert truncated["truncated"] is True
    assert len(truncated["output"]) == 100
    assert truncated["exit_code"] == 0

    command_id = uuid.uuid4().hex
    result = {}
    context = copy_context()

    def long_running():
        result["value"] = value.execute(
            uid="one",
            run_id=run_id,
            owner="integration-owner",
            command_id=command_id,
            command="sleep 30; echo late",
            cwd="/home/user",
            workdir="projects/demo",
            timeout_seconds=60,
            output_budget=1024,
            stdin_b64=None,
            python=False,
            envs={},
        )

    thread = threading.Thread(target=lambda: context.run(long_running))
    thread.start()
    time.sleep(1)
    assert value.cancel_exec(uid="one", run_id=run_id, owner="integration-owner", command_id=command_id) is True
    thread.join(10)
    assert thread.is_alive() is False
    assert result["value"]["exit_code"] in {137, 143}
    assert "sleep 30" not in run(value, run_id, "ps -eo args || true")["output"]
    assert value.remove_run(uid="one", run_id=run_id) == {"deleted": True}
    assert value.remove_run(uid="one", run_id=run_id) == {"deleted": False, "reason": "not_found"}


def test_cancel_during_container_exec_creation_has_no_side_effect(manager):
    """cancel 在 exec 尚未启动时不能被误认为成功，命令不能产生副作用。"""
    value, _, workdir = manager
    run_id = str(uuid.uuid4())
    command_id = uuid.uuid4().hex
    value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
    original = value.engine.create_exec
    started = threading.Event()

    def delayed(*args, **kwargs):
        started.set()
        time.sleep(1.5)
        return original(*args, **kwargs)

    value.engine.create_exec = delayed
    result = {}

    def execute():
        result["value"] = value.execute(
            uid="one",
            run_id=run_id,
            owner="integration-owner",
            command_id=command_id,
            command="echo side-effect > should-not-exist.txt; sleep 5",
            cwd="/home/user/workspace/projects/demo",
            workdir="projects/demo",
            timeout_seconds=30,
            output_budget=1024,
            stdin_b64=None,
            python=False,
            envs={},
        )

    thread = threading.Thread(target=execute)
    thread.start()
    assert started.wait(5)
    assert value.cancel_exec(uid="one", run_id=run_id, owner="integration-owner", command_id=command_id) is False
    thread.join(10)
    assert thread.is_alive() is False
    assert result["value"]["exit_code"] == 143
    assert not (workdir / "should-not-exist.txt").exists()
    value.remove_run(uid="one", run_id=run_id)


def test_timeout_stops_continuous_output(manager):
    """持续输出不会无限延长执行；超时返回 124 并保留已截断输出。"""
    value, _, _ = manager
    run_id = str(uuid.uuid4())
    value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
    started = time.monotonic()
    result = run(value, run_id, "while true; do echo flood; done", timeout=2, budget=1024)
    assert time.monotonic() - started < 15
    assert result["exit_code"] == 124
    assert result["truncated"] is True
    value.remove_run(uid="one", run_id=run_id)


def test_idempotent_create_and_stdin_roundtrip(manager):
    """重复 create 复用同一容器，stdin 在 Workdir 落盘后被消费删除。"""
    value, _, workdir = manager
    run_id = str(uuid.uuid4())
    first = value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
    second = value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
    assert first["id"] == second["id"]
    result = run(value, run_id, 'python3 -c "import sys; print(sys.stdin.buffer.read().decode())"', stdin=b"stdin-payload")
    assert result["output"] == "stdin-payload\n"
    assert not list(workdir.glob(".yuxi-stdin-*"))
    value.remove_run(uid="one", run_id=run_id)


def test_deployments_with_same_run_id_are_isolated(manager, tmp_path):
    """真实 daemon 上相同 Run ID 的两个部署互不枚举、限流或清理。"""
    value, _, _ = manager
    (tmp_path / "shared/one/workspace/projects/demo").mkdir(parents=True)
    (tmp_path / "skills").mkdir()
    other = ContainerManager(
        replace(
            value.settings,
            host_data_dir=str(tmp_path),
            data_mount=str(tmp_path),
            host_skill_dir=str(tmp_path / "skills"),
            skill_mount=str(tmp_path / "skills"),
            max_containers=1,
        )
    )
    run_id = str(uuid.uuid4())
    try:
        first = value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
        second = other.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
        assert first["id"] != second["id"]
        assert {item["id"] for item in other.list_containers()["containers"]} == {second["id"]}
        assert first["id"] in {item["id"] for item in value.list_containers()["containers"]}
        assert second["id"] not in {item["id"] for item in value.list_containers()["containers"]}
        with pytest.raises(SandboxConflictError, match="部署"):
            other.remove_container(first["id"])
        assert other.remove_all() == 1
        assert value.engine.inspect_container(first["id"])["State"]["Running"] is True
        # 同一宿主数据根的 runner 重启继续识别自己的容器。
        restarted = ContainerManager(value.settings)
        assert restarted.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")["id"] == first["id"]
    finally:
        value.remove_run(uid="one", run_id=run_id)
        other.remove_run(uid="one", run_id=run_id)


@pytest.mark.parametrize("ignore_term", [False, True])
@pytest.mark.parametrize("mode", ["completed", "timeout_without_control", "disconnected"])
def test_completed_command_stops_background_group_without_touching_parallel_command(manager, monkeypatch, ignore_term, mode):
    """正常结束或控制请求故障下超时都停止命令组，不影响同 Run 并发命令。"""
    from concurrent.futures import ThreadPoolExecutor

    value, _, workdir = manager
    run_id = str(uuid.uuid4())
    prefix = uuid.uuid4().hex
    ready = workdir / f"ready-{prefix}"
    late = workdir / f"late-{prefix}"
    trigger = workdir / f"trigger-{prefix}"
    parallel_ready = workdir / f"parallel-ready-{prefix}"
    release = workdir / f"release-{prefix}"
    parallel_result = workdir / f"parallel-result-{prefix}"
    script = ("trap '' TERM; " if ignore_term else "") + (
        f"touch {ready.name}; while [ ! -f {trigger.name} ]; do sleep .02; done; printf late > {late.name}"
    )
    command = (
        "env -i /bin/sh -c " + shlex.quote(script) + " >/dev/null 2>&1 </dev/null & "
        f"while [ ! -f {ready.name} ]; do sleep .02; done; printf done; exit 7"
    )
    value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
    remove = value.engine.remove_container
    if mode == "timeout_without_control":
        command = "exec env -i /bin/sh -c " + shlex.quote(script)

        def unavailable(*_args, **_kwargs):
            """只切断控制请求，命令流和 Workdir 仍使用真实 Docker。"""
            raise EngineError("control request unavailable")

        monkeypatch.setattr(value.engine, "run_probe", unavailable)
        monkeypatch.setattr(value.engine, "remove_container", unavailable)
    elif mode == "disconnected":
        command = f"echo {prefix}; exec env -i /bin/sh -c " + shlex.quote(script)
        feed = engine_module._FrameDecoder.feed

        def disconnected(decoder, chunk):
            """首帧真实输出后断流，保留容器内仍在运行的命令。"""
            for payload in feed(decoder, chunk):
                if prefix.encode() in payload:
                    raise EngineError("stream disconnected")
                yield payload

        monkeypatch.setattr(engine_module._FrameDecoder, "feed", disconnected)
    with ThreadPoolExecutor(max_workers=1) as executor:
        parallel = executor.submit(
            run,
            value,
            run_id,
            f"touch {parallel_ready.name}; while [ ! -f {release.name} ]; do sleep .02; done; "
            f"printf survivor > {parallel_result.name}; printf other",
        )
        try:
            deadline = time.monotonic() + 5
            while not parallel_ready.exists() and not parallel.done() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert parallel_ready.exists()
            if mode == "disconnected":
                with pytest.raises(SandboxError):
                    run(value, run_id, command)
            else:
                result = run(value, run_id, command, timeout=1 if mode == "timeout_without_control" else 30)
                expected = (
                    {"output": "", "exit_code": 124, "truncated": False}
                    if mode == "timeout_without_control"
                    else {"output": "done", "exit_code": 7, "truncated": False}
                )
                assert result == expected
            assert not parallel.done(), "本次命令的清理不能终止同 Run 的另一命令"
            # 返回后才允许副作用，区分取消处理期间的写入与虚假收尾。
            trigger.touch()
            time.sleep(0.5)
            assert not late.exists(), "命令返回后后台进程仍在写 Workdir"
            release.touch()
            assert parallel.result(timeout=10) == {"output": "other", "exit_code": 0, "truncated": False}
            assert parallel_result.read_text() == "survivor"
        finally:
            release.touch()
            monkeypatch.setattr(value.engine, "remove_container", remove)
            value.remove_run(uid="one", run_id=run_id)
            for path in (ready, late, trigger, parallel_ready, release, parallel_result):
                path.unlink(missing_ok=True)


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
@pytest.mark.parametrize("ignore_term", [False, True])
def test_cleared_environment_cannot_outlive_timeout_or_cancel(manager, mode, ignore_term):
    """exec env -i 清除身份后仍能取消，忽略 TERM 的子进程也不能延迟写入。"""
    value, _, workdir = manager
    run_id = str(uuid.uuid4())
    command_id = uuid.uuid4().hex
    ready = workdir / f"ready-{command_id}"
    late = workdir / f"late-{command_id}"
    script = ("trap '' TERM; " if ignore_term else "") + f"touch {ready.name}; sleep 6; printf late > {late.name}"
    result = {}
    errors = []

    def execute():
        """在独立线程等待真实 exec 结果，主线程从文件确认命令已经启动。"""
        try:
            result.update(
                value.execute(
                    uid="one",
                    run_id=run_id,
                    owner="integration-owner",
                    command_id=command_id,
                    command="exec env -i /bin/sh -c " + shlex.quote(script),
                    cwd="/home/user/workspace/projects/demo",
                    workdir="projects/demo",
                    timeout_seconds=2 if mode == "timeout" else 20,
                    output_budget=1024,
                    stdin_b64=None,
                    python=False,
                    envs={},
                )
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=execute)
    started = time.monotonic()
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), errors
        if mode == "cancel":
            assert value.cancel_exec(uid="one", run_id=run_id, owner="integration-owner", command_id=command_id) is True
        thread.join(8)
        assert not thread.is_alive()
        assert not errors
        assert result["exit_code"] in ({124} if mode == "timeout" else {137, 143})
        # 覆盖原命令的延迟写入窗口，回读文件而非相信取消响应。
        time.sleep(max(0, started + 6.5 - time.monotonic()))
        assert not late.exists()
        assert command_id not in run(value, run_id, "ps -eo args")["output"]
        assert run(value, run_id, "echo usable")["output"] == "usable\n"
    finally:
        value.remove_run(uid="one", run_id=run_id)
        thread.join(10)
        ready.unlink(missing_ok=True)
        late.unlink(missing_ok=True)


def test_reconciliation_snapshot_cannot_remove_new_attempt_container(manager, run_owner):
    """真实旧容器快照被替换后，对账不能删除新 attempt 的容器。"""
    value, _, _ = manager
    run_id = str(uuid.uuid4())
    replacement = {}

    class ReplacingClient:
        """在枚举与清理之间模拟旧 attempt 收尾和新 attempt 创建。"""

        def list(self):
            snapshot = [item for item in value.list_containers()["containers"] if item["labels"]["yuxi.run_id"] == run_id]
            value.remove_run(uid="one", run_id=run_id)
            run_owner["worker_id"] = "new-owner"
            replacement.update(value.create(uid="one", run_id=run_id, owner="new-owner", workdir="projects/demo"))
            return snapshot

        def remove_run(self, **kwargs):
            return value.remove_run(**kwargs)

        def remove_container(self, container_id):
            return value.remove_container(container_id)

    class Repository:
        """Run 已有新 owner，旧快照因此成为清理候选。"""

        def run_owner(self, _run_id):
            return {"uid": "one", "status": "running", "worker_id": "new-owner"}

    try:
        run_owner["worker_id"] = "old-owner"
        old = value.create(uid="one", run_id=run_id, owner="old-owner", workdir="projects/demo")
        ContainerSandboxProvider(client=ReplacingClient(), repository=Repository()).maintain()
        assert replacement["id"] != old["id"]
        assert value.engine.inspect_container(replacement["id"])["State"]["Running"] is True
    finally:
        value.remove_run(uid="one", run_id=run_id)


def test_pids_limit_is_enforced(manager):
    """fork 数量受到 cgroup pids 限制。"""
    value, _, _ = manager
    run_id = str(uuid.uuid4())
    value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
    fork_script = """import os,time
n=0
for _ in range(200):
    try:
        child=os.fork()
        if child==0:
            time.sleep(30)
            os._exit(0)
        n+=1
    except OSError:
        break
print('children='+str(n))
"""
    fork = run(value, run_id, "python3 -c " + shlex.quote(fork_script), timeout=20)
    assert "children=" in fork["output"]
    assert int(fork["output"].split("children=")[1].split()[0]) < 200
    value.remove_run(uid="one", run_id=run_id)


def test_memory_limit_is_enforced(manager):
    """超过容器内存上限的进程被 OOM 杀死，下一次执行仍可用。"""
    value, _, _ = manager
    run_id = str(uuid.uuid4())
    value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
    oom = run(value, run_id, "python3 -c 'bytearray(2*1024**3)'", timeout=20)
    assert oom["exit_code"] == 137
    assert run(value, run_id, "echo recovered")["output"] == "recovered\n"
    value.remove_run(uid="one", run_id=run_id)


def test_runner_http_contract_auth_lazy_exec_and_remove(tmp_path, monkeypatch, run_owner):
    """真实 runner HTTP 入口要求认证，并按契约创建、执行和删除。"""
    run_owner["worker_id"] = "http-owner"
    from fastapi.testclient import TestClient

    from yuxi.sandbox_runner import main as runner_main

    (tmp_path / "shared" / "one" / "workspace" / "projects" / "demo").mkdir(parents=True)
    (tmp_path / "skills" / "one").mkdir(parents=True)
    monkeypatch.setenv("SANDBOX_RUNNER_TOKEN", "integration-token-1234567890")
    monkeypatch.setenv("SANDBOX_CONTAINER_IMAGE", os.environ.get("SANDBOX_CONTAINER_IMAGE", "yuxi-sandbox:0.8.0.beta6"))
    monkeypatch.setenv("SANDBOX_CONTAINER_IMAGE_DIGEST", os.environ.get("SANDBOX_CONTAINER_IMAGE_DIGEST", ""))
    monkeypatch.setenv("SANDBOX_CONTAINER_HOST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SANDBOX_CONTAINER_HOST_SKILL_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("SANDBOX_CONTAINER_DATA_MOUNT", str(tmp_path))
    monkeypatch.setenv("SANDBOX_CONTAINER_SKILL_MOUNT", str(tmp_path / "skills"))
    run_uid = os.getuid() or 65534
    run_gid = os.getgid() or 65534
    if os.getuid() == 0:
        for path in (tmp_path, *tmp_path.rglob("*")):
            os.chown(path, run_uid, run_gid)
    monkeypatch.setenv("SANDBOX_CONTAINER_UID", str(run_uid))
    monkeypatch.setenv("SANDBOX_CONTAINER_GID", str(run_gid))
    runner_main._settings = None
    runner_main.app.state.manager = None
    run_id = str(uuid.uuid4())
    try:
        with TestClient(runner_main.app) as client:
            assert client.get("/v1/stats").status_code == 401
            assert client.get("/v1/health").status_code == 401
            headers = {"Authorization": "Bearer integration-token-1234567890"}
            assert client.get("/v1/health", headers=headers).status_code == 200
            created = client.post(
                f"/v1/runs/{run_id}/container",
                headers=headers,
                json={"uid": "one", "owner": "http-owner", "workdir": "projects/demo"},
            )
            assert created.status_code == 404, "显式创建不属于 runner 对外协议"
            executed = client.post(
                f"/v1/runs/{run_id}/exec",
                headers=headers,
                json={
                    "uid": "one",
                    "owner": "http-owner",
                    "command_id": uuid.uuid4().hex,
                    "command": "printf 'http-ok\\n'",
                    "cwd": "/home/user/workspace/projects/demo",
                    "workdir": "projects/demo",
                    "timeout_seconds": 10,
                    "output_budget": 1024,
                    "python": False,
                    "envs": {},
                },
            )
            assert executed.status_code == 200, executed.text
            assert executed.json()["output"] == "http-ok\n"
            piped = client.post(
                f"/v1/runs/{run_id}/exec",
                headers=headers,
                json={
                    "uid": "one",
                    "owner": "http-owner",
                    "command_id": uuid.uuid4().hex,
                    "command": "import os,sys; print(os.environ['HTTP_TEST']); print(sys.stdin.read()); sys.exit(7)",
                    "cwd": "/home/user/workspace/projects/demo",
                    "workdir": "projects/demo",
                    "timeout_seconds": 10,
                    "output_budget": 1024,
                    "stdin_b64": base64.b64encode(b"wire-input").decode(),
                    "python": True,
                    "envs": {"HTTP_TEST": "env-ok"},
                },
            )
            assert piped.status_code == 200, piped.text
            assert piped.json() == {"output": "env-ok\nwire-input\n", "exit_code": 7, "truncated": False}
            removed = client.delete(f"/v1/runs/{run_id}", headers=headers, params={"uid": "one"})
            assert removed.status_code == 200, removed.text
            assert removed.json()["deleted"] is True
    finally:
        runner_main._settings = None
        runner_main.app.state.manager = None


def test_matches_plain_docker_exit_code_and_output(manager):
    """同一镜像与命令的 stdout/stderr 合并结果和退出码一致。"""
    value, _, _ = manager
    run_id = str(uuid.uuid4())
    value.create(uid="one", run_id=run_id, owner="integration-owner", workdir="projects/demo")
    command = "printf 'out\\n'; printf 'err\\n' >&2; exit 7"
    result = run(value, run_id, command)
    plain = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            os.environ.get("SANDBOX_CONTAINER_IMAGE", "yuxi-sandbox:0.8.0.beta6"),
            "bash",
            "-lc",
            command,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )
    assert result["exit_code"] == plain.returncode == 7
    # 合并流保留两路内容；stdout/stderr 在 Docker 多路复用帧中的到达顺序不承诺一致。
    assert sorted(result["output"].splitlines()) == sorted(plain.stdout.splitlines())
    value.remove_run(uid="one", run_id=run_id)


@pytest.mark.parametrize("state", ["yielded", "pending", "cancel_requested", "new_owner"])
def test_late_exec_cannot_recreate_cleaned_run(manager, run_owner, monkeypatch, state):
    """创建前排队的请求在清理后恢复，不能复活旧 owner 或写 Workdir。"""
    from concurrent.futures import ThreadPoolExecutor

    value, _, workdir = manager
    run_id = str(uuid.uuid4())
    marker = workdir / ("late-" + run_id)
    queued = threading.Event()
    resume = threading.Event()
    create = value.create

    def delayed_create(**kwargs):
        """让旧请求停在实际取得 Run 锁前，模拟 executor 排队。"""
        queued.set()
        assert resume.wait(10)
        return create(**kwargs)

    monkeypatch.setattr(value, "create", delayed_create)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run, value, run_id, f"printf stale > {shlex.quote(marker.name)}")
        try:
            assert queued.wait(10)
            if state == "new_owner":
                run_owner["worker_id"] = "next-owner"
            else:
                run_owner["status"] = state
            run_owner["runtime_cleanup_pending"] = True
            assert value.remove_run(uid="one", run_id=run_id)["reason"] == "not_found"
            run_owner["runtime_cleanup_pending"] = False
            resume.set()
            with pytest.raises(SandboxConflictError):
                future.result(timeout=20)
            assert not marker.exists()
            assert all(item["labels"]["yuxi.run_id"] != run_id for item in value.list_containers()["containers"])
        finally:
            resume.set()
            value.remove_run(uid="one", run_id=run_id)
            marker.unlink(missing_ok=True)


@pytest.mark.parametrize("workdir", [None, "projects/demo", "agents/skills"])
def test_global_skill_directory_persists_between_run_containers(manager, workdir):
    """标准入口写入宿主个人目录，重建 Run 后保留，其他用户不可见。"""
    value, root, _ = manager
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    skill = root / "shared/one/workspace/agents/skills/directory-probe/SKILL.md"
    try:
        written = run(
            value,
            first,
            "mkdir -p ~/.agents/skills/directory-probe && printf persistent-skill > ~/.agents/skills/directory-probe/SKILL.md",
            workdir=workdir,
        )
        assert written["exit_code"] == 0, written
        assert skill.read_text() == "persistent-skill"
        value.remove_run(uid="one", run_id=first)
        reread = run(value, second, "cat ~/.agents/skills/directory-probe/SKILL.md", workdir=workdir)
        assert reread["exit_code"] == 0, reread
        assert reread["output"].strip() == "persistent-skill"
        assert not (root / "shared/two/workspace/agents/skills/directory-probe").exists()
        assert (root / "shared/two/workspace/secret.txt").read_text() == "other-user-secret\n"
    finally:
        value.remove_run(uid="one", run_id=first)
        value.remove_run(uid="one", run_id=second)


def test_skills_cli_global_install_writes_real_personal_files(manager):
    """真实 npm CLI 安装本地包，宿主独立回读原字节和实际目录。"""
    value, root, workdir = manager
    source = workdir / "cli-source"
    source.mkdir(exist_ok=True)
    content = "---\nname: cli-probe\ndescription: CLI integration fixture\n---\nCLI_FILE_MARKER\n"
    (source / "SKILL.md").write_text(content)
    cli = ContainerManager(replace(value.settings, network="bridge", home_bytes=256 * 1024 * 1024, memory_bytes=512 * 1024 * 1024))
    run_id = str(uuid.uuid4())
    try:
        installed = run(cli, run_id, "npx -y skills add ./cli-source -g -y", timeout=300)
        assert installed["exit_code"] == 0, installed
        directory = root / "shared/one/workspace/agents/skills/cli-probe"
        assert directory.is_dir() and not directory.is_symlink()
        assert (directory / "SKILL.md").read_text() == content
        removed = run(cli, run_id, "npx -y skills remove cli-probe -g -y", timeout=120)
        assert removed["exit_code"] == 0, removed
        assert not directory.exists()
    finally:
        cli.remove_run(uid="one", run_id=run_id)
