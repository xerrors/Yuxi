"""API 内 MCP 的真实协议装配、身份隔离与错误边界。"""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from yuxi.api.dependencies import auth
from yuxi.api.routers.public_v1.knowledge_mcp import create_knowledge_mcp
from yuxi.modules.knowledge.base import KBNotFoundError
from yuxi.modules.knowledge.mcp import server as knowledge_mcp

HEADERS = {"Authorization": "Bearer yxkey_alice", "Accept": "application/json, text/event-stream"}


def rpc(client, method, params=None, headers=None):
    """发送独立 MCP HTTP 请求。"""
    return client.post("/api/mcp", headers=headers or HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


@pytest.fixture
def client(monkeypatch):
    """保留真实认证依赖与协议传输，替换数据库和检索存储边界。"""

    @asynccontextmanager
    async def session():
        """提供认证提交所需的数据库上下文。"""
        yield SimpleNamespace(commit=AsyncMock())

    async def verify(token, db):
        """两种用户与不同权限 Key，用于验证请求身份隔离。"""
        if token not in ("yxkey_alice", "yxkey_bob", "yxkey_agents", "yxkey_full", "yxkey_no_department"):
            return None, None
        user = SimpleNamespace(uid=token.removeprefix("yxkey_"), department_id=None if token.endswith("no_department") else 1)
        key = SimpleNamespace(
            access_level="agents" if token.endswith("agents") else "full" if token.endswith("full") else "knowledge", app_id=None
        )
        return user, key

    async def visible(uid, *, selection="all"):
        """可见集合仅包含当前用户资源。"""
        assert selection == "all"
        return [{"kb_id": uid, "name": uid}]

    async def retrieve(kb_id, query_text, file_name=None):
        """只替换检索存储边界，保留真实查询用例与权限校验。"""
        return {"kb_id": kb_id, "results": [{"content": f"document for {kb_id}", "file_id": "source"}]}

    monkeypatch.setattr(knowledge_mcp.pg_manager, "get_async_session_context", session)
    monkeypatch.setattr(auth, "verify_api_key", verify)
    monkeypatch.setattr(knowledge_mcp, "visible_knowledge_bases", visible)
    monkeypatch.setattr(knowledge_mcp.knowledge_tools.knowledge_base, "retrieve", retrieve)
    adapter = create_knowledge_mcp()

    @asynccontextmanager
    async def lifespan(app):
        """与 API 一样管理 MCP manager 的启动关闭。"""
        async with adapter.manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.knowledge_mcp = adapter
    app.router.routes.append(adapter.route)
    with TestClient(app, base_url="http://localhost:5050") as client:
        yield client


def test_tools_publish_query_array_and_existing_document_schemas(client):
    """MCP 公开多库参数，其余输入继续复用 Public API Schema。"""
    tools = {t["name"]: t for t in rpc(client, "tools/list").json()["result"]["tools"]}
    assert set(tools) == {"list_kbs", "query_kb", "search_file", "open_kb_document", "find_kb_document"}
    for name, model in {
        "query_kb": knowledge_mcp.QueryKBInput,
        "search_file": knowledge_mcp.SearchFileInput,
        "open_kb_document": knowledge_mcp.OpenInputSchema,
        "find_kb_document": knowledge_mcp.FindInputSchema,
    }.items():
        assert tools[name]["inputSchema"] == model.model_json_schema()
    assert all(t["annotations"]["readOnlyHint"] for t in tools.values())
    assert "文件名" in tools["search_file"]["description"] and "has_more" in tools["search_file"]["description"]
    assert "next_offset" in tools["open_kb_document"]["description"]
    assert tools["query_kb"]["inputSchema"]["properties"]["kb_id"]["anyOf"] == [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}},
    ]
    assert knowledge_mcp.SearchInputSchema.model_json_schema()["properties"]["kb_id"]["type"] == "string"


def test_initialize_explains_search_and_source_workflow(client):
    """握手提供工具串联与来源核实指引。"""
    response = rpc(
        client, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}
    )
    instructions = response.json()["result"]["instructions"]
    for name in ("list_kbs", "query_kb", "search_file", "find_kb_document", "open_kb_document"):
        assert name in instructions
    assert "next_offset" in instructions and "行号" in instructions


def test_query_array_returns_merged_protocol_result(client, monkeypatch):
    """协议接收 ID 数组，文本与结构化结果包含各目标内容。"""
    monkeypatch.setattr(knowledge_mcp, "visible_knowledge_bases", AsyncMock(return_value=[{"kb_id": "alice"}, {"kb_id": "shared"}]))
    result = rpc(client, "tools/call", {"name": "query_kb", "arguments": {"kb_id": ["alice", "shared"], "query_text": "needle"}}).json()[
        "result"
    ]
    expected = {
        "kb_ids": ["alice", "shared"],
        "results": [
            {"content": "document for alice", "file_id": "source"},
            {"content": "document for shared", "file_id": "source"},
        ],
    }
    assert not result["isError"]
    assert result["structuredContent"] == json.loads(result["content"][0]["text"]) == expected


@pytest.mark.parametrize("kb_ids", [[], [1], [None], ["alice", "bob"]])
def test_query_array_rejects_invalid_or_invisible_targets(client, kb_ids):
    """数组参数保持类型约束，混合可见与不可见库返回失败。"""
    result = rpc(client, "tools/call", {"name": "query_kb", "arguments": {"kb_id": kb_ids, "query_text": "needle"}}).json()["result"]
    assert result["isError"]
    assert result.get("structuredContent") is None


@pytest.mark.parametrize(
    "token,status",
    [
        ("", 401),
        ("Bearer jwt", 401),
        ("Bearer yxkey_invalid", 401),
        ("Bearer yxkey_agents", 403),
        ("Bearer yxkey_no_department", 400),
        ("Bearer yxkey_full", 200),
    ],
)
def test_api_key_boundary(client, token, status):
    """MCP 沿用 API 权限与部门约束并拒绝 JWT。"""
    assert rpc(client, "tools/list", headers={**HEADERS, "Authorization": token}).status_code == status


def test_concurrent_keys_keep_resource_identity(client):
    """并发请求的列表及检索都只能看到自己的资源。"""

    def invoke(owner):
        """对实际工具返回内容断言身份隔离。"""
        headers = {**HEADERS, "Authorization": f"Bearer yxkey_{owner}"}
        listed = rpc(client, "tools/call", {"name": "list_kbs", "arguments": {}}, headers).json()["result"]
        text_kbs = json.loads(listed["content"][0]["text"])
        assert text_kbs[0]["kb_id"] == owner
        assert listed["structuredContent"] == {"knowledge_bases": text_kbs}
        result = rpc(client, "tools/call", {"name": "query_kb", "arguments": {"kb_id": owner, "query_text": "needle"}}, headers).json()[
            "result"
        ]
        assert not result["isError"]
        text_result = json.loads(result["content"][0]["text"])
        assert text_result["results"][0]["content"] == f"document for {owner}"
        assert result["structuredContent"] == text_result

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(invoke, ["alice", "bob"] * 3))


def test_other_users_kb_is_rejected(client):
    """调用服务层的可见性 guard 拒绝他人知识库。"""
    result = rpc(client, "tools/call", {"name": "query_kb", "arguments": {"kb_id": "bob", "query_text": "needle"}}).json()["result"]
    assert result["isError"] and "404" in result["content"][0]["text"]


def test_revocation_after_initialize_is_immediate(client, monkeypatch):
    """握手成功不能绕过后续凭据撤销。"""
    assert (
        rpc(
            client, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}
        ).status_code
        == 200
    )
    monkeypatch.setattr(auth, "verify_api_key", AsyncMock(return_value=(None, None)))
    assert rpc(client, "tools/call", {"name": "list_kbs", "arguments": {}}).status_code == 401


@pytest.mark.parametrize(
    "failure,status,guidance",
    [
        (RuntimeError("PRIVATE password"), 500, "稍后重试"),
        (KBNotFoundError("PRIVATE resource"), 404, "list_kbs"),
        (ValueError("PRIVATE input"), 400, "参数"),
    ],
)
def test_service_failures_are_errors_without_private_details(client, monkeypatch, failure, status, guidance):
    """SDK 不得把服务异常正文直接公开给客户端。"""
    monkeypatch.setattr(knowledge_mcp.knowledge_tools, "query_kb", AsyncMock(side_effect=failure))
    result = rpc(client, "tools/call", {"name": "query_kb", "arguments": {"kb_id": "alice", "query_text": "needle"}}).json()["result"]
    assert result["isError"] and str(status) in result["content"][0]["text"]
    assert guidance in result["content"][0]["text"]
    assert "PRIVATE" not in str(result)
    assert result.get("structuredContent") is None


@pytest.mark.parametrize(
    "name,arguments",
    [("search_file", {}), ("find_kb_document", {"kb_id": "alice", "file_id": "doc", "patterns": []})],
)
def test_business_input_errors_offer_safe_recovery(client, name, arguments):
    """Schema 合法但不满足业务条件时返回参数指引。"""
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments}).json()["result"]
    assert result["isError"]
    assert "400" in result["content"][0]["text"] and "参数" in result["content"][0]["text"]


@pytest.mark.parametrize("arguments", [{"window_size": 0}, {"window_size": 2001}, {"line": 0}, {"offset": -1}])
def test_window_bounds_rejected(client, arguments):
    """协议层在调用存储前校验原文窗口约束。"""
    result = rpc(client, "tools/call", {"name": "open_kb_document", "arguments": {"kb_id": "alice", "file_id": "doc", **arguments}}).json()[
        "result"
    ]
    assert result["isError"]


def test_unknown_tool_cannot_dispatch_arbitrary_service(client):
    """注册外的名称不能调用任意服务函数。"""
    result = rpc(client, "tools/call", {"name": "require_visible_kb", "arguments": {}}).json()["result"]
    assert result["isError"] and result["content"][0]["text"] == "Unknown knowledge tool"


@pytest.mark.parametrize("headers,status", [({"Host": "evil.example"}, 421), ({"Origin": "https://evil.example"}, 403)])
def test_http_transport_boundary(client, headers, status):
    """保留 Host/Origin 安全边界。"""
    assert rpc(client, "tools/list", headers={**HEADERS, **headers}).status_code == status


@pytest.mark.asyncio
async def test_api_lifespan_starts_mcp_and_cleans_up_after_failure(monkeypatch):
    """MCP 启动失败阻止接流量，且已有 API 资源仍被清理。"""
    from yuxi.api import lifespan as lifecycle

    released = []

    async def release(name, callback):
        """记录实际执行的清理阶段。"""
        released.append(name)

    @asynccontextmanager
    async def failed_manager():
        """在 manager 进入阶段恢复启动失败。"""
        raise RuntimeError("MCP startup failed")
        yield

    monkeypatch.setattr(lifecycle, "startup", AsyncMock())
    monkeypatch.setattr(lifecycle, "shutdown_component", release)
    app = FastAPI()
    app.state.knowledge_mcp = SimpleNamespace(manager=SimpleNamespace(run=failed_manager))
    with pytest.raises(RuntimeError, match="MCP startup failed"):
        async with lifecycle.lifespan(app):
            pytest.fail("MCP failure must prevent serving requests")
    assert released == ["queue_clients", "neo4j", "postgres"]
    assert app.state.startup_complete is False


def test_removed_download_is_not_discoverable_or_callable(client):
    """已移除的工具不出现在目录，猜测名称也只返回未知工具。"""
    listed = rpc(client, "tools/list").json()["result"]["tools"]
    assert "download_kb_file" not in {item["name"] for item in listed}
    result = rpc(client, "tools/call", {"name": "download_kb_file", "arguments": {"kb_id": "alice", "file_id": "source"}}).json()["result"]
    assert result["isError"]
    assert result["content"][0]["text"] == "Unknown knowledge tool"


def test_api_key_rejection_preserves_response_and_never_enters_mcp(client, monkeypatch):
    """API 认证失败的状态、正文和响应头原样传出，协议执行器不能被调用。"""
    from yuxi.api.routers.public_v1 import knowledge_mcp as api_adapter

    monkeypatch.setattr(
        api_adapter,
        "get_current_user",
        AsyncMock(side_effect=HTTPException(403, "当前凭据不能读取知识库", headers={"X-Auth-Rejection": "knowledge"})),
    )
    manager = AsyncMock(side_effect=AssertionError("拒绝请求不得进入 MCP"))
    monkeypatch.setattr(client.app.state.knowledge_mcp.manager, "handle_request", manager)
    response = rpc(client, "tools/list")
    assert response.status_code == 403
    assert response.json() == {"detail": "当前凭据不能读取知识库"}
    assert response.headers["X-Auth-Rejection"] == "knowledge"
    manager.assert_not_awaited()


@pytest.mark.parametrize("authorization", ["", "Bearer invalid-delegation"])
def test_invalid_delegate_returns_bearer_challenge_before_mcp(client, monkeypatch, authorization):
    """缺失或无效委托直接返回标准 401，不能进入 MCP manager。"""
    manager = AsyncMock(side_effect=AssertionError("拒绝请求不得进入 MCP"))
    monkeypatch.setattr(client.app.state.knowledge_mcp.manager, "handle_request", manager)
    response = rpc(client, "tools/list", headers={**HEADERS, "Authorization": authorization})
    assert response.status_code == 401
    assert response.json() == {
        "detail": "未提供有效的知识库访问凭据：请使用 Access Token 或具备知识库权限的 API Key（Authorization: Bearer）"
    }
    assert response.headers["WWW-Authenticate"] == "Bearer"
    manager.assert_not_awaited()


def test_internal_tool_failure_records_diagnostic_without_exposing_it(client, monkeypatch):
    """服务端保留故障类型与栈，MCP 仍只公开稳定错误。"""
    records = []
    sink_id = knowledge_mcp.logger.add(
        lambda message: records.append(message.record), filter=lambda record: record["extra"].get("component") == "knowledge_mcp"
    )
    try:
        monkeypatch.setattr(knowledge_mcp.knowledge_tools, "query_kb", AsyncMock(side_effect=RuntimeError("private diagnostic")))
        result = rpc(client, "tools/call", {"name": "query_kb", "arguments": {"kb_id": "alice", "query_text": "needle"}}).json()["result"]
        assert result["isError"] is True
        assert "private diagnostic" not in str(result)
        diagnostic = records[-1]
        assert diagnostic["extra"]["tool_name"] == "query_kb"
        assert diagnostic["extra"]["error_type"] == "RuntimeError"
        assert diagnostic["exception"].type is RuntimeError
    finally:
        knowledge_mcp.logger.remove(sink_id)
