"""Yuxi Schema 版本事实在真实 PostgreSQL 上的集成测试。"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from yuxi.storage.postgres.manager import BUSINESS_SCHEMA_VERSION, KNOWLEDGE_SCHEMA_VERSION, PostgresManager

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


async def test_business_v1_to_v2_adds_durable_task_contract_idempotently() -> None:
    """相邻升级保留 legacy 非终态，并用 handler_version=0 交给 full worker 收敛。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_task_schema")

    try:
        async with scoped_engine.begin() as connection:
            await connection.execute(text(LEGACY_TASK_TABLE_SQL))
            await connection.execute(
                text(
                    "INSERT INTO tasks (id, name, type, status) "
                    "VALUES ('legacy-running', 'legacy', 'knowledge_parse', 'running')"
                )
            )

        await manager.upgrade_business_schema_v1_to_v2()
        await manager.upgrade_business_schema_v1_to_v2()

        async with scoped_engine.connect() as connection:
            columns = set(
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
            row = (
                await connection.execute(
                    text(
                        "SELECT status, error, recovery_strategy, handler_version, attempt_count "
                        "FROM tasks WHERE id = 'legacy-running'"
                    )
                )
            ).one()

        assert {
            "recovery_strategy",
            "handler_version",
            "dedupe_key",
            "attempt_count",
            "worker_id",
            "heartbeat_at",
            "lease_expires_at",
            "timeout_seconds",
        } <= columns
        assert tuple(row) == ("running", None, "fail", 0, 0)
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
                        "SELECT status, error, recovery_strategy, handler_version, lease_expires_at "
                        "FROM tasks WHERE id = 'legacy-pending'"
                    )
                )
            ).one()
        assert tuple(row) == ("pending", None, "fail", 0, None)
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)


async def test_schema_version_is_persisted_and_runtime_validation_fails_closed() -> None:
    """版本表缺失、错误和正确三种状态必须形成精确启动结论。"""
    schema, admin_engine, scoped_engine, manager = await _create_isolated_manager("pytest_schema_version")

    try:
        with pytest.raises(RuntimeError, match="business=missing"):
            await manager.require_current_schema(include_knowledge=False)

        await manager.create_schema_version_table()
        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION + 1)
        with pytest.raises(RuntimeError, match=f"business={BUSINESS_SCHEMA_VERSION + 1}"):
            await manager.require_current_schema(include_knowledge=False)

        await manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION)
        await manager.require_current_schema(include_knowledge=False)
        assert await manager.get_schema_versions() == {"business": BUSINESS_SCHEMA_VERSION}
    finally:
        await _drop_isolated_schema(schema, admin_engine, scoped_engine)
