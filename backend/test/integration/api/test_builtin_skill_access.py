"""内置 Skill 初始化在独立 PostgreSQL 与真实 HTTP 中保持普通用户读取权限。"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.services.agent_run_test_helpers import create_agent_run
from test.integration.support.module_audit import serve_http
from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.api.routers.extensions.skills import user_skills
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.migrations.schema import create_business_tables
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.runtime.context import BaseContext, refresh_agent_context_resources
from yuxi.modules.extensions.skills.builtin.sync import init_builtin_skills
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.identity.models import Department, User
from yuxi.modules.identity.permissions import ResourcePermission, resolve_skill_permission
from yuxi.modules.identity.security import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]

LEGACY_DEFAULT = {"access_level": "global", "department_ids": [], "user_uids": []}
BUILTIN_READ_SCOPE = {
    "version": 2,
    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
    "manage_scope": None,
}


@pytest.fixture
async def builtin_store(monkeypatch, tmp_path):
    """只写本例 Schema 和临时 Skill 目录，不使用运行服务的共享权限或用户。"""
    monkeypatch.setenv("YUXI_SKILL_DATA_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("YUXI_SKILL_PROJECTION_DIR", str(tmp_path / "projections"))
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "users"))
    monkeypatch.setenv("YUXI_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", uuid4().hex + uuid4().hex)
    monkeypatch.setenv("YUXI_INSTANCE_ID", "pytest-builtin-access")
    schema, admin, engine, manager = await create_isolated_manager("pytest_builtin_access")
    manager.AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(pg_manager, "get_async_session_context", manager.get_async_session_context)
    try:
        await create_business_tables(manager)
        async with manager.get_async_session_context() as db:
            await init_builtin_skills(db)
        yield SimpleNamespace(sessions=manager.AsyncSession, skill_root=tmp_path / "skills")
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_new_builtin_is_readable_and_preloads_for_employee(builtin_store):
    """普通员工可经 HTTP 发现内置 Skill，并预加载其正式发布的内容。"""
    store = builtin_store
    slug = "chat-widgets"
    run_id, thread_id, _ = await create_agent_run(
        store.sessions,
        prefix="builtin-skill",
        message_content="查询知识库字段",
        input_payload={"context_snapshot": {"knowledges": [], "knowledge_files": None}},
    )
    async with store.sessions() as db:
        session = await db.scalar(select(Session).where(Session.thread_id == thread_id))
        user = await db.scalar(select(User).where(User.uid == session.uid))
        department = Department(name="builtin-readers")
        db.add(department)
        await db.flush()
        user.role, user.department_id = "user", department.id
        await db.commit()
        token = AuthUtils.create_access_token({"sub": str(user.id), "auth_method": "password"})
        persisted = await db.scalar(select(Skill).where(Skill.slug == slug))
        assert resolve_skill_permission(user, persisted) == ResourcePermission.READ
        assert persisted.share_config == BUILTIN_READ_SCOPE

    app = FastAPI()
    app.include_router(user_skills, prefix="/api")
    async with serve_http(app) as client:
        response = await client.get("/api/skills/accessible", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        skill = next((item for item in response.json()["data"] if item["slug"] == slug), None)
        assert skill is not None, response.json()
        assert "mcp_dependencies" not in skill
        assert "tool_dependencies" not in skill

    context = BaseContext(
        uid=user.uid,
        run_id=run_id,
        thread_id=thread_id,
        skills=[slug],
        preload_skills=[slug],
        tools=[],
        mcps=[],
        knowledges=[],
    )
    async with store.sessions() as db:
        await refresh_agent_context_resources(context, db=db, user=user)
    assert context.skills == context.preload_skills == [slug]
    assert context.mcps == []
    assert context.skill_runtime_snapshot["preloaded_skill_contents"][slug] == (
        store.skill_root / "shared" / slug / "SKILL.md"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("share_config", "expected"),
    [
        (LEGACY_DEFAULT, BUILTIN_READ_SCOPE),
        (BUILTIN_READ_SCOPE, BUILTIN_READ_SCOPE),
        (
            {"version": 2, "read_scope": {"access_level": "user", "user_uids": ["custom-reader"]}, "manage_scope": None},
            {"version": 2, "read_scope": {"access_level": "user", "user_uids": ["custom-reader"]}, "manage_scope": None},
        ),
        ({"access_level": "user", "user_uids": ["custom-reader"]}, {"access_level": "user", "user_uids": ["custom-reader"]}),
        ({**LEGACY_DEFAULT, "custom": True}, {**LEGACY_DEFAULT, "custom": True}),
    ],
    ids=["legacy-default", "current-default", "custom-v2", "unknown-legacy", "extended-legacy"],
)
@pytest.mark.parametrize("enabled", [True, False], ids=["enabled", "disabled"])
async def test_builtin_resync_repairs_only_old_default_and_preserves_existing_choices(builtin_store, share_config, expected, enabled):
    """重复初始化只修复准确旧默认；启停、分享、身份与来源内容由独立回读确认。"""
    store = builtin_store
    async with store.sessions() as db:
        row = await db.scalar(select(Skill).where(Skill.slug == "knowledge-base"))
        row.share_config, row.enabled = deepcopy(share_config), enabled
        await db.commit()
        before = {key: value for key, value in row.to_dict().items() if key not in {"share_config", "updated_at", "updated_by"}}
        content = (store.skill_root / row.dir_path / "SKILL.md").read_bytes()
        identity = row.id

    for _ in range(2):
        async with store.sessions() as db:
            await init_builtin_skills(db, created_by="release-fixture")
        async with store.sessions() as db:
            row = await db.scalar(select(Skill).where(Skill.slug == "knowledge-base"))
            assert row.id == identity and row.share_config == expected and row.enabled is enabled
            assert {key: value for key, value in row.to_dict().items() if key not in {"share_config", "updated_at", "updated_by"}} == before
            assert (store.skill_root / row.dir_path / "SKILL.md").read_bytes() == content
            employee = User(uid="ordinary-reader", username="ordinary-reader", role="user", department_id=1)
            readable = [item.slug for item in await SkillRepository(db).list_enabled_readable(employee)]
            assert ("knowledge-base" in readable) is (enabled and expected == BUILTIN_READ_SCOPE)
