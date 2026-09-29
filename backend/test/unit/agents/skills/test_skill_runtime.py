from types import SimpleNamespace

import pytest
import yuxi.agents.skills.runtime as skill_runtime
from sqlalchemy.dialects import postgresql
from yuxi.agents.skills.runtime import build_dependency_bundle, expand_skill_closure, resolve_runtime_skills_for_context
from yuxi.repositories.skill_repository import SkillRepository as RealSkillRepository
from yuxi.workspace.paths import user_workspace_dir


def _mock_runtime_sources(monkeypatch, accessible):
    """让运行时组合测试使用给定共享和个人候选。"""

    async def locked(db, user, _selected, *, shadowed_slugs=None):
        items = await accessible(db, user)
        shadowed_slugs = shadowed_slugs or set()
        return [
            item
            for item in items
            if item.source_scope != "personal" and item.slug not in shadowed_slugs
        ]

    async def personal(uid):
        items = await accessible(object(), SimpleNamespace(uid=uid))
        return [item for item in items if item.source_scope == "personal"]

    monkeypatch.setattr(skill_runtime, "lock_accessible_shared_skills_for_runtime", locked)
    monkeypatch.setattr(skill_runtime, "list_personal_skills", personal)
    monkeypatch.setattr(skill_runtime, "resolved_shared_skill", lambda item: item)


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [[], ["shared"], "all"])
@pytest.mark.parametrize("preloads", [[], "all"])
async def test_personal_skills_are_available_independently_of_shared_selection(
    tmp_path, monkeypatch, selection, preloads
):
    """真实个人目录始终参与运行，选项仅共享且其他用户目录不可见。"""
    from yuxi.agents.context import normalize_agent_context_config, resolve_agent_resource_options
    from yuxi.services.skills import shared as service
    from yuxi.storage.postgres.models_business import Skill
    from yuxi.workspace import paths

    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path / "user-data")
    monkeypatch.setattr(service, "get_skill_data_dir", lambda: tmp_path)
    shared_dir = tmp_path / "shared" / "extra"
    shared_dir.mkdir(parents=True)
    (shared_dir / "SKILL.md").write_text("# Extra shared body", encoding="utf-8")
    shared = Skill(
        id=1,
        slug="shared",
        name="Shared title",
        description="shared description",
        source_type="upload",
        dir_path="shared/shared",
        enabled=True,
        created_by="user-a",
        share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
    )
    extra = Skill(
        id=2,
        slug="extra",
        name="Extra",
        description="extra shared",
        source_type="upload",
        dir_path="shared/extra",
        enabled=True,
        created_by="user-a",
        share_config=shared.share_config,
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
    )

    class FakeSkillRepository(RealSkillRepository):
        """提供共享记录，个人来源由真实目录扫描。"""

        def __init__(self, db):
            """接收测试会话。"""

        async def list_enabled(self):
            """返回测试共享记录。"""
            return [shared, extra]

        async def lock_rows_for_read(self, ids):
            """模拟按可见 ID 锁定共享来源。"""
            return [item for item in (shared, extra) if item.id in ids]

        async def get_by_slug_for_read(self, slug):
            """模拟单行持锁读取。"""
            return next((item for item in (shared, extra) if item.slug == slug), None)

    monkeypatch.setattr(service, "SkillRepository", FakeSkillRepository)
    monkeypatch.setattr("yuxi.repositories.skill_repository.SkillRepository", FakeSkillRepository)
    for uid, slug in [("user-a", "personal"), ("user-a", "shared"), ("user-b", "other-user")]:
        directory = user_workspace_dir(uid) / "agents" / "skills" / slug
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: personal {slug}\n---\nPersonal body", encoding="utf-8"
        )
    user = SimpleNamespace(uid="user-a", role="user", department_id=None)
    options = await resolve_agent_resource_options({"skills"}, db=None, user=user)
    assert options["skills"] == [
        {"key": "shared", "name": "Shared title", "description": "shared description"},
        {"key": "extra", "name": "Extra", "description": "extra shared"},
    ]
    config = {"tools": [], "knowledges": [], "skills": selection, "preload_skills": preloads}
    normalized = await normalize_agent_context_config(config, db=None, user=user)
    assert normalized["skills"] == (["shared", "extra"] if selection == "all" else selection)
    scope = await resolve_runtime_skills_for_context(SimpleNamespace(**normalized), db=None, user=user)
    expected = {"shared", "personal", "extra"} if selection == "all" else {"shared", "personal"}
    assert set(scope["context_skills"]) == expected
    assert set(scope["effective_skills"]) == expected
    assert scope["runtime_skills"]["shared"]["description"] == "personal shared"
    assert scope["runtime_skills"]["shared"]["path"] == "/home/gem/user-data/agents/skills/shared/SKILL.md"
    assert "other-user" not in scope["runtime_skills"]
    expected_contents = {}
    if preloads == "all" and selection:
        expected_contents["shared"] = "---\nname: shared\ndescription: personal shared\n---\nPersonal body"
        if selection == "all":
            expected_contents["extra"] = "# Extra shared body"
    assert scope["preloaded_skill_contents"] == expected_contents
    assert config["skills"] == selection


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
async def test_personal_skill_is_not_a_direct_preload_candidate(tmp_path, monkeypatch):
    """自动加入的个人 Skill 不扩大显式预加载范围。"""
    personal = _skill(tmp_path, "personal")
    personal.source_scope = "personal"

    async def accessible(_db, _user):
        return [personal]

    _mock_runtime_sources(monkeypatch, accessible)
    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=[], preload_skills=["personal"]), db=None, user=SimpleNamespace(uid="test")
    )

    assert scope["context_skills"] == ["personal"]
    assert scope["context_preload_skills"] == []
    assert scope["preloaded_skill_contents"] == {}


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

    _mock_runtime_sources(monkeypatch, fake_list_accessible_skills)

    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["alpha", "missing"]),
        db=object(),
        user=SimpleNamespace(uid="test"),
    )

    assert scope["context_skills"] == ["alpha", "beta"]
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

    _mock_runtime_sources(monkeypatch, fake_list_accessible_skills)
    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["alpha"], preload_skills=["alpha", "beta", "missing"]),
        db=object(),
        user=SimpleNamespace(uid="test"),
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

    _mock_runtime_sources(monkeypatch, fake_list_accessible_skills)

    with pytest.raises(RuntimeError, match="根级 SKILL.md 不可读"):
        await resolve_runtime_skills_for_context(
            SimpleNamespace(skills=["alpha"], preload_skills=["alpha"]),
            db=object(),
            user=SimpleNamespace(uid="test"),
        )


@pytest.mark.asyncio
async def test_manifest_retains_metadata_from_authorized_resolution(tmp_path, monkeypatch):
    """源记录更新后，manifest 仍使用首次解析的版本与内容摘要。"""
    from yuxi.services.agent_run_manifest_service import build_skill_manifest_entries

    item = _skill(tmp_path, "alpha", content="original body")

    async def accessible(db, user):
        return [item]

    _mock_runtime_sources(monkeypatch, accessible)
    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["alpha"], preload_skills=["alpha"]),
        db=object(),
        user=SimpleNamespace(uid="test"),
    )
    item.version, item.content_hash = "v2", "hash-v2"
    (item.source_dir / "SKILL.md").write_text("changed body", encoding="utf-8")
    entries = build_skill_manifest_entries({"skills": ["alpha"]}, scope)
    assert entries[0]["version"] == "v1"
    assert entries[0]["content_hash"] == "hash-v1"
    assert scope["preloaded_skill_contents"]["alpha"] == "original body"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection, expected",
    [([], []), (["missing"], []), (["alpha", "missing"], ["alpha", "beta"]), ("all", ["alpha", "beta", "gamma"])],
)
async def test_preload_all_reads_only_enabled_authorized_skill_closure(tmp_path, monkeypatch, selection, expected):
    """全部预加载沿真实解析链读取已启用 Skill 及其授权依赖的文件。"""
    from yuxi.agents.context import normalize_agent_context_config
    from yuxi.services.skills import shared as service

    skills = [
        _skill(tmp_path, "alpha", dependencies=["beta"]),
        _skill(tmp_path, "beta"),
        _skill(tmp_path, "gamma"),
    ]

    async def accessible(db, user):
        return skills

    monkeypatch.setattr(service.SkillRepository, "list_enabled_readable", accessible)
    _mock_runtime_sources(monkeypatch, accessible)
    normalized = await normalize_agent_context_config(
        {"tools": [], "knowledges": [], "skills": selection, "preload_skills": "all"},
        db=None,
        user=None,
    )
    scope = await resolve_runtime_skills_for_context(
        SimpleNamespace(**normalized), db=None, user=SimpleNamespace(uid="test")
    )
    assert scope["preloaded_skills"] == expected
    assert scope["preloaded_skill_contents"] == {slug: f"# {slug}" for slug in expected}
    assert scope["context_preload_skills"] == normalized["skills"]


@pytest.mark.asyncio
async def test_runtime_skill_query_holds_shared_row_locks():
    """运行时读元数据时等待共享 Skill 编辑事务完成。"""
    statements = []

    class Session:
        async def execute(self, stmt):
            statements.append(stmt)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    await RealSkillRepository(Session()).lock_rows_for_read([1])

    assert "FOR SHARE" in str(statements[0].compile(dialect=postgresql.dialect()))
    assert statements[0].get_execution_options()["populate_existing"] is True


@pytest.mark.asyncio
async def test_skill_file_read_uses_shared_row_lock():
    """普通读取允许其他读取并发，仍阻止编辑发布。"""
    statements = []

    class Session:
        async def execute(self, stmt):
            statements.append(stmt)
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    await RealSkillRepository(Session()).get_by_slug_for_read("demo")

    assert "FOR SHARE" in str(statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in str(statements[0].compile(dialect=postgresql.dialect()))
