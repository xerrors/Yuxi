"""缺少新列的真实历史 Schema 验证显式升级和数据保留。"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.infrastructure.postgres.schema import get_schema_versions, require_current_schema
from yuxi.migrations.knowledge_directories import audit as audit_directories
from yuxi.migrations.knowledge_directories import convert
from yuxi.migrations.knowledge_metadata import upgrade
from yuxi.migrations.schema import create_business_tables, create_knowledge_tables, create_schema_version_table, record_schema_version

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def legacy_schema(manager, version):
    """建立真实缺列旧库，不用新版 ORM 假装旧版表结构。"""
    manager.AsyncSession = async_sessionmaker(manager.async_engine, expire_on_commit=False)
    await create_business_tables(manager)
    await create_knowledge_tables(manager)
    await create_schema_version_table(manager)
    await record_schema_version(manager, "business", 4)
    await record_schema_version(manager, "knowledge", version)
    async with manager.async_engine.begin() as connection:
        await connection.execute(text("ALTER TABLE knowledge_bases DROP COLUMN metadata_fields"))
        await connection.execute(text("ALTER TABLE knowledge_files DROP COLUMN attributes"))
        await connection.execute(text("INSERT INTO knowledge_bases(kb_id,name,kb_type) VALUES ('legacy','legacy','milvus')"))


async def test_v2_upgrade_is_atomic_repeatable_and_preserves_existing_attributes():
    """新列默认值、重复升级和显式已有属性均从数据库回读验证。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_metadata_v2")
    try:
        await legacy_schema(manager, 2)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO knowledge_files(file_id,kb_id,filename,is_folder,status) "
                    "VALUES ('source','legacy','guide.md',false,'parsed')"
                )
            )
        result = await upgrade(manager)
        assert result["versions"] == {"business": 4, "knowledge": 3}
        async with engine.begin() as connection:
            row = (await connection.execute(text("SELECT filename,status,attributes FROM knowledge_files WHERE file_id='source'"))).one()
            assert (row.filename, row.status, row.attributes) == ("guide.md", "parsed", {})
            await connection.execute(
                text("UPDATE knowledge_files SET attributes='{" + '"label":"preserved"' + "}'::jsonb WHERE file_id='source'")
            )
        await upgrade(manager)
        await require_current_schema(manager)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT attributes FROM knowledge_files WHERE file_id='source'")) == {"label": "preserved"}
            assert await connection.scalar(text("SELECT metadata_fields FROM knowledge_bases WHERE kb_id='legacy'")) == []
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_v1_directory_conversion_then_v3_metadata_upgrade():
    """目录审计与目录 INSERT 均不能依赖未来版本列。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_metadata_v1")
    try:
        await legacy_schema(manager, 1)
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE knowledge_files DROP CONSTRAINT ck_knowledge_files_leaf_name"))
            await connection.execute(text("DROP INDEX uq_knowledge_files_active_name"))
            await connection.execute(
                text(
                    "INSERT INTO knowledge_files(file_id,kb_id,filename,is_folder,status,path) "
                    "VALUES ('source','legacy','manuals/guide.md',false,'parsed','original-object')"
                )
            )
        assert (await audit_directories(manager))["blocking_ids"] == []
        assert (await convert(manager))["complete"] is True
        assert (await get_schema_versions(manager))["knowledge"] == 2
        await upgrade(manager)
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("SELECT file_id,filename,parent_id,path,attributes FROM knowledge_files ORDER BY is_folder DESC")
                )
            ).all()
            assert len(rows) == 2
            folder, file = rows
            assert folder.filename == "manuals"
            assert file.filename == "guide.md" and file.parent_id == folder.file_id
            assert file.path == "original-object" and file.attributes == {}
        await require_current_schema(manager)
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_incompatible_attribute_column_rolls_back_version_and_other_column():
    """不能用版本号更新掩盖不满足契约的已有列。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_metadata_bad")
    try:
        await legacy_schema(manager, 2)
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE knowledge_files ADD COLUMN attributes TEXT NOT NULL DEFAULT 'existing'"))
        with pytest.raises(RuntimeError, match="结构"):
            await upgrade(manager)
        assert (await get_schema_versions(manager))["knowledge"] == 2
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() "
                    "AND table_name='knowledge_bases' AND column_name='metadata_fields'"
                    )
                )
                == 0
            )
    finally:
        await drop_isolated_schema(schema, admin, engine)
