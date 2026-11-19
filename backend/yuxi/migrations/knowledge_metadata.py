"""维护窗口内将 knowledge v2 升级至 v3；默认只读检查，--apply 才修改。"""

import argparse
import asyncio
import json

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.postgres.schema import BUSINESS_SCHEMA_VERSION, get_schema_versions
from yuxi.migrations.schema import schema_migration_lock


async def audit(manager=pg_manager) -> dict:
    """只读取版本和字段形状，不读取文件内容或属性值。"""
    versions = await get_schema_versions(manager)
    async with manager.async_engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND "
                "((table_name='knowledge_bases' AND column_name='metadata_fields') OR "
                "(table_name='knowledge_files' AND column_name='attributes'))"
            )
        )
        columns = [dict(row._mapping) for row in rows]
    return {"versions": versions, "columns": columns, "target_version": 3}


async def upgrade(manager=pg_manager) -> dict:
    """在同一 PostgreSQL 事务中建立属性列、索引和版本事实，可重复执行。"""
    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions not in ({"business": BUSINESS_SCHEMA_VERSION, "knowledge": 2}, {"business": BUSINESS_SCHEMA_VERSION, "knowledge": 3}):
            raise RuntimeError(f"文件属性迁移要求 business={BUSINESS_SCHEMA_VERSION}、knowledge=2 或 3；旧版本须先完成既有迁移")
        async with manager.async_engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS metadata_fields JSONB NOT NULL DEFAULT '[]'::jsonb")
            )
            await connection.execute(
                text("ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS attributes JSONB NOT NULL DEFAULT '{}'::jsonb")
            )
            rows = await connection.execute(
                text(
                    "SELECT table_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND "
                    "((table_name='knowledge_bases' AND column_name='metadata_fields') OR "
                    "(table_name='knowledge_files' AND column_name='attributes'))"
                )
            )
            columns = rows.all()
            if len(columns) != 2 or any(row.data_type != "jsonb" or row.is_nullable != "NO" for row in columns):
                raise RuntimeError("属性字段结构与 v3 契约不一致，迁移事务已回滚")
            await connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_knowledge_files_attributes ON knowledge_files "
                    "USING gin (attributes jsonb_path_ops) WHERE deleted_at IS NULL AND NOT is_folder"
                )
            )
            await connection.execute(
                text("UPDATE yuxi_schema_migrations SET version=3, applied_at=CURRENT_TIMESTAMP WHERE domain='knowledge'")
            )
    return await audit(manager)


async def main() -> None:
    """执行显式维护命令，只输出结构和版本检查结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="API/worker 停写并完成备份后执行升级")
    args = parser.parse_args()
    pg_manager.initialize()
    try:
        print(json.dumps(await upgrade() if args.apply else await audit(), ensure_ascii=False))
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
