"""运行进程的 PostgreSQL schema 版本只读校验。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from yuxi.infrastructure.postgres.manager import PostgresManager

BUSINESS_SCHEMA_VERSION = 2
KNOWLEDGE_SCHEMA_VERSION = 2
SCHEMA_VERSION_TABLE = "yuxi_schema_migrations"


async def get_schema_versions(manager: PostgresManager) -> dict[str, int]:
    """读取当前数据库已完成的 Yuxi Schema 版本。"""
    manager.require_initialized()
    async with manager.async_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": SCHEMA_VERSION_TABLE},
        )
        if not exists:
            return {}
        rows = await conn.execute(text(f"SELECT domain, version FROM {SCHEMA_VERSION_TABLE}"))
        return {str(row.domain): int(row.version) for row in rows}


async def require_current_schema(manager: PostgresManager) -> None:
    """只读校验运行进程需要的 Schema 域均为精确当前版本。"""
    versions = await get_schema_versions(manager)
    required = {
        "business": BUSINESS_SCHEMA_VERSION,
        "knowledge": KNOWLEDGE_SCHEMA_VERSION,
    }
    mismatches = [
        f"{domain}={versions.get(domain, 'missing')} (required {version})"
        for domain, version in required.items()
        if versions.get(domain) != version
    ]
    if "initializing" in versions:
        mismatches.append("fresh initialization is in progress")
    if mismatches:
        detail = ", ".join(mismatches)
        raise RuntimeError(f"Database schema initialization is incomplete or incompatible: {detail}")
