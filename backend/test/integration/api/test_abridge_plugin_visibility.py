"""真实 HTTP 与 PostgreSQL 验证插件专属 MCP 的选择与管理。"""

from copy import deepcopy

from fastapi import FastAPI
import pytest
from sqlalchemy import select

from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.api.routers.agents.management import agent_router
from yuxi.api.routers.extensions.mcp import mcp
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.runtime.context import normalize_agent_context_config
from yuxi.modules.extensions.mcp.models import MCPServer
from yuxi.modules.extensions.mcp.service import available_mcp_servers, get_enabled_mcp_server_slugs
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.identity.models import User
from yuxi.modules.system.models import ConfigOption

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]


@pytest.mark.parametrize(("slug", "plugin_id"), [("knowledge", "knowledge-base"), ("abridge", "abridge")])
async def test_owned_mcp_is_selected_only_as_plugin_and_remains_manageable(monkeypatch, slug, plugin_id):
    """直接选择被拒绝且不落库，all 不包含专属连接器，插件和普通 MCP 仍可保存。"""
    monkeypatch.setenv("ABRIDGE_PUBLIC_ORIGIN", "https://bridge.example")
    monkeypatch.setenv("YUXI_OIDC_PROVIDER_ENABLED", "true")
    # 资源配置目录只测试连接与插件，不需要查询知识库内容。
    monkeypatch.setattr("yuxi.modules.knowledge.mcp.client.knowledge_resource_options", _empty_knowledge_options)
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions() as db:
            owner = User(uid="abridge-plugin-owner", username="abridge-plugin-owner", password_hash="unused", role="user")
            administrator = User(uid="abridge-plugin-admin", username="abridge-plugin-admin", password_hash="unused", role="superadmin")
            db.add_all([owner, administrator])
            await db.flush()
            agent = Agent(
                slug="device-agent",
                name="Device agent",
                backend_id="ChatbotAgent",
                created_by=owner.uid,
                visibility="private",
                share_config={"version": 2, "read_scope": {"access_level": "user", "user_uids": [owner.uid]}, "manage_scope": None},
                config_json={"context": {"knowledges": [], "skills": [], "plugins": [], "mcps": []}},
            )
            db.add(agent)
            db.add_all(
                [
                    Skill(
                        slug=plugin_id,
                        name=plugin_id,
                        description="插件入口",
                        source_type="builtin",
                        dir_path=f"builtin/{plugin_id}",
                        enabled=True,
                        created_by="system",
                        share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
                    ),
                    ConfigOption(key="abridge_options", name="设备管理", value={"url": "https://bridge.example"}),
                    MCPServer(
                        slug=slug,
                        name=slug,
                        transport="streamable_http",
                        url="http://configured.example/mcp",
                        auth_mode="delegation",
                        code_owned=True,
                        enabled=1,
                        created_by="system",
                        updated_by="system",
                    ),
                    MCPServer(
                        slug="ordinary-mcp",
                        name="Ordinary MCP",
                        transport="streamable_http",
                        url="https://ordinary.example/mcp",
                        auth_mode="none",
                        enabled=1,
                        created_by=owner.uid,
                        updated_by=owner.uid,
                    ),
                ]
            )
            await db.commit()
            agent_id = agent.id

        app = FastAPI()
        app.include_router(agent_router, prefix="/api")
        app.include_router(mcp, prefix="/api")

        async def database():
            """每个真实请求使用隔离 Schema 的独立事务。"""
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_required_user] = lambda: owner
        async with serve_http(app) as client:
            response = await client.get("/api/agent/device-agent")
            assert response.status_code == 200, response.text
            options = response.json()["agent"]["configurable_items"]
            assert [item["key"] for item in options["plugins"]["options"]] == [plugin_id]
            assert [item["key"] for item in options["mcps"]["options"]] == ["ordinary-mcp"]
            assert plugin_id not in [item["key"] for item in options["skills"]["options"]]

            ordinary = await client.put("/api/agent/device-agent", json={"config_json": {"context": {"mcps": ["ordinary-mcp"]}}})
            assert ordinary.status_code == 200, ordinary.text
            async with sessions() as db:
                saved = await db.get(Agent, agent_id)
                assert saved.config_json["context"]["mcps"] == ["ordinary-mcp"]
                before_rejection = deepcopy(saved.config_json)

            rejected = await client.put("/api/agent/device-agent", json={"config_json": {"context": {"mcps": [slug]}}})
            assert rejected.status_code == 422, rejected.text
            assert "连接器通过插件启用" in rejected.text
            async with sessions() as db:
                saved = await db.get(Agent, agent_id)
                assert saved.config_json == before_rejection

            accepted = await client.put(
                "/api/agent/device-agent", json={"config_json": {"context": {"mcps": "all", "plugins": [plugin_id]}}}
            )
            assert accepted.status_code == 200, accepted.text
            async with sessions() as db:
                saved = await db.scalar(select(Agent).where(Agent.id == agent_id))
                assert saved.config_json["context"]["plugins"] == [plugin_id]
                assert saved.config_json["context"]["mcps"] == "all"
                normalized = await normalize_agent_context_config(saved.config_json["context"], db=db, user=owner, agent_id=agent_id)
                assert normalized["mcps"] == ["ordinary-mcp"]
                assert normalized["plugins"] == [plugin_id]
                assert set(await get_enabled_mcp_server_slugs(db=db)) == {slug, "ordinary-mcp"}
                assert set(await available_mcp_servers(db, [slug, "ordinary-mcp"], user_id=owner.id)) == {
                    slug,
                    "ordinary-mcp",
                }

            directories = [await client.get("/api/system/mcp-servers")]
            app.dependency_overrides[get_required_user] = lambda: administrator
            directories.append(await client.get("/api/system/mcp-servers"))
            for directory in directories:
                assert directory.status_code == 200, directory.text
                servers = {server["slug"]: server for server in directory.json()["data"]}
                assert set(servers) == {slug, "ordinary-mcp"}
                assert servers[slug]["available"] is True
                assert servers[slug]["auth_mode"] == "delegation"


async def _empty_knowledge_options(uid):
    """当前用例只验证插件与 MCP 的配置目录。"""
    return []
