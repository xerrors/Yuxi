"""显式为标准 v0.8 的 2/2 Schema 添加本地定制表，不由启动进程调用。"""

import asyncio

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import PostgresManager, pg_manager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.schema import schema_migration_lock
from yuxi.modules.identity.models import WPSCloudConfig, WPSConnection
from yuxi.modules.knowledge.models import (
    KnowledgeChildChunk,
    KnowledgeDocumentVersion,
    KnowledgeParentChunk,
    KnowledgeParentGraphEntityMention,
    KnowledgeParentGraphTripleMention,
)


async def upgrade_v080_custom_schema(manager: PostgresManager) -> None:
    """在单一事务中添加定制表并提升版本，拒绝旧架构和不明半成品。"""
    load_models()
    tables = [
        model.__table__
        for model in (
            WPSCloudConfig,
            WPSConnection,
            KnowledgeDocumentVersion,
            KnowledgeParentChunk,
            KnowledgeChildChunk,
            KnowledgeParentGraphEntityMention,
            KnowledgeParentGraphTripleMention,
        )
    ]
    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions in ({"business": 3, "knowledge": 3}, {"business": 4, "knowledge": 3}):
            return
        if versions != {"business": 2, "knowledge": 2}:
            raise RuntimeError(f"定制表升级只支持标准 v0.8 Schema 2/2，当前为 {versions}")
        async with manager.async_engine.begin() as conn:
            existing = set((await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"))).scalars())
            conflicts = existing.intersection(table.name for table in tables)
            if conflicts:
                raise RuntimeError(f"发现未版本化定制表，请先审计：{', '.join(sorted(conflicts))}")
            # PostgreSQL 事务 DDL 保证建表、配置播种与两个版本标记同时提交。
            for table in tables:
                await conn.run_sync(table.create)
            await conn.execute(text("INSERT INTO wps_cloud_config (id) VALUES (1)"))
            await conn.execute(
                text(
                    "UPDATE yuxi_schema_migrations SET version = 3, applied_at = CURRENT_TIMESTAMP "
                    "WHERE domain IN ('business', 'knowledge')"
                )
            )


async def main() -> None:
    """供停止 API 与 worker 后显式执行，不读取或改写其他版本标记。"""
    pg_manager.initialize()
    try:
        await upgrade_v080_custom_schema(pg_manager)
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
