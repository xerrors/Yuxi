"""管理连接检查不继承运行时停用与降级语义。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.agents.mcp import service
from yuxi.storage.postgres.models_business import MCPServer


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["empty", "tool", "error"])
async def test_inspection_connects_disabled_server_without_mutating_runtime(monkeypatch, outcome):
    server = MCPServer(
        slug="fixture-inspect",
        name="fixture",
        transport="streamable_http",
        url="http://127.0.0.1:12345/mcp",
        enabled=0,
        disabled_tools=["fixture_tool"],
        headers={"Authorization": "Bearer fixture"},
        timeout=1,
        sse_read_timeout=1,
    )
    tool = SimpleNamespace(name="fixture_tool", metadata={"retained": "value"})
    get_tools = AsyncMock(return_value=[] if outcome == "empty" else [tool])
    if outcome == "error":
        get_tools.side_effect = RuntimeError("fixture-credential-must-not-be-swallowed")
    configs = []

    def client(config):
        configs.append(config)
        return SimpleNamespace(get_tools=get_tools)

    monkeypatch.setattr(service, "MultiServerMCPClient", client)
    cache, stats = dict(service._mcp_tools_cache), dict(service._mcp_tools_stats)
    if outcome == "error":
        with pytest.raises(RuntimeError, match="fixture-credential"):
            await service.inspect_mcp_server_tools(server)
    else:
        tools = await service.inspect_mcp_server_tools(server)
        assert len(tools) == (0 if outcome == "empty" else 1)
        if tools:
            assert tool.metadata == {"retained": "value", "id": "mcp__fixtureInspect__fixtureTool"}
    get_tools.assert_awaited_once_with()
    assert configs[0][server.slug]["url"] == server.url
    assert configs[0][server.slug]["headers"] == server.headers
    assert "disabled_tools" not in configs[0][server.slug]
    assert server.enabled == 0 and server.disabled_tools == ["fixture_tool"]
    assert service._mcp_tools_cache == cache
    assert service._mcp_tools_stats == stats


@pytest.mark.asyncio
async def test_inspection_rejects_unmigrated_stdio_before_connecting(monkeypatch):
    def client(_):
        pytest.fail("自定义旧 stdio 不应启动子进程")

    monkeypatch.setattr(service, "MultiServerMCPClient", client)
    with pytest.raises(ValueError, match="stdio"):
        await service.inspect_mcp_server_tools(MCPServer(slug="fixture-stdio", transport="stdio", enabled=0))
