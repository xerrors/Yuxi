"""通过隔离 PostgreSQL 与回环 HTTP 验证委托 MCP 的持续授权边界。"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.api.dependencies.auth import get_db, get_required_user, get_superadmin_user
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.extensions.mcp import mcp as management_router
from yuxi.api.routers.public_v1.knowledge import public_knowledge_router
from yuxi.api.routers.public_v1.knowledge_mcp import create_knowledge_mcp
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.extensions.mcp import service
from yuxi.modules.extensions.mcp.models import MCPConnection, MCPServer
from yuxi.modules.identity.models import Department, User
from yuxi.modules.knowledge.models import KnowledgeBase
from yuxi.shared.errors import DomainError, PermissionDeniedError, StateConflictError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


def read_scope(uid):
    """仅授予本例用户读取合成知识库。"""
    return {
        "version": 2,
        "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [uid]},
        "manage_scope": None,
    }


def list_ids(output):
    """从真实 adapter 的协议文本回读业务身份。"""
    if isinstance(output, list):
        payload = json.loads(output[0]["text"])
    elif isinstance(output, dict):
        payload = output
    else:
        payload = json.loads(output)
    rows = payload.get("knowledge_bases", []) if isinstance(payload, dict) else payload
    return [row["kb_id"] for row in rows]


def nested_errors(error):
    """展开 SDK TaskGroup 包装，断言真实拒绝原因。"""
    yield error
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            yield from nested_errors(child)
    elif error.__cause__ is not None:
        yield from nested_errors(error.__cause__)


@pytest_asyncio.fixture
async def isolated_mcp(monkeypatch):
    """每例仅创建并回收自己的 Schema、HTTP 服务与合成用户。"""
    schema, admin, engine, _ = await create_isolated_manager("pytest_mcp_delegation")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    http_server = None
    task = None
    sock = None
    try:
        async with engine.begin() as connection:
            for model in [Department, User, MCPServer, MCPConnection, KnowledgeBase]:
                await connection.run_sync(model.__table__.create)
        async with sessions() as db:
            department = Department(name="isolated-mcp-delegation")
            db.add(department)
            await db.flush()
            reader = User(
                uid="delegation-reader", username="delegation-reader", password_hash="!disabled", role="user", department_id=department.id
            )
            owner = User(
                uid="delegation-admin",
                username="delegation-admin",
                password_hash="!disabled",
                role="superadmin",
                department_id=department.id,
            )
            db.add_all([reader, owner])
            await db.flush()
            db.add(
                KnowledgeBase(
                    kb_id="delegation-kb",
                    name="Synthetic isolated source",
                    kb_type="milvus",
                    created_by=owner.uid,
                    share_config=read_scope(reader.uid),
                )
            )
            await db.commit()

        @asynccontextmanager
        async def session_context():
            """把真实业务读取绑定到本例独立 Schema。"""
            async with sessions() as db:
                yield db
                await db.commit()

        monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)

        knowledge = create_knowledge_mcp()

        @asynccontextmanager
        async def lifespan(app):
            """启动真实 MCP transport 生命周期。"""
            async with knowledge.manager.run():
                yield

        app = FastAPI(routes=[knowledge.route], lifespan=lifespan)
        app.include_router(management_router, prefix="/api")
        app.include_router(public_knowledge_router, prefix="/api")
        app.add_exception_handler(DomainError, domain_error_handler)
        app.dependency_overrides[get_required_user] = lambda: reader
        requests = []

        @app.middleware("http")
        async def record_outbound(request, call_next):
            """仅记录路径，用于证明拒绝发生在实际出站之前。"""
            payload = await request.json() if request.method == "POST" and request.url.path == "/api/mcp" else {}
            requests.append({"path": request.url.path, "method": payload.get("method"), "params": payload.get("params", {})})
            return await call_next(request)

        async def management_db():
            """管理 HTTP 也使用本例数据库。"""
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = management_db
        app.dependency_overrides[get_superadmin_user] = lambda: owner
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        url = base_url + "/api/mcp"
        monkeypatch.setattr("yuxi.modules.knowledge.mcp.client.KNOWLEDGE_MCP_URL", url)
        manifest = {**service.BUILTIN_MCP_SERVERS}
        manifest["knowledge"] = {
            "type": "http",
            "url": url,
            "extra_data": {"name": "知识库"},
        }
        monkeypatch.setattr(service, "BUILTIN_MCP_SERVERS", manifest)
        http_server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        task = asyncio.create_task(http_server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not http_server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        async with sessions() as db:
            row = MCPServer(
                slug="knowledge",
                name="Knowledge",
                transport="streamable_http",
                url=url,
                auth_mode="delegation",
                enabled=1,
                created_by="system",
                updated_by="system",
            )
            db.add(row)
            await db.commit()
        runtime = SimpleNamespace(uid=reader.uid, knowledges="all", run_id="independent-run")
        yield SimpleNamespace(
            app=app,
            sessions=sessions,
            engine=engine,
            reader=reader,
            owner=owner,
            runtime=runtime,
            row=row,
            requests=requests,
            base_url=base_url,
        )
    finally:
        if http_server:
            http_server.should_exit = True
        if task:
            await asyncio.wait_for(task, 10)
        if sock:
            sock.close()
        await drop_isolated_schema(schema, admin, engine)


async def runtime_tools(context, *, selection="all"):
    """从正式运行入口取目录，业务预期由真实 HTTP 返回值独立证明。"""
    runtime = SimpleNamespace(uid=context.reader.uid, knowledges=selection, run_id="independent-run")
    loaded = await service.resolve_runtime_mcp_tools(["knowledge"], uid=context.reader.uid, context=runtime)
    return loaded.get("knowledge", [])


async def update_server(context, **values):
    """通过独立事务提交策略变化。"""
    async with context.sessions() as db:
        row = await db.scalar(select(MCPServer).where(MCPServer.slug == "knowledge"))
        for key, value in values.items():
            setattr(row, key, value)
        await db.commit()


async def test_existing_tool_honors_server_disabled_without_outbound(isolated_mcp):
    """旧对象直接调用也不能绕过管理员停用。"""
    context = isolated_mcp
    tools = {tool.name: tool for tool in await runtime_tools(context)}
    assert list_ids(await tools["list_kbs"].ainvoke({})) == ["delegation-kb"]
    await update_server(context, enabled=0)
    before = len(context.requests)
    with pytest.raises(Exception) as rejected:
        await tools["list_kbs"].ainvoke({})
    assert any(isinstance(error, StateConflictError) for error in nested_errors(rejected.value))
    assert len(context.requests) == before, "管理员停用后旧工具仍向 MCP 发送了请求"


async def test_existing_tool_honors_tool_disabled_before_tool_request(isolated_mcp):
    """单工具停用必须在旧对象的真实执行边界生效。"""
    context = isolated_mcp
    tools = {tool.name: tool for tool in await runtime_tools(context)}
    await update_server(context, disabled_tools=["list_kbs"])
    before = len(context.requests)
    with pytest.raises(Exception) as rejected:
        await tools["list_kbs"].ainvoke({})
    assert any(isinstance(error, StateConflictError) and "工具已停用" in str(error) for error in nested_errors(rejected.value))
    assert not any(item["method"] == "tools/call" for item in context.requests[before:]), "disabled_tools 变更后旧工具仍执行调用"


async def test_cached_catalog_recovers_tool_enabled_in_same_run(isolated_mcp):
    """Run 缓存必须保存完整目录，管理员重新启用后恢复候选。"""
    context = isolated_mcp
    await update_server(context, disabled_tools=["list_kbs"])
    cache = {}
    before = await service.resolve_runtime_mcp_tools(["knowledge"], uid=context.reader.uid, run_tools=cache, context=context.runtime)
    assert "list_kbs" not in {tool.name for tool in before["knowledge"]}
    await update_server(context, disabled_tools=[])
    after = await service.resolve_runtime_mcp_tools(["knowledge"], uid=context.reader.uid, run_tools=cache, context=context.runtime)
    assert "list_kbs" in {tool.name for tool in after["knowledge"]}, "同 Run 重新启用后工具仍从缓存永久消失"
    restored = next(tool for tool in after["knowledge"] if tool.name == "list_kbs")
    assert list_ids(await restored.ainvoke({})) == ["delegation-kb"]


async def test_configured_mcp_recovers_after_transient_discovery_and_stays_bound(isolated_mcp, monkeypatch):
    """直接配置的服务发现暂时失败后，后续模型请求恢复并持续绑定工具。"""
    from langchain.agents.middleware.types import ModelRequest

    from yuxi.modules.agents.runtime.middlewares.skills import SkillsMiddleware
    from yuxi.modules.extensions.tools.runtime import resolve_configured_runtime_tools

    context = isolated_mcp
    runtime = context.runtime
    runtime.tools, runtime.mcps, runtime.mcp_unavailable = [], ["knowledge"], {}
    runtime.skill_runtime_snapshot = {"effective_skills": [], "preloaded_skills": [], "runtime_skills": {}, "skill_metadata": {}}
    original_loader = service.load_mcp_tools
    attempts = 0

    async def discover(*args, **kwargs):
        """仅让首次发现发生临时故障，恢复后使用真实 MCP transport。"""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("synthetic transient discovery failure")
        return await original_loader(*args, **kwargs)

    monkeypatch.setattr(service, "load_mcp_tools", discover)
    registered = await resolve_configured_runtime_tools(runtime)
    assert "list_kbs" not in {tool.name for tool in registered}
    request = ModelRequest(model=SimpleNamespace(), messages=[], tools=registered, state={}, runtime=SimpleNamespace(context=runtime))

    async def model(current):
        """读取正式中间件实际交付给模型的工具。"""
        return {tool.name: tool for tool in current.tools}

    middleware = SkillsMiddleware()
    first = await middleware.awrap_model_call(request, model)
    second = await middleware.awrap_model_call(request, model)
    assert "list_kbs" in first and "list_kbs" in second
    assert attempts == 2 and runtime.mcp_unavailable == {}
    assert list_ids(await second["list_kbs"].ainvoke({})) == ["delegation-kb"]


async def test_existing_tool_reflects_current_knowledge_visibility(isolated_mcp):
    """凭据和旧工具不能固化已撤销的知识库权限。"""
    context = isolated_mcp
    tools = {tool.name: tool for tool in await runtime_tools(context)}
    assert list_ids(await tools["list_kbs"].ainvoke({})) == ["delegation-kb"]
    async with context.sessions() as db:
        kb = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == "delegation-kb"))
        kb.share_config = read_scope(context.owner.uid)
        await db.commit()
    assert list_ids(await tools["list_kbs"].ainvoke({})) == []
    output = await tools["query_kb"].ainvoke({"kb_id": "delegation-kb", "query_text": "synthetic"})
    assert "404" in str(output)
    assert "Synthetic isolated source" not in str(output)


@pytest.mark.skipif(os.getenv("RUN_ISOLATED_OBJECT_STORE") != "1", reason="需要独立的临时对象存储")
async def test_mcp_document_pages_use_current_permission_and_bounded_wire_payload(isolated_mcp):
    """真实 MCP、数据库与对象存储中，逐页恢复原文且撤权后不可访问。"""
    from yuxi.infrastructure.minio import get_minio_client
    from yuxi.infrastructure.minio.client import MinIOClient
    from yuxi.modules.knowledge.mcp.auth import create_knowledge_token
    from yuxi.modules.knowledge.models import KnowledgeFile

    context = isolated_mcp
    async with context.engine.begin() as connection:
        await connection.run_sync(KnowledgeFile.__table__.create)
    lines = [f"line-{index} " + "多字节正文" * 500 for index in range(8)]
    store = get_minio_client()
    bucket = MinIOClient.KB_BUCKETS["parsed"]
    object_name = "delegation-kb/parsed/isolated-file.md"
    uploaded = await store.aupload_file(bucket, object_name, "\n".join(lines).encode(), content_type="text/markdown")
    try:
        async with context.sessions() as db:
            db.add(
                KnowledgeFile(
                    kb_id="delegation-kb", file_id="isolated-file", filename="document.md", markdown_file=uploaded.url, status="parsed"
                )
            )
            await db.commit()
        token = create_knowledge_token(uid=context.reader.uid, selection=["delegation-kb"])
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
        async with httpx.AsyncClient(base_url=context.base_url, headers=headers, trust_env=False) as client:

            async def open_page(offset):
                """经真实 MCP HTTP 读取指定偏移。"""
                return await client.post(
                    "/api/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "open_kb_document",
                            "arguments": {"kb_id": "delegation-kb", "file_id": "isolated-file", "offset": offset},
                        },
                    },
                )

            offset, recovered, pages = 0, [], 0
            while offset is not None:
                response = await open_page(offset)
                assert response.status_code == 200 and len(response.content) <= 64 * 1024
                result = response.json()["result"]
                assert result["isError"] is False
                page = json.loads(result["content"][0]["text"])
                assert page == result["structuredContent"]
                recovered.extend(line.split("\t", 1)[1] for line in page["content"].splitlines())
                offset = page["next_offset"]
                pages += 1
            assert pages > 1
            assert recovered == lines
            oversized_lines = [f"line-{index + 1}" for index in range(1200)]
            oversized_lines[899] = "x" * 40000
            await store.aupload_file(bucket, object_name, "\n".join(oversized_lines).encode(), content_type="text/markdown")
            arguments = {"kb_id": "delegation-kb", "file_id": "isolated-file", "offset": 899, "window_size": 101}
            too_large = await client.post("/api/v1/knowledge/tools/open_kb_document", json=arguments)
            assert too_large.status_code == 413
            assert too_large.json() == {"detail": {"code": "document_line_too_large", "message": "文档单行超过读取窗口字节上限"}}
            following = await client.post("/api/v1/knowledge/tools/open_kb_document", json={**arguments, "offset": 900, "window_size": 100})
            assert following.status_code == 200
            following_page = following.json()
            assert (following_page["start_line"], following_page["end_line"]) == (901, 1000)
            assert [line.split("\t", 1)[1] for line in following_page["content"].splitlines()] == oversized_lines[900:1000]
            mcp_too_large = await open_page(899)
            assert mcp_too_large.json()["result"]["isError"] is True
            async with context.sessions() as db:
                kb = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == "delegation-kb"))
                kb.share_config = read_scope(context.owner.uid)
                await db.commit()
            denied = await open_page(0)
            assert denied.json()["result"]["isError"] is True
            assert "多字节正文" not in denied.text
    finally:
        await store.adelete_file(bucket, object_name)


@pytest.mark.parametrize("admin_enabled", [0, 1])
async def test_management_http_lists_and_tests_delegated_server(isolated_mcp, admin_enabled):
    """普通用户不能测试委托服务，超管仍可检查停用服务的连接。"""
    context = isolated_mcp
    await update_server(context, enabled=admin_enabled, disabled_tools=["query_kb"])
    async with httpx.AsyncClient(base_url=context.base_url, trust_env=False) as client:
        denied = await client.post("/api/system/mcp-servers/knowledge/test")
        assert denied.status_code == 403, denied.text
        assert not any(request["path"] == "/api/mcp" for request in context.requests)

        context.app.dependency_overrides[get_required_user] = lambda: context.owner
        tested = await client.post("/api/system/mcp-servers/knowledge/test")
        listed = await client.get("/api/system/mcp-servers/knowledge/tools")
    assert tested.status_code == 200, tested.text
    assert tested.json()["tool_count"] == 5
    assert listed.status_code == 200, listed.text
    data = {tool["name"]: tool for tool in listed.json()["data"]}
    assert set(data) == {"list_kbs", "query_kb", "search_file", "open_kb_document", "find_kb_document"}
    assert data["query_kb"]["enabled"] is False
    assert data["list_kbs"]["enabled"] is True
    async with context.sessions() as db:
        row = await db.scalar(select(MCPServer).where(MCPServer.slug == "knowledge"))
        assert row.enabled == admin_enabled


async def test_existing_tool_rejects_changed_address_before_outbound(isolated_mcp):
    """目录对象不得继续携带凭据调用已失效的旧地址。"""
    context = isolated_mcp
    tools = {tool.name: tool for tool in await runtime_tools(context)}
    await update_server(context, url=context.base_url + "/changed-address")
    before = len(context.requests)
    with pytest.raises(Exception) as rejected:
        await tools["list_kbs"].ainvoke({})
    assert any(isinstance(error, StateConflictError) for error in nested_errors(rejected.value))
    assert len(context.requests) == before, "地址修订后旧工具仍向原服务发送了凭据"


async def test_existing_tool_rejects_locked_actor_before_outbound(isolated_mcp):
    """执行者锁定后旧工具在签发委托之前拒绝出站。"""
    from datetime import timedelta

    from yuxi.shared.datetime import utc_now

    context = isolated_mcp
    tools = {tool.name: tool for tool in await runtime_tools(context)}
    async with context.sessions() as db:
        user = await db.scalar(select(User).where(User.uid == context.reader.uid))
        user.login_locked_until = utc_now() + timedelta(minutes=5)
        await db.commit()
    before = len(context.requests)
    with pytest.raises(Exception) as rejected:
        await tools["list_kbs"].ainvoke({})
    assert any(isinstance(error, PermissionDeniedError) for error in nested_errors(rejected.value))
    assert len(context.requests) == before


@pytest.mark.parametrize(
    "selection,expected",
    [("all", {"delegation-kb", "delegation-other-kb"}), (["delegation-kb"], {"delegation-kb"}), ([], set())],
)
async def test_delegate_preserves_all_explicit_and_empty_selection(isolated_mcp, selection, expected):
    """真实权限与会话选择取交集，模型直接指定其他资源仍被服务端拒绝。"""
    context = isolated_mcp
    async with context.sessions() as db:
        db.add_all(
            [
                KnowledgeBase(
                    kb_id="delegation-other-kb",
                    name="Second authorized source",
                    kb_type="milvus",
                    created_by=context.owner.uid,
                    share_config=read_scope(context.reader.uid),
                ),
                KnowledgeBase(
                    kb_id="delegation-private-kb",
                    name="Private source must not leak",
                    kb_type="milvus",
                    created_by=context.owner.uid,
                    share_config=read_scope(context.owner.uid),
                ),
            ]
        )
        await db.commit()
    tools = {tool.name: tool for tool in await runtime_tools(context, selection=selection)}
    assert set(list_ids(await tools["list_kbs"].ainvoke({}))) == expected
    denied_id = "delegation-private-kb" if selection == "all" else "delegation-other-kb"
    denied = await tools["query_kb"].ainvoke({"kb_id": denied_id, "query_text": "synthetic"})
    assert "404" in str(denied)
    assert "Private source must not leak" not in str(denied)


@pytest.mark.parametrize("missing", ["context", "selection"])
async def test_runtime_without_trusted_selection_never_defaults_to_all(isolated_mcp, missing):
    """缺少运行身份容器或原始选择时不能把可见目录扩大成 all。"""
    context = isolated_mcp
    runtime = None if missing == "context" else SimpleNamespace(uid=context.reader.uid, run_id="independent-run")
    unavailable = {}
    before = len(context.requests)
    loaded = await service.resolve_runtime_mcp_tools(["knowledge"], uid=context.reader.uid, context=runtime, unavailable=unavailable)
    assert loaded.get("knowledge", []) == []
    assert "knowledge" in unavailable
    assert len(context.requests) == before, "缺失可信会话选择时仍向 Knowledge 发出了委托请求"


async def test_existing_delegated_tool_rejects_auth_mode_change(isolated_mcp):
    """代码认证不能在配置改成共享 headers 后继续签发委托。"""
    context = isolated_mcp
    tools = {tool.name: tool for tool in await runtime_tools(context)}
    await update_server(context, auth_mode="headers")
    before = len(context.requests)
    with pytest.raises(Exception) as rejected:
        await tools["list_kbs"].ainvoke({})
    assert any(isinstance(error, StateConflictError) for error in nested_errors(rejected.value))
    assert len(context.requests) == before


async def test_delegated_transport_does_not_send_credentials_to_environment_proxy(isolated_mcp, monkeypatch):
    """真实环境代理接收器必须收不到内部委托请求。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    received = []

    class ProxyReceiver(BaseHTTPRequestHandler):
        """只记录合成请求的存在，不记录任何凭据字节。"""

        def do_POST(self):
            """若 transport 错用环境代理则留下独立协议事实。"""
            received.append(bool(self.headers.get("Authorization")))
            self.send_response(502)
            self.end_headers()

        def log_message(self, *args):
            """禁止输出请求内容。"""

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyReceiver)
    thread = Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        for key in ["HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"]:
            monkeypatch.setenv(key, f"http://127.0.0.1:{proxy.server_port}")
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        context = isolated_mcp
        tools = {tool.name: tool for tool in await runtime_tools(context)}
        assert list_ids(await tools["list_kbs"].ainvoke({})) == ["delegation-kb"]
        assert received == [], "内部委托请求到达了环境代理"
    finally:
        await asyncio.to_thread(proxy.shutdown)
        proxy.server_close()
        thread.join()


async def test_real_mcp_adapter_recorder_and_reference_keep_source_identity(isolated_mcp, monkeypatch):
    """真实 MCP 协议和 adapter 转换不能丢失引用来源身份。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from yuxi.modules.agents.services.message_recorder import tool_event_output
    from yuxi.modules.agents.services.references import collect_reference_sources
    from yuxi.modules.knowledge.mcp.auth import create_knowledge_token
    from yuxi.modules.knowledge.services import tools as knowledge_tools

    async def query_kb(**kwargs):
        """仅替代向量供应商，协议、身份和引用消费保持真实。"""
        return {
            "kb_id": "delegation-kb",
            "results": [
                {
                    "id": "independent-chunk",
                    "file_id": "independent-file",
                    "content": "Independent source evidence",
                    "metadata": {"start_line": 10, "end_line": 12},
                }
            ],
        }

    monkeypatch.setattr(knowledge_tools, "query_kb", query_kb)
    context = isolated_mcp
    tools = {tool.name: tool for tool in await runtime_tools(context)}
    message = await tools["query_kb"].ainvoke(
        {
            "type": "tool_call",
            "id": "independent-reference",
            "name": "query_kb",
            "args": {"kb_id": "delegation-kb", "query_text": "synthetic"},
        }
    )
    token = create_knowledge_token(uid=context.reader.uid, selection=["delegation-kb"])
    async with (
        httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, trust_env=False) as http_client,
        streamable_http_client(context.base_url + "/api/mcp", http_client=http_client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        raw = await session.call_tool("query_kb", {"kb_id": "delegation-kb", "query_text": "synthetic"})
    assert raw.isError is False
    assert raw.structuredContent["results"][0]["file_id"] == "independent-file"
    assert message.artifact["structured_content"] == raw.structuredContent
    recorded = tool_event_output({"output": message})
    stored = SimpleNamespace(
        id=1,
        run_id="independent-run",
        extra_metadata={
            "tool_name": "query_kb",
            "input": {"kb_id": "delegation-kb"},
            "output": recorded,
        },
    )
    sources = collect_reference_sources([stored], {"delegation-kb"})
    assert [
        (source["kb_id"], source["file_id"], source["chunk_id"], source["start_line"], source["end_line"], source["content"])
        for source in sources
    ] == [("delegation-kb", "independent-file", "independent-chunk", 10, 12, "Independent source evidence")]
    assert collect_reference_sources([stored], set()) == []


async def test_product_selector_read_has_explicit_independent_agent_policy(isolated_mcp):
    """产品资源读取复用委托传输，管理员 Agent 工具开关不隐藏历史来源。"""
    from yuxi.modules.knowledge.mcp.client import list_knowledge_bases

    context = isolated_mcp
    await update_server(context, enabled=0, disabled_tools=["list_kbs"])
    rows = await list_knowledge_bases(context.reader.uid)
    assert [row["kb_id"] for row in rows] == ["delegation-kb"]
    assert await list_knowledge_bases(context.reader.uid, selection=[]) == []


@pytest.mark.parametrize("admin_enabled", [0, 1])
async def test_deployment_toggle_preserves_builtin_identity_and_admin_policy(isolated_mcp, monkeypatch, admin_enabled):
    """关闭部署只改变可用性，重开后保留管理员显式意图。"""
    context = isolated_mcp
    await update_server(context, enabled=admin_enabled, disabled_tools=["query_kb"])
    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", "false")
    await service.ensure_builtin_mcp_servers_in_db()
    async with context.sessions() as db:
        row = await db.scalar(select(MCPServer).where(MCPServer.slug == "knowledge"))
        assert service.is_builtin_mcp_server(row)
        assert row.auth_mode == "delegation"
        assert row.enabled == admin_enabled
        assert row.disabled_tools == ["query_kb"]
        assert "knowledge" not in await service.get_enabled_mcp_server_slugs(db=db)
        assert "knowledge" not in await service.available_mcp_servers(db, ["knowledge"], user_id=context.reader.id)
    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", "true")
    await service.ensure_builtin_mcp_servers_in_db()
    async with context.sessions() as db:
        row = await db.scalar(select(MCPServer).where(MCPServer.slug == "knowledge"))
        assert row.enabled == admin_enabled
        assert row.disabled_tools == ["query_kb"]
        assert ("knowledge" in await service.get_enabled_mcp_server_slugs(db=db)) is bool(admin_enabled)


async def test_dynamic_skill_restores_reenabled_tool_with_same_run_cache(isolated_mcp):
    """动态 Skill 每轮读取当前策略，使用同一完整目录缓存恢复工具。"""
    from langchain.agents.middleware.types import ModelRequest

    from yuxi.modules.agents.runtime.middlewares.skills import SkillsMiddleware

    context = isolated_mcp
    runtime = SimpleNamespace(
        uid=context.reader.uid,
        mcps=[],
        mcp_tools={},
        mcp_tool_servers={},
        mcp_unavailable={},
        knowledges=["delegation-kb"],
        run_id="independent-skill-run",
        skill_runtime_snapshot={
            "effective_skills": ["dynamic-kb"],
            "preloaded_skills": [],
            "runtime_skills": {
                "dynamic-kb": {
                    "name": "Dynamic knowledge",
                    "description": "Knowledge fixture",
                    "path": "/home/user/skills/dynamic-kb/SKILL.md",
                    "tools": [],
                    "mcps": ["knowledge"],
                    "skills": [],
                }
            },
        },
    )
    request = ModelRequest(
        model=SimpleNamespace(), messages=[], runtime=SimpleNamespace(context=runtime), tools=[], state={"activated_skills": ["dynamic-kb"]}
    )

    async def tools_for_model(current):
        """回读中间件真正交给模型的工具对象。"""
        return {tool.name: tool for tool in current.tools}

    middleware = SkillsMiddleware()
    await update_server(context, disabled_tools=["list_kbs"])
    first = await middleware.awrap_model_call(request, tools_for_model)
    assert "list_kbs" not in first
    await update_server(context, disabled_tools=[])
    second = await middleware.awrap_model_call(request, tools_for_model)
    assert "list_kbs" in second
    assert list_ids(await second["list_kbs"].ainvoke({})) == ["delegation-kb"]
    assert runtime.mcp_tool_servers["list_kbs"] == "knowledge"
