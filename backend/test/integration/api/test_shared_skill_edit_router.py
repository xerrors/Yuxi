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

from yuxi.agents.skills.repository import SkillRepository
from yuxi.agents.skills.service import get_skills_root_dir, get_user_skills_root_dir, sync_user_accessible_skills
from yuxi.storage.postgres.models_business import Skill

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
                runtime_read = asyncio.create_task(SkillRepository(runtime).list_enabled(for_share=True))
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
