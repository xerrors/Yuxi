"""固定 Skill 获取协议、最小挂载和请求回收约束。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from test.unit.sandbox.test_sandbox_runner_manager import FakeEngine, settings
from yuxi.modules.agents.repositories.sandbox import SandboxRepository
from yuxi.sandbox_runner.manager import ContainerManager, SandboxValidationError

REQUEST_ID = "remote-skill-" + "a" * 32


class SkillEngine(FakeEngine):
    """记录可信 CLI 的实际 argv 和环境，不执行外部下载。"""

    def __init__(self):
        """保留容器配置与失败注入位置。"""
        super().__init__()
        self.execution = None
        self.fail_at = None

    def create_exec(self, container_id, **kwargs):
        """保留协议参数，模拟获取进程创建失败。"""
        self.execution = kwargs
        if self.fail_at == "create":
            raise RuntimeError("create failed")
        return "fetch-exec"

    def start_exec(self, exec_id, **kwargs):
        """返回固定的 CLI 输出或模拟读取超时。"""
        if self.fail_at == "read":
            raise TimeoutError("read failed")
        return b"Available Skills\n\ndemo\n", False

    def inspect_exec(self, exec_id):
        """只有可靠的进程终态才能成为获取结果。"""
        if self.fail_at == "inspect":
            raise RuntimeError("inspect failed")
        return 0


@pytest.fixture
def fetch_manager(tmp_path, monkeypatch):
    """请求没有 Run，目录只覆盖本次获取所需的两个子目录。"""
    request = tmp_path / "shared/one/workspace/.remote-skills" / REQUEST_ID
    (request / "workspace").mkdir(parents=True)
    (tmp_path / "shared/one/workspace/private.txt").write_text("PRIVATE_WORKSPACE_MARKER")
    engine = SkillEngine()
    manager = ContainerManager(settings(tmp_path), engine=engine)

    def reject_run_lookup(*_args):
        """获取流程不能伪造或查询聊天执行身份。"""
        pytest.fail("Skill 获取不属于 Agent Run")

    monkeypatch.setattr(SandboxRepository, "run_owner", reject_run_lookup)
    return manager, engine, request


@pytest.mark.parametrize(
    "arguments",
    [
        ["add", "https://github.com/owner/repo", "--list"],
        ["add", "https://modelscope.cn/skills/demo", "--skill", "demo", "-g", "-y", "--copy"],
        ["find", "web; touch /tmp/injected"],
    ],
)
def test_fetch_uses_one_request_mount_and_fixed_cli_without_run(fetch_manager, arguments):
    """可联网的获取进程看不到工作区、共享 Skill 或应用环境。"""
    manager, engine, request = fetch_manager
    result = manager.fetch_skills(uid="one", request_id=REQUEST_ID, args=["npx", "-y", "skills", *arguments])
    assert result == {"output": "Available Skills\n\ndemo\n", "exit_code": 0, "truncated": False}
    config = engine.configs[0]
    mounts = config["HostConfig"]["Mounts"]
    assert len(mounts) == 1
    assert Path(mounts[0]["Source"]) == request
    assert mounts[0]["Target"] == f"/home/user/workspace/.remote-skills/{REQUEST_ID}"
    assert config["HostConfig"]["NetworkMode"] == "bridge"
    assert config["HostConfig"]["ReadonlyRootfs"] is True
    assert config["HostConfig"]["CapDrop"] == ["ALL"]
    assert "yuxi.run_id" not in config["Labels"]
    assert config["Labels"]["yuxi.purpose"] == "skill-fetch"
    assert time.time() < float(config["Labels"]["yuxi.expires_at"]) <= time.time() + 360
    assert engine.execution["cmd"][5:] == ["/usr/local/bin/skills", *arguments]
    assert not any(key.startswith(("POSTGRES_", "SANDBOX_RUNNER_TOKEN=", "OPENAI_")) for key in engine.execution["env"])
    assert not (request.parents[1] / "agents").exists()
    assert "TMPDIR=/tmp" in config["Env"]
    assert engine.removed == ["cid1"]
    assert engine.container is None


@pytest.mark.parametrize("failure", ["create", "read", "inspect"])
def test_fetch_failure_removes_container_before_return(fetch_manager, failure):
    """启动、输出读取和终态失败均不能遗留联网容器。"""
    manager, engine, _ = fetch_manager
    engine.fail_at = failure
    with pytest.raises((RuntimeError, TimeoutError), match="failed"):
        manager.fetch_skills(uid="one", request_id=REQUEST_ID, args=["npx", "-y", "skills", "find", "demo"])
    assert engine.container is None
    assert engine.removed == ["cid1"]


@pytest.mark.parametrize(
    "args",
    [
        ["bash", "-c", "echo", "arbitrary", "command"],
        ["npx", "-y", "skills", "remove", "demo"],
        ["npx", "-y", "skills", "find", "--help"],
        ["npx", "-y", "skills", "add", "file:///app", "--list"],
        ["npx", "-y", "skills", "add", "https://[", "--list"],
        ["npx", "-y", "skills", "find", "\0"],
        ["npx", "-y", "skills", "find", "x" * 4097],
        ["npx", "-y", "skills", "add", "https://user:pass@example.com/repo", "--list"],
        ["npx", "-y", "skills", "add", "https://github.com/owner/repo", "--all"],
        ["npx", "-y", "skills", "add", "https://github.com/owner/repo", "--skill", "../escape", "-g", "-y", "--copy"],
    ],
)
def test_fetch_rejects_arbitrary_commands_and_options(fetch_manager, args):
    """认证后的替代调用路径也只能使用固定获取协议。"""
    manager, engine, _ = fetch_manager
    with pytest.raises(SandboxValidationError):
        manager.fetch_skills(uid="one", request_id=REQUEST_ID, args=args)
    assert engine.configs == []


@pytest.mark.parametrize("expires_at", [str(time.time() - 1), "invalid"])
def test_expired_fetch_is_removed_without_run_lookup(fetch_manager, expires_at):
    """runner 崩溃遗留的请求按自己的期限回收。"""
    manager, engine, _ = fetch_manager
    engine.create_container(
        name="fetch-orphan",
        config={"Labels": {"yuxi.scope": manager.scope, "yuxi.purpose": "skill-fetch", "yuxi.expires_at": expires_at}},
    )
    assert manager.remove_expired() == 1
    assert engine.container is None


def test_fetch_rejects_request_path_traversal_before_creating_container(fetch_manager):
    """请求标识不能选择另一目录或扩展挂载范围。"""
    manager, engine, _ = fetch_manager
    with pytest.raises(SandboxValidationError, match="请求标识非法"):
        manager.fetch_skills(uid="one", request_id="../other-request", args=["npx", "-y", "skills", "find", "demo"])
    assert engine.configs == []
