"""共享 Agent 输入集成测试的隔离数据库与接收样板。"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import yuxi.modules.agents.services.run_lifecycle as lifecycle
from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.migrations.schema import create_business_tables
from yuxi.modules.agents.input_messages import build_chat_input_message, serialize_input_message
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.repositories.definitions import DEFAULT_SHARE_CONFIG
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.input_receipt import AgentInputReceiptRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services import input_config, inputs, scheduler, threads, turns
from yuxi.modules.system.models import ConfigOption
from yuxi.modules.system.options import system_options
from yuxi.modules.workspace.paths import user_workspace_dir

SCOPE = ActorScope(uid="input-user", app_id=None)


async def create_input_schema():
    """建立输入测试专用 Schema，并播种单一会话。"""
    schema = f"pytest_agent_input_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = engine
    manager._initialized = True
    await create_business_tables(manager)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(ConfigOption(key=system_options.key, name=system_options.name, params=system_options.params, value={}))
        await db.commit()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (username, uid, password_hash, role, login_failed_count, is_deleted) VALUES "
                "('input-user', 'input-user', 'hash', 'user', 0, 0)"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO projects (id, uid, selection_status, workdir_path, directory_mode) "
                "VALUES ('input-project', 'input-user', 'implicit', 'projects/input-project', 'managed')"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO sessions "
                "(thread_id, tree_root_thread_id, uid, agent_id, project_id, is_pinned, status, config_snapshot) "
                "VALUES ('input-thread', 'input-thread', 'input-user', 'main', 'input-project', false, 'active', "
                '\'{"model":"test:chat","tool_approval_mode":"default"}\')'
            )
        )
    return schema, admin_engine, engine


async def drop_input_schema(schema: str, admin_engine, engine) -> None:
    """释放输入用例创建的独立 Schema。"""
    await engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin_engine.dispose()


async def queue_inputs(sessions, *, count: int) -> None:
    """写入接收回执并保留输入 FIFO 序号。"""
    async with sessions() as db:
        for number in range(count):
            input_id = f"input-{number}"
            item = await AgentInputRepository(db).create(
                input_id=input_id,
                messages=[serialize_input_message(build_chat_input_message(f"message-{number}"))],
                thread_id="input-thread",
                uid=SCOPE.uid,
                app_id=None,
                agent_slug="main",
                kind="follow_up",
                input_payload={"context_snapshot": {"model": "ci-replay:deterministic-chat", "tool_approval_mode": "default"}},
            )
            await AgentInputReceiptRepository(db).create(
                receipt_id=f"receipt-{number}",
                idempotency_key=f"key-{number}",
                uid=SCOPE.uid,
                app_id=None,
                thread_id="input-thread",
                event_type="agent.session.input.message",
                intent_hash=f"intent-{number}",
                input_id=item.id,
            )
        await db.commit()


async def send_input(sessions, content: str, mode: str = "steer") -> dict:
    """提交队列测试输入，用户主动恢复由专门的 HTTP 用例验证。"""
    async with sessions() as db:
        return await inputs.accept_message(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            idempotency_key=content,
            source="test_queue",
            mode=mode,
            messages=[build_chat_input_message(content)],
        )


@pytest_asyncio.fixture
async def sessions(monkeypatch, tmp_path):
    """为输入契约建真实数据库，替代外部发现、沙盒、投递与通知。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    (user_workspace_dir(SCOPE.uid) / "projects/input-project").mkdir(parents=True)
    schema, admin_engine, engine = await create_input_schema()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(
            Agent(
                slug="main",
                backend_id="ChatbotAgent",
                name="test",
                created_by=SCOPE.uid,
                share_config=DEFAULT_SHARE_CONFIG.copy(),
                config_json={"context": {"model": "test:chat"}},
            )
        )
        agent_session = await SessionRepository(db).get_session_by_thread_id("input-thread")
        agent_session.queue_paused = True
        agent_session.config_snapshot = {"model": "test:chat", "tool_approval_mode": "default"}
        await db.commit()

    async def deliver(claimed_run_id):
        """回读已提交 Run，避免触碰 Redis worker。"""
        async with factory() as db:
            assert (await db.get(AgentRun, claimed_run_id)).status == "pending"
        return True

    @asynccontextmanager
    async def context():
        """为调度代码提供当前隔离 schema 的短事务。"""
        async with factory() as db:
            yield db
            await db.commit()

    notify = AsyncMock()

    monkeypatch.setattr(input_config.model_cache, "get_model_info", lambda spec: SimpleNamespace(model_type="chat"))
    monkeypatch.setattr(lifecycle, "get_sandbox_provider", lambda: SimpleNamespace(cleanup_run=lambda *_: None))
    monkeypatch.setattr(lifecycle, "finish_turn_observation_if_terminal", notify)
    monkeypatch.setattr(turns, "finish_turn_observation_if_terminal", notify)
    monkeypatch.setattr(turns, "publish_cancel_signals", notify)
    monkeypatch.setattr(threads, "deliver", deliver)
    monkeypatch.setattr(scheduler, "deliver", deliver)
    monkeypatch.setattr(scheduler.pg_manager, "get_async_session_context", context)
    try:
        yield factory
    finally:
        await drop_input_schema(schema, admin_engine, engine)


async def start_input_run(sessions, *, running: bool = True):
    """领取队首并按需设置实际执行 owner。"""
    await queue_inputs(sessions, count=1)
    async with sessions() as db:
        agent_session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
        agent_session.queue_paused = False
        claimed_run_id = await scheduler.claim_next_input(db=db, agent_session=agent_session)
        run = await db.get(AgentRun, claimed_run_id)
        if running:
            run, acquired = await AgentRunRepository(db).mark_running(claimed_run_id, worker_id="owner", lease_seconds=60)
            assert acquired
        await db.commit()
        return run
