"""MCP 协议结果与知识库委托签发器的独立边界案例。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp import types

from yuxi.modules.knowledge.mcp import client

pytestmark = pytest.mark.asyncio


@pytest.fixture
def result_transport(monkeypatch):
    """固定 SDK 协议结果；真实握手与 HTTP 由 integration 覆盖。"""
    state = SimpleNamespace(result=None, fail=False)

    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield None, None, None

    class Session:
        """复用 SDK 原始 CallToolResult 契约。"""

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            pass

        async def call_tool(self, *args, **kwargs):
            if state.fail:
                raise ConnectionError("wire down")
            return state.result

    monkeypatch.setattr(client, "streamable_http_client", transport)
    monkeypatch.setattr(client, "ClientSession", Session)
    return state


async def test_mcp_iserror_is_a_tool_failure_and_not_service_disconnect(result_transport):
    """参数/权限拒绝仍允许之后的正确调用恢复。"""
    result_transport.result = types.CallToolResult(isError=True, content=[types.TextContent(type="text", text="资源不可见")])
    with pytest.raises(client.KnowledgeMCPError, match="资源不可见") as error:
        await client.call_knowledge_mcp("query_kb", {}, uid="u", selection=[])
    assert isinstance(error.value, RuntimeError)
    result_transport.result = types.CallToolResult(content=[], structuredContent={"knowledge_bases": []})
    assert await client.call_knowledge_mcp("list_kbs", {}, uid="u", selection=[]) == []


async def test_malformed_wire_result_cannot_masquerade_as_empty_success(result_transport):
    """缺少结构化结果时显式失败。"""
    result_transport.result = types.CallToolResult(content=[types.TextContent(type="text", text="not structured")])
    with pytest.raises(client.KnowledgeMCPError, match="无效结果"):
        await client.call_knowledge_mcp("list_kbs", {}, uid="u", selection="all")


async def test_connection_failure_is_unavailable_not_tool_level_error(result_transport):
    """连接故障收敛为 server 不可用，与工具参数拒绝保持不同类型。"""
    assert not issubclass(client.KnowledgeMCPUnavailable, client.KnowledgeMCPError)
    result_transport.fail = True
    with pytest.raises(client.KnowledgeMCPUnavailable, match="不可用"):
        await client.call_knowledge_mcp("query_kb", {}, uid="u", selection="all")


@pytest.mark.parametrize("selection", ["all", ["kb-selected"], []])
async def test_runtime_scope_keeps_session_selection(selection):
    """认证直接读取可信运行上下文，不建立第二份会话范围状态。"""
    context = SimpleNamespace(uid="u", knowledges=selection)
    options = client.knowledge_mcp_connection(uid="u", context=context)
    assert options["auth"].selection == selection


async def test_runtime_requires_matching_context():
    """缺失上下文不得静默扩大到全部知识库。"""
    with pytest.raises(ValueError, match="可信运行上下文"):
        client.knowledge_mcp_connection(uid="u")
    with pytest.raises(ValueError, match="可信运行上下文"):
        client.knowledge_mcp_connection(uid="u", context=SimpleNamespace(uid="other"))
    with pytest.raises(client.MCPDelegationContextError, match="选择范围"):
        client.knowledge_mcp_connection(uid="u", context=SimpleNamespace(uid="u"))
    assert client.knowledge_mcp_connection(uid="u", management=True)["auth"].selection == "all"
