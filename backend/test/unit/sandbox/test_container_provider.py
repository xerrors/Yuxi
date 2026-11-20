"""Container provider 的 Run 归属、结果协议与清理语义。"""

from __future__ import annotations

import threading
import time
from contextlib import nullcontext
from contextvars import copy_context
from datetime import timedelta
from types import SimpleNamespace

import pytest

from yuxi.modules.agents.runtime.sandbox import container as container_module
from yuxi.modules.agents.runtime.sandbox import provider as provider_module
from yuxi.modules.agents.runtime.sandbox.container import ContainerSandboxProvider, SandboxRunnerClient
from yuxi.modules.agents.runtime.sandbox.context import sandbox_run_env, sandbox_run_owner
from yuxi.modules.agents.runtime.sandbox.cube_provider import CubeSandboxProvider
from yuxi.modules.agents.runtime.sandbox import validate_sandbox_configuration
from yuxi.modules.workspace.paths import workspace_uid_dirname
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import StateConflictError, UpstreamError


class FakeRepository:
    """用固定行模拟 PostgreSQL 的 Run 所有权事实。"""

    def __init__(self, row: dict | None = None, runs: list[dict] | None = None):
        self.row = row
        self.runs = runs or []

    def connect(self):
        return nullcontext()

    def run_owner(self, run_id: str):
        return self.row

    def active_runs(self, connection, uid: str):
        return self.runs


class FakeClient:
    """记录 provider 发出的窄 HTTP 契约调用。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.containers: list[dict] = []
        self.block = threading.Event()
        self.cancelled = threading.Event()

    def health(self):
        return {"ok": True}

    def list(self):
        return self.containers

    def stats(self):
        return {"active_vms": 1, "total_vms": 2}

    def remove_run(self, **kwargs):
        self.calls.append(("remove_run", kwargs))
        return {"deleted": True}

    def remove_container(self, container_id):
        self.calls.append(("remove_container", {"container_id": container_id}))

    def cancel_exec(self, **kwargs):
        self.calls.append(("cancel_exec", kwargs))
        self.cancelled.set()
        return True

    def execute(self, **kwargs):
        self.calls.append(("execute", kwargs))
        if self.block.is_set():
            self.cancelled.wait(5)
            return {"output": "", "exit_code": 143, "truncated": False}
        return {"output": "ok", "exit_code": 0, "truncated": False}


@pytest.fixture
def run_context():
    """默认提供活动 Run 与空环境变量快照。"""
    owner_token = sandbox_run_owner.set(("run-1", "worker-1"))
    env_token = sandbox_run_env.set({"A": "B"})
    try:
        yield
    finally:
        sandbox_run_env.reset(env_token)
        sandbox_run_owner.reset(owner_token)


def active_row(**overrides):
    row = {"uid": "one", "status": "running", "worker_id": "worker-1", "lease_expires_at": utc_now() + timedelta(seconds=60)}
    row.update(overrides)
    return row


def test_execute_passes_trusted_run_identity_and_workdir(run_context):
    """命令由可信 ContextVar 绑定 Run，Workdir 不由模型决定。"""
    client = FakeClient()
    provider = ContainerSandboxProvider(client=client, repository=FakeRepository(active_row()))
    output, exit_code, truncated = provider.execute(
        "one",
        "echo hi",
        "/home/user/workspace/projects/demo",
        10,
        workdir="projects/demo",
    )
    assert (output, exit_code, truncated) == ("ok", 0, False)
    call = client.calls[0]
    assert call[0] == "execute"
    assert call[1]["run_id"] == "run-1"
    assert call[1]["owner"] == "worker-1"
    assert call[1]["workdir"] == "projects/demo"
    assert call[1]["envs"] == {"A": "B"}


def test_execute_derives_workdir_from_cwd_when_not_explicit(run_context):
    """Backend 未显式传 Workdir 时只从可信 cwd 派生。"""
    client = FakeClient()
    provider = ContainerSandboxProvider(client=client, repository=FakeRepository(active_row()))
    provider.execute("one", "pwd", "/home/user/workspace/projects/demo", 10)
    assert client.calls[0][1]["workdir"] == "projects/demo"


def test_execute_requires_bound_workdir(run_context):
    """没有 Workdir 的内部执行不能创建一个缺少挂载的 Run 容器。"""
    client = FakeClient()
    provider = ContainerSandboxProvider(client=client, repository=FakeRepository(active_row()))
    with pytest.raises(StateConflictError, match="Workdir") as result:
        provider.execute("one", "pwd", "/home/user/workspace", 10)
    assert result.value.code == "sandbox_workdir_required"
    assert client.calls == []


def test_execute_without_active_run_is_rejected():
    """独立 Skill 或环境验证不能创建无主容器。"""
    provider = ContainerSandboxProvider(client=FakeClient(), repository=FakeRepository(active_row()))
    with pytest.raises(StateConflictError, match="必须属于一个活动 Run") as result:
        provider.execute("one", "echo hi", "/home/user", 10)
    assert result.value.code == "sandbox_run_required"


def test_run_cancel_removes_container_with_attempt_owner(run_context):
    """取消通知 runner 删除本 attempt 的容器，不触碰新 attempt。"""
    client = FakeClient()
    provider = ContainerSandboxProvider(client=client, repository=FakeRepository(active_row()))
    provider.cleanup_run("one", "run-1", "worker-1")
    assert client.calls[-1] == ("remove_run", {"uid": "one", "run_id": "run-1", "owner": "worker-1"})


def test_execute_cancellation_only_targets_the_command(run_context):
    """协程取消通知 runner 取消指定 command_id，并返回 143。"""
    client = FakeClient()
    client.block.set()
    provider = ContainerSandboxProvider(client=client, repository=FakeRepository(active_row()))
    cancelled = threading.Event()
    result = {}

    def run():
        result["value"] = provider.execute(
            "one",
            "sleep 10",
            "/home/user/workspace/projects/demo",
            30,
            cancelled,
            workdir="projects/demo",
        )

    context = copy_context()
    thread = threading.Thread(target=lambda: context.run(run))
    thread.start()
    deadline = time.monotonic() + 3
    while not client.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    cancelled.set()
    thread.join(5)
    assert thread.is_alive() is False
    assert result["value"] == ("", 143, False)
    cancel_calls = [call[1] for call in client.calls if call[0] == "cancel_exec"]
    assert cancel_calls and cancel_calls[0]["command_id"] == client.calls[0][1]["command_id"]


def test_cancel_failure_is_explicit():
    """runner 清理未确认时保留持久清理语义。"""

    class Broken(FakeClient):
        def remove_run(self, **kwargs):
            raise RuntimeError("down")

    provider = ContainerSandboxProvider(client=Broken(), repository=FakeRepository(active_row()))
    with pytest.raises(UpstreamError, match="清理未确认"):
        provider.cleanup_run("one", "run-1")


def test_status_reports_container_backend_without_persistent_vm(run_context):
    """状态接口保留前端字段，但语义改为任务级临时容器。"""
    client = FakeClient()
    client.containers = [{"id": "cid", "state": "running", "labels": {"yuxi.uid": workspace_uid_dirname("one")}}]
    provider = ContainerSandboxProvider(
        client=client,
        repository=FakeRepository(active_row(), runs=[{"id": "run-1", "thread_id": "t", "turn_id": "u", "status": "running"}]),
    )
    status = provider.status("one")
    assert status["backend"] == "container"
    assert status["state"] == "running"
    assert status["busy"] is True
    assert status["idle_timeout_seconds"] == 0
    assert status["active_runs"][0]["id"] == "run-1"


def test_status_does_not_claim_success_when_runner_is_down(run_context):
    """runner 不可用必须显示失败，而不是伪造空环境。"""

    class Broken(FakeClient):
        def list(self):
            raise RuntimeError("down")

    status = ContainerSandboxProvider(client=Broken(), repository=FakeRepository(active_row())).status("one")
    assert "不可用" in status["error"]


def test_rebuild_and_disk_usage_are_explicitly_unsupported():
    """任务级容器没有用户级重建或持久 Home 磁盘语义。"""
    provider = ContainerSandboxProvider(client=FakeClient(), repository=FakeRepository(active_row()))
    with pytest.raises(StateConflictError) as rebuild:
        provider.rebuild("one")
    assert rebuild.value.code == "sandbox_ephemeral"
    with pytest.raises(UpstreamError, match="没有持久主目录"):
        provider.disk_usage("one")


def test_maintain_removes_terminal_owner_changed_and_expired_containers():
    """对账只保留仍由活动 lease 拥有且 uid 匹配的容器。"""
    safe_uid = workspace_uid_dirname("one")
    created = str(int(time.time()))

    def container(container_id, run_id, owner):
        return {
            "id": container_id,
            "state": "running",
            "labels": {"yuxi.run_id": run_id, "yuxi.uid": safe_uid, "yuxi.owner": owner, "yuxi.created_at": created},
        }

    containers = [
        container("terminal", "r1", "w"),
        container("owner", "r2", "old"),
        container("live", "r3", "worker-1"),
        container("lease", "r4", "worker-1"),
        container("uid", "r5", "worker-1"),
    ]
    creating = container("creating", "r6", "worker-1")
    creating["state"] = "created"
    stuck = container("stuck", "r7", "worker-1")
    stuck["state"] = "created"
    stuck["labels"]["yuxi.created_at"] = str(int(time.time()) - 61)
    containers.extend([creating, stuck])
    client = FakeClient()
    client.containers = containers
    rows = {
        "r1": active_row(status="completed"),
        "r2": active_row(worker_id="worker-1"),
        "r3": active_row(worker_id="worker-1"),
        "r4": active_row(worker_id="worker-1", lease_expires_at=utc_now() - timedelta(seconds=1)),
        "r5": active_row(uid="two", worker_id="worker-1"),
        "r6": active_row(worker_id="worker-1"),
        "r7": active_row(worker_id="worker-1"),
    }

    class Repo(FakeRepository):
        def run_owner(self, run_id: str):
            return rows.get(run_id)

    ContainerSandboxProvider(client=client, repository=Repo()).maintain()
    removed = [call[1]["container_id"] for call in client.calls if call[0] == "remove_container"]
    assert removed == ["terminal", "owner", "lease", "uid", "stuck"]


def test_maintain_keeps_active_skill_fetch_and_removes_expired_requests():
    """获取请求不查询 Run；活动请求保留，过期或非法期限的请求回收。"""

    class Repository(FakeRepository):
        """获取容器没有业务 Run 记录。"""

        def run_owner(self, run_id):
            """任何 Run 查询都说明请求被错误归类。"""
            pytest.fail("Skill 获取不应查询 Agent Run")

    class Client(FakeClient):
        """维护实际剩余容器集合，验证清理后的状态。"""

        def remove_container(self, container_id):
            """仅删除本次对账选择的具体容器。"""
            self.containers = [item for item in self.containers if item["id"] != container_id]

    client = Client()
    for name, deadline in [("active", str(time.time() + 60)), ("expired", str(time.time() - 1)), ("invalid", "bad"), ("missing", None)]:
        client.containers.append({"id": name, "state": "running", "labels": {"yuxi.purpose": "skill-fetch", "yuxi.expires_at": deadline}})
    ContainerSandboxProvider(client=client, repository=Repository()).maintain()
    assert [item["id"] for item in client.containers] == ["active"]


def test_maintain_stale_snapshot_does_not_delete_replacement():
    """对账读完旧快照后同 Run 重建，清理仍只能删除旧容器 ID。"""
    old = {
        "id": "old-container",
        "state": "exited",
        "labels": {"yuxi.run_id": "run-1", "yuxi.uid": "one", "yuxi.owner": "worker-1"},
    }
    replacement = {**old, "id": "new-container", "state": "running"}

    class ReplacingClient(FakeClient):
        def list(self):
            self.containers = [replacement]
            return [old]

        def remove_run(self, **kwargs):
            self.containers = []

        def remove_container(self, container_id):
            self.containers = [item for item in self.containers if item["id"] != container_id]

    client = ReplacingClient()
    ContainerSandboxProvider(client=client, repository=FakeRepository(active_row())).maintain()
    assert client.containers == [replacement]


def test_sandbox_runner_client_rejects_missing_token(monkeypatch):
    """未配置 token 时不能发起 runner 调用。"""
    client = SandboxRunnerClient(base_url="http://127.0.0.1:1", token="")
    with pytest.raises(UpstreamError, match="SANDBOX_RUNNER_TOKEN"):
        client.health()
    with pytest.raises(UpstreamError, match="SANDBOX_RUNNER_TOKEN"):
        client.stats()


def test_factory_selects_backend_and_rejects_none(monkeypatch):
    """只允许 cube 与 container；未知值在启动时失败。"""
    monkeypatch.setattr(provider_module, "_sandbox_provider", None)
    monkeypatch.setenv("SANDBOX_BACKEND", "cube")
    monkeypatch.setenv("CUBE_API_URL", "http://node")
    monkeypatch.setenv("CUBE_PROXY_NODE_IP", "node")
    monkeypatch.setenv("CUBE_NODE_USER_DATA_DIR", "/data/threads")
    monkeypatch.setenv("CUBE_NODE_SKILL_PROJECTION_DIR", "/data/skills")
    assert provider_module.get_sandbox_provider().backend == "cube"
    monkeypatch.setattr(provider_module, "_sandbox_provider", None)
    monkeypatch.setenv("SANDBOX_BACKEND", "container")
    monkeypatch.setenv("SANDBOX_RUNNER_TOKEN", "t" * 32)
    monkeypatch.setenv("SANDBOX_CONTAINER_IMAGE", "yuxi-sandbox:test")
    monkeypatch.setenv("YUXI_ENV", "development")
    assert provider_module.get_sandbox_provider().backend == "container"
    monkeypatch.setattr(provider_module, "_sandbox_provider", None)
    monkeypatch.setenv("SANDBOX_BACKEND", "none")
    with pytest.raises(RuntimeError, match="cube 或 container"):
        provider_module.get_sandbox_provider()


def test_factory_defaults_to_container_and_requires_token(monkeypatch):
    """未选择后端时使用 container，缺认证仍明确失败。"""
    monkeypatch.setattr(provider_module, "_sandbox_provider", None)
    monkeypatch.delenv("SANDBOX_BACKEND", raising=False)
    monkeypatch.delenv("SANDBOX_RUNNER_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="SANDBOX_RUNNER_TOKEN"):
        provider_module.get_sandbox_provider()
    monkeypatch.setenv("SANDBOX_RUNNER_TOKEN", "t" * 32)
    assert provider_module.get_sandbox_provider().backend == "container"


def test_cube_health_requires_real_node_configuration(monkeypatch):
    """显式 cube 模式缺节点配置时启动失败，不静默回退或使用 localhost。"""
    monkeypatch.setenv("SANDBOX_BACKEND", "cube")
    for name in ("CUBE_API_URL", "CUBE_PROXY_NODE_IP", "CUBE_NODE_USER_DATA_DIR", "CUBE_NODE_SKILL_PROJECTION_DIR"):
        monkeypatch.delenv(name, raising=False)
    provider = CubeSandboxProvider(
        adapter=SimpleNamespace(health=lambda: {"ok": True}),
        repository=FakeRepository(),
    )
    with pytest.raises(RuntimeError, match="SANDBOX_BACKEND=cube"):
        provider.health()


def test_container_configuration_only_requires_client_credentials(monkeypatch):
    """业务进程只校验连接凭据，镜像和资源配置由 runner 拥有。"""
    monkeypatch.setenv("SANDBOX_BACKEND", "container")
    monkeypatch.delenv("SANDBOX_RUNNER_TOKEN", raising=False)
    monkeypatch.setenv("YUXI_ENV", "production")
    for name in ("SANDBOX_CONTAINER_IMAGE", "SANDBOX_CONTAINER_IMAGE_DIGEST", "SANDBOX_CONTAINER_MAX_LIFETIME_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="SANDBOX_RUNNER_TOKEN"):
        validate_sandbox_configuration()
    monkeypatch.setenv("SANDBOX_RUNNER_TOKEN", "t" * 32)
    assert validate_sandbox_configuration() == "container"


def test_container_module_exports_provider():
    """公开入口保持单一 Provider 类型。"""
    assert container_module.ContainerSandboxProvider is ContainerSandboxProvider
