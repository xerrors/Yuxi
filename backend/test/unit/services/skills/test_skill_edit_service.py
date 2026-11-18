"""共享 Skill 在线编辑的文件与索引结果。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from yuxi.modules.extensions.tools import catalog as tool_service
from yuxi.modules.extensions.skills import edit as edit_service
from yuxi.modules.extensions.skills import content as content_service
from yuxi.modules.extensions.skills import projection as projection_service
from yuxi.modules.extensions.skills import shared as skill_service
from yuxi.modules.extensions.skills import access as skill_access
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.paths import user_workspace_dir
from yuxi.modules.workspace.runtime_files import RuntimeFiles


class _Session:
    def __init__(self, *, fail_commit: bool = False):
        self.fail_commit = fail_commit

    def add(self, item):
        pass

    async def flush(self):
        pass

    async def rollback(self):
        pass

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
    )

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug, *, for_update=False):
            assert slug == "demo"
            return item

        async def update_metadata(self, target, *, name, description, updated_by):
            target.name = name
            target.description = description
            target.updated_by = updated_by
            return target

    async def accessible(_db, _user):
        return [item]

    async def mcps(db=None):
        return []

    monkeypatch.setattr(edit_service, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(content_service, "get_skill_data_dir", lambda: tmp_path)
    monkeypatch.setattr(content_service, "SkillRepository", FakeRepo)

    async def prune(_db, _slug):
        pass

    monkeypatch.setattr(content_service, "prune_skill_content", prune)
    monkeypatch.setattr(edit_service, "SkillRepository", FakeRepo)
    monkeypatch.setattr(skill_service, "SkillRepository", FakeRepo)
    monkeypatch.setattr(skill_access, "SkillRepository", FakeRepo)
    monkeypatch.setattr(FakeRepo, "list_enabled_readable", accessible, raising=False)
    monkeypatch.setattr(tool_service, "get_tool_metadata", lambda: [])
    monkeypatch.setattr("yuxi.modules.extensions.mcp.service.get_enabled_mcp_server_slugs", mcps)
    return skill_dir, item, content


def _user(uid: str) -> User:
    return User(uid=uid, role="admin")


def test_shared_projection_ignores_personal_override_and_rejects_links(tmp_path, monkeypatch):
    """共享 runtime 路径读取用户投影，同名个人 Skill 和来源变更均不替代它。"""
    skill_dir, _item, _old = _setup_shared_skill(tmp_path, monkeypatch)
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "users"))
    monkeypatch.setenv("YUXI_SKILL_PROJECTION_DIR", str(tmp_path / "projections"))
    personal_dir = user_workspace_dir("owner") / "agents/skills/demo"
    personal_dir.mkdir(parents=True)
    (skill_dir / "notes.txt").write_text("shared", encoding="utf-8")
    (personal_dir / "notes.txt").write_text("personal", encoding="utf-8")
    projection = projection_service.sync_user_accessible_skills("owner", {"demo": skill_dir}) / "demo"
    (skill_dir / "notes.txt").write_text("new source", encoding="utf-8")
    target = tmp_path / "download.txt"
    target.write_bytes(b"")

    RuntimeFiles("owner").download_to_path("/home/user/skills/demo/notes.txt", str(target), 1024)
    assert target.read_text(encoding="utf-8") == "shared"
    (projection / "link.txt").symlink_to(personal_dir / "notes.txt")
    with pytest.raises(PermissionError):
        RuntimeFiles("owner").download_to_path("/home/user/skills/demo/link.txt", str(target), 1024)
    assert target.read_text(encoding="utf-8") == "shared"


@pytest.mark.asyncio
async def test_shared_node_create_commit_failure_removes_new_file(tmp_path, monkeypatch):
    """数据库提交失败时，新文件不能留在正式共享来源。"""
    skill_dir, item, _old = _setup_shared_skill(tmp_path, monkeypatch)

    async def manageable(_db, _user, _slug, *, for_update=False):
        assert for_update
        return item

    monkeypatch.setattr(edit_service, "get_manageable_skill_or_raise", manageable)
    with pytest.raises(RuntimeError, match="database commit failed"):
        await edit_service.create_skill_node(
            _Session(fail_commit=True),
            slug="demo",
            relative_path="new.txt",
            is_dir=False,
            content="new",
            operator=_user("owner"),
        )
    assert not (skill_dir / "new.txt").exists()


@pytest.mark.asyncio
async def test_shared_node_delete_commit_failure_restores_file(tmp_path, monkeypatch):
    """删除提交失败时，原文件仍可从共享目录读取。"""
    skill_dir, item, _old = _setup_shared_skill(tmp_path, monkeypatch)
    (skill_dir / "notes.txt").write_text("before", encoding="utf-8")

    async def manageable(_db, _user, _slug, *, for_update=False):
        assert for_update
        return item

    monkeypatch.setattr(edit_service, "get_manageable_skill_or_raise", manageable)
    with pytest.raises(RuntimeError, match="database commit failed"):
        await edit_service.delete_skill_node(_Session(fail_commit=True), slug="demo", relative_path="notes.txt", operator=_user("owner"))
    assert (skill_dir / "notes.txt").read_text(encoding="utf-8") == "before"


@pytest.mark.asyncio
async def test_shared_tree_rejects_symlinked_entry(tmp_path, monkeypatch):
    """文件树不能把目录外的个人文件伪装成共享节点。"""
    skill_dir, item, _old = _setup_shared_skill(tmp_path, monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (skill_dir / "linked.txt").symlink_to(outside)

    async def readable(_db, _user, _slug):
        return item

    monkeypatch.setattr(edit_service, "_lock_readable_skill", readable)
    with pytest.raises(ValueError, match="链接"):
        await edit_service.get_skill_tree(_Session(), slug="demo", operator=_user("owner"))


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
    assert content_service.content_path(item).joinpath("SKILL.md").read_text(encoding="utf-8") == new
    assert revision == hashlib.sha256(new.encode()).hexdigest()


@pytest.mark.asyncio
async def test_edit_rejects_stale_revision_without_overwriting(tmp_path, monkeypatch):
    skill_dir, item, old = _setup_shared_skill(tmp_path, monkeypatch)

    with pytest.raises(content_service.SkillEditConflict, match="其他编辑"):
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
    with pytest.raises(ValueError, match="无权管理"):
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
async def test_projection_reads_old_content_until_database_commit(tmp_path, monkeypatch):
    """未提交的新目录不进入用户投影，提交后只读取完整内容。"""
    skill_dir, item, old = _setup_shared_skill(tmp_path, monkeypatch)
    monkeypatch.setattr(projection_service, "get_skill_projection_dir", lambda: tmp_path / "projections")
    db = _Session()

    async def observe_commit():
        projection = projection_service.sync_user_accessible_skills("owner", {"demo": skill_dir}) / "demo"
        assert (projection / "SKILL.md").read_text() == old
        assert not any(path.name.endswith(".tmp") for path in projection.rglob("*"))

    monkeypatch.setattr(db, "commit", observe_commit)
    updated = old.replace("old", "new")
    await edit_service.edit_shared_skill_file(
        db,
        slug="demo",
        relative_path="SKILL.md",
        content=updated,
        expected_revision=hashlib.sha256(old.encode()).hexdigest(),
        operator=_user("owner"),
    )
    assert skill_dir.joinpath("SKILL.md").read_text() == old
    assert content_service.content_path(item).joinpath("SKILL.md").read_text() == updated


@pytest.mark.asyncio
async def test_edit_rejects_invalid_dependency_without_publishing_file(tmp_path, monkeypatch):
    skill_dir, item, old = _setup_shared_skill(tmp_path, monkeypatch)
    invalid = old.replace("description: old", "description: changed\ntool_dependencies:\n- missing-tool")

    with pytest.raises(ValueError, match="部署插件"):
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


@pytest.mark.asyncio
async def test_export_closes_source_directory_when_tempfile_creation_fails(tmp_path, monkeypatch):
    """导出临时文件创建失败时，已打开的来源目录描述符仍被释放。"""
    _directory, item, _content = _setup_shared_skill(tmp_path, monkeypatch)
    opened = []
    original_open = edit_service.open_shared_skill_dir

    async def readable(*_args):
        """使用已经授权的共享条目。"""
        return item

    def capture_open(skill):
        """记录真实目录描述符供失败后核对。"""
        fd = original_open(skill)
        opened.append(fd)
        return fd

    def fail_tempfile(**_kwargs):
        """模拟目标磁盘无法创建导出文件。"""
        raise OSError("cannot create export")

    monkeypatch.setattr(edit_service, "_lock_readable_skill", readable)
    monkeypatch.setattr(edit_service, "open_shared_skill_dir", capture_open)
    monkeypatch.setattr(edit_service.tempfile, "mkstemp", fail_tempfile)
    with pytest.raises(OSError, match="cannot create export"):
        await edit_service.export_skill_zip(_Session(), slug="demo", operator=_user("owner"))

    assert len(opened) == 1
    with pytest.raises(OSError) as error:
        os.fstat(opened[0])
    import errno

    assert error.value.errno == errno.EBADF


@pytest.mark.asyncio
async def test_complete_edit_cannot_exceed_package_budget(tmp_path, monkeypatch):
    """在线编辑的最终完整包同样受条目与字节限制。"""
    from yuxi.modules.extensions.skills.content import commit_skill_content

    skill_dir, item, old = _setup_shared_skill(tmp_path, monkeypatch)
    for changes in (
        [{"action": "mkdir", "path": f"d-{index}"} for index in range(2001)],
        [{"action": "write", "path": "SKILL.md", "content": old + "x" * (50 * 1024 * 1024)}],
    ):
        with pytest.raises(ValueError, match="50 MiB"):
            await commit_skill_content(
                _Session(),
                item=item,
                operator=_user("owner"),
                expected_revision=content_service.compute_skill_directory_hash(skill_dir).hex(),
                changes=changes,
            )
        assert skill_dir.joinpath("SKILL.md").read_text() == old
        assert item.dir_path == "shared/demo"
