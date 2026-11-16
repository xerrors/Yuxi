"""定制 Schema 扩展的真实 PostgreSQL 事务、幂等及拒绝边界。"""

import pytest
from sqlalchemy import text

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.custom_v080 import upgrade_v080_custom_schema
from yuxi.migrations.schema import create_schema_version_table, record_schema_version

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
CUSTOM_TABLES = {
    "wps_cloud_config",
    "wps_connections",
    "knowledge_document_versions",
    "knowledge_parent_chunks",
    "knowledge_child_chunks",
    "knowledge_parent_graph_entity_mentions",
    "knowledge_parent_graph_triple_mentions",
}


@pytest.fixture
async def standard_v080():
    """建立不含定制表的标准 v0.8 结构，并保留真实用户与文档哨兵。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_custom_v080")
    try:
        async with engine.begin() as conn:
            for metadata in load_models():
                tables = [table for table in metadata.sorted_tables if table.name not in CUSTOM_TABLES | {"workspace_deliverables"}]
                await conn.run_sync(lambda sync_conn: metadata.create_all(sync_conn, tables=tables))
            await conn.execute(
                text(
                    "INSERT INTO users (username, uid, password_hash, role, login_failed_count, is_deleted) "
                    "VALUES ('retained', 'retained', 'unchanged', 'user', 0, 0)"
                )
            )
            await conn.execute(text("INSERT INTO knowledge_bases (kb_id, name, kb_type) VALUES ('kb', 'retained', 'milvus')"))
            await conn.execute(text("INSERT INTO knowledge_files (file_id, kb_id, filename) VALUES ('file', 'kb', 'retained.md')"))
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 2)
        await record_schema_version(manager, "knowledge", 2)
        yield manager
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_extension_preserves_existing_rows_and_rerun(standard_v080):
    """重复显式升级只添加定制结构，旧用户与文档内容保持原值。"""
    manager = standard_v080
    await upgrade_v080_custom_schema(manager)
    await upgrade_v080_custom_schema(manager)
    assert await get_schema_versions(manager) == {"business": 3, "knowledge": 3}
    async with manager.async_engine.connect() as conn:
        names = set((await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))).scalars())
        assert CUSTOM_TABLES <= names
        assert await conn.scalar(text("SELECT password_hash FROM users WHERE uid = 'retained'")) == "unchanged"
        assert await conn.scalar(text("SELECT filename FROM knowledge_files WHERE file_id = 'file'")) == "retained.md"
        assert await conn.scalar(text("SELECT count(*) FROM wps_cloud_config WHERE id = 1 AND enabled = false")) == 1


async def test_extension_rejects_old_architecture_without_bumping_markers(standard_v080):
    """旧本地版本号不能被当作标准 v0.8 结构接受。"""
    manager = standard_v080
    await record_schema_version(manager, "business", 9)
    await record_schema_version(manager, "knowledge", 4)
    with pytest.raises(RuntimeError, match="只支持标准"):
        await upgrade_v080_custom_schema(manager)
    assert await get_schema_versions(manager) == {"business": 9, "knowledge": 4}


async def test_extension_rejects_unversioned_custom_table_without_partial_ddl(standard_v080):
    """发现不明定制表时拒绝，其他表和两个版本均不被提前创建或提升。"""
    manager = standard_v080
    async with manager.async_engine.begin() as conn:
        await conn.execute(text("CREATE TABLE knowledge_document_versions (sentinel text)"))
    with pytest.raises(RuntimeError, match="未版本化定制表"):
        await upgrade_v080_custom_schema(manager)
    assert await get_schema_versions(manager) == {"business": 2, "knowledge": 2}
    async with manager.async_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('wps_cloud_config')")) is None
