"""文件发现、真实 PostgreSQL 落库与 HTTP 可见性。"""

import os
import sys
import uuid
from importlib import invalidate_caches

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.modules.agents import presets
from yuxi.modules.agents.runtime.agent_backends import AgentBackendNotFoundError
from yuxi.modules.extensions.skills.builtin import sync as skill_service
from yuxi.modules.agents.services.configuration import initialize_agent_presets
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.extensions.skills.models import Skill

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_default_chatbot_is_first_in_fresh_database():
    """独立空 Schema 中默认助手最先落库，重复初始化保留原记录。"""
    schema = f"pytest_presets_{uuid.uuid4().hex}"
    engine = create_async_engine(os.environ["POSTGRES_URL"], execution_options={"schema_translate_map": {None: schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.run_sync(Agent.__table__.create)

        async with sessions() as db:
            await initialize_agent_presets(db)

        async with sessions() as db:
            agents = list(await db.scalars(select(Agent).order_by(Agent.id)))
            assert [agent.slug for agent in agents] == ["default-chatbot", "deep-research", "knowledge-base-qa"]
            assert agents[0].id == 1
            assert agents[0].created_at == min(agent.created_at for agent in agents)
            original = [(agent.id, agent.slug, agent.created_at) for agent in agents]
            await initialize_agent_presets(db)

        async with sessions() as db:
            agents = list(await db.scalars(select(Agent).order_by(Agent.id)))
            assert [(agent.id, agent.slug, agent.created_at) for agent in agents] == original
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def test_backend_http_contract_rejects_unknown_ids(test_client, admin_headers):
    """公开后端 ID 来自注册表，未知后端返回 404 且不创建角色。"""
    response = await test_client.get("/api/agent/backends", headers=admin_headers)
    assert response.status_code == 200, response.text
    backends = {item["backend_id"]: item for item in response.json()["backends"]}
    assert set(backends) == {"ChatbotAgent"}
    assert backends["ChatbotAgent"]["name"] == "智能助手"
    assert all("capabilities" not in item for item in backends.values())
    assert all("id" not in item for item in backends.values())
    response = await test_client.get("/api/agent/backends/SubAgentBackend", headers=admin_headers)
    assert response.status_code == 404, response.text

    slug = f"pytest-unknown-backend-{uuid.uuid4().hex}"
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for method, url, kwargs in [
            ("GET", "/api/agent/backends/UnknownBackend", {}),
            ("POST", "/api/agent", {"json": {"name": "无效后端", "slug": slug, "backend_id": "UnknownBackend"}}),
        ]:
            response = await test_client.request(method, url, headers=admin_headers, **kwargs)
            assert response.status_code == 404, response.text
            assert response.json()["detail"] == "智能体后端 UnknownBackend 不存在"
        async with sessions() as db:
            assert await db.scalar(select(Agent.id).where(Agent.slug == slug)) is None

        me = await test_client.get("/api/auth/me", headers=admin_headers)
        assert me.status_code == 200, me.text
        uid = me.json()["uid"]
        async with sessions() as db:
            db.add(
                Agent(
                    slug=slug,
                    name="失效后端角色",
                    backend_id="UnknownBackend",
                    created_by=uid,
                    config_json={"context": {}},
                    share_config={
                        "version": 2,
                        "read_scope": {"access_level": "user", "user_uids": [uid]},
                        "manage_scope": {"access_level": "user", "user_uids": [uid]},
                    },
                )
            )
            await db.commit()
        for method, url, kwargs in [
            ("GET", f"/api/agent/{slug}", {}),
            ("GET", "/api/agent", {}),
            ("PUT", f"/api/agent/{slug}", {"json": {"config_json": {"context": {}}}}),
            ("PUT", f"/api/agent/{slug}", {"json": {"name": "不得保存"}}),
        ]:
            response = await test_client.request(method, url, headers=admin_headers, **kwargs)
            assert response.status_code == 404, response.text
            assert response.json()["detail"] == "智能体后端 UnknownBackend 不存在"
        async with sessions() as db:
            assert await db.scalar(select(Agent.name).where(Agent.slug == slug)) == "失效后端角色"
    finally:
        async with sessions() as db:
            await db.execute(delete(Agent).where(Agent.slug == slug))
            await db.commit()
        await engine.dispose()


async def test_unknown_backend_prevents_all_preset_writes(tmp_path, monkeypatch):
    """后续角色后端不存在时，前面的有效角色也不能落库。"""
    suffix = uuid.uuid4().hex
    slugs = [f"pytest-valid-{suffix}", f"pytest-invalid-{suffix}"]
    modules = [f"a_valid_{suffix}", f"z_invalid_{suffix}"]
    for module, slug, backend in zip(modules, slugs, ["ChatbotAgent", "UnknownBackend"], strict=True):
        (tmp_path / f"{module}.py").write_text(
            f'from yuxi.modules.agents.presets import AgentPreset\nPRESET = AgentPreset(slug="{slug}", name="验证角色", description="测试", backend_id="{backend}")\n'
        )
    monkeypatch.setattr(presets, "__file__", str(tmp_path / "__init__.py"))
    monkeypatch.setattr(presets, "__path__", [str(tmp_path)])
    invalidate_caches()
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            with pytest.raises(AgentBackendNotFoundError, match="UnknownBackend"):
                await initialize_agent_presets(db)
        async with sessions() as db:
            assert list(await db.scalars(select(Agent.slug).where(Agent.slug.in_(slugs)))) == []
    finally:
        async with sessions() as db:
            await db.execute(delete(Agent).where(Agent.slug.in_(slugs)))
            await db.commit()
        await engine.dispose()
        for module in modules:
            sys.modules.pop(f"yuxi.modules.agents.presets.{module}", None)


async def test_discovered_content_persists_and_preserves_customization(tmp_path, monkeypatch, test_client, admin_headers):
    """新文件进入持久记录；重启初始化保留角色定制与 Skill 停用状态。"""
    suffix = uuid.uuid4().hex
    role_slug = f"pytest-preset-{suffix}"
    skill_slug = f"pytest-builtin-skill-{suffix}"
    module_name = f"test_preset_{suffix}"
    role_dir = tmp_path / "presets"
    role_dir.mkdir()
    (role_dir / f"{module_name}.py").write_text(
        "from yuxi.modules.agents.presets import AgentPreset\n"
        f'PRESET = AgentPreset(slug="{role_slug}", name="发现测试", description="初始化测试", '
        'backend_id="ChatbotAgent", context={"system_prompt": "原始提示词"})\n'
    )
    monkeypatch.setattr(presets, "__file__", str(role_dir / "__init__.py"))
    monkeypatch.setattr(presets, "__path__", [str(role_dir)])
    invalidate_caches()
    skill_dir = tmp_path / "builtin-skills" / skill_slug
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f'---\nname: 测试技能\nslug: {skill_slug}\ndescription: 初始描述\nversion: "1.0"\n---\n技能正文\n'
    )
    monkeypatch.setattr(skill_service, "BUILTIN_SKILLS_DIR", skill_dir.parent)
    monkeypatch.setattr(skill_service, "get_plugin_skill_directories", lambda: ())
    monkeypatch.setattr(skill_service, "get_skills_root_dir", lambda: tmp_path / "skill-sources/shared")
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with sessions() as db:
            await initialize_agent_presets(db)
            await skill_service.init_builtin_skills(db)

        async with sessions() as db:
            role = await db.scalar(select(Agent).where(Agent.slug == role_slug))
            skill = await db.scalar(select(Skill).where(Skill.slug == skill_slug))
            assert role.backend_id == "ChatbotAgent"
            assert role.config_json == {"context": {"system_prompt": "原始提示词"}}
            assert skill.description == "初始描述"
            assert "tool_dependencies" not in skill.to_dict()
            assert (tmp_path / "skill-sources/shared" / skill_slug / "SKILL.md").read_text() == skill_md.read_text()
            assert skill.version == "1.0" and skill.source_type == "builtin"
            role_id, skill_id = role.id, skill.id
            role.name = "管理员定制"
            role.config_json = {"context": {"system_prompt": "定制提示词"}}
            skill.enabled = False
            await db.commit()

        response = await test_client.get(f"/api/agent/{role_slug}", headers=admin_headers)
        assert response.status_code == 200, response.text
        assert response.json()["agent"]["name"] == "管理员定制"
        assert "capabilities" not in response.json()["agent"]
        assert response.json()["agent"]["config_json"]["context"]["system_prompt"] == "定制提示词"

        skill_md.write_text(skill_md.read_text().replace("初始描述", "更新描述").replace('"1.0"', '"2.0"'))
        async with sessions() as db:
            await initialize_agent_presets(db)
            await skill_service.init_builtin_skills(db)

        async with sessions() as db:
            role = await db.scalar(select(Agent).where(Agent.slug == role_slug))
            skill = await db.scalar(select(Skill).where(Skill.slug == skill_slug))
            assert role.id == role_id and role.name == "管理员定制"
            assert role.config_json == {"context": {"system_prompt": "定制提示词"}}
            assert skill.id == skill_id and not skill.enabled
            assert skill.description == "更新描述" and skill.version == "2.0"
            assert (tmp_path / "skill-sources/shared" / skill_slug / "SKILL.md").read_text() == skill_md.read_text()
    finally:
        async with sessions() as db:
            await db.execute(delete(Agent).where(Agent.slug == role_slug))
            await db.execute(delete(Skill).where(Skill.slug == skill_slug))
            await db.commit()
        await engine.dispose()
        sys.modules.pop(f"yuxi.modules.agents.presets.{module_name}", None)
