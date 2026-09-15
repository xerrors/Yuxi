"""回收站连续迁移只增加目标字段与journal，保留现有业务数据。"""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from yuxi.storage.postgres.manager import pg_manager, BUSINESS_SCHEMA_VERSION, KNOWLEDGE_SCHEMA_VERSION
from yuxi.storage.postgres.models_business import Base as BusinessBase
from yuxi.storage.postgres.models_knowledge import Base as KnowledgeBase


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_trash_schema_is_contiguous_and_repeatable(monkeypatch, existing):
    """新库及上游business7/knowledge2存量，重复迁移与版本拒绝。"""
    url = os.environ.get("TEST_TRASH_POSTGRES_URL", "")
    if os.environ.get("TEST_ALLOW_TRASH_DB") != "1" or not url:
        pytest.skip("需要显式隔离 fixture PostgreSQL")
    assert make_url(url).database == "trash"
    assert (BUSINESS_SCHEMA_VERSION, KNOWLEDGE_SCHEMA_VERSION) == (8, 3)
    schema = "pytest_trash_migration_" + uuid4().hex
    admin = create_async_engine(url, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    monkeypatch.setattr(pg_manager, "async_engine", engine)
    monkeypatch.setattr(pg_manager, "_initialized", True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(BusinessBase.metadata.create_all)
            await conn.run_sync(KnowledgeBase.metadata.create_all)
            await conn.execute(
                text("INSERT INTO knowledge_bases (kb_id,name,kb_type) VALUES ('old','preserved','milvus')")
            )
            await conn.execute(
                text("INSERT INTO knowledge_files (file_id,kb_id,filename) VALUES ('old-file','old','preserved.pdf')")
            )
            if existing:
                await conn.execute(text("DROP TABLE personal_trash_entries"))
                for column in (
                    "deleted_at",
                    "purge_after",
                    "deletion_id",
                    "deleted_by",
                    "purge_started_at",
                    "purge_lease_until",
                    "purge_token",
                    "purge_error",
                    "purge_objects",
                ):
                    await conn.execute(text(f"ALTER TABLE knowledge_files DROP COLUMN {column}"))
        await pg_manager.create_schema_version_table()
        if existing:
            await pg_manager.record_schema_version("business", 7)
            await pg_manager.record_schema_version("knowledge", 2)
            with pytest.raises(RuntimeError, match="incompatible"):
                await pg_manager.require_current_schema()
        for _ in range(2):
            await pg_manager.upgrade_business_schema_v7_to_v8()
            await pg_manager.upgrade_knowledge_schema_v2_to_v3()
            await pg_manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION)
            await pg_manager.record_schema_version("knowledge", KNOWLEDGE_SCHEMA_VERSION)
            await pg_manager.require_current_schema()
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text("SELECT filename, deleted_at, purge_after FROM knowledge_files WHERE file_id='old-file'")
                )
            ).one()
            assert tuple(row) == ("preserved.pdf", None, None)
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
            assert "personal_trash_entries" in tables
            assert "memory_entries" not in tables and "knowledge_sources" not in tables
            columns = await conn.run_sync(
                lambda c: {item["name"] for item in inspect(c).get_columns("knowledge_files")}
            )
            assert "citation_page_map" not in columns
            assert {"deleted_at", "purge_after", "purge_objects"} <= columns
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
