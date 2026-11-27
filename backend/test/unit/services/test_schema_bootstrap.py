"""当前 Schema 初始化入口拒绝旧版本。"""

from unittest.mock import AsyncMock

import pytest

from yuxi.migrations import main as schema_bootstrap


@pytest.mark.asyncio
async def test_legacy_schema_version_is_rejected_before_ddl(monkeypatch):
    """旧版本标记必须阻止任何当前 Schema 建表。"""
    manager = object()

    create_tables = AsyncMock()
    monkeypatch.setattr(schema_bootstrap, "get_schema_versions", AsyncMock(return_value={"business": 7, "knowledge": 2}))
    monkeypatch.setattr(schema_bootstrap, "create_business_tables", create_tables)

    with pytest.raises(RuntimeError, match="Unsupported Yuxi schema versions"):
        await schema_bootstrap.initialize_schema(manager)

    create_tables.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_schema_failure_cleans_partial_tables_before_retry(monkeypatch):
    """fresh baseline 任一阶段失败后必须清理半成品。"""
    manager = object()

    create_tables = AsyncMock(side_effect=RuntimeError("ddl failed"))
    drop_schema = AsyncMock()
    monkeypatch.setattr(schema_bootstrap, "get_schema_versions", AsyncMock(return_value={}))
    monkeypatch.setattr(schema_bootstrap, "_require_empty_database", AsyncMock())
    monkeypatch.setattr(schema_bootstrap, "create_schema_version_table", AsyncMock())
    monkeypatch.setattr(schema_bootstrap, "record_schema_version", AsyncMock())
    monkeypatch.setattr(schema_bootstrap, "create_business_tables", create_tables)
    monkeypatch.setattr(schema_bootstrap, "drop_fresh_schema", drop_schema)

    with pytest.raises(RuntimeError, match="ddl failed"):
        await schema_bootstrap.initialize_schema(manager)

    drop_schema.assert_awaited_once_with(manager)
