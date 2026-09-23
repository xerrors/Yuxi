"""Agent 专属技能的绑定、权限派生与运行时强制预加载。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.agents.skills import service as skill_service
from yuxi.agents.skills.service import (
    AGENT_BOUND_SKILL_SOURCE_TYPE,
    can_skill_depend_on,
    is_agent_bound_skill,
)
from yuxi.permissions import ResourcePermission


def _agent(*, slug: str = "mysql-reader-agent", created_by: str = "owner", share_config=None):
    return SimpleNamespace(
        id=7,
        slug=slug,
        name="MySQL Reader",
        description="读取 MySQL",
        created_by=created_by,
        share_config=share_config
        or {
            "version": 2,
            "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["owner", "reader"]},
            "manage_scope": None,
        },
    )


def _bound_skill(*, slug: str = "mysql-reader-agent-self-skill", agent_id: int = 7):
    return SimpleNamespace(
        id=11,
        slug=slug,
        name="MySQL Reader",
        description="MySQL 专属规则",
        source_type="upload",
        bound_agent_id=agent_id,
        enabled=True,
        created_by="owner",
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
        version=None,
        content_hash=None,
        dir_path="skill-sources/shared/mysql-reader-agent-self-skill",
    )


class _FakeSkillRepo:
    def __init__(self, item=None):
        self.item = item

    async def get_by_bound_agent_id(self, agent_id):
        assert agent_id == 7
        return self.item


def test_is_agent_bound_skill_uses_binding_column_not_slug():
    """绑定依据是 bound_agent_id，不是 slug 命名约定。"""
    assert is_agent_bound_skill({"bound_agent_id": 3}) is True
    assert is_agent_bound_skill({"bound_agent_id": None}) is False
    assert is_agent_bound_skill(_bound_skill()) is True
    assert is_agent_bound_skill(_bound_skill(agent_id=None)) is False


def test_ensure_not_agent_bound_rejects_share_enable_and_delete():
    """绑定 Skill 不允许单独共享、启停或删除。"""
    with pytest.raises(ValueError, match="不允许单独共享"):
        skill_service._ensure_not_agent_bound(_bound_skill())
    # 普通 Skill 不受影响。
    skill_service._ensure_not_agent_bound(SimpleNamespace(bound_agent_id=None))


@pytest.mark.asyncio
async def test_effective_skill_permission_derives_from_bound_agent(monkeypatch):
    """绑定 Skill 权限完全派生自绑定 Agent，与自身 share_config 无关。"""

    class _Repo:
        def __init__(self, db):
            pass

        async def get_by_id(self, agent_id):
            return _agent()

        async def list_by_ids(self, agent_ids):
            return [_agent()]

    monkeypatch.setattr("yuxi.repositories.agent_repository.AgentRepository", _Repo)

    owner = SimpleNamespace(uid="owner", role="user")
    reader = SimpleNamespace(uid="reader", role="user")
    outsider = SimpleNamespace(uid="outsider", role="user")

    item = _bound_skill()
    assert await skill_service._effective_skill_permission(None, owner, item) == ResourcePermission.MANAGE
    assert await skill_service._effective_skill_permission(None, reader, item) == ResourcePermission.READ
    assert await skill_service._effective_skill_permission(None, outsider, item) == ResourcePermission.NONE


@pytest.mark.asyncio
async def test_agent_permission_change_immediately_changes_self_skill(monkeypatch):
    """扩大 Agent 共享范围后，绑定 Skill 权限同步变化，不需要同步任务。"""
    shared = _agent(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        }
    )

    class _Repo:
        def __init__(self, db):
            pass

        async def get_by_id(self, agent_id):
            return shared

        async def list_by_ids(self, agent_ids):
            return [shared]

    monkeypatch.setattr("yuxi.repositories.agent_repository.AgentRepository", _Repo)

    outsider = SimpleNamespace(uid="outsider", role="user")
    assert await skill_service._effective_skill_permission(None, outsider, _bound_skill()) == ResourcePermission.READ


@pytest.mark.asyncio
async def test_missing_bound_agent_is_fail_closed(monkeypatch):
    """绑定 Agent 不存在时拒绝访问，不退化为普通 Skill。"""

    class _Repo:
        def __init__(self, db):
            pass

        async def get_by_id(self, agent_id):
            return None

        async def list_by_ids(self, agent_ids):
            return []

    monkeypatch.setattr("yuxi.repositories.agent_repository.AgentRepository", _Repo)

    owner = SimpleNamespace(uid="owner", role="user")
    assert await skill_service._effective_skill_permission(None, owner, _bound_skill()) == ResourcePermission.NONE


def test_resolved_agent_bound_skill_projects_agent_permission_inputs(tmp_path):
    """运行时视图装载 Agent 的权限输入与共享虚拟路径，不写副本。"""
    agent = _agent()
    item = _bound_skill()
    item.source_dir = tmp_path

    resolved = skill_service._resolved_agent_bound_skill(item, agent)

    assert resolved.source_scope == AGENT_BOUND_SKILL_SOURCE_TYPE
    assert resolved.bound_agent_id == 7
    assert resolved.created_by == "owner"
    # share_config 是 Agent 配置的读时投影，因此解析结果等于 Agent 权限。
    assert resolved.share_config == skill_service.normalize_permission_config(agent.share_config)
    # 绑定关系不出现在 to_dict 的对外选择性字段里之外，但可被前端识别。
    assert resolved.to_dict()["is_agent_bound"] is True


def test_bound_skill_dependency_uses_agent_read_scope():
    """绑定 Skill 声明依赖时，父范围取绑定 Agent 的读取范围。"""
    bound = _bound_skill()
    agent = _agent()
    parent_config = skill_service.normalize_permission_config(agent.share_config)

    shared_dep = SimpleNamespace(
        slug="mysql-reporter",
        source_type="upload",
        enabled=True,
        created_by="owner",
        bound_agent_id=None,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        },
    )
    assert (
        skill_service._can_depend_on(
            parent_config=parent_config,
            parent_created_by=bound.created_by,
            parent_is_bound=True,
            dependency=shared_dep,
        )
        is True
    )

    private_dep = SimpleNamespace(
        slug="someone-elses-skill",
        source_type="upload",
        enabled=True,
        created_by="stranger",
        bound_agent_id=None,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["stranger"]},
            "manage_scope": None,
        },
    )
    assert (
        skill_service._can_depend_on(
            parent_config=parent_config,
            parent_created_by=bound.created_by,
            parent_is_bound=True,
            dependency=private_dep,
        )
        is False
    )

    # 普通 Skill 仍按自身 share_config 判断，行为不回退。
    plain_parent = SimpleNamespace(
        slug="plain",
        source_type="upload",
        bound_agent_id=None,
        created_by="owner",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        },
    )
    assert can_skill_depend_on(plain_parent, shared_dep) is True


@pytest.mark.asyncio
async def test_list_skill_slugs_hides_agent_bound_entries(monkeypatch):
    """依赖引用列表不包含 Agent 专属技能。"""

    async def fake_list_accessible_shared_skills(db, user, *, require_enabled=True):
        return [_bound_skill(), _bound_skill(slug="plain-skill", agent_id=None)]

    monkeypatch.setattr(skill_service, "_list_accessible_shared_skills", fake_list_accessible_shared_skills)

    slugs = await skill_service.list_skill_slugs(None, user=SimpleNamespace(uid="owner", role="user"))
    assert slugs == ["plain-skill"]


class _FakeDb:
    """记录提交与回滚的最小 AsyncSession 替身。"""

    def __init__(self, fail_on_commit=False):
        self.fail_on_commit = fail_on_commit
        self.committed = 0
        self.rolled_back = 0

    async def commit(self):
        if self.fail_on_commit:
            raise RuntimeError("commit failed")
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    async def flush(self):
        return None

    async def refresh(self, item):
        return item


class _UploadRepo:
    """覆盖 upload_agent_self_skill 用到的 SkillRepository 方法。"""

    def __init__(self, db, item=None):
        self.db = db
        self.item = item
        self.created_kwargs = None
        self.updated_metadata = None
        self.updated_dependencies = None

    async def get_by_bound_agent_id(self, agent_id, *, for_update=False):
        assert agent_id == 7
        return self.item

    async def exists_slug(self, slug):
        return False

    async def create(self, **kwargs):
        self.created_kwargs = kwargs
        return SimpleNamespace(**kwargs)

    async def update_metadata(self, item, *, name, description, updated_by):
        self.updated_metadata = {"name": name, "description": description}
        return item

    async def update_dependencies(self, item, *, tool_dependencies, mcp_dependencies, skill_dependencies, updated_by):
        self.updated_dependencies = {
            "tool_dependencies": tool_dependencies,
            "mcp_dependencies": mcp_dependencies,
            "skill_dependencies": skill_dependencies,
        }
        return item


def _zip_with_skill_md(skill_md: str) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("pkg/SKILL.md", skill_md)
    return buffer.getvalue()


def _uploaded_skill_md(slug: str, name: str = "上传规则") -> str:
    return f"---\nslug: {slug}\nname: {name}\ndescription: 来自上传包\ntool_dependencies: [file_read]\n---\n\n# {name}\n\n上传内容。\n"


@pytest.mark.asyncio
async def test_upload_agent_self_skill_uses_derived_slug_and_ignores_package_slug(monkeypatch, tmp_path):
    """上传创建时 slug 按 Agent 派生，包内 slug 被重写，不占用其他名字。"""
    skills_root = tmp_path / "shared"
    skills_root.mkdir()
    monkeypatch.setattr(skill_service, "get_skills_root_dir", lambda: skills_root)

    db = _FakeDb()
    repo_holder = {}

    class _Repo(_UploadRepo):
        def __init__(self, db_session):
            super().__init__(db_session)
            repo_holder["repo"] = self

    monkeypatch.setattr(skill_service, "SkillRepository", _Repo)

    result = await skill_service.upload_agent_self_skill(
        db,
        agent=_agent(),
        filename="package.zip",
        file_bytes=_zip_with_skill_md(_uploaded_skill_md("totally-different-slug")),
    )

    derived_slug = "mysql-reader-agent-self-skill"
    assert result.slug == derived_slug
    assert result.bound_agent_id == 7
    assert repo_holder["repo"].created_kwargs["tool_dependencies"] == ["file_read"]
    assert db.committed == 1
    assert (skills_root / derived_slug / "SKILL.md").read_text(encoding="utf-8").startswith(f"---\nslug: {derived_slug}")


@pytest.mark.asyncio
async def test_upload_agent_self_skill_replaces_existing_content_atomically(monkeypatch, tmp_path):
    """重复上传整体替换内容：旧文件不残留，元数据同步更新。"""
    skills_root = tmp_path / "shared"
    slug = "mysql-reader-agent-self-skill"
    existing_dir = skills_root / slug
    existing_dir.mkdir(parents=True)
    (existing_dir / "SKILL.md").write_text("---\nslug: old\nname: 旧\n---\n", encoding="utf-8")
    (existing_dir / "stale-script.py").write_text("print('stale')\n", encoding="utf-8")
    monkeypatch.setattr(skill_service, "get_skills_root_dir", lambda: skills_root)

    db = _FakeDb()
    repo_holder = {}

    class _Repo(_UploadRepo):
        def __init__(self, db_session):
            super().__init__(db_session, item=_bound_skill())
            repo_holder["repo"] = self

    monkeypatch.setattr(skill_service, "SkillRepository", _Repo)

    result = await skill_service.upload_agent_self_skill(
        db,
        agent=_agent(),
        filename="SKILL.md",
        file_bytes=_uploaded_skill_md("ignored", name="新规则").encode("utf-8"),
    )

    assert result.slug == slug
    assert repo_holder["repo"].created_kwargs is None
    assert repo_holder["repo"].updated_metadata == {"name": "新规则", "description": "来自上传包"}
    assert (skills_root / slug / "SKILL.md").read_text(encoding="utf-8").startswith(f"---\nslug: {slug}")
    assert not (skills_root / slug / "stale-script.py").exists()
    assert not list(skills_root.glob(".*"))


@pytest.mark.asyncio
async def test_upload_agent_self_skill_rejects_unsupported_file_and_restores_content(monkeypatch, tmp_path):
    """非法后缀直接拒绝；写入库失败时旧内容原样恢复，不留下半替换。"""
    skills_root = tmp_path / "shared"
    slug = "mysql-reader-agent-self-skill"
    existing_dir = skills_root / slug
    existing_dir.mkdir(parents=True)
    (existing_dir / "SKILL.md").write_text("---\nslug: old\nname: 旧\n---\n", encoding="utf-8")
    monkeypatch.setattr(skill_service, "get_skills_root_dir", lambda: skills_root)

    with pytest.raises(ValueError, match="仅支持上传"):
        await skill_service.upload_agent_self_skill(
            _FakeDb(), agent=_agent(), filename="readme.txt", file_bytes=b"x"
        )
    assert (existing_dir / "SKILL.md").read_text(encoding="utf-8") == "---\nslug: old\nname: 旧\n---\n"

    monkeypatch.setattr(skill_service, "SkillRepository", lambda db: _UploadRepo(db, item=_bound_skill()))

    class _FailingRepo(_UploadRepo):
        def __init__(self, db):
            super().__init__(db, item=_bound_skill())

        async def update_metadata(self, item, *, name, description, updated_by):
            raise RuntimeError("db down")

    monkeypatch.setattr(skill_service, "SkillRepository", _FailingRepo)

    failing_db = _FakeDb()
    with pytest.raises(RuntimeError, match="db down"):
        await skill_service.upload_agent_self_skill(
            failing_db,
            agent=_agent(),
            filename="SKILL.md",
            file_bytes=_uploaded_skill_md("ignored").encode("utf-8"),
        )
    assert failing_db.rolled_back == 1
    assert (existing_dir / "SKILL.md").read_text(encoding="utf-8") == "---\nslug: old\nname: 旧\n---\n"
    assert not list(skills_root.glob(".*"))


@pytest.mark.asyncio
async def test_upload_agent_self_skill_rejects_zip_with_multiple_skills(monkeypatch, tmp_path):
    """ZIP 包含多个 SKILL.md 时拒绝，不猜测挑哪一个。"""
    import io
    import zipfile

    skills_root = tmp_path / "shared"
    skills_root.mkdir()
    monkeypatch.setattr(skill_service, "get_skills_root_dir", lambda: skills_root)
    monkeypatch.setattr(skill_service, "SkillRepository", lambda db: _UploadRepo(db))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("a/SKILL.md", _uploaded_skill_md("a"))
        zf.writestr("b/SKILL.md", _uploaded_skill_md("b"))

    with pytest.raises(ValueError, match="必须且只能包含一个技能"):
        await skill_service.upload_agent_self_skill(
            _FakeDb(), agent=_agent(), filename="bundle.zip", file_bytes=buffer.getvalue()
        )
    assert not list(skills_root.iterdir())
