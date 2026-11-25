"""显式升级存量库，补 Run 终态状态投影所需的 sessions.agent_state_view 列。"""

import asyncio

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.schema import schema_migration_lock


async def upgrade_agent_state_view(manager: PostgresManager) -> None:
    """在初始化锁内幂等升级 business v5→v6，保留既有行；其余版本在 DDL 前拒绝。"""
    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions == {"business": 6, "knowledge": 2}:
            return
        if versions != {"business": 5, "knowledge": 2}:
            raise RuntimeError(f"会话状态迁移需要 business 5 或 6，且 knowledge 为 2，当前为 {versions}")
        async with manager.async_engine.begin() as conn:
            await conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS agent_state_view JSONB"))
            await conn.execute(
                text("UPDATE yuxi_schema_migrations SET version = 6, applied_at = CURRENT_TIMESTAMP WHERE domain = 'business'")
            )


async def main() -> None:
    """使用当前部署连接，显式执行一次结构升级。"""
    manager = PostgresManager()
    manager.initialize()
    try:
        await upgrade_agent_state_view(manager)
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
