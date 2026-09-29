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
from yuxi.repositories.skill_repository import SkillRepository
from yuxi.services.skills.projection import get_user_skills_root_dir, sync_user_accessible_skills
from yuxi.services.skills.shared import (
    get_skills_root_dir,
    lock_accessible_shared_skills_for_runtime,
)
from yuxi.storage.postgres.models_business import Skill, User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_shared_skill_edit_updates_file_and_index_and_rejects_stale_or_unauthorized(
    test_client, admin_headers, standard_user
):
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
        read = await test_client.get(
            f"/api/system/skills/{slug}/file", params={"path": "SKILL.md"}, headers=admin_headers
        )
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

        options = await test_client.get(
            "/api/system/skills/dependency-options", params={"slug": slug}, headers=admin_headers
        )
        assert options.status_code == 200, options.text
        tool_slug = options.json()["data"]["tools"][0]["slug"]
        dependencies = await test_client.put(
            f"/api/system/skills/{slug}/dependencies",
            headers=admin_headers,
            json={
                "tool_dependencies": [tool_slug],
                "mcp_dependencies": [],
                "skill_dependencies": [],
                "expected_revision": saved_revision,
            },
        )
        assert dependencies.status_code == 200, dependencies.text

        root_snapshot = await test_client.get(
            f"/api/system/skills/{slug}/file", params={"path": "SKILL.md"}, headers=admin_headers
        )
        assert root_snapshot.status_code == 200, root_snapshot.text
        assert root_snapshot.json()["data"]["skill"]["tool_dependencies"] == [tool_slug]
        assert root_snapshot.json()["data"]["revision"] == dependencies.json()["data"]["revision"]

        source = get_skills_root_dir() / slug / "SKILL.md"
        persisted_content = source.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(persisted_content.split("---", 2)[1])
        assert frontmatter["description"] == "after"
        assert frontmatter["tool_dependencies"] == [tool_slug]
        assert "# After" in persisted_content
        engine = create_async_engine(os.environ["POSTGRES_URL"])
        try:
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                row = (await db.execute(select(Skill).where(Skill.slug == slug))).scalar_one()
                assert row.description == "after"
                assert row.tool_dependencies == [tool_slug]

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
        f"---\nname: {slug}\ndescription:\n"
        '  Use this skill for PDFs.\n  CREATE (from scratch): "make a PDF".\n'
        "license: MIT\n---\n# Body\n"
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
        read = await test_client.get(
            f"/api/system/skills/{slug}/file", params={"path": "SKILL.md"}, headers=admin_headers
        )
        assert read.status_code == 200, read.text
        saved = await test_client.put(
            f"/api/system/skills/{slug}/dependencies",
            headers=admin_headers,
            json={
                "tool_dependencies": [],
                "mcp_dependencies": [],
                "skill_dependencies": [],
                "expected_revision": read.json()["data"]["revision"],
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["skill"]["description"] == (
            'Use this skill for PDFs. CREATE (from scratch): "make a PDF".'
        )
    finally:
        deleted = await test_client.delete(f"/api/system/skills/{slug}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
