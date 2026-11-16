"""显式为 business 3/knowledge 3 部署添加个人成果目录。"""

import asyncio

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import PostgresManager, pg_manager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.schema import schema_migration_lock


async def upgrade_workspace_deliverables_schema(manager: PostgresManager) -> None:
    """在单一事务中建成果表并提升业务版本，重复执行不修改数据。"""
    load_models()
    from yuxi.modules.workspace.models import Deliverable

    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions == {"business": 4, "knowledge": 3}:
            return
        if versions != {"business": 3, "knowledge": 3}:
            raise RuntimeError(f"成果目录升级只支持 business=3/knowledge=3，当前为 {versions}")

        async with manager.async_engine.begin() as conn:
            exists = await conn.scalar(text("SELECT to_regclass('workspace_deliverables') IS NOT NULL"))
            if exists:
                raise RuntimeError("发现未版本化成果表，请先审计结构，禁止只修改版本号")
            await conn.run_sync(Deliverable.__table__.create)
            await conn.execute(
                text("UPDATE yuxi_schema_migrations SET version = 4, applied_at = CURRENT_TIMESTAMP WHERE domain = 'business'")
            )


async def main() -> None:
    """供部署维护窗口显式执行成果结构升级。"""
    pg_manager.initialize()
    try:
        await upgrade_workspace_deliverables_schema(pg_manager)
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
