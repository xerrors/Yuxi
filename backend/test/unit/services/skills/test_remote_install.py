from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.modules.extensions.skills import remote as svc


@pytest.fixture(autouse=True)
def remote_skill_source_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def get_policy(_db=None) -> dict[str, list[str]]:
        return {"allowed_hosts": ["github.com", "modelscope.cn"]}

    monkeypatch.setattr(svc, "remote_skill_source_policy", SimpleNamespace(get=get_policy))


class _FakeRemoteSkillSandbox:
    def __init__(self, *, output: str = "installed", available: set[str] | None = None, run_error: bool = False):
        self.output = output
        self.available = available or set()
        self.run_error = run_error
        self.calls: list[list[str]] = []
        self.download_calls: list[str] = []
        self.cleaned = False

    async def run(self, args: list[str]) -> str:
        self.calls.append(args)
        if self.run_error:
            raise ValueError("CLI failed")
        return self.output

    async def download_skill(self, name: str, target_dir: Path) -> None:
        self.download_calls.append(name)
        if name not in self.available:
            raise ValueError("missing")
        target_dir.mkdir(parents=True)

    async def cleanup(self) -> None:
        self.cleaned = True


def _use_fake_sandbox(monkeypatch: pytest.MonkeyPatch, sandbox: _FakeRemoteSkillSandbox) -> None:
    monkeypatch.setattr(svc, "_RemoteSkillRequest", SimpleNamespace(create=lambda uid: sandbox))


@pytest.mark.parametrize(
    ("source", "allowed_hosts", "expected"),
    [
        ("anthropics/skills", ["github.com", "modelscope.cn"], "https://github.com/anthropics/skills"),
        (
            "https://github.com/anthropics/skills.git",
            ["github.com", "modelscope.cn"],
            "https://github.com/anthropics/skills",
        ),
        (
            "https://modelscope.cn/skills/@pskoett/self-improving-agent/",
            ["github.com", "modelscope.cn"],
            "https://modelscope.cn/skills/@pskoett/self-improving-agent",
        ),
        (
            "https://skills.example.com/catalog/demo/",
            ["skills.example.com"],
            "https://skills.example.com/catalog/demo",
        ),
        ("anthropics/skills", [" GitHub.com. "], "https://github.com/anthropics/skills"),
    ],
)
def test_normalize_source_accepts_allowed_skill_sources(
    source: str,
    allowed_hosts: list[str],
    expected: str,
) -> None:
    assert svc._normalize_source(source, allowed_hosts) == expected


@pytest.mark.parametrize(
    "source",
    [
        "https://example.com/skills/demo",
        "https://sub.modelscope.cn/skills/demo",
        "https://www.github.com/anthropics/skills",
        "http://modelscope.cn/skills/demo",
        "file:///tmp/skills",
        "https://modelscope.cn/skills/demo?download=1",
        "../..",
        "./repo",
        "owner/..",
    ],
)
def test_normalize_source_rejects_sources_outside_allowlist(
    source: str,
) -> None:
    with pytest.raises(ValueError, match="远程 Skill 来源白名单"):
        svc._normalize_source(source, ["github.com", "modelscope.cn"])


def test_normalize_source_rejects_all_sources_when_allowlist_is_empty() -> None:
    with pytest.raises(ValueError, match="远程 Skill 来源白名单"):
        svc._normalize_source("anthropics/skills", [])


def test_parse_available_skills_from_cli_output() -> None:
    output = """
    \x1b[38;5;250m███████╗\x1b[0m
    ◇  Available Skills
    Claude Api

        claude-api

          Build apps with the Claude API.

    Example Skills

        frontend-design

          Create distinctive frontend interfaces.

    └  Use --skill <name> to install specific skills
    """

    skills = svc._parse_available_skills(output)

    assert skills == [
        {"name": "claude-api", "description": "Build apps with the Claude API."},
        {"name": "frontend-design", "description": "Create distinctive frontend interfaces."},
    ]


@pytest.mark.asyncio
async def test_list_remote_skills_uses_isolated_home(monkeypatch: pytest.MonkeyPatch):
    sandbox = _FakeRemoteSkillSandbox(
        output="""
        ◇  Available Skills

            frontend-design

              Create distinctive frontend interfaces.

        └  Use --skill <name> to install specific skills
        """
    )
    _use_fake_sandbox(monkeypatch, sandbox)

    items = await svc.list_remote_skills("anthropics/skills", uid="user-1")

    assert items == [{"name": "frontend-design", "description": "Create distinctive frontend interfaces."}]
    assert sandbox.calls == [["npx", "-y", "skills", "add", "https://github.com/anthropics/skills", "--list"]]
    assert sandbox.cleaned is True


@pytest.mark.asyncio
async def test_remote_skill_sandbox_executes_cli_through_user_backend():
    calls: list[str] = []

    class FakeBackend:
        async def aexecute(self, command: str, *, timeout: int):
            assert timeout == svc.CLI_TIMEOUT_SECONDS
            calls.append(command)
            return SimpleNamespace(output="done", exit_code=0)

    sandbox = svc._RemoteSkillRequest(
        home=f"{svc.WORKSPACE_PATH}/.remote-skills/remote-skill-test",
        backend=FakeBackend(),
    )

    output = await sandbox.run(["npx", "-y", "skills", "add", "https://github.com/owner/repo", "--list"])

    assert output == "done"
    assert len(calls) == 1
    assert f"HOME={sandbox.home}" in calls[0]
    assert "npx -y skills add https://github.com/owner/repo --list" in calls[0]


@pytest.mark.asyncio
async def test_remote_skill_uses_request_runner_without_agent_execution():
    """Container 通过专用获取协议，不能调用缺少 Run 的通用执行器。"""

    class Runner:
        """返回可信 runner 的进程结果。"""

        def fetch_skills(self, *, uid, request_id, args):
            """核对用户身份与固定请求参数。"""
            assert uid == "user-1"
            assert request_id == "remote-skill-test"
            assert args == ["npx", "-y", "skills", "find", "demo"]
            return {"exit_code": 0, "output": "found", "truncated": False}

    request = svc._RemoteSkillRequest(
        home="/home/user/workspace/.remote-skills/remote-skill-test", backend=SimpleNamespace(id="user-1"), runner=Runner()
    )
    assert await request.run(["npx", "-y", "skills", "find", "demo"]) == "found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result", [{"exit_code": 1, "output": "PRIVATE", "truncated": False}, {"exit_code": 0, "output": "PRIVATE", "truncated": True}]
)
async def test_remote_skill_rejects_failed_or_truncated_fetch(result):
    """截断的发现结果和失败输出都不能冒充可安装包。"""
    runner = SimpleNamespace(fetch_skills=lambda **_kwargs: result)
    request = svc._RemoteSkillRequest(
        home="/home/user/workspace/.remote-skills/remote-skill-test", backend=SimpleNamespace(id="user-1"), runner=runner
    )
    with pytest.raises(ValueError, match="skills CLI 执行失败") as failure:
        await request.run(["npx", "-y", "skills", "find", "demo"])
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.asyncio
async def test_remote_skill_cancellation_waits_for_runner_cleanup(monkeypatch, tmp_path):
    """取消不能让调用方先删除仍由获取容器写入的临时目录。"""
    from yuxi.modules.workspace.paths import user_workspace_dir

    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def fetch_skills(**_kwargs):
        """模拟 runner 返回前完成容器清理。"""
        started.set()
        assert release.wait(3)
        finished.set()
        return {"exit_code": 0, "output": "found", "truncated": False}

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("yuxi.modules.agents.runtime.sandbox.backend.get_sandbox_provider", lambda: SimpleNamespace())
    request = svc._RemoteSkillRequest.create("user-1")
    request.runner = SimpleNamespace(fetch_skills=fetch_skills)
    monkeypatch.setattr(svc._RemoteSkillRequest, "create", lambda uid: request)
    root = user_workspace_dir("user-1")
    home = root / ".remote-skills" / Path(request.home).name
    (home / "live.txt").write_text("runner owns this directory")
    (root / "keep.txt").write_text("keep")
    task = asyncio.create_task(svc.list_remote_skills("owner/repo", uid="user-1"))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
        assert not task.done()
        assert not finished.is_set()
        assert (home / "live.txt").read_text() == "runner owns this directory"
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set()
    assert not home.exists()
    assert (root / "keep.txt").read_text() == "keep"


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [1, None])
async def test_remote_skill_sandbox_rejects_failed_command_without_echoing_output(exit_code) -> None:
    """失败和未完成的命令均不把原始输出作为业务提示。"""

    class FakeBackend:
        async def aexecute(self, _command: str, *, timeout: int):
            assert timeout == svc.CLI_TIMEOUT_SECONDS
            return SimpleNamespace(output="PRIVATE_CLI_MARKER", exit_code=exit_code)

    sandbox = svc._RemoteSkillRequest(
        home=f"{svc.WORKSPACE_PATH}/.remote-skills/remote-skill-test",
        backend=FakeBackend(),
    )

    with pytest.raises(ValueError, match="skills CLI 执行失败，请检查来源或稍后重试") as failure:
        await sandbox.run(["npx", "-y", "skills", "add", "https://github.com/owner/repo", "--list"])
    assert "PRIVATE_CLI_MARKER" not in str(failure.value)


@pytest.mark.parametrize(("backend", "has_runner"), [(None, True), ("container", True), ("cube", False)])
def test_remote_skill_request_follows_default_container_backend(monkeypatch, tmp_path, backend, has_runner):
    """省略后端时仍使用专用获取 runner，显式 Cube 保持原执行路径。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("yuxi.modules.agents.runtime.sandbox.backend.get_sandbox_provider", lambda: SimpleNamespace())
    if backend is None:
        monkeypatch.delenv("SANDBOX_BACKEND", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_BACKEND", backend)

    request = svc._RemoteSkillRequest.create("user-1")
    assert (request.runner is not None) is has_runner


def test_remote_skill_sandbox_uses_operator_workspace(monkeypatch, tmp_path):
    """请求有独立目录，运行身份固定为操作者。"""
    from yuxi.modules.workspace.paths import user_workspace_dir

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("yuxi.modules.agents.runtime.sandbox.backend.get_sandbox_provider", lambda: SimpleNamespace())
    first = svc._RemoteSkillRequest.create("user-1")
    second = svc._RemoteSkillRequest.create("user-1")
    assert first.backend.id == second.backend.id == "user-1"
    assert first.home != second.home
    root = user_workspace_dir("user-1")
    assert (root / ".remote-skills" / Path(first.home).name).is_dir()
    assert (root / ".remote-skills" / Path(second.home).name).is_dir()


@pytest.mark.asyncio
async def test_download_remote_skills_downloads_duplicate_skill_once(monkeypatch: pytest.MonkeyPatch):
    sandbox = _FakeRemoteSkillSandbox(available={"frontend-design"})
    _use_fake_sandbox(monkeypatch, sandbox)

    preparation = await svc.download_remote_skills(
        uid="user-1",
        source="anthropics/skills",
        skills=["frontend-design", "frontend-design"],
    )
    try:
        assert [isinstance(item, svc.DownloadedSkill) for item in preparation.results] == [True, True]
        assert sandbox.download_calls == ["frontend-design"]
        assert preparation.results[0].source_dir == preparation.results[1].source_dir
    finally:
        await preparation.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skills", "available", "expected_results", "expected_cli_skills"),
    [
        (
            ["skill-a", "skill-b", "skill-c"],
            {"skill-a", "skill-c"},
            [
                {"slug": "skill-a", "success": True},
                {"slug": "skill-b", "success": False, "error": "skills CLI 未生成预期的技能目录"},
                {"slug": "skill-c", "success": True},
            ],
            ["skill-a", "skill-b", "skill-c"],
        ),
        (
            ["valid-skill", "Bad Name", "another-valid"],
            {"valid-skill"},
            [
                {"slug": "valid-skill", "success": True},
                {"slug": "Bad Name", "success": False, "error": "skill 名称不合法"},
                {"slug": "another-valid", "success": False, "error": "skills CLI 未生成预期的技能目录"},
            ],
            ["valid-skill", "another-valid"],
        ),
    ],
)
async def test_download_remote_skills_preserves_partial_results(
    monkeypatch: pytest.MonkeyPatch,
    skills: list[str],
    available: set[str],
    expected_results: list[dict],
    expected_cli_skills: list[str],
):
    sandbox = _FakeRemoteSkillSandbox(available=available)
    _use_fake_sandbox(monkeypatch, sandbox)

    preparation = await svc.download_remote_skills(uid="user-1", source="test/repo", skills=skills)
    try:
        results = [
            (
                {"slug": result.slug, "success": False, "error": result.error}
                if isinstance(result, svc.SkillDownloadFailure)
                else {"slug": result.slug, "success": True}
            )
            for result in preparation.results
        ]
        assert results == expected_results
        assert len(sandbox.calls) == 1
        cli_skill_names = [sandbox.calls[0][index + 1] for index, arg in enumerate(sandbox.calls[0]) if arg == "--skill"]
        assert cli_skill_names == expected_cli_skills
    finally:
        await preparation.cleanup()


@pytest.mark.asyncio
async def test_download_remote_skills_removes_temp_home_when_sandbox_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    sandbox = _FakeRemoteSkillSandbox(available={"frontend-design"})

    async def fail_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    def make_temp_home(*_args, **_kwargs) -> str:
        temp_home = tmp_path / "remote-home"
        temp_home.mkdir()
        return str(temp_home)

    sandbox.cleanup = fail_cleanup
    _use_fake_sandbox(monkeypatch, sandbox)
    monkeypatch.setattr(svc.tempfile, "mkdtemp", make_temp_home)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await svc.download_remote_skills(
            uid="user-1",
            source="anthropics/skills",
            skills=["frontend-design"],
        )

    assert not (tmp_path / "remote-home").exists()


@pytest.mark.asyncio
async def test_download_remote_skills_creates_sandbox_before_temp_home(monkeypatch: pytest.MonkeyPatch):
    def fail_create(uid):
        raise RuntimeError("provider init failed")

    monkeypatch.setattr(svc, "_RemoteSkillRequest", SimpleNamespace(create=fail_create))
    monkeypatch.setattr(
        svc.tempfile,
        "mkdtemp",
        lambda **_kwargs: pytest.fail("Sandbox 初始化失败时不应创建宿主临时目录"),
    )

    with pytest.raises(RuntimeError, match="provider init failed"):
        await svc.download_remote_skills(uid="user-1", source="anthropics/skills", skills=["frontend-design"])


@pytest.mark.asyncio
async def test_remote_skill_cleanup_preserves_other_request_and_workspace(monkeypatch, tmp_path):
    """回读真实文件证明清理不会删除用户环境或其他请求目录。"""
    from yuxi.modules.workspace.paths import user_workspace_dir

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("yuxi.modules.agents.runtime.sandbox.backend.get_sandbox_provider", lambda: SimpleNamespace())
    first = svc._RemoteSkillRequest.create("user-1")
    second = svc._RemoteSkillRequest.create("user-1")
    root = user_workspace_dir("user-1")
    (root / "keep.txt").write_text("keep")
    first_home = root / ".remote-skills" / Path(first.home).name
    second_home = root / ".remote-skills" / Path(second.home).name
    (second_home / "keep.txt").write_text("other request")
    npm_bin = first_home / ".npm/_npx/cache/node_modules/.bin"
    npm_bin.mkdir(parents=True)
    (npm_bin / "skills").symlink_to("../skills/bin/cli.mjs")
    (first_home / "other-request").symlink_to(second_home, target_is_directory=True)
    (first_home / "user-file").symlink_to(root / "keep.txt")
    await first.cleanup()
    assert not (root / ".remote-skills" / Path(first.home).name).exists()
    assert (root / ".remote-skills" / Path(second.home).name).is_dir()
    assert (second_home / "keep.txt").read_text() == "other request"
    assert (root / "keep.txt").read_text() == "keep"


def test_parse_search_skills() -> None:
    output = """
    Install with npx skills add <owner/repo@skill>

    vercel-labs/agent-skills@web-design-guidelines 339.3K installs
    └ https://skills.sh/vercel-labs/agent-skills/web-design-guidelines

    xixu-me/skills@secure-linux-web-hosting 158.6K installs
    └ https://skills.sh/xixu-me/skills/secure-linux-web-hosting

    anthropics/skills@webapp-testing
    └ https://skills.sh/anthropics/skills/webapp-testing
    """

    results = svc._parse_search_skills(output)
    assert results == [
        {
            "source": "vercel-labs/agent-skills",
            "name": "web-design-guidelines",
            "installs": "339.3K installs",
        },
        {
            "source": "xixu-me/skills",
            "name": "secure-linux-web-hosting",
            "installs": "158.6K installs",
        },
        {
            "source": "anthropics/skills",
            "name": "webapp-testing",
            "installs": "",
        },
    ]


@pytest.mark.asyncio
async def test_search_remote_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = _FakeRemoteSkillSandbox(
        output="""
        vercel-labs/agent-skills@web-design-guidelines 339.3K installs
        """
    )
    _use_fake_sandbox(monkeypatch, sandbox)

    items = await svc.search_remote_skills("web", uid="user-1")
    assert items == [
        {
            "source": "vercel-labs/agent-skills",
            "name": "web-design-guidelines",
            "installs": "339.3K installs",
        }
    ]
    assert sandbox.calls == [["npx", "-y", "skills", "find", "web"]]
    assert sandbox.cleaned is True


@pytest.mark.asyncio
async def test_large_download_keeps_order_across_runner_batches(monkeypatch):
    """大批量下载拆成可接受的请求，所有条目按原顺序返回。"""
    names = [f"sample-{index}" for index in range(99)]
    sandbox = _FakeRemoteSkillSandbox(available=set(names))
    _use_fake_sandbox(monkeypatch, sandbox)
    downloads = await svc.download_remote_skills(uid="user-1", source="test/repo", skills=names)
    try:
        assert [len(call) for call in sandbox.calls] == [204, 10]
        assert sandbox.download_calls == names
        assert [entry.slug for entry in downloads.results if isinstance(entry, svc.DownloadedSkill)] == names
    finally:
        if downloads.temp_home:
            import shutil

            shutil.rmtree(downloads.temp_home)
