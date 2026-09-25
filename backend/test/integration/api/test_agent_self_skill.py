"""Agent 专属技能的绑定、权限派生、隐藏与生命周期。"""

from __future__ import annotations

import io
import json
import os
import uuid
import zipfile

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.skills import service as skill_service
from yuxi.agents.skills.service import (
    SELF_SKILL_SLUG_SUFFIX,
    list_accessible_skills,
    list_skill_cards_for_user,
)
from yuxi.permissions import ResourcePermission
from yuxi.storage.postgres.models_business import Agent, Skill, User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _skill_md(slug: str, name: str, description: str) -> str:
    """生成一份最小可用 SKILL.md。"""
    return f"---\nslug: {slug}\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


def _user_scope(uid: str) -> dict:
    return {"access_level": "user", "department_ids": [], "user_uids": [uid]}


def _user_read_scope() -> dict:
    """全局可读、无管理范围的 v2 共享配置。"""
    return {
        "version": 2,
        "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
        "manage_scope": None,
    }


async def created_admin_uid(dsn: str) -> str:
    """返回初始化管理员的 uid，用于把 Agent 限定为仅创建者可见。"""
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow("SELECT uid FROM users WHERE role = 'superadmin' ORDER BY id LIMIT 1")
        assert row is not None
        return str(row["uid"])
    finally:
        await conn.close()


async def _read_skill_row(dsn: str, slug: str) -> dict | None:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT slug, bound_agent_id, enabled, share_config FROM skills WHERE slug = $1", slug
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def test_create_agent_with_remote_mcp_and_uploaded_skill(test_client, admin_headers, standard_user):
    """创建时选定的 MCP 与 ZIP 最终分别绑定到 Agent 配置和专属 Skill。"""
    suffix = uuid.uuid4().hex[:10]
    agent_slug = f"pytest-create-resources-{suffix}"
    mcp_slug = f"pytest-create-mcp-{suffix}"
    skill_slug = f"{agent_slug}{SELF_SKILL_SLUG_SUFFIX}"
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "")

    denied = await test_client.post(
        "/api/system/mcp-servers",
        headers=standard_user["headers"],
        json={"slug": mcp_slug, "name": mcp_slug, "transport": "streamable_http", "url": "https://example.com/mcp"},
    )
    assert denied.status_code == 403, denied.text

    try:
        mcp = await test_client.post(
            "/api/system/mcp-servers",
            headers=admin_headers,
            json={"slug": mcp_slug, "name": mcp_slug, "transport": "streamable_http", "url": "https://example.com/mcp"},
        )
        assert mcp.status_code == 200, mcp.text

        agent = await test_client.post(
            "/api/agent",
            headers=admin_headers,
            json={
                "name": "Imported resources",
                "slug": agent_slug,
                "backend_id": "ChatbotAgent",
                "config_json": {"context": {"mcps": [mcp_slug]}},
            },
        )
        assert agent.status_code == 200, agent.text

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("sample/SKILL.md", _skill_md("ignored-package-slug", "Imported Skill", "Imported rules"))
        uploaded = await test_client.post(
            f"/api/agent/{agent_slug}/self-skill/upload",
            headers=admin_headers,
            files={"file": ("skill.zip", archive.getvalue(), "application/zip")},
        )
        assert uploaded.status_code == 200, uploaded.text

        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT a.id, a.config_json, s.bound_agent_id FROM agents a "
                "JOIN skills s ON s.bound_agent_id = a.id WHERE a.slug = $1 AND s.slug = $2",
                agent_slug,
                skill_slug,
            )
            assert row is not None
            assert row["bound_agent_id"] == row["id"]
            assert json.loads(row["config_json"])["context"]["mcps"] == [mcp_slug]
            mcp_row = await conn.fetchrow("SELECT transport, url FROM mcp_servers WHERE slug = $1", mcp_slug)
            assert mcp_row is not None
            assert mcp_row["transport"] == "streamable_http"
            assert mcp_row["url"] == "https://example.com/mcp"
        finally:
            await conn.close()

        content = await test_client.get(
            f"/api/system/skills/{skill_slug}/file", headers=admin_headers, params={"path": "SKILL.md"}
        )
        assert content.status_code == 200, content.text
        assert "# Imported Skill" in content.json()["data"]["content"]
    finally:
        await test_client.delete(f"/api/agent/{agent_slug}", headers=admin_headers)
        await test_client.delete(f"/api/system/mcp-servers/{mcp_slug}", headers=admin_headers)


@pytest.mark.asyncio
async def test_agent_self_skill_is_bound_hidden_and_force_preloaded(
    test_client, admin_headers, standard_user, tmp_path
):
    """绑定 Skill 入库后：不进配置选择器、不进普通列表、权限跟随 Agent。"""
    slug = f"pytest-self-skill-{uuid.uuid4().hex[:10]}"
    bound_slug = f"{slug}{SELF_SKILL_SLUG_SUFFIX}"
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "")

    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={
            "name": "Pytest self skill agent",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "share_config": _user_read_scope(),
            "config_json": {"context": {"skills": [], "preload_skills": []}},
        },
    )
    assert created.status_code == 200, created.text

    # Agent 创建本身不产生空 self-skill 行。
    assert await _read_skill_row(dsn, bound_slug) is None

    created = await test_client.post(f"/api/agent/{slug}/self-skill", headers=admin_headers)
    assert created.status_code == 200, created.text
    assert created.json()["slug"] == bound_slug
    # 内容管理复用普通 Skill 的文件入口，不提供平行的 self-skill 编辑器。
    saved = await test_client.put(
        f"/api/system/skills/{bound_slug}/file",
        headers=admin_headers,
        json={"path": "SKILL.md", "content": _skill_md(bound_slug, "MySQL Reader", "只读 MySQL")},
    )
    assert saved.status_code == 200, saved.text

    row = await _read_skill_row(dsn, bound_slug)
    assert row is not None
    assert row["bound_agent_id"] is not None
    # 绑定 Skill 不维护独立共享语义，权限在读取时派生。
    assert json.loads(row["share_config"])["manage_scope"] is None

    # 普通 Skill 选择器与依赖列表都不包含绑定 Skill。
    options = await test_client.get(f"/api/agent/{slug}", headers=admin_headers)
    assert options.status_code == 200, options.text
    skill_options = [
        item["key"]
        for group in options.json()["agent"].get("configurable_items", {}).values()
        if isinstance(group, dict) and group.get("kind") == "skills"
        for item in group.get("options", [])
    ]
    assert bound_slug not in skill_options
    # 选择器本身有内容，避免断言因空列表而恒真。
    assert "mysql-reporter" in skill_options

    cards = await test_client.get("/api/system/skills", headers=admin_headers)
    assert cards.status_code == 200, cards.text
    card_slugs = {item["slug"] for item in cards.json()["data"]}
    # 普通管理列表默认隐藏绑定 Skill，即使管理员能管理该 Agent。
    assert bound_slug not in card_slugs
    assert "mysql-reporter" in card_slugs


@pytest.mark.asyncio
async def test_bound_skill_reuses_skill_management_surface(test_client, admin_headers):
    """绑定 Skill 通过普通 slug 接口获得完整管理能力：文件树、文件 CRUD、依赖。"""
    slug = f"pytest-self-surface-{uuid.uuid4().hex[:10]}"
    bound_slug = f"{slug}{SELF_SKILL_SLUG_SUFFIX}"
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "")

    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={
            "name": "Pytest self surface",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "share_config": _user_read_scope(),
        },
    )
    assert created.status_code == 200, created.text
    assert (await test_client.post(f"/api/agent/{slug}/self-skill", headers=admin_headers)).status_code == 200

    # 单 Skill 查询允许返回绑定 Skill，否则管理页无法按 slug 打开。
    detail = await test_client.get(f"/api/system/skills/{bound_slug}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["is_agent_bound"] is True
    assert detail.json()["data"]["can_manage"] is True

    # 文件树与文件 CRUD 复用普通 Skill 入口。
    tree = await test_client.get(f"/api/system/skills/{bound_slug}/tree", headers=admin_headers)
    assert tree.status_code == 200, tree.text
    made = await test_client.post(
        f"/api/system/skills/{bound_slug}/file",
        headers=admin_headers,
        json={"path": "references/notes.md", "is_dir": False, "content": "# notes\n"},
    )
    assert made.status_code == 200, made.text
    read = await test_client.get(
        f"/api/system/skills/{bound_slug}/file", headers=admin_headers, params={"path": "references/notes.md"}
    )
    assert read.status_code == 200, read.text
    assert read.json()["data"]["content"] == "# notes\n"

    # 依赖编辑同样可用。
    deps = await test_client.put(
        f"/api/system/skills/{bound_slug}/dependencies",
        headers=admin_headers,
        json={"tool_dependencies": [], "mcp_dependencies": [], "skill_dependencies": ["mysql-reporter"]},
    )
    assert deps.status_code == 200, deps.text
    row = await _read_skill_row(dsn, bound_slug)
    assert row is not None

    removed = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
    assert removed.status_code == 200, removed.text
    assert await _read_skill_row(dsn, bound_slug) is None


@pytest.mark.asyncio
async def test_agent_self_skill_guards_reject_share_enable_and_delete(test_client, admin_headers, standard_user):
    """绑定 Skill 不允许单独共享、停用或删除。"""
    slug = f"pytest-self-guard-{uuid.uuid4().hex[:10]}"
    bound_slug = f"{slug}{SELF_SKILL_SLUG_SUFFIX}"

    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={
            "name": "Pytest self guard",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "share_config": _user_read_scope(),
        },
    )
    assert created.status_code == 200, created.text
    created = await test_client.post(f"/api/agent/{slug}/self-skill", headers=admin_headers)
    assert created.status_code == 200, created.text
    saved = await test_client.put(
        f"/api/system/skills/{bound_slug}/file",
        headers=admin_headers,
        json={"path": "SKILL.md", "content": _skill_md(bound_slug, "Guard", "guard")},
    )
    assert saved.status_code == 200, saved.text

    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "")
    before = await _read_skill_row(dsn, bound_slug)

    shared = await test_client.put(
        f"/api/system/skills/{bound_slug}/share-config",
        headers=admin_headers,
        json={"share_config": {"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None}},
    )
    assert shared.status_code >= 400, shared.text
    assert (await _read_skill_row(dsn, bound_slug))["share_config"] == before["share_config"]

    disabled = await test_client.put(
        f"/api/system/skills/{bound_slug}/enabled",
        headers=admin_headers,
        json={"enabled": False},
    )
    assert disabled.status_code >= 400, disabled.text
    assert (await _read_skill_row(dsn, bound_slug))["enabled"] is True

    deleted = await test_client.delete(f"/api/system/skills/{bound_slug}", headers=admin_headers)
    assert deleted.status_code >= 400, deleted.text
    assert await _read_skill_row(dsn, bound_slug) is not None

    # self-skill 没有独立写入口：内容、依赖都走普通 Skill 的 slug 接口，
    # 共享/启停/删除仍被拒，删除只随 Agent 删除发生。
    standalone_delete = await test_client.delete(f"/api/agent/{slug}/self-skill", headers=admin_headers)
    assert standalone_delete.status_code == 405, standalone_delete.text
    standalone_put = await test_client.put(
        f"/api/agent/{slug}/self-skill",
        headers=admin_headers,
        json={"path": "SKILL.md", "content": "---\nslug: x\nname: x\ndescription: x\n---\n"},
    )
    assert standalone_put.status_code == 405, standalone_put.text
    assert await _read_skill_row(dsn, bound_slug) is not None

    # 删除 Agent 会级联移除绑定 Skill。
    removed_agent = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
    assert removed_agent.status_code == 200, removed_agent.text
    assert await _read_skill_row(dsn, bound_slug) is None


@pytest.mark.asyncio
async def test_bound_skill_permission_follows_agent_share_scope(test_client, admin_headers, standard_user):
    """把 Agent 共享给普通用户后，该用户立即获得绑定 Skill 的运行时访问权。"""
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "")
    uid = str(standard_user["user"]["uid"])
    slug = f"pytest-self-perm-{uuid.uuid4().hex[:10]}"
    bound_slug = f"{slug}{SELF_SKILL_SLUG_SUFFIX}"
    # 初始仅创建者本人可读，普通用户既看不到 Agent 也看不到其绑定 Skill。
    admin_uid = await created_admin_uid(dsn)

    created = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={
            "name": "Pytest self perm",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "share_config": {"version": 2, "read_scope": _user_scope(admin_uid), "manage_scope": None},
        },
    )
    assert created.status_code == 200, created.text

    engine = create_async_engine(os.environ["POSTGRES_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            agent = await db.scalar(select(Agent).where(Agent.slug == slug))
            assert agent is not None
            from yuxi.agents.skills.service import create_agent_self_skill

            await create_agent_self_skill(db, agent=agent)
            await db.commit()

            standard = await db.scalar(select(User).where(User.uid == uid))
            assert standard is not None

            before = {item.slug for item in await list_accessible_skills(db, standard)}
            assert bound_slug not in before
            before_cards = {item.slug for item in await list_skill_cards_for_user(db, standard)}
            assert bound_slug not in before_cards

            # 把 Agent 读取范围显式授予该用户后，绑定 Skill 权限立即跟随，无需同步任务。
            agent.share_config = {"version": 2, "read_scope": _user_scope(uid), "manage_scope": None}
            await db.commit()
            await db.refresh(agent)

            bound_row = await db.scalar(select(Skill).where(Skill.slug == bound_slug))
            assert bound_row is not None
            # 用户级只读：能跑 Agent 就能用其专属技能，但不能改内容。
            assert await skill_service._effective_skill_permission(db, standard, bound_row) == ResourcePermission.READ

            after = {item.slug for item in await list_accessible_skills(db, standard)}
            assert bound_slug in after
            resolved = next(item for item in await list_accessible_skills(db, standard) if item.slug == bound_slug)
            assert resolved.source_scope == "agent_bound"
            assert resolved.bound_agent_id == agent.id

            # 只有 Agent 管理者才拿到 MANAGE，绑定 Skill 不因被看到而获得管理权。
            admin = await db.scalar(select(User).where(User.uid == admin_uid))
            assert await skill_service._effective_skill_permission(db, admin, bound_row) == ResourcePermission.MANAGE
    finally:
        await engine.dispose()
