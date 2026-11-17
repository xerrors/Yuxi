"""模块缺陷经真实 HTTP 与 PostgreSQL 回读的结果。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import select

import yuxi.api.routers.extensions.skills as skill_router
import yuxi.api.routers.knowledge.management as knowledge_router
from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.identity.models import User
from yuxi.modules.knowledge.models import KnowledgeBase
from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_concurrent_knowledge_names_return_one_conflict_and_preserve_winner(monkeypatch):
    """即使两个预检都通过，数据库仍只保存一个大小写无关的有效名称。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        arrivals = 0
        ready = asyncio.Event()

        async def raced_check(self, _name):
            """让两个创建请求同时通过名称预检。"""
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 5)
            return False

        monkeypatch.setattr(KnowledgeBaseRepository, "name_exists", raced_check)
        app = FastAPI()
        app.include_router(knowledge_router.knowledge, prefix="/api")
        app.dependency_overrides[knowledge_router.get_admin_user] = lambda: SimpleNamespace(uid="operator", department_id=None)
        async with sessions.begin() as db:
            db.add(User(username="operator", uid="operator", password_hash="disabled", role="admin"))
        payload = {
            "description": "same name",
            "kb_type": "dify",
            "additional_params": {"dify_api_url": "https://example.com/v1", "dify_token": "fixture", "dify_dataset_id": "fixture"},
        }
        async with serve_http(app) as client:
            responses = await asyncio.gather(
                *[
                    client.post("/api/knowledge/knowledge-bases", json={**payload, "name": name})
                    for name in ("Audit Knowledge", "audit knowledge")
                ]
            )
        assert sorted(response.status_code for response in responses) == [200, 409], [response.text for response in responses]
        winner = next(response.json() for response in responses if response.status_code == 200)
        conflict = next(response.json() for response in responses if response.status_code == 409)
        assert "已存在" in conflict["detail"]
        async with sessions() as db:
            rows = list((await db.scalars(select(KnowledgeBase))).all())
            assert len(rows) == 1
            assert rows[0].kb_id == winner["kb_id"]
            assert rows[0].name == winner["name"]


async def test_builtin_skill_visibility_permission_and_toggle_agree(monkeypatch):
    """admin 只读取已启用内置项，superadmin 管理停用项且动作实际落库。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions.begin() as db:
            db.add_all(
                [
                    Skill(
                        slug=slug,
                        name=slug,
                        description="fixture",
                        source_type="builtin",
                        dir_path=f"shared/{slug}",
                        enabled=enabled,
                        created_by="admin",
                        share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": {"access_level": "global"}},
                    )
                    for slug, enabled in (("builtin-enabled", True), ("builtin-disabled", False))
                ]
            )
        actor = User(username="admin", uid="admin", role="admin", is_deleted=0)

        async def db_dependency():
            """为每次 HTTP 请求提供独立数据库会话。"""
            async with sessions() as db:
                yield db

        app = FastAPI()
        app.include_router(skill_router.skills, prefix="/api")
        app.dependency_overrides[skill_router.get_required_user] = lambda: actor
        app.dependency_overrides[skill_router.get_db] = db_dependency
        async with serve_http(app) as client:
            listing = await client.get("/api/system/skills")
            assert listing.status_code == 200, listing.text
            cards = listing.json()["data"]
            assert [card["slug"] for card in cards] == ["builtin-enabled"]
            assert cards[0]["can_manage"] is False
            assert cards[0]["effective_permission"] == "read"
            denied = await client.put("/api/system/skills/builtin-disabled/enabled", json={"enabled": True})
            assert denied.status_code in {403, 404}, denied.text
            async with sessions() as db:
                assert await db.scalar(select(Skill.enabled).where(Skill.slug == "builtin-disabled")) is False
            actor.role = "superadmin"
            listing = await client.get("/api/system/skills")
            assert {card["slug"] for card in listing.json()["data"]} == {"builtin-enabled", "builtin-disabled"}
            assert all(card["can_manage"] and card["effective_permission"] == "manage" for card in listing.json()["data"])
            enabled = await client.put("/api/system/skills/builtin-disabled/enabled", json={"enabled": True})
            assert enabled.status_code == 200, enabled.text
            async with sessions() as db:
                assert await db.scalar(select(Skill.enabled).where(Skill.slug == "builtin-disabled")) is True


async def test_knowledge_rename_conflict_returns_409_without_changing_record(monkeypatch):
    """新增名称约束同样覆盖重命名，冲突回滚后原名称保留。"""
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions.begin() as db:
            db.add_all(
                [
                    KnowledgeBase(kb_id="rename-a", name="Original", kb_type="dify"),
                    KnowledgeBase(kb_id="rename-b", name="Occupied", kb_type="dify"),
                ]
            )
        app = FastAPI()
        app.include_router(knowledge_router.knowledge, prefix="/api")
        app.dependency_overrides[knowledge_router.require_knowledge_base_manage] = lambda: SimpleNamespace(
            uid="operator", department_id=None
        )
        async with serve_http(app) as client:
            response = await client.put("/api/knowledge/knowledge-bases/rename-a", json={"name": "OCCUPIED", "description": "conflict"})
        assert response.status_code == 409, response.text
        assert "已存在" in response.json()["detail"]
        async with sessions() as db:
            assert await db.scalar(select(KnowledgeBase.name).where(KnowledgeBase.kb_id == "rename-a")) == "Original"
