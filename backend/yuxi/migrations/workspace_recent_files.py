"""为存量个人空间建立最近打开记录。"""

import asyncio

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.schema import schema_migration_lock


async def upgrade_workspace_recent_files(manager: PostgresManager) -> None:
    """在同一事务中建立最近文件表并推进 business 6 到 7。"""
    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions == {"business": 7, "knowledge": 2}:
            return
        if versions != {"business": 6, "knowledge": 2}:
            raise RuntimeError(f"最近文件迁移需要 business 6 或 7，且 knowledge 为 2，当前为 {versions}")
        async with manager.async_engine.begin() as conn:
            await conn.execute(
                text("""
                    CREATE TABLE workspace_recent_files (
                        uid VARCHAR(128) PRIMARY KEY REFERENCES users(uid) ON DELETE CASCADE,
                        entries JSONB NOT NULL
                    )
                """)
            )
            await conn.execute(
                text("UPDATE yuxi_schema_migrations SET version = 7, applied_at = CURRENT_TIMESTAMP WHERE domain = 'business'")
            )


async def main() -> None:
    """按当前部署连接执行最近文件表迁移。"""
    manager = PostgresManager()
    manager.initialize()
    try:
        await upgrade_workspace_recent_files(manager)
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
