"""Skill artifact 的实时授权与投影收敛 HTTP 集成测试。"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.skills import shared as skill_service
from yuxi.services.skills import projection as projection_service
from yuxi.storage.postgres.models_business import Skill
from test.live_api_cleanup import make_test_conversation_metadata, make_test_conversation_title

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _create_thread(test_client, headers: dict[str, str], title: str) -> str:
    agent_response = await test_client.get("/api/agent/default", headers=headers)
    assert agent_response.status_code == 200, agent_response.text
    agent = agent_response.json()["agent"]
    agent_id = agent.get("slug") or agent.get("agent_id") or agent.get("id")
    assert agent_id
    response = await test_client.post(
        "/api/chat/thread",
        json={
            "agent_id": agent_id,
            "title": make_test_conversation_title(title),
            "metadata": make_test_conversation_metadata("skill-artifact"),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json().get("thread_id") or response.json()["id"]


async def test_skill_artifact_rechecks_authorization_after_share_revoke(
    test_client,
    admin_headers,
    standard_user,
):
    """撤权提交后旧 artifact URL 必须立即拒绝，仍授权用户继续可读。"""
    admin_profile = await test_client.get("/api/auth/me", headers=admin_headers)
    assert admin_profile.status_code == 200, admin_profile.text
    admin_uid = admin_profile.json()["uid"]
    user_uid = standard_user["user"]["uid"]
    user_headers = standard_user["headers"]

    suffix = uuid.uuid4().hex
    slug = f"pytest-artifact-{suffix}"
    source_dir = skill_service.get_skills_root_dir() / slug
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text("# authorized artifact\n", encoding="utf-8")

    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    skill_id: int | None = None
    projection_paths = [projection_service.get_user_skills_root_dir(uid) / slug for uid in (admin_uid, user_uid)]
    try:
        async with session_factory() as db:
            skill = Skill(
                slug=slug,
                name=slug,
                description="Skill artifact authorization integration fixture",
                source_type="upload",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=[],
                dir_path=f"shared/{slug}",
                share_config={
                    "version": 2,
                    "read_scope": {
                        "access_level": "user",
                        "department_ids": [],
                        "user_uids": [admin_uid, user_uid],
                    },
                    "manage_scope": None,
                },
                enabled=True,
                created_by=admin_uid,
            )
            db.add(skill)
            await db.commit()
            skill_id = skill.id

        for uid in (admin_uid, user_uid):
            projection_service.sync_user_accessible_skills(uid, {slug: source_dir})

        admin_thread = await _create_thread(test_client, admin_headers, f"skill-artifact-admin-{suffix[:8]}")
        user_thread = await _create_thread(test_client, user_headers, f"skill-artifact-user-{suffix[:8]}")
        artifact_path = f"home/gem/skills/{slug}/SKILL.md"
        admin_url = f"/api/chat/thread/{admin_thread}/artifacts/{artifact_path}"
        user_url = f"/api/chat/thread/{user_thread}/artifacts/{artifact_path}"

        admin_before = await test_client.get(admin_url, headers=admin_headers)
        user_before = await test_client.get(user_url, headers=user_headers)
        assert admin_before.status_code == 200, admin_before.text
        assert user_before.status_code == 200, user_before.text
        assert admin_before.content == user_before.content == b"# authorized artifact\n"

        revoke = await test_client.put(
            f"/api/system/skills/{slug}/share-config",
            headers=admin_headers,
            json={
                "share_config": {
                    "version": 2,
                    "read_scope": {
                        "access_level": "user",
                        "department_ids": [],
                        "user_uids": [admin_uid],
                    },
                    "manage_scope": None,
                }
            },
        )
        assert revoke.status_code == 200, revoke.text

        user_after = await test_client.get(user_url, headers=user_headers)
        admin_after = await test_client.get(admin_url, headers=admin_headers)
        assert user_after.status_code == 403, user_after.text
        assert admin_after.status_code == 200, admin_after.text
        assert admin_after.content == b"# authorized artifact\n"
        assert not projection_paths[1].exists()
        assert (projection_paths[0] / "SKILL.md").is_file()
    finally:
        async with session_factory() as db:
            if skill_id is not None:
                await db.execute(delete(Skill).where(Skill.id == skill_id))
                await db.commit()
        await engine.dispose()
        await asyncio.to_thread(shutil.rmtree, source_dir, ignore_errors=True)
        for path in projection_paths:
            await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
