from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.agents.skills import service as svc
from yuxi.agents.toolkits import service as tool_service
from yuxi.storage.postgres.models_business import Skill, User


def _build_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _user(uid: str = "root", role: str = "admin") -> User:
    return User(username=uid, uid=uid, password_hash="x", role=role, department_id=1)


def test_allowed_skill_access_levels_by_role():
    assert svc.get_allowed_skill_access_levels(_user(role="user")) == ["user"]
    assert svc.get_allowed_skill_access_levels(_user(role="admin")) == ["global", "department", "user"]
    assert svc.get_allowed_skill_access_levels(_user(role="superadmin")) == ["global", "department", "user"]


@pytest.mark.asyncio
async def test_list_visible_skills_for_management_includes_owned_disabled_and_enabled_shared(
    monkeypatch: pytest.MonkeyPatch,
):
    items = [
        Skill(slug="owned-disabled", name="owned-disabled", description="", created_by="root", enabled=False),
        Skill(
            slug="shared-enabled",
            name="shared-enabled",
            description="",
            created_by="other",
            enabled=True,
            share_config={"access_level": "user", "department_ids": [], "user_uids": ["root"]},
        ),
        Skill(
            slug="shared-disabled",
            name="shared-disabled",
            description="",
            created_by="other",
            enabled=False,
            share_config={"access_level": "user", "department_ids": [], "user_uids": ["root"]},
        ),
        Skill(slug="unrelated", name="unrelated", description="", created_by="other", enabled=True),
    ]

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return items

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    visible = await svc.list_visible_skills_for_management(None, _user("root", role="user"))

    assert [item.slug for item in visible] == ["owned-disabled", "shared-enabled"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill,operator",
    [
        (
            Skill(slug="owned-disabled", name="owned-disabled", description="", created_by="root", enabled=False),
            _user("root", role="user"),
        ),
        (
            Skill(slug="admin-disabled", name="admin-disabled", description="", created_by="other", enabled=False),
            _user("root", role="admin"),
        ),
        (
            Skill(
                slug="shared-enabled",
                name="shared-enabled",
                description="",
                created_by="other",
                enabled=True,
                share_config={"access_level": "user", "department_ids": [], "user_uids": ["root"]},
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
    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            assert slug == skill.slug
            return skill

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    result = await svc.get_management_readable_skill_or_raise(None, operator, skill.slug)

    assert result is skill


@pytest.mark.asyncio
async def test_management_readable_skill_rejects_disabled_shared_readonly(monkeypatch: pytest.MonkeyPatch):
    skill = Skill(
        slug="shared-disabled",
        name="shared-disabled",
        description="",
        created_by="other",
        enabled=False,
        share_config={"access_level": "user", "department_ids": [], "user_uids": ["root"]},
    )

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            assert slug == skill.slug
            return skill

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    with pytest.raises(ValueError, match="不存在或无权访问"):
        await svc.get_management_readable_skill_or_raise(None, _user("root", role="user"), skill.slug)


@pytest.mark.asyncio
async def test_runtime_access_still_excludes_disabled_shared_skill(monkeypatch: pytest.MonkeyPatch):
    skill = Skill(
        slug="shared-disabled",
        name="shared-disabled",
        description="",
        created_by="other",
        enabled=False,
        share_config={"access_level": "user", "department_ids": [], "user_uids": ["root"]},
    )

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_enabled(self):
            return []

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    assert svc.user_can_access_skill(_user("root", role="user"), skill) is False
    assert await svc.list_accessible_skills(None, _user("root", role="user")) == []


@pytest.mark.asyncio
async def test_normal_user_skill_upload_draft_defaults_to_user_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def exists_slug(self, _slug: str) -> bool:
            return False

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    draft = await svc.prepare_skill_upload(
        None,
        filename="SKILL.md",
        file_bytes=b"---\nname: demo\ndescription: demo skill\n---\n# Demo\n",
        operator=_user("normal-user", role="user"),
    )

    assert draft["default_share_config"] == {
        "access_level": "user",
        "department_ids": [],
        "user_uids": ["normal-user"],
    }
    assert draft["allowed_access_levels"] == ["user"]


@pytest.mark.parametrize(
    "share_config",
    [
        {"access_level": "global", "department_ids": [], "user_uids": []},
        {"access_level": "department", "department_ids": [1], "user_uids": []},
    ],
)
@pytest.mark.asyncio
async def test_normal_user_confirm_skill_draft_rejects_wider_share_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    share_config: dict,
):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def exists_slug(self, _slug: str) -> bool:
            return False

        async def create(self, **_kwargs) -> Skill:
            raise AssertionError("普通用户的越权共享范围应在创建前被拒绝")

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)
    operator = _user("normal-user", role="user")
    draft = await svc.prepare_skill_upload(
        None,
        filename="SKILL.md",
        file_bytes=b"---\nname: demo\ndescription: demo skill\n---\n# Demo\n",
        operator=operator,
    )

    with pytest.raises(ValueError, match="无权使用该 Skill 共享范围"):
        await svc.confirm_skill_install_draft(
            None,
            draft_id=draft["draft_id"],
            share_config=share_config,
            operator=operator,
        )


def test_parse_skill_markdown_ok():
    content = "---\nname: demo-skill\ndescription: demo description\n---\n# Demo\n"
    slug, name, desc, meta = svc._parse_skill_markdown(content)
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
    slug, name, desc, meta = svc._parse_skill_markdown(content)
    assert slug == "word-docx"
    assert name == "Word / DOCX"
    assert desc == "Create, inspect, and edit Microsoft Word documents."
    assert meta["version"] == "1.0.2"


def test_parse_skill_markdown_requires_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        svc._parse_skill_markdown("# missing")


def test_image_gen_builtin_skill_spec():
    specs = {spec["slug"]: spec for spec in svc.list_builtin_skill_specs()}

    assert "image-gen" in specs
    image_gen = specs["image-gen"]
    assert image_gen["name"] == "image-gen"
    assert image_gen["tool_dependencies"] == ["present_artifacts"]
    assert (image_gen["source_dir"] / "SKILL.md").exists()


def test_html_preview_builtin_skill_spec():
    specs = {spec["slug"]: spec for spec in svc.list_builtin_skill_specs()}

    assert "html-preview" in specs
    html_preview = specs["html-preview"]
    assert html_preview["name"] == "html-preview"
    assert html_preview["tool_dependencies"] == []
    assert html_preview["mcp_dependencies"] == []

    content = (html_preview["source_dir"] / "SKILL.md").read_text(encoding="utf-8")
    assert "```html:preview" in content
    assert "\n    ```html:preview" not in content
    assert "最多只能有 3 个前导空格" in content
    assert "普通 `html` 代码块" in content


def test_deep_research_builtin_skill_includes_html_preview_dependency():
    specs = {spec["slug"]: spec for spec in svc.list_builtin_skill_specs()}

    assert specs["deep-research"]["skill_dependencies"] == ["html-preview"]


def test_knowledge_base_builtin_skill_spec():
    specs = {spec["slug"]: spec for spec in svc.list_builtin_skill_specs()}

    assert "knowledge-base" in specs
    knowledge_base = specs["knowledge-base"]
    assert knowledge_base["name"] == "knowledge-base"
    assert knowledge_base["tool_dependencies"] == [
        "list_kbs",
        "query_kb",
        "find_kb_document",
        "open_kb_document",
        "get_mindmap",
        "search_file",
        "download_kb_file",
    ]
    assert (knowledge_base["source_dir"] / "SKILL.md").exists()


def test_mysql_reporter_builtin_skill_spec_replaces_reporter_and_deep_reporter():
    specs = {spec["slug"]: spec for spec in svc.list_builtin_skill_specs()}

    assert "reporter" not in specs
    assert "deep-reporter" not in specs
    assert "mysql-reporter" in specs
    mysql_reporter = specs["mysql-reporter"]
    assert mysql_reporter["name"] == "mysql reporter"
    assert mysql_reporter["tool_dependencies"] == []
    assert mysql_reporter["mcp_dependencies"] == ["mcp-server-chart"]
    assert (mysql_reporter["source_dir"] / "SKILL.md").exists()
    for script_name in ("list_tables.py", "describe_table.py", "query.py"):
        script_path = mysql_reporter["source_dir"] / "scripts" / script_name
        assert script_path.exists()
        assert script_path.read_text(encoding="utf-8").startswith("# /// script")


def test_is_valid_skill_slug():
    # Test valid slugs
    assert svc.is_valid_skill_slug("demo-skill") is True
    assert svc.is_valid_skill_slug("valid-name-123") is True
    # Test invalid slugs
    assert svc.is_valid_skill_slug("../bad") is False
    assert svc.is_valid_skill_slug("Invalid") is False  # uppercase not allowed
    assert svc.is_valid_skill_slug("") is False


def test_sync_thread_readable_skills_none_keeps_no_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))
    skills_root = tmp_path / "skills"
    (skills_root / "alpha").mkdir(parents=True, exist_ok=True)
    (skills_root / "alpha" / "SKILL.md").write_text("alpha", encoding="utf-8")

    thread_root = svc.sync_thread_readable_skills("thread_1", None)

    assert list(thread_root.iterdir()) == []


def test_sync_thread_readable_skills_only_keeps_selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))
    skills_root = tmp_path / "skills"
    (skills_root / "alpha").mkdir(parents=True, exist_ok=True)
    (skills_root / "alpha" / "SKILL.md").write_text("alpha", encoding="utf-8")
    (skills_root / "beta").mkdir(parents=True, exist_ok=True)
    (skills_root / "beta" / "SKILL.md").write_text("beta", encoding="utf-8")

    thread_root = svc.sync_thread_readable_skills("thread_1", ["alpha", "missing", "alpha"])

    assert thread_root == tmp_path / "threads" / "thread_1" / "skills"
    assert sorted(path.name for path in thread_root.iterdir()) == ["alpha"]
    assert (thread_root / "alpha").is_dir()
    assert not (thread_root / "alpha").is_symlink()
    assert (thread_root / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "alpha"

    svc.sync_thread_readable_skills("thread_1", ["beta"])
    assert sorted(path.name for path in thread_root.iterdir()) == ["beta"]
    assert (thread_root / "beta" / "SKILL.md").read_text(encoding="utf-8") == "beta"


@pytest.mark.asyncio
async def test_get_skill_dependency_options(monkeypatch: pytest.MonkeyPatch):
    # Mock get_tool_metadata to return tool list
    def fake_get_tool_metadata(category=None):
        return [
            {"slug": "calculator", "name": "Calculator"},
            {"slug": "search", "name": "Search"},
        ]

    monkeypatch.setattr(tool_service, "get_tool_metadata", fake_get_tool_metadata)

    async def fake_get_enabled_mcp_server_slugs(db=None):
        del db
        return ["mcp-a", "mcp-b"]

    monkeypatch.setattr(svc, "get_enabled_mcp_server_slugs", fake_get_enabled_mcp_server_slugs)

    user = SimpleNamespace(uid="user")

    async def fake_list_skill_slugs(_db, *, user):
        assert user.uid == "user"
        return ["alpha", "beta"]

    monkeypatch.setattr(svc, "list_skill_slugs", fake_list_skill_slugs)

    result = await svc.get_skill_dependency_options(None, user)
    assert result["tools"] == [{"slug": "calculator", "name": "Calculator"}, {"slug": "search", "name": "Search"}]
    assert result["mcps"] == ["mcp-a", "mcp-b"]
    assert result["skills"] == ["alpha", "beta"]


def test_resolve_relative_path_blocks_traversal(tmp_path: Path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="上级路径"):
        svc._resolve_relative_path(skill_dir, "../outside.txt")


@pytest.mark.asyncio
async def test_skill_upload_prepare_confirm_rewrites_conflicting_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    class FakeRepo:
        existing_slugs = {"demo"}
        created_item: Skill | None = None

        def __init__(self, _db):
            pass

        async def exists_slug(self, slug: str) -> bool:
            return slug in self.__class__.existing_slugs

        async def create(self, **kwargs) -> Skill:
            item = Skill(**kwargs, updated_by=kwargs["created_by"])
            self.__class__.existing_slugs.add(item.slug)
            self.__class__.created_item = item
            return item

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    zip_bytes = _build_zip(
        {
            "demo/SKILL.md": ("---\nname: demo\ndescription: this is demo\n---\n# Demo\n"),
            "demo/prompts/system.md": "You are demo skill",
        }
    )
    operator = _user("root")

    draft = await svc.prepare_skill_upload(
        None,
        filename="demo.zip",
        file_bytes=zip_bytes,
        operator=operator,
    )
    results = await svc.confirm_skill_install_draft(
        None,
        draft_id=draft["draft_id"],
        share_config=draft["default_share_config"],
        operator=operator,
    )

    assert results[0]["slug"] == "demo-v2"
    assert results[0]["success"] is True
    assert FakeRepo.created_item.slug == "demo-v2"
    skill_md = (tmp_path / "skills" / "demo-v2" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: demo-v2" in skill_md


@pytest.mark.asyncio
async def test_skill_zip_import_uses_skill_md_name_not_zip_or_root_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    class FakeRepo:
        created_item: Skill | None = None

        def __init__(self, _db):
            pass

        async def exists_slug(self, _slug: str) -> bool:
            return False

        async def create(self, **kwargs) -> Skill:
            item = Skill(**kwargs, updated_by=kwargs["created_by"])
            self.__class__.created_item = item
            return item

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    zip_bytes = _build_zip(
        {
            "Bad--Archive-Name/SKILL.md": ("---\nname: valid-skill\ndescription: this is valid\n---\n# Valid\n"),
            "Bad--Archive-Name/prompts/system.md": "Use valid skill metadata.",
        }
    )
    operator = _user("root")

    draft = await svc.prepare_skill_upload(
        None,
        filename="Bad--Archive-Name.zip",
        file_bytes=zip_bytes,
        operator=operator,
    )
    results = await svc.confirm_skill_install_draft(
        None,
        draft_id=draft["draft_id"],
        share_config=draft["default_share_config"],
        operator=operator,
    )

    assert draft["items"][0]["original_name"] == "valid-skill"
    assert draft["items"][0]["slug"] == "valid-skill"
    assert results[0]["success"] is True
    assert results[0]["slug"] == "valid-skill"
    assert FakeRepo.created_item.slug == "valid-skill"
    assert (tmp_path / "skills" / "valid-skill" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_skill_zip_import_validates_skill_md_name_not_zip_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def exists_slug(self, _slug: str) -> bool:
            return False

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    zip_bytes = _build_zip(
        {
            "valid-archive/SKILL.md": ("---\nname: invalid--skill\ndescription: invalid name\n---\n# Invalid\n"),
        }
    )

    with pytest.raises(ValueError, match="SKILL.md frontmatter.name 必须是小写字母/数字/短横线"):
        await svc.prepare_skill_upload(
            None,
            filename="valid-archive.zip",
            file_bytes=zip_bytes,
            operator=_user("root"),
        )


@pytest.mark.asyncio
async def test_skill_zip_import_uses_frontmatter_slug_and_keeps_display_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    class FakeRepo:
        created_item: Skill | None = None

        def __init__(self, _db):
            pass

        async def exists_slug(self, _slug: str) -> bool:
            return False

        async def create(self, **kwargs) -> Skill:
            item = Skill(**kwargs, updated_by=kwargs["created_by"])
            self.__class__.created_item = item
            return item

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    zip_bytes = _build_zip(
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
        }
    )
    operator = _user("root")

    draft = await svc.prepare_skill_upload(
        None,
        filename="Word Skill.zip",
        file_bytes=zip_bytes,
        operator=operator,
    )
    results = await svc.confirm_skill_install_draft(
        None,
        draft_id=draft["draft_id"],
        share_config=draft["default_share_config"],
        operator=operator,
    )

    assert draft["items"][0]["slug"] == "word-docx"
    assert draft["items"][0]["name"] == "Word / DOCX"
    assert results[0]["success"] is True
    assert results[0]["slug"] == "word-docx"
    assert FakeRepo.created_item.slug == "word-docx"
    assert FakeRepo.created_item.name == "Word / DOCX"


@pytest.mark.asyncio
async def test_skill_zip_import_rewrites_conflicting_slug_not_display_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    class FakeRepo:
        existing_slugs = {"word-docx"}
        created_item: Skill | None = None

        def __init__(self, _db):
            pass

        async def exists_slug(self, slug: str) -> bool:
            return slug in self.__class__.existing_slugs

        async def create(self, **kwargs) -> Skill:
            item = Skill(**kwargs, updated_by=kwargs["created_by"])
            self.__class__.existing_slugs.add(item.slug)
            self.__class__.created_item = item
            return item

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    zip_bytes = _build_zip(
        {
            "Word Skill/SKILL.md": (
                "---\n"
                "name: Word / DOCX\n"
                "slug: word-docx\n"
                "description: Create, inspect, and edit Microsoft Word documents.\n"
                "---\n"
                "# Word / DOCX\n"
            )
        }
    )
    operator = _user("root")

    draft = await svc.prepare_skill_upload(
        None,
        filename="Word Skill.zip",
        file_bytes=zip_bytes,
        operator=operator,
    )
    results = await svc.confirm_skill_install_draft(
        None,
        draft_id=draft["draft_id"],
        share_config=draft["default_share_config"],
        operator=operator,
    )

    assert results[0]["slug"] == "word-docx-v2"
    assert results[0]["success"] is True
    assert FakeRepo.created_item.name == "Word / DOCX"
    skill_md = (tmp_path / "skills" / "word-docx-v2" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: Word / DOCX" in skill_md
    assert "slug: word-docx-v2" in skill_md


@pytest.mark.asyncio
async def test_skill_md_prepare_confirm_creates_single_file_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    class FakeRepo:
        created_item: Skill | None = None

        def __init__(self, _db):
            pass

        async def exists_slug(self, slug: str) -> bool:
            return False

        async def create(self, **kwargs) -> Skill:
            item = Skill(**kwargs, updated_by=kwargs["created_by"])
            self.__class__.created_item = item
            return item

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    skill_md = "---\nname: demo\ndescription: this is demo\n---\n# Demo\n"
    operator = _user("root")
    draft = await svc.prepare_skill_upload(
        None,
        filename="SKILL.md",
        file_bytes=skill_md.encode("utf-8"),
        operator=operator,
    )
    results = await svc.confirm_skill_install_draft(
        None,
        draft_id=draft["draft_id"],
        share_config=draft["default_share_config"],
        operator=operator,
    )

    assert results[0]["slug"] == "demo"
    assert results[0]["success"] is True
    assert FakeRepo.created_item.name == "demo"
    assert (tmp_path / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8") == skill_md


@pytest.mark.asyncio
async def test_import_skill_dir_requires_root_skill_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))
    source_dir = tmp_path / "source-skill"
    source_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="根级 SKILL.md"):
        await svc.import_skill_dir(
            None,
            source_dir=source_dir,
            created_by="root",
        )


@pytest.mark.asyncio
async def test_update_skill_md_syncs_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: old\n---\n# old\n",
        encoding="utf-8",
    )

    item = Skill(
        slug="demo",
        name="demo",
        description="old",
        dir_path="skills/demo",
        created_by="root",
        updated_by="root",
    )

    async def fake_get_skill_or_raise(_db, _slug: str):
        return item

    updates: dict[str, str | None] = {}

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def update_metadata(
            self,
            _item: Skill,
            *,
            name: str,
            description: str,
            updated_by: str | None,
        ) -> Skill:
            updates["name"] = name
            updates["description"] = description
            updates["updated_by"] = updated_by
            return item

    monkeypatch.setattr(svc, "get_skill_or_raise", fake_get_skill_or_raise)
    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    new_content = "---\nname: demo\ndescription: updated desc\n---\n# updated\n"
    await svc.update_skill_file(
        None,
        slug="demo",
        relative_path="SKILL.md",
        content=new_content,
        updated_by="admin",
    )

    assert updates["name"] == "demo"
    assert updates["description"] == "updated desc"
    assert updates["updated_by"] == "admin"
    saved_content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "description: updated desc" in saved_content


@pytest.mark.asyncio
async def test_update_skill_dependencies(monkeypatch: pytest.MonkeyPatch):
    item = Skill(
        slug="alpha",
        name="alpha",
        description="alpha",
        source_type="upload",
        dir_path="skills/alpha",
        share_config={"access_level": "user", "department_ids": [], "user_uids": ["root"]},
        enabled=True,
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
    )
    dependency = Skill(
        slug="beta",
        name="beta",
        description="beta",
        source_type="upload",
        dir_path="skills/beta",
        share_config={"access_level": "user", "department_ids": [], "user_uids": ["root"]},
        enabled=True,
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
    )

    # Mock get_tool_metadata to return tool list
    def fake_get_tool_metadata(category=None):
        return [{"slug": "calculator", "name": "Calculator"}]

    monkeypatch.setattr(tool_service, "get_tool_metadata", fake_get_tool_metadata)

    async def fake_get_enabled_mcp_server_slugs(db=None):
        del db
        return ["mcp-a"]

    monkeypatch.setattr(svc, "get_enabled_mcp_server_slugs", fake_get_enabled_mcp_server_slugs)

    async def fake_get_skill_or_raise(_db, slug: str):
        assert slug == "alpha"
        return item

    captured: dict[str, list[str] | str | None] = {}

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return [item, dependency]

        async def update_dependencies(
            self,
            _item: Skill,
            *,
            tool_dependencies: list[str],
            mcp_dependencies: list[str],
            skill_dependencies: list[str],
            updated_by: str | None,
        ):
            captured["tool_dependencies"] = tool_dependencies
            captured["mcp_dependencies"] = mcp_dependencies
            captured["skill_dependencies"] = skill_dependencies
            captured["updated_by"] = updated_by
            _item.tool_dependencies = tool_dependencies
            _item.mcp_dependencies = mcp_dependencies
            _item.skill_dependencies = skill_dependencies
            return _item

    async def fake_list_accessible_skills(_db, _operator):
        return [item, dependency]

    monkeypatch.setattr(svc, "get_skill_or_raise", fake_get_skill_or_raise)
    monkeypatch.setattr(svc, "list_accessible_skills", fake_list_accessible_skills)
    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    updated = await svc.update_skill_dependencies(
        None,
        slug="alpha",
        tool_dependencies=["calculator", "calculator"],
        mcp_dependencies=["mcp-a", "mcp-a"],
        skill_dependencies=["beta", "beta"],
        operator=_user("root"),
    )
    assert captured["tool_dependencies"] == ["calculator"]
    assert captured["mcp_dependencies"] == ["mcp-a"]
    assert captured["skill_dependencies"] == ["beta"]
    assert captured["updated_by"] == "root"
    assert updated.skill_dependencies == ["beta"]


@pytest.mark.asyncio
async def test_init_builtin_skills_create_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    source_dir = tmp_path / "builtin-skills" / "reporter"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: reporter\ndescription: SQL report\n---\n# SQL Reporter\n",
        encoding="utf-8",
    )
    (source_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (source_dir / "prompts" / "system.md").write_text("prompt", encoding="utf-8")

    monkeypatch.setattr(
        svc,
        "get_builtin_skill_specs",
        lambda: [
            SimpleNamespace(
                slug="reporter",
                source_dir=source_dir,
                description="SQL report from python",
                tool_dependencies=("mysql_query",),
                mcp_dependencies=("charts",),
                skill_dependencies=("common-report",),
            )
        ],
    )

    class FakeRepo:
        created_payload: dict | None = None

        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            assert slug == "reporter"
            return None

        async def create(self, **kwargs) -> Skill:
            self.__class__.created_payload = kwargs
            return Skill(**kwargs, updated_by=kwargs["created_by"])

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    items = await svc.init_builtin_skills(None)

    assert len(items) == 1
    assert items[0].slug == "reporter"
    assert FakeRepo.created_payload["source_type"] == "builtin"
    assert FakeRepo.created_payload["share_config"] == svc.BUILTIN_SKILL_SHARE_CONFIG
    assert FakeRepo.created_payload["enabled"] is True
    assert FakeRepo.created_payload["created_by"] == "system"
    assert FakeRepo.created_payload["tool_dependencies"] == ["mysql_query"]
    assert FakeRepo.created_payload["mcp_dependencies"] == ["charts"]
    assert FakeRepo.created_payload["skill_dependencies"] == ["common-report"]
    assert (tmp_path / "skills" / "reporter" / "SKILL.md").exists()
    assert (tmp_path / "skills" / "reporter" / "prompts" / "system.md").read_text(encoding="utf-8") == "prompt"


@pytest.mark.asyncio
async def test_init_builtin_skills_updates_existing_record_and_preserves_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    source_dir = tmp_path / "builtin-skills" / "reporter"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: reporter\ndescription: new markdown description\n---\n# SQL Reporter\n",
        encoding="utf-8",
    )
    (source_dir / "prompt.md").write_text("new builtin content", encoding="utf-8")

    target_dir = tmp_path / "skills" / "reporter"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "prompt.md").write_text("old content", encoding="utf-8")

    monkeypatch.setattr(
        svc,
        "get_builtin_skill_specs",
        lambda: [
            SimpleNamespace(
                slug="reporter",
                source_dir=source_dir,
                description="new description",
                version="1.0.1",
                tool_dependencies=("mysql_query",),
                mcp_dependencies=("charts",),
                skill_dependencies=(),
            )
        ],
    )

    existing_item = Skill(
        slug="reporter",
        name="reporter",
        description="old description",
        dir_path="skills/reporter",
        source_type="builtin",
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
        share_config={"access_level": "global", "department_ids": [], "user_uids": []},
        enabled=False,
        version="1.0.0",
        content_hash="old-hash",
        created_by="system",
        updated_by="system",
    )

    captured: dict[str, object] = {}

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            assert slug == "reporter"
            return existing_item

        async def update_metadata(self, item: Skill, *, name: str, description: str, updated_by: str | None) -> Skill:
            item.name = name
            item.description = description
            captured["metadata"] = {"name": name, "description": description, "updated_by": updated_by}
            return item

        async def update_dependencies(
            self,
            item: Skill,
            *,
            tool_dependencies: list[str],
            mcp_dependencies: list[str],
            skill_dependencies: list[str],
            updated_by: str | None,
        ) -> Skill:
            item.tool_dependencies = tool_dependencies
            item.mcp_dependencies = mcp_dependencies
            item.skill_dependencies = skill_dependencies
            captured["dependencies"] = {
                "tool_dependencies": tool_dependencies,
                "mcp_dependencies": mcp_dependencies,
                "skill_dependencies": skill_dependencies,
                "updated_by": updated_by,
            }
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
            item.share_config = svc.BUILTIN_SKILL_SHARE_CONFIG.copy()
            item.updated_by = updated_by
            captured["install"] = {"version": version, "content_hash": content_hash, "updated_by": updated_by}
            return item

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    items = await svc.init_builtin_skills(None, created_by="release-bot")

    assert len(items) == 1
    assert items[0].enabled is False
    assert items[0].version == "1.0.1"
    assert (target_dir / "prompt.md").read_text(encoding="utf-8") == "new builtin content"
    assert captured["metadata"] == {
        "name": "reporter",
        "description": "new description",
        "updated_by": "release-bot",
    }
    assert captured["dependencies"] == {
        "tool_dependencies": ["mysql_query"],
        "mcp_dependencies": ["charts"],
        "skill_dependencies": [],
        "updated_by": "release-bot",
    }
    assert captured["install"]["updated_by"] == "release-bot"


@pytest.mark.asyncio
async def test_init_builtin_skills_rejects_non_builtin_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    source_dir = tmp_path / "builtin" / "reporter"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\nname: reporter\ndescription: SQL report\n---\n# SQL Reporter\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        svc,
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

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            return Skill(slug=slug, name=slug, description="uploaded", dir_path=f"skills/{slug}", source_type="upload")

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    with pytest.raises(ValueError, match="非内置 skill 冲突"):
        await svc.init_builtin_skills(None)


@pytest.mark.asyncio
async def test_update_skill_enabled_allows_builtin(monkeypatch: pytest.MonkeyPatch):
    builtin_item = Skill(
        slug="reporter",
        name="reporter",
        description="builtin",
        dir_path="skills/reporter",
        source_type="builtin",
        enabled=True,
    )

    async def fake_get_manageable_skill_or_raise(_db, user, slug: str):
        assert user.uid == "root"
        assert slug == "reporter"
        return builtin_item

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def update_enabled(self, item: Skill, *, enabled: bool, updated_by: str | None):
            item.enabled = enabled
            item.updated_by = updated_by
            return item

    monkeypatch.setattr(svc, "get_manageable_skill_or_raise", fake_get_manageable_skill_or_raise)
    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    updated = await svc.update_skill_enabled(None, slug="reporter", enabled=False, operator=_user("root"))

    assert updated.enabled is False
    assert updated.updated_by == "root"


@pytest.mark.asyncio
async def test_builtin_skill_file_edit_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    target_dir = tmp_path / "skills" / "reporter"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "SKILL.md").write_text(
        "---\nname: reporter\ndescription: builtin\n---\n# Reporter\n",
        encoding="utf-8",
    )

    builtin_item = Skill(
        slug="reporter",
        name="reporter",
        description="builtin",
        dir_path="skills/reporter",
        source_type="builtin",
    )

    async def fake_get_skill_or_raise(_db, _slug: str):
        return builtin_item

    monkeypatch.setattr(svc, "get_skill_or_raise", fake_get_skill_or_raise)

    with pytest.raises(ValueError, match="内置 skill 不允许直接修改文件"):
        await svc.update_skill_file(
            None,
            slug="reporter",
            relative_path="SKILL.md",
            content="new content",
            updated_by="root",
        )


@pytest.mark.asyncio
async def test_delete_skills_batch_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    # 模拟两个已安装的技能
    (tmp_path / "skills" / "skill-a").mkdir(parents=True, exist_ok=True)
    (tmp_path / "skills" / "skill-b").mkdir(parents=True, exist_ok=True)

    item_a = Skill(slug="skill-a", name="skill-a", description="a", dir_path="skills/skill-a")
    item_b = Skill(slug="skill-b", name="skill-b", description="b", dir_path="skills/skill-b")

    db_items = {"skill-a": item_a, "skill-b": item_b}
    deleted_slugs = []

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str, *, for_update: bool = False):
            return db_items.get(slug)

        async def delete(self, item: Skill):
            deleted_slugs.append(item.slug)
            db_items.pop(item.slug, None)

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    # 执行批量删除，skill-a, skill-b, skill-c (不存在)
    results = await svc.delete_skills_batch(None, slugs=["skill-a", "skill-b", "skill-c"])

    assert results == [
        {"slug": "skill-a", "success": True},
        {"slug": "skill-b", "success": True},
        {"slug": "skill-c", "success": False, "error": "技能 'skill-c' 不存在"},
    ]
    assert deleted_slugs == ["skill-a", "skill-b"]
    assert not (tmp_path / "skills" / "skill-a").exists()
    assert not (tmp_path / "skills" / "skill-b").exists()


@pytest.mark.asyncio
async def test_delete_skills_batch_limit_exceeded():
    slugs = [f"skill-{i}" for i in range(51)]
    with pytest.raises(ValueError, match="批量删除的技能数量不能超过 50 个"):
        await svc.delete_skills_batch(None, slugs=slugs)


@pytest.mark.asyncio
async def test_delete_skill_concurrent_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))
    (tmp_path / "skills" / "concurrent-skill").mkdir(parents=True, exist_ok=True)

    item = Skill(
        slug="concurrent-skill", name="concurrent-skill", description="desc", dir_path="skills/concurrent-skill"
    )

    db_items = {"concurrent-skill": item}
    lock_active = asyncio.Lock()

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str, *, for_update: bool = False):
            # 用 asyncio.Lock 模拟 with_for_update() 的排他锁
            if for_update:
                await lock_active.acquire()
                try:
                    return db_items.get(slug)
                finally:
                    pass
            else:
                return db_items.get(slug)

        async def delete(self, item: Skill):
            db_items.pop(item.slug, None)
            if lock_active.locked():
                lock_active.release()

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    # 同时发起两个 delete_skill 调用
    task1 = asyncio.create_task(svc.delete_skill(None, slug="concurrent-skill"))
    task2 = asyncio.create_task(svc.delete_skill(None, slug="concurrent-skill"))

    results = await asyncio.gather(task1, task2, return_exceptions=True)

    success_count = 0
    error_count = 0
    for r in results:
        if r is None:
            success_count += 1
        elif isinstance(r, ValueError) and "不存在" in str(r):
            error_count += 1

    assert success_count == 1
    assert error_count == 1
    assert not (tmp_path / "skills" / "concurrent-skill").exists()
