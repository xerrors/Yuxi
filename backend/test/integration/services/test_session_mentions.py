"""对话标题引用通过真实 HTTP 与 PostgreSQL 验证。"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from test.integration.services.agent_input_fixtures import SCOPE
from test.integration.support.module_audit import serve_http
from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.api.routers.agents.mentions import mention_router
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.personal_assistants import PersonalAssistant
from yuxi.modules.agents.repositories.messages import MessageRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.services.memory import read_thread_messages
from yuxi.modules.identity.models import APIKey, Department, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.workspace.models import Project

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
pytest_plugins = ["test.integration.services.agent_input_fixtures"]


async def test_session_mentions_only_search_titles_and_keep_user_app_scope(sessions):
    """正文、个人助理、其他用户、APP、归档和通配符均不能混入标题结果。"""
    query = r"方案%_\设计"
    async with sessions() as db:
        await db.execute(
            text(
                "INSERT INTO users (username, uid, password_hash, role, login_failed_count, is_deleted) "
                "VALUES ('other', 'other', 'hash', 'user', 0, 0)"
            )
        )
        await db.execute(
            text(
                "INSERT INTO projects (id, uid, selection_status, workdir_path, directory_mode) "
                "VALUES ('other-project', 'other', 'implicit', 'projects/other', 'managed')"
            )
        )
        repo = SessionRepository(db)
        for thread_id, uid, app_id, title, status in [
            ("title-a", SCOPE.uid, None, query, "active"),
            ("title-b", SCOPE.uid, None, query, "active"),
            ("body-only", SCOPE.uid, None, "正文命中", "active"),
            ("wildcard", SCOPE.uid, None, "方案XXZ设计", "active"),
            ("other-app", SCOPE.uid, "private-app", query, "active"),
            ("other-user", "other", None, query, "active"),
            ("archived", SCOPE.uid, None, query, "archived"),
        ]:
            row = await repo.add_session(
                uid=uid,
                app_id=app_id,
                agent_id="main",
                title=title,
                thread_id=thread_id,
                project_id="input-project" if uid == SCOPE.uid else "other-project",
            )
            row.status = status
            await MessageRepository(db).add_message(row.id, "user", query + " 私密正文")
        personal_agent = Agent(slug="personal-mention", name="个人助理", backend_id="ChatbotAgent", share_config={}, created_by=SCOPE.uid)
        db.add_all(
            [
                personal_agent,
                Project(
                    id="personal-project",
                    uid=SCOPE.uid,
                    selection_status="implicit",
                    workdir_path="agents/assistant",
                    directory_mode="linked",
                ),
            ]
        )
        await db.flush()
        await repo.add_session(
            uid=SCOPE.uid, agent_id=personal_agent.slug, title=query, thread_id="personal-root", project_id="personal-project"
        )
        db.add(PersonalAssistant(uid=SCOPE.uid, agent_id=personal_agent.id, project_id="personal-project", main_thread_id="personal-root"))
        await db.commit()

    app = FastAPI()
    app.include_router(mention_router, prefix="/api")

    async def database():
        """HTTP 请求读取独立的真实数据库事务。"""
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_required_user] = lambda: SimpleNamespace(uid=SCOPE.uid)
    async with serve_http(app) as client:
        response = await client.get("/api/mention/sessions", params={"query": query})
        assert response.status_code == 200, response.text
        assert {row["thread_id"] for row in response.json()} == {"title-a", "title-b"}
        assert all(row["title"] == query for row in response.json())
        assert "私密正文" not in response.text
        assert all(set(row) == {"thread_id", "title", "updated_at"} for row in response.json())
        recent = (await client.get("/api/mention/sessions")).json()
        assert {row["thread_id"] for row in recent} == {"input-thread", "title-a", "title-b", "body-only", "wildcard"}
        assert (await client.get("/api/mention/sessions", params={"query": "不存在"})).json() == []
        assert (await client.get("/api/mention/sessions", params={"query": "x" * 257})).status_code == 422

    history = await read_thread_messages(uid=SCOPE.uid, thread_id="title-a")
    assert history["messages"][0]["content"] == query + " 私密正文"
    with pytest.raises(ValueError, match="不存在或不可见"):
        await read_thread_messages(uid=SCOPE.uid, thread_id="other-user")


async def test_session_title_search_limits_recent_results(sessions):
    """空查询也只返回最近十条，排序与截断由数据库执行。"""
    async with sessions() as db:
        for index in range(12):
            await SessionRepository(db).add_session(
                uid=SCOPE.uid, agent_id="main", project_id="input-project", thread_id=f"recent-{index}", title="近期对话"
            )
        await db.commit()
    async with sessions() as db:
        rows, has_more = await SessionRepository(db).search_sessions(uid=SCOPE.uid, app_id=None, query="", title_only=True, limit=10)
        assert has_more
        assert [row["agent_session"].thread_id for row in rows] == [f"recent-{index}" for index in range(11, 1, -1)]


@pytest.mark.parametrize("app_id", [None, "bound-app"])
async def test_session_mentions_requires_web_login(sessions, app_id):
    """真实 JWT 可读取本人标题，绑定或未绑定 APP 的 full Key 都必须拒绝。"""
    async with sessions() as db:
        department = Department(name="mention-auth")
        db.add(department)
        await db.flush()
        user = await db.scalar(select(User).where(User.uid == SCOPE.uid))
        user.department_id = department.id
        key, key_hash, key_prefix = AuthUtils.generate_api_key()
        db.add(
            APIKey(
                key_hash=key_hash,
                key_prefix=key_prefix,
                name="mention-auth",
                user_id=user.id,
                created_by=user.uid,
                access_level="full",
                app_id=app_id,
            )
        )
        await db.commit()
        jwt = AuthUtils.create_access_token({"sub": str(user.id), "auth_method": "password"})

    app = FastAPI()
    app.include_router(mention_router, prefix="/api")

    async def database():
        """认证及标题读取共用当前测试的隔离数据库。"""
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    async with serve_http(app) as client:
        assert (await client.get("/api/mention/sessions")).status_code == 401
        response = await client.get("/api/mention/sessions", headers={"Authorization": f"Bearer {jwt}"})
        assert response.status_code == 200, response.text
        assert [row["thread_id"] for row in response.json()] == ["input-thread"]
        denied = await client.get("/api/mention/sessions", headers={"Authorization": f"Bearer {key}"})
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"] == "对话引用仅支持 Web 登录用户"
