"""在维护窗口建立原生媒体修订表。默认检查当前版本，--apply 执行迁移。"""

import argparse
import asyncio
import json

from sqlalchemy import inspect, text

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.postgres.schema import BUSINESS_SCHEMA_VERSION, get_schema_versions
from yuxi.migrations.schema import schema_migration_lock
from yuxi.modules.knowledge.models import KnowledgeDocumentRevision


async def audit(manager=pg_manager) -> dict:
    """检查版本及修订表存在性，不读取业务数据。"""
    versions = await get_schema_versions(manager)
    async with manager.async_engine.connect() as connection:
        present = await connection.run_sync(lambda db: inspect(db).has_table(KnowledgeDocumentRevision.__tablename__))
    return {"versions": versions, "media_revision_table": present, "target_knowledge_version": 4}


async def upgrade(manager=pg_manager) -> dict:
    """创建修订表并发布版本，DDL 和版本标记使用同一事务。"""
    async with schema_migration_lock(manager):
        before = await audit(manager)
        versions = before["versions"]
        if versions == {"business": BUSINESS_SCHEMA_VERSION, "knowledge": 4} and before["media_revision_table"]:
            return before
        if versions != {"business": BUSINESS_SCHEMA_VERSION, "knowledge": 2} or before["media_revision_table"]:
            raise RuntimeError("媒体迁移要求 business 2、knowledge 2 且修订表尚未创建")
        async with manager.async_engine.begin() as connection:
            await connection.run_sync(KnowledgeDocumentRevision.__table__.create)
            await connection.execute(
                text("UPDATE yuxi_schema_migrations SET version=4, applied_at=CURRENT_TIMESTAMP WHERE domain='knowledge'")
            )
    return await audit(manager)


async def main() -> None:
    """执行显式维护操作并输出结构检查结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    pg_manager.initialize()
    try:
        print(json.dumps(await (upgrade() if args.apply else audit()), ensure_ascii=False))
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
