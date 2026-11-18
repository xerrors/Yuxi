from __future__ import annotations

import asyncio
from yuxi.modules.extensions.skills import package as skill_package
from yuxi.modules.extensions.skills import access as skill_access
from yuxi.modules.extensions.skills.builtin import sync as builtin_service
import io
import json
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from yuxi.modules.extensions.skills.repository import SkillRepository as RealSkillRepository
from yuxi.modules.extensions.skills import catalog
from yuxi.modules.extensions.skills import install_draft as draft_service
from yuxi.modules.extensions.skills import edit as edit_service
from yuxi.modules.extensions.skills import content as content_service
from yuxi.modules.extensions.skills import package as package_service
from yuxi.modules.extensions.skills import personal as personal_service
from yuxi.modules.extensions.skills import projection as projection_service
from yuxi.modules.extensions.skills import shared as svc
from yuxi.modules.extensions.skills.remote import DownloadedSkill
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.paths import user_workspace_dir

_MULTIPROCESS_SKILL_SYNC_SCRIPT = """
import json
import os
import select
import sys
import time
import traceback
from pathlib import Path
from yuxi.modules.extensions.skills import projection as service

save_dir, uid, encoded_sources = sys.argv[1:]
sources = json.loads(encoded_sources)
service.get_skill_projection_dir = lambda: Path(save_dir) / "skill-projections"

ready_read, ready_write = os.pipe()
release_read, release_write = os.pipe()
done_read, done_write = os.pipe()
started_read, started_write = os.pipe()

holder_pid = os.fork()
if holder_pid == 0:
    os.close(ready_read)
    os.close(release_write)
    os.close(done_read)
    os.close(done_write)
    os.close(started_read)
    os.close(started_write)
    with service._user_skills_file_lock(uid):
        os.write(ready_write, b"ready")
        os.read(release_read, 1)
    os._exit(0)

os.close(ready_write)
os.close(release_read)
os.read(ready_read, 5)

worker_pid = os.fork()
if worker_pid == 0:
    os.close(ready_read)
    os.close(release_write)
    os.close(done_read)
    os.close(started_read)
    try:
        os.write(started_write, b"started")
        service.sync_user_accessible_skills(uid, sources)
        os.write(done_write, b"done")
    except BaseException:
        traceback.print_exc()
        os._exit(1)
    os._exit(0)

os.close(done_write)
os.close(started_write)
os.read(started_read, 7)
blocked = not bool(select.select([done_read], [], [], 0.5)[0])
os.write(release_write, b"x")
os.close(release_write)
finished = bool(select.select([done_read], [], [], 10)[0])
holder_code = os.waitstatus_to_exitcode(os.waitpid(holder_pid, 0)[1])
worker_code = os.waitstatus_to_exitcode(os.waitpid(worker_pid, 0)[1])
if not blocked or not finished or holder_code or worker_code:
    raise SystemExit(1)
raise SystemExit(0)
"""


def _build_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _user(uid: str = "root", role: str = "admin") -> User:
    return User(username=uid, uid=uid, password_hash="x", role=role, department_id=1)


def _fake_skill_repository(monkeypatch, target=svc, *, existing_slugs=(), methods=None):
    """为 Skill 用例提供统一的轻量仓储替身。"""

    class FakeRepo(RealSkillRepository):
        created_item: Skill | None = None
        created_items: list[Skill] = []

        def __init__(self, _db):
            pass

        async def exists_slug(self, slug: str) -> bool:
            return slug in self.__class__.existing_slugs

        async def create(self, **kwargs) -> Skill:
            item = Skill(**kwargs, updated_by=kwargs["created_by"])
            self.__class__.created_item = item
            self.__class__.created_items.append(item)
            self.__class__.existing_slugs.add(item.slug)
            return item

    FakeRepo.existing_slugs = set(existing_slugs)
    for name, implementation in (methods or {}).items():
        setattr(FakeRepo, name, implementation)
    monkeypatch.setattr(target, "SkillRepository", FakeRepo)
    if target is svc:
        monkeypatch.setattr(skill_access, "SkillRepository", FakeRepo)
    return FakeRepo


class _UnitOfWork:
    def add(self, item):
        repo_type = svc.SkillRepository
        if hasattr(repo_type, "created_item"):
            repo_type.created_item = item
        item.id = item.id or 1

    async def flush(self):
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _isolated_skill_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每个 Skill unit 使用独立的显式存储域。"""
    monkeypatch.setenv("YUXI_SKILL_DATA_DIR", str(tmp_path / "skill-sources"))
    monkeypatch.setenv("YUXI_SKILL_PROJECTION_DIR", str(tmp_path / "skill-projections"))
    monkeypatch.setenv("YUXI_RUNTIME_DIR", str(tmp_path / "runtime"))

    class ContentRepo:
        def __init__(self, db):
            pass

        async def list_enabled_readable(self, operator):
            return []

    async def no_prune(db, slug):
        pass

    async def no_mcps(db=None):
        return []

    monkeypatch.setattr(content_service, "SkillRepository", ContentRepo)
    monkeypatch.setattr(content_service, "prune_skill_content", no_prune)
    monkeypatch.setattr("yuxi.modules.extensions.mcp.service.get_enabled_mcp_server_slugs", no_mcps)


def test_allowed_skill_access_levels_by_role():
    assert skill_access.get_allowed_skill_access_levels(_user(role="user")) == ["user"]
    assert skill_access.get_allowed_skill_access_levels(_user(role="admin")) == ["global", "department", "user"]
    assert skill_access.get_allowed_skill_access_levels(_user(role="superadmin")) == ["global", "department", "user"]


@pytest.mark.asyncio
async def test_create_remote_skill_draft_stages_success_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    valid_dir = tmp_path / "remote-pdf"
    invalid_dir = tmp_path / "remote-broken"
    valid_dir.mkdir()
    invalid_dir.mkdir()
    (valid_dir / "SKILL.md").write_text(
        "---\nname: pdf\ndescription: PDF operations\n---\n# PDF\n",
        encoding="utf-8",
    )

    class FakePreparation:
        results = [
            DownloadedSkill(slug="pdf", source_dir=valid_dir),
            DownloadedSkill(slug="broken", source_dir=invalid_dir),
            DownloadedSkill(slug="duplicate", source_dir=valid_dir),
        ]
        cleaned = False

        async def cleanup(self):
            self.cleaned = True

    preparation = FakePreparation()

    async def fake_download_remote_skills(*, source, skills, uid):
        assert uid == str(_user().uid)
        assert source == "anthropics/skills"
        assert skills == ["pdf", "broken", "duplicate"]
        return preparation

    monkeypatch.setattr(
        "yuxi.modules.extensions.skills.remote.download_remote_skills",
        fake_download_remote_skills,
    )
    draft = await draft_service.create_remote_skill_draft(
        source="anthropics/skills",
        skills=["pdf", "broken", "duplicate"],
        operator=_user(),
    )

    assert [item["slug"] for item in draft["items"]] == ["pdf"]
    assert draft["failures"] == [
        {"slug": "broken", "error": "技能目录缺少根级 SKILL.md"},
        {"slug": "duplicate", "error": "Skill slug 重复: pdf"},
    ]
    staged_items = draft_service.get_skill_drafts_root_dir() / draft["draft_id"] / "items"
    assert [item.name for item in staged_items.iterdir()] == [Path(draft["items"][0]["source_dir"]).name]
    assert preparation.cleaned is True


@pytest.mark.asyncio
async def test_list_visible_skills_for_management_includes_owned_disabled_and_enabled_shared(
    monkeypatch: pytest.MonkeyPatch,
):
    items = [
        Skill(
            slug="owned-disabled",
            name="owned-disabled",
            description="",
            created_by="root",
            enabled=False,
            share_config={"version": 2, "read_scope": None, "manage_scope": None},
        ),
        Skill(
            slug="shared-enabled",
            name="shared-enabled",
            description="",
            created_by="other",
            enabled=True,
            share_config={
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["root"]},
                "manage_scope": {"access_level": "user", "user_uids": ["root"]},
            },
        ),
        Skill(
            slug="shared-disabled",
            name="shared-disabled",
            description="",
            created_by="other",
            enabled=False,
            share_config={
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["root"]},
                "manage_scope": {"access_level": "user", "user_uids": ["root"]},
            },
        ),
        Skill(
            slug="unrelated",
            name="unrelated",
            description="",
            created_by="other",
            enabled=True,
            share_config={"version": 2, "read_scope": None, "manage_scope": None},
        ),
    ]

    _fake_skill_repository(
        monkeypatch,
        target=svc,
        methods={"list_all": AsyncMock(return_value=items)},
    )

    visible = await svc.SkillRepository(None).list_visible_for_management(_user("root", role="admin"))

    assert [item.slug for item in visible] == ["owned-disabled", "shared-enabled", "shared-disabled"]


@pytest.mark.asyncio
async def test_locked_shared_list_only_locks_visible_rows_and_rechecks_permission(monkeypatch):
    """无权行不参与运行时锁；等待期间撤销的权限也不能进入结果。"""
    visible = Skill(
        id=1,
        slug="visible",
        enabled=True,
        created_by="other",
        share_config={"version": 2, "read_scope": {"access_level": "user", "user_uids": ["root"]}},
    )
    hidden = Skill(
        id=2,
        slug="hidden",
        enabled=True,
        created_by="other",
        share_config={"version": 2, "read_scope": {"access_level": "user", "user_uids": ["other"]}},
    )
    locked_ids = []

    async def lock_rows_for_read(self, ids):
        locked_ids.extend(ids)
        visible.share_config = {
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["other"]},
        }
        return [visible]

    _fake_skill_repository(
        monkeypatch,
        target=projection_service,
        methods={"list_all": AsyncMock(return_value=[visible, hidden]), "lock_rows_for_read": lock_rows_for_read},
    )

    result = await projection_service.lock_accessible_shared_skills_for_projection(None, _user("root", role="user"))

    assert locked_ids == [1]
    assert result == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill,operator",
    [
        (
            Skill(
                slug="owned-disabled",
                name="owned-disabled",
                description="",
                created_by="root",
                enabled=False,
                share_config={"version": 2, "read_scope": None, "manage_scope": None},
            ),
            _user("root", role="admin"),
        ),
        (
            Skill(
                slug="admin-disabled",
                name="admin-disabled",
                description="",
                created_by="other",
                enabled=False,
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "global"},
                    "manage_scope": {"access_level": "global"},
                },
            ),
            _user("root", role="admin"),
        ),
        (
            Skill(
                slug="shared-enabled",
                name="shared-enabled",
                description="",
                created_by="other",
                enabled=True,
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "user", "user_uids": ["root"]},
                    "manage_scope": {"access_level": "user", "user_uids": ["root"]},
                },
            ),
            _user("root", role="user"),
        ),
    ],
)
async def test_management_readable_skill_allows_manageable_disabled_and_enabled_shared(
    monkeypatch: pytest.MonkeyPatch,
    skill: Skill,
    operator: User,
):
    async def get_by_slug(self, slug: str):
        assert slug == skill.slug
        return skill

    _fake_skill_repository(
        monkeypatch,
        target=svc,
        methods={"get_by_slug": get_by_slug},
    )

    result = await skill_access.get_management_readable_skill_or_raise(None, operator, skill.slug)

    assert result is skill


@pytest.mark.asyncio
async def test_management_readable_skill_denies_disabled_ordinary_user(monkeypatch: pytest.MonkeyPatch):
    skill = Skill(
        slug="shared-disabled",
        name="shared-disabled",
        description="",
        created_by="other",
        enabled=False,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["root"]},
            "manage_scope": {"access_level": "user", "user_uids": ["root"]},
        },
    )

    async def get_by_slug(self, slug: str):
        assert slug == skill.slug
        return skill

    _fake_skill_repository(
        monkeypatch,
        target=svc,
        methods={"get_by_slug": get_by_slug},
    )

    with pytest.raises(ValueError, match="无权访问"):
        await skill_access.get_management_readable_skill_or_raise(None, _user("root", role="user"), skill.slug)


@pytest.mark.asyncio
async def test_runtime_access_still_excludes_disabled_shared_skill(monkeypatch: pytest.MonkeyPatch):
    skill = Skill(
        slug="shared-disabled",
        name="shared-disabled",
        description="",
        created_by="other",
        enabled=False,
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["root"]},
            "manage_scope": {"access_level": "user", "user_uids": ["root"]},
        },
    )

    _fake_skill_repository(
        monkeypatch,
        target=catalog,
        methods={"list_enabled": AsyncMock(return_value=[])},
    )

    async def no_personal_skills(_uid):
        return []

    monkeypatch.setattr(catalog, "list_personal_skills", no_personal_skills)

    assert skill_access.user_can_access_skill(_user("root", role="user"), skill) is False
    assert await catalog.list_accessible_skills(None, _user("root", role="user")) == []


@pytest.mark.asyncio
async def test_upload_draft_does_not_include_shared_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    draft = await draft_service.create_uploaded_skill_draft(
        filename="SKILL.md",
        file_bytes=b"---\nname: demo\ndescription: demo skill\n---\n# Demo\n",
        operator=_user("normal-user", role="user"),
    )

    assert "default_share_config" not in draft
    assert "allowed_access_levels" not in draft
    assert draft["items"][0]["slug"] == "demo"


@pytest.mark.parametrize(
    "share_config",
    [
        {
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "global"},
        },
        {
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [1]},
            "manage_scope": {"access_level": "department", "department_ids": [1]},
        },
    ],
)
@pytest.mark.asyncio
async def test_normal_user_confirm_skill_draft_rejects_wider_share_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    share_config: dict,
):

    _fake_skill_repository(
        monkeypatch,
        target=svc,
        methods={
            "exists_slug": AsyncMock(return_value=False),
            "create": AsyncMock(side_effect=AssertionError("普通用户的越权共享范围应在创建前被拒绝")),
        },
    )

    operator = _user("normal-user", role="user")
    draft = await draft_service.create_uploaded_skill_draft(
        filename="SKILL.md",
        file_bytes=b"---\nname: demo\ndescription: demo skill\n---\n# Demo\n",
        operator=operator,
    )

    with pytest.raises(ValueError, match="需要管理员权限"):
        await svc.confirm_skill_install_draft(
            _UnitOfWork(),
            draft_id=draft["draft_id"],
            share_config=share_config,
            operator=operator,
        )


@pytest.mark.asyncio
async def test_install_draft_loader_only_selects_ready_slugs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft_dir = tmp_path / "draft"
    items_dir = draft_dir / "items"
    items_dir.mkdir(parents=True)
    for item_id in ("a" * 32, "b" * 32):
        (items_dir / item_id).mkdir()
    data = {
        "created_by": "root",
        "source_type": "remote",
        "items": [
            {"slug": "alpha", "source_dir": f"items/{'a' * 32}"},
            {"slug": "beta", "source_dir": f"items/{'b' * 32}"},
        ],
        "failures": [{"slug": "broken", "error": "加载失败"}],
    }
    monkeypatch.setattr(draft_service, "load_skill_draft", lambda _draft_id: (draft_dir, data))
    _draft_dir, _data, selected = draft_service.load_and_select_draft_items("draft-1", ["beta"], _user())

    assert [item.slug for item in selected] == ["beta"]


@pytest.mark.parametrize(("slugs", "message"), [([], "至少选择一个 Skill"), (["missing"], "草稿外的 Skill")])
@pytest.mark.asyncio
async def test_confirm_skill_install_draft_rejects_invalid_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    slugs: list[str],
    message: str,
):
    draft_dir = tmp_path / "draft"
    item_dir = draft_dir / "items" / ("a" * 32)
    item_dir.mkdir(parents=True)
    data = {
        "created_by": "root",
        "source_type": "remote",
        "items": [{"slug": "alpha", "source_dir": f"items/{'a' * 32}"}],
        "failures": [],
    }
    monkeypatch.setattr(draft_service, "load_skill_draft", lambda _draft_id: (draft_dir, data))

    with pytest.raises(ValueError, match=message):
        await svc.confirm_skill_install_draft(
            _UnitOfWork(),
            draft_id="draft-1",
            share_config={
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["root"]},
                "manage_scope": {"access_level": "user", "user_uids": ["root"]},
            },
            slugs=slugs,
            operator=_user(),
        )


def test_parse_skill_markdown_ok():
    content = "---\nname: demo-skill\ndescription: demo description\n---\n# Demo\n"
    slug, name, desc, meta = skill_package.parse_skill_markdown(content)
    assert slug == "demo-skill"
    assert name == "demo-skill"
    assert desc == "demo description"
    assert meta["name"] == "demo-skill"


def test_parse_skill_markdown_supports_display_name_with_slug():
    content = (
        "---\n"
        "name: Word / DOCX\n"
        "slug: word-docx\n"
        "version: 1.0.2\n"
        "homepage: https://clawic.com/skills/word-docx\n"
        "description: Create, inspect, and edit Microsoft Word documents.\n"
        'metadata: {"clawdbot":{"emoji":"📘","os":["linux","darwin","win32"]}}\n'
        "---\n"
        "# Word / DOCX\n"
    )
    slug, name, desc, meta = skill_package.parse_skill_markdown(content)
    assert slug == "word-docx"
    assert name == "Word / DOCX"
    assert desc == "Create, inspect, and edit Microsoft Word documents."
    assert meta["version"] == "1.0.2"


@pytest.mark.parametrize(
    ("name", "expected_slug"),
    [("周易智慧", "zhouyizhihui"), ("PRD to Prototype", "prd-to-prototype"), ("Word / DOCX", "word-docx"), ("✨", "skill-09324d876aae")],
)
def test_parse_skill_markdown_derives_stable_slug_without_changing_display_name(name, expected_slug):
    """缺少标识的展示名可重复解析为相同 slug，名称保持原样。"""
    content = f"---\nname: {name}\ndescription: demo\n---\n# Demo\n"
    for _ in range(2):
        slug, parsed_name, description, _ = skill_package.parse_skill_markdown(content)
        assert (slug, parsed_name, description) == (expected_slug, name, "demo")


def test_parse_skill_markdown_rejects_invalid_explicit_slug():
    """显式非法标识不能被名称派生掩盖。"""
    with pytest.raises(ValueError, match="frontmatter.slug 必须是小写字母/数字/短横线"):
        skill_package.parse_skill_markdown("---\nname: 展示名\nslug: Invalid_Slug\ndescription: demo\n---\n")


@pytest.mark.parametrize("explicit_slug", [False, True])
def test_copy_skill_snapshot_preserves_existing_identity_content(tmp_path, explicit_slug):
    """标识未变时保留注释和换行，避免改变文件修订。"""
    source = tmp_path / "source"
    source.mkdir()
    identity = "slug: demo\r\nname: Demo" if explicit_slug else "name: demo"
    content = f"---\r\n# keep comment\r\n{identity}\r\ndescription: demo\r\n---\r\n# Demo\r\n".encode()
    (source / "SKILL.md").write_bytes(content)
    target = tmp_path / "target"
    skill_package.copy_skill_snapshot(source, target, expected_slug="demo", final_slug="demo")
    assert (target / "SKILL.md").read_bytes() == content


def test_parse_skill_markdown_requires_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        skill_package.parse_skill_markdown("# missing")


@pytest.fixture
def builtin_skill_specs():
    return {spec["slug"]: spec for spec in builtin_service.list_builtin_skill_specs()}


@pytest.mark.parametrize("slug", ["image-gen", "deep-research", "knowledge-base", "mysql-reporter"])
def test_builtin_skill_spec_has_no_runtime_dependencies(builtin_skill_specs, slug):
    """部署插件拥有运行依赖，内置 Skill 只保留说明元数据。"""
    spec = builtin_skill_specs[slug]
    assert not {"tool_dependencies", "mcp_dependencies", "skill_dependencies"}.intersection(spec)
    assert (spec["source_dir"] / "SKILL.md").exists()

    if slug == "mysql-reporter":
        assert "reporter" not in builtin_skill_specs
        assert "deep-reporter" not in builtin_skill_specs
        for script_name in ("list_tables.py", "describe_table.py", "query.py"):
            script_path = spec["source_dir"] / "scripts" / script_name
            assert script_path.exists()
            assert script_path.read_text(encoding="utf-8").startswith("# /// script")


def test_is_valid_skill_slug():
    # Test valid slugs
    assert skill_package.is_valid_skill_slug("demo-skill") is True
    assert skill_package.is_valid_skill_slug("valid-name-123") is True
    # Test invalid slugs
    assert skill_package.is_valid_skill_slug("../bad") is False
    assert skill_package.is_valid_skill_slug("Invalid") is False  # uppercase not allowed
    assert skill_package.is_valid_skill_slug(" demo ") is False
    assert skill_package.is_valid_skill_slug("demo\n") is False
    assert skill_package.is_valid_skill_slug("") is False


@pytest.mark.parametrize(
    ("seed_files", "mutation", "steps"),
    [
        (
            {"skills/alpha": "alpha"},
            None,
            [({}, {})],
        ),
        (
            {"skills/alpha": "alpha", "skills/beta": "beta"},
            None,
            [
                ({"alpha": "skills/alpha"}, {"alpha": "alpha"}),
                ({"beta": "skills/beta"}, {"beta": "beta"}),
            ],
        ),
        (
            {"skills/demo": "shared", "personal/demo": "personal"},
            ("personal/demo", "changed"),
            [
                ({"demo": "personal/demo"}, {"demo": "personal"}),
                ({"demo": "personal/demo"}, {"demo": "changed"}),
            ],
        ),
    ],
)
def test_sync_user_accessible_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_files: dict[str, str],
    mutation: tuple[str, str] | None,
    steps: list[tuple],
):
    for rel, content in seed_files.items():
        skill_dir = tmp_path / rel
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    for step_index, (source_dirs, expected_entries) in enumerate(steps):
        if step_index > 0 and mutation is not None:
            rel_path, new_content = mutation
            (tmp_path / rel_path / "SKILL.md").write_text(new_content, encoding="utf-8")

        resolved_sources = {slug: tmp_path / rel for slug, rel in source_dirs.items()}
        user_root = projection_service.sync_user_accessible_skills("user_1", resolved_sources)

        assert user_root == tmp_path / "skill-projections" / "user_1"
        assert sorted(path.name for path in user_root.iterdir()) == sorted(expected_entries)
        for name, content in expected_entries.items():
            entry = user_root / name
            assert entry.is_dir()
            assert not entry.is_symlink()
            assert (entry / "SKILL.md").read_text(encoding="utf-8") == content


def test_get_user_skills_root_dir_does_not_create_directory(tmp_path: Path):
    """查询投影路径不创建用户目录或存储根目录。"""
    root = projection_service.get_user_skills_root_dir("user-1")

    assert root == tmp_path / "skill-projections" / "user-1"
    assert not root.parent.exists()


def test_sync_empty_skill_projection_creates_directory(tmp_path: Path):
    """没有授权 Skill 时，同步仍创建可挂载的空投影目录。"""
    root = projection_service.sync_user_accessible_skills("user-1", {})

    assert root == tmp_path / "skill-projections" / "user-1"
    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_unchanged_skill_projection_does_not_create_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """重复刷新直接保留既有文件，不再复制内容相同的 staging。"""
    source = tmp_path / "sources/demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# unchanged\n", encoding="utf-8")
    projection = projection_service.sync_user_accessible_skills("user-1", {"demo": source})
    projected_file = projection / "demo/SKILL.md"
    original_inode = projected_file.stat().st_ino

    def refuse_staging(*args, **kwargs):
        raise AssertionError("未变化的投影不应创建 staging")

    monkeypatch.setattr(projection_service, "copy_skill_tree_no_symlinks", refuse_staging)
    projection_service.sync_user_accessible_skills("user-1", {"demo": source})

    assert projected_file.read_text(encoding="utf-8") == "# unchanged\n"
    assert projected_file.stat().st_ino == original_inode
    assert sorted(path.name for path in projection.iterdir()) == ["demo"]


@pytest.mark.parametrize("linked_side", ["source", "projection"])
def test_projection_comparison_does_not_accept_equal_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked_side: str):
    """字节相同的链接不能通过未变化判定；非法来源撤下旧投影。"""
    source = tmp_path / "sources/demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# identical\n", encoding="utf-8")
    projection = projection_service.sync_user_accessible_skills("user-1", {"demo": source})
    outside = tmp_path / "outside.md"
    outside.write_text("# identical\n", encoding="utf-8")
    linked_file = (source if linked_side == "source" else projection / "demo") / "SKILL.md"
    linked_file.unlink()
    linked_file.symlink_to(outside)

    if linked_side == "source":
        with pytest.raises(PermissionError, match="symlink"):
            projection_service.sync_user_accessible_skills("user-1", {"demo": source})
        assert not (projection / "demo").exists()
    else:
        projection_service.sync_user_accessible_skills("user-1", {"demo": source})
        assert not linked_file.is_symlink()
        assert linked_file.read_text(encoding="utf-8") == "# identical\n"
    assert outside.read_text(encoding="utf-8") == "# identical\n"


@pytest.mark.parametrize("component", ["root", "ancestor"])
def test_projection_comparison_rejects_symlinked_source_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, component: str):
    """投影快路径逐层校验来源根和祖先，不读取链接后的目录。"""
    source = tmp_path / "sources/demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# same\n", encoding="utf-8")
    projection = projection_service.sync_user_accessible_skills("user-1", {"demo": source})
    linked = tmp_path / "linked"
    linked.symlink_to(source if component == "root" else source.parent, target_is_directory=True)
    with pytest.raises(OSError):
        projection_service.sync_user_accessible_skills("user-1", {"demo": linked if component == "root" else linked / "demo"})
    assert not (projection / "demo").exists()


def test_projection_comparison_rejects_file_replaced_after_stat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """文件在检查后变成链接时，真正打开的 fd 仍拒绝它。"""
    source = tmp_path / "sources/demo"
    source.mkdir(parents=True)
    source_file = source / "SKILL.md"
    source_file.write_text("# same\n", encoding="utf-8")
    projection = projection_service.sync_user_accessible_skills("user-1", {"demo": source})
    outside = tmp_path / "outside.md"
    outside.write_text("# same\n", encoding="utf-8")
    original_open = package_service.open_regular_file_fd

    def swap_before_open(*args, **kwargs):
        source_file.unlink()
        source_file.symlink_to(outside)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(package_service, "open_regular_file_fd", swap_before_open)
    with pytest.raises(PermissionError, match="symlink"):
        projection_service.sync_user_accessible_skills("user-1", {"demo": source})
    assert not (projection / "demo").exists()
    assert outside.read_text(encoding="utf-8") == "# same\n"


@pytest.mark.asyncio
async def test_skill_policy_change_removes_stale_projection_before_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """撤权提交时旧 Skill 必须先消失，再按新数据库授权恢复仍有权用户。"""
    for uid in ("user-1", "user-2"):
        target = tmp_path / "skill-projections" / uid / "reporter"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("# stale\n", encoding="utf-8")

    lifecycle: list[str] = []

    class Result:
        @staticmethod
        def scalars():
            return SimpleNamespace(all=lambda: ["user-1", "user-2"])

    class Db:
        async def commit(self):
            assert not (tmp_path / "skill-projections/user-1/reporter").exists()
            assert not (tmp_path / "skill-projections/user-2/reporter").exists()
            lifecycle.append("commit")

        async def execute(self, _statement, _parameters=None):
            return Result()

    async def refresh(uid: str):
        lifecycle.append(f"refresh:{uid}")
        return {}

    monkeypatch.setattr(projection_service, "refresh_user_skill_projection_async", refresh)

    await projection_service.commit_skill_policy_and_refresh_projections(Db(), "reporter")

    assert lifecycle == ["commit", "refresh:user-1", "refresh:user-2"]


def test_sync_user_accessible_skills_serializes_multiple_processes(tmp_path: Path):
    """一个进程持有 uid 文件锁时，另一进程的同步必须等待。"""
    source_dir = tmp_path / "sources" / "skill-a"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text("# skill-a\n", encoding="utf-8")
    sources = {"skill-a": str(source_dir)}

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _MULTIPROCESS_SKILL_SYNC_SCRIPT,
            str(tmp_path),
            "user-1",
            json.dumps(sources),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    projection = tmp_path / "skill-projections" / "user-1"
    assert sorted(path.name for path in projection.iterdir()) == sorted(sources)
    assert not list(projection.glob(".*.tmp-*"))


def test_sync_user_accessible_skills_rejects_special_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    slug = "personal-socket"
    source_dir = tmp_path / "s"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text("# personal\n", encoding="utf-8")
    projection = projection_service.sync_user_accessible_skills("user-1", {slug: source_dir})
    assert (projection / slug / "SKILL.md").is_file()

    unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    monkeypatch.chdir(source_dir)
    unix_socket.bind("stream.sock")
    try:
        with pytest.raises((OSError, ValueError)):
            projection_service.sync_user_accessible_skills("user-1", {slug: source_dir})
    finally:
        unix_socket.close()

    assert not (projection / slug).exists()


def test_personal_skill_root_is_inside_user_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """个人 Skill 的唯一持久路径必须位于对应用户的 UserWorkspace。"""
    from yuxi.modules.workspace import paths as sandbox_paths

    monkeypatch.setattr(sandbox_paths, "get_user_data_dir", lambda: tmp_path / "user-data")

    root = user_workspace_dir("user-1") / "agents" / "skills"

    assert root == tmp_path / "user-data/shared/user-1/workspace/agents/skills"


@pytest.mark.parametrize("component", ["workspace", "agents", "skills"])
def test_personal_skill_root_rejects_symlinked_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
):
    """个人 Skill 根不得通过可写路径组件越过当前 UserWorkspace。"""
    from yuxi.modules.workspace import paths as sandbox_paths

    user_data = tmp_path / "user-data"
    user_root = user_data / "shared/user-1"
    outside = tmp_path / "outside"
    outside.mkdir()
    components = ["workspace", "agents", "skills"]
    index = components.index(component)
    parent = user_root.joinpath(*components[:index])
    parent.mkdir(parents=True)
    (parent / component).symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(sandbox_paths, "get_user_data_dir", lambda: user_data)

    with pytest.raises(ValueError, match="路径"):
        personal_service._scan_personal_skills("user-1")
    if component == "agents":
        assert not (outside / "skills").exists()


@pytest.mark.asyncio
async def test_read_personal_skill_file_rejects_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """个人 Skill 文本读取不得跟随链接离开 Skill 目录。"""
    root = _personal_skill_root(tmp_path, monkeypatch)
    skill_dir = _write_personal_skill(root, "demo", "personal")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (skill_dir / "leak.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="路径非法"):
        await personal_service.read_personal_skill_file("user-1", "demo", "leak.txt")


@pytest.mark.parametrize("with_file", [False, True], ids=["empty-directory", "existing-content"])
def test_install_personal_skill_preserves_concurrent_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_file):
    """安装提交遇到并发同名目录时必须保留用户文件并失败。"""
    root = _personal_skill_root(tmp_path, monkeypatch)
    source = _write_personal_skill(tmp_path / "source", "demo", "personal")
    original_exists = Path.exists
    injected = False

    def exists_with_concurrent_target(path):
        nonlocal injected
        existed = original_exists(path)
        if path == root / "demo" and not existed and not injected:
            injected = True
            path.mkdir()
            if with_file:
                (path / "user-file.txt").write_text("keep", encoding="utf-8")
        return existed

    monkeypatch.setattr(Path, "exists", exists_with_concurrent_target)

    with pytest.raises(ValueError, match="已存在同名 Skill"):
        personal_service._install_personal_skill_dir_sync("user-1", source)

    if with_file:
        assert (root / "demo/user-file.txt").read_text(encoding="utf-8") == "keep"
    else:
        assert list((root / "demo").iterdir()) == []
    assert not list(root.glob(".install.tmp-*"))


def test_sync_user_accessible_skills_updates_executable_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    slug = "personal-script"
    source_dir = tmp_path / "sources" / slug
    source_dir.mkdir(parents=True)
    script = source_dir / "run.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o644)

    projection = projection_service.sync_user_accessible_skills("user-1", {slug: source_dir})
    projected_script = projection / slug / "run.sh"
    assert projected_script.stat().st_mode & 0o111 == 0

    script.chmod(0o755)
    projection_service.sync_user_accessible_skills("user-1", {slug: source_dir})

    assert projected_script.stat().st_mode & 0o111 == 0o111


@pytest.mark.asyncio
async def test_refresh_user_skill_projection_serializes_authorization_snapshots(monkeypatch: pytest.MonkeyPatch):
    """旧 Run 不得在较新的撤权同步完成后复活已撤销 Skill。"""
    import yuxi.modules.identity.repositories.users as user_repository
    import yuxi.infrastructure.postgres.manager as postgres_manager

    advisory_lock = asyncio.Lock()
    first_sync_started = asyncio.Event()
    allow_first_sync = asyncio.Event()
    current_items = [SimpleNamespace(slug="legacy", source_dir=Path("/tmp/legacy"), enabled=True)]
    synchronized_sources: list[dict[str, str]] = []

    class FakeDb:
        async def execute(self, _statement, parameters):
            assert parameters["lock_scope"].endswith("user-1")
            await advisory_lock.acquire()

    class FakeSessionContext:
        async def __aenter__(self):
            return FakeDb()

        async def __aexit__(self, *_args):
            advisory_lock.release()

    async def get_user(_self, _db, uid):
        assert uid == "user-1"
        return SimpleNamespace(is_deleted=0)

    async def list_shared(_db, _user):
        return list(current_items)

    async def to_thread(_func, _uid, sources):
        if not synchronized_sources:
            first_sync_started.set()
            await allow_first_sync.wait()
        synchronized_sources.append(dict(sources))

    monkeypatch.setattr(
        postgres_manager.pg_manager,
        "get_async_session_context",
        lambda: FakeSessionContext(),
    )
    monkeypatch.setattr(user_repository.UserRepository, "get_by_uid_with_db", get_user)
    monkeypatch.setattr(projection_service, "lock_accessible_shared_skills_for_projection", list_shared)
    monkeypatch.setattr(projection_service, "_resolve_shared_skill_dir", lambda item: item.source_dir)
    monkeypatch.setattr(svc.asyncio, "to_thread", to_thread)

    old_run = asyncio.create_task(projection_service.refresh_user_skill_projection_async("user-1"))
    await first_sync_started.wait()
    current_items.clear()
    new_run = asyncio.create_task(projection_service.refresh_user_skill_projection_async("user-1"))
    allow_first_sync.set()

    await asyncio.gather(old_run, new_run)

    assert synchronized_sources == [{"legacy": "/tmp/legacy"}, {}]


@pytest.mark.asyncio
async def test_refresh_user_skill_projection_excludes_personal_skills(monkeypatch: pytest.MonkeyPatch):
    """共享只读投影不得复制 UserWorkspace 中的个人 Skill。"""
    import yuxi.modules.identity.repositories.users as user_repository
    import yuxi.infrastructure.postgres.manager as postgres_manager

    synchronized_sources: list[dict[str, str]] = []
    shared = SimpleNamespace(slug="shared", enabled=True)

    class FakeDb:
        async def execute(self, _statement, _parameters):
            return None

    class FakeSessionContext:
        async def __aenter__(self):
            return FakeDb()

        async def __aexit__(self, *_args):
            return None

    async def get_user(_self, _db, _uid):
        return SimpleNamespace(is_deleted=0)

    async def list_shared(_db, _user):
        return [shared]

    async def fail_combined_list(*_args, **_kwargs):
        raise AssertionError("共享投影不得扫描或合并个人 Skill")

    async def to_thread(func, uid, sources):
        assert func is projection_service.sync_user_accessible_skills
        assert uid == "user-1"
        synchronized_sources.append(dict(sources))

    monkeypatch.setattr(postgres_manager.pg_manager, "get_async_session_context", lambda: FakeSessionContext())
    monkeypatch.setattr(user_repository.UserRepository, "get_by_uid_with_db", get_user)
    monkeypatch.setattr(projection_service, "lock_accessible_shared_skills_for_projection", list_shared)
    monkeypatch.setattr(catalog, "list_accessible_skills", fail_combined_list)
    monkeypatch.setattr(projection_service, "_resolve_shared_skill_dir", lambda item: Path(f"/tmp/{item.slug}"))
    monkeypatch.setattr(projection_service.asyncio, "to_thread", to_thread)

    sources = await projection_service.refresh_user_skill_projection_async("user-1")

    assert sources == {"shared": "/tmp/shared"}
    assert synchronized_sources == [{"shared": "/tmp/shared"}]


def test_shared_skill_path_blocks_traversal():
    with pytest.raises(ValueError, match="非法 Skill 文件路径"):
        edit_service._skill_path_parts("../outside.txt")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "files", "existing_slugs", "draft_slug", "draft_name", "installed_slug", "installed_name", "content", "error"),
    [
        (
            "demo.zip",
            {
                "demo/SKILL.md": "---\nname: demo\ndescription: this is demo\n---\n# Demo\n",
                "demo/prompts/system.md": "You are demo skill",
            },
            {"demo"},
            "demo",
            None,
            "demo-v2",
            "demo-v2",
            "name: demo-v2",
            None,
        ),
        (
            "Bad--Archive-Name.zip",
            {
                "Bad--Archive-Name/SKILL.md": "---\nname: valid-skill\ndescription: this is valid\n---\n# Valid\n",
                "Bad--Archive-Name/prompts/system.md": "Use valid skill metadata.",
            },
            set(),
            "valid-skill",
            None,
            "valid-skill",
            "valid-skill",
            None,
            None,
        ),
        (
            "valid-archive.zip",
            {"valid-archive/SKILL.md": "---\nname: 展示名\nslug: invalid--skill\ndescription: invalid slug\n---\n# Invalid\n"},
            set(),
            None,
            None,
            None,
            None,
            None,
            "SKILL.md frontmatter.slug 必须是小写字母/数字/短横线",
        ),
        (
            "NanjingHJLP.yi.zip",
            {"skill/SKILL.md": "---\nname: 周易智慧\ndescription: demo\n---\n# Demo\n"},
            {"zhouyizhihui"},
            "zhouyizhihui",
            "周易智慧",
            "zhouyizhihui-v2",
            "周易智慧",
            ("name: 周易智慧", "slug: zhouyizhihui-v2"),
            None,
        ),
        (
            "PRD to Prototype.zip",
            {"skill/SKILL.md": "---\nname: PRD to Prototype\ndescription: demo\n---\n# Demo\n"},
            set(),
            "prd-to-prototype",
            "PRD to Prototype",
            "prd-to-prototype",
            "PRD to Prototype",
            ("name: PRD to Prototype", "slug: prd-to-prototype"),
            None,
        ),
        (
            "Word Skill.zip",
            {
                "Word Skill/SKILL.md": (
                    "---\n"
                    "name: Word / DOCX\n"
                    "slug: word-docx\n"
                    "version: 1.0.2\n"
                    "homepage: https://clawic.com/skills/word-docx\n"
                    "description: Create, inspect, and edit Microsoft Word documents.\n"
                    "changelog: Tightened review workflows.\n"
                    'metadata: {"clawdbot":{"emoji":"📘","os":["linux","darwin","win32"]}}\n'
                    "---\n"
                    "# Word / DOCX\n"
                )
            },
            set(),
            "word-docx",
            "Word / DOCX",
            "word-docx",
            "Word / DOCX",
            None,
            None,
        ),
        (
            "Word Skill.zip",
            {
                "Word Skill/SKILL.md": (
                    "---\nname: Word / DOCX\nslug: word-docx\n"
                    "description: Create, inspect, and edit Microsoft Word documents.\n---\n# Word / DOCX\n"
                )
            },
            {"word-docx"},
            "word-docx",
            None,
            "word-docx-v2",
            "Word / DOCX",
            ("name: Word / DOCX", "slug: word-docx-v2"),
            None,
        ),
        (
            "SKILL.md",
            {"SKILL.md": "---\nname: demo\ndescription: this is demo\n---\n# Demo\n"},
            set(),
            "demo",
            None,
            "demo",
            "demo",
            "---\nname: demo\ndescription: this is demo\n---\n# Demo\n",
            None,
        ),
    ],
)
@pytest.mark.asyncio
async def test_skill_package_prepare_confirm_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename,
    files,
    existing_slugs,
    draft_slug,
    draft_name,
    installed_slug,
    installed_name,
    content,
    error,
):
    """归档和单文件安装共用流程，保留各自命名、碰撞、内容与拒绝契约。"""
    fake_repo = _fake_skill_repository(monkeypatch, existing_slugs=existing_slugs)
    if filename == "SKILL.md":
        file_bytes = files[filename].encode("utf-8")
    else:
        file_bytes = _build_zip(files)

    if error:
        with pytest.raises(ValueError, match=error):
            await draft_service.create_uploaded_skill_draft(filename=filename, file_bytes=file_bytes, operator=_user("root"))
        return

    draft = await draft_service.create_uploaded_skill_draft(
        filename=filename,
        file_bytes=file_bytes,
        operator=_user("root"),
    )
    assert draft["items"][0]["slug"] == draft_slug
    if draft_name is not None:
        assert draft["items"][0]["name"] == draft_name

    results = await svc.confirm_skill_install_draft(
        _UnitOfWork(),
        draft_id=draft["draft_id"],
        share_config=None,
        operator=_user("root"),
    )

    assert results[0]["success"] is True
    assert results[0]["slug"] == installed_slug
    assert fake_repo.created_item.slug == installed_slug
    assert fake_repo.created_item.name == installed_name
    skill_md = content_service.content_path(fake_repo.created_item).joinpath("SKILL.md").read_text(encoding="utf-8")
    if isinstance(content, tuple):
        for marker in content:
            assert marker in skill_md
    elif content is not None:
        assert skill_md == content if filename == "SKILL.md" else content in skill_md


def test_list_builtin_skill_specs_rejects_missing_skill_file(tmp_path, monkeypatch):
    """发现的目录必须包含 Skill 定义，不能静默漏装。"""
    (tmp_path / "incomplete").mkdir()
    monkeypatch.setattr(builtin_service, "BUILTIN_SKILLS_DIR", tmp_path)
    monkeypatch.setattr(builtin_service, "get_plugin_skill_directories", lambda: ())
    with pytest.raises(ValueError, match="内置 skill 缺少 SKILL.md"):
        builtin_service.list_builtin_skill_specs()


def test_list_builtin_skill_specs_rejects_mismatched_slug(tmp_path, monkeypatch):
    """目录名与声明标识不一致时拒绝加载。"""
    source = tmp_path / "expected"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: other\ndescription: test\n---\n")
    monkeypatch.setattr(builtin_service, "BUILTIN_SKILLS_DIR", tmp_path)
    monkeypatch.setattr(builtin_service, "get_plugin_skill_directories", lambda: ())
    with pytest.raises(ValueError, match="frontmatter.slug 必须等于 slug"):
        builtin_service.list_builtin_skill_specs()


@pytest.mark.asyncio
async def test_init_builtin_skills_create_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):

    source_dir = tmp_path / "builtin-skills" / "reporter"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: reporter\ndescription: SQL report from markdown\n---\n# SQL Reporter\n",
        encoding="utf-8",
    )
    (source_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (source_dir / "prompts" / "system.md").write_text("prompt", encoding="utf-8")

    monkeypatch.setattr(builtin_service, "BUILTIN_SKILLS_DIR", source_dir.parent)
    monkeypatch.setattr(builtin_service, "get_plugin_skill_directories", lambda: ())

    async def get_by_slug(self, slug: str):
        assert slug == "reporter"
        return None

    async def create(self, **kwargs) -> Skill:
        self.__class__.created_payload = kwargs
        return Skill(**kwargs, updated_by=kwargs["created_by"])

    FakeRepo = _fake_skill_repository(
        monkeypatch,
        target=builtin_service,
        methods={"get_by_slug": get_by_slug, "create": create},
    )
    FakeRepo.created_payload = None

    items = await builtin_service.init_builtin_skills(None)

    assert len(items) == 1
    assert items[0].slug == "reporter"
    assert FakeRepo.created_payload["source_type"] == "builtin"
    assert FakeRepo.created_payload["share_config"] == {
        "version": 2,
        "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
        "manage_scope": None,
    }
    assert FakeRepo.created_payload["enabled"] is True
    assert FakeRepo.created_payload["created_by"] == "system"
    assert (tmp_path / "skill-sources/shared" / "reporter" / "SKILL.md").exists()
    assert (tmp_path / "skill-sources/shared" / "reporter" / "prompts" / "system.md").read_text(encoding="utf-8") == "prompt"


@pytest.mark.asyncio
async def test_init_builtin_skills_updates_existing_record_and_preserves_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):

    source_dir = tmp_path / "builtin-skills" / "reporter"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: reporter\ndescription: new description\nversion: 1.0.1\n---\n# SQL Reporter\n",
        encoding="utf-8",
    )
    (source_dir / "prompt.md").write_text("new builtin content", encoding="utf-8")

    target_dir = tmp_path / "skill-sources/shared" / "reporter"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "prompt.md").write_text("old content", encoding="utf-8")

    monkeypatch.setattr(builtin_service, "BUILTIN_SKILLS_DIR", source_dir.parent)
    monkeypatch.setattr(builtin_service, "get_plugin_skill_directories", lambda: ())

    existing_item = Skill(
        slug="reporter",
        name="reporter",
        description="old description",
        dir_path="shared/reporter",
        source_type="builtin",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": None,
        },
        enabled=False,
        version="1.0.0",
        content_hash="old-hash",
        created_by="system",
        updated_by="system",
    )

    captured: dict[str, object] = {}

    async def get_by_slug(self, slug: str):
        assert slug == "reporter"
        return existing_item

    async def update_metadata(self, item: Skill, *, name: str, description: str, updated_by: str | None) -> Skill:
        item.name = name
        item.description = description
        captured["metadata"] = {"name": name, "description": description, "updated_by": updated_by}
        return item

    async def update_builtin_install(
        self,
        item: Skill,
        *,
        version: str,
        content_hash: str,
        updated_by: str | None,
    ) -> Skill:
        item.version = version
        item.content_hash = content_hash
        item.source_type = "builtin"
        item.share_config = builtin_service.BUILTIN_SKILL_SHARE_CONFIG.copy()
        item.updated_by = updated_by
        captured["install"] = {"version": version, "content_hash": content_hash, "updated_by": updated_by}
        return item

    _fake_skill_repository(
        monkeypatch,
        target=builtin_service,
        methods={
            "get_by_slug": get_by_slug,
            "update_metadata": update_metadata,
            "update_builtin_install": update_builtin_install,
        },
    )

    items = await builtin_service.init_builtin_skills(None, created_by="release-bot")

    assert len(items) == 1
    assert items[0].enabled is False
    assert items[0].version == "1.0.1"
    assert (target_dir / "prompt.md").read_text(encoding="utf-8") == "new builtin content"
    assert captured["metadata"] == {
        "name": "reporter",
        "description": "new description",
        "updated_by": "release-bot",
    }
    assert captured["install"]["updated_by"] == "release-bot"


@pytest.mark.asyncio
async def test_init_builtin_skills_rejects_non_builtin_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):

    source_dir = tmp_path / "builtin" / "reporter"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: reporter\ndescription: SQL report\n---\n# SQL Reporter\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        builtin_service,
        "list_builtin_skill_specs",
        lambda: [
            {
                "slug": "reporter",
                "name": "reporter",
                "description": "SQL report",
                "version": "1.0.0",
                "tool_dependencies": [],
                "mcp_dependencies": [],
                "skill_dependencies": [],
                "content_hash": "hash-v1",
                "source_dir": source_dir,
            }
        ],
    )

    async def get_by_slug(self, slug: str):
        return Skill(slug=slug, name=slug, description="uploaded", dir_path=f"shared/{slug}", source_type="upload")

    _fake_skill_repository(
        monkeypatch,
        target=builtin_service,
        methods={"get_by_slug": get_by_slug},
    )

    with pytest.raises(ValueError, match="非内置 skill 冲突"):
        await builtin_service.init_builtin_skills(None)


@pytest.mark.asyncio
async def test_update_skill_enabled_allows_builtin(monkeypatch: pytest.MonkeyPatch):
    builtin_item = Skill(
        slug="reporter",
        name="reporter",
        description="builtin",
        dir_path="shared/reporter",
        source_type="builtin",
        enabled=True,
    )

    async def fake_get_manageable_skill_or_raise(_db, user, slug: str):
        assert user.uid == "root"
        assert slug == "reporter"
        return builtin_item

    async def update_enabled(self, item: Skill, *, enabled: bool, updated_by: str | None):
        item.enabled = enabled
        item.updated_by = updated_by
        return item

    _fake_skill_repository(
        monkeypatch,
        target=svc,
        methods={"update_enabled": update_enabled},
    )

    monkeypatch.setattr(svc, "get_manageable_skill_or_raise", fake_get_manageable_skill_or_raise)
    monkeypatch.setattr(svc, "commit_skill_policy_and_refresh_projections", lambda *_args: asyncio.sleep(0))

    updated = await svc.update_skill_enabled(_UnitOfWork(), slug="reporter", enabled=False, operator=_user("root"))

    assert updated.enabled is False
    assert updated.updated_by == "root"


@pytest.mark.asyncio
async def test_skill_node_mutations_lock_skill_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    item = Skill(
        slug="demo",
        name="demo",
        description="demo",
        source_type="upload",
        dir_path="shared/demo",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
        },
    )
    locked_reads = []

    async def get_by_slug(self, slug, *, for_update=False):
        locked_reads.append((slug, for_update))
        return item

    _fake_skill_repository(
        monkeypatch,
        target=svc,
        methods={"get_by_slug": get_by_slug},
    )

    source = tmp_path / "skill-sources/shared/demo"
    source.mkdir(parents=True)
    source.joinpath("SKILL.md").write_text("---\nslug: demo\nname: demo\ndescription: demo\n---\n# demo\n")
    await edit_service.create_skill_node(
        _UnitOfWork(),
        slug="demo",
        relative_path="notes",
        is_dir=True,
        content=None,
        operator=_user("root", role="admin"),
    )
    note = content_service.content_path(item) / "notes" / "note.md"
    note.write_text("before", encoding="utf-8")
    await edit_service.delete_skill_node(_UnitOfWork(), slug="demo", relative_path="notes/note.md", operator=_user("root", role="admin"))

    assert locked_reads == [("demo", False), ("demo", True), ("demo", False), ("demo", True)]
    assert note.read_text() == "before"
    assert not content_service.content_path(item).joinpath("notes/note.md").exists()


@pytest.mark.asyncio
async def test_delete_skills_batch_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def invalidate(db, slug):
        return []

    monkeypatch.setattr(svc, "invalidate_skill_projections", invalidate)

    # 模拟两个已安装的技能
    (tmp_path / "skill-sources/shared" / "skill-a").mkdir(parents=True, exist_ok=True)
    (tmp_path / "skill-sources/shared" / "skill-b").mkdir(parents=True, exist_ok=True)

    share_config = {
        "version": 2,
        "read_scope": {"access_level": "user", "user_uids": ["root"]},
        "manage_scope": {"access_level": "user", "user_uids": ["root"]},
    }
    item_a = Skill(
        slug="skill-a",
        name="skill-a",
        description="a",
        dir_path="shared/skill-a",
        created_by="root",
        share_config=share_config,
    )
    item_b = Skill(
        slug="skill-b",
        name="skill-b",
        description="b",
        dir_path="shared/skill-b",
        created_by="root",
        share_config=share_config,
    )

    db_items = {"skill-a": item_a, "skill-b": item_b}
    deleted_slugs = []

    async def get_by_slug(self, slug: str, *, for_update: bool = False):
        return db_items.get(slug)

    async def delete(self, item: Skill):
        deleted_slugs.append(item.slug)
        db_items.pop(item.slug, None)

    _fake_skill_repository(
        monkeypatch,
        target=svc,
        methods={"get_by_slug": get_by_slug, "delete": delete},
    )

    # 执行批量删除，skill-a, skill-b, skill-c (不存在)
    results = await svc.delete_skills_batch(
        _UnitOfWork(),
        slugs=["skill-a", "skill-b", "skill-c"],
        operator=_user("root"),
    )

    assert results == [
        {"slug": "skill-a", "success": True},
        {"slug": "skill-b", "success": True},
        {"slug": "skill-c", "success": False, "error": "技能 'skill-c' 不存在"},
    ]
    assert deleted_slugs == ["skill-a", "skill-b"]
    assert not (tmp_path / "skill-sources/shared" / "skill-a").exists()
    assert not (tmp_path / "skill-sources/shared" / "skill-b").exists()


@pytest.mark.asyncio
async def test_delete_skills_batch_limit_exceeded():
    slugs = [f"skill-{i}" for i in range(51)]
    with pytest.raises(ValueError, match="批量删除的技能数量不能超过 50 个"):
        await svc.delete_skills_batch(_UnitOfWork(), slugs=slugs, operator=_user("root"))


@pytest.mark.asyncio
async def test_delete_skill_commits_database_before_removing_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def invalidate(db, slug):
        return []

    monkeypatch.setattr(svc, "invalidate_skill_projections", invalidate)
    (tmp_path / "skill-sources/shared" / "concurrent-skill").mkdir(parents=True, exist_ok=True)
    item = Skill(
        slug="concurrent-skill",
        name="concurrent-skill",
        description="desc",
        dir_path="shared/concurrent-skill",
        created_by="root",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["root"]},
            "manage_scope": {"access_level": "user", "user_uids": ["root"]},
        },
    )
    events: list[str] = []

    class UnitOfWork(_UnitOfWork):
        async def commit(self) -> None:
            events.append("commit")

    async def get_by_slug(self, slug: str, *, for_update: bool = False):
        assert slug == "concurrent-skill"
        assert for_update is True
        return item

    async def delete(self, deleted: Skill):
        events.append(f"delete:{deleted.slug}")

    _fake_skill_repository(
        monkeypatch,
        target=svc,
        methods={"get_by_slug": get_by_slug, "delete": delete},
    )

    await svc.delete_skill(UnitOfWork(), slug="concurrent-skill", operator=_user("root"))

    assert events == ["delete:concurrent-skill", "commit"]
    assert not (tmp_path / "skill-sources/shared" / "concurrent-skill").exists()


def _write_personal_skill(root: Path, slug: str, description: str) -> Path:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: {description}\n---\n# {slug}\n",
        encoding="utf-8",
    )
    return skill_dir


def _personal_skill_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uid: str = "user-1") -> Path:
    """把 UserWorkspace 根定向到测试临时目录。"""
    from yuxi.modules.workspace import paths as sandbox_paths

    user_data = tmp_path / "user-data"
    monkeypatch.setattr(sandbox_paths, "get_user_data_dir", lambda: user_data)
    return user_data / "shared" / uid / "workspace" / "agents" / "skills"


@pytest.mark.asyncio
async def test_personal_skill_list_reads_current_workspace_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _personal_skill_root(tmp_path, monkeypatch)
    _write_personal_skill(root, "demo", "first")

    first = await personal_service.list_personal_skills("user-1")
    _write_personal_skill(root, "demo", "changed")
    current = await personal_service.list_personal_skills("user-1")

    assert first[0].description == "first"
    assert current[0].description == "changed"


@pytest.mark.asyncio
async def test_personal_skill_overrides_shared_skill_and_drops_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    personal_root = _personal_skill_root(tmp_path, monkeypatch)
    _write_personal_skill(personal_root, "demo", "personal")
    shared = Skill(
        id=1,
        slug="demo",
        name="Shared Demo",
        description="shared",
        source_type="upload",
        dir_path="shared/demo",
        enabled=True,
        created_by="other",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "global"},
        },
    )

    _fake_skill_repository(
        monkeypatch,
        target=catalog,
        methods={"list_enabled": AsyncMock(return_value=[shared])},
    )

    items = await catalog.list_accessible_skills(None, _user("user-1", role="user"))

    assert len(items) == 1
    assert items[0].source_scope == "personal"
    assert items[0].description == "personal"
    assert items[0].overrides_shared is True
    assert items[0].share_config is None
    assert "share_config" not in items[0].to_dict()


@pytest.mark.asyncio
async def test_personal_skills_are_isolated_by_uid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    roots = {uid: _personal_skill_root(tmp_path, monkeypatch, uid) for uid in ("user-a", "user-b")}
    _write_personal_skill(roots["user-a"], "demo", "from a")
    _write_personal_skill(roots["user-b"], "demo", "from b")

    user_a = await personal_service.list_personal_skills("user-a")
    user_b = await personal_service.list_personal_skills("user-b")

    assert user_a[0].description == "from a"
    assert user_b[0].description == "from b"
    assert user_a[0].source_dir != user_b[0].source_dir


@pytest.mark.asyncio
async def test_skill_cards_keep_shadowed_shared_item_for_management(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    personal_root = _personal_skill_root(tmp_path, monkeypatch)
    _write_personal_skill(personal_root, "demo", "personal")
    shared = Skill(
        id=1,
        slug="demo",
        name="Shared Demo",
        description="shared",
        source_type="upload",
        dir_path="shared/demo",
        enabled=True,
        created_by="user-1",
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
    )

    _fake_skill_repository(
        monkeypatch,
        target=catalog,
        methods={"list_all": AsyncMock(return_value=[shared])},
    )

    cards = await catalog.list_skill_cards_for_user(None, _user("user-1", role="admin"))

    assert [(item.slug, item.source_scope) for item in cards] == [
        ("demo", "personal"),
        ("demo", "shared"),
    ]
    assert cards[0].overrides_shared is True
    assert cards[1].shadowed_by_personal is True


@pytest.mark.asyncio
async def test_confirm_personal_skill_draft_uses_package_slug_without_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    personal_root = _personal_skill_root(tmp_path, monkeypatch)
    draft_id = "11111111-1111-1111-1111-111111111111"
    draft_dir = tmp_path / "runtime/skill_import_drafts" / draft_id
    item_dir = draft_dir / "items" / ("a" * 32)
    item_dir.mkdir(parents=True)
    (item_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: personal demo\n---\n# Demo\n",
        encoding="utf-8",
    )
    (draft_dir / "metadata.json").write_text(
        json.dumps(
            {
                "created_by": "user-1",
                "source_type": "remote",
                "expires_at": time.time() + 300,
                "failures": [],
                "items": [
                    {
                        "slug": "demo",
                        "source_dir": f"items/{'a' * 32}",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("YUXI_RUNTIME_DIR", str(tmp_path / "runtime"))

    class ContentRepo:
        def __init__(self, db):
            pass

        async def list_enabled_readable(self, operator):
            return []

    async def no_prune(db, slug):
        pass

    async def no_mcps(db=None):
        return []

    monkeypatch.setattr(content_service, "SkillRepository", ContentRepo)
    monkeypatch.setattr(content_service, "prune_skill_content", no_prune)
    monkeypatch.setattr("yuxi.modules.extensions.mcp.service.get_enabled_mcp_server_slugs", no_mcps)

    results = await personal_service.confirm_personal_skill_install_draft(
        draft_id=draft_id,
        slugs=["demo"],
        operator=_user("user-1", role="user"),
    )

    assert results[0]["success"] is True
    assert results[0]["slug"] == "demo"
    assert results[0]["requested_slug"] == "demo"
    assert (personal_root / "demo" / "SKILL.md").exists()
    assert not draft_dir.exists()


def test_resolved_shared_skill_captures_original_version_and_hash(monkeypatch, tmp_path):
    """数据库行后续改变不能改写首次适配得到的 Skill 元数据。"""
    row = Skill(
        id=1,
        slug="versioned",
        name="Versioned",
        description="skill",
        source_type="local",
        enabled=True,
        created_by="user",
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
        version="v1",
        content_hash="hash-v1",
    )
    monkeypatch.setattr(skill_access, "_resolve_skill_dir", lambda item: tmp_path)
    resolved = skill_access.resolved_shared_skill(row)
    row.version, row.content_hash = "v2", "hash-v2"
    assert resolved.version == "v1"
    assert resolved.content_hash == "hash-v1"
    assert resolved.source_scope == "shared"


def test_builtin_hash_uses_canonical_tree_and_executable_mode(tmp_path, monkeypatch):
    """内置和安装包比较相同目录哈希，脚本可执行位参与修订。"""
    from yuxi.modules.extensions.skills.package import compute_skill_directory_hash

    source = tmp_path / "demo"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: demo\ndescription: test\n---\n")
    script = source / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o644)
    monkeypatch.setattr(builtin_service, "BUILTIN_SKILLS_DIR", tmp_path)
    monkeypatch.setattr(builtin_service, "get_plugin_skill_directories", lambda: ())
    first = builtin_service.list_builtin_skill_specs()[0]["content_hash"]
    assert first == compute_skill_directory_hash(source).hex()
    script.chmod(0o755)
    second = builtin_service.list_builtin_skill_specs()[0]["content_hash"]
    assert second == compute_skill_directory_hash(source).hex()
    assert second != first


def test_builtin_skill_discovery_rejects_duplicate_plugin_slug(tmp_path, monkeypatch):
    """插件与共享来源不能覆盖同一持久 Skill 身份。"""
    shared = tmp_path / "shared"
    bundled = tmp_path / "plugin" / "skills"
    for root in (shared, bundled):
        source = root / "duplicate"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: duplicate\ndescription: test\n---\n")
    monkeypatch.setattr(builtin_service, "BUILTIN_SKILLS_DIR", shared)
    monkeypatch.setattr(builtin_service, "get_plugin_skill_directories", lambda: (bundled,))
    with pytest.raises(ValueError, match="内置 skill slug 重复: duplicate"):
        builtin_service.list_builtin_skill_specs()
