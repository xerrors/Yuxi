"""Yuxi Schema 版本事实在真实 PostgreSQL 上的集成测试。"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.storage.postgres.manager import BUSINESS_SCHEMA_VERSION, KNOWLEDGE_SCHEMA_VERSION, PostgresManager
from yuxi.storage.postgres.models_knowledge import KnowledgeBase

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

LEGACY_TASK_TABLE_SQL = """
CREATE TABLE tasks (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    progress DOUBLE PRECISION NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    payload JSONB,
    result JSONB,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITHOUT TIME ZONE,
    completed_at TIMESTAMP WITHOUT TIME ZONE
)
"""


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """本文件自行创建隔离 Schema，不依赖运行中的 API。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """隔离 Schema 测试没有 HTTP 资源需要清理。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """隔离 Schema 测试没有 Sandbox 资源需要清理。"""
    yield


def _scoped_manager(engine) -> PostgresManager:
    """创建不触碰进程单例的隔离 manager。"""
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager.async_engine = engine
    manager._initialized = True
    return manager


async def _create_isolated_manager(prefix: str):
    """创建位于独立 PostgreSQL Schema 的 manager 与清理句柄。"""
    schema = f"{prefix}_{uuid.uuid4().hex[:16]}"
    admin_engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    return schema, admin_engine, scoped_engine, _scoped_manager(scoped_engine)


async def _drop_isolated_schema(schema: str, admin_engine, scoped_engine) -> None:
    """释放隔离 Schema 及其 engine。"""
    await scoped_engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await admin_engine.dispose()


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


async def test_v072_business_converges_current_schema_idempotently() -> None:
    """v0.7.2 发布结构一次补齐当前字段与约束，重复执行保持幂等。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_task_schema")

    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            # v0.7.2 tag 没有这些字段，不能用当前 ORM 预建它们来证明迁移。
            for column in ("prepared_at", "first_output_at", "first_model_request_at"):
                await connection.execute(text(f"ALTER TABLE agent_runs DROP COLUMN {column}"))
            await connection.execute(text("ALTER TABLE agent_runs ADD COLUMN last_event_id VARCHAR(64)"))
            await connection.execute(text("DROP TABLE scheduled_agent_runs"))
            await connection.execute(text("DROP TABLE scheduled_agent_jobs"))
            await connection.execute(text("DROP TABLE tasks"))
            await connection.execute(text(LEGACY_TASK_TABLE_SQL))
            await connection.execute(
                text(
                    "INSERT INTO tasks (id, name, type, status) "
                    "VALUES ('legacy-running', 'legacy', 'knowledge_parse', 'running')"
                )
            )

        await manager.ensure_business_schema()
        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            task_columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'tasks'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            run_columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'agent_runs'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            row = (
                await connection.execute(
                    text("SELECT status, error, handler_version, attempt_count FROM tasks WHERE id = 'legacy-running'")
                )
            ).one()
            scheduled_tables = set(
                (
                    await connection.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = :schema "
                            "AND table_name IN ('scheduled_agent_jobs', 'scheduled_agent_runs')"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            scheduled_columns = {
                (row.table_name, row.column_name)
                for row in (
                    await connection.execute(
                        text(
                            "SELECT table_name, column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema "
                            "AND table_name IN ('scheduled_agent_jobs', 'scheduled_agent_runs')"
                        ),
                        {"schema": schema},
                    )
                )
            }
            scheduled_constraints = {
                row.conname: row.definition
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT con.conname, pg_get_constraintdef(con.oid) AS definition
                            FROM pg_constraint AS con
                            JOIN pg_namespace AS ns ON ns.oid = con.connamespace
                            WHERE ns.nspname = :schema
                              AND con.conname IN (
                                  'fk_scheduled_agent_jobs_project_uid',
                                  'uq_scheduled_agent_jobs_uid_creation_request',
                                  'scheduled_agent_runs_job_id_fkey',
                                  'uq_scheduled_agent_runs_job_occurrence',
                                  'uq_scheduled_agent_runs_request',
                                  'uq_scheduled_agent_runs_thread'
                              )
                            """
                        ),
                        {"schema": schema},
                    )
                )
            }
            scheduled_indexes = set(
                (
                    await connection.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = :schema "
                            "AND tablename IN ('scheduled_agent_jobs', 'scheduled_agent_runs')"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )

        assert {
            "handler_version",
            "dedupe_key",
            "attempt_count",
            "worker_id",
            "heartbeat_at",
            "lease_expires_at",
            "timeout_seconds",
        } <= task_columns
        assert {"prepared_at", "first_output_at", "first_model_request_at"} <= run_columns
        assert "last_event_id" not in run_columns
        assert tuple(row) == ("running", None, 0, 0)
        assert scheduled_tables == {"scheduled_agent_jobs", "scheduled_agent_runs"}
        assert {
            ("scheduled_agent_jobs", "creation_request_id"),
            ("scheduled_agent_jobs", "creation_intent_hash"),
            ("scheduled_agent_jobs", "model_spec"),
            ("scheduled_agent_runs", "model_spec"),
        }.issubset(scheduled_columns)
        assert "ON DELETE CASCADE" in scheduled_constraints["fk_scheduled_agent_jobs_project_uid"]
        assert "ON DELETE CASCADE" in scheduled_constraints["scheduled_agent_runs_job_id_fkey"]
        assert (
            "UNIQUE (uid, creation_request_id)" in scheduled_constraints["uq_scheduled_agent_jobs_uid_creation_request"]
        )
        assert {
            "uq_scheduled_agent_runs_job_occurrence",
            "uq_scheduled_agent_runs_request",
            "uq_scheduled_agent_runs_thread",
        }.issubset(scheduled_constraints)
        assert {
            "ix_scheduled_agent_jobs_due",
            "ix_scheduled_agent_runs_job_created",
            "ix_scheduled_agent_runs_dispatching",
        }.issubset(scheduled_indexes)
        assert BUSINESS_SCHEMA_VERSION == 9
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_business_v7_to_v8_backfills_fixed_roles_and_rejects_unknown_values() -> None:
    """v7 用户按旧平台角色获得最小默认业务角色，数据库拒绝未知角色。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_business_roles")
    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE users DROP COLUMN business_roles"))
            await connection.execute(
                text(
                    "INSERT INTO users (username, uid, password_hash, role, login_failed_count, is_deleted) VALUES "
                    "('counselor', 'counselor', 'x', 'user', 0, 0), "
                    "('business', 'business', 'x', 'admin', 0, 0), "
                    "('technical', 'technical', 'x', 'superadmin', 0, 0)"
                )
            )

        await manager.upgrade_business_schema_v7_to_v8()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("UPDATE users SET business_roles = '[]'::jsonb WHERE uid = 'counselor'"))
        await manager.upgrade_business_schema_v7_to_v8()

        async with scoped_engine.connect() as connection:
            rows = (await connection.execute(text("SELECT uid, business_roles FROM users ORDER BY uid"))).all()
        assert {row.uid: row.business_roles for row in rows} == {
            "business": ["business_admin"],
            "counselor": [],
            "technical": ["technical_admin"],
        }

        with pytest.raises(IntegrityError, match="ck_users_business_roles"):
            async with scoped_engine.begin() as connection:
                await connection.execute(
                    text("UPDATE users SET business_roles = '[\"unknown\"]'::jsonb WHERE uid = 'counselor'")
                )
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_business_v8_to_v9_creates_student_schema_idempotently() -> None:
    """既有业务库重复升级后具有完整学生档案约束与默认值。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_student_records")
    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("DROP TABLE counseling_students"))

        await manager.upgrade_business_schema_v8_to_v9()
        await manager.upgrade_business_schema_v8_to_v9()
        async with scoped_engine.connect() as connection:
            defaults = dict(
                (await connection.execute(text(
                    "SELECT column_name, column_default FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'counseling_students' "
                    "AND column_name IN ('background_summary', 'status')"
                ), {"schema": schema})).all()
            )
        assert defaults == {"background_summary": "''::text", "status": "'active'::character varying"}
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_release_upgrade_adds_audit_columns_idempotently() -> None:
    """发布版缺失的 Trace 与 Message 审计列由完整升级补齐，且可安全重放。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_audit_schema")
    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE agent_runs DROP COLUMN langfuse_trace_id"))
            audit_columns = (
                "operation_id",
                "started_at",
                "finished_at",
                "duration_ms",
                "sequence",
                "execution_status",
                "usage",
            )
            for column in audit_columns:
                await connection.execute(text(f"ALTER TABLE messages DROP COLUMN {column}"))

        await manager.ensure_business_schema()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("DROP INDEX uq_messages_run_role_operation_id"))
            await connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_messages_run_operation_id "
                    "ON messages(run_id, operation_id) WHERE operation_id IS NOT NULL"
                )
            )
        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT table_name, column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name IN ('agent_runs', 'messages')"
                        ),
                        {"schema": schema},
                    )
                ).all()
            )
            audit_indexes = {
                name: definition
                for name, definition in (
                    await connection.execute(
                        text(
                            "SELECT indexname, indexdef FROM pg_indexes "
                            "WHERE schemaname = :schema AND tablename = 'messages' "
                            "AND indexname LIKE 'uq_messages_run%operation_id'"
                        ),
                        {"schema": schema},
                    )
                ).all()
            }
        assert ("agent_runs", "langfuse_trace_id") in columns
        assert {("messages", column) for column in audit_columns} <= columns
        assert "uq_messages_run_operation_id" not in audit_indexes
        assert "(run_id, role, operation_id)" in audit_indexes["uq_messages_run_role_operation_id"]
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_knowledge_v1_to_v2_adds_file_attempt_owner_idempotently() -> None:
    """知识 schema 相邻升级为文件中间态增加 Task attempt fencing。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_knowledge_schema")
    try:
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE knowledge_files ("
                    "id SERIAL PRIMARY KEY, file_id VARCHAR(64) NOT NULL, status VARCHAR(32), "
                    "error_message TEXT, updated_at TIMESTAMPTZ)"
                )
            )
            await connection.execute(
                text("INSERT INTO knowledge_files (file_id, status) VALUES ('legacy-file', 'parsing')")
            )

        await manager.upgrade_knowledge_schema_v1_to_v2()
        await manager.upgrade_knowledge_schema_v1_to_v2()

        async with scoped_engine.connect() as connection:
            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :schema AND table_name = 'knowledge_files'"
                        ),
                        {"schema": schema},
                    )
                ).scalars()
            )
            legacy = (
                await connection.execute(
                    text("SELECT status, error_message FROM knowledge_files WHERE file_id = 'legacy-file'")
                )
            ).one()
        assert {"processing_task_id", "processing_owner"} <= columns
        assert tuple(legacy) == (
            "error_parsing",
            "service_interrupted: 旧执行实例中断，处理结果未知，请重试",
        )
        assert KNOWLEDGE_SCHEMA_VERSION == 2
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_knowledge_timestamp_defaults_match_database_clock_in_non_utc_session() -> None:
    """带时区字段不依赖 PostgreSQL 会话时区解释无时区 UTC。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_knowledge_clock")
    try:
        await manager.create_knowledge_tables()
        session_factory = async_sessionmaker(scoped_engine, expire_on_commit=False)
        async with session_factory.begin() as session:
            await session.execute(text("SET TIME ZONE 'Asia/Shanghai'"))
            database = KnowledgeBase(kb_id="clock-kb", name="clock", kb_type="milvus")
            session.add(database)
            await session.flush()
            database_now = await session.scalar(text("SELECT clock_timestamp()"))

        async with session_factory() as verify_session:
            await verify_session.execute(text("SET TIME ZONE 'UTC'"))
            persisted_at = await verify_session.scalar(
                text("SELECT created_at FROM knowledge_bases WHERE kb_id = 'clock-kb'")
            )

        assert persisted_at.tzinfo is not None
        assert abs((persisted_at - database_now).total_seconds()) < 2
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_unversioned_knowledge_baseline_adds_timestamp_before_owner_convergence() -> None:
    """未版本化的旧表缺少 updated_at 时仍能收敛中间态。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_knowledge_baseline")
    try:
        await manager.create_knowledge_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO knowledge_bases (kb_id, name, kb_type) VALUES ('legacy-kb', 'legacy', 'milvus')")
            )
            await connection.execute(
                text(
                    "INSERT INTO knowledge_files (file_id, kb_id, filename, status) "
                    "VALUES ('legacy-file', 'legacy-kb', 'legacy.txt', 'indexing')"
                )
            )
            await connection.execute(text("ALTER TABLE knowledge_files DROP COLUMN processing_task_id"))
            await connection.execute(text("ALTER TABLE knowledge_files DROP COLUMN processing_owner"))
            await connection.execute(text("ALTER TABLE knowledge_files DROP COLUMN updated_at"))

        await manager.create_knowledge_tables()
        await manager.ensure_knowledge_schema()

        async with scoped_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT status, error_message, updated_at, processing_task_id, processing_owner "
                        "FROM knowledge_files WHERE file_id = 'legacy-file'"
                    )
                )
            ).one()
        assert row.status == "error_indexing"
        assert row.error_message == "service_interrupted: 旧执行实例中断，处理结果未知，请重试"
        assert row.updated_at is not None
        assert row.processing_task_id is None
        assert row.processing_owner is None
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_unversioned_baseline_repairs_existing_legacy_task_table() -> None:
    """未版本化数据库的 create_all + ensure 路径必须补齐旧 tasks 表。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_task_baseline")

    try:
        await manager.create_business_tables()
        async with scoped_engine.begin() as connection:
            await connection.execute(text("DROP TABLE tasks"))
            await connection.execute(text(LEGACY_TASK_TABLE_SQL))
            await connection.execute(
                text(
                    "INSERT INTO tasks (id, name, type, status) "
                    "VALUES ('legacy-pending', 'legacy', 'knowledge_parse', 'pending')"
                )
            )

        await manager.ensure_business_schema()

        async with scoped_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT status, error, handler_version, lease_expires_at FROM tasks WHERE id = 'legacy-pending'"
                    )
                )
            ).one()
        assert tuple(row) == ("pending", None, 0, None)
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_schema_version_is_persisted_and_runtime_validation_fails_closed() -> None:
    """版本表缺失、错误和正确三种状态必须形成精确启动结论。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_schema_version")

    try:
        with pytest.raises(RuntimeError, match="business=missing"):
            await manager.require_current_schema()

        await manager.create_schema_version_table()
        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION + 1)
        with pytest.raises(RuntimeError, match=f"business={BUSINESS_SCHEMA_VERSION + 1}"):
            await manager.require_current_schema()

        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION)
        with pytest.raises(RuntimeError, match="knowledge=missing"):
            await manager.require_current_schema()

        await manager.record_schema_version("knowledge", KNOWLEDGE_SCHEMA_VERSION)
        await manager.require_current_schema()
        assert await manager.get_schema_versions() == {
            "business": BUSINESS_SCHEMA_VERSION,
            "knowledge": KNOWLEDGE_SCHEMA_VERSION,
        }
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)
