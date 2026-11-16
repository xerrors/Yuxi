"""应用初始化任务使用的当前 PostgreSQL Schema 基线。"""

from __future__ import annotations

from sqlalchemy import text

from yuxi.infrastructure.postgres.base import BusinessBase, KnowledgeBase
from yuxi.infrastructure.postgres.checkpointer import setup_langgraph_checkpointer
from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.schema import BUSINESS_SCHEMA_VERSION, KNOWLEDGE_SCHEMA_VERSION, get_schema_versions
from yuxi.migrations.schema import (
    create_business_tables,
    create_knowledge_tables,
    create_schema_version_table,
    drop_fresh_schema,
    ensure_business_schema,
    ensure_knowledge_schema,
    record_schema_version,
)


async def initialize_schema(manager: PostgresManager) -> None:
    """在调用方持有初始化锁时建立空库基线，失败只清理本次半成品。"""
    versions = await get_schema_versions(manager)
    current = {"business": BUSINESS_SCHEMA_VERSION, "knowledge": KNOWLEDGE_SCHEMA_VERSION}
    if versions.get("initializing") == 1:
        # 该标记只在确认空库后提交；崩溃恢复只能清理本初始化器拥有的半成品。
        await drop_fresh_schema(manager)
        versions = {}
    if versions == current:
        return
    if versions:
        hint = ""
        if versions == {"business": 9, "knowledge": 4}:
            hint = (
                "; 本地 v0.7.3 数据库须先停写并备份，再执行 "
                "python -m yuxi.migrations.legacy_v073 审计；确认后加 --apply 显式迁移，禁止手工改版本号"
            )
        raise RuntimeError(f"Unsupported Yuxi schema versions: {versions}; required {current}{hint}")

    await _require_empty_database(manager)
    try:
        await create_schema_version_table(manager)
        await record_schema_version(manager, "initializing", 1)
        await create_business_tables(manager)
        await create_knowledge_tables(manager)
        await ensure_business_schema(manager)
        await ensure_knowledge_schema(manager)
        await setup_langgraph_checkpointer(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", BUSINESS_SCHEMA_VERSION)
        await record_schema_version(manager, "knowledge", KNOWLEDGE_SCHEMA_VERSION)
        async with manager.async_engine.begin() as conn:
            await conn.execute(text("DELETE FROM yuxi_schema_migrations WHERE domain = 'initializing'"))
    except BaseException:
        await drop_fresh_schema(manager)
        raise


async def _require_empty_database(manager: PostgresManager) -> None:
    """拒绝未版本化的旧库和上次初始化失败后留下的半成品。"""
    load_models()
    owned_tables = (
        set(BusinessBase.metadata.tables)
        | set(KnowledgeBase.metadata.tables)
        | {
            "tasks",  # 已退役的后台作业表仍属于旧库。
            "conversations",  # 已退役的业务表仍属于旧库，禁止被当作空库。
            "agent_input_messages",
            "tool_calls",
            "checkpoint_writes",
            "checkpoint_blobs",
            "checkpoints",
            "checkpoint_migrations",
        }
    )
    async with manager.async_engine.connect() as conn:
        rows = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))
        existing = owned_tables.intersection(rows.scalars().all())
    if existing:
        raise RuntimeError(f"Fresh Yuxi schema requires an empty database; found {', '.join(sorted(existing))}")
