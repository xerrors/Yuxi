"""一次创建的真实 HTTP、PostgreSQL 与文件提交证据。"""

import json
import uuid
from io import BytesIO
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.services.definitions import create_agent_definition
from yuxi.modules.extensions.mcp.models import MCPServer
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.identity.models import User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


def bundle(*, mcp=None, invalid=False, slug="guide"):
    """构造根指南与辅助文件，依赖由实际包 parser 解析。"""
    if invalid:
        return b"not-a-zip"
    result = BytesIO()
    dependencies = f"mcp_dependencies:\n- {mcp}\n" if mcp else ""
    with ZipFile(result, "w") as archive:
        archive.writestr(
            "guide/SKILL.md",
            f"---\nslug: {slug}\nname: Guide\ndescription: imported\n" + dependencies + "---\n# Imported guide\n",
        )
        archive.writestr("guide/references/note.txt", "CREATION_REFERENCE")
    return result.getvalue()


def remote_config(slug):
    """远程资源使用保留域名与占位请求头。"""
    return {
        "slug": slug,
        "name": slug,
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "headers": {"X-Test": "fixture-only"},
    }


async def assert_absent(postgres_sessions, agent_slug, mcp_slug):
    """提交失败后重新查询 PostgreSQL 的所有关联结果。"""
    async with postgres_sessions() as db:
        assert await db.scalar(select(Agent.id).where(Agent.slug == agent_slug)) is None
        assert await db.scalar(select(MCPServer.id).where(MCPServer.slug == mcp_slug)) is None
        assert await db.scalar(select(Skill.id).join(Agent, Skill.bound_agent_id == Agent.id).where(Agent.slug == agent_slug)) is None


async def test_create_with_skill_and_existing_mcp(test_client, admin_headers, standard_user, postgres_sessions):
    """回读完整包、显式 MCP 选择和脱敏结果，普通用户可复用已启用 MCP。"""
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    assert me.status_code == 200, me.text
    suffix = uuid.uuid4().hex[:10]
    slug, mcp_slug = f"pytest-create-{suffix}", f"pytest-mcp-{suffix}"
    other_slug = f"pytest-other-{suffix}"
    created = await test_client.post("/api/system/mcp-servers", headers=admin_headers, json=remote_config(mcp_slug))
    assert created.status_code == 200, created.text
    try:
        response = await test_client.post(
            "/api/agent/with-skill",
            headers=admin_headers,
            data={
                "agent": json.dumps(
                    {
                        "slug": slug,
                        "name": "Guide Agent",
                        "config_json": {
                            "context": {
                                "mcps": [mcp_slug],
                                "system_prompt": "创建时指定角色",
                                "tools": [],
                                "max_execution_steps": 7,
                            }
                        },
                    }
                )
            },
            files={"file": ("guide.zip", bundle(), "application/zip")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["agent"]["agent_id"] == slug
        read = await test_client.get(f"/api/agent/{slug}", headers=admin_headers)
        context = read.json()["agent"]["config_json"]["context"]
        assert context["mcps"] == [mcp_slug]
        assert context["system_prompt"] == "创建时指定角色"
        assert context["tools"] == [] and context["max_execution_steps"] == 7
        async with postgres_sessions() as db:
            agent = await db.scalar(select(Agent).where(Agent.slug == slug))
            skill = await db.scalar(select(Skill).where(Skill.bound_agent_id == agent.id))
            server = await db.scalar(select(MCPServer).where(MCPServer.slug == mcp_slug))
            assert server.enabled == 1 and server.headers == {"X-Test": "fixture-only"}
            assert server.created_by == server.updated_by == me.json()["username"]
            assert "mcp_dependencies" not in skill.to_dict()
            assert (get_skill_data_dir() / skill.dir_path / "references/note.txt").read_text() == "CREATION_REFERENCE"
            assert f"slug: {skill.slug}" in (get_skill_data_dir() / skill.dir_path / "SKILL.md").read_text()
        public = await test_client.get("/api/system/mcp-servers", headers=standard_user["headers"])
        server = next(item for item in public.json()["data"] if item["slug"] == mcp_slug)
        assert "headers" not in server and "url" not in server
        ordinary = await test_client.post(
            "/api/agent",
            headers=standard_user["headers"],
            json={"slug": other_slug, "name": "Existing MCP", "config_json": {"context": {"mcps": [mcp_slug]}}},
        )
        assert ordinary.status_code == 200, ordinary.text
        read = await test_client.get(f"/api/agent/{other_slug}", headers=standard_user["headers"])
        assert read.json()["agent"]["config_json"]["context"]["mcps"] == [mcp_slug]
        assert (await test_client.get(f"/api/agent/{other_slug}/self-skill", headers=standard_user["headers"])).json()["skill"] is None
    finally:
        for agent_slug in (slug, other_slug):
            result = await test_client.delete(f"/api/agent/{agent_slug}", headers=admin_headers)
            assert result.status_code in {200, 404}, result.text
        result = await test_client.delete(f"/api/system/mcp-servers/{mcp_slug}", headers=admin_headers)
        assert result.status_code in {200, 404}, result.text


async def test_json_creation_keeps_backend_defaults_without_empty_skill(test_client, admin_headers, postgres_sessions):
    """省略 Context 保持默认意图，不创建空专属包或其他资源。"""
    suffix = uuid.uuid4().hex[:10]
    slug, mcp_slug = f"pytest-json-{suffix}", f"pytest-json-mcp-{suffix}"
    try:
        result = await test_client.post(
            "/api/agent",
            headers=admin_headers,
            json={"slug": slug, "name": slug},
        )
        assert result.status_code == 200, result.text
        read = await test_client.get(f"/api/agent/{slug}", headers=admin_headers)
        context = read.json()["agent"]["config_json"]["context"]
        assert "mcps" not in context and "tools" not in context
        assert read.json()["agent"]["configurable_items"]["tools"]["default"] == "all"
        async with postgres_sessions() as db:
            agent = await db.scalar(select(Agent).where(Agent.slug == slug))
            assert await db.scalar(select(Skill.id).where(Skill.bound_agent_id == agent.id)) is None
            assert await db.scalar(select(MCPServer.id).where(MCPServer.slug == mcp_slug)) is None
    finally:
        await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        await test_client.delete(f"/api/system/mcp-servers/{mcp_slug}", headers=admin_headers)


async def test_disabled_mcp_cannot_be_selected(test_client, admin_headers, postgres_sessions):
    """拒绝禁用资源选择，已有独立配置保持原值。"""
    suffix = uuid.uuid4().hex[:10]
    existing, slug = f"pytest-existing-{suffix}", f"pytest-existing-agent-{suffix}"
    result = await test_client.post("/api/system/mcp-servers", headers=admin_headers, json=remote_config(existing))
    assert result.status_code == 200, result.text
    try:
        result = await test_client.put(f"/api/system/mcp-servers/{existing}/status", headers=admin_headers, json={"enabled": False})
        assert result.status_code == 200, result.text
        rejected = await test_client.post(
            "/api/agent",
            headers=admin_headers,
            json={"name": slug, "slug": slug, "config_json": {"context": {"mcps": [existing]}}},
        )
        assert rejected.status_code == 422, rejected.text
        async with postgres_sessions() as db:
            assert await db.scalar(select(Agent.id).where(Agent.slug == slug)) is None
            row = await db.scalar(select(MCPServer).where(MCPServer.slug == existing))
            assert row.url == "https://example.com/mcp" and row.headers == {"X-Test": "fixture-only"}
            assert not row.enabled
    finally:
        await test_client.delete(f"/api/system/mcp-servers/{existing}", headers=admin_headers)


@pytest.mark.parametrize("failure", ["zip", "dependency", "config", "config-shape", "removed-import", "oversize", "payload"])
async def test_invalid_creation_leaves_no_partial_resources(test_client, admin_headers, postgres_sessions, failure):
    """失败不留下 Agent 或专属包，Agent 请求不能创建 MCP。"""
    root = get_skill_data_dir() / "packages"
    before = set(root.glob("*")) | set(root.glob("*/*"))
    suffix = uuid.uuid4().hex[:10]
    slug, mcp_slug = f"pytest-reject-{suffix}", f"pytest-reject-mcp-{suffix}"
    payload = {"slug": slug, "name": slug}
    if failure == "config":
        payload["config_json"] = {"context": {"mcps": ["missing-server"]}}
    if failure == "config-shape":
        payload["config_json"] = {"context": "invalid"}
    if failure == "removed-import":
        payload["mcp_servers"] = [remote_config(mcp_slug)]
    package = (
        b"x" * (10 * 1024 * 1024 + 1)
        if failure == "oversize"
        else bundle(mcp="missing-server" if failure == "dependency" else None, invalid=failure == "zip")
    )
    response = await test_client.post(
        "/api/agent/with-skill",
        headers=admin_headers,
        data={"agent": "not-json" if failure == "payload" else json.dumps(payload)},
        files={"file": ("guide.zip", package, "application/zip")},
    )
    assert response.status_code == (413 if failure == "oversize" else 422), response.text
    await assert_absent(postgres_sessions, slug, mcp_slug)
    assert set(root.glob("*")) | set(root.glob("*/*")) == before


@pytest.mark.parametrize("role", ["user", "admin", "superadmin"])
async def test_mcp_is_created_only_by_its_management_endpoint(test_client, admin_headers, standard_user, postgres_sessions, role):
    """独立创建权限保持不变，所有角色都不能在 Agent 请求内导入。"""
    headers = admin_headers if role == "superadmin" else standard_user["headers"]
    if role == "admin":
        result = await test_client.put(f"/api/auth/users/{standard_user['user']['id']}", headers=admin_headers, json={"role": "admin"})
        assert result.status_code == 200, result.text
    suffix = uuid.uuid4().hex[:10]
    slug, mcp_slug = f"pytest-role-{suffix}", f"pytest-role-mcp-{suffix}"
    rejected = await test_client.post(
        "/api/agent", headers=headers, json={"slug": slug, "name": slug, "mcp_servers": [remote_config(mcp_slug)]}
    )
    assert rejected.status_code == 422, rejected.text
    await assert_absent(postgres_sessions, slug, mcp_slug)
    try:
        result = await test_client.post("/api/system/mcp-servers", headers=headers, json=remote_config(mcp_slug))
        assert result.status_code == (200 if role == "superadmin" else 403), result.text
        async with postgres_sessions() as db:
            assert (await db.scalar(select(MCPServer.id).where(MCPServer.slug == mcp_slug)) is not None) == (role == "superadmin")
    finally:
        await test_client.delete(f"/api/system/mcp-servers/{mcp_slug}", headers=admin_headers)


@pytest.mark.parametrize("with_skill", [False, True])
async def test_final_commit_failure_rolls_back_all_creation(test_client, admin_headers, postgres_sessions, monkeypatch, with_skill):
    """Agent/专属包提交失败回滚自身，已经独立发布的 MCP 保留。"""
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    suffix = uuid.uuid4().hex[:10]
    slug, mcp_slug = f"pytest-atomic-{suffix}", f"pytest-atomic-mcp-{suffix}"
    result = await test_client.post("/api/system/mcp-servers", headers=admin_headers, json=remote_config(mcp_slug))
    assert result.status_code == 200, result.text
    root = get_skill_data_dir() / "packages"
    before = set(root.glob("*")) | set(root.glob("*/*"))
    async with postgres_sessions() as db:
        operator = await db.scalar(select(User).where(User.uid == me.json()["uid"]))

        async def fail_final_commit():
            """拒绝提前提交，并在全部行已 flush 的真实事务注入故障。"""
            agent = await db.scalar(select(Agent).where(Agent.slug == slug))
            assert agent is not None
            assert await db.scalar(select(MCPServer.id).where(MCPServer.slug == mcp_slug)) is not None
            skill = await db.scalar(select(Skill).where(Skill.bound_agent_id == agent.id))
            assert (skill is not None) == with_skill
            raise RuntimeError("injected final commit failure")

        monkeypatch.setattr(db, "commit", fail_final_commit)
        with pytest.raises(RuntimeError, match="final commit failure"):
            await create_agent_definition(
                db,
                operator=operator,
                backend_id="ChatbotAgent",
                slug=slug,
                name=slug,
                config_json={"context": {"mcps": [mcp_slug]}},
                skill_upload=("guide.zip", bundle()) if with_skill else None,
            )
    try:
        async with postgres_sessions() as db:
            assert await db.scalar(select(Agent.id).where(Agent.slug == slug)) is None
            server = await db.scalar(select(MCPServer).where(MCPServer.slug == mcp_slug))
            assert server.url == "https://example.com/mcp" and server.headers == {"X-Test": "fixture-only"}
        assert set(root.glob("*")) | set(root.glob("*/*")) == before
    finally:
        await test_client.delete(f"/api/system/mcp-servers/{mcp_slug}", headers=admin_headers)


@pytest.mark.parametrize("role", ["user", "admin"])
async def test_shared_skill_creation_has_its_own_permission_and_lifetime(
    test_client,
    admin_headers,
    standard_user,
    postgres_sessions,
    role,
):
    """共享 Skill 仅管理员发布，Agent 失败不会回滚已发布的 Skill。"""
    headers = standard_user["headers"]
    if role == "admin":
        changed = await test_client.put(f"/api/auth/users/{standard_user['user']['id']}", headers=admin_headers, json={"role": "admin"})
        assert changed.status_code == 200, changed.text
    suffix = uuid.uuid4().hex[:10]
    skill_slug, agent_slug = f"pytest-shared-{suffix}", f"pytest-select-{suffix}"
    prepared = await test_client.post(
        "/api/skills/import/prepare",
        headers=headers,
        files={"file": ("shared.zip", bundle(slug=skill_slug), "application/zip")},
    )
    assert prepared.status_code == 200, prepared.text
    draft_id = prepared.json()["data"]["draft_id"]
    try:
        result = await test_client.post(
            f"/api/skills/install-drafts/{draft_id}/confirm",
            headers=headers,
            json={
                "slugs": [skill_slug],
                "share_config": {
                    "version": 2,
                    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                    "manage_scope": None,
                },
            },
        )
        assert result.status_code == (200 if role == "admin" else 403), result.text
        async with postgres_sessions() as db:
            shared = await db.scalar(select(Skill).where(Skill.slug == skill_slug))
            assert (shared is not None) == (role == "admin")
            if shared is not None:
                assert shared.bound_agent_id is None
        if role == "admin":
            assert result.json()["data"][0]["success"] is True
            created = await test_client.post(
                "/api/agent",
                headers=headers,
                json={"slug": agent_slug, "name": agent_slug, "config_json": {"context": {"skills": [skill_slug]}}},
            )
            assert created.status_code == 200, created.text
            read = await test_client.get(f"/api/agent/{agent_slug}", headers=headers)
            assert read.json()["agent"]["config_json"]["context"]["skills"] == [skill_slug]
            rejected = await test_client.post(
                "/api/agent/with-skill",
                headers=headers,
                data={
                    "agent": json.dumps(
                        {
                            "slug": agent_slug + "-bad",
                            "name": "Bad",
                            "config_json": {"context": {"skills": [skill_slug]}},
                        }
                    )
                },
                files={"file": ("bad.zip", bundle(invalid=True), "application/zip")},
            )
            assert rejected.status_code == 422, rejected.text
            async with postgres_sessions() as db:
                assert await db.scalar(select(Agent.id).where(Agent.slug == agent_slug + "-bad")) is None
                assert await db.scalar(select(Skill.id).where(Skill.slug == skill_slug)) is not None
    finally:
        await test_client.delete(f"/api/agent/{agent_slug}", headers=admin_headers)
        await test_client.delete(f"/api/skills/install-drafts/{draft_id}", headers=headers)
        await test_client.delete(f"/api/system/skills/{skill_slug}", headers=admin_headers)
