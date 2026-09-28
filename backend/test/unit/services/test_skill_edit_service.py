"""共享 Skill 在线编辑的文件与索引结果。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from yuxi.agents.skills import service as skill_service
from yuxi.services import skill_edit_service as edit_service
from yuxi.storage.postgres.models_business import Skill, User


class _Session:
    def __init__(self, *, fail_commit: bool = False):
        self.fail_commit = fail_commit

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("database commit failed")


def _setup_shared_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    skill_dir = tmp_path / "shared" / "demo"
    skill_dir.mkdir(parents=True)
    content = "---\nname: demo\nslug: demo\ndescription: old\n---\n# Old\n"
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    item = Skill(
        slug="demo",
        name="demo",
        description="old",
        source_type="upload",
        dir_path="shared/demo",
        enabled=True,
        created_by="owner",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["owner"], "department_ids": []},
            "manage_scope": {"access_level": "user", "user_uids": ["owner"], "department_ids": []},
        },
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
    )

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug, *, for_update=False):
            assert slug == "demo" and for_update
            return item

        async def update_metadata(self, target, *, name, description, updated_by):
            target.name = name
            target.description = description
            target.updated_by = updated_by
            return target

        async def update_dependencies(
            self, target, *, tool_dependencies, mcp_dependencies, skill_dependencies, updated_by
        ):
            target.tool_dependencies = tool_dependencies
            target.mcp_dependencies = mcp_dependencies
            target.skill_dependencies = skill_dependencies
            target.updated_by = updated_by
            return target

    async def accessible(_db, _user):
        return [item]

    async def mcps(db=None):
        return []

    monkeypatch.setattr(edit_service, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(edit_service, "SkillRepository", FakeRepo)
    monkeypatch.setattr(edit_service, "list_accessible_shared_skills", accessible)
    monkeypatch.setattr(skill_service, "_get_all_tool_names", lambda: [])
    monkeypatch.setattr(skill_service, "get_enabled_mcp_server_slugs", mcps)
    return skill_dir, item, content


def _user(uid: str) -> User:
    return User(uid=uid, role="user")


@pytest.mark.asyncio
async def test_edit_root_file_updates_bytes_and_database_dependencies(tmp_path, monkeypatch):
    skill_dir, item, old = _setup_shared_skill(tmp_path, monkeypatch)
    new = "---\nname: demo\nslug: demo\ndescription: updated\n---\n# New\n"

    result, revision = await edit_service.edit_shared_skill_file(
        _Session(),
        slug="demo",
        relative_path="SKILL.md",
        content=new,
        expected_revision=hashlib.sha256(old.encode()).hexdigest(),
        operator=_user("owner"),
    )

    assert result is item
    assert item.description == "updated"
    assert item.tool_dependencies == []
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == new
    assert revision == hashlib.sha256(new.encode()).hexdigest()


@pytest.mark.asyncio
async def test_edit_rejects_stale_revision_without_overwriting(tmp_path, monkeypatch):
    skill_dir, item, old = _setup_shared_skill(tmp_path, monkeypatch)

    with pytest.raises(edit_service.SkillEditConflict, match="其他编辑"):
        await edit_service.edit_shared_skill_file(
            _Session(),
            slug="demo",
            relative_path="SKILL.md",
            content=old.replace("old", "new"),
            expected_revision="0" * 64,
            operator=_user("owner"),
        )

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == old
    assert item.description == "old"


@pytest.mark.asyncio
async def test_edit_rejects_unmanaged_and_builtin_skills(tmp_path, monkeypatch):
    skill_dir, item, old = _setup_shared_skill(tmp_path, monkeypatch)
    revision = hashlib.sha256(old.encode()).hexdigest()

    with pytest.raises(ValueError, match="无权管理"):
        await edit_service.edit_shared_skill_file(
            _Session(),
            slug="demo",
            relative_path="SKILL.md",
            content="changed",
            expected_revision=revision,
            operator=_user("other"),
        )
    item.source_type = "builtin"
    with pytest.raises(ValueError, match="内置 skill"):
        await edit_service.edit_shared_skill_file(
            _Session(),
            slug="demo",
            relative_path="SKILL.md",
            content="changed",
            expected_revision=revision,
            operator=User(uid="owner", role="admin"),
        )

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == old


@pytest.mark.asyncio
async def test_edit_rejects_symlink_and_path_traversal(tmp_path, monkeypatch):
    skill_dir, _item, old = _setup_shared_skill(tmp_path, monkeypatch)
    (skill_dir / "link.md").symlink_to(skill_dir / "SKILL.md")
    revision = hashlib.sha256(old.encode()).hexdigest()

    for path in ("../outside.md", "link.md"):
        with pytest.raises(ValueError, match="路径"):
            await edit_service.edit_shared_skill_file(
                _Session(),
                slug="demo",
                relative_path=path,
                content="changed",
                expected_revision=revision,
                operator=_user("owner"),
            )

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == old


@pytest.mark.asyncio
async def test_edit_restores_file_when_database_commit_fails(tmp_path, monkeypatch):
    skill_dir, _item, old = _setup_shared_skill(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="database commit failed"):
        await edit_service.edit_shared_skill_file(
            _Session(fail_commit=True),
            slug="demo",
            relative_path="SKILL.md",
            content=old.replace("old", "updated"),
            expected_revision=hashlib.sha256(old.encode()).hexdigest(),
            operator=_user("owner"),
        )

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == old


@pytest.mark.asyncio
async def test_edit_restores_file_when_directory_sync_fails(tmp_path, monkeypatch):
    skill_dir, _item, _old = _setup_shared_skill(tmp_path, monkeypatch)
    file = skill_dir / "notes.md"
    file.write_text("before", encoding="utf-8")
    original_fsync = os.fsync
    calls = 0

    def fail_first_directory_sync(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("directory sync failed")
        original_fsync(fd)

    monkeypatch.setattr(edit_service.os, "fsync", fail_first_directory_sync)
    with pytest.raises(OSError, match="directory sync failed"):
        await edit_service.edit_shared_skill_file(
            _Session(),
            slug="demo",
            relative_path="notes.md",
            content="after",
            expected_revision=hashlib.sha256(b"before").hexdigest(),
            operator=_user("owner"),
        )

    assert file.read_text(encoding="utf-8") == "before"


@pytest.mark.asyncio
async def test_projection_cannot_copy_edit_staging_file(tmp_path, monkeypatch):
    skill_dir, _item, old = _setup_shared_skill(tmp_path, monkeypatch)
    monkeypatch.setattr(skill_service, "get_skill_projection_dir", lambda: tmp_path / "projections")
    original_replace = os.replace
    observed = False

    def observe_publication(source, target, *, src_dir_fd, dst_dir_fd):
        nonlocal observed
        observed = True
        projection = skill_service.sync_user_accessible_skills("owner", {"demo": skill_dir}) / "demo"
        assert (projection / "SKILL.md").read_text(encoding="utf-8") == old
        assert not any(path.name.endswith(".tmp") for path in projection.rglob("*"))
        return original_replace(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(edit_service.os, "replace", observe_publication)
    updated = old.replace("old", "new")
    await edit_service.edit_shared_skill_file(
        _Session(),
        slug="demo",
        relative_path="SKILL.md",
        content=updated,
        expected_revision=hashlib.sha256(old.encode()).hexdigest(),
        operator=_user("owner"),
    )

    assert observed
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == updated


@pytest.mark.asyncio
async def test_dependency_form_updates_root_file_and_index(tmp_path, monkeypatch):
    skill_dir, item, old = _setup_shared_skill(tmp_path, monkeypatch)
    monkeypatch.setattr(skill_service, "_get_all_tool_names", lambda: ["calculator"])

    result, revision = await edit_service.edit_shared_skill_dependencies(
        _Session(),
        slug="demo",
        tool_dependencies=["calculator"],
        mcp_dependencies=[],
        skill_dependencies=[],
        expected_revision=hashlib.sha256(old.encode()).hexdigest(),
        operator=_user("owner"),
    )

    saved = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert result is item
    assert item.tool_dependencies == ["calculator"]
    assert "tool_dependencies:\n- calculator" in saved
    assert revision == hashlib.sha256(saved.encode()).hexdigest()


@pytest.mark.asyncio
async def test_dependency_edit_accepts_unquoted_multiline_description(tmp_path, monkeypatch):
    """预览可识别的多行描述也必须能保存依赖。"""
    skill_dir, item, _old = _setup_shared_skill(tmp_path, monkeypatch)
    original = (
        "---\nname: demo\nslug: demo\ndescription:\n"
        '  Use this skill for PDFs.\n  CREATE (from scratch): "make a PDF".\n'
        "license: MIT\n---\n# Body\n"
    )
    (skill_dir / "SKILL.md").write_text(original, encoding="utf-8")

    result, revision = await edit_service.edit_shared_skill_dependencies(
        _Session(),
        slug="demo",
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
        expected_revision=hashlib.sha256(original.encode()).hexdigest(),
        operator=_user("owner"),
    )

    saved = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert result is item
    assert item.description == 'Use this skill for PDFs. CREATE (from scratch): "make a PDF".'
    assert skill_service.parse_skill_markdown(saved)[2] == item.description
    assert revision == hashlib.sha256(saved.encode()).hexdigest()


@pytest.mark.asyncio
async def test_edit_rejects_invalid_dependency_without_publishing_file(tmp_path, monkeypatch):
    skill_dir, item, old = _setup_shared_skill(tmp_path, monkeypatch)
    invalid = old.replace("description: old", "description: changed\ntool_dependencies:\n- missing-tool")

    with pytest.raises(ValueError, match="无效工具依赖"):
        await edit_service.edit_shared_skill_file(
            _Session(),
            slug="demo",
            relative_path="SKILL.md",
            content=invalid,
            expected_revision=hashlib.sha256(old.encode()).hexdigest(),
            operator=_user("owner"),
        )

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == old
    assert item.description == "old"
    assert item.tool_dependencies == []
