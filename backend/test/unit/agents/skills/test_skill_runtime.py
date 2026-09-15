from types import SimpleNamespace

import pytest

import yuxi.agents.skills.runtime as skill_runtime
from yuxi.agents.skills.runtime import build_dependency_bundle, expand_skill_closure, resolve_runtime_skills_for_context


def _skill(tmp_path, slug: str, *, dependencies: list[str] | None = None, content: str | None = None):
    source_dir = tmp_path / slug
    source_dir.mkdir()
    (source_dir / "SKILL.md").write_text(content or f"# {slug}", encoding="utf-8")
    return SimpleNamespace(
        slug=slug,
        name=slug.title(),
        description=f"{slug} desc",
        source_scope="shared",
        version="v1",
        content_hash="hash-v1",
        source_dir=source_dir,
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=dependencies or [],
    )


@pytest.mark.asyncio
async def test_resolve_runtime_skills_derives_authorized_scope(monkeypatch):
    """运行时 scope 只保留授权选择，并按依赖闭包区分共享与个人来源。"""

    async def fake_list_accessible_skills(db, user):
        assert db is not None
        assert user is not None
        return [
            SimpleNamespace(
                slug="alpha",
                name="Alpha",
                description="alpha desc",
                source_scope="shared",
                version="v1",
                content_hash="hash-v1",
                source_dir="/tmp/shared/alpha",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=["beta"],
            ),
            SimpleNamespace(
                slug="beta",
                name="Beta",
                description="beta desc",
                source_scope="personal",
                version=None,
                content_hash=None,
                source_dir="/tmp/personal/beta",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=[],
            ),
        ]

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", fake_list_accessible_skills)

    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["alpha", "missing"]),
        db=object(),
        user=object(),
    )

    assert scope["context_skills"] == ["alpha"]
    assert scope["effective_skills"] == ["alpha", "beta"]
    assert set(scope["runtime_skills"]) == {"alpha", "beta"}
    assert scope["runtime_skills"]["alpha"]["path"] == "/home/gem/skills/alpha/SKILL.md"
    assert scope["runtime_skills"]["beta"]["path"] == "/home/gem/user-data/agents/skills/beta/SKILL.md"
    assert scope["runtime_skills"]["alpha"]["skills"] == ["beta"]


def test_expand_skill_closure_handles_cycles_missing_and_duplicates():
    """循环、缺失目标和重复依赖保持 fail-safe 且稳定去重。"""
    runtime_skills = {
        "alpha": {"tools": [], "mcps": [], "skills": ["beta", "missing", "beta"]},
        "beta": {"tools": [], "mcps": [], "skills": ["alpha"]},
    }

    assert expand_skill_closure(["alpha", "alpha"], runtime_skills) == ["alpha", "beta"]


def test_dependency_bundle_returns_only_consumed_dependencies():
    """依赖包只暴露 Middleware 消费的工具和 MCP 字段。"""
    runtime_skills = {
        "alpha": {"tools": ["tool-a", "tool-a"], "mcps": ["mcp-a"], "skills": ["beta"]},
        "beta": {"tools": ["tool-b"], "mcps": ["mcp-a", "mcp-b"], "skills": []},
    }

    bundle = build_dependency_bundle(["alpha", "beta"], runtime_skills)

    assert bundle == {"tools": ["tool-a", "tool-b"], "mcps": ["mcp-a", "mcp-b"]}
    assert "skills" not in bundle


@pytest.mark.asyncio
async def test_preload_reads_authorized_dependency_closure(tmp_path, monkeypatch):
    skills = [
        _skill(tmp_path, "alpha", dependencies=["beta"], content="# Alpha\nUSE_ALPHA"),
        _skill(tmp_path, "beta", content="# Beta\nUSE_BETA"),
    ]

    async def fake_list_accessible_skills(_db, _user):
        return skills

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", fake_list_accessible_skills)
    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["alpha"], preload_skills=["alpha", "beta", "missing"]),
        db=object(),
        user=object(),
    )

    assert scope["context_preload_skills"] == ["alpha"]
    assert scope["preloaded_skills"] == ["alpha", "beta"]
    assert scope["preloaded_skill_contents"] == {
        "alpha": "# Alpha\nUSE_ALPHA",
        "beta": "# Beta\nUSE_BETA",
    }


@pytest.mark.asyncio
async def test_preload_rejects_symlinked_source_ancestor(tmp_path, monkeypatch):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    item = _skill(real_parent, "alpha")
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    item.source_dir = linked_parent / "alpha"

    async def fake_list_accessible_skills(_db, _user):
        return [item]

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", fake_list_accessible_skills)

    with pytest.raises(RuntimeError, match="根级 SKILL.md 不可读"):
        await resolve_runtime_skills_for_context(
            SimpleNamespace(skills=["alpha"], preload_skills=["alpha"]),
            db=object(),
            user=object(),
        )


@pytest.mark.asyncio
async def test_manifest_retains_metadata_from_authorized_resolution(tmp_path, monkeypatch):
    """源记录更新后，manifest 仍使用首次解析的版本与内容摘要。"""
    from yuxi.services.agent_run_manifest_service import build_skill_manifest_entries

    item = _skill(tmp_path, "alpha", content="original body")

    async def accessible(db, user):
        return [item]

    monkeypatch.setattr(skill_runtime, "list_accessible_skills", accessible)
    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["alpha"], preload_skills=["alpha"]),
        db=object(),
        user=object(),
    )
    item.version, item.content_hash = "v2", "hash-v2"
    (item.source_dir / "SKILL.md").write_text("changed body", encoding="utf-8")
    entries = build_skill_manifest_entries({"skills": ["alpha"]}, scope)
    assert entries[0]["version"] == "v1"
    assert entries[0]["content_hash"] == "hash-v1"
    assert scope["preloaded_skill_contents"]["alpha"] == "original body"
