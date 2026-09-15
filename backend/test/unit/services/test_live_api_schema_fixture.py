"""临时 Schema 检查事件循环必须完整释放其连接池。"""

import asyncio
from unittest.mock import Mock

import pytest
from yuxi.storage.postgres.manager import pg_manager

from test.integration import conftest as integration_fixtures


@pytest.mark.parametrize("schema_error", [False, True])
def test_live_schema_fixture_closes_pool_in_owning_loop(monkeypatch, schema_error):
    """校验成功或失败均先关闭同一循环内的池，再退出anyio.run。"""
    events = []

    async def verify():
        events.append(("verify", asyncio.get_running_loop()))
        if schema_error:
            raise RuntimeError("schema mismatch")

    async def close():
        events.append(("close", asyncio.get_running_loop()))

    initialize = Mock()
    monkeypatch.setattr(integration_fixtures, "ADMIN_LOGIN", "fixture")
    monkeypatch.setattr(integration_fixtures, "ADMIN_PASSWORD", "fixture")
    monkeypatch.setattr(pg_manager, "initialize", initialize)
    monkeypatch.setattr(pg_manager, "require_current_schema", verify)
    monkeypatch.setattr(pg_manager, "close", close)
    fixture = integration_fixtures.ensure_live_api_schema.__wrapped__
    if schema_error:
        with pytest.raises(RuntimeError, match="schema mismatch"):
            fixture()
    else:
        fixture()
    initialize.assert_called_once()
    assert [event for event, _loop in events] == ["verify", "close"]
    assert events[0][1] is events[1][1]
    assert events[0][1].is_closed()
