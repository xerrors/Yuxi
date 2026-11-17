"""共享 Skill 编辑经真实 HTTP、PostgreSQL 和文件来源的结果。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir, sync_user_accessible_skills
from yuxi.modules.extensions.skills.access import (
    lock_accessible_shared_skills_for_runtime,
)
from yuxi.modules.extensions.skills.models import Skill
from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.modules.identity.models import User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_shared_skill_edit_updates_file_and_index_and_rejects_stale_or_unauthorized(test_client, admin_headers, standard_user):
    """保存后回读两个事实来源，旧修订与无权限用户不能覆盖。"""
    slug = f"pytest-edit-{uuid.uuid4().hex[:10]}"
    original = f"---\r\nname: {slug}\r\nslug: {slug}\r\ndescription: before\r\n---\r\n# Before\r\n"
    prepared = await test_client.post(
        "/api/skills/import/prepare",
        headers=admin_headers,
        files={"file": ("SKILL.md", original.encode(), "text/markdown")},
    )
    assert prepared.status_code == 200, prepared.text
    draft_id = prepared.json()["data"]["draft_id"]
    confirmed = await test_client.post(
        f"/api/skills/install-drafts/{draft_id}/confirm",
        headers=admin_headers,
        json={"slugs": [slug], "share_config": None},
    )
    assert confirmed.status_code == 200, confirmed.text

    try:
        read = await test_client.get(f"/api/system/skills/{slug}/file", params={"path": "SKILL.md"}, headers=admin_headers)
        assert read.status_code == 200, read.text
        revision = read.json()["data"]["revision"]
        assert revision == hashlib.sha256(original.encode()).hexdigest()
        assert read.json()["data"]["content"] == original

        missing_revision = await test_client.put(
            f"/api/system/skills/{slug}/file",
            headers=admin_headers,
            json={"path": "SKILL.md", "content": original},
        )
        assert missing_revision.status_code == 422, missing_revision.text

        missing_file = await test_client.put(
            f"/api/system/skills/{slug}/file",
            headers=admin_headers,
            json={"path": "missing.md", "content": "unexpected", "expected_revision": revision},
        )
        assert missing_file.status_code == 404, missing_file.text
        assert missing_file.json()["detail"] == {"code": "skill_request_invalid", "message": "文件不存在"}
        unchanged = await test_client.get(f"/api/system/skills/{slug}/content", headers=admin_headers)
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["data"]["files"] == {"SKILL.md": original}

        updated_content = original.replace("before", "after").replace("# Before", "# After")
        saved = await test_client.put(
            f"/api/system/skills/{slug}/file",
            headers=admin_headers,
            json={"path": "SKILL.md", "content": updated_content, "expected_revision": revision},
        )
        assert saved.status_code == 200, saved.text
        saved_revision = saved.json()["data"]["revision"]
        assert saved_revision == hashlib.sha256(updated_content.encode()).hexdigest()

        stale = await test_client.put(
            f"/api/system/skills/{slug}/file",
            headers=admin_headers,
            json={"path": "SKILL.md", "content": original, "expected_revision": revision},
        )
        assert stale.status_code == 409, stale.text

        denied = await test_client.put(
            f"/api/system/skills/{slug}/file",
            headers=standard_user["headers"],
            json={"path": "SKILL.md", "content": original, "expected_revision": revision},
        )
        assert denied.status_code in {403, 404}, denied.text

        dependencies = await test_client.put(
            f"/api/system/skills/{slug}/file",
            headers=admin_headers,
            json={
                "path": "SKILL.md",
                "content": updated_content.replace("---", "---\r\ntool_dependencies: [web_search]", 1),
                "expected_revision": saved_revision,
            },
        )
        assert dependencies.status_code == 400, dependencies.text
        assert "部署插件" in dependencies.text

        root_snapshot = await test_client.get(f"/api/system/skills/{slug}/file", params={"path": "SKILL.md"}, headers=admin_headers)
        assert root_snapshot.status_code == 200, root_snapshot.text
        assert "tool_dependencies" not in root_snapshot.json()["data"]["skill"]
        assert root_snapshot.json()["data"]["revision"] == saved_revision

        source = get_skill_data_dir() / saved.json()["data"]["skill"]["dir_path"] / "SKILL.md"
        persisted_content = source.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(persisted_content.split("---", 2)[1])
        assert frontmatter["description"] == "after"
        assert "tool_dependencies" not in frontmatter
        assert "# After" in persisted_content
        engine = create_async_engine(os.environ["POSTGRES_URL"])
        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                row = (await db.execute(select(Skill).where(Skill.slug == slug))).scalar_one()
                assert row.description == "after"
                assert "tool_dependencies" not in row.to_dict()

            async with session_factory() as editor, session_factory() as runtime:
                await editor.execute(select(Skill).where(Skill.slug == slug).with_for_update())
                runtime_read = asyncio.create_task(SkillRepository(runtime).lock_rows_for_read([row.id]))
                await asyncio.sleep(0.2)
                assert not runtime_read.done(), "运行时读取必须等待编辑行锁释放"
                await editor.rollback()
                assert any(item.slug == slug for item in await asyncio.wait_for(runtime_read, 5))
        finally:
            await engine.dispose()

        profile = await test_client.get("/api/auth/me", headers=admin_headers)
        assert profile.status_code == 200, profile.text
        uid = profile.json()["uid"]
        sync_user_accessible_skills(uid, {slug: source.parent})
        assert (get_user_skills_root_dir(uid) / slug / "SKILL.md").read_text(encoding="utf-8") == persisted_content
    finally:
        deleted = await test_client.delete(f"/api/system/skills/{slug}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text


@pytest.mark.parametrize("personal_override", [False, True])
async def test_runtime_read_does_not_lock_unrelated_private_skill(personal_override):
    """其他用户的私有 Skill 正在编辑时，当前用户仍可读取自己的共享快照。"""
    suffix = uuid.uuid4().hex[:10]
    reader_uid = f"reader-{suffix}"
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    visible = Skill(
        slug=f"visible-{suffix}",
        name="visible",
        description="visible",
        source_type="upload",
        dir_path=f"shared/visible-{suffix}",
        enabled=True,
        created_by=reader_uid,
        share_config={"version": 2, "read_scope": {"access_level": "user", "user_uids": [reader_uid]}},
    )
    hidden = Skill(
        slug=f"hidden-{suffix}",
        name="hidden",
        description="hidden",
        source_type="upload",
        dir_path=f"shared/hidden-{suffix}",
        enabled=True,
        created_by=f"owner-{suffix}",
        share_config={"version": 2, "read_scope": {"access_level": "user", "user_uids": [f"owner-{suffix}"]}},
    )
    if personal_override:
        hidden.share_config = {"version": 2, "read_scope": {"access_level": "global"}}
    try:
        async with session_factory() as setup:
            setup.add_all([visible, hidden])
            await setup.commit()

        async with session_factory() as editor, session_factory() as runtime:
            await editor.execute(select(Skill).where(Skill.id == hidden.id).with_for_update())
            reader = User(uid=reader_uid, role="user")
            items = await asyncio.wait_for(
                lock_accessible_shared_skills_for_runtime(
                    runtime,
                    reader,
                    [visible.slug, hidden.slug],
                    shadowed_slugs={hidden.slug} if personal_override else set(),
                ),
                timeout=2,
            )
            slugs = {item.slug for item in items}
            assert visible.slug in slugs
            assert hidden.slug not in slugs
            await editor.rollback()
    finally:
        async with session_factory() as cleanup:
            for item_id in (visible.id, hidden.id):
                if item_id is not None:
                    found = await cleanup.get(Skill, item_id)
                    if found is not None:
                        await cleanup.delete(found)
            await cleanup.commit()
        await engine.dispose()


async def test_unquoted_multiline_description_can_be_saved_through_http(test_client, admin_headers):
    """预览支持的多行描述可经真实安装和依赖编辑路径保存。"""
    slug = f"pytest-multiline-{uuid.uuid4().hex[:10]}"
    content = (
        f'---\nname: {slug}\ndescription:\n  Use this skill for PDFs.\n  CREATE (from scratch): "make a PDF".\nlicense: MIT\n---\n# Body\n'
    )
    prepared = await test_client.post(
        "/api/skills/import/prepare",
        headers=admin_headers,
        files={"file": ("SKILL.md", content.encode(), "text/markdown")},
    )
    assert prepared.status_code == 200, prepared.text
    draft_id = prepared.json()["data"]["draft_id"]
    confirmed = await test_client.post(
        f"/api/skills/install-drafts/{draft_id}/confirm",
        headers=admin_headers,
        json={"slugs": [slug], "share_config": None},
    )
    assert confirmed.status_code == 200, confirmed.text

    try:
        read = await test_client.get(f"/api/system/skills/{slug}/file", params={"path": "SKILL.md"}, headers=admin_headers)
        assert read.status_code == 200, read.text
        saved = await test_client.put(
            f"/api/system/skills/{slug}/file",
            headers=admin_headers,
            json={
                "path": "SKILL.md",
                "content": read.json()["data"]["content"],
                "expected_revision": read.json()["data"]["revision"],
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["skill"]["description"] == ('Use this skill for PDFs. CREATE (from scratch): "make a PDF".')
    finally:
        deleted = await test_client.delete(f"/api/system/skills/{slug}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text


async def test_delete_cleanup_preserves_reinstalled_same_slug(test_client, admin_headers, monkeypatch):
    """删除提交后的旧目录清理不能删除随后同 slug 安装的新包。"""
    from yuxi.modules.extensions.skills import shared

    slug = f"pytest-reinstall-{uuid.uuid4().hex[:10]}"
    root = f"---\nname: {slug}\nslug: {slug}\ndescription: old\n---\nold"

    async def install(content):
        """通过真实 HTTP 安装指定字节，返回持久引用。"""
        prepared = await test_client.post(
            "/api/skills/import/prepare",
            headers=admin_headers,
            files={"file": ("SKILL.md", content.encode(), "text/markdown")},
        )
        assert prepared.status_code == 200, prepared.text
        confirmed = await test_client.post(
            f"/api/skills/install-drafts/{prepared.json()['data']['draft_id']}/confirm",
            headers=admin_headers,
            json={"slugs": [slug], "share_config": None},
        )
        assert confirmed.status_code == 200, confirmed.text
        detail = await test_client.get(f"/api/system/skills/{slug}", headers=admin_headers)
        return get_skill_data_dir() / detail.json()["data"]["dir_path"]

    old = await install(root)
    revision = (await test_client.get(f"/api/system/skills/{slug}/content", headers=admin_headers)).json()["data"]["revision"]
    ordinary_release = await test_client.put(
        f"/api/system/skills/{slug}/content",
        headers=admin_headers,
        json={"expected_revision": revision, "changes": [], "release": True},
    )
    assert ordinary_release.status_code == 400, ordinary_release.text
    assert old.joinpath("SKILL.md").read_text() == root
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    original_to_thread = asyncio.to_thread
    replacement = None

    async def reinstall_before_cleanup(func, *args, **kwargs):
        """在删除提交与旧包清理之间安装新的同名内容。"""
        nonlocal replacement
        if func is shared.shutil.rmtree and args and args[0] == old:
            replacement = await install(root.replace("old", "new"))
        return await original_to_thread(func, *args, **kwargs)

    try:
        monkeypatch.setattr(asyncio, "to_thread", reinstall_before_cleanup)
        async with factory() as db:
            operator = await db.scalar(select(User).where(User.uid == me.json()["uid"]))
            await shared.delete_skill(db, slug=slug, operator=operator)
        assert replacement is not None and replacement != old
        assert not old.exists()
        assert replacement.joinpath("SKILL.md").read_text() == root.replace("old", "new")
        read = await test_client.get(f"/api/system/skills/{slug}/content", headers=admin_headers)
        assert read.status_code == 200, read.text
        assert read.json()["data"]["files"]["SKILL.md"] == root.replace("old", "new")
    finally:
        monkeypatch.setattr(asyncio, "to_thread", original_to_thread)
        await test_client.delete(f"/api/system/skills/{slug}", headers=admin_headers)
        await engine.dispose()
