"""资源提及目录在真实 HTTP 与 PostgreSQL 上遵循会话快照及当前权限。"""

import asyncio
import hashlib

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.agents.mentions import mention_router
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.mentions import list_session_mention_resources
from yuxi.modules.extensions.plugins import registry
from yuxi.modules.extensions.plugins.base import YuxiPlugin
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.identity.models import APIKey, Department, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.workspace.models import Project
from yuxi.shared.errors import DomainError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]


async def test_resource_mentions_use_session_selection_and_current_visibility(monkeypatch):
    """定义编辑不改变旧会话选择；私有、停用、错误绑定及越权身份都不可读取。"""
    knowledge_bases = [
        {"kb_id": "snapshot-kb", "name": "会话知识库", "description": ""},
        {"kb_id": "new-kb", "name": "新定义知识库", "description": ""},
    ]
    knowledge_stalled = False
    resume_knowledge = asyncio.Event()

    async def visible_knowledge_bases(uid):
        assert uid == "mention-owner"
        assert manager.async_engine.pool.checkedout() == 0, "等待 Knowledge MCP 前应归还会话查询占用的连接"
        if knowledge_stalled:
            await resume_knowledge.wait()
        return knowledge_bases

    monkeypatch.setattr("yuxi.modules.knowledge.mcp.client.list_knowledge_bases", visible_knowledge_bases)
    current_config = {"context": {"plugins": ["new-plugin"], "skills": ["new-skill"], "knowledges": ["new-kb"]}}
    plugin_ids = ("snapshot-plugin", "new-plugin", "private-plugin", "disabled-plugin", "other-bound-plugin")
    plugins = tuple(
        type(
            plugin_id,
            (YuxiPlugin,),
            {
                "__doc__": "当前场景的部署插件声明。",
                "plugin_id": plugin_id,
                "name": f"名称 {plugin_id}",
                "description": f"描述 {plugin_id}",
                "entry_skill": plugin_id,
                "skills": (plugin_id,),
            },
        )
        for plugin_id in plugin_ids
    )
    monkeypatch.setattr(registry, "get_plugins", lambda: plugins)
    async with isolated_audit_store(monkeypatch) as (manager, sessions):
        async with sessions() as db:
            department = Department(name="插件提及测试")
            db.add(department)
            await db.flush()
            owner = User(uid="mention-owner", username="mention-owner", password_hash="fixture", department_id=department.id)
            other = User(uid="mention-other", username="mention-other", password_hash="fixture", department_id=department.id)
            db.add_all([owner, other])
            await db.flush()
            owner_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(owner.id), 'auth_method': 'password'})}"}
            other_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(other.id), 'auth_method': 'password'})}"}
            token = "yxkey_plugin_mentions_fixture"
            db.add(
                APIKey(
                    key_hash=hashlib.sha256(token.encode()).hexdigest(),
                    key_prefix=token[:16],
                    name="插件目录测试",
                    access_level="full",
                    user_id=owner.id,
                    department_id=department.id,
                    created_by=owner.uid,
                )
            )
            share_config = {"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None}
            agents = [
                Agent(
                    slug=slug,
                    name=slug,
                    backend_id="ChatbotAgent",
                    created_by=owner.uid,
                    visibility="private",
                    share_config=share_config,
                    config_json=current_config,
                )
                for slug in ("mention-target", "mention-other-agent")
            ]
            db.add_all(agents)
            project = Project(
                id="mention-project", uid=owner.uid, selection_status="implicit", workdir_path="test/mention", directory_mode="managed"
            )
            db.add(project)
            await db.flush()
            for plugin_id in plugin_ids:
                private = plugin_id == "private-plugin"
                bound_agent_id = None
                if plugin_id == "snapshot-plugin":
                    bound_agent_id = agents[0].id
                elif plugin_id == "other-bound-plugin":
                    bound_agent_id = agents[1].id
                db.add(
                    Skill(
                        slug=plugin_id,
                        name=plugin_id,
                        description=plugin_id,
                        source_type="upload",
                        dir_path=f"shared/{plugin_id}",
                        enabled=plugin_id != "disabled-plugin",
                        created_by=other.uid if private else owner.uid,
                        bound_agent_id=bound_agent_id,
                        share_config=(
                            {"version": 2, "read_scope": {"access_level": "user", "user_uids": [other.uid]}, "manage_scope": None}
                            if private
                            else share_config
                        ),
                    )
                )
            for slug in ("snapshot-skill", "new-skill", "private-skill"):
                db.add(
                    Skill(
                        slug=slug,
                        name=slug,
                        description="",
                        source_type="upload",
                        dir_path=f"shared/{slug}",
                        enabled=True,
                        created_by=other.uid if slug == "private-skill" else owner.uid,
                        share_config=(
                            {"version": 2, "read_scope": {"access_level": "user", "user_uids": [other.uid]}, "manage_scope": None}
                            if slug == "private-skill"
                            else share_config
                        ),
                    )
                )
            snapshot = {
                "plugins": ["snapshot-plugin", "private-plugin", "disabled-plugin", "other-bound-plugin", "removed-plugin"],
                "skills": ["snapshot-skill", "private-skill"],
                "knowledges": ["snapshot-kb", "private-kb"],
            }
            for thread_id, app_id, status, config in (
                ("mention-thread", None, "active", snapshot),
                ("mention-app-thread", "external-app", "active", snapshot),
                ("mention-deleted-thread", None, "deleted", snapshot),
                ("mention-empty-thread", None, "active", {"plugins": [], "skills": [], "knowledges": []}),
                ("mention-legacy-thread", None, "active", {"skills": [], "mcps": []}),
                ("mention-broken-thread", None, "active", None),
            ):
                db.add(
                    Session(
                        thread_id=thread_id,
                        uid=owner.uid,
                        app_id=app_id,
                        project_id=project.id,
                        agent_id=agents[0].slug,
                        status=status,
                        config_snapshot=config,
                    )
                )
            await db.commit()
        app = FastAPI()
        app.add_exception_handler(DomainError, domain_error_handler)
        app.include_router(mention_router, prefix="/api")
        path = "/api/mention/resources"
        empty_directory = {"plugins": [], "skills": [], "knowledges": []}
        async with serve_http(app) as client:
            response = await client.get(path, params={"thread_id": "mention-thread"}, headers=owner_headers)
            assert response.status_code == 200, response.text
            assert response.json() == {
                "plugins": [{"key": "snapshot-plugin", "name": "名称 snapshot-plugin", "description": "描述 snapshot-plugin"}],
                "skills": [{"key": "snapshot-skill", "name": "snapshot-skill", "description": ""}],
                "knowledges": [{"key": "snapshot-kb", "name": "会话知识库", "description": ""}],
            }

            monkeypatch.setattr("yuxi.modules.agents.services.mentions.KNOWLEDGE_MENTION_TIMEOUT_SECONDS", 0.02)
            knowledge_stalled = True
            try:
                async with asyncio.timeout(2):
                    partial = await client.get(path, params={"thread_id": "mention-thread"}, headers=owner_headers)
            finally:
                knowledge_stalled = False
                resume_knowledge.set()
            assert partial.status_code == 200 and partial.json() == {**response.json(), "knowledges": []}

            empty = await client.get(path, params={"thread_id": "mention-empty-thread"}, headers=owner_headers)
            assert empty.status_code == 200 and empty.json() == empty_directory
            legacy = await client.get(path, params={"thread_id": "mention-legacy-thread"}, headers=owner_headers)
            assert legacy.status_code == 200 and legacy.json() == {
                **empty_directory,
                "knowledges": [
                    {"key": "snapshot-kb", "name": "会话知识库", "description": ""},
                    {"key": "new-kb", "name": "新定义知识库", "description": ""},
                ],
            }
            for thread_id in ("mention-app-thread", "mention-deleted-thread", "missing-thread"):
                denied = await client.get(path, params={"thread_id": thread_id}, headers=owner_headers)
                assert denied.status_code == 404 and denied.json()["detail"] == "Thread 不存在"
            denied = await client.get(path, params={"thread_id": "mention-thread"}, headers=other_headers)
            assert denied.status_code == 404 and denied.json()["detail"] == "Thread 不存在"
            key_denied = await client.get(path, params={"thread_id": "mention-thread"}, headers={"Authorization": f"Bearer {token}"})
            assert key_denied.status_code == 403
            assert (await client.get(path, params={"thread_id": "mention-thread"})).status_code == 401
            assert (await client.get(path, headers=owner_headers)).status_code == 422

            # 当前入口权限变化必须立即收窄目录，旧快照与定义均保持原值。
            async with sessions() as db:
                entries = await db.scalars(select(Skill).where(Skill.slug.in_(("snapshot-plugin", "snapshot-skill"))))
                for entry in entries:
                    entry.enabled = False
                await db.commit()
            knowledge_bases.pop(0)
            unavailable = await client.get(path, params={"thread_id": "mention-thread"}, headers=owner_headers)
            assert unavailable.status_code == 200 and unavailable.json() == empty_directory
            async with sessions() as db:
                saved = await db.scalar(select(Session).where(Session.thread_id == "mention-thread"))
                agent = await db.scalar(select(Agent).where(Agent.slug == "mention-target"))
                assert saved.config_snapshot == snapshot
                assert agent.config_json == current_config
                agent.created_by = other.uid
                await db.commit()
            revoked = await client.get(path, params={"thread_id": "mention-thread"}, headers=owner_headers)
            assert revoked.status_code == 404 and revoked.json()["detail"] == "智能体不存在或无权限访问"
            async with sessions() as db:
                agent = await db.scalar(select(Agent).where(Agent.slug == "mention-target"))
                agent.created_by = owner.uid
                await db.commit()
                with pytest.raises(ValueError, match="Session 缺少配置快照"):
                    await list_session_mention_resources(
                        db=db, scope=ActorScope(uid=owner.uid, app_id=None), thread_id="mention-broken-thread", user=owner
                    )
            broken = await client.get(path, params={"thread_id": "mention-broken-thread"}, headers=owner_headers)
            assert broken.status_code == 500
