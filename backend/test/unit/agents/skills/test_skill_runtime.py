from types import SimpleNamespace

import pytest
import yuxi.modules.extensions.skills.runtime as skill_runtime
from sqlalchemy.dialects import postgresql
from yuxi.modules.extensions.skills.runtime import (
    resolve_runtime_skills_for_context,
)
from yuxi.modules.extensions.skills.repository import SkillRepository as RealSkillRepository
from yuxi.modules.workspace.paths import user_workspace_dir


@pytest.mark.asyncio
async def test_personal_skill_same_slug_replacement_changes_runtime_identity(tmp_path, monkeypatch):
    """真实个人来源被同名替换或修改后，不得沿用旧请求的身份。"""
    from yuxi.modules.workspace import paths

    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path)
    directory = user_workspace_dir("user-a") / "agents" / "skills" / "personal"
    directory.mkdir(parents=True)
    skill_file = directory / "SKILL.md"
    skill_file.write_text("---\nname: personal\ndescription: first\n---\nFirst", encoding="utf-8")
    context = SimpleNamespace(skills=[], preload_skills=[])
    user = SimpleNamespace(uid="user-a")

    async def no_shared(*args, **kwargs):
        return []

    monkeypatch.setattr(skill_runtime, "lock_accessible_shared_skills_for_runtime", no_shared)
    first = await resolve_runtime_skills_for_context(context, db=None, user=user)
    replacement = directory / "replacement"
    replacement.write_text("---\nname: personal\ndescription: second\n---\nSecond", encoding="utf-8")
    replacement.replace(skill_file)
    second = await resolve_runtime_skills_for_context(context, db=None, user=user)
    assert first["skill_metadata"]["personal"] != second["skill_metadata"]["personal"]


@pytest.mark.asyncio
async def test_runtime_skill_selection_rechecks_each_row_without_listing_all(monkeypatch):
    """逐行加锁后复核权限，不展开旧依赖且不读取其他 Agent 的专属 Skill。"""
    from yuxi.modules.extensions.skills import access

    items = {
        "root": SimpleNamespace(slug="root", bound_agent_id=None, enabled=True, skill_dependencies=["other-bound", "denied", "dep"]),
        "other-bound": SimpleNamespace(slug="other-bound", bound_agent_id=99, enabled=True, skill_dependencies=[]),
        "denied": SimpleNamespace(slug="denied", bound_agent_id=None, enabled=False, skill_dependencies=[]),
        "dep": SimpleNamespace(slug="dep", bound_agent_id=None, enabled=True, skill_dependencies=[]),
    }

    class Repository:
        """按真实选中路径提供已锁定行。"""

        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug):
            return items.get(slug)

        async def get_by_slug_for_read(self, slug):
            return items.get(slug)

        async def list_enabled_readable(self, _user):
            raise AssertionError("不得全量扫描共享 Skill")

    monkeypatch.setattr(access, "SkillRepository", Repository)
    monkeypatch.setattr(access, "user_can_access_skill", lambda _user, item: item.enabled)
    visible = await access.lock_accessible_shared_skills_for_runtime(None, object(), ["root"])
    assert [item.slug for item in visible] == ["root"]
    bound = await access.lock_accessible_shared_skills_for_runtime(None, object(), ["other-bound"], bound_slug="other-bound")
    assert [item.slug for item in bound] == ["other-bound"]


def _mock_runtime_sources(monkeypatch, accessible):
    """让运行时组合测试使用给定共享和个人候选。"""

    async def locked(db, user, _selected, *, shadowed_slugs=None):
        items = await accessible(db, user)
        for item in items:
            if not hasattr(item, "id"):
                item.id = item.slug
        shadowed_slugs = shadowed_slugs or set()
        return [item for item in items if item.source_scope != "personal" and item.slug not in shadowed_slugs and item.slug in _selected]

    async def personal(uid):
        items = await accessible(object(), SimpleNamespace(uid=uid))
        return [item for item in items if item.source_scope == "personal"]

    monkeypatch.setattr(skill_runtime, "lock_accessible_shared_skills_for_runtime", locked)
    monkeypatch.setattr(skill_runtime, "list_personal_skills", personal)
    monkeypatch.setattr(skill_runtime, "resolved_shared_skill", lambda item: item)


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [[], ["shared"], "all"])
@pytest.mark.parametrize("preloads", [[], "all"])
async def test_personal_skills_are_available_independently_of_shared_selection(tmp_path, monkeypatch, selection, preloads):
    """真实个人目录始终参与运行，选项仅共享且其他用户目录不可见。"""
    from yuxi.modules.agents.runtime.context import normalize_agent_context_config, resolve_agent_resource_options
    from yuxi.modules.extensions.skills import shared as service
    from yuxi.modules.extensions.skills.models import Skill
    from yuxi.modules.workspace import paths

    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path / "user-data")
    monkeypatch.setattr(service, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr("yuxi.modules.extensions.skills.access.get_skill_data_dir", lambda: tmp_path)
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

        async def get_by_slug(self, slug):
            """模拟单行无锁可见性筛选。"""
            return next((item for item in (shared, extra) if item.slug == slug), None)

    monkeypatch.setattr(service, "SkillRepository", FakeSkillRepository)
    monkeypatch.setattr("yuxi.modules.extensions.skills.access.SkillRepository", FakeSkillRepository)
    monkeypatch.setattr("yuxi.modules.extensions.skills.repository.SkillRepository", FakeSkillRepository)
    for uid, slug in [("user-a", "personal"), ("user-a", "shared"), ("user-b", "other-user")]:
        directory = user_workspace_dir(uid) / "agents" / "skills" / slug
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"---\nname: {slug}\ndescription: personal {slug}\n---\nPersonal body", encoding="utf-8")
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
    assert scope["runtime_skills"]["shared"]["path"] == "/home/user/workspace/agents/skills/shared/SKILL.md"
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
async def test_resolve_runtime_skills_derives_authorized_scope(monkeypatch, tmp_path):
    """运行时 scope 只保留授权选择，并按依赖闭包区分共享与个人来源。"""
    personal_dir = tmp_path / "beta"
    personal_dir.mkdir()
    (personal_dir / "SKILL.md").write_text("# beta", encoding="utf-8")

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
                skill_dependencies=["beta"],
            ),
            SimpleNamespace(
                slug="beta",
                name="Beta",
                description="beta desc",
                source_scope="personal",
                version=None,
                content_hash=None,
                source_dir=personal_dir,
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
    assert scope["runtime_skills"]["alpha"]["path"] == "/home/user/skills/alpha/SKILL.md"
    assert scope["runtime_skills"]["beta"]["path"] == "/home/user/workspace/agents/skills/beta/SKILL.md"
    assert "skills" not in scope["runtime_skills"]["alpha"]


@pytest.mark.asyncio
async def test_preload_reads_only_selected_content(tmp_path, monkeypatch):
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
    assert scope["preloaded_skills"] == ["alpha"]
    assert scope["preloaded_skill_contents"] == {
        "alpha": "# Alpha\nUSE_ALPHA",
    }
    context = SimpleNamespace(skills=["alpha"], preload_skills=["alpha"], skill_runtime_snapshot=scope)
    (skills[0].source_dir / "SKILL.md").write_text("# Alpha\nUPDATED", encoding="utf-8")
    refreshed = await resolve_runtime_skills_for_context(context, db=object(), user=SimpleNamespace(uid="test"))
    assert refreshed["preloaded_skill_contents"]["alpha"] == "# Alpha\nUPDATED"
    assert refreshed["skill_metadata"]["alpha"] != scope["skill_metadata"]["alpha"]


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
    from yuxi.modules.agents.services.preparation import build_skill_manifest_entries

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
    [([], []), (["missing"], []), (["alpha", "missing"], ["alpha"]), ("all", ["alpha", "beta", "gamma"])],
)
async def test_preload_all_reads_only_enabled_authorized_skills(tmp_path, monkeypatch, selection, expected):
    """全部预加载沿真实解析链读取已启用的授权 Skill 文件。"""
    from yuxi.modules.agents.runtime.context import normalize_agent_context_config
    from yuxi.modules.extensions.skills import shared as service

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
    scope = await resolve_runtime_skills_for_context(SimpleNamespace(**normalized), db=None, user=SimpleNamespace(uid="test"))
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


@pytest.mark.asyncio
async def test_plugin_entry_is_not_preloaded_or_directly_selected(tmp_path, monkeypatch):
    """插件入口只作为加载结果准备，不能借普通选择或预加载进入模型。"""
    entry = _skill(tmp_path, "abridge", content="# image instructions")
    shared = _skill(tmp_path, "chat-widgets", content="# shared widget instructions")

    async def accessible(_db, _user):
        return [entry, shared]

    _mock_runtime_sources(monkeypatch, accessible)
    disabled = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=["abridge"], preload_skills=["abridge"], plugins=[]),
        db=object(),
        user=SimpleNamespace(uid="test"),
    )
    assert disabled["effective_skills"] == []
    assert disabled["plugin_entry_contents"] == {}
    enabled = await resolve_runtime_skills_for_context(
        SimpleNamespace(skills=[], preload_skills=[], plugins=["abridge"]),
        db=object(),
        user=SimpleNamespace(uid="test"),
    )
    assert enabled["context_skills"] == []
    assert enabled["preloaded_skill_contents"] == {}
    assert enabled["plugin_entry_contents"] == {"abridge": "# image instructions"}
    assert set(enabled["effective_skills"]) == {"abridge", "chat-widgets"}


@pytest.mark.asyncio
async def test_plugin_entry_cannot_be_shadowed_by_personal_skill(tmp_path, monkeypatch):
    """个人同名文件不能替换部署插件的使用说明。"""
    entry = _skill(tmp_path, "abridge")
    entry.source_scope = "personal"

    async def accessible(_db, _user):
        return [entry]

    _mock_runtime_sources(monkeypatch, accessible)
    for plugins in ([], ["abridge"]):
        snapshot = await resolve_runtime_skills_for_context(
            SimpleNamespace(skills=[], preload_skills=[], plugins=plugins),
            db=object(),
            user=SimpleNamespace(uid="test"),
        )
        assert snapshot["context_skills"] == ["abridge"]
        assert snapshot["runtime_skills"]["abridge"]["path"].startswith("/home/user/workspace/")
        assert snapshot["plugin_entry_contents"] == {}
        assert snapshot["capability_limited"] == bool(plugins)
