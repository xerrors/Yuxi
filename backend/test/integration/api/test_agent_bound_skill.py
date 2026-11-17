"""Agent 专属 Skill 的真实 HTTP、数据库约束和目录证据。"""

import asyncio
import hashlib
import os
import re
import shutil
import uuid
from io import BytesIO
from zipfile import ZipFile

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.modules.extensions.skills.models import Skill, SkillVersion
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir, refresh_user_skill_projection_async
from yuxi.modules.extensions.skills.access import get_skills_root_dir
from yuxi.infrastructure.runtime_settings import get_skill_data_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture(autouse=True)
async def close_projection_pool():
    """投影用例的连接池在所属测试事件循环退出前关闭。"""
    yield
    from yuxi.infrastructure.postgres.manager import pg_manager

    await pg_manager.close()


async def current_content(slug):
    """从独立数据库连接读取持久目录引用。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        async with async_sessionmaker(engine)() as db:
            item = await db.scalar(select(Skill).where(Skill.slug == slug))
            return get_skill_data_dir() / item.dir_path
    finally:
        await engine.dispose()


def skill_zip(*, description="uploaded", dependency=None):
    """构造实际 ZIP，包含根说明与辅助文件。"""
    result = BytesIO()
    metadata = f"---\nslug: imported\nname: Imported\ndescription: {description}\n"
    if dependency:
        metadata += f"skill_dependencies:\n- {dependency}\n"
    with ZipFile(result, "w") as archive:
        archive.writestr("bundle/SKILL.md", metadata + "---\n# Guide\n")
        archive.writestr("bundle/references/note.md", "reference-before")
        archive.writestr("bundle/scripts/run.py", "print('bound')\n")
    return result.getvalue()


async def test_bound_skill_creation_upload_revision_visibility_and_delete(test_client, admin_headers, standard_user):
    """并发创建唯一绑定，拒绝 ZIP 覆盖，编辑与删除资产可回读。"""
    agent_slug = f"pytest_bound_{uuid.uuid4().hex[:10]}"
    created = await test_client.post("/api/agent", headers=admin_headers, json={"name": "n" * 100, "slug": agent_slug})
    assert created.status_code == 200, created.text
    assert created.json()["agent"]["agent_id"] == agent_slug
    skill_slug = None
    try:
        absent = await test_client.get(f"/api/agent/{agent_slug}/self-skill", headers=admin_headers)
        assert absent.json()["skill"] is None
        bindings = await asyncio.gather(*[test_client.post(f"/api/agent/{agent_slug}/self-skill", headers=admin_headers) for _ in range(2)])
        assert all(result.status_code == 200 for result in bindings), [result.text for result in bindings]
        skill_slug = bindings[0].json()["skill"]["slug"]
        assert re.fullmatch(r"self-[0-9a-f]{8}", skill_slug)
        assert bindings[1].json()["skill"]["slug"] == skill_slug
        revision = bindings[0].json()["revision"]
        assert bindings[0].json()["skill"]["bound_agent_id"] is not None
        assert len(bindings[0].json()["skill"]["name"]) <= 128
        assert "share_config" not in bindings[0].json()["skill"]
        for route in ("/api/skills", "/api/system/skills", "/api/skills/accessible"):
            listed = await test_client.get(route, headers=admin_headers)
            assert listed.status_code == 200, listed.text
            assert skill_slug not in str(listed.json())
        options = await test_client.get("/api/system/skills/dependency-options", headers=admin_headers)
        assert skill_slug not in options.json()["data"]["skills"]
        detail = await test_client.get(f"/api/system/skills/{skill_slug}", headers=admin_headers)
        assert detail.status_code == 200 and detail.json()["data"]["source_scope"] == "agent_bound"
        for method, suffix, payload in (
            ("put", "/enabled", {"enabled": False}),
            ("put", "/share-config", {"share_config": None}),
            ("delete", "", None),
        ):
            result = await test_client.request(method, f"/api/system/skills/{skill_slug}{suffix}", headers=admin_headers, json=payload)
            assert result.status_code == 400, result.text
            assert "专属 Skill" in result.text
        for method, path, payload in (
            ("get", f"/api/system/skills/{skill_slug}/file?path=SKILL.md", None),
            ("post", f"/api/agent/{agent_slug}/self-skill", None),
        ):
            result = await test_client.request(method, path, headers=standard_user["headers"], json=payload)
            assert result.status_code in {403, 404}, result.text
        uploaded = await test_client.post(
            f"/api/agent/{agent_slug}/self-skill/upload",
            headers=admin_headers,
            data={"expected_revision": revision},
            files={"file": ("skill.zip", skill_zip(), "application/zip")},
        )
        assert uploaded.status_code == 409, uploaded.text
        assert "已存在专属 Skill" in uploaded.text
        saved = await test_client.put(
            f"/api/system/skills/{skill_slug}/content",
            headers=admin_headers,
            json={
                "expected_revision": revision,
                "changes": [
                    {"action": "create", "path": "references/note.md", "content": "reference-before"},
                    {"action": "create", "path": "scripts/run.py", "content": "print('bound')\n"},
                ],
            },
        )
        assert saved.status_code == 200, saved.text
        old_revision = saved.json()["data"]["revision"]
        read = await test_client.get(f"/api/system/skills/{skill_slug}/file?path=references/note.md", headers=admin_headers)
        edited = await test_client.put(
            f"/api/system/skills/{skill_slug}/file",
            headers=admin_headers,
            json={
                "path": "references/note.md",
                "content": "reference-after",
                "expected_revision": read.json()["data"]["revision"],
            },
        )
        assert edited.status_code == 200, edited.text
        stale = await test_client.post(
            f"/api/agent/{agent_slug}/self-skill/upload",
            headers=admin_headers,
            data={"expected_revision": old_revision},
            files={"file": ("skill.zip", skill_zip(), "application/zip")},
        )
        assert stale.status_code == 409, stale.text
        source = await current_content(skill_slug)
        assert source.joinpath("references/note.md").read_text() == "reference-after"
        binding = await test_client.get(f"/api/agent/{agent_slug}/self-skill", headers=admin_headers)
        assert binding.json()["revision"] != old_revision
        rejected = await test_client.post(
            f"/api/agent/{agent_slug}/self-skill/upload",
            headers=admin_headers,
            data={"expected_revision": binding.json()["revision"]},
            files={"file": ("skill.zip", skill_zip(dependency=skill_slug), "application/zip")},
        )
        assert rejected.status_code == 409, rejected.text
        assert source.joinpath("references/note.md").read_text() == "reference-after"
        prepared = await test_client.post(
            "/api/skills/import/prepare",
            headers=admin_headers,
            files={"file": ("ordinary.zip", skill_zip(dependency=skill_slug), "application/zip")},
        )
        assert prepared.status_code == 200, prepared.text
        installed = await test_client.post(
            f"/api/skills/install-drafts/{prepared.json()['data']['draft_id']}/confirm",
            headers=admin_headers,
            json={"share_config": None},
        )
        assert installed.status_code == 200, installed.text
        assert installed.json()["data"][0]["success"] is False
        assert "skill 依赖" in installed.json()["data"][0]["error"]
        discarded = await test_client.delete(
            f"/api/skills/install-drafts/{prepared.json()['data']['draft_id']}",
            headers=admin_headers,
        )
        assert discarded.status_code == 200, discarded.text
        exported = await test_client.get(f"/api/system/skills/{skill_slug}/export", headers=admin_headers)
        assert exported.status_code == 200, exported.text
        with ZipFile(BytesIO(exported.content)) as archive:
            assert any(name.endswith("references/note.md") for name in archive.namelist())
        engine = create_async_engine(os.environ["POSTGRES_URL"])
        try:
            async with async_sessionmaker(engine)() as db:
                row = (await db.execute(select(Skill).where(Skill.slug == skill_slug))).scalar_one()
                assert row.content_hash == binding.json()["revision"]
                assert row.bound_agent.slug == agent_slug
                count = await db.scalar(text("SELECT count(*) FROM skills WHERE bound_agent_id = :id"), {"id": row.bound_agent_id})
                assert count == 1
        finally:
            await engine.dispose()
    finally:
        deleted = await test_client.delete(f"/api/agent/{agent_slug}", headers=admin_headers)
        assert deleted.status_code in {200, 404}, deleted.text
    assert not any((get_skill_data_dir() / "packages" / skill_slug).iterdir())
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        async with async_sessionmaker(engine)() as db:
            assert await db.scalar(select(Skill.id).where(Skill.slug == skill_slug)) is None
    finally:
        await engine.dispose()


async def test_bound_permission_follows_agent_and_removes_revoked_projection(test_client, admin_headers, standard_user):
    """私有定义原地共享后绑定可读，撤权后文件接口与用户投影共同拒绝。"""
    slug = f"pytest-bound-policy-{uuid.uuid4().hex[:8]}"
    user_uid = standard_user["user"]["uid"]
    grants = {
        "version": 2,
        "read_scope": {"access_level": "user", "user_uids": [user_uid], "department_ids": []},
        "manage_scope": None,
    }
    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={"slug": slug, "name": slug},
    )
    assert created.status_code == 200, created.text
    try:
        bound = await test_client.post(f"/api/agent/{slug}/self-skill", headers=admin_headers)
        assert bound.status_code == 200, bound.text
        skill_slug = bound.json()["skill"]["slug"]
        denied = await test_client.get(f"/api/agent/{slug}/self-skill", headers=standard_user["headers"])
        assert denied.status_code == 404, denied.text
        before = await current_content(skill_slug)
        shared = await test_client.put(f"/api/agent/{slug}", headers=admin_headers, json={"visibility": "shared", "share_config": grants})
        assert shared.status_code == 200, shared.text
        assert shared.json()["agent"]["id"] == created.json()["agent"]["id"]
        readable = await test_client.get(f"/api/agent/{slug}/self-skill", headers=standard_user["headers"])
        assert readable.status_code == 200, readable.text
        assert readable.json()["skill"]["slug"] == skill_slug
        assert readable.json()["revision"] == bound.json()["revision"]
        assert await current_content(skill_slug) == before
        await refresh_user_skill_projection_async(user_uid)
        projection = get_user_skills_root_dir(user_uid) / skill_slug
        assert projection.joinpath("SKILL.md").is_file()
        denied = await test_client.put(
            f"/api/system/skills/{skill_slug}/file",
            headers=standard_user["headers"],
            json={
                "path": "SKILL.md",
                "content": "denied",
                "expected_revision": hashlib.sha256(projection.joinpath("SKILL.md").read_bytes()).hexdigest(),
            },
        )
        assert denied.status_code in {403, 404}, denied.text
        revoked = await test_client.put(
            f"/api/agent/{slug}",
            headers=admin_headers,
            json={"share_config": {"version": 2, "read_scope": None, "manage_scope": None}},
        )
        assert revoked.status_code == 200, revoked.text
        assert not projection.exists()
        for path in (
            f"/api/agent/{slug}/self-skill",
            f"/api/system/skills/{skill_slug}/file?path=SKILL.md",
            f"/api/system/skills/{skill_slug}",
        ):
            result = await test_client.get(path, headers=standard_user["headers"])
            assert result.status_code == 404, result.text
    finally:
        deleted = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        assert deleted.status_code in {200, 404}, deleted.text


async def test_private_agent_sharing_rolls_back_when_bound_dependency_is_restricted(test_client, admin_headers):
    """扩大共享范围不能绕过专属依赖授权，失败保留定义和绑定内容。"""
    from yuxi.modules.agents.models.definitions import Agent

    suffix = uuid.uuid4().hex[:8]
    agent_slug, dependency_slug = f"pytest-promote-{suffix}", f"pytest-restricted-{suffix}"
    uid = (await test_client.get("/api/auth/me", headers=admin_headers)).json()["uid"]
    limited = {"version": 2, "read_scope": {"access_level": "user", "user_uids": [uid]}, "manage_scope": None}
    source = f"---\nslug: {dependency_slug}\nname: Restricted\ndescription: limited\n---\n# Private guide\n"
    prepared = await test_client.post(
        "/api/skills/import/prepare",
        headers=admin_headers,
        files={"file": ("SKILL.md", source.encode(), "text/markdown")},
    )
    assert prepared.status_code == 200, prepared.text
    confirmed = await test_client.post(
        f"/api/skills/install-drafts/{prepared.json()['data']['draft_id']}/confirm",
        headers=admin_headers,
        json={"slugs": [dependency_slug], "share_config": limited},
    )
    assert confirmed.status_code == 200 and confirmed.json()["data"][0]["success"], confirmed.text
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        created = await test_client.post("/api/agent", headers=admin_headers, json={"name": agent_slug, "slug": agent_slug})
        assert created.status_code == 200, created.text
        uploaded = await test_client.post(
            f"/api/agent/{agent_slug}/self-skill/upload",
            headers=admin_headers,
            files={"file": ("skill.zip", skill_zip(dependency=dependency_slug), "application/zip")},
        )
        assert uploaded.status_code == 200, uploaded.text
        skill_slug = uploaded.json()["skill"]["slug"]
        before = await current_content(skill_slug)
        original_content = before.joinpath("SKILL.md").read_bytes()
        shared = await test_client.put(
            f"/api/agent/{agent_slug}",
            headers=admin_headers,
            json={
                "visibility": "shared",
                "name": "must roll back",
                "share_config": {
                    "version": 2,
                    "read_scope": {"access_level": "global"},
                    "manage_scope": None,
                },
            },
        )
        assert shared.status_code == 422 and "权限范围不匹配" in shared.text, shared.text
        async with async_sessionmaker(engine)() as db:
            agent = await db.scalar(select(Agent).where(Agent.slug == agent_slug))
            skill = await db.scalar(select(Skill).where(Skill.slug == skill_slug))
            assert (agent.id, agent.name, agent.created_by, agent.visibility) == (
                created.json()["agent"]["id"],
                agent_slug,
                uid,
                "private",
            )
            assert agent.share_config == {"version": 2, "read_scope": None, "manage_scope": None}
            assert skill.bound_agent_id == agent.id and skill.skill_dependencies == [dependency_slug]
            assert get_skill_data_dir() / skill.dir_path == before
            assert skill.content_hash == uploaded.json()["revision"]
        assert before.joinpath("SKILL.md").read_bytes() == original_content
    finally:
        deleted = await test_client.delete(f"/api/agent/{agent_slug}", headers=admin_headers)
        assert deleted.status_code in {200, 404}, deleted.text
        deleted = await test_client.delete(f"/api/system/skills/{dependency_slug}", headers=admin_headers)
        assert deleted.status_code in {200, 404}, deleted.text
        await engine.dispose()


async def test_bound_runtime_uses_app_agent_visibility_without_management(test_client, admin_headers, standard_user):
    """Key 所属账号的显式范围允许 APP 自动加载绑定，不借用管理员治理权。"""
    from sqlalchemy import delete
    from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
    from yuxi.modules.extensions.skills.runtime import resolve_runtime_skills_for_context
    from yuxi.modules.identity.models import User
    from yuxi.modules.identity.permissions import ResourcePermission, resolve_skill_permission
    from yuxi.modules.identity.repositories.users import UserRepository

    slug = f"pytest-bound-app-{uuid.uuid4().hex[:8]}"
    app_id = f"pytest-bound-{uuid.uuid4().hex}"
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    owner_uid = me.json()["uid"]
    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={
            "slug": slug,
            "name": slug,
            "visibility": "shared",
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": [owner_uid], "department_ids": []},
                "manage_scope": None,
            },
        },
    )
    assert created.status_code == 200, created.text
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        bound = await test_client.post(f"/api/agent/{slug}/self-skill", headers=admin_headers)
        assert bound.status_code == 200, bound.text
        skill_slug = bound.json()["skill"]["slug"]
        async with factory() as db:
            owner = await db.scalar(select(User).where(User.uid == owner_uid))
            other_owner = await db.scalar(select(User).where(User.uid == standard_user["user"]["uid"]))
            repo = UserRepository(db)
            end_user = await repo.get_or_create_public_end_user(owner=owner, app_id=app_id, end_user_id="allowed")
            denied_user = await repo.get_or_create_public_end_user(owner=other_owner, app_id=app_id, end_user_id="denied")
            allowed_uid, denied_uid = end_user.uid, denied_user.uid
            await db.commit()
        async with factory() as db:
            user = await db.scalar(select(User).where(User.uid == allowed_uid))
            context = ChatBotContext(uid=user.uid, agent_slug=slug, skills=[], preload_skills=[])
            scope = await resolve_runtime_skills_for_context(context, db=db, user=user)
            assert scope["context_preload_skills"] == [skill_slug]
            item = await db.scalar(select(Skill).where(Skill.slug == skill_slug))
            assert resolve_skill_permission(user, item) == ResourcePermission.READ
        await refresh_user_skill_projection_async(allowed_uid)
        assert get_user_skills_root_dir(allowed_uid).joinpath(skill_slug, "SKILL.md").is_file()
        async with factory() as db:
            user = await db.scalar(select(User).where(User.uid == denied_uid))
            context = ChatBotContext(uid=user.uid, agent_slug=slug, skills=[], preload_skills=[])
            with pytest.raises(PermissionError, match="无权限访问"):
                await resolve_runtime_skills_for_context(context, db=db, user=user)
    finally:
        deleted = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        assert deleted.status_code in {200, 404}, deleted.text
        async with factory() as db:
            await db.execute(delete(User).where(User.app_id == app_id))
            await db.commit()
        await engine.dispose()


async def test_binding_added_after_preparation_waits_for_next_context(test_client, admin_headers):
    """运行准备后新增绑定不改写旧范围，下一执行准备自动加载。"""
    from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
    from yuxi.modules.extensions.skills.runtime import resolve_runtime_skills_for_context
    from yuxi.modules.identity.models import User

    slug = f"pytest-bound-late-{uuid.uuid4().hex[:8]}"
    created = await test_client.post("/api/agent", headers=admin_headers, json={"slug": slug, "name": slug})
    assert created.status_code == 200, created.text
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.scalar(select(User).where(User.uid == me.json()["uid"]))
            current = ChatBotContext(uid=user.uid, agent_slug=slug, skills=[], preload_skills=[])
            current.skill_runtime_snapshot = await resolve_runtime_skills_for_context(current, db=db, user=user)
        bound = await test_client.post(f"/api/agent/{slug}/self-skill", headers=admin_headers)
        assert bound.status_code == 200, bound.text
        skill_slug = bound.json()["skill"]["slug"]
        async with factory() as db:
            user = await db.scalar(select(User).where(User.uid == me.json()["uid"]))
            unchanged = await resolve_runtime_skills_for_context(current, db=db, user=user)
            assert skill_slug not in unchanged["preloaded_skills"]
            assert skill_slug not in unchanged["effective_skills"]
        async with factory() as db:
            user = await db.scalar(select(User).where(User.uid == me.json()["uid"]))
            following = ChatBotContext(uid=user.uid, agent_slug=slug, skills=[], preload_skills=[])
            refreshed = await resolve_runtime_skills_for_context(following, db=db, user=user)
            assert refreshed["preloaded_skills"] == [skill_slug]
            assert refreshed["preloaded_skill_contents"][skill_slug] == (await current_content(skill_slug)).joinpath("SKILL.md").read_text()
    finally:
        deleted = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        assert deleted.status_code in {200, 404}, deleted.text
        await engine.dispose()


async def test_package_compensation_respects_commit_point(test_client, admin_headers, monkeypatch):
    """提交前失败恢复文件，提交后清理失败保留已持久化的包。"""
    from yuxi.modules.extensions.skills import content as content_service
    from yuxi.modules.identity.models import User

    slug = f"pytest-bound-compensation-{uuid.uuid4().hex[:8]}"
    created = await test_client.post("/api/agent", headers=admin_headers, json={"slug": slug, "name": slug})
    assert created.status_code == 200, created.text
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        initial = await test_client.post(f"/api/agent/{slug}/self-skill", headers=admin_headers)
        assert initial.status_code == 200, initial.text
        skill_slug, revision = initial.json()["skill"]["slug"], initial.json()["revision"]
        source = await current_content(skill_slug)
        original = source.joinpath("SKILL.md").read_bytes()

        async def fail_commit():
            raise RuntimeError("injected commit failure")

        async with factory() as db:
            user = await db.scalar(select(User).where(User.uid == me.json()["uid"]))
            with monkeypatch.context() as patch:
                patch.setattr(db, "commit", fail_commit)
                with pytest.raises(RuntimeError, match="commit failure"):
                    await content_service.save_skill_content(
                        db,
                        slug=skill_slug,
                        changes=[{"action": "create", "path": "references/note.md", "content": "reference-before"}],
                        expected_revision=revision,
                        operator=user,
                    )
        assert source.joinpath("SKILL.md").read_bytes() == original
        assert not source.joinpath("references/note.md").exists()
        unchanged = await test_client.get(f"/api/agent/{slug}/self-skill", headers=admin_headers)
        assert unchanged.json()["revision"] == revision
        previous_content = source
        remove_directory = content_service.shutil.rmtree
        cleanup_failures = []

        def fail_previous_cleanup(path, *args, **kwargs):
            """在真实清理旧包时注入失败，其他临时目录仍正常回收。"""
            if path == previous_content:
                cleanup_failures.append(path)
                raise OSError("injected package cleanup failure")
            return remove_directory(path, *args, **kwargs)

        async with factory() as db:
            user = await db.scalar(select(User).where(User.uid == me.json()["uid"]))
            with monkeypatch.context() as patch:
                patch.setattr(content_service.shutil, "rmtree", fail_previous_cleanup)
                result = await content_service.save_skill_content(
                    db,
                    slug=skill_slug,
                    changes=[{"action": "create", "path": "references/note.md", "content": "reference-before"}],
                    expected_revision=revision,
                    operator=user,
                )
                assert result.skill.slug == skill_slug
        assert cleanup_failures == [previous_content]
        assert previous_content.joinpath("SKILL.md").read_bytes() == original
        source = await current_content(skill_slug)
        assert source != previous_content
        assert source.joinpath("references/note.md").read_text() == "reference-before"
        persisted = await test_client.get(f"/api/agent/{slug}/self-skill", headers=admin_headers)
        assert persisted.json()["revision"] != revision
        assert persisted.json()["skill"]["content_hash"] == persisted.json()["revision"]
    finally:
        deleted = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        assert deleted.status_code in {200, 404}, deleted.text
        await engine.dispose()


async def test_bound_zip_import_is_create_only(test_client, admin_headers):
    """并发首次导入只接受一份 ZIP，正确修订也不能覆盖已导入的文件。"""
    agent = f"pytest-bound-import-{uuid.uuid4().hex[:8]}"
    created = await test_client.post("/api/agent", headers=admin_headers, json={"slug": agent, "name": agent})
    assert created.status_code == 200, created.text
    try:
        results = await asyncio.gather(
            *[
                test_client.post(
                    f"/api/agent/{agent}/self-skill/upload",
                    headers=admin_headers,
                    files={"file": ("skill.zip", skill_zip(description=label), "application/zip")},
                )
                for label in ("first", "second")
            ]
        )
        assert sorted(result.status_code for result in results) == [200, 409]
        accepted = next(result.json() for result in results if result.status_code == 200)
        source = await current_content(accepted["skill"]["slug"])
        before = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
        rejected = await test_client.post(
            f"/api/agent/{agent}/self-skill/upload",
            headers=admin_headers,
            data={"expected_revision": accepted["revision"]},
            files={"file": ("skill.zip", skill_zip(description="replacement"), "application/zip")},
        )
        assert rejected.status_code == 409, rejected.text
        assert "已存在专属 Skill" in rejected.text
        latest = (await test_client.get(f"/api/agent/{agent}/self-skill", headers=admin_headers)).json()
        assert latest["revision"] == accepted["revision"]
        after = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
        assert after == before
    finally:
        deleted = await test_client.delete(f"/api/agent/{agent}", headers=admin_headers)
        assert deleted.status_code in {200, 404}, deleted.text


@pytest_asyncio.fixture
async def bound_version_skill(test_client, admin_headers):
    """建立真实绑定并在退出时清理 Agent。"""
    slug = f"pytest-version-{uuid.uuid4().hex[:8]}"
    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={
            "slug": slug,
            "name": slug,
            "visibility": "shared",
            "share_config": {"version": 2, "read_scope": None, "manage_scope": None},
        },
    )
    assert created.status_code == 200, created.text
    response = await test_client.post(f"/api/agent/{slug}/self-skill", headers=admin_headers)
    assert response.status_code == 200, response.text
    try:
        yield slug, response.json()["skill"]["slug"]
    finally:
        deleted = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        assert deleted.status_code in {200, 404}, deleted.text


async def test_save_release_restore_delete_complete_package(test_client, admin_headers, bound_version_skill):
    """完整保存与历史引用同一内容，恢复后删除该历史仍保留当前包。"""
    _, slug = bound_version_skill
    base = f"/api/system/skills/{slug}"
    before = (await test_client.get(f"{base}/content", headers=admin_headers)).json()["data"]
    saved = await test_client.put(
        f"{base}/content",
        headers=admin_headers,
        json={
            "expected_revision": before["revision"],
            "release": True,
            "changes": [
                {"action": "write", "path": "SKILL.md", "content": before["files"]["SKILL.md"] + "\nV1 root"},
                {"action": "create", "path": "scripts/run.py", "content": "print('v1')\n"},
                {"action": "create", "path": "references/note.md", "content": "v1 reference"},
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    label = saved.json()["data"]["published_version"]["version"]
    assert re.fullmatch(r"\d{8}-[0-9a-f]{8}", label)
    v1 = await current_content(slug)
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        async with async_sessionmaker(engine)() as db:
            item = await db.scalar(select(Skill).where(Skill.slug == slug))
            row = await db.scalar(select(SkillVersion).where(SkillVersion.skill_id == item.id))
            assert row.dir_path == item.dir_path
            assert row.content_hash == item.content_hash
        changed = await test_client.put(
            f"{base}/content",
            headers=admin_headers,
            json={
                "expected_revision": saved.json()["data"]["revision"],
                "changes": [
                    {"action": "write", "path": "scripts/run.py", "content": "print('v2')\n"},
                    {"action": "create", "path": "extra.md", "content": "unreleased"},
                ],
            },
        )
        assert changed.status_code == 200, changed.text
        assert v1.joinpath("scripts/run.py").read_text() == "print('v1')\n"
        stale = await test_client.post(
            f"{base}/versions/{label}/restore",
            headers=admin_headers,
            json={"expected_revision": saved.json()["data"]["revision"]},
        )
        assert stale.status_code == 409, stale.text
        restored = await test_client.post(
            f"{base}/versions/{label}/restore",
            headers=admin_headers,
            json={"expected_revision": changed.json()["data"]["revision"]},
        )
        assert restored.status_code == 200, restored.text
        assert await current_content(slug) == v1
        assert v1.joinpath("references/note.md").read_text() == "v1 reference"
        assert not v1.joinpath("extra.md").exists()
        deleted = await test_client.delete(f"{base}/versions/{label}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
        assert v1.joinpath("SKILL.md").read_text().endswith("V1 root")
        assert (await test_client.get(f"{base}/versions", headers=admin_headers)).json()["data"]["versions"] == []
        read = (await test_client.get(f"{base}/content", headers=admin_headers)).json()["data"]
        final = await test_client.put(
            f"{base}/content",
            headers=admin_headers,
            json={
                "expected_revision": read["revision"],
                "changes": [{"action": "write", "path": "SKILL.md", "content": read["files"]["SKILL.md"] + " updated"}],
            },
        )
        assert final.status_code == 200, final.text
        assert not v1.exists()
    finally:
        await engine.dispose()


async def test_save_release_commit_failure_keeps_old_reference(test_client, admin_headers, bound_version_skill, monkeypatch):
    """真实 PG 提交失败，旧引用和字节保持有效，新包与历史均不留下。"""
    from yuxi.modules.extensions.skills.content import save_skill_content
    from yuxi.modules.identity.models import User

    _, slug = bound_version_skill
    before = (await test_client.get(f"/api/system/skills/{slug}/content", headers=admin_headers)).json()["data"]
    original = await current_content(slug)
    original_bytes = original.joinpath("SKILL.md").read_bytes()
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.scalar(select(User).where(User.uid == me.json()["uid"]))

            async def fail_commit():
                """在已写入完整包后拒绝数据库提交。"""
                raise RuntimeError("injected commit failure")

            monkeypatch.setattr(db, "commit", fail_commit)
            with pytest.raises(RuntimeError, match="commit failure"):
                await save_skill_content(
                    db,
                    slug=slug,
                    operator=user,
                    expected_revision=before["revision"],
                    changes=[{"action": "write", "path": "SKILL.md", "content": before["files"]["SKILL.md"] + " changed"}],
                    release=True,
                )
        assert await current_content(slug) == original
        assert original.joinpath("SKILL.md").read_bytes() == original_bytes
        assert list(original.parent.iterdir()) == [original]
        async with factory() as db:
            item = await db.scalar(select(Skill).where(Skill.slug == slug))
            assert await db.scalar(select(SkillVersion.id).where(SkillVersion.skill_id == item.id)) is None
    finally:
        await engine.dispose()


async def test_reader_and_corrupt_history_cannot_restore(test_client, admin_headers, standard_user, bound_version_skill):
    """读取权限不能管理版本，历史字节损坏不能覆盖有效当前包。"""
    agent, slug = bound_version_skill
    base = f"/api/system/skills/{slug}"
    grants = {
        "version": 2,
        "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
        "manage_scope": None,
    }
    response = await test_client.put(f"/api/agent/{agent}", headers=admin_headers, json={"share_config": grants})
    assert response.status_code == 200, response.text
    before = (await test_client.get(f"{base}/content", headers=admin_headers)).json()["data"]
    published = await test_client.put(
        f"{base}/content",
        headers=admin_headers,
        json={"expected_revision": before["revision"], "changes": [], "release": True},
    )
    assert published.status_code == 200, published.text
    label = published.json()["data"]["published_version"]["version"]
    history = await current_content(slug)
    revision = published.json()["data"]["revision"]
    for method, path, payload in [
        ("post", f"/versions/{label}/restore", {"expected_revision": revision}),
        ("put", "/content", {"expected_revision": revision, "changes": [], "release": True}),
    ]:
        denied = await getattr(test_client, method)(base + path, headers=standard_user["headers"], json=payload)
        assert denied.status_code in {400, 403, 404}, denied.text
    saved = await test_client.put(
        f"{base}/content",
        headers=admin_headers,
        json={
            "expected_revision": revision,
            "changes": [{"action": "write", "path": "SKILL.md", "content": before["files"]["SKILL.md"] + " current"}],
        },
    )
    assert saved.status_code == 200, saved.text
    current = await current_content(slug)
    history.joinpath("SKILL.md").write_text("corrupt")
    rejected = await test_client.post(
        f"{base}/versions/{label}/restore",
        headers=admin_headers,
        json={"expected_revision": saved.json()["data"]["revision"]},
    )
    assert rejected.status_code == 400 and "损坏" in rejected.text, rejected.text
    assert current.joinpath("SKILL.md").read_text().endswith(" current")


async def test_concurrent_content_save_only_one_commits(test_client, admin_headers, bound_version_skill):
    """同一个完整编辑基准仅允许一笔提交成功，不形成混合包。"""
    _, slug = bound_version_skill
    base = f"/api/system/skills/{slug}"
    before = (await test_client.get(f"{base}/content", headers=admin_headers)).json()["data"]
    results = await asyncio.gather(
        *[
            test_client.put(
                f"{base}/content",
                headers=admin_headers,
                json={
                    "expected_revision": before["revision"],
                    "changes": [{"action": "write", "path": "SKILL.md", "content": before["files"]["SKILL.md"] + suffix}],
                },
            )
            for suffix in (" A", " B")
        ]
    )
    assert sorted(result.status_code for result in results) == [200, 409], [result.text for result in results]
    current = await current_content(slug)
    assert current.joinpath("SKILL.md").read_text().endswith((" A", " B"))
    assert list(current.parent.iterdir()) == [current]


async def test_content_save_does_not_refresh_all_users(test_client, admin_headers, bound_version_skill, monkeypatch):
    """普通内容变更保持现有投影，执行准备从当前持久引用刷新。"""
    from yuxi.modules.extensions.skills.content import save_skill_content
    from yuxi.modules.extensions.skills import projection
    from yuxi.modules.identity.models import User

    _, slug = bound_version_skill
    me = (await test_client.get("/api/auth/me", headers=admin_headers)).json()
    await projection.refresh_user_skill_projection_async(me["uid"])
    path = projection.get_user_skills_root_dir(me["uid"]) / slug / "SKILL.md"
    old = path.read_bytes()
    before = (await test_client.get(f"/api/system/skills/{slug}/content", headers=admin_headers)).json()["data"]
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            user = await db.scalar(select(User).where(User.uid == me["uid"]))

            async def forbidden(*args):
                """内容保存不得触发全用户投影用例。"""
                raise AssertionError("content save invoked global projection")

            with monkeypatch.context() as patch:
                patch.setattr(projection, "invalidate_skill_projections", forbidden)
                patch.setattr(projection, "refresh_skill_projections", forbidden)
                await save_skill_content(
                    db,
                    slug=slug,
                    operator=user,
                    expected_revision=before["revision"],
                    changes=[{"action": "write", "path": "SKILL.md", "content": before["files"]["SKILL.md"] + " next"}],
                )
        assert path.read_bytes() == old
        await projection.refresh_user_skill_projection_async(me["uid"])
        assert path.read_text().endswith(" next")
    finally:
        await engine.dispose()


async def test_restore_revalidates_dependencies_against_current_agent_scope(test_client, admin_headers, standard_user, bound_version_skill):
    """latest 移除依赖后扩大 Agent 授权，旧快照不得恢复不覆盖新范围的依赖。"""
    agent_slug, slug = bound_version_skill
    base = f"/api/system/skills/{slug}"
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    dep_slug = f"pytest-version-dep-{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    dep_source = get_skills_root_dir() / dep_slug
    dep_source.mkdir()
    dep_source.joinpath("SKILL.md").write_text(f"---\nslug: {dep_slug}\nname: dependency\ndescription: test\n---\n")
    try:
        async with factory() as db:
            db.add(
                Skill(
                    slug=dep_slug,
                    name=dep_slug,
                    description="dependency",
                    source_type="upload",
                    dir_path=f"shared/{dep_slug}",
                    share_config={
                        "version": 2,
                        "read_scope": {"access_level": "user", "user_uids": [me.json()["uid"]], "department_ids": []},
                        "manage_scope": None,
                    },
                    created_by=me.json()["uid"],
                    enabled=True,
                )
            )
            await db.commit()
        saved = await test_client.put(
            f"{base}/content",
            headers=admin_headers,
            json={
                "changes": [{"action": "dependencies", "skill_dependencies": [dep_slug]}],
                "expected_revision": (await test_client.get(f"{base}/content", headers=admin_headers)).json()["data"]["revision"],
                "release": True,
            },
        )
        assert saved.status_code == 200, saved.text
        label = saved.json()["data"]["published_version"]["version"]
        removed = await test_client.put(
            f"{base}/dependencies",
            headers=admin_headers,
            json={
                "skill_dependencies": [],
                "expected_revision": (await test_client.get(f"{base}/file?path=SKILL.md", headers=admin_headers)).json()["data"][
                    "revision"
                ],
            },
        )
        assert removed.status_code == 200, removed.text
        grants = {
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": [standard_user["user"]["uid"]], "department_ids": []},
            "manage_scope": None,
        }
        published = await test_client.put(f"/api/agent/{agent_slug}", headers=admin_headers, json={"share_config": grants})
        assert published.status_code == 200, published.text
        listed = await test_client.get(f"{base}/versions", headers=admin_headers)
        source = (await current_content(slug)) / "SKILL.md"
        before = source.read_bytes()
        rejected = await test_client.post(
            f"{base}/versions/{label}/restore",
            headers=admin_headers,
            json={"expected_revision": listed.json()["data"]["revision"]},
        )
        assert rejected.status_code == 400, rejected.text
        assert dep_slug in rejected.text
        assert source.read_bytes() == before
        detail = await test_client.get(base, headers=admin_headers)
        assert detail.json()["data"]["skill_dependencies"] == []
    finally:
        async with factory() as db:
            dep = await db.scalar(select(Skill).where(Skill.slug == dep_slug))
            await db.delete(dep)
            await db.commit()
        shutil.rmtree(dep_source)
        await engine.dispose()


async def test_release_collision_preserves_existing_history(test_client, admin_headers, bound_version_skill, monkeypatch):
    """相同版本标识被唯一约束拒绝，不删除已发布内容。"""
    from types import SimpleNamespace
    from yuxi.modules.extensions.skills import content
    from yuxi.modules.identity.models import User

    _, slug = bound_version_skill
    data = (await test_client.get(f"/api/system/skills/{slug}/content", headers=admin_headers)).json()["data"]
    me = (await test_client.get("/api/auth/me", headers=admin_headers)).json()
    path = await current_content(slug)
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        with monkeypatch.context() as patch:
            patch.setattr(content.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
            for attempt in range(2):
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    user = await db.scalar(select(User).where(User.uid == me["uid"]))
                    if attempt == 0:
                        result = await content.save_skill_content(
                            db, slug=slug, operator=user, expected_revision=data["revision"], changes=[], release=True
                        )
                        label = result.published_version["version"]
                    else:
                        with pytest.raises(content.SkillEditConflict, match="标识冲突"):
                            await content.save_skill_content(
                                db,
                                slug=slug,
                                operator=user,
                                expected_revision=data["revision"],
                                changes=[],
                                release=True,
                            )
        async with async_sessionmaker(engine)() as db:
            item = await db.scalar(select(Skill).where(Skill.slug == slug))
            rows = list((await db.scalars(select(SkillVersion).where(SkillVersion.skill_id == item.id))).all())
            assert len(rows) == 1 and rows[0].version == label
        assert path.joinpath("SKILL.md").read_text() == data["files"]["SKILL.md"]
    finally:
        await engine.dispose()


async def test_committed_response_survives_following_save_prune(test_client, admin_headers, bound_version_skill, monkeypatch):
    """暂停首笔提交后，让第二笔保存清理旧包，首笔仍准确返回成功。"""
    from yuxi.modules.extensions.skills import content
    from yuxi.modules.identity.models import User

    _, slug = bound_version_skill
    data = (await test_client.get(f"/api/system/skills/{slug}/content", headers=admin_headers)).json()["data"]
    me = (await test_client.get("/api/auth/me", headers=admin_headers)).json()
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    first_committed, second_finished = asyncio.Event(), asyncio.Event()
    first_path = None
    original_prune = content.prune_skill_content

    async def pause_first_prune(db_to_prune, value):
        """首笔已经提交但尚未返回，将调度权交给后续保存。"""
        nonlocal first_path
        if not first_committed.is_set():
            first_path = await current_content(slug)
            first_committed.set()
            await asyncio.wait_for(second_finished.wait(), timeout=20)
        await original_prune(db_to_prune, value)

    async def save_first():
        """通过真实编辑用例写入首笔内容。"""
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            user = await db.scalar(select(User).where(User.uid == me["uid"]))
            return await content.save_skill_content(
                db,
                slug=slug,
                operator=user,
                changes=[{"action": "write", "path": "SKILL.md", "content": data["files"]["SKILL.md"] + " first"}],
                expected_revision=data["revision"],
            )

    async def save_following():
        """在首笔提交之后提交新根，并清理不再引用的首笔包。"""
        await asyncio.wait_for(first_committed.wait(), timeout=20)
        try:
            latest = (await test_client.get(f"/api/system/skills/{slug}/content", headers=admin_headers)).json()["data"]
            response = await test_client.put(
                f"/api/system/skills/{slug}/content",
                headers=admin_headers,
                json={
                    "expected_revision": latest["revision"],
                    "changes": [{"action": "write", "path": "SKILL.md", "content": latest["files"]["SKILL.md"] + " following"}],
                },
            )
            assert response.status_code == 200, response.text
        finally:
            second_finished.set()

    try:
        # 第二笔由 API 独立进程执行，补丁只暂停首笔用例。
        with monkeypatch.context() as patch:
            patch.setattr(content, "prune_skill_content", pause_first_prune)
            result, _ = await asyncio.gather(save_first(), save_following())
        assert result.skill.slug == slug
        assert result.revision
        assert not first_path.exists()
        assert (await current_content(slug)).joinpath("SKILL.md").read_text().endswith(" following")
    finally:
        await engine.dispose()
