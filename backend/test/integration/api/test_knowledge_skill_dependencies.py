"""真实 HTTP 验证共享与专属 Skill 均不能声明运行能力。"""

import uuid
from io import BytesIO
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.extensions.skills.models import Skill

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.mark.parametrize("source_kind", ["shared", "agent_bound"])
@pytest.mark.parametrize(
    "field,value", [("tool_dependencies", "[query_kb]"), ("mcp_dependencies", "[knowledge]"), ("skill_dependencies", "[]")]
)
async def test_skill_upload_rejects_all_runtime_dependencies(source_kind, field, value, test_client, admin_headers, postgres_sessions):
    """拒绝在实际数据库中创建绑定或共享资源，不以 HTTP 状态作为唯一证据。"""
    slug = f"pytest-skill-contract-{uuid.uuid4().hex[:10]}"
    content = f"---\nslug: {slug}\nname: Guide\ndescription: Test\n{field}: {value}\n---\n# Guide\n"
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("bundle/SKILL.md", content)
    try:
        if source_kind == "agent_bound":
            created = await test_client.post("/api/agent", headers=admin_headers, json={"slug": slug, "name": "Skill contract"})
            assert created.status_code == 200, created.text
            route = f"/api/agent/{slug}/self-skill/upload"
        else:
            route = "/api/skills/import/prepare"
        rejected = await test_client.post(route, headers=admin_headers, files={"file": ("skill.zip", buffer.getvalue(), "application/zip")})
        assert rejected.status_code == (422 if source_kind == "agent_bound" else 400), rejected.text
        assert "部署插件" in rejected.text
        async with postgres_sessions() as db:
            assert await db.scalar(select(Skill.id).where(Skill.slug == slug)) is None
            if source_kind == "agent_bound":
                agent = await db.scalar(select(Agent).where(Agent.slug == slug))
                assert await db.scalar(select(Skill.id).where(Skill.bound_agent_id == agent.id)) is None
    finally:
        if source_kind == "agent_bound":
            deleted = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
            assert deleted.status_code in {200, 404}, deleted.text
