from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.services import skill_service as svc
from yuxi.services import tool_service
from yuxi.storage.postgres.models_business import Skill


def _build_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def test_parse_skill_markdown_ok():
    content = "---\nname: demo-skill\ndescription: demo description\n---\n# Demo\n"
    name, desc, meta = svc._parse_skill_markdown(content)
    assert name == "demo-skill"
    assert desc == "demo description"
    assert meta["name"] == "demo-skill"


def test_parse_skill_markdown_requires_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        svc._parse_skill_markdown("# missing")


def test_is_valid_skill_slug():
    # Test valid slugs
    assert svc.is_valid_skill_slug("demo-skill") is True
    assert svc.is_valid_skill_slug("valid-name-123") is True
    # Test invalid slugs
    assert svc.is_valid_skill_slug("../bad") is False
    assert svc.is_valid_skill_slug("Invalid") is False  # uppercase not allowed
    assert svc.is_valid_skill_slug("") is False


@pytest.mark.asyncio
async def test_get_skill_dependency_options(monkeypatch: pytest.MonkeyPatch):
    # Mock get_tool_metadata to return tool list
    def fake_get_tool_metadata(category=None):
        return [
            {"id": "calculator", "name": "Calculator"},
            {"id": "search", "name": "Search"},
        ]

    monkeypatch.setattr(tool_service, "get_tool_metadata", fake_get_tool_metadata)
    monkeypatch.setattr(svc, "get_mcp_server_names", lambda: ["mcp-a", "mcp-b"])

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return [
                Skill(slug="alpha", name="alpha", description="a", dir_path="skills/alpha"),
                Skill(slug="beta", name="beta", description="b", dir_path="skills/beta"),
            ]

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    result = await svc.get_skill_dependency_options(None)
    assert result["tools"] == [{"id": "calculator", "name": "Calculator"}, {"id": "search", "name": "Search"}]
    assert result["mcps"] == ["mcp-a", "mcp-b"]
    assert result["skills"] == ["alpha", "beta"]


def test_resolve_relative_path_blocks_traversal(tmp_path: Path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="上级路径"):
        svc._resolve_relative_path(skill_dir, "../outside.txt")


@pytest.mark.asyncio
async def test_import_skill_zip_conflict_rewrite_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    class FakeRepo:
        existing_slugs = {"demo"}
        created_item: Skill | None = None

        def __init__(self, _db):
            pass

        async def exists_slug(self, slug: str) -> bool:
            return slug in self.__class__.existing_slugs

        async def create(
            self,
            *,
            slug: str,
            name: str,
            description: str,
            tool_dependencies: list[str] | None,
            mcp_dependencies: list[str] | None,
            skill_dependencies: list[str] | None,
            dir_path: str,
            created_by: str | None,
        ) -> Skill:
            item = Skill(
                slug=slug,
                name=name,
                description=description,
                tool_dependencies=tool_dependencies or [],
                mcp_dependencies=mcp_dependencies or [],
                skill_dependencies=skill_dependencies or [],
                dir_path=dir_path,
                created_by=created_by,
                updated_by=created_by,
            )
            self.__class__.existing_slugs.add(slug)
            self.__class__.created_item = item
            return item

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    zip_bytes = _build_zip(
        {
            "demo/SKILL.md": ("---\nname: demo\ndescription: this is demo\n---\n# Demo\n"),
            "demo/prompts/system.md": "You are demo skill",
        }
    )

    item = await svc.import_skill_zip(
        None,
        filename="demo.zip",
        file_bytes=zip_bytes,
        created_by="root",
    )

    assert item.slug == "demo-v2"
    assert item.name == "demo-v2"
    skill_md = (tmp_path / "skills" / "demo-v2" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: demo-v2" in skill_md


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
        dir_path="skills/alpha",
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
    )

    # Mock get_tool_metadata to return tool list
    def fake_get_tool_metadata(category=None):
        return [{"id": "calculator", "name": "Calculator"}]

    monkeypatch.setattr(tool_service, "get_tool_metadata", fake_get_tool_metadata)
    monkeypatch.setattr(svc, "get_mcp_server_names", lambda: ["mcp-a"])

    async def fake_get_skill_or_raise(_db, slug: str):
        assert slug == "alpha"
        return item

    captured: dict[str, list[str] | str | None] = {}

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def list_all(self):
            return [
                item,
                Skill(
                    slug="beta",
                    name="beta",
                    description="beta",
                    dir_path="skills/beta",
                    tool_dependencies=[],
                    mcp_dependencies=[],
                    skill_dependencies=[],
                ),
            ]

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

    monkeypatch.setattr(svc, "get_skill_or_raise", fake_get_skill_or_raise)
    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    updated = await svc.update_skill_dependencies(
        None,
        slug="alpha",
        tool_dependencies=["calculator", "calculator"],
        mcp_dependencies=["mcp-a", "mcp-a"],
        skill_dependencies=["beta", "beta"],
        updated_by="root",
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
        "---\n"
        "name: reporter\n"
        "description: SQL report\n"
        "---\n"
        "# SQL Reporter\n",
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
        created: list[dict] = []

        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            return None

        async def create(
            self,
            *,
            slug: str,
            name: str,
            description: str,
            tool_dependencies: list[str] | None,
            mcp_dependencies: list[str] | None,
            skill_dependencies: list[str] | None,
            dir_path: str,
            created_by: str | None,
        ) -> Skill:
            self.__class__.created.append(
                {
                    "slug": slug,
                    "name": name,
                    "description": description,
                    "tool_dependencies": tool_dependencies,
                    "mcp_dependencies": mcp_dependencies,
                    "skill_dependencies": skill_dependencies,
                    "dir_path": dir_path,
                    "created_by": created_by,
                }
            )
            return Skill(
                slug=slug,
                name=name,
                description=description,
                dir_path=dir_path,
                tool_dependencies=tool_dependencies or [],
                mcp_dependencies=mcp_dependencies or [],
                skill_dependencies=skill_dependencies or [],
                created_by=created_by,
                updated_by=created_by,
            )

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    await svc.init_builtin_skills(None)

    assert len(FakeRepo.created) == 1
    created = FakeRepo.created[0]
    assert created["slug"] == "reporter"
    assert created["name"] == "reporter"
    assert created["description"] == "SQL report from python"
    assert created["tool_dependencies"] == ["mysql_query"]
    assert created["mcp_dependencies"] == ["charts"]
    assert created["skill_dependencies"] == ["common-report"]
    assert created["created_by"] == svc.BUILTIN_SKILL_OPERATOR

    target_dir = tmp_path / "skills" / "reporter"
    assert target_dir.is_symlink()
    assert (target_dir / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_init_builtin_skills_updates_existing_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))

    source_dir = tmp_path / "builtin-skills" / "reporter"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "SKILL.md").write_text(
        "---\n"
        "name: reporter\n"
        "description: old\n"
        "---\n"
        "# SQL Reporter\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        svc,
        "get_builtin_skill_specs",
        lambda: [
            SimpleNamespace(
                slug="reporter",
                source_dir=source_dir,
                description="new description",
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
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
        created_by="system",
        updated_by="system",
    )

    captured: dict[str, list[str] | str | None] = {}

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            return existing_item

        async def update_metadata(
            self,
            item: Skill,
            *,
            name: str,
            description: str,
            updated_by: str | None,
        ) -> Skill:
            item.name = name
            item.description = description
            captured["name"] = name
            captured["description"] = description
            captured["updated_by"] = updated_by
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
            captured["tool_dependencies"] = tool_dependencies
            captured["mcp_dependencies"] = mcp_dependencies
            captured["skill_dependencies"] = skill_dependencies
            captured["updated_by_deps"] = updated_by
            return item

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)

    await svc.init_builtin_skills(None, created_by="release-bot")

    target_dir = tmp_path / "skills" / "reporter"
    assert target_dir.is_symlink()
    assert captured["description"] == "new description"
    assert captured["tool_dependencies"] == ["mysql_query"]
    assert captured["mcp_dependencies"] == ["charts"]
    assert captured["skill_dependencies"] == []
    assert captured["updated_by"] == "release-bot"
    assert captured["updated_by_deps"] == "release-bot"
