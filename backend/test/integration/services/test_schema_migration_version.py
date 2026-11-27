"""Yuxi Schema 版本事实在真实 PostgreSQL 上的集成测试。"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.infrastructure.postgres.connection import postgres_conninfo
from yuxi.modules.agents.models.sandbox import UserSandbox
from yuxi.infrastructure.postgres.schema import BUSINESS_SCHEMA_VERSION, KNOWLEDGE_SCHEMA_VERSION
from yuxi.migrations import main as schema_bootstrap
from yuxi.migrations.schema import (
    create_business_tables,
    create_knowledge_tables,
    create_schema_version_table,
    ensure_business_schema,
    record_schema_version,
)
from yuxi.infrastructure.postgres.schema import get_schema_versions, require_current_schema
from yuxi.modules.knowledge.models import KnowledgeBase
from test.integration.support.postgres_schema import (
    create_isolated_manager as _create_isolated_manager,
    drop_isolated_schema as _drop_isolated_schema,
    scoped_manager as _scoped_manager,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.mark.parametrize(
    "business,knowledge",
    [(2, 1), (3, 1), (3, 2), (4, 1), (4, 2), (5, 1), (5, 2), (6, 1), (6, 2), (7, 1), (7, 2), (9, 2), (16, 2), (1, 1), (1, 3)],
)
async def test_incompatible_schema_is_rejected_without_modifying_data(monkeypatch, business, knowledge):
    """历史及未来版本不再隐式升级，数据与两个版本均保持原值。"""
    schema, admin, engine, manager = await _create_isolated_manager("pytest_old_business")
    try:
        await create_business_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", business)
        await record_schema_version(manager, "knowledge", knowledge)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('existing','existing','original','user',0,0)"
                )
            )
        monkeypatch.setattr(manager, "initialize", lambda: None)
        monkeypatch.setattr(manager, "close", AsyncMock())
        with pytest.raises(RuntimeError, match="incomplete or incompatible"):
            await require_current_schema(manager)
        with pytest.raises(RuntimeError, match="Unsupported Yuxi schema"):
            await schema_bootstrap.initialize_schema(manager)
        assert await get_schema_versions(manager) == {"business": business, "knowledge": knowledge}
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT password_hash FROM users WHERE uid='existing'")) == "original"
            assert await conn.scalar(text("SELECT count(*) FROM oidc_identities")) == 0
    finally:
        await _drop_isolated_schema(schema, admin, engine)


async def test_legacy_business_8_without_usage_table_cannot_start_or_replay():
    """同编号的旧开发库缺少用量表时拒绝接管，保留原数据。"""
    from yuxi.migrations.workspace_usage import upgrade_workspace_usage

    schema, admin, engine, manager = await _create_isolated_manager("pytest_legacy_business_8")
    try:
        await create_business_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 8)
        await record_schema_version(manager, "knowledge", 2)
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE workspace_usage"))
            await conn.execute(
                text(
                    "INSERT INTO users (username, uid, password_hash, role, login_failed_count, is_deleted) "
                    "VALUES ('old','old','original','user',0,0)"
                )
            )
        for entrypoint in (require_current_schema, schema_bootstrap.initialize_schema, upgrade_workspace_usage):
            with pytest.raises(RuntimeError, match="workspace_usage is missing"):
                await entrypoint(manager)
        assert await get_schema_versions(manager) == {"business": 8, "knowledge": 2}
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT password_hash FROM users WHERE uid='old'")) == "original"
            assert await conn.scalar(text("SELECT to_regclass('workspace_usage')")) is None
    finally:
        await _drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("business", [7, 8])
async def test_same_business_version_without_recent_files_is_rejected(business):
    """同编号开发分支缺最近文件表时，不得应用迁移或通过启动校验。"""
    from yuxi.migrations.workspace_usage import upgrade_workspace_usage

    schema, admin, engine, manager = await _create_isolated_manager("pytest_other_business_branch")
    try:
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", business)
        await record_schema_version(manager, "knowledge", 2)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE users (uid VARCHAR(128) PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO users VALUES ('legacy')"))
            if business == 8:
                await conn.execute(text("CREATE TABLE workspace_usage (uid VARCHAR(128) PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match="workspace_recent_files is missing"):
            await upgrade_workspace_usage(manager)
        if business == 8:
            for entrypoint in (require_current_schema, schema_bootstrap.initialize_schema):
                with pytest.raises(RuntimeError, match="workspace_recent_files is missing"):
                    await entrypoint(manager)
        assert await get_schema_versions(manager) == {"business": business, "knowledge": 2}
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT uid FROM users")) == "legacy"
            assert await conn.scalar(text("SELECT to_regclass('workspace_usage') IS NOT NULL")) == (business == 8)
    finally:
        await _drop_isolated_schema(schema, admin, engine)


async def test_schema_migration_lock_serializes_real_postgres_sessions() -> None:
    """两个 migrator 竞争同一 advisory lock 时只允许一个进入临界区。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    manager = _scoped_manager(engine)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_migrator() -> None:
        async with manager.schema_migration_lock():
            first_entered.set()
            await release_first.wait()

    async def second_migrator() -> None:
        await first_entered.wait()
        async with manager.schema_migration_lock():
            second_entered.set()

    first_task = asyncio.create_task(first_migrator())
    second_task = asyncio.create_task(second_migrator())
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=2)
        await asyncio.sleep(0.1)
        assert second_entered.is_set() is False
        release_first.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2)
        assert second_entered.is_set() is True
    finally:
        release_first.set()
        for task in (first_task, second_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(first_task, second_task, return_exceptions=True)
        await engine.dispose()


async def test_fresh_business_schema_contains_input_lifecycle_without_request_table() -> None:
    """新环境直接建立 Input、Receipt、Turn 和 Run 当前结构，不执行历史补列。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_agent_schema")
    try:
        await create_business_tables(manager)
        await ensure_business_schema(manager)
        async with scoped_engine.connect() as connection:
            tables = set(
                (
                    await connection.execute(
                        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"),
                        {"schema": schema},
                    )
                ).scalars()
            )
            run_columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns WHERE table_schema = :schema AND table_name = 'agent_runs'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            input_columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns WHERE table_schema = :schema AND table_name = 'agent_inputs'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            session_columns = set(
                (
                    await connection.execute(
                        text("SELECT column_name FROM information_schema.columns WHERE table_schema = :schema AND table_name = 'sessions'"),
                        {"schema": schema},
                    )
                ).scalars()
            )
            execution_seq_nullable = await connection.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns WHERE table_schema = :schema AND table_name = 'agent_runs' AND column_name = 'execution_seq'"
                ),
                {"schema": schema},
            )
            execution_seq_default = await connection.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns WHERE table_schema = :schema AND table_name = 'agent_runs' AND column_name = 'execution_seq'"
                ),
                {"schema": schema},
            )
        assert {
            "sessions",
            "agent_turns",
            "agent_runs",
            "agent_inputs",
            "agent_input_receipts",
        } <= tables
        assert "agent_input_messages" not in tables
        assert "conversations" not in tables
        assert "agent_run_requests" not in tables
        assert "agent_session_input_receipts" not in tables
        assert {"turn_id", "resume_from_run_id", "session_record_id", "thread_id"} <= run_columns
        assert {"conversation_id", "conversation_thread_id"}.isdisjoint(run_columns)
        assert execution_seq_nullable == "NO"
        assert execution_seq_default and "nextval" in execution_seq_default
        assert "agent_runs_execution_seq" in execution_seq_default
        assert "input_id" not in run_columns
        assert "cutoff_seq" not in input_columns
        assert "request_id" not in run_columns
        assert {
            "kind",
            "status",
            "turn_id",
            "consumed_run_id",
            "messages",
            "received_seq",
            "thread_id",
        } <= input_columns
        assert "conversation_thread_id" not in input_columns
        assert "agent_state_view" in session_columns
        assert {"workspace_recent_files", "workspace_usage"} <= tables
        assert BUSINESS_SCHEMA_VERSION == 8
        assert KNOWLEDGE_SCHEMA_VERSION == 2
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_knowledge_child_cannot_cross_database_boundaries() -> None:
    """Chunk 的 file_id 与 kb_id 不一致时由复合外键拒绝写入。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_knowledge_boundary")
    try:
        await create_knowledge_tables(manager)
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO knowledge_bases (kb_id, name, kb_type) VALUES ('kb-a', 'A', 'milvus'), ('kb-b', 'B', 'milvus')")
            )
            await connection.execute(text("INSERT INTO knowledge_files (file_id, kb_id, filename) VALUES ('file-a', 'kb-a', 'a.txt')"))

        with pytest.raises(IntegrityError):
            async with scoped_engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO knowledge_chunks (chunk_id, file_id, kb_id, chunk_index, content) VALUES ('chunk-cross', 'file-a', 'kb-b', 0, 'invalid')"
                    )
                )
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_knowledge_timestamp_defaults_match_database_clock_in_non_utc_session() -> None:
    """带时区字段不依赖 PostgreSQL 会话时区解释无时区 UTC。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_knowledge_clock")
    try:
        await create_knowledge_tables(manager)
        session_factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with session_factory.begin() as session:
            await session.execute(text("SET TIME ZONE 'Asia/Shanghai'"))
            database = KnowledgeBase(kb_id="clock-kb", name="clock", kb_type="milvus")
            session.add(database)
            await session.flush()
            database_now = await session.scalar(text("SELECT clock_timestamp()"))

        async with session_factory() as verify_session:
            await verify_session.execute(text("SET TIME ZONE 'UTC'"))
            persisted_at = await verify_session.scalar(text("SELECT created_at FROM knowledge_bases WHERE kb_id = 'clock-kb'"))

        assert persisted_at.tzinfo is not None
        assert abs((persisted_at - database_now).total_seconds()) < 2
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_open_attempt_is_unique_in_fresh_business_schema() -> None:
    """结果消息必须是已存在记录，且同一 Run 不能有两个开放 Attempt。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_execution_constraints")
    try:
        await create_business_tables(manager)
        await ensure_business_schema(manager)
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (username, uid, password_hash, role, user_kind, is_deleted, login_failed_count) "
                    "VALUES ('constraint-user', 'constraint-user', 'test', 'user', 'human', 0, 0)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, uid, selection_status, workdir_path, directory_mode, status) "
                    "VALUES ('constraint-project', 'constraint-user', 'implicit', '/tmp/constraint', "
                    "'managed', 'active')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO sessions "
                    "(thread_id, tree_root_thread_id, uid, agent_id, project_id, status, is_pinned) "
                    "VALUES ('constraint-thread', 'constraint-thread', 'constraint-user', 'default', 'constraint-project', 'active', false)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO agent_turns "
                    "(id, thread_id, uid, status, created_at) "
                    "VALUES ('constraint-turn', 'constraint-thread', 'constraint-user', 'running', CURRENT_TIMESTAMP)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO agent_runs "
                    "(id, thread_id, runtime_scope_id, agent_slug, uid, status, turn_id, "
                    "source, channel, run_type, origin_metadata, input_payload, token_usage, runtime_cleanup_pending) "
                    "VALUES ('constraint-run', 'constraint-thread', 'constraint-thread', 'default', 'constraint-user', "
                    "'pending', 'constraint-turn', 'chat', 'web', 'chat', '{}', '{}', '{}', false)"
                )
            )

        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_runs SET session_record_id = (SELECT id FROM sessions WHERE thread_id = 'constraint-thread') WHERE id = 'constraint-run'"
                )
            )
            message_id = await connection.scalar(
                text(
                    "INSERT INTO messages (session_record_id, role, content, delivery_status) "
                    "SELECT id, 'assistant', 'unbound candidate', 'complete' FROM sessions "
                    "WHERE thread_id = 'constraint-thread' RETURNING id"
                )
            )
        for column in ("input_message_id", "output_message_id"):
            with pytest.raises(IntegrityError, match=f"fk_agent_runs_{column.split('_')[0]}_message_scope"):
                async with scoped_engine.begin() as connection:
                    await connection.execute(
                        text(f"UPDATE agent_runs SET {column} = :message_id WHERE id = 'constraint-run'"),
                        {"message_id": message_id},
                    )
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text("UPDATE messages SET run_id = 'constraint-run', turn_id = 'constraint-turn' WHERE id = :message_id"),
                {"message_id": message_id},
            )
            await connection.execute(
                text("UPDATE agent_runs SET output_message_id = :message_id WHERE id = 'constraint-run'"),
                {"message_id": message_id},
            )
        with pytest.raises(IntegrityError, match="ck_messages_execution_scope"):
            async with scoped_engine.begin() as connection:
                await connection.execute(text("UPDATE messages SET turn_id = NULL WHERE id = :message_id"), {"message_id": message_id})

        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO agent_run_attempts (run_id, attempt_no, worker_id, started_at) VALUES ('constraint-run', 1, 'worker-a', CURRENT_TIMESTAMP)"
                )
            )

        with pytest.raises(IntegrityError):
            async with scoped_engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO agent_run_attempts (run_id, attempt_no, worker_id, started_at) VALUES ('constraint-run', 2, 'worker-b', CURRENT_TIMESTAMP)"
                    )
                )
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_failed_fresh_initialization_cleans_checkpoint_tables_and_can_retry(monkeypatch):
    """在 checkpoint 建表后注入故障，回读空 Schema 并重新运行初始化。"""
    schema, admin, engine, manager = await _create_isolated_manager("pytest_bootstrap_retry")
    manager.AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(manager, "initialize", lambda: None)
    monkeypatch.setattr(manager, "close", AsyncMock())

    async def checkpoint_stage(_manager):
        """模拟跨连接 checkpoint 阶段已经提交的四张持久表。"""
        async with engine.begin() as connection:
            for name in ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"):
                await connection.execute(text(f"CREATE TABLE {name} (id INTEGER)"))

    async def failed_checkpoint_stage(_manager):
        """模拟 checkpoint 表已提交后失败。"""
        await checkpoint_stage(_manager)
        raise RuntimeError("after checkpoint")

    monkeypatch.setattr(schema_bootstrap, "setup_langgraph_checkpointer", failed_checkpoint_stage)
    try:
        with pytest.raises(RuntimeError, match="after checkpoint"):
            await schema_bootstrap.initialize_schema(manager)
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))).all() == []
        monkeypatch.setattr(schema_bootstrap, "setup_langgraph_checkpointer", checkpoint_stage)
        await schema_bootstrap.initialize_schema(manager)
        await require_current_schema(manager)
        assert await get_schema_versions(manager) == {
            "business": BUSINESS_SCHEMA_VERSION,
            "knowledge": KNOWLEDGE_SCHEMA_VERSION,
        }
    finally:
        await _drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("table_name", ["sessions", "conversations", "tasks", "tool_calls"])
async def test_unversioned_existing_yuxi_table_blocks_fresh_schema_initialization(monkeypatch, table_name) -> None:
    """未版本化的旧表不得被空库初始化误认为全新部署。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_fresh_only")
    try:
        await schema_bootstrap._require_empty_database(manager)
        async with scoped_engine.begin() as connection:
            await connection.execute(text(f"CREATE TABLE {table_name} (id integer PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match=table_name):
            await schema_bootstrap._require_empty_database(manager)
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_schema_version_is_persisted_and_runtime_validation_fails_closed() -> None:
    """缺失、旧版、未来版本与有效 Schema 都形成精确启动结论。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_schema_version")

    try:
        with pytest.raises(RuntimeError, match="business=missing"):
            await require_current_schema(manager)

        await create_business_tables(manager)
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('legacy-user','legacy-user','hash','user',0,0)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO projects (id,uid,selection_status,workdir_path,directory_mode) "
                    "VALUES ('legacy-project','legacy-user','implicit','projects/legacy','managed')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO sessions (thread_id,tree_root_thread_id,uid,agent_id,project_id,status,is_pinned) "
                    "VALUES ('legacy-thread','legacy-thread','legacy-user','main','legacy-project','active',false)"
                )
            )
        await create_schema_version_table(manager)
        previous_version = BUSINESS_SCHEMA_VERSION - 1
        await record_schema_version(manager, "business", previous_version)
        with pytest.raises(RuntimeError, match=f"business={previous_version}"):
            await require_current_schema(manager)
        assert await get_schema_versions(manager) == {"business": previous_version}
        async with scoped_engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM sessions WHERE thread_id='legacy-thread'")) == 1

        incompatible_version = BUSINESS_SCHEMA_VERSION + 1
        await record_schema_version(manager, "business", incompatible_version)
        with pytest.raises(RuntimeError, match=f"business={incompatible_version}"):
            await require_current_schema(manager)
        assert (await get_schema_versions(manager))["business"] == incompatible_version

        await record_schema_version(manager, "business", BUSINESS_SCHEMA_VERSION)
        with pytest.raises(RuntimeError, match="knowledge=missing"):
            await require_current_schema(manager)

        await record_schema_version(manager, "knowledge", KNOWLEDGE_SCHEMA_VERSION)
        await require_current_schema(manager)
        assert await get_schema_versions(manager) == {
            "business": BUSINESS_SCHEMA_VERSION,
            "knowledge": KNOWLEDGE_SCHEMA_VERSION,
        }
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_background_job_baseline_replaces_task_table_and_knowledge_owner_column() -> None:
    """新库只建立后台作业模型，文件执行关联使用作业 ID。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_background_job_schema")
    try:
        await create_business_tables(manager)
        await create_knowledge_tables(manager)
        await ensure_business_schema(manager)
        async with scoped_engine.connect() as connection:
            tables = set(
                (
                    await connection.execute(
                        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"),
                        {"schema": schema},
                    )
                ).scalars()
            )
            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns WHERE table_schema = :schema AND table_name = 'knowledge_files'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            constraints = set(
                (
                    await connection.execute(
                        text(
                            "SELECT constraint_name FROM information_schema.table_constraints WHERE table_schema = :schema AND table_name = 'background_jobs'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
        assert "background_jobs" in tables
        assert {"tasks", "jobs"}.isdisjoint(tables)
        assert "processing_job_id" in columns and "processing_task_id" not in columns
        assert {"ck_background_jobs_status", "uq_background_jobs_active_dedupe"} <= constraints
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_fresh_initializer_creates_complete_baseline_and_restarts_without_changing_data(monkeypatch):
    """真实入口直接建立约束、沙盒与 checkpoint，重跑保留持久事实。"""
    schema, admin, engine, manager = await _create_isolated_manager("pytest_initial_baseline")
    manager.AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    pool = AsyncConnectionPool(
        conninfo=postgres_conninfo(os.environ["POSTGRES_URL"]),
        kwargs={"autocommit": True, "options": f"-c search_path={schema}"},
        open=False,
    )
    manager.langgraph_pool = pool
    monkeypatch.setattr(manager, "initialize", lambda: None)
    monkeypatch.setattr(manager, "close", AsyncMock())
    try:
        await pool.open()
        await schema_bootstrap.initialize_schema(manager)
        await require_current_schema(manager)
        assert await get_schema_versions(manager) == {"business": BUSINESS_SCHEMA_VERSION, "knowledge": KNOWLEDGE_SCHEMA_VERSION}
        async with engine.connect() as conn:
            tables = set((await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))).scalars())
            assert {"user_sandboxes", "sandbox_commands", "checkpoints", "checkpoint_blobs", "checkpoint_writes"} <= tables
            assert await conn.scalar(text("SELECT count(*) FROM checkpoint_migrations")) > 0
            paused_at = (
                await conn.execute(
                    text(
                        "SELECT data_type, is_nullable FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name = 'user_sandboxes' AND column_name = 'paused_at'"
                    )
                )
            ).one()
            assert tuple(paused_at) == ("timestamp with time zone", "YES")
        async with engine.connect() as conn:
            mcp_columns = set(
                (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema=current_schema() AND table_name='mcp_servers'"
                        )
                    )
                ).scalars()
            )
        assert {"mcp_servers", "mcp_connections", "mcp_oauth_attempts"} <= tables
        assert {"auth_mode", "oauth_config", "oauth_secret", "oauth_registration"} <= mcp_columns
        assert {"icon", "tags"}.isdisjoint(mcp_columns)
        async with manager.AsyncSession.begin() as session:
            session.add(UserSandbox(uid="fixture", envd_token="fixture-token", operation_id="fixture-operation"))
            session.add(KnowledgeBase(kb_id="existing", name="Preserved", kb_type="milvus"))
        async with engine.connect() as conn:
            sandbox_before = dict((await conn.execute(text("SELECT * FROM user_sandboxes WHERE uid='fixture'"))).mappings().one())
            assert sandbox_before["paused_at"] is None
            versions_before = (await conn.execute(text("SELECT * FROM yuxi_schema_migrations ORDER BY domain"))).all()
        await schema_bootstrap.initialize_schema(manager)
        async with engine.connect() as conn:
            assert dict((await conn.execute(text("SELECT * FROM user_sandboxes WHERE uid='fixture'"))).mappings().one()) == sandbox_before
            assert (await conn.execute(text("SELECT * FROM yuxi_schema_migrations ORDER BY domain"))).all() == versions_before
            assert await conn.scalar(text("SELECT name FROM knowledge_bases WHERE kb_id='existing'")) == "Preserved"
        with pytest.raises(IntegrityError, match="uq_knowledge_bases_active_name"):
            async with engine.begin() as conn:
                await conn.execute(text("INSERT INTO knowledge_bases (kb_id, name, kb_type) VALUES ('duplicate', 'PRESERVED', 'milvus')"))
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE knowledge_bases SET deleted_at=CURRENT_TIMESTAMP WHERE kb_id='existing'"))
            await conn.execute(text("INSERT INTO knowledge_bases (kb_id, name, kb_type) VALUES ('replacement', 'preserved', 'milvus')"))
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM knowledge_bases")) == 2
    finally:
        await pool.close()
        await _drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("business,knowledge", [(5, 2), (5, 1), (99, 2)])
async def test_agent_state_view_upgrade_is_explicit_and_preserves_existing_data(business, knowledge):
    """补列保留既有会话，并在写入前拒绝不兼容的域版本。"""
    from yuxi.migrations.agent_state_view import upgrade_agent_state_view

    schema, admin, engine, manager = await _create_isolated_manager("pytest_state_view_upgrade")
    read_applied_at = text("SELECT applied_at FROM yuxi_schema_migrations WHERE domain='business'")
    try:
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", business)
        await record_schema_version(manager, "knowledge", knowledge)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE sessions (thread_id TEXT PRIMARY KEY, title TEXT)"))
            await conn.execute(text("INSERT INTO sessions VALUES ('fixture', 'Kept')"))
        supported = business == 5 and knowledge == 2
        if supported:
            await upgrade_agent_state_view(manager)
            async with engine.connect() as conn:
                applied_at = await conn.scalar(read_applied_at)
            await upgrade_agent_state_view(manager)
        else:
            with pytest.raises(RuntimeError, match="business 5 或 6.*knowledge 为 2"):
                await upgrade_agent_state_view(manager)
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT title FROM sessions WHERE thread_id='fixture'")) == "Kept"
            columns = (
                (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='sessions'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert ("agent_state_view" in columns) == supported
            if supported:
                assert await conn.scalar(read_applied_at) == applied_at
        assert await get_schema_versions(manager) == {"business": 6 if supported else business, "knowledge": knowledge}
    finally:
        await _drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("knowledge", [1, 2])
async def test_workspace_recent_files_upgrade_preserves_users_and_is_idempotent(knowledge):
    """最近文件表由合法版本显式迁入，复跑保留已生成的打开记录。"""
    from yuxi.migrations.workspace_recent_files import upgrade_workspace_recent_files

    schema, admin, engine, manager = await _create_isolated_manager("pytest_recent_files_upgrade")
    try:
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 6)
        await record_schema_version(manager, "knowledge", knowledge)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE users (uid VARCHAR(128) PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO users VALUES ('existing')"))
        if knowledge == 1:
            with pytest.raises(RuntimeError, match="knowledge 为 2"):
                await upgrade_workspace_recent_files(manager)
            assert await get_schema_versions(manager) == {"business": 6, "knowledge": 1}
            async with engine.connect() as conn:
                assert await conn.scalar(text("SELECT to_regclass('workspace_recent_files')")) is None
            return

        await upgrade_workspace_recent_files(manager)
        assert await get_schema_versions(manager) == {"business": 7, "knowledge": 2}
        async with engine.begin() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM workspace_recent_files")) == 0
            await conn.execute(text("""INSERT INTO workspace_recent_files VALUES ('existing', '[{"path":"/old.txt"}]')"""))
        await upgrade_workspace_recent_files(manager)
        async with engine.begin() as conn:
            assert await conn.scalar(text("SELECT entries->0->>'path' FROM workspace_recent_files WHERE uid='existing'")) == "/old.txt"
            await conn.execute(text("DELETE FROM users WHERE uid='existing'"))
            assert await conn.scalar(text("SELECT count(*) FROM workspace_recent_files")) == 0
    finally:
        await _drop_isolated_schema(schema, admin, engine)


async def test_workspace_usage_migration_preserves_snapshots_on_replay():
    """验证 6→7→8，保留最近文件历史并可重复执行用量迁移。"""
    from yuxi.migrations.workspace_recent_files import upgrade_workspace_recent_files
    from yuxi.migrations.workspace_usage import upgrade_workspace_usage
    from yuxi.modules.workspace.repositories.usage import save_usage

    schema, admin, engine, manager = await _create_isolated_manager("pytest_usage_upgrade")
    try:
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 6)
        await record_schema_version(manager, "knowledge", 2)
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE users (uid VARCHAR(128) PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO users VALUES ('existing')"))
        with pytest.raises(RuntimeError, match="需要 business 7、knowledge 2"):
            await upgrade_workspace_usage(manager)
        assert await get_schema_versions(manager) == {"business": 6, "knowledge": 2}
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT to_regclass('workspace_usage')")) is None

        await upgrade_workspace_recent_files(manager)
        async with engine.begin() as conn:
            await conn.execute(text("""INSERT INTO workspace_recent_files VALUES ('existing', '[{"path":"/kept.txt"}]')"""))
        await upgrade_workspace_usage(manager)
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO workspace_usage VALUES ('existing', 4294967296, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        await upgrade_workspace_usage(manager)

        await require_current_schema(manager)
        async with engine.begin() as conn:
            assert await conn.scalar(text("SELECT entries FROM workspace_recent_files WHERE uid='existing'")) == [{"path": "/kept.txt"}]
            measured = await conn.scalar(text("SELECT measured_at FROM workspace_usage WHERE uid='existing'"))
            read_usage = text("SELECT used_bytes, measured_at, last_attempt_at FROM workspace_usage WHERE uid=:uid")
            failed = measured + timedelta(seconds=2)
            await save_usage(conn, "existing", None, failed)
            row = (await conn.execute(read_usage, {"uid": "existing"})).one()
            assert tuple(row) == (4294967296, measured, failed)
            # 时钟回拨不应导致新扫描的低用量无法覆盖已过时的超额快照。
            earlier_success = measured - timedelta(seconds=1)
            await save_usage(conn, "existing", 0, earlier_success)
            row = (await conn.execute(read_usage, {"uid": "existing"})).one()
            assert tuple(row) == (0, earlier_success, earlier_success)
            await conn.execute(text("INSERT INTO users VALUES ('new-user')"))
            await save_usage(conn, "new-user", None, failed)
            await save_usage(conn, "new-user", 8589934592, earlier_success)
            row = (await conn.execute(read_usage, {"uid": "new-user"})).one()
            assert tuple(row) == (8589934592, earlier_success, earlier_success)
        assert await get_schema_versions(manager) == {"business": 8, "knowledge": 2}
    finally:
        await _drop_isolated_schema(schema, admin, engine)
