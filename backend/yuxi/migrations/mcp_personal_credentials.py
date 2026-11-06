"""显式升级个人 MCP 凭证需要的业务库字段。"""

import asyncio

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.schema import schema_migration_lock


async def upgrade_mcp_personal_credentials(manager: PostgresManager) -> None:
    """在初始化锁内幂等升级 business v1/v2，保留既有凭证。"""
    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions.get("business") not in {1, 2} or "initializing" in versions:
            raise RuntimeError("个人 MCP 凭证升级需要已完成初始化的 business v1/v2")
        async with manager.async_engine.begin() as conn:
            await conn.execute(text("ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS api_key_header VARCHAR(128)"))
            await conn.execute(
                text("UPDATE yuxi_schema_migrations SET version = 2, applied_at = CURRENT_TIMESTAMP WHERE domain = 'business'")
            )


async def main() -> None:
    """使用当前部署连接，显式执行一次结构升级。"""
    manager = PostgresManager()
    manager.initialize()
    try:
        await upgrade_mcp_personal_credentials(manager)
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
