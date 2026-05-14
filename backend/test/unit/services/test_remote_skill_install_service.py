from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.services import remote_skill_install_service as svc


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
    captured: dict[str, object] = {}

    async def fake_run_skills_cli(args: list[str], *, env: dict[str, str], cwd: str) -> str:
        captured["args"] = args
        captured["home"] = env["HOME"]
        captured["cwd"] = cwd
        return """
        ◇  Available Skills

            frontend-design

              Create distinctive frontend interfaces.

        └  Use --skill <name> to install specific skills
        """

    monkeypatch.setattr(svc, "_run_skills_cli", fake_run_skills_cli)

    items = await svc.list_remote_skills("anthropics/skills")

    assert items == [{"name": "frontend-design", "description": "Create distinctive frontend interfaces."}]
    assert captured["args"] == ["npx", "-y", "skills", "add", "anthropics/skills", "--list"]
    assert str(captured["cwd"]).startswith(str(captured["home"]))


@pytest.mark.asyncio
async def test_install_remote_skill_imports_from_cli_output_dir(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[list[str], str]] = []

    async def fake_run_skills_cli(args: list[str], *, env: dict[str, str], cwd: str) -> str:
        calls.append((args, env["HOME"]))
        home = Path(env["HOME"])
        if "--list" in args:
            return """
            ◇  Available Skills

                frontend-design

                  Create distinctive frontend interfaces.

            └  Use --skill <name> to install specific skills
            """
        skill_dir = home / ".agents" / "skills" / "frontend-design"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: frontend-design\ndescription: demo\n---\n# Demo\n",
            encoding="utf-8",
        )
        return "installed"

    captured: dict[str, object] = {}

    async def fake_import_skill_dir(_db, *, source_dir, created_by):
        captured["source_dir"] = Path(source_dir)
        captured["created_by"] = created_by
        return {"slug": "frontend-design"}

    monkeypatch.setattr(svc, "_run_skills_cli", fake_run_skills_cli)
    monkeypatch.setattr(svc, "import_skill_dir", fake_import_skill_dir)

    item = await svc.install_remote_skill(
        None,
        source="anthropics/skills",
        skill="frontend-design",
        created_by="root",
    )

    assert item == {"slug": "frontend-design"}
    assert calls[0][0] == ["npx", "-y", "skills", "add", "anthropics/skills", "--list"]
    assert calls[1][0] == [
        "npx",
        "-y",
        "skills",
        "add",
        "anthropics/skills",
        "--skill",
        "frontend-design",
        "-g",
        "-y",
        "--copy",
    ]
    assert captured["source_dir"] == Path(calls[1][1]) / ".agents" / "skills" / "frontend-design"
    assert captured["created_by"] == "root"


@pytest.mark.asyncio
async def test_install_remote_skill_rejects_missing_remote_skill(monkeypatch: pytest.MonkeyPatch):
    async def fake_run_skills_cli(args: list[str], *, env: dict[str, str], cwd: str) -> str:
        return """
        ◇  Available Skills

            other-skill

              Description

        └  Use --skill <name> to install specific skills
        """

    monkeypatch.setattr(svc, "_run_skills_cli", fake_run_skills_cli)

    with pytest.raises(ValueError, match="不存在 skill"):
        await svc.install_remote_skill(
            None,
            source="anthropics/skills",
            skill="frontend-design",
            created_by="root",
        )


@pytest.mark.asyncio
async def test_install_remote_skills_batch_installs_all(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[list[str], str]] = []
    imported_skills: list[str] = []

    async def fake_run_skills_cli(args: list[str], *, env: dict[str, str], cwd: str) -> str:
        calls.append((args, env["HOME"]))
        home = Path(env["HOME"])
        skill_dir_base = home / ".agents" / "skills"
        for skill_name in ("frontend-design", "claude-api", "code-review"):
            (skill_dir_base / skill_name).mkdir(parents=True, exist_ok=True)
            (skill_dir_base / skill_name / "SKILL.md").write_text(
                f"---\nname: {skill_name}\ndescription: demo\n---\n# {skill_name}\n",
                encoding="utf-8",
            )
        return "installed"

    async def fake_import_skill_dir(_db, *, source_dir, created_by):
        imported_skills.append(source_dir.name)
        return SimpleNamespace(slug=source_dir.name)

    monkeypatch.setattr(svc, "_run_skills_cli", fake_run_skills_cli)
    monkeypatch.setattr(svc, "import_skill_dir", fake_import_skill_dir)

    results = await svc.install_remote_skills_batch(
        None,
        source="anthropics/skills",
        skills=["frontend-design", "claude-api", "code-review"],
        created_by="root",
    )

    # Should only have 1 CLI call (no --list, direct batch install)
    assert len(calls) == 1
    assert calls[0][0] == [
        "npx",
        "-y",
        "skills",
        "add",
        "anthropics/skills",
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

    assert len(results) == 3
    assert all(r["success"] for r in results)
    assert [r["slug"] for r in results] == ["frontend-design", "claude-api", "code-review"]
    assert imported_skills == ["frontend-design", "claude-api", "code-review"]


@pytest.mark.asyncio
async def test_install_remote_skills_batch_skips_missing(monkeypatch: pytest.MonkeyPatch):
    async def fake_run_skills_cli(args: list[str], *, env: dict[str, str], cwd: str) -> str:
        home = Path(env["HOME"])
        skill_dir_base = home / ".agents" / "skills"
        (skill_dir_base / "frontend-design").mkdir(parents=True, exist_ok=True)
        (skill_dir_base / "frontend-design" / "SKILL.md").write_text(
            "---\nname: frontend-design\ndescription: demo\n---\n# Demo\n",
            encoding="utf-8",
        )
        return "installed"

    async def fake_import_skill_dir(_db, *, source_dir, created_by):
        return SimpleNamespace(slug=source_dir.name)

    monkeypatch.setattr(svc, "_run_skills_cli", fake_run_skills_cli)
    monkeypatch.setattr(svc, "import_skill_dir", fake_import_skill_dir)

    results = await svc.install_remote_skills_batch(
        None,
        source="anthropics/skills",
        skills=["frontend-design", "nonexistent-skill"],
        created_by="root",
    )

    assert len(results) == 2
    assert results[0] == {"slug": "frontend-design", "success": True}
    assert results[1] == {"slug": "nonexistent-skill", "success": False, "error": "skills CLI 未生成预期的技能目录"}


@pytest.mark.asyncio
async def test_install_remote_skills_batch_partial_failure(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[list[str], str]] = []

    async def fake_run_skills_cli(args: list[str], *, env: dict[str, str], cwd: str) -> str:
        calls.append((args, env["HOME"]))
        home = Path(env["HOME"])
        skill_dir_base = home / ".agents" / "skills"
        (skill_dir_base / "skill-a").mkdir(parents=True, exist_ok=True)
        (skill_dir_base / "skill-a" / "SKILL.md").write_text(
            "---\nname: skill-a\ndescription: demo\n---\n# A\n", encoding="utf-8",
        )
        # skill-b directory missing (simulate install failure from CLI side)
        (skill_dir_base / "skill-c").mkdir(parents=True, exist_ok=True)
        (skill_dir_base / "skill-c" / "SKILL.md").write_text(
            "---\nname: skill-c\ndescription: demo\n---\n# C\n", encoding="utf-8",
        )
        return "installed"

    async def fake_import_skill_dir(_db, *, source_dir, created_by):
        return SimpleNamespace(slug=source_dir.name)

    monkeypatch.setattr(svc, "_run_skills_cli", fake_run_skills_cli)
    monkeypatch.setattr(svc, "import_skill_dir", fake_import_skill_dir)

    results = await svc.install_remote_skills_batch(
        None,
        source="test/repo",
        skills=["skill-a", "skill-b", "skill-c"],
        created_by="root",
    )

    assert len(results) == 3
    assert results[0] == {"slug": "skill-a", "success": True}
    assert results[1] == {"slug": "skill-b", "success": False, "error": "skills CLI 未生成预期的技能目录"}
    assert results[2] == {"slug": "skill-c", "success": True}


@pytest.mark.asyncio
async def test_install_remote_skills_batch_handles_invalid_names(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[list[str], str]] = []

    async def fake_run_skills_cli(args: list[str], *, env: dict[str, str], cwd: str) -> str:
        calls.append((args, env["HOME"]))
        home = Path(env["HOME"])
        skill_dir_base = home / ".agents" / "skills"
        (skill_dir_base / "valid-skill").mkdir(parents=True, exist_ok=True)
        (skill_dir_base / "valid-skill" / "SKILL.md").write_text(
            "---\nname: valid-skill\ndescription: demo\n---\n# Valid\n", encoding="utf-8",
        )
        return "installed"

    async def fake_import_skill_dir(_db, *, source_dir, created_by):
        return SimpleNamespace(slug=source_dir.name)

    monkeypatch.setattr(svc, "_run_skills_cli", fake_run_skills_cli)
    monkeypatch.setattr(svc, "import_skill_dir", fake_import_skill_dir)

    results = await svc.install_remote_skills_batch(
        None,
        source="test/repo",
        skills=["valid-skill", "Bad Name", "another-valid"],
        created_by="root",
    )

    assert len(results) == 3
    assert results[0] == {"slug": "valid-skill", "success": True}
    assert results[1]["success"] is False
    assert "不合法" in results[1]["error"]
    assert results[2] == {"slug": "another-valid", "success": False, "error": "skills CLI 未生成预期的技能目录"}

    # Only valid skills passed to the CLI
    assert len(calls) == 1
    assert "--skill" in str(calls[0][0])
    assert "valid-skill" in str(calls[0][0])
    assert "Bad" not in str(calls[0][0])
