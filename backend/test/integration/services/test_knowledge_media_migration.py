"""在独立 PostgreSQL Schema 验证原生媒体升级。"""

import pytest
from sqlalchemy import inspect, text

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.migrations.knowledge_media import audit, upgrade
from yuxi.migrations.schema import create_business_tables, create_knowledge_tables, create_schema_version_table, record_schema_version

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_v2_media_upgrade_preserves_files_and_repeats():
    """迁移发布版本之前创建实际修订表，已有文件保持可读。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_media_schema")
    try:
        await create_business_tables(manager)
        await create_knowledge_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 2)
        await record_schema_version(manager, "knowledge", 2)
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE knowledge_document_revisions"))
            await conn.execute(text("INSERT INTO knowledge_bases (kb_id, name, kb_type) VALUES ('fixture', 'Fixture', 'milvus')"))
            await conn.execute(text("INSERT INTO knowledge_files (file_id, kb_id, filename) VALUES ('file', 'fixture', 'sample.md')"))
        assert (await audit(manager))["media_revision_table"] is False

        result = await upgrade(manager)

        assert result["versions"] == {"business": 2, "knowledge": 4}
        assert await upgrade(manager) == result
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT filename FROM knowledge_files WHERE file_id='file'")) == "sample.md"
            constraints = await conn.run_sync(lambda db: inspect(db).get_check_constraints("knowledge_document_revisions"))
            assert "ck_document_revision_hash" in {item["name"] for item in constraints}
    finally:
        await drop_isolated_schema(schema, admin, engine)
