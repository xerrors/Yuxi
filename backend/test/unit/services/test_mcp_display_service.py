"""纯显示改名不改变 MCP 连接字段。"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from yuxi.services import mcp_display_service as service


@pytest.mark.asyncio
async def test_mcp_display_keeps_every_connection_field(monkeypatch):
    """包括旧stdio字段都保持原字节，响应只返回slug/name。"""
    server = SimpleNamespace(
        slug="stable",
        name="旧名",
        updated_by="old",
        transport="stdio",
        command="binary",
        args=["--flag"],
        env={"TOKEN": "synthetic"},
        url="http://fixture",
        headers={"Authorization": "synthetic"},
        timeout=33,
        sse_read_timeout=77,
        enabled=True,
        disabled_tools=["foo"],
        tags=["tag"],
        icon="icon",
        description="desc",
    )
    before = deepcopy(vars(server))
    monkeypatch.setattr(service, "lock_server", AsyncMock(return_value=server))
    monkeypatch.setattr(service, "is_builtin_mcp_server", lambda item: False)
    db = SimpleNamespace(commit=AsyncMock())
    response = await service.set_mcp_display_name(db, "stable", " 新名 ", "admin")
    assert response == {"slug": "stable", "name": "新名"}
    assert vars(server) == {**before, "name": "新名", "updated_by": "admin"}
    db.commit.assert_awaited_once()
    monkeypatch.setattr(service, "is_builtin_mcp_server", lambda item: True)
    save = AsyncMock()
    monkeypatch.setattr(service.tool_display_repository, "save_name", save)
    await service.set_mcp_display_name(db, "stable", "内置中文", "admin")
    save.assert_awaited_once_with("stable", "内置中文", "admin", key=service.MCP_NAMES)
    assert vars(server) == {**before, "name": "新名", "updated_by": "admin"}
    assert db.commit.await_count == 1
