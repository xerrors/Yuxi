from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.storage.postgres.manager import PostgresManager


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _FakeBeginContext:
    def __init__(self, statements: list[str]):
        self._statements = statements

    async def __aenter__(self):
        async def execute(stmt):
            self._statements.append(str(stmt))

        return SimpleNamespace(execute=execute)

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_ensure_business_schema_includes_mcp_auth_tables_and_columns():
    statements: list[str] = []
    manager = object.__new__(PostgresManager)
    PostgresManager.__init__(manager)
    manager._initialized = True
    manager.async_engine = SimpleNamespace(begin=lambda: _FakeBeginContext(statements))

    await manager.ensure_business_schema()

    assert any(
        "ALTER TABLE IF EXISTS mcp_servers ADD COLUMN IF NOT EXISTS auth_config_json JSONB" in stmt
        for stmt in statements
    )
    assert any("CREATE TABLE IF NOT EXISTS mcp_connections" in stmt for stmt in statements)
