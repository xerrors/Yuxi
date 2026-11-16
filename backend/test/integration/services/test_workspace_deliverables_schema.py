"""个人成果表的显式升级、幂等与事务拒绝边界。"""

import pytest
from sqlalchemy import text

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.schema import create_schema_version_table, record_schema_version
from yuxi.migrations.workspace_deliverables import upgrade_workspace_deliverables_schema

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture
async def previous_schema():
    """建立升级前结构和哨兵用户，全部操作限定在独立 Schema。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_deliverables_schema")
    try:
        async with engine.begin() as conn:
            for metadata in load_models():
                tables = [table for table in metadata.sorted_tables if table.name != "workspace_deliverables"]
                await conn.run_sync(lambda sync: metadata.create_all(sync, tables=tables))
            await conn.execute(
                text(
                    "INSERT INTO users (username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('retained','retained','unchanged','user',0,0)"
                )
            )
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 3)
        await record_schema_version(manager, "knowledge", 3)
        yield manager
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_upgrade_preserves_existing_data_and_rerun(previous_schema):
    """重复升级保留用户，成果表、唯一发布约束与版本标记真实存在。"""
    await upgrade_workspace_deliverables_schema(previous_schema)
    await upgrade_workspace_deliverables_schema(previous_schema)
    assert await get_schema_versions(previous_schema) == {"business": 4, "knowledge": 3}
    async with previous_schema.async_engine.connect() as conn:
        assert await conn.scalar(text("SELECT password_hash FROM users WHERE uid='retained'")) == "unchanged"
        assert await conn.scalar(text("SELECT count(*) FROM workspace_deliverables")) == 0
        constraints = set(
            (
                await conn.execute(
                    text("SELECT conname FROM pg_constraint WHERE conrelid = 'workspace_deliverables'::regclass")
                )
            ).scalars()
        )
        assert "uq_workspace_deliverables_publication" in constraints


@pytest.mark.parametrize("business,knowledge", [(2, 2), (4, 2), (9, 4)])
async def test_upgrade_rejects_other_versions_without_writes(previous_schema, business, knowledge):
    """未知结构不建表、不提升任何域版本。"""
    await record_schema_version(previous_schema, "business", business)
    await record_schema_version(previous_schema, "knowledge", knowledge)
    with pytest.raises(RuntimeError, match="只支持 business=3/knowledge=3"):
        await upgrade_workspace_deliverables_schema(previous_schema)
    assert await get_schema_versions(previous_schema) == {"business": business, "knowledge": knowledge}
    async with previous_schema.async_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('workspace_deliverables')")) is None


async def test_upgrade_rejects_unversioned_table_without_bumping_version(previous_schema):
    """不明同名表不能被静默当作成功升级。"""
    async with previous_schema.async_engine.begin() as conn:
        await conn.execute(text("CREATE TABLE workspace_deliverables (sentinel text)"))
    with pytest.raises(RuntimeError, match="未版本化成果表"):
        await upgrade_workspace_deliverables_schema(previous_schema)
    assert await get_schema_versions(previous_schema) == {"business": 3, "knowledge": 3}
    async with previous_schema.async_engine.connect() as conn:
        await conn.execute(text("SELECT sentinel FROM workspace_deliverables"))
