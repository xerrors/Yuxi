from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from yuxi.agents.skills import remote_install as svc


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
    monkeypatch.setattr(svc, "_RemoteSkillSandbox", SimpleNamespace(create=lambda: sandbox))


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

    items = await svc.list_remote_skills("anthropics/skills")

    assert items == [{"name": "frontend-design", "description": "Create distinctive frontend interfaces."}]
    assert sandbox.calls == [["npx", "-y", "skills", "add", "https://github.com/anthropics/skills", "--list"]]
    assert sandbox.cleaned is True


@pytest.mark.asyncio
async def test_remote_skill_sandbox_executes_cli_through_provisioner_backend():
    calls: list[str] = []

    class FakeBackend:
        def execute(self, command: str, *, timeout: int):
            assert timeout == svc.CLI_TIMEOUT_SECONDS
            calls.append(command)
            return SimpleNamespace(output="done", exit_code=0)

    sandbox = svc._RemoteSkillSandbox(
        thread_id="remote-skill-test",
        home=f"{svc.REMOTE_SKILL_SANDBOX_ROOT}/.remote-skill-test",
        backend=FakeBackend(),
    )

    output = await sandbox.run(["npx", "-y", "skills", "add", "https://github.com/owner/repo", "--list"])

    assert output == "done"
    assert len(calls) == 1
    assert f"HOME={sandbox.home}" in calls[0]
    assert "npx -y skills add https://github.com/owner/repo --list" in calls[0]


@pytest.mark.asyncio
async def test_remote_skill_sandbox_rejects_incomplete_command() -> None:
    class FakeBackend:
        def execute(self, _command: str, *, timeout: int):
            assert timeout == svc.CLI_TIMEOUT_SECONDS
            return SimpleNamespace(output="still running", exit_code=None)

    sandbox = svc._RemoteSkillSandbox(
        thread_id="remote-skill-test",
        home=f"{svc.REMOTE_SKILL_SANDBOX_ROOT}/.remote-skill-test",
        backend=FakeBackend(),
    )

    with pytest.raises(ValueError, match="still running"):
        await sandbox.run(["npx", "-y", "skills", "add", "https://github.com/owner/repo", "--list"])


def test_remote_skill_sandbox_uses_unique_workspace_uid(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, str | bool] = {}

    class FakeBackend:
        def __init__(self, *, thread_id, uid, inherit_env):
            captured["thread_id"] = thread_id
            captured["uid"] = uid
            captured["inherit_env"] = inherit_env

    monkeypatch.setattr(svc, "ProvisionerSandboxBackend", FakeBackend)
    sandbox = svc._RemoteSkillSandbox.create()

    assert captured == {"thread_id": sandbox.thread_id, "uid": sandbox.thread_id, "inherit_env": False}


@pytest.mark.asyncio
async def test_install_remote_skill_imports_from_cli_output_dir(monkeypatch: pytest.MonkeyPatch):
    sandbox = _FakeRemoteSkillSandbox(available={"frontend-design"})
    captured: dict[str, object] = {}

    async def fake_import_skill_dir(_db, *, source_dir, created_by):
        captured["source_dir"] = Path(source_dir)
        captured["created_by"] = created_by
        return {"slug": "frontend-design"}

    _use_fake_sandbox(monkeypatch, sandbox)
    monkeypatch.setattr(svc, "import_skill_dir", fake_import_skill_dir)

    item = await svc.install_remote_skill(
        None,
        source="anthropics/skills",
        skill="frontend-design",
        created_by="root",
    )

    assert item == {"slug": "frontend-design"}
    assert sandbox.calls[0] == [
        "npx",
        "-y",
        "skills",
        "add",
        "https://github.com/anthropics/skills",
        "--skill",
        "frontend-design",
        "-g",
        "-y",
        "--copy",
    ]
    assert Path(captured["source_dir"]).name == "frontend-design"
    assert captured["created_by"] == "root"
    assert sandbox.cleaned is True


@pytest.mark.asyncio
async def test_install_remote_skill_rejects_missing_remote_skill(monkeypatch: pytest.MonkeyPatch):
    sandbox = _FakeRemoteSkillSandbox()
    _use_fake_sandbox(monkeypatch, sandbox)

    with pytest.raises(ValueError, match="未生成预期"):
        await svc.install_remote_skill(
            None,
            source="anthropics/skills",
            skill="frontend-design",
            created_by="root",
        )


@pytest.mark.asyncio
async def test_install_remote_skills_batch_installs_all(monkeypatch: pytest.MonkeyPatch):
    imported_skills: list[str] = []
    sandbox = _FakeRemoteSkillSandbox(available={"frontend-design", "claude-api", "code-review"})

    async def fake_import_skill_dir(_db, *, source_dir, created_by):
        imported_skills.append(source_dir.name)
        return SimpleNamespace(slug=source_dir.name)

    _use_fake_sandbox(monkeypatch, sandbox)
    monkeypatch.setattr(svc, "import_skill_dir", fake_import_skill_dir)

    results = await svc.install_remote_skills_batch(
        None,
        source="anthropics/skills",
        skills=["frontend-design", "claude-api", "code-review"],
        created_by="root",
    )

    # Should only have 1 CLI call (no --list, direct batch install)
    assert sandbox.calls == [
        [
            "npx",
            "-y",
            "skills",
            "add",
            "https://github.com/anthropics/skills",
            "--skill",
            "frontend-design",
            "--skill",
            "claude-api",
            "--skill",
            "code-review",
            "-g",
            "-y",
            "--copy",
        ]
    ]

    assert len(results) == 3
    assert all(r["success"] for r in results)
    assert [r["slug"] for r in results] == ["frontend-design", "claude-api", "code-review"]
    assert imported_skills == ["frontend-design", "claude-api", "code-review"]


@pytest.mark.asyncio
async def test_prepare_remote_skills_batch_downloads_duplicate_skill_once(monkeypatch: pytest.MonkeyPatch):
    sandbox = _FakeRemoteSkillSandbox(available={"frontend-design"})
    _use_fake_sandbox(monkeypatch, sandbox)

    preparation = await svc.prepare_remote_skills_batch(
        source="anthropics/skills",
        skills=["frontend-design", "frontend-design"],
    )
    try:
        assert [item["success"] for item in preparation.results] == [True, True]
        assert sandbox.download_calls == ["frontend-design"]
        assert preparation.results[0]["source_dir"] == preparation.results[1]["source_dir"]
    finally:
        await preparation.cleanup()


@pytest.mark.asyncio
async def test_prepare_remote_skills_batch_removes_temp_home_when_sandbox_cleanup_fails(
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
        await svc.prepare_remote_skills_batch(
            source="anthropics/skills",
            skills=["frontend-design"],
        )

    assert not (tmp_path / "remote-home").exists()


@pytest.mark.asyncio
async def test_prepare_remote_skills_batch_creates_sandbox_before_temp_home(monkeypatch: pytest.MonkeyPatch):
    def fail_create():
        raise RuntimeError("provider init failed")

    monkeypatch.setattr(svc, "_RemoteSkillSandbox", SimpleNamespace(create=fail_create))
    monkeypatch.setattr(
        svc.tempfile,
        "mkdtemp",
        lambda **_kwargs: pytest.fail("Sandbox 初始化失败时不应创建宿主临时目录"),
    )

    with pytest.raises(RuntimeError, match="provider init failed"):
        await svc.prepare_remote_skills_batch(source="anthropics/skills", skills=["frontend-design"])


@pytest.mark.asyncio
async def test_remote_skill_sandbox_cleanup_removes_whole_thread_dir_when_release_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    thread_dir = tmp_path / "threads" / "remote-skill-test"
    user_data_dir = thread_dir / "user-data"
    workspace_root = tmp_path / "threads" / "shared" / "remote-skill-test"
    (user_data_dir / "outputs").mkdir(parents=True)
    (thread_dir / "skills").mkdir()
    (workspace_root / "workspace").mkdir(parents=True)
    release_calls: list[tuple[tuple, dict]] = []

    class FakeProvider:
        def release(self, *_args, **_kwargs):
            release_calls.append((_args, _kwargs))
            raise RuntimeError("release failed")

    monkeypatch.setattr(svc, "get_sandbox_provider", lambda: FakeProvider())
    monkeypatch.setattr(svc, "sandbox_user_data_dir", lambda _thread_id: user_data_dir)
    monkeypatch.setattr(svc, "sandbox_workspace_dir", lambda _thread_id, _uid: workspace_root / "workspace")
    sandbox = svc._RemoteSkillSandbox(
        thread_id="remote-skill-test",
        home="/home/gem/user-data/outputs/.remote-skill-test",
        backend=SimpleNamespace(),
    )

    with pytest.raises(RuntimeError, match="release failed"):
        await sandbox.cleanup()

    assert not thread_dir.exists()
    assert not workspace_root.exists()
    assert release_calls == [
        (
            ("remote-skill-test",),
            {
                "uid": "remote-skill-test",
                "clear_cache_on_delete_failure": True,
            },
        )
    ]


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
async def test_install_remote_skills_batch_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
    skills: list[str],
    available: set[str],
    expected_results: list[dict],
    expected_cli_skills: list[str],
):
    sandbox = _FakeRemoteSkillSandbox(available=available)

    async def fake_import_skill_dir(_db, *, source_dir, created_by):
        return SimpleNamespace(slug=source_dir.name)

    _use_fake_sandbox(monkeypatch, sandbox)
    monkeypatch.setattr(svc, "import_skill_dir", fake_import_skill_dir)

    results = await svc.install_remote_skills_batch(
        None,
        source="test/repo",
        skills=skills,
        created_by="root",
    )

    assert results == expected_results
    assert len(sandbox.calls) == 1
    cli_skill_names = [sandbox.calls[0][index + 1] for index, arg in enumerate(sandbox.calls[0]) if arg == "--skill"]
    assert cli_skill_names == expected_cli_skills


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

    items = await svc.search_remote_skills("web")
    assert items == [
        {
            "source": "vercel-labs/agent-skills",
            "name": "web-design-guidelines",
            "installs": "339.3K installs",
        }
    ]
    assert sandbox.calls == [["npx", "-y", "skills", "find", "web"]]
    assert sandbox.cleaned is True
