"""真实 API、MCP HTTP 协议及独立 PostgreSQL 验证管理连接检查。"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
from threading import Thread
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import uvicorn

from yuxi.agents.mcp import service
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import MCPServer, User
from server.routers.mcp_router import mcp
from server.utils.auth_middleware import get_admin_user, get_db

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    """此模块只使用独立数据库，不启动既有服务检查。"""


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """独立 schema 自行清理，不访问既有知识库。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """此模块不创建沙盒。"""
    yield


@pytest.mark.parametrize(
    "mode,enabled",
    [("empty", 0), ("empty", 1), ("tool", 0), ("error", 0), ("refused", 0), ("error", 1), ("refused", 1)],
)
async def test_management_connection_uses_real_protocol_and_preserves_state(monkeypatch, mode, enabled):
    """认证身份注入测试用户；路由、SQL读取和 MCP 适配器使用真实实现。"""
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url or os.getenv("TEST_ALLOW_MCP_INSPECTION_DB") != "1":
        pytest.skip("需要显式启用独立 MCP 检查测试数据库")
    assert make_url(database_url).database == "fixture", "仅允许隔离 fixture 数据库"
    schema = "pytest_mcp_inspect_" + uuid4().hex[:12]
    admin = create_async_engine(database_url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(database_url, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context():
        async with sessions.begin() as db:
            yield db

    # 路由与内部 Owner 的二次查询共用同一真实隔离 schema，原实现同样可运行。
    monkeypatch.setattr(pg_manager, "get_async_session_context", session_context)
    methods = []
    headers = []

    class ProtocolHandler(BaseHTTPRequestHandler):
        """无 session 的本地 Streamable HTTP MCP 协议对端。"""

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = request.get("method")
            methods.append(method)
            headers.append(self.headers.get("Authorization"))
            if mode == "error":
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"fixture-secret-response")
                return
            if "id" not in request:
                self.send_response(202)
                self.end_headers()
                return
            if method == "initialize":
                result = {
                    "protocolVersion": request["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            elif method == "tools/list":
                result = {
                    "tools": []
                    if mode == "empty"
                    else [
                        {
                            "name": "fixture_tool",
                            "description": "Fixture tool",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                }
            else:
                raise AssertionError(f"Unexpected MCP method: {method}")
            payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.send_response(405)
            self.end_headers()

        def log_message(self, *_):
            pass

    protocol = ThreadingHTTPServer(("127.0.0.1", 0), ProtocolHandler)
    protocol_thread = Thread(target=protocol.serve_forever, daemon=True)
    protocol_thread.start()
    refused = socket.socket()
    refused.bind(("127.0.0.1", 0))
    port = refused.getsockname()[1] if mode == "refused" else protocol.server_port
    app = FastAPI()
    app.include_router(mcp, prefix="/api")

    async def db_dependency():
        async with sessions() as db:
            yield db

    async def admin_dependency():
        return User(uid="fixture-admin", username="fixture-admin", password_hash="fixture", role="admin")

    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_admin_user] = admin_dependency
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    api_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    api = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    api_task = None
    cache, stats = deepcopy(service._mcp_tools_cache), deepcopy(service._mcp_tools_stats)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(MCPServer.__table__.create)
        async with sessions.begin() as db:
            db.add(
                MCPServer(
                    slug="fixture-inspect",
                    name="Fixture",
                    transport="streamable_http",
                    url=f"http://127.0.0.1:{port}/mcp",
                    enabled=enabled,
                    disabled_tools=["fixture_tool"],
                    headers={"Authorization": "Bearer fixture-header"},
                    timeout=1,
                    sse_read_timeout=1,
                    created_by="fixture-admin",
                    updated_by="fixture-admin",
                )
            )
        async with sessions() as db:
            row = await db.scalar(select(MCPServer).where(MCPServer.slug == "fixture-inspect"))
            before = {column.name: deepcopy(getattr(row, column.name)) for column in MCPServer.__table__.columns}
        api_task = asyncio.create_task(api.serve(sockets=[listener]))
        async with asyncio.timeout(10):
            while not api.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=api_url, timeout=15, trust_env=False) as client:
            response = await client.post("/api/system/mcp-servers/fixture-inspect/test")
        if mode in {"error", "refused"}:
            assert response.status_code == 502, response.text
            assert "fixture-secret-response" not in response.text
            assert "fixture-header" not in response.text
            assert response.json()["detail"] == "MCP 连接失败，请检查服务地址、凭据和网络后重试"
        else:
            assert response.status_code == 200, response.text
            assert response.json()["success"] is True
            assert response.json()["tool_count"] == (0 if mode == "empty" else 1)
            assert "initialize" in methods and "tools/list" in methods
            assert headers and all(value == "Bearer fixture-header" for value in headers)
        if mode == "error":
            assert "initialize" in methods
        async with sessions() as db:
            row = await db.scalar(select(MCPServer).where(MCPServer.slug == "fixture-inspect"))
            after = {column.name: getattr(row, column.name) for column in MCPServer.__table__.columns}
        assert after == before
        assert service._mcp_tools_cache == cache
        assert service._mcp_tools_stats == stats
    finally:
        api.should_exit = True
        if api_task is not None:
            await asyncio.wait_for(api_task, timeout=10)
        listener.close()
        refused.close()
        protocol.shutdown()
        protocol.server_close()
        protocol_thread.join(timeout=5)
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
