"""插件目录、保存和运行权限通过真实 HTTP 与隔离 PostgreSQL 验证。"""

from fastapi import FastAPI
import pytest
from sqlalchemy import select

from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.api.routers.agents.management import agent_router
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.runtime.context import normalize_agent_context_config
from yuxi.modules.extensions.plugins import registry
from yuxi.modules.extensions.plugins.base import YuxiPlugin
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.identity.models import User

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]


async def test_plugin_directory_and_write_follow_entry_visibility_and_target_agent(monkeypatch):
    """隐藏名称不泄露，显式越权新增被拒绝，绑定入口只出现在目标智能体。"""

    class PrivatePlugin(YuxiPlugin):
        """来自私有 Skill 的部署声明。"""

        plugin_id = "private-plugin"
        name = "Secret project title"
        description = "Secret project description"
        entry_skill = "private-entry"
        skills = ("private-entry",)

    class BoundPlugin(PrivatePlugin):
        """绑定一个智能体的部署声明。"""

        plugin_id = "bound-plugin"
        name = "Bound project"
        description = "Bound description"
        entry_skill = "bound-entry"
        skills = ("bound-entry",)

    monkeypatch.setattr(registry, "get_plugins", lambda: (PrivatePlugin, BoundPlugin))
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions() as db:
            owner = User(uid="plugin-owner", username="plugin-owner", password_hash="unused", role="user")
            reader = User(uid="plugin-reader", username="plugin-reader", password_hash="unused", role="user")
            db.add_all([owner, reader])
            await db.flush()
            agents = [
                Agent(
                    slug=slug,
                    name=slug,
                    backend_id="ChatbotAgent",
                    created_by=reader.uid,
                    visibility="private",
                    share_config={"version": 2, "read_scope": {"access_level": "user", "user_uids": [reader.uid]}, "manage_scope": None},
                    config_json={"context": {"knowledges": [], "plugins": []}},
                )
                for slug in ("target", "other")
            ]
            db.add_all(agents)
            await db.flush()
            target_id = agents[0].id
            db.add_all(
                [
                    Skill(
                        slug="private-entry",
                        name="Private",
                        description="private",
                        source_type="upload",
                        dir_path="shared/private-entry",
                        enabled=True,
                        created_by=owner.uid,
                        share_config={"version": 2, "read_scope": {"access_level": "user", "user_uids": [owner.uid]}, "manage_scope": None},
                    ),
                    Skill(
                        slug="bound-entry",
                        name="Bound",
                        description="bound",
                        source_type="upload",
                        dir_path="shared/bound-entry",
                        enabled=True,
                        created_by=reader.uid,
                        bound_agent_id=target_id,
                        share_config={
                            "version": 2,
                            "read_scope": {"access_level": "user", "user_uids": [reader.uid]},
                            "manage_scope": None,
                        },
                    ),
                ]
            )
            await db.commit()
        app = FastAPI()
        app.include_router(agent_router, prefix="/api")

        async def database():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_required_user] = lambda: reader
        # 此测试不使用知识资源，独立验证插件目录。
        monkeypatch.setattr("yuxi.modules.knowledge.mcp.client.knowledge_resource_options", lambda uid: _empty())
        async with serve_http(app) as client:
            target = await client.get("/api/agent/target")
            assert target.status_code == 200, target.text
            options = target.json()["agent"]["configurable_items"]["plugins"]["options"]
            assert [item["key"] for item in options] == ["bound-plugin"]
            assert "Secret project" not in target.text
            other = await client.get("/api/agent/other")
            assert other.json()["agent"]["configurable_items"]["plugins"]["options"] == []
            rejected = await client.put("/api/agent/target", json={"config_json": {"context": {"plugins": ["private-plugin"]}}})
            assert rejected.status_code == 422, rejected.text
            assert "无权新增智能体资源 plugins" in rejected.text
            async with sessions() as db:
                existing = await db.scalar(select(Agent).where(Agent.slug == "target"))
                existing.config_json = {"context": {"knowledges": [], "plugins": ["private-plugin"]}}
                await db.commit()
            accepted = await client.put("/api/agent/target", json={"config_json": {"context": {"plugins": ["bound-plugin"]}}})
            assert accepted.status_code == 200, accepted.text
            async with sessions() as db:
                saved = await db.scalar(select(Agent).where(Agent.slug == "target"))
                assert saved.config_json["context"]["plugins"] == ["private-plugin", "bound-plugin"]
                normalized = await normalize_agent_context_config(
                    {"plugins": ["private-plugin", "bound-plugin"], "knowledges": []}, db=db, user=reader, agent_id=target_id
                )
                assert normalized["plugins"] == ["bound-plugin"]
                entry = await db.scalar(select(Skill).where(Skill.slug == "bound-entry"))
                entry.enabled = False
                await db.commit()
            unavailable = await client.get("/api/agent/target")
            assert unavailable.json()["agent"]["configurable_items"]["plugins"]["options"] == []
            # 隐藏的已有选择保持可见配置事实，运行时重新收窄。
            assert unavailable.json()["agent"]["config_json"]["context"]["plugins"] == ["private-plugin", "bound-plugin"]
            async with sessions() as db:
                normalized = await normalize_agent_context_config(
                    {"plugins": ["bound-plugin"], "knowledges": []}, db=db, user=reader, agent_id=target_id
                )
                assert normalized["plugins"] == []


async def _empty():
    """知识资源不属于当前场景。"""
    return []
