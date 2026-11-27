"""存量库的个人 Workspace 用量快照迁移。"""

import asyncio

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.infrastructure.postgres.schema import get_schema_versions, require_schema_table, require_workspace_schema
from yuxi.migrations.schema import schema_migration_lock


async def upgrade_workspace_usage(manager: PostgresManager) -> None:
    """在初始化锁内创建快照表并登记版本。"""
    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions == {"business": 8, "knowledge": 2}:
            await require_workspace_schema(manager)
            return
        if versions != {"business": 7, "knowledge": 2}:
            raise RuntimeError(f"个人空间用量迁移需要 business 7、knowledge 2，当前为 {versions}")
        async with manager.async_engine.begin() as conn:
            await require_schema_table(conn, "workspace_recent_files")
            await conn.execute(
                text("""
                    CREATE TABLE workspace_usage (
                        uid VARCHAR(128) PRIMARY KEY REFERENCES users(uid) ON DELETE CASCADE,
                        used_bytes BIGINT,
                        measured_at TIMESTAMPTZ,
                        last_attempt_at TIMESTAMPTZ NOT NULL,
                        CONSTRAINT ck_workspace_usage_nonnegative CHECK (used_bytes >= 0)
                    )
                """)
            )
            await conn.execute(
                text("UPDATE yuxi_schema_migrations SET version = 8, applied_at = CURRENT_TIMESTAMP WHERE domain = 'business'")
            )


async def main() -> None:
    """使用部署连接执行离线升级。"""
    manager = PostgresManager()
    manager.initialize()
    try:
        await upgrade_workspace_usage(manager)
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
