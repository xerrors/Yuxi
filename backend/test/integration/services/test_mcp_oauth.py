"""真实 HTTP 和 PostgreSQL 证明个人 MCP OAuth 的隔离与撤销。"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import uvicorn
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.api.dependencies.auth import get_db
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.extensions.mcp import mcp
from yuxi.modules.extensions.mcp.models import MCPConnection, MCPOAuthAttempt, MCPServer
from yuxi.modules.extensions.mcp.oauth_config import decrypt, encrypt
from yuxi.modules.extensions.mcp.oauth_service import MCPOAuthService
from yuxi.modules.extensions.mcp.service import get_enabled_mcp_tools
from yuxi.modules.identity.models import Department, RevokedAccessToken, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import DomainError, StateConflictError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def scenario(monkeypatch):
    """固定协议 oracle 校验 PKCE、resource 和刷新轮换，独立 schema 回读事实。"""
    schema = "pytest_mcp_oauth_" + uuid4().hex
    admin = create_async_engine(os.environ["POSTGRES_URL"])
    engine = create_async_engine(os.environ["POSTGRES_URL"], connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    root = f"http://127.0.0.1:{sock.getsockname()[1]}"
    issuer, resource = root + "/issuer", root + "/mcp"
    monkeypatch.setenv("YUXI_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MCP_OAUTH_LAB_HTTP_ORIGINS", root)
    monkeypatch.setenv("YUXI_PUBLIC_URL", root)
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(mcp, prefix="/api")
    codes, refreshes, identities = {}, {}, {}
    current = {
        "refresh_count": 0,
        "register_count": 0,
        "refresh_started": asyncio.Event(),
        "refresh_release": asyncio.Event(),
        "discovery_started": asyncio.Event(),
        "discovery_release": asyncio.Event(),
        "rejection_started": asyncio.Event(),
        "rejection_release": asyncio.Event(),
    }

    async def request_db():
        """HTTP 请求使用独立 session，复用真实 JWT 身份依赖。"""
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = request_db

    @app.get("/mcp")
    async def challenge():
        """公开资源挑战仅列出此 fixture 的受信授权服务器。"""
        return JSONResponse(
            {}, status_code=401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{root}/.well-known/oauth-protected-resource/mcp"'}
        )

    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def resource_metadata():
        """固定资源，拒绝客户端请求其他 audience。"""
        if current.get("block_discovery"):
            current["discovery_started"].set()
            await current["discovery_release"].wait()
        return {"resource": resource, "authorization_servers": [current.get("issuer", issuer)]}

    @app.get("/.well-known/oauth-authorization-server/issuer")
    async def metadata():
        """发布固定授权服务器，不依赖被测代码生成端点。"""
        return {
            "issuer": issuer,
            "authorization_endpoint": root + "/authorize" + current.get("authorization_query", ""),
            "token_endpoint": root + "/token",
            "registration_endpoint": root + "/register",
            "code_challenge_methods_supported": current.get("pkce", ["S256"]),
            "token_endpoint_auth_methods_supported": ["none"],
        }

    @app.post("/register")
    async def register(request: Request):
        """动态注册结果必须跨用户复用。"""
        data = await request.json()
        assert data["redirect_uris"] == [root + "/api/system/mcp-servers/oauth/callback"]
        current["register_count"] += 1
        return {"client_id": "fixture-client"}

    @app.post("/token")
    async def token(request: Request):
        """真实授权码校验与一次性刷新令牌轮换。"""
        form = await request.form()
        assert form["resource"] == resource and form["client_id"] == "fixture-client"
        if form["grant_type"] == "authorization_code":
            expected = codes.pop(form["code"])
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            assert challenge == expected["code_challenge"][0]
            assert form["redirect_uri"] == root + "/api/system/mcp-servers/oauth/callback"
            subject = expected["subject"]
        else:
            if current.get("invalid_grant"):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            subject = refreshes.pop(form["refresh_token"])
            current["refresh_count"] += 1
            current["refresh_started"].set()
            if current.get("block_refresh"):
                await current["refresh_release"].wait()
        access, refresh = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        refreshes[refresh], identities[access] = subject, subject
        response = {"access_token": access, "refresh_token": refresh, "token_type": "Bearer", "expires_in": 3600}
        return response

    @app.post("/mcp")
    async def mcp_protocol(request: Request):
        """协议结果包含 token 对应员工，用于观察缓存隔离。"""
        if current.get("error_status"):
            return JSONResponse({}, status_code=current["error_status"])
        authorization = request.headers.get(current.get("auth_header", "authorization"), "").removeprefix("Bearer ")
        current.setdefault("received_credentials", []).append(authorization)
        if authorization == current.get("delayed_reject_token"):
            current["rejection_started"].set()
            await current["rejection_release"].wait()
            return JSONResponse({}, status_code=401)
        if authorization not in identities or current.get("reject_access"):
            return JSONResponse({}, status_code=401)
        payload = await request.json()
        if "id" not in payload:
            return Response(status_code=202)
        method = payload["method"]
        previous_method = current.get("last_mcp_method")
        current["last_mcp_method"] = method
        if method == "initialize":
            result = {
                "protocolVersion": payload["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "tools/list":
            current["tool_list_count"] = current.get("tool_list_count", 0) + 1
            # SDK 在成功 tools/call 后读取目录校验结果；本用例串行执行协议会话。
            if previous_method != "tools/call":
                current["discovery_count"] = current.get("discovery_count", 0) + 1
            result = {
                "tools": [
                    {"name": name, "description": "查询本人", "inputSchema": {"type": "object", "properties": {}}}
                    for name in current.get("tool_names", ["my_attendance"])
                ]
            }
        elif method == "tools/call":
            assert payload["params"]["name"] in current.get("tool_names", ["my_attendance"])
            current["tool_call_count"] = current.get("tool_call_count", 0) + 1
            result = {"content": [{"type": "text", "text": identities[authorization]}], "isError": False}
        else:
            raise AssertionError(method)
        return {"jsonrpc": "2.0", "id": payload["id"], "result": result}

    sse_sessions = {}

    @app.get("/sse")
    async def sse_connection(request: Request):
        """SSE 使用独立队列回传协议消息，认证仍由每次请求提供。"""
        if current.get("error_status"):
            return JSONResponse({}, status_code=current["error_status"])
        credential = request.headers.get(current.get("auth_header", "authorization"), "").removeprefix("Bearer ")
        if credential not in identities:
            return JSONResponse({}, status_code=401)
        session = uuid4().hex
        queue = sse_sessions[session] = asyncio.Queue()

        async def events():
            """只发布同来源 endpoint，并在断开时释放队列。"""
            try:
                yield f"event: endpoint\ndata: {root}/sse/messages?session_id={session}\n\n"
                while True:
                    yield "event: message\ndata: " + await queue.get() + "\n\n"
            finally:
                sse_sessions.pop(session, None)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/sse/messages")
    async def sse_message(request: Request, session_id: str):
        """复用同一独立协议 oracle 验证 SSE POST 身份。"""
        result = await mcp_protocol(request)
        if isinstance(result, Response):
            return result
        await sse_sessions[session_id].put(json.dumps(result))
        return Response(status_code=202)

    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical", access_log=False))
    task = None
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            for model in (Department, User, RevokedAccessToken, MCPServer, MCPConnection, MCPOAuthAttempt):
                await conn.run_sync(lambda sync, model=model: model.__table__.create(sync))
        async with factory() as db:
            department = Department(name="fixture", description="fixture")
            db.add(department)
            await db.flush()
            users = [
                User(username=name, uid=name, password_hash="unused", role=role, department_id=department.id)
                for name, role in [("alice", "user"), ("bob", "user"), ("admin", "superadmin")]
            ]
            db.add_all(users)
            await db.flush()
            db.add(
                MCPServer(
                    slug="fixture",
                    name="Fixture",
                    transport="streamable_http",
                    url=resource,
                    auth_mode="oauth",
                    oauth_config={"issuer_url": issuer, "registration": "dynamic", "scopes": "default"},
                    created_by="admin",
                    updated_by="admin",
                )
            )
            await db.commit()
            headers = {
                user.uid: {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": str(user.id), "auth_method": "password"})}
                for user in users
            }
        from yuxi.infrastructure.postgres.manager import pg_manager

        @asynccontextmanager
        async def runtime_session():
            """运行时使用同一独立数据库，但每次出站重新开 session。"""
            async with factory() as db:
                yield db

        monkeypatch.setattr(pg_manager, "get_async_session_context", runtime_session)
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=root, follow_redirects=False, trust_env=False) as http:
            yield {
                "http": http,
                "db": factory,
                "schema": schema,
                "headers": headers,
                "codes": codes,
                "identities": identities,
                "current": current,
                "root": root,
                "users": users,
            }
    finally:
        if task:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
        sock.close()
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


async def begin(scenario, user="alice", *, subject=None):
    """授权入口产生挑战；独立 provider 随后核对。"""
    response = await scenario["http"].post("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"][user])
    assert response.status_code == 200, response.text
    params = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    assert params["code_challenge_method"] == ["S256"]
    code = secrets.token_urlsafe(24)
    scenario["codes"][code] = {**params, "subject": subject or user}
    return {"state": params["state"][0], "code": code}


async def finish(scenario, params, user="alice"):
    """真实 HTTP 回调和当前 JWT 确认组成完整个人授权。"""
    callback = await scenario["http"].get("/api/system/mcp-servers/oauth/callback", params=params)
    assert callback.status_code == 302
    query = parse_qs(urlsplit(callback.headers["location"]).query)
    if "mcp_oauth_error" in query:
        return query
    result = await scenario["http"].post(
        "/api/system/mcp-servers/oauth/complete", json={"result": query["mcp_oauth_result"][0]}, headers=scenario["headers"][user]
    )
    return result


async def test_personal_tools_use_own_identity_and_old_tool_stops_after_disconnect(scenario):
    """两个真实 token 返回不同员工，缓存不能串用且旧工具不能绕过断开。"""
    from yuxi.modules.agents.runtime.context import BaseContext
    from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools

    alice_context, bob_context = BaseContext(uid="alice"), BaseContext(uid="bob")
    assert alice_context.mcp_tools is not bob_context.mcp_tools
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    alice = (await resolve_runtime_mcp_tools(["fixture"], uid=alice_context.uid, run_tools=alice_context.mcp_tools))["fixture"][0]
    assert "alice" in str(await alice.ainvoke({}))
    assert (await finish(scenario, await begin(scenario, "bob"), "bob")).status_code == 200
    bob = (await resolve_runtime_mcp_tools(["fixture"], uid=bob_context.uid, run_tools=bob_context.mcp_tools))["fixture"][0]
    assert "bob" in str(await bob.ainvoke({}))
    assert "alice" in str(await alice.ainvoke({}))
    assert scenario["current"]["register_count"] == 1
    response = await scenario["http"].delete("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"]["alice"])
    assert response.status_code == 200
    with pytest.raises(Exception, match="连接|ExceptionGroup|TaskGroup"):
        await alice.ainvoke({})
    assert "bob" in str(await bob.ainvoke({}))
    async with scenario["db"]() as db:
        connection = await db.scalar(select(MCPConnection).where(MCPConnection.user_id == scenario["users"][0].id))
        assert connection.status == "disconnected" and connection.credentials is None


async def test_callback_replay_wrong_browser_and_cross_account_confirmation(scenario):
    """错误浏览器不消费 state，切换账号不能发布别人的授权。"""
    params = await begin(scenario)
    cookie = scenario["http"].cookies.get("yuxi_mcp_browser")
    scenario["http"].cookies.clear()
    failed = await finish(scenario, params)
    assert "mcp_oauth_error" in failed
    scenario["http"].cookies.set("yuxi_mcp_browser", cookie)
    callback = await scenario["http"].get("/api/system/mcp-servers/oauth/callback", params=params)
    result = parse_qs(urlsplit(callback.headers["location"]).query)["mcp_oauth_result"][0]
    assert "mcp_oauth_error" in await finish(scenario, params)
    wrong = await scenario["http"].post(
        "/api/system/mcp-servers/oauth/complete", headers=scenario["headers"]["bob"], json={"result": result}
    )
    assert wrong.status_code == 409
    right = await scenario["http"].post(
        "/api/system/mcp-servers/oauth/complete", headers=scenario["headers"]["alice"], json={"result": result}
    )
    assert right.status_code == 200
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection).where(MCPConnection.user_id == scenario["users"][0].id))
        assert "access_token" not in row.credentials and decrypt(row.credentials)["access_token"]


async def test_disconnect_invalidates_pending_callback_and_permissions_are_backend_owned(scenario):
    """已发起授权不能复活撤销，普通用户不能配置服务。"""
    params = await begin(scenario)
    await scenario["http"].delete("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"]["alice"])
    assert "mcp_oauth_error" in await finish(scenario, params)
    denied = await scenario["http"].put("/api/system/mcp-servers/fixture", headers=scenario["headers"]["alice"], json={"name": "stolen"})
    assert denied.status_code == 403
    listing = await scenario["http"].get("/api/system/mcp-servers", headers=scenario["headers"]["alice"])
    assert listing.status_code == 200
    assert listing.json()["data"][0]["connection_status"] == "disconnected"
    assert not ({"url", "headers", "oauth_config", "oauth_secret"} & set(listing.json()["data"][0]))


async def test_concurrent_refresh_rotates_once_and_disconnect_wins(scenario):
    """真实行锁令两个调用共享一次轮换，随后撤销不保留刷新结果。"""
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection))
        previous = decrypt(row.credentials)["refresh_token"]
        row.expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()

    async def access():
        """每个 worker 使用独立数据库事务。"""
        async with scenario["db"]() as db:
            return await MCPOAuthService(db).access_token("fixture", "alice")

    left, right = await asyncio.gather(access(), access())
    assert left == right and scenario["current"]["refresh_count"] == 1
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection))
        assert decrypt(row.credentials)["refresh_token"] != previous
        row.expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    scenario["current"]["refresh_started"].clear()
    scenario["current"]["block_refresh"] = True
    refreshing = asyncio.create_task(access())
    await asyncio.wait_for(scenario["current"]["refresh_started"].wait(), 5)
    disconnecting = asyncio.create_task(
        scenario["http"].delete("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"]["alice"])
    )
    await asyncio.sleep(0.05)
    assert not disconnecting.done()
    scenario["current"]["refresh_release"].set()
    await refreshing
    assert (await disconnecting).status_code == 200
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection))
        assert row.credentials is None and row.status == "disconnected"


async def test_invalid_refresh_and_changed_config_require_reconnect(scenario):
    """invalid_grant 形成持久状态，配置改变拒绝旧授权。"""
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection))
        row.expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    scenario["current"]["invalid_grant"] = True
    async with scenario["db"]() as db:
        with pytest.raises(StateConflictError):
            await MCPOAuthService(db).access_token("fixture", "alice")
    async with scenario["db"]() as db:
        assert (await db.scalar(select(MCPConnection))).status == "reconnect_required"
    scenario["current"]["invalid_grant"] = False
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer))
        server.url += "/changed"
        await db.commit()
    async with scenario["db"]() as db:
        with pytest.raises(StateConflictError):
            await MCPOAuthService(db).access_token("fixture", "alice")


@pytest.mark.parametrize("user", ["alice", "admin"])
async def test_platform_accounts_can_authorize_an_independent_resource_identity(scenario, user):
    """无企业绑定的本地用户可授权不同资源账号，连接仍仅属于当前平台用户。"""
    assert (await finish(scenario, await begin(scenario, user, subject="resource-employee"), user)).status_code == 200
    tool = (await get_enabled_mcp_tools("fixture", uid=user))[0]
    assert "resource-employee" in str(await tool.ainvoke({}))
    async with scenario["db"]() as db:
        rows = (await db.scalars(select(MCPConnection))).all()
        owner = next(item for item in scenario["users"] if item.uid == user)
        assert len(rows) == 1 and rows[0].user_id == owner.id
        assert rows[0].status == "connected"
    async with scenario["db"]() as db:
        with pytest.raises(StateConflictError, match="连接"):
            await MCPOAuthService(db).access_token("fixture", "bob")


@pytest.mark.parametrize("mutation", ["user_deleted", "server_disabled", "tool_disabled", "old_revision"])
async def test_existing_tool_rechecks_current_authorization_at_executor(scenario, mutation):
    """保留已装配工具后改变事实，旧对象不能绕过调用边界。"""
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    tool = (await get_enabled_mcp_tools("fixture", uid="alice"))[0]
    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer))
        if mutation == "user_deleted":
            user = await db.get(User, scenario["users"][0].id)
            user.is_deleted = 1
        elif mutation == "server_disabled":
            server.enabled = 0
        elif mutation == "tool_disabled":
            server.disabled_tools = ["my_attendance"]
        else:
            server.headers = {"X-Config-Changed": "yes"}
        await db.commit()
    # 检查失败原因来自授权边界；恢复其中任一 guard 都会返回 alice。
    with pytest.raises(Exception) as failure:
        await tool.ainvoke({})

    def messages(error):
        """展开异步 transport 的 ExceptionGroup，核对领域失败。"""
        return [str(error), *[text for child in getattr(error, "exceptions", []) for text in messages(child)]]

    expected = {"user_deleted": "用户不可用", "server_disabled": "已停用", "tool_disabled": "工具已停用", "old_revision": "配置已改变"}
    assert any(expected[mutation] in message for message in messages(failure.value))
    assert scenario["current"].get("tool_call_count", 0) == 0


async def test_expired_state_and_completed_result_after_disconnect_cannot_publish(scenario):
    """过期 state 与已兑换但尚未确认的结果都不能恢复授权。"""
    params = await begin(scenario)
    async with scenario["db"]() as db:
        attempt = await db.scalar(select(MCPOAuthAttempt))
        attempt.expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    assert "mcp_oauth_error" in await finish(scenario, params)
    params = await begin(scenario)
    callback = await scenario["http"].get("/api/system/mcp-servers/oauth/callback", params=params)
    result = parse_qs(urlsplit(callback.headers["location"]).query)["mcp_oauth_result"][0]
    await scenario["http"].delete("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"]["alice"])
    response = await scenario["http"].post(
        "/api/system/mcp-servers/oauth/complete", headers=scenario["headers"]["alice"], json={"result": result}
    )
    assert response.status_code == 409
    async with scenario["db"]() as db:
        assert (await db.scalar(select(MCPConnection))).credentials is None


@pytest.mark.parametrize("field,value", [("pkce", ["plain"]), ("issuer", "https://evil.example")])
async def test_discovery_rejects_untrusted_issuer_and_missing_pkce(scenario, field, value):
    """远端元数据不能替换超管信任根或取消 PKCE。"""
    scenario["current"][field] = value
    response = await scenario["http"].post("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"]["alice"])
    assert response.status_code == 502
    async with scenario["db"]() as db:
        assert await db.scalar(select(MCPOAuthAttempt)) is None


async def test_waiting_worker_rechecks_tool_revocation_after_refresh_lock(scenario):
    """等待刷新锁期间撤销工具，等待者不能使用锁前的旧策略。"""
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    from yuxi.modules.extensions.mcp.oauth_config import config_revision
    from yuxi.modules.extensions.mcp.oauth_runtime import PersonalMCPAuth

    async with scenario["db"]() as db:
        revision = config_revision(await db.scalar(select(MCPServer)))
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection))
        row.expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    scenario["current"]["block_refresh"] = True

    async def refresh():
        """首个 worker 持有连接锁，阻塞在真实 token HTTP 请求。"""
        async with scenario["db"]() as db:
            return await MCPOAuthService(db).access_token("fixture", "alice")

    refreshing = asyncio.create_task(refresh())
    await asyncio.wait_for(scenario["current"]["refresh_started"].wait(), 5)

    async def call_tool():
        """直接发送实际 tools/call，避免 SDK 的前置 initialize 掩盖等待竞态。"""
        auth = PersonalMCPAuth("fixture", "alice", revision)
        async with httpx.AsyncClient(auth=auth, trust_env=False) as http:
            return await http.post(
                scenario["root"] + "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "my_attendance", "arguments": {}},
                },
            )

    waiting = asyncio.create_task(call_tool())
    await asyncio.sleep(0.1)
    assert not waiting.done()
    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer))
        server.disabled_tools = ["my_attendance"]
        await db.commit()
    scenario["current"]["refresh_release"].set()
    await refreshing
    with pytest.raises(Exception):
        await waiting
    assert scenario["current"].get("tool_call_count", 0) == 0


async def test_remote_revocation_marks_reconnect_without_restoring_shared_credentials(scenario):
    """远端拒绝 access token 后，页面状态持久变成需要重连。"""
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    tool = (await get_enabled_mcp_tools("fixture", uid="alice"))[0]
    scenario["current"]["reject_access"] = True
    with pytest.raises(Exception):
        await tool.ainvoke({})
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection))
        assert row.status == "reconnect_required" and row.credentials is None


async def test_disconnect_cancels_connect_already_waiting_for_discovery(scenario):
    """connect 在网络之前固定世代，断开后旧 discovery 不能再发布授权事务。"""
    scenario["current"]["block_discovery"] = True
    pending = asyncio.create_task(scenario["http"].post("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"]["alice"]))
    await asyncio.wait_for(scenario["current"]["discovery_started"].wait(), 5)
    response = await scenario["http"].delete("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"]["alice"])
    assert response.status_code == 200
    scenario["current"]["discovery_release"].set()
    assert (await pending).status_code == 409
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection))
        assert row.status == "disconnected" and row.credentials is None
        assert await db.scalar(select(MCPOAuthAttempt)) is None


async def test_old_401_cannot_clear_a_newly_connected_token(scenario):
    """旧 HTTP 请求晚到的 401 不得撤销已完成的新授权。"""
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    tool = (await get_enabled_mcp_tools("fixture", uid="alice"))[0]
    async with scenario["db"]() as db:
        old = decrypt((await db.scalar(select(MCPConnection))).credentials)["access_token"]
    scenario["current"]["delayed_reject_token"] = old
    pending = asyncio.create_task(tool.ainvoke({}))
    await asyncio.wait_for(scenario["current"]["rejection_started"].wait(), 5)
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    scenario["current"]["rejection_release"].set()
    with pytest.raises(Exception):
        await pending
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection))
        assert row.status == "connected" and decrypt(row.credentials)["access_token"] != old
    assert "alice" in str(await tool.ainvoke({}))


async def test_expired_attempt_cleanup_skips_a_result_locked_by_completion(scenario):
    """完成请求持有结果锁时，新连接不得等待它而形成反向连接锁死锁。"""
    await begin(scenario)
    async with scenario["db"]() as db:
        attempt = await db.scalar(select(MCPOAuthAttempt))
        state_hash = attempt.state_hash
        attempt.expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    async with scenario["db"]() as completing:
        await completing.scalar(select(MCPOAuthAttempt).where(MCPOAuthAttempt.state_hash == state_hash).with_for_update())
        # 锁拥有者在新连接结束后才释放；旧全局 DELETE 会阻塞在此行。
        await asyncio.wait_for(begin(scenario), timeout=3)
        async with scenario["db"]() as db:
            assert await db.get(MCPOAuthAttempt, state_hash) is not None
        await completing.rollback()
    await begin(scenario)
    async with scenario["db"]() as db:
        assert await db.get(MCPOAuthAttempt, state_hash) is None


async def test_worker_cleans_expired_oauth_attempts_without_new_authorization(scenario):
    """正式 cron 在启动及后续周期清理两阶段过期载荷，跳过锁定行并保留长期连接。"""
    from yuxi.infrastructure.redis import create_arq_redis_pool
    from yuxi.workers.settings import WorkerSettings

    jobs = [job for job in WorkerSettings.cron_jobs if job.coroutine.__name__ == "cleanup_expired_mcp_oauth_attempts"]
    assert len(jobs) == 1, "正式 worker 必须注册 OAuth 临时记录清理"
    assert jobs[0].run_at_startup and jobs[0].second == 0 and jobs[0].minute is None
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    attempts = {
        name: hashlib.sha256(name.encode()).hexdigest()
        for name in ("expired_authorization", "expired_complete", "live_authorization", "live_complete", "locked_complete")
    }
    async with scenario["db"]() as db:
        connection = await db.scalar(select(MCPConnection))
        original_connection = (
            connection.credentials,
            connection.status,
            connection.generation,
            connection.config_revision,
            connection.expires_at,
        )
        for name, state_hash in attempts.items():
            payload = (
                {"phase": "complete", "tokens": {"access_token": "pending-fixture"}}
                if name.endswith("complete")
                else {"verifier": "fixture"}
            )
            db.add(
                MCPOAuthAttempt(
                    state_hash=state_hash,
                    browser_hash="a" * 64,
                    connection_id=connection.id,
                    generation=connection.generation,
                    config_revision=connection.config_revision,
                    payload=encrypt(payload),
                    expires_at=utc_now() + timedelta(minutes=10) if name.startswith("live_") else utc_now() - timedelta(seconds=1),
                )
            )
        await db.commit()

    async def remaining_attempts():
        """独立事务回读已提交记录，避免把 worker 日志当作清理结果。"""
        async with scenario["db"]() as observer:
            return set((await observer.scalars(select(MCPOAuthAttempt.state_hash))).all())

    queue = "pytest_mcp_cleanup_" + uuid4().hex
    redis = await create_arq_redis_pool()
    process = None
    live = {attempts["live_authorization"], attempts["live_complete"]}
    try:
        async with scenario["db"]() as completing:
            await completing.scalar(
                select(MCPOAuthAttempt).where(MCPOAuthAttempt.state_hash == attempts["locked_complete"]).with_for_update()
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _OAUTH_CLEANUP_WORKER_SCRIPT,
                env={**os.environ, "YUXI_TEST_SCHEMA": scenario["schema"], "YUXI_TEST_QUEUE": queue},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async with asyncio.timeout(15):
                while {attempts["expired_authorization"], attempts["expired_complete"]} & await remaining_attempts():
                    await asyncio.sleep(0.05)
            assert await remaining_attempts() == live | {attempts["locked_complete"]}
            await completing.rollback()

        # 保留正式每分钟调度；后续周期必须回收启动时因行锁跳过的记录。
        async with asyncio.timeout(70):
            while attempts["locked_complete"] in await remaining_attempts():
                await asyncio.sleep(0.1)
        assert await remaining_attempts() == live
        async with scenario["db"]() as observer:
            connection = await observer.scalar(select(MCPConnection))
            assert (
                connection.credentials,
                connection.status,
                connection.generation,
                connection.config_revision,
                connection.expires_at,
            ) == original_connection
    finally:
        if process is not None:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.communicate(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.communicate()
        keys = [key async for key in redis.scan_iter(match=f"*{queue}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


async def test_runtime_availability_is_personal_and_disconnected_does_not_load_tools(scenario):
    """他人的授权不使本人可用；断开不再阻止普通工具装配。"""
    from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools

    loaded = await resolve_runtime_mcp_tools(["fixture", "fixture"], uid="bob")
    assert loaded == {}
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    loaded = await resolve_runtime_mcp_tools(["fixture"], uid="bob")
    assert loaded == {}
    loaded = await resolve_runtime_mcp_tools(["fixture"], uid="alice")
    assert len(loaded["fixture"]) == 1
    await scenario["http"].delete("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"]["alice"])
    loaded = await resolve_runtime_mcp_tools(["fixture"], uid="alice")
    assert loaded == {}
    assert scenario["current"].get("tool_call_count", 0) == 0


async def test_runtime_omits_missing_and_disabled_services(scenario):
    """已连接也不能绕过服务停用，不存在的服务同样不进入运行工具。"""
    from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools

    assert (await finish(scenario, await begin(scenario))).status_code == 200
    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer).where(MCPServer.slug == "fixture"))
        server.enabled = 0
        await db.commit()
    assert await resolve_runtime_mcp_tools(["missing", "fixture"], uid="alice") == {}
    assert scenario["current"].get("tool_call_count", 0) == 0


@pytest.mark.parametrize("grouped", [False, True])
async def test_runtime_reports_unknown_loading_failure_without_exposing_tools(scenario, monkeypatch, grouped):
    """发现故障不暴露工具，临时故障可重试，明确失效的凭据保留安全提示。"""
    from yuxi.modules.extensions.mcp import service

    assert (await finish(scenario, await begin(scenario))).status_code == 200
    failure = RuntimeError("PRIVATE_PROTOCOL_FAILURE")
    if grouped:
        failure = ExceptionGroup("mixed", [StateConflictError("reconnect", code="mcp_reconnect_required"), failure])

    async def fail(*args, **kwargs):
        """模拟真实装配边界的未知协议故障。"""
        raise failure

    original = service.personal_mcp_tools
    monkeypatch.setattr(service, "personal_mcp_tools", fail)
    unavailable, run_tools = {}, {}
    assert await service.resolve_runtime_mcp_tools(["fixture"], uid="alice", run_tools=run_tools, unavailable=unavailable) == {}
    expected = "服务连接或调用失败，请检查连接器后重试"
    if grouped:
        expected = "授权凭据不可用或已失效，请在扩展页面重新连接；" + expected
    assert unavailable == ({"fixture": expected} if grouped else {})
    assert run_tools == {}
    assert scenario["current"].get("tool_call_count", 0) == 0
    if not grouped:
        monkeypatch.setattr(service, "personal_mcp_tools", original)
        recovered = await service.resolve_runtime_mcp_tools(["fixture"], uid="alice", run_tools=run_tools, unavailable=unavailable)
        assert [tool.name for tool in recovered["fixture"]] == ["my_attendance"]
        assert unavailable == {}


async def test_runtime_invalid_refresh_excludes_service_and_preserves_reconnect_state(scenario):
    """装配中远端拒绝刷新时保留重连状态，不把异常组变成普通聊天失败。"""
    from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools

    assert (await finish(scenario, await begin(scenario))).status_code == 200
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection))
        row.expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    scenario["current"]["invalid_grant"] = True
    loaded = await resolve_runtime_mcp_tools(["fixture"], uid="alice")
    assert loaded == {}
    async with scenario["db"]() as db:
        row = await db.scalar(select(MCPConnection))
        assert row.status == "reconnect_required" and row.credentials is None


async def test_normal_access_uses_three_reads_without_remote_refresh(scenario):
    """真实 SQL 证明普通路径只有策略、连接锁和锁后策略三次读取。"""
    from sqlalchemy import event

    assert (await finish(scenario, await begin(scenario))).status_code == 200
    async with scenario["db"]() as db:
        selects = []
        engine = db.bind.sync_engine

        def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
            """只记录 SQL 类型，不收集参数或凭据。"""
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(engine, "before_cursor_execute", observe)
        try:
            assert await MCPOAuthService(db).access_token("fixture", "alice")
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert len(selects) == 3
        assert sum("FOR UPDATE" in sql for sql in selects) == 1
    assert scenario["current"]["refresh_count"] == 0


@pytest.mark.parametrize("phase", ["connected", "callback", "complete"])
async def test_config_roundtrip_cannot_restore_credentials_or_pending_authorization(scenario, phase):
    """配置 A→B→A 不恢复旧凭据、授权码事务或待确认结果。"""
    params = await begin(scenario)
    result = None
    if phase == "connected":
        assert (await finish(scenario, params)).status_code == 200
    elif phase == "complete":
        callback = await scenario["http"].get("/api/system/mcp-servers/oauth/callback", params=params)
        result = parse_qs(urlsplit(callback.headers["location"]).query)["mcp_oauth_result"][0]
    for headers in ({"X-Changed": "yes"}, {}):
        response = await scenario["http"].put(
            "/api/system/mcp-servers/fixture", headers=scenario["headers"]["admin"], json={"headers": headers}
        )
        assert response.status_code == 200
    if phase == "connected":
        async with scenario["db"]() as db:
            with pytest.raises(StateConflictError, match="连接"):
                await MCPOAuthService(db).access_token("fixture", "alice")
    elif phase == "callback":
        assert "mcp_oauth_error" in await finish(scenario, params)
    else:
        response = await scenario["http"].post(
            "/api/system/mcp-servers/oauth/complete", headers=scenario["headers"]["alice"], json={"result": result}
        )
        assert response.status_code == 409
    async with scenario["db"]() as db:
        assert (await db.scalar(select(MCPConnection))).credentials is None


async def test_authorization_endpoint_preserves_existing_query(scenario):
    """授权端点固定参数与 OAuth/PKCE 参数分别存在，不能拼成两个问号。"""
    scenario["current"]["authorization_query"] = "?tenant=fixture"
    params = await begin(scenario)
    query = next(iter(scenario["codes"].values()))
    assert query["tenant"] == ["fixture"]
    assert query["response_type"] == ["code"] and query["state"] == [params["state"]]
    assert (await finish(scenario, params)).status_code == 200


async def test_disconnect_blocks_skill_local_side_effect_at_execution_boundary(scenario, monkeypatch, tmp_path):
    """模型已选中本地工具后撤销 MCP，真正执行处仍不允许产生文件副作用。"""
    from types import SimpleNamespace
    from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
    from yuxi.modules.agents.runtime.middlewares import authorization

    state = await begin(scenario, "alice")
    assert (await finish(scenario, state, "alice")).status_code == 200
    context = ChatBotContext(
        uid="alice",
        run_id=None,
        thread_id="fixture",
        mcps=[],
        tools=[],
        registered_builtin_tool_names={"write_report"},
        middleware_tool_names=set(),
        mcp_tool_servers={},
        _capability_limited=False,
        skill_runtime_snapshot={
            "skill_metadata": {},
            "effective_skills": ["report"],
            "preloaded_skills": ["report"],
            "runtime_skills": {"report": {"tools": ["write_report"], "mcps": ["fixture"], "skills": []}},
        },
    )

    async def actor(_context, *, db):
        """独立 schema 只省略 Agent/Run 外壳，连接与身份仍取真实 PostgreSQL。"""
        user = await db.scalar(select(User).where(User.uid == _context.uid))
        return user, None, None

    async def retain_snapshot(*args, **kwargs):
        """保留模型已准备好的 Skill 快照，不访问本用例未构建的投影。"""

    monkeypatch.setattr(authorization, "_require_execution_actor", actor)
    monkeypatch.setattr(authorization, "refresh_agent_context_resources", retain_snapshot)
    monkeypatch.setattr(authorization, "sync_agent_context_skills", retain_snapshot)
    mcp_policy = await authorization.refresh_execution_authorization(context)
    assert authorization.tool_allowed(context, "write_report", {}, mcp_policy)
    response = await scenario["http"].delete("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"]["alice"])
    assert response.status_code == 200
    output = tmp_path / "report.txt"

    async def execute(_request):
        """只有真正到达工具处理器才写入产物。"""
        output.write_text("must not be written")

    request = SimpleNamespace(runtime=SimpleNamespace(context=context), state={}, tool_call={"name": "write_report", "id": "call"})
    result = await authorization.RuntimeAuthorizationMiddleware().awrap_tool_call(request, execute)
    assert result.status == "error"
    assert not output.exists()
    assert "fixture" not in await authorization.refresh_execution_authorization(context)


async def test_shared_tool_cannot_survive_switch_to_personal_oauth(scenario):
    """旧共享工具在用户重连前后均不能将共享凭据发送给已切换 OAuth 的服务。"""
    assert (await finish(scenario, await begin(scenario))).status_code == 200
    http, headers = scenario["http"], scenario["headers"]["admin"]
    async with scenario["db"]() as db:
        connection = await db.scalar(select(MCPConnection))
        shared_token = decrypt(connection.credentials)["access_token"]
    response = await http.put(
        "/api/system/mcp-servers/fixture",
        headers=headers,
        json={
            "auth_mode": "headers",
            "headers": {"Authorization": "Bearer " + shared_token},
        },
    )
    assert response.status_code == 200
    old = (await get_enabled_mcp_tools("fixture", uid="alice"))[0]
    assert "alice" in str(await old.ainvoke({}))
    response = await http.put(
        "/api/system/mcp-servers/fixture",
        headers=headers,
        json={
            "auth_mode": "oauth",
            "headers": {},
            "oauth_config": {"issuer_url": scenario["root"] + "/issuer", "registration": "dynamic", "scopes": "default"},
        },
    )
    assert response.status_code == 200
    calls = scenario["current"]["tool_call_count"]
    for reconnect in [False, True]:
        if reconnect:
            assert (await finish(scenario, await begin(scenario))).status_code == 200
        with pytest.raises((StateConflictError, ExceptionGroup)):
            await old.ainvoke({})
        assert scenario["current"]["tool_call_count"] == calls
    personal = (await get_enabled_mcp_tools("fixture", uid="alice"))[0]
    assert "alice" in str(await personal.ainvoke({}))


@pytest.mark.parametrize("auth_mode", ["headers", "oauth"])
async def test_management_reads_current_tools_without_refresh(scenario, auth_mode):
    """普通读取取得最新目录与开关，新运行无需管理刷新即可发现远端变化。"""
    assert (await finish(scenario, await begin(scenario, "admin"), "admin")).status_code == 200
    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer).where(MCPServer.slug == "fixture"))
        if auth_mode == "headers":
            connection = await db.scalar(select(MCPConnection).where(MCPConnection.user_id == scenario["users"][2].id))
            server.auth_mode = "headers"
            server.headers = {"Authorization": "Bearer " + decrypt(connection.credentials)["access_token"]}
        server.disabled_tools = ["my_attendance", "removed_tool"]
        await db.commit()
    path = "/api/system/mcp-servers/fixture/tools"
    response = await scenario["http"].get(path, headers=scenario["headers"]["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [(tool["name"], tool["enabled"]) for tool in response.json()["data"]] == [("my_attendance", False)]

    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer).where(MCPServer.slug == "fixture"))
        server.disabled_tools = ["removed_tool"]
        await db.commit()
    response = await scenario["http"].get(path, headers=scenario["headers"]["admin"])
    assert response.status_code == 200, response.text
    assert [(tool["name"], tool["enabled"]) for tool in response.json()["data"]] == [("my_attendance", True)]
    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer).where(MCPServer.slug == "fixture"))
        assert server.disabled_tools == ["removed_tool"]

    from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools

    scenario["current"]["tool_names"] = ["old_attendance"]
    loaded = await resolve_runtime_mcp_tools(["fixture"], uid="admin")
    assert [tool.name for tool in loaded["fixture"]] == ["old_attendance"]
    scenario["current"]["tool_names"] = ["new_attendance"]
    tools = await resolve_runtime_mcp_tools(["fixture"], uid="admin")
    assert [tool.name for tool in tools["fixture"]] == ["new_attendance"]
    response = await scenario["http"].get(path, headers=scenario["headers"]["admin"])
    assert response.status_code == 200, response.text
    assert [tool["name"] for tool in response.json()["data"]] == ["new_attendance"]


@pytest.mark.parametrize("change", ["disabled", "disconnected", "tool_disabled"])
async def test_availability_rereads_current_policy_when_session_already_loaded_rows(scenario, change):
    """复用查询 session 时不能把 ORM 已加载对象当作最新授权事实。"""
    from yuxi.modules.extensions.mcp.service import available_mcp_servers

    assert (await finish(scenario, await begin(scenario))).status_code == 200
    async with scenario["db"]() as reading:
        assert "fixture" in await available_mcp_servers(reading, ["fixture"], user_id=scenario["users"][0].id)
        # 显式保留 ORM 对象，独立事务修改后必须以数据库回读结果为准。
        server = await reading.scalar(select(MCPServer).where(MCPServer.slug == "fixture"))
        connection = await reading.scalar(select(MCPConnection).where(MCPConnection.user_id == scenario["users"][0].id))
        async with scenario["db"]() as changing:
            if change == "disconnected":
                current = await changing.get(MCPConnection, connection.id)
                current.status, current.credentials = "disconnected", None
            else:
                current = await changing.get(MCPServer, server.id)
                if change == "disabled":
                    current.enabled = 0
                else:
                    current.disabled_tools = ["my_attendance"]
            await changing.commit()
        available = await available_mcp_servers(reading, ["fixture"], user_id=scenario["users"][0].id)
        if change == "tool_disabled":
            assert available["fixture"].disabled_tools == ["my_attendance"]
        else:
            assert available == {}


@pytest.mark.parametrize("all_tools", [False, True])
async def test_personal_discovery_reads_server_configuration_once(scenario, all_tools):
    """单服务入口把服务配置直接交给个人适配器，实际出站仍独立查授权。"""
    from sqlalchemy import event
    from yuxi.modules.extensions.mcp.service import get_all_mcp_tools, get_enabled_mcp_tools

    assert (await finish(scenario, await begin(scenario))).status_code == 200
    async with scenario["db"]() as db:
        engine = db.bind.sync_engine
    catalog_reads = []

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        """实际 SQL 区分目录读取与每次出站的用户授权查询。"""
        if "from mcp_servers" in statement.lower():
            catalog_reads.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        loader = get_all_mcp_tools if all_tools else get_enabled_mcp_tools
        tools = await loader("fixture", uid="alice")
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert [tool.name for tool in tools] == ["my_attendance"]
    assert len(catalog_reads) == 1


@pytest.mark.parametrize("auth_mode", ["headers", "oauth"])
@pytest.mark.parametrize("via_skill", [False, True])
async def test_each_run_discovers_once_across_model_rounds(scenario, monkeypatch, auth_mode, via_skill):
    """两次真实图运行各三轮模型，每 Run 一次目录发现，工具调用仍各自检查授权。"""
    from deepagents.backends import CompositeBackend, StateBackend
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from pydantic import Field
    from yuxi.modules.agents.runtime.agent_backends.chatbot import graph as chatbot_graph
    from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
    from yuxi.modules.agents.runtime.middlewares import authorization
    from yuxi.modules.extensions.tools.runtime import resolve_configured_runtime_tools

    assert (await finish(scenario, await begin(scenario))).status_code == 200
    if auth_mode == "headers":
        async with scenario["db"]() as db:
            server = await db.scalar(select(MCPServer).where(MCPServer.slug == "fixture"))
            connection = await db.scalar(select(MCPConnection).where(MCPConnection.user_id == scenario["users"][0].id))
            server.auth_mode = "headers"
            server.headers = {"Authorization": "Bearer " + decrypt(connection.credentials)["access_token"]}
            await db.commit()

    async def actor(context, *, db):
        """省略此 fixture 未建的 Agent/Run 外壳，保留真实数据库用户与全部 MCP 授权。"""
        return await db.scalar(select(User).where(User.uid == context.uid, User.is_deleted == 0)), None, None

    async def retain_snapshot(*args, **kwargs):
        """图使用明确的 Skill 快照，不访问 fixture 未创建的来源文件。"""

    monkeypatch.setattr(authorization, "_require_execution_actor", actor)
    monkeypatch.setattr(authorization, "refresh_agent_context_resources", retain_snapshot)
    monkeypatch.setattr(authorization, "sync_agent_context_skills", retain_snapshot)

    class ToolLoopModel(BaseChatModel):
        """实际模型绑定工具后调用两次远端工具，再返回终答。"""

        expected_name: str
        rounds: int = 0
        bound_tools: list = Field(default_factory=list)

        @property
        def _llm_type(self):
            """返回确定性协议模型类型。"""
            return "mcp-discovery-frequency"

        def bind_tools(self, tools, **kwargs):
            """记录真实图最终提供的工具对象。"""
            self.bound_tools = list(tools)
            return self

        def _generate(self, *args, **kwargs):
            """本用例只运行异步生产图。"""
            raise NotImplementedError

        async def _agenerate(self, messages, **kwargs):
            """从实际可见工具调用，保留工具结果供最终断言。"""
            assert self.expected_name in {tool.name for tool in self.bound_tools}
            self.rounds += 1
            if self.rounds <= 2:
                message = AIMessage(
                    content="", tool_calls=[{"name": self.expected_name, "args": {}, "id": f"call-{self.rounds}", "type": "tool_call"}]
                )
            else:
                message = AIMessage(content="CHAT_OK")
            return ChatResult(generations=[ChatGeneration(message=message)])

    for run_index, tool_name in enumerate(["my_attendance", "updated_attendance"], start=1):
        scenario["current"]["tool_names"] = [tool_name]
        context = ChatBotContext(
            uid="alice", thread_id="fixture", workdir_relative_path="projects/fixture", tools=[], mcps=[] if via_skill else ["fixture"]
        )
        context.skill_runtime_snapshot = {
            "skill_metadata": {},
            "effective_skills": ["report"] if via_skill else [],
            "preloaded_skills": ["report"] if via_skill else [],
            "preloaded_skill_contents": {"report": "# Report"},
            "runtime_skills": {"report": {"tools": [], "mcps": ["fixture"], "skills": []}},
        }
        tools = await resolve_configured_runtime_tools(context)
        model = ToolLoopModel(expected_name=tool_name)
        middlewares = await chatbot_graph._build_middlewares(
            context, CompositeBackend(default=StateBackend, routes={}), cleanup_model=model
        )
        graph = create_agent(model=model, tools=tools, middleware=middlewares)
        result = await graph.ainvoke({"messages": [HumanMessage(content="query")]}, context=context)
        assert result["messages"][-1].content == "CHAT_OK" and model.rounds == 3
        tool_results = [message for message in result["messages"] if isinstance(message, ToolMessage)]
        assert len(tool_results) == 2 and all("alice" in str(message.content) and message.status == "success" for message in tool_results)
        assert scenario["current"]["discovery_count"] == run_index
        assert scenario["current"]["tool_list_count"] == run_index * 3
        assert scenario["current"]["tool_call_count"] == run_index * 2


async def test_run_directory_reuses_empty_results_and_rechecks_current_tool_policy(scenario):
    """空目录仅发现一次，新运行重新发现；工具开关变化只过滤目录，不重做发现。"""
    from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools

    assert (await finish(scenario, await begin(scenario))).status_code == 200
    scenario["current"]["tool_names"] = []
    run_tools = {}
    for _ in range(3):
        assert await resolve_runtime_mcp_tools(["fixture"], uid="alice", run_tools=run_tools) == {"fixture": []}
    assert scenario["current"]["tool_list_count"] == 1
    scenario["current"]["tool_names"] = ["my_attendance"]
    next_run_tools = {}
    loaded = await resolve_runtime_mcp_tools(["fixture"], uid="alice", run_tools=next_run_tools)
    assert [tool.name for tool in loaded["fixture"]] == ["my_attendance"]
    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer).where(MCPServer.slug == "fixture"))
        server.disabled_tools = ["my_attendance"]
        await db.commit()
    assert await resolve_runtime_mcp_tools(["fixture"], uid="alice", run_tools=next_run_tools) == {"fixture": []}
    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer).where(MCPServer.slug == "fixture"))
        server.disabled_tools = []
        await db.commit()
    enabled = await resolve_runtime_mcp_tools(["fixture"], uid="alice", run_tools=next_run_tools)
    assert enabled["fixture"][0] is loaded["fixture"][0]
    assert scenario["current"]["tool_list_count"] == 2
    response = await scenario["http"].delete("/api/system/mcp-servers/fixture/connection", headers=scenario["headers"]["alice"])
    assert response.status_code == 200
    assert await resolve_runtime_mcp_tools(["fixture"], uid="alice", run_tools=next_run_tools) == {}
    with pytest.raises(Exception, match="连接|ExceptionGroup|TaskGroup"):
        await loaded["fixture"][0].ainvoke({})
    assert scenario["current"].get("tool_call_count", 0) == 0


_OAUTH_CLEANUP_WORKER_SCRIPT = """
import asyncio
import os
from dataclasses import replace
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.workers.arq import YuxiWorker
from yuxi.workers.settings import WorkerSettings

async def startup(ctx):
    "仅将真实数据库连接限定到测试 Schema。"
    pg_manager.async_engine = create_async_engine(
        os.environ["POSTGRES_URL"],
        connect_args={"server_settings": {"search_path": os.environ["YUXI_TEST_SCHEMA"]}},
    )
    pg_manager.AsyncSession = async_sessionmaker(pg_manager.async_engine, expire_on_commit=False)
    pg_manager._initialized = True

async def shutdown(ctx):
    "释放独立 worker 数据库连接。"
    await pg_manager.close()

async def main():
    "复用正式 cron 和调度周期，只隔离 Redis 名称与启动依赖。"
    queue = os.environ["YUXI_TEST_QUEUE"]
    job = next(job for job in WorkerSettings.cron_jobs if job.coroutine.__name__ == "cleanup_expired_mcp_oauth_attempts")
    worker = YuxiWorker(
        functions=[],
        cron_jobs=[replace(job, name=queue + ":cleanup", next_run=None)],
        redis_settings=WorkerSettings.redis_settings,
        queue_name=queue,
        health_check_key=queue + ":health",
        max_jobs=1,
        poll_delay=0.05,
        on_startup=startup,
        on_shutdown=shutdown,
    )
    try:
        await worker.async_run()
    finally:
        await worker.close()

asyncio.run(main())
"""


@pytest.mark.parametrize("mode,transport", [("bearer", "streamable_http"), ("api_key", "streamable_http"), ("api_key", "sse")])
async def test_personal_static_credentials_use_own_identity_and_revoke_old_tools(scenario, mode, transport):
    """真实接口、密文和工具返回共同证明个人静态凭证的隔离与撤销。"""
    http, headers, current = scenario["http"], scenario["headers"], scenario["current"]
    base = "/api/system/mcp-servers"
    current["auth_header"] = "x-api-key" if mode == "api_key" else "authorization"
    response = await http.put(
        base + "/fixture",
        headers=headers["admin"],
        json={
            "auth_mode": mode,
            "api_key_header": "X-API-Key" if mode == "api_key" else None,
            "headers": {},
            "transport": transport,
            "url": scenario["root"] + ("/sse" if transport == "sse" else "/mcp"),
        },
    )
    assert response.status_code == 200, response.text
    revision = response.json()["data"]["config_revision"]
    scenario["identities"].update({"alice-key": "alice", "bob-key": "bob", "alice-new-key": "alice-new"})
    for user in ("alice", "bob"):
        response = await http.put(
            base + "/fixture/connection",
            headers=headers[user],
            json={
                "secret": user + "-key",
                "config_revision": revision,
            },
        )
        assert response.json() == {"success": True, "connection_status": "connected"}
    async with scenario["db"]() as db:
        connections = (await db.scalars(select(MCPConnection).order_by(MCPConnection.user_id))).all()
        assert len(connections) == 2
        assert {decrypt(row.credentials)["secret"] for row in connections} == {"alice-key", "bob-key"}
        assert all("alice-key" not in row.credentials and "bob-key" not in row.credentials for row in connections)
        alice_generation = next(row.generation for row in connections if row.user_id == scenario["users"][0].id)
    listing = await http.get(base, headers=headers["alice"])
    assert listing.json()["data"][0]["config_revision"] == revision
    assert "alice-key" not in listing.text and "bob-key" not in listing.text
    received = len(current.get("received_credentials", []))
    assert (await http.post(base + "/fixture/test", headers=headers["admin"])).status_code == 502
    assert len(current.get("received_credentials", [])) == received
    assert (await http.post(base + "/fixture/test", headers=headers["alice"])).status_code == 200
    alice_tools = await get_enabled_mcp_tools("fixture", uid="alice")
    bob_tools = await get_enabled_mcp_tools("fixture", uid="bob")
    assert "alice" in str(await alice_tools[0].ainvoke({}))
    assert "bob" in str(await bob_tools[0].ainvoke({}))
    response = await http.put(
        base + "/fixture/connection",
        headers=headers["alice"],
        json={
            "secret": "alice-new-key",
            "config_revision": revision,
        },
    )
    assert response.status_code == 200
    async with scenario["db"]() as db:
        await MCPOAuthService(db).reject_credentials("fixture", "alice", alice_generation)
    assert "alice-new" in str(await alice_tools[0].ainvoke({}))
    assert current["refresh_count"] == 0 and current["register_count"] == 0

    # 权限不足与临时网络故障不能删除已保存的个人密文。
    current["error_status"] = 403
    failed = await http.post(base + "/fixture/test", headers=headers["alice"])
    assert failed.status_code == 502
    assert failed.json()["detail"] == {
        "code": "mcp_connection_failed",
        "message": "连接器服务方权限不足，请检查个人账号权限",
    }
    current.pop("error_status")
    async with scenario["db"]() as db:
        own = await db.scalar(select(MCPConnection).where(MCPConnection.user_id == scenario["users"][0].id))
        assert own.status == "connected" and decrypt(own.credentials)["secret"] == "alice-new-key"

    await http.delete(base + "/fixture/connection", headers=headers["alice"])
    received = len(current["received_credentials"])
    with pytest.raises(Exception):
        await alice_tools[0].ainvoke({})
    assert len(current["received_credentials"]) == received
    assert "bob" in str(await bob_tools[0].ainvoke({}))
    await http.put(base + "/fixture/status", headers=headers["admin"], json={"enabled": False})
    assert (await http.get(base, headers=headers["alice"])).json()["data"] == []
    bob_listing = (await http.get(base, headers=headers["bob"])).json()["data"]
    assert len(bob_listing) == 1 and bob_listing[0]["enabled"] is False
    # 停用后的连接测试仍用本人凭证发现工具，不把服务放回运行集合。
    probed = await http.post(base + "/fixture/test", headers=headers["bob"])
    assert probed.status_code == 200, probed.text
    assert probed.json()["success"] is True and probed.json()["tool_count"] == 1
    assert "bob-key" not in probed.text
    assert await get_enabled_mcp_tools("fixture", uid="bob") == []
    async with scenario["db"]() as db:
        disabled = await db.scalar(select(MCPServer).where(MCPServer.slug == "fixture"))
        assert disabled.enabled == 0
    await http.delete(base + "/fixture/connection", headers=headers["bob"])
    assert (await http.get(base, headers=headers["bob"])).json()["data"] == []
    async with scenario["db"]() as db:
        rows = (await db.scalars(select(MCPConnection))).all()
        assert all(row.credentials is None and row.status == "disconnected" for row in rows)
        generations = {row.user_id: row.generation for row in rows}
    changed = await http.put(base + "/fixture", headers=headers["admin"], json={"headers": {"X-Changed": "yes"}})
    assert changed.status_code == 200
    for user in ("alice", "bob"):
        assert (await http.get(base, headers=headers[user])).json()["data"] == []
    async with scenario["db"]() as db:
        rows = (await db.scalars(select(MCPConnection))).all()
        assert all(row.status == "disconnected" and row.credentials is None for row in rows)
        assert all(row.generation == generations[row.user_id] + 1 for row in rows)


async def test_static_credential_input_and_stale_revision_cannot_publish_secrets(scenario):
    """输入错误不回显秘密，旧表单和伪造身份不能写入当前连接。"""
    http, headers = scenario["http"], scenario["headers"]
    base = "/api/system/mcp-servers/fixture"
    response = await http.put(base, headers=headers["admin"], json={"auth_mode": "bearer", "headers": {}})
    revision = response.json()["data"]["config_revision"]
    for body in (
        {"secret": "private-key", "config_revision": revision, "user_id": scenario["users"][1].id},
        {"secret": "private-key\n", "config_revision": revision},
        {"secret": "private-key" * 4000, "config_revision": revision},
        {"secret": "", "config_revision": revision},
    ):
        invalid = await http.put(base + "/connection", headers=headers["alice"], json=body)
        assert invalid.status_code == 422 and "private-key" not in invalid.text
    assert (await http.put(base, headers=headers["alice"], json={"url": "https://other.example/mcp"})).status_code == 403
    for update in (
        {"auth_mode": "api_key", "api_key_header": "Host"},
        {"headers": {"aUtHoRiZaTiOn": "Bearer shared"}},
    ):
        invalid = await http.put(base, headers=headers["admin"], json=update)
        assert invalid.status_code == 400
    changed = await http.put(base, headers=headers["admin"], json={"url": scenario["root"] + "/mcp?changed=1"})
    assert changed.status_code == 200
    stale = await http.put(base + "/connection", headers=headers["alice"], json={"secret": "private-key", "config_revision": revision})
    assert stale.status_code == 409 and "private-key" not in stale.text
    async with scenario["db"]() as db:
        assert (await db.scalars(select(MCPConnection))).all() == []


async def test_static_401_revokes_only_current_connection_and_other_origins_are_rejected(scenario):
    """远端 401 形成重连状态，跨来源请求在发送凭证前拒绝。"""
    from yuxi.modules.extensions.mcp.oauth_runtime import PersonalMCPAuth

    http, headers = scenario["http"], scenario["headers"]
    base = "/api/system/mcp-servers/fixture"
    response = await http.put(base, headers=headers["admin"], json={"auth_mode": "bearer", "headers": {}})
    revision = response.json()["data"]["config_revision"]
    saved = await http.put(
        base + "/connection", headers=headers["alice"], json={"secret": "fixture-invalid-key", "config_revision": revision}
    )
    assert saved.status_code == 200
    auth = PersonalMCPAuth("fixture", "alice", revision, auth_mode="bearer", url=scenario["root"] + "/mcp")
    async with httpx.AsyncClient(auth=auth, trust_env=False) as client:
        with pytest.raises(StateConflictError, match="来源"):
            await client.post(scenario["root"].replace("127.0.0.1", "localhost") + "/mcp", json={})
    assert scenario["current"].get("received_credentials", []) == []
    failed = await http.post(base + "/test", headers=headers["alice"])
    assert failed.status_code == 502
    async with scenario["db"]() as db:
        own = await db.scalar(select(MCPConnection).where(MCPConnection.user_id == scenario["users"][0].id))
        assert own.status == "reconnect_required" and own.credentials is None


@pytest.mark.parametrize("mode", ["none", "bearer", "api_key", "oauth"])
async def test_switch_from_shared_auth_requires_explicit_headers(scenario, mode):
    """切换认证方式不能隐式沿用共享凭证，拒绝时保留原配置。"""
    http, headers = scenario["http"], scenario["headers"]["admin"]
    base = "/api/system/mcp-servers/fixture"
    shared_headers = {"X-API-Key": "fixture-shared-key"}
    saved = await http.put(base, headers=headers, json={"auth_mode": "headers", "headers": shared_headers})
    assert saved.status_code == 200
    changes = {"auth_mode": mode, "api_key_header": "X-Personal-Key"}
    if mode == "oauth":
        changes["oauth_config"] = {"issuer_url": scenario["root"] + "/issuer", "registration": "dynamic"}
    rejected = await http.put(base, headers=headers, json=changes)
    assert rejected.status_code == 400
    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer))
        assert server.auth_mode == "headers" and server.headers == shared_headers


@pytest.mark.parametrize("mode", ["none", "bearer", "api_key", "oauth"])
@pytest.mark.parametrize(
    "shared_headers",
    [{"X-Version": "2"}, {"X-API-Key": "fixture-shared-key", "X-Version": "2"}],
    ids=["protocol-only", "shared-key"],
)
async def test_switch_from_shared_auth_accepts_explicit_public_headers(scenario, mode, shared_headers):
    """显式公共协议头可在切换时保存，包括重填与原值相同的协议头。"""
    http, headers = scenario["http"], scenario["headers"]["admin"]
    base = "/api/system/mcp-servers/fixture"
    saved = await http.put(base, headers=headers, json={"auth_mode": "headers", "headers": shared_headers})
    assert saved.status_code == 200
    changes = {"auth_mode": mode, "headers": {"X-Version": "2"}, "api_key_header": "X-Personal-Key"}
    if mode == "oauth":
        changes["oauth_config"] = {"issuer_url": scenario["root"] + "/issuer", "registration": "dynamic"}
    updated = await http.put(base, headers=headers, json=changes)
    assert updated.status_code == 200, updated.text
    async with scenario["db"]() as db:
        server = await db.scalar(select(MCPServer))
        assert server.auth_mode == mode and server.headers == {"X-Version": "2"}


async def test_explicit_anonymous_mode_does_not_require_a_personal_connection(scenario):
    """无认证方式仍走当前服务策略，不发送共享或个人凭证。"""
    http, headers = scenario["http"], scenario["headers"]
    response = await http.put("/api/system/mcp-servers/fixture", headers=headers["admin"], json={"auth_mode": "none", "headers": {}})
    assert response.status_code == 200
    scenario["identities"][""] = "anonymous-result"
    tools = await get_enabled_mcp_tools("fixture")
    assert "anonymous-result" in str(await tools[0].ainvoke({}))
    assert set(scenario["current"]["received_credentials"]) == {""}
    assert (await http.post("/api/system/mcp-servers/fixture/test", headers=headers["alice"])).status_code == 403
