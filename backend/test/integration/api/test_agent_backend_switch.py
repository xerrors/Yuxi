"""智能体后端切换通过 HTTP 路由与隔离 PostgreSQL 验证。"""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.agents.management import agent_router
from yuxi.migrations.schema import create_business_tables
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.runtime import base as agent_base
from yuxi.modules.agents.runtime.agent_backends import AGENT_BACKENDS
from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
from yuxi.modules.agents.runtime.agent_backends.chatbot.graph import ChatbotAgent
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.models import Project
from yuxi.shared.errors import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def agent_api(monkeypatch):
    """在独立 Schema 中装配真实管理路由与当前用户。"""
    schema, admin_engine, engine, manager = await create_isolated_manager("pytest_agent_backend_switch")
    await create_business_tables(manager)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    uid = f"backend-switch-{uuid4().hex}"
    user = User(uid=uid, username=uid, role="superadmin", password_hash="test-only", is_deleted=0)

    async def empty_resource_options(*_args, **_kwargs):
        """隔离测试不读取共享模型与资源目录。"""
        return {}

    @dataclass
    class TestBackendContext(ChatBotContext):
        """用于证明切换后会移除旧后端的专属配置。"""

        test_backend_setting: str = "enabled"

    class SwitchTestBackend(ChatbotAgent):
        """用已注册的测试后端验证配置迁移。"""

        context_schema = TestBackendContext

    @dataclass
    class DifferentFieldContext(ChatBotContext):
        """同名字段使用不同声明，保存时不能继承原值。"""

        system_prompt: int = 0

    class DifferentFieldBackend(ChatbotAgent):
        """注册声明不兼容配置的测试后端。"""

        context_schema = DifferentFieldContext

    monkeypatch.setitem(AGENT_BACKENDS, "DifferentFieldBackend", DifferentFieldBackend)
    monkeypatch.setitem(AGENT_BACKENDS, "SwitchTestBackend", SwitchTestBackend)
    monkeypatch.setattr(agent_base, "resolve_agent_resource_options", empty_resource_options)
    async with sessions() as db:
        db.add(user)
        await db.commit()

    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(agent_router, prefix="/api")

    async def override_db():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_required_user] = lambda: user

    async def create_agent(*, backend_id="ChatbotAgent", config=None, slug=None, builtin=False):
        agent_slug = slug or f"backend-switch-{uuid4().hex}"
        async with sessions() as db:
            db.add(
                Agent(
                    slug=agent_slug,
                    name="后端切换测试",
                    backend_id=backend_id,
                    visibility="shared" if builtin else "private",
                    is_builtin=builtin,
                    created_by=uid,
                    config_json=config or {"context": {}},
                    share_config=(
                        {"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None}
                        if builtin
                        else {"version": 2, "read_scope": None, "manage_scope": None}
                    ),
                )
            )
            await db.commit()
        return agent_slug

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(client=client, sessions=sessions, uid=uid, create_agent=create_agent)
    finally:
        await drop_isolated_schema(schema, admin_engine, engine)


async def create_session(agent_api, slug):
    """创建本测试 Agent 的空闲 Thread。"""
    thread_id = str(uuid4())
    project_id = str(uuid4())
    async with agent_api.sessions() as db:
        db.add(
            Project(
                id=project_id,
                uid=agent_api.uid,
                selection_status="implicit",
                workdir_path="projects/backend-switch-test",
                directory_mode="managed",
                status="active",
            )
        )
        await db.flush()
        db.add(
            Session(
                thread_id=thread_id,
                tree_root_thread_id=thread_id,
                uid=agent_api.uid,
                agent_id=slug,
                title="后端切换测试",
                status="active",
                project_id=project_id,
                config_snapshot={"system_prompt": "Thread 快照", "model": "test:model", "tool_approval_mode": "default"},
            )
        )
        await db.commit()
    return thread_id


async def test_backend_switch_updates_http_and_persisted_config(agent_api):
    """切换后端时回读目标 Schema 过滤结果与既有 Thread 快照。"""
    slug = await agent_api.create_agent(
        backend_id="SwitchTestBackend",
        config={"context": {"system_prompt": "保留的提示词", "test_backend_setting": "enabled"}},
    )
    thread_id = await create_session(agent_api, slug)

    response = await agent_api.client.put(
        f"/api/agent/{slug}",
        json={"backend_id": "ChatbotAgent", "config_json": {"context": {"system_prompt": "保留的提示词"}}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["agent"]["backend_id"] == "ChatbotAgent"
    async with agent_api.sessions() as db:
        persisted = await db.scalar(select(Agent).where(Agent.slug == slug))
        thread = await db.scalar(select(Session).where(Session.thread_id == thread_id))
        assert persisted.backend_id == "ChatbotAgent"
        assert persisted.config_json["context"] == {"system_prompt": "保留的提示词"}
        assert thread.config_snapshot == {"system_prompt": "Thread 快照", "model": "test:model", "tool_approval_mode": "default"}


@pytest.mark.parametrize("work_kind", ["pending_input", "active_turn", "active_run", "runtime_cleanup"])
async def test_backend_switch_rejects_unfinished_work_without_persisting_changes(agent_api, work_kind):
    """未完成输入、执行或清理责任必须阻止切换并保持定义不变。"""
    slug = await agent_api.create_agent(config={"context": {"system_prompt": "before"}})
    thread_id = await create_session(agent_api, slug)
    work_id = uuid4().hex
    turn_id = uuid4().hex
    run_id = uuid4().hex

    async with agent_api.sessions() as db:
        session = await db.scalar(select(Session).where(Session.thread_id == thread_id))
        if work_kind == "pending_input":
            db.add(
                AgentInput(
                    id=work_id,
                    thread_id=thread_id,
                    uid=agent_api.uid,
                    agent_slug=slug,
                    kind="follow_up",
                    status="pending",
                    messages=[{"content": [{"type": "text", "text": "pending"}]}],
                    input_payload={},
                )
            )
        elif work_kind == "active_turn":
            db.add(AgentTurn(id=turn_id, thread_id=thread_id, uid=agent_api.uid, status="running"))
        else:
            db.add(AgentTurn(id=turn_id, thread_id=thread_id, uid=agent_api.uid, status="completed"))
            await db.flush()
            db.add(
                AgentRun(
                    id=run_id,
                    thread_id=thread_id,
                    runtime_scope_id=thread_id,
                    agent_slug=slug,
                    uid=agent_api.uid,
                    turn_id=turn_id,
                    session_record_id=session.id,
                    status="running" if work_kind == "active_run" else "completed",
                    runtime_cleanup_pending=work_kind == "runtime_cleanup",
                    run_type="chat",
                    input_payload={},
                    token_usage={},
                    origin_metadata={},
                )
            )
        await db.commit()

    response = await agent_api.client.put(
        f"/api/agent/{slug}",
        json={"backend_id": "SwitchTestBackend", "config_json": {"context": {"system_prompt": "after"}}},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "agent_backend_busy"
    async with agent_api.sessions() as db:
        persisted = await db.scalar(select(Agent).where(Agent.slug == slug))
        assert persisted.backend_id == "ChatbotAgent"
        assert persisted.config_json["context"]["system_prompt"] == "before"


async def test_backend_switch_waits_for_input_acceptance_and_rejects_pending_input(agent_api):
    """接收事务持锁时切换等待，并在输入提交后拒绝更新。"""
    slug = await agent_api.create_agent(config={"context": {"system_prompt": "before"}})
    thread_id = await create_session(agent_api, slug)
    async with agent_api.sessions() as receiver_db:
        await SessionRepository(receiver_db).lock_session_by_thread_id(thread_id)
        receiver_pid = await receiver_db.scalar(text("SELECT pg_backend_pid()"))
        switch = asyncio.create_task(
            agent_api.client.put(
                f"/api/agent/{slug}",
                json={"backend_id": "SwitchTestBackend"},
            )
        )

        try:
            blocked_by_receiver = False
            async with asyncio.timeout(5):
                while not switch.done():
                    async with agent_api.sessions() as observer_db:
                        blocked_by_receiver = await observer_db.scalar(
                            text(
                                """
                                SELECT EXISTS (
                                    SELECT 1
                                    FROM pg_stat_activity
                                    WHERE wait_event_type = 'Lock'
                                      AND :receiver_pid = ANY(pg_blocking_pids(pid))
                                      AND query ILIKE '%FROM sessions%'
                                      AND query ILIKE '%FOR NO KEY UPDATE%'
                                )
                                """
                            ),
                            {"receiver_pid": receiver_pid},
                        )
                    if blocked_by_receiver:
                        break
                    await asyncio.sleep(0.05)

            assert blocked_by_receiver, "切换请求应在 PostgreSQL 中等待接收事务持有的 Thread 行锁"
            receiver_db.add(
                AgentInput(
                    id=uuid4().hex,
                    thread_id=thread_id,
                    uid=agent_api.uid,
                    agent_slug=slug,
                    kind="follow_up",
                    status="pending",
                    messages=[{"content": [{"type": "text", "text": "concurrent input"}]}],
                    input_payload={},
                )
            )
            await receiver_db.commit()
        finally:
            await receiver_db.rollback()
            if not switch.done():
                await asyncio.wait_for(switch, timeout=5)

    response = await asyncio.wait_for(switch, timeout=5)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "agent_backend_busy"
    async with agent_api.sessions() as db:
        persisted = await db.scalar(select(Agent).where(Agent.slug == slug))
        assert persisted.backend_id == "ChatbotAgent"


async def test_builtin_agent_backend_cannot_be_switched(agent_api):
    """内置 Agent 的执行后端保持不可变。"""
    slug = await agent_api.create_agent(slug="default-chatbot", builtin=True)

    response = await agent_api.client.put(f"/api/agent/{slug}", json={"backend_id": "SwitchTestBackend"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "agent_backend_immutable"
    async with agent_api.sessions() as db:
        persisted = await db.scalar(select(Agent).where(Agent.slug == slug))
        assert persisted.backend_id == "ChatbotAgent"


async def test_switch_drops_same_name_fields_with_incompatible_declaration(agent_api):
    """不把旧后端的同名配置交给类型和权限声明不同的后端。"""
    slug = await agent_api.create_agent(config={"context": {"system_prompt": "old prompt"}})
    response = await agent_api.client.put(f"/api/agent/{slug}", json={"backend_id": "DifferentFieldBackend"})
    assert response.status_code == 200, response.text
    async with agent_api.sessions() as db:
        current = await db.scalar(select(Agent).where(Agent.slug == slug))
        assert current.backend_id == "DifferentFieldBackend"
        assert "system_prompt" not in current.config_json["context"]


async def test_switch_rejects_incompatible_existing_thread_snapshot(agent_api):
    """已有会话保存同名异型配置时，后端切换应保留原定义。"""
    slug = await agent_api.create_agent(config={"context": {"system_prompt": "old prompt"}})
    await create_session(agent_api, slug)

    response = await agent_api.client.put(f"/api/agent/{slug}", json={"backend_id": "DifferentFieldBackend"})

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "agent_backend_thread_config_conflict"
    async with agent_api.sessions() as db:
        persisted = await db.scalar(select(Agent).where(Agent.slug == slug))
        assert persisted.backend_id == "ChatbotAgent"
