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
async def test_prepare_remote_skill_install_stages_success_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    valid_dir = tmp_path / "remote-pdf"
    invalid_dir = tmp_path / "remote-broken"
    valid_dir.mkdir()
    invalid_dir.mkdir()
    (valid_dir / "SKILL.md").write_text(
        "---\nname: pdf\ndescription: PDF operations\n---\n# PDF\n",
        encoding="utf-8",
    )

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def exists_slug(self, _slug: str) -> bool:
            return False

    class FakePreparation:
        results = [
            {"slug": "pdf", "success": True, "source_dir": valid_dir},
            {"slug": "broken", "success": True, "source_dir": invalid_dir},
        ]
        cleaned = False

        async def cleanup(self):
            self.cleaned = True

    preparation = FakePreparation()

    async def fake_prepare_remote_skills_batch(*, source, skills):
        assert source == "anthropics/skills"
        assert skills == ["pdf", "broken"]
        return preparation

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)
    monkeypatch.setattr(
        "yuxi.agents.skills.remote_install.prepare_remote_skills_batch",
        fake_prepare_remote_skills_batch,
    )
    draft = await svc.prepare_remote_skill_install(
        None,
        source="anthropics/skills",
        skills=["pdf", "broken"],
        operator=_user(),
    )

    assert [item["success"] for item in draft["items"]] == [True, False]
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

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return items

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    visible = await svc.list_visible_skills_for_management(None, _user("root", role="user"))

    assert [item.slug for item in visible] == ["owned-disabled", "shared-enabled", "shared-disabled"]


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
            _user("root", role="user"),
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
async def test_management_readable_skill_allows_disabled_user_shared_manager(monkeypatch: pytest.MonkeyPatch):
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

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            assert slug == skill.slug
            return skill

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    result = await svc.get_management_readable_skill_or_raise(None, _user("root", role="user"), skill.slug)

    assert result is skill


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

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_enabled(self):
            return []

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    async def no_personal_skills(_uid, *, refresh=False):
        del refresh
        return svc.PersonalSkillSnapshot(items=[], scanned_at="", from_cache=False)

    monkeypatch.setattr(svc, "list_personal_skills", no_personal_skills)

    assert svc.user_can_access_skill(_user("root", role="user"), skill) is False
    assert await svc.list_accessible_skills(None, _user("root", role="user")) == []


@pytest.mark.asyncio
async def test_normal_user_skill_upload_draft_defaults_to_personal_read_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
        "version": 2,
        "read_scope": {"access_level": "user", "department_ids": [], "user_uids": ["normal-user"]},
        "manage_scope": None,
    }
    assert draft["allowed_access_levels"] == ["user"]


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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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


@pytest.mark.asyncio
async def test_confirm_skill_install_draft_only_processes_selected_slugs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    draft_dir = tmp_path / "draft"
    draft_dir.mkdir()
    data = {
        "created_by": "root",
        "source_type": "remote",
        "items": [
            {"slug": "alpha", "success": False, "error": "alpha failed"},
            {"slug": "beta", "success": False, "error": "beta failed"},
        ],
    }

    class FakeRepo:
        def __init__(self, _db):
            pass

    monkeypatch.setattr(svc, "_load_skill_draft", lambda _draft_id: (draft_dir, data))
    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)
    results = await svc.confirm_skill_install_draft(
        None,
        draft_id="draft-1",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["root"]},
            "manage_scope": {"access_level": "user", "user_uids": ["root"]},
        },
        slugs=["beta"],
        operator=_user(),
    )

    assert results == [{"slug": "beta", "success": False, "error": "beta failed"}]


@pytest.mark.parametrize(("slugs", "message"), [([], "至少选择一个 Skill"), (["missing"], "草稿外的 Skill")])
@pytest.mark.asyncio
async def test_confirm_skill_install_draft_rejects_invalid_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    slugs: list[str],
    message: str,
):
    draft_dir = tmp_path / "draft"
    draft_dir.mkdir()
    data = {
        "created_by": "root",
        "source_type": "remote",
        "items": [{"slug": "alpha", "success": True}],
    }
    monkeypatch.setattr(svc, "_load_skill_draft", lambda _draft_id: (draft_dir, data))

    with pytest.raises(ValueError, match=message):
        await svc.confirm_skill_install_draft(
            None,
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


@pytest.mark.parametrize(
    ("seed_files", "mutation", "steps"),
    [
        (
            {"skills/alpha": "alpha"},
            None,
            [(None, None, {})],
        ),
        (
            {"skills/alpha": "alpha", "skills/beta": "beta"},
            None,
            [
                (["alpha", "missing", "alpha"], None, {"alpha": "alpha"}),
                (["beta"], None, {"beta": "beta"}),
            ],
        ),
        (
            {"skills/demo": "shared", "personal/demo": "personal"},
            ("personal/demo", "changed"),
            [
                (["demo"], {"demo": "personal/demo"}, {"demo": "personal"}),
                (["demo"], {"demo": "personal/demo"}, {"demo": "changed"}),
            ],
        ),
    ],
)
def test_sync_thread_readable_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_files: dict[str, str],
    mutation: tuple[str, str] | None,
    steps: list[tuple],
):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    for rel, content in seed_files.items():
        skill_dir = tmp_path / rel
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    for step_index, (selected, source_dirs, expected_entries) in enumerate(steps):
        if step_index > 0 and mutation is not None:
            rel_path, new_content = mutation
            (tmp_path / rel_path / "SKILL.md").write_text(new_content, encoding="utf-8")

        resolved_sources = {slug: tmp_path / rel for slug, rel in source_dirs.items()} if source_dirs else None
        thread_root = svc.sync_thread_readable_skills("thread_1", selected, resolved_sources)

        assert thread_root == tmp_path / "threads" / "thread_1" / "skills"
        assert sorted(path.name for path in thread_root.iterdir()) == sorted(expected_entries)
        for name, content in expected_entries.items():
            entry = thread_root / name
            assert entry.is_dir()
            assert not entry.is_symlink()
            assert (entry / "SKILL.md").read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_sync_thread_readable_skills_async_runs_in_thread(monkeypatch: pytest.MonkeyPatch):
    """异步同步入口必须把目录扫描和复制下沉到工作线程。"""
    calls = []
    expected_root = Path("/tmp/thread-skills")

    async def to_thread(func, *args):
        calls.append((func, args))
        return expected_root

    monkeypatch.setattr(svc.asyncio, "to_thread", to_thread)

    result = await svc.sync_thread_readable_skills_async(
        "thread-1",
        ["alpha"],
        {"alpha": "/tmp/alpha"},
    )

    assert result == expected_root
    assert calls == [
        (
            svc.sync_thread_readable_skills,
            ("thread-1", ["alpha"], {"alpha": "/tmp/alpha"}),
        )
    ]


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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
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
        created_by="root",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["root"]},
            "manage_scope": {"access_level": "user", "user_uids": ["root"]},
        },
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
        created_by="root",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["root"]},
            "manage_scope": {"access_level": "user", "user_uids": ["root"]},
        },
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

    async def fake_list_accessible_shared_skills(_db, _operator):
        return [item, dependency]

    monkeypatch.setattr(svc, "get_skill_or_raise", fake_get_skill_or_raise)
    monkeypatch.setattr(svc, "_list_accessible_shared_skills", fake_list_accessible_shared_skills)
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


def test_skill_dependency_scope_covers_read_and_manage_audiences():
    parent = Skill(
        slug="parent",
        name="parent",
        description="parent",
        source_type="upload",
        dir_path="skills/parent",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [1]},
            "manage_scope": {"access_level": "user", "user_uids": ["manager"]},
        },
        enabled=True,
    )
    dependency = Skill(
        slug="dependency",
        name="dependency",
        description="dependency",
        source_type="upload",
        dir_path="skills/dependency",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [1]},
            "manage_scope": {"access_level": "user", "user_uids": ["manager"]},
        },
        enabled=True,
    )

    assert svc.can_skill_depend_on(parent, dependency) is True


def test_owner_only_skill_can_depend_on_visible_skill():
    parent = Skill(
        slug="parent-owner-only",
        name="parent-owner-only",
        description="parent",
        source_type="upload",
        dir_path="skills/parent-owner-only",
        created_by="owner",
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
        enabled=True,
    )
    dependency = Skill(
        slug="dependency-global",
        name="dependency-global",
        description="dependency",
        source_type="upload",
        dir_path="skills/dependency-global",
        created_by="other",
        share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
        enabled=True,
    )

    assert svc.can_skill_depend_on(parent, dependency) is True


@pytest.mark.asyncio
async def test_init_builtin_skills_create_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

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
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
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


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.lock_acquisitions: dict[str, int] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, *, ex: int):
        self.data[key] = value
        self.expirations[key] = ex

    async def delete(self, key: str):
        self.data.pop(key, None)

    def lock(self, name: str, **_kwargs):
        redis = self
        lock = self.locks.setdefault(name, asyncio.Lock())

        class FakeLock:
            async def __aenter__(self):
                await lock.acquire()
                redis.lock_acquisitions[name] = redis.lock_acquisitions.get(name, 0) + 1
                return self

            async def __aexit__(self, *_args):
                lock.release()

        return FakeLock()


def _write_personal_skill(root: Path, slug: str, description: str) -> Path:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: {description}\n---\n# {slug}\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.mark.asyncio
async def test_personal_skill_cache_uses_five_minute_snapshot_and_manual_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    redis = _FakeRedis()
    root = tmp_path / "workspace-skills"
    _write_personal_skill(root, "demo", "first")

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(svc, "get_async_redis_client", fake_get_redis)
    monkeypatch.setattr(svc, "get_personal_skills_root_dir", lambda _uid: root)

    first = await svc.list_personal_skills("user-1")
    _write_personal_skill(root, "demo", "changed")
    cached = await svc.list_personal_skills("user-1")
    refreshed = await svc.list_personal_skills("user-1", refresh=True)

    assert first.items[0].description == "first"
    assert cached.items[0].description == "first"
    assert cached.from_cache is True
    assert refreshed.items[0].description == "changed"
    assert redis.expirations[svc._personal_skill_cache_key("user-1")] == 300


@pytest.mark.asyncio
async def test_personal_skill_cache_miss_scans_once_under_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    redis = _FakeRedis()
    root = tmp_path / "workspace-skills"
    _write_personal_skill(root, "demo", "first")
    scan_calls = 0
    original_scan = svc._scan_personal_skills

    async def fake_get_redis():
        return redis

    def counted_scan(uid: str):
        nonlocal scan_calls
        scan_calls += 1
        return original_scan(uid)

    monkeypatch.setattr(svc, "get_async_redis_client", fake_get_redis)
    monkeypatch.setattr(svc, "get_personal_skills_root_dir", lambda _uid: root)
    monkeypatch.setattr(svc, "_scan_personal_skills", counted_scan)

    first, second = await asyncio.gather(
        svc.list_personal_skills("user-1"),
        svc.list_personal_skills("user-1"),
    )

    assert scan_calls == 1
    assert first.items[0].slug == "demo"
    assert second.items[0].slug == "demo"
    assert {first.from_cache, second.from_cache} == {False, True}
    assert redis.lock_acquisitions[svc._personal_skill_scan_lock_key("user-1")] == 2


@pytest.mark.asyncio
async def test_personal_skill_overrides_shared_skill_and_drops_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    redis = _FakeRedis()
    personal_root = tmp_path / "personal"
    _write_personal_skill(personal_root, "demo", "personal")
    shared = Skill(
        id=1,
        slug="demo",
        name="Shared Demo",
        description="shared",
        source_type="upload",
        dir_path="skills/demo",
        enabled=True,
        created_by="other",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "global"},
        },
        tool_dependencies=["calculator"],
        mcp_dependencies=["mcp-a"],
        skill_dependencies=["base"],
    )

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_enabled(self):
            return [shared]

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)
    monkeypatch.setattr(svc, "get_async_redis_client", fake_get_redis)
    monkeypatch.setattr(svc, "get_personal_skills_root_dir", lambda _uid: personal_root)
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

    items = await svc.list_accessible_skills(None, _user("user-1", role="user"))

    assert len(items) == 1
    assert items[0].source_scope == "personal"
    assert items[0].description == "personal"
    assert items[0].overrides_shared is True
    assert items[0].share_config is None
    assert "share_config" not in items[0].to_dict()
    assert items[0].tool_dependencies == []
    assert items[0].mcp_dependencies == []
    assert items[0].skill_dependencies == []


@pytest.mark.asyncio
async def test_personal_skills_are_isolated_by_uid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    redis = _FakeRedis()
    roots = {uid: tmp_path / uid for uid in ("user-a", "user-b")}
    _write_personal_skill(roots["user-a"], "demo", "from a")
    _write_personal_skill(roots["user-b"], "demo", "from b")

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(svc, "get_async_redis_client", fake_get_redis)
    monkeypatch.setattr(svc, "get_personal_skills_root_dir", lambda uid: roots[uid])

    user_a = await svc.list_personal_skills("user-a")
    user_b = await svc.list_personal_skills("user-b")

    assert user_a.items[0].description == "from a"
    assert user_b.items[0].description == "from b"
    assert user_a.items[0].source_dir != user_b.items[0].source_dir
    assert svc._personal_skill_cache_key("user-a") != svc._personal_skill_cache_key("user-b")


@pytest.mark.asyncio
async def test_skill_cards_keep_shadowed_shared_item_for_management(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    redis = _FakeRedis()
    personal_root = tmp_path / "personal"
    _write_personal_skill(personal_root, "demo", "personal")
    shared = Skill(
        id=1,
        slug="demo",
        name="Shared Demo",
        description="shared",
        source_type="upload",
        dir_path="skills/demo",
        enabled=True,
        created_by="user-1",
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
    )

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return [shared]

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)
    monkeypatch.setattr(svc, "get_async_redis_client", fake_get_redis)
    monkeypatch.setattr(svc, "get_personal_skills_root_dir", lambda _uid: personal_root)
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

    cards, snapshot = await svc.list_skill_cards_for_user(None, _user("user-1", role="user"))

    assert [(item.slug, item.source_scope) for item in cards] == [
        ("demo", "personal"),
        ("demo", "shared"),
    ]
    assert cards[0].overrides_shared is True
    assert cards[1].shadowed_by_personal is True
    assert snapshot.items[0].slug == "demo"


@pytest.mark.asyncio
async def test_confirm_personal_skill_draft_uses_original_slug_without_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    redis = _FakeRedis()
    personal_root = tmp_path / "personal"
    draft_id = "11111111-1111-1111-1111-111111111111"
    draft_dir = tmp_path / "skill_import_drafts" / draft_id
    item_dir = draft_dir / "items" / "item-1"
    item_dir.mkdir(parents=True)
    (item_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: personal demo\n---\n# Demo\n",
        encoding="utf-8",
    )
    (draft_dir / "metadata.json").write_text(
        svc.json.dumps(
            {
                "created_by": "user-1",
                "source_type": "remote",
                "expires_at": svc.time.time() + 300,
                "items": [
                    {
                        "slug": "demo-v2",
                        "original_name": "demo",
                        "source_dir": "items/item-1",
                        "success": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(svc, "get_async_redis_client", fake_get_redis)
    monkeypatch.setattr(svc, "get_personal_skills_root_dir", lambda _uid: personal_root)
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

    results = await svc.confirm_personal_skill_install_draft(
        draft_id=draft_id,
        slugs=["demo-v2"],
        operator=_user("user-1", role="user"),
    )

    assert results[0]["success"] is True
    assert results[0]["slug"] == "demo"
    assert results[0]["requested_slug"] == "demo-v2"
    assert (personal_root / "demo" / "SKILL.md").exists()
    assert not draft_dir.exists()

