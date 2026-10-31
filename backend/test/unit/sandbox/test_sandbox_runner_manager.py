"""Sandbox runner 的挂载、资源限制与幂等生命周期。"""

from __future__ import annotations

import os
import stat
import threading
import time
from dataclasses import replace
from datetime import timedelta

from yuxi.modules.agents.repositories.sandbox import SandboxRepository
from yuxi.shared.datetime import utc_now
from pathlib import Path

import pytest

from yuxi.sandbox_runner.engine import EngineError, ExecDeadlineExceeded
from yuxi.sandbox_runner.manager import (
    ContainerManager,
    SandboxCapacityError,
    SandboxConflictError,
    SandboxError,
    SandboxValidationError,
)
from yuxi.sandbox_runner.settings import RunnerSettings


class FakeEngine:
    """记录容器配置，不访问真实 Docker。"""

    def __init__(self, container: dict | None = None):
        self.container = container
        self.configs: list[dict] = []
        self.removed: list[str] = []
        self.created = 0
        self.probe_result = (0, "")
        self.probes: list[list[str]] = []

    def image_identity(self, image):
        return {"id": "sha256:" + "a" * 64, "repo_digests": []}

    def version(self):
        return {"Version": "test"}

    def ping(self):
        return "OK"

    def inspect_container(self, reference):
        if self.container and reference in {self.container["Id"], self.container["Name"].lstrip("/")}:
            return self.container
        return None

    def list_containers(self, *, scope):
        if not self.container:
            return []
        if self.container["Config"]["Labels"].get("yuxi.scope") != scope:
            return []
        return [
            {
                "Id": self.container["Id"],
                "State": "running" if self.container["State"].get("Running") else "exited",
                "Labels": self.container["Config"]["Labels"],
            }
        ]

    def create_container(self, *, name, config):
        self.created += 1
        self.configs.append(config)
        self.container = {
            "Id": f"cid{self.created}",
            "Config": {"Labels": config["Labels"]},
            "State": {"Running": True},
            "Name": f"/{name}",
        }
        return {"Id": self.container["Id"]}

    def start_container(self, container_id):
        self.container["State"] = {"Running": True}

    def remove_container(self, container_id):
        self.removed.append(container_id)
        self.container = None

    def run_probe(self, container_id, *, cmd, env, workdir, user, timeout=15, output_budget=65536):
        self.probes.append(cmd)
        return self.probe_result


def settings(tmp_path: Path, *, uid: int | None = None, max_containers: int = 2) -> RunnerSettings:
    """构造与宿主机当前用户一致的测试配置。"""
    skills = tmp_path / "skills"
    skills.mkdir(exist_ok=True)
    return RunnerSettings(
        token="t" * 32,
        socket_path="/nonexistent",
        image="yuxi-sandbox:test",
        image_digest="",
        network="none",
        memory_bytes=256 * 1024 * 1024,
        cpus=0.5,
        pids_limit=64,
        home_bytes=16 * 1024 * 1024,
        tmp_bytes=16 * 1024 * 1024,
        max_containers=max_containers,
        max_execs=2,
        max_lifetime_seconds=600,
        host_data_dir=str(tmp_path),
        host_skill_dir=str(skills),
        data_mount=str(tmp_path),
        skill_mount=str(skills),
        run_uid=uid if uid is not None else os.getuid(),
        run_gid=os.getgid(),
    )


@pytest.fixture(autouse=True)
def run_owner(monkeypatch):
    """固定业务归属，测试只替换 PostgreSQL 边界。"""
    row = {
        "uid": "one",
        "status": "running",
        "worker_id": "worker:1",
        "lease_expires_at": utc_now() + timedelta(hours=1),
        "runtime_cleanup_pending": False,
    }
    monkeypatch.setattr(SandboxRepository, "run_owner", lambda _self, _run_id: dict(row))
    return row


@pytest.fixture
def manager(tmp_path):
    """建立真实 Workdir 与授权 Skill 投影。"""
    workdir = tmp_path / "shared" / "one" / "workspace" / "projects" / "demo"
    workdir.mkdir(parents=True)
    (workdir / "a.txt").write_text("bytes")
    (tmp_path / "skills" / "one" / "demo").mkdir(parents=True)
    engine = FakeEngine()
    value = ContainerManager(settings(tmp_path), engine=engine)
    return value, engine, workdir


@pytest.mark.parametrize("environment", ["development", "production"])
def test_unpinned_or_mismatched_image_fails_startup(tmp_path, environment):
    """生产未固定 digest 或显式 digest 不匹配时 runner 不提供执行能力。"""
    (tmp_path / "shared" / "one" / "workspace" / "projects" / "demo").mkdir(parents=True)
    config = replace(settings(tmp_path), environment=environment)
    if environment == "production":
        with pytest.raises(SandboxError, match="digest"):
            ContainerManager(config, engine=FakeEngine())
    else:
        ContainerManager(config, engine=FakeEngine())
    config = replace(config, image_digest="sha256:" + "b" * 64)
    with pytest.raises(SandboxError, match="digest"):
        ContainerManager(config, engine=FakeEngine())


def test_create_mounts_workdir_personal_skills_and_readonly_shared_skills(manager):
    """容器挂载当前 Workdir、个人技能与只读共享投影。"""
    value, engine, workdir = manager
    result = value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    assert result["state"] == "running"
    config = engine.configs[0]
    mounts = {(item["Target"], item["ReadOnly"]) for item in config["HostConfig"]["Mounts"]}
    assert mounts == {
        ("/home/user/workspace/projects/demo", False),
        ("/home/user/skills", True),
        ("/home/user/workspace/agents/skills", False),
    }
    assert config["Image"] == "sha256:" + "a" * 64
    assert config["User"] == f"{os.getuid()}:{os.getgid()}"
    assert config["HostConfig"]["ReadonlyRootfs"] is True
    assert config["HostConfig"]["CapDrop"] == ["ALL"]
    assert config["HostConfig"]["SecurityOpt"] == ["no-new-privileges"]
    assert config["HostConfig"]["NetworkMode"] == "none"
    assert config["HostConfig"]["PidsLimit"] == 64
    assert config["HostConfig"]["Memory"] == 256 * 1024 * 1024
    assert config["HostConfig"]["NanoCpus"] == 500_000_000
    workdir_mount = next(item for item in config["HostConfig"]["Mounts"] if item["Target"].endswith("/demo"))
    assert Path(workdir_mount["Source"]) == workdir


def test_local_stdin_path_is_separate_from_docker_host_source(tmp_path):
    """Compose 中 runner 只看到 /sandbox-data；stdin 必须写本地挂载而不是宿主 Source。"""
    local_root = tmp_path / "local-data"
    local_skills = tmp_path / "local-skills"
    workdir = local_root / "shared" / "one" / "workspace" / "projects" / "demo"
    workdir.mkdir(parents=True)
    (local_skills / "one").mkdir(parents=True)
    config = replace(
        settings(tmp_path),
        data_mount=str(local_root),
        host_data_dir=str(tmp_path / "host-only-not-mounted"),
        skill_mount=str(local_skills),
        host_skill_dir=str(tmp_path / "host-skills"),
    )
    engine = FakeEngine()
    value = ContainerManager(config, engine=engine)
    local, host = value._validated_workdir("one", "projects/demo")
    assert local == workdir
    assert host == tmp_path / "host-only-not-mounted" / "shared" / "one" / "workspace" / "projects" / "demo"
    value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    mount = next(item for item in engine.configs[0]["HostConfig"]["Mounts"] if item["Target"].endswith("/demo"))
    assert Path(mount["Source"]) == host
    stdin = local / ".yuxi-stdin-run-1-cmd"
    value._write_stdin(stdin, b"payload")
    assert stdin.read_bytes() == b"payload"
    value._delete_stdin_file(stdin)


def test_create_is_idempotent_and_owner_change_conflicts(manager):
    """同名容器只复用同一 attempt；新 owner 必须等旧容器清理。"""
    value, engine, _ = manager
    first = value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    second = value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    assert first["id"] == second["id"]
    assert engine.created == 1
    with pytest.raises(SandboxConflictError, match="owner"):
        value.create(uid="one", run_id="run-1", owner="worker:2", workdir="projects/demo")


def test_create_rejects_symlink_workdir(manager, tmp_path):
    """Workdir 解析遇到符号链接必须拒绝。"""
    value, _, _ = manager
    target = tmp_path / "outside"
    target.mkdir()
    (tmp_path / "shared" / "one" / "workspace" / "link").symlink_to(target, target_is_directory=True)
    with pytest.raises(SandboxValidationError, match="符号链接"):
        value.create(uid="one", run_id="run-1", owner="worker:1", workdir="link")


def test_create_rejects_traversal_and_unknown_uid(manager):
    """Workdir 词法越界与空 uid 不进入 Docker。"""
    value, _, _ = manager
    with pytest.raises(SandboxValidationError, match="workdir"):
        value.create(uid="one", run_id="run-1", owner="worker:1", workdir="../two")
    with pytest.raises(SandboxValidationError, match="uid"):
        value.create(uid="", run_id="run-1", owner="worker:1", workdir="projects/demo")


def test_capacity_limit_rejects_new_container(tmp_path):
    """达到全局容器上限时显式拒绝，不排队创建。"""
    (tmp_path / "shared" / "one" / "workspace" / "projects" / "demo").mkdir(parents=True)

    class Full(FakeEngine):
        def inspect_container(self, reference):
            return None

        def list_containers(self, *, scope):
            return [{"Id": f"cid{index}", "State": "running", "Labels": {"yuxi.managed": "true"}} for index in range(2)]

    value = ContainerManager(settings(tmp_path, max_containers=2), engine=Full())
    with pytest.raises(SandboxCapacityError):
        value.create(uid="one", run_id="run-2", owner="worker:1", workdir="projects/demo")


def test_remove_is_idempotent_and_owner_scoped(manager):
    """删除只匹配当前 owner，重复删除成功。"""
    value, engine, _ = manager
    value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    assert value.remove_run(uid="one", run_id="run-1", owner="worker:2") == {"deleted": False, "reason": "owner_mismatch"}
    assert value.remove_run(uid="one", run_id="run-1", owner="worker:1") == {"deleted": True}
    assert value.remove_run(uid="one", run_id="run-1", owner="worker:1") == {"deleted": False, "reason": "not_found"}


def test_remove_rejects_same_name_with_other_uid(manager):
    """同名容器不能由另一个 uid 删除。"""
    value, _, _ = manager
    value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    with pytest.raises(SandboxConflictError, match="标签"):
        value.remove_run(uid="two", run_id="run-1")


def test_expired_container_is_removed(manager):
    """超过最大存活时间的容器由 TTL 兜底清理。"""
    value, engine, _ = manager
    value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    engine.container["Config"]["Labels"]["yuxi.created_at"] = str(int(time.time()) - 601)
    assert value.remove_expired() == 1
    assert engine.removed


def test_independent_data_roots_cannot_manage_each_others_containers(manager, tmp_path):
    """共享 daemon 时，独立数据槽位不能枚举、复用或删除另一槽位的容器。"""
    value, engine, _ = manager
    created = value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    other_root = tmp_path / "other-deployment"
    other_root.mkdir()
    other = ContainerManager(settings(other_root), engine=engine)

    assert other.list_containers() == {"containers": []}
    assert other.stats() == {"active_containers": 0, "total_containers": 0}
    assert other.remove_run(uid="one", run_id="run-1") == {"deleted": False, "reason": "not_found"}
    with pytest.raises(SandboxConflictError, match="部署"):
        other.remove_container(created["id"])
    assert other.remove_all() == 0
    engine.container["Config"]["Labels"]["yuxi.created_at"] = str(int(time.time()) - 601)
    assert other.remove_expired() == 0
    assert engine.inspect_container(created["id"]) is not None
    assert engine.removed == []


def test_direct_removal_rejects_container_without_deployment_scope(manager):
    """直接删除入口不能接管未标注部署身份的历史容器。"""
    value, engine, _ = manager
    created = value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    engine.container["Config"]["Labels"].pop("yuxi.scope", None)
    with pytest.raises(SandboxConflictError, match="部署"):
        value.remove_container(created["id"])
    with pytest.raises(SandboxConflictError, match="部署"):
        value.remove_run(uid="one", run_id="run-1")
    with pytest.raises(SandboxConflictError, match="scope"):
        value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    assert engine.removed == []


def test_stdin_file_is_owned_and_removed(manager):
    """stdin 临时文件写到 Workdir，身份匹配容器用户，执行后删除。"""
    value, _, workdir = manager
    path = workdir / ".yuxi-stdin-test"
    value._write_stdin(path, b"payload")
    assert path.read_bytes() == b"payload"
    assert path.stat().st_uid == os.getuid()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    value._delete_stdin_file(path)
    assert not path.exists()


def test_trusted_env_rejects_reserved_override_and_keeps_markers_last(tmp_path):
    """用户环境不能覆盖 runner 的执行标识。"""
    value = ContainerManager.__new__(ContainerManager)
    value.settings = settings(tmp_path)
    env = value._trusted_env(
        {"A": "B", "YUXI_EXECUTION_ID": "forged"},
        command_id="c" * 32,
        run_id="run-1",
        uid="one",
    )
    assert env.count(f"YUXI_EXECUTION_ID={'c' * 32}") == 1
    assert "YUXI_EXECUTION_ID=forged" not in env
    with pytest.raises(SandboxValidationError):
        value._trusted_env({"BAD-KEY": "x"}, command_id="c" * 32, run_id="run-1", uid="one")


def test_cancel_is_not_acknowledged_before_exec_starts(manager):
    """cancel 在 exec 启动前返回 False，provider 会继续重试而不是误报成功。"""
    value, engine, _ = manager
    key = ("run-1", "cmd")
    with value._exec_lock:
        value._execs[key] = {
            "run_id": "run-1",
            "container_id": "cid",
            "uid": "one",
            "owner": "worker:1",
            "cancel": threading.Event(),
            "timed_out": False,
            "exec_id": None,
            "started": False,
        }
    try:
        assert value.cancel_exec(uid="one", run_id="run-1", owner="worker:1", command_id="cmd") is False
        with value._exec_lock:
            value._execs[key]["exec_id"] = "exec-1"
            value._execs[key]["started"] = True
        engine.probe_result = (3, "")
        assert value.cancel_exec(uid="one", run_id="run-1", owner="worker:1", command_id="cmd") is False
        engine.probe_result = (0, "")
        assert value.cancel_exec(uid="one", run_id="run-1", owner="worker:1", command_id="cmd") is True
    finally:
        with value._exec_lock:
            value._execs.pop(key, None)


def test_stdin_cleanup_only_removes_owner_run(manager):
    """共享 Workdir 的其他协作 Run stdin 文件不能被本 Run 清理删除。"""
    value, _, workdir = manager
    own = workdir / ".yuxi-stdin-run-a-cmd"
    other = workdir / ".yuxi-stdin-run-b-cmd"
    own.write_bytes(b"a")
    other.write_bytes(b"b")
    value._cleanup_stdin_dir({"yuxi.uid": "one", "yuxi.workdir": "projects/demo"}, "run-a")
    assert not own.exists()
    assert other.exists()


@pytest.mark.parametrize("failure", ["deadline", "timeout", "cancel", "stream"])
@pytest.mark.parametrize("delete_fails", [False, True])
def test_unconfirmed_command_stop_never_returns_successful_timeout(manager, monkeypatch, failure, delete_fails):
    """停止未确认时销毁确切容器并报错，删除失败也不能返回普通超时结果。"""
    value, engine, _ = manager
    monkeypatch.setattr(engine, "create_exec", lambda *_args, **_kwargs: "exec-1", raising=False)
    monkeypatch.setattr(value, "_cancel_until_ack", lambda *_args: False)

    def broken_stream(*_args, **_kwargs):
        """模拟执行流断开，保留所属命令的超时或取消原因。"""
        entry = value._execs[("run-1", "cmd")]
        if failure == "deadline":
            raise ExecDeadlineExceeded(b"partial", False)
        if failure == "timeout":
            entry["timed_out"] = True
        elif failure == "cancel":
            entry["cancel"].set()
        raise EngineError("stream unavailable")

    monkeypatch.setattr(engine, "start_exec", broken_stream, raising=False)
    if delete_fails:

        def unavailable(_container_id):
            """删除失败继续由现有 Run 容器与 TTL 清理责任兜底。"""
            raise EngineError("delete unavailable")

        monkeypatch.setattr(engine, "remove_container", unavailable)
    with pytest.raises(EngineError if delete_fails else SandboxError, match="delete unavailable" if delete_fails else "命令停止未确认"):
        value.execute(
            uid="one",
            run_id="run-1",
            owner="worker:1",
            command_id="cmd",
            command="sleep 30",
            cwd="/home/user",
            workdir="projects/demo",
            timeout_seconds=30,
            output_budget=1024,
            stdin_b64=None,
            python=False,
            envs={},
        )
    assert (engine.container is not None) == delete_fails
    if not delete_fails:
        assert engine.removed == ["cid1"]


def test_container_cwd_rejects_traversal(manager):
    """容器 cwd 不接受词法越界或对象 URL。"""
    value, _, _ = manager
    for cwd in ("", "   ", "relative", "/home/user/../etc", "https://host/a", "/home/user/a\\b", "/home/user/\0"):
        with pytest.raises(SandboxValidationError, match="^cwd 非法$") as error:
            value._container_cwd(cwd)
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
    assert value._container_cwd(" /home//user/./workspace/ ") == "/home/user/workspace"
    assert value._container_cwd("/") == "/"


@pytest.mark.parametrize(
    "changes",
    [
        {"uid": "two"},
        {"worker_id": "worker:2"},
        {"status": "cancel_requested"},
        {"runtime_cleanup_pending": True},
        {"lease_expires_at": None},
        {"lease_expires_at": utc_now() - timedelta(seconds=1)},
    ],
)
def test_create_rejects_stale_run_before_docker(manager, run_owner, changes):
    """归属、lease 或清理责任不满足时不能产生容器副作用。"""
    value, engine, _ = manager
    run_owner.update(changes)
    with pytest.raises(SandboxConflictError):
        value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    assert engine.configs == []


def test_run_owner_is_read_after_acquiring_creation_lock(manager, run_owner, monkeypatch):
    """等待创建锁期间失效的 Run 不得使用锁外旧快照。"""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

    value, engine, _ = manager
    lock = value._run_lock("run-1")
    waiting = threading.Event()

    @contextmanager
    def waiting_lock(_run_id):
        """标记创建已到达锁边界，调用者控制释放时机。"""
        waiting.set()
        with lock:
            yield

    monkeypatch.setattr(value, "_run_lock", waiting_lock)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with lock:
            future = executor.submit(value.create, uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
            assert waiting.wait(5)
            run_owner["status"] = "yielded"
        with pytest.raises(SandboxConflictError):
            future.result(timeout=5)
    assert engine.configs == []


@pytest.mark.parametrize("unavailable", [False, True])
def test_run_owner_lookup_fails_closed(manager, monkeypatch, unavailable):
    """Run 不存在或数据库不可达都不能创建无主容器。"""
    value, engine, _ = manager

    def lookup(_run_id):
        """模拟数据库缺失事实或连接失败。"""
        if unavailable:
            raise ConnectionError("database unavailable")
        return None

    monkeypatch.setattr(value.repository, "run_owner", lookup)
    with pytest.raises(ConnectionError if unavailable else SandboxConflictError):
        value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    assert engine.configs == []


@pytest.mark.parametrize("component", ["agents", "skills"])
def test_personal_skill_mount_rejects_symlinked_components(manager, tmp_path, component):
    """用户可写目录不能通过链接将别的目录挂进容器。"""
    value, engine, workdir = manager
    workspace = workdir.parents[1]
    target = tmp_path / "outside"
    target.mkdir()
    (target / "sentinel").write_text("keep")
    link = workspace / "agents" if component == "agents" else workspace / "agents" / "skills"
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SandboxValidationError, match="个人 Skill"):
        value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    assert not engine.configs
    assert (target / "sentinel").read_text() == "keep"


def test_skill_link_initialization_failure_removes_container(manager):
    """映射建立失败不能返回可用环境，已创建的容器必须回收。"""
    value, engine, _ = manager
    engine.probe_result = (1, "failed")
    with pytest.raises(SandboxError, match="个人 Skill"):
        value.create(uid="one", run_id="run-1", owner="worker:1", workdir="projects/demo")
    assert engine.container is None
    assert engine.removed == ["cid1"]
