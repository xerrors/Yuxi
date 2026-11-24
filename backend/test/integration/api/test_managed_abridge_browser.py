"""通过真实 HTTP、PostgreSQL 与 MCP 协议验证托管浏览器租户边界。"""

import asyncio
import hashlib
import json
import os
import time
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update

from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.public_v1.agents import public_agents_router
from yuxi.api.routers.system import system
from yuxi.infrastructure.postgres.connection import postgres_conninfo
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sandbox import UserSandbox
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.runtime.context import BaseContext
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.extensions.mcp.models import MCPServer
from yuxi.modules.extensions.mcp.runtime import MCPUnavailableError
from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools, runtime_mcp_failure
from yuxi.modules.extensions.plugins.abridge import managed_browser
from yuxi.modules.extensions.plugins.abridge.client import abridge_mcp_connection, issue_abridge_delegation
from yuxi.modules.extensions.plugins.abridge.options import abridge_options
from yuxi.modules.identity.models import APIKey, Department, User
from yuxi.modules.identity.provider import ProviderError
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.system.models import ConfigOption
from yuxi.modules.workspace.models import Project
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import DomainError, PermissionDeniedError, StateConflictError, UpstreamError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]

PUBLIC_ORIGIN = "https://bridge.example"
ISSUER = "https://yuxi.example/api/auth/provider"
ENDPOINT = "/api/v1/agents/managed-browser"
APP_HEADER = {"Authorization": "Bearer yxkey_app_one", "X-End-User-Id": "same-external-id"}


async def test_provision_and_delegate_current_app_browser(monkeypatch, tmp_path):
    """登记新终端，验证幂等、另一个 APP 隔离、停用和在途工具权限复查。"""
    monkeypatch.delenv("ABRIDGE_ALLOW_INSECURE_HTTP", raising=False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "issuer.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    for name, value in {
        "YUXI_OIDC_PROVIDER_ENABLED": "true",
        "YUXI_OIDC_PROVIDER_PRIVATE_KEY_FILE": str(pem),
        "YUXI_OIDC_PROVIDER_ISSUER": ISSUER,
        "YUXI_OIDC_PROVIDER_CLIENT_ID": "bridge-test",
        "YUXI_OIDC_PROVIDER_CLIENT_SECRET": "fixture-client-secret" * 2,
        "YUXI_OIDC_PROVIDER_REDIRECT_URI": PUBLIC_ORIGIN + "/callback",
        "ABRIDGE_PUBLIC_ORIGIN": PUBLIC_ORIGIN,
    }.items():
        monkeypatch.setenv(name, value)

    bridge = FastAPI()
    online = set()
    running = set()
    bound = {}
    registrations = {}
    sent = []
    launches = []
    preparations = []
    transient_launch_failures = []
    upstream_mode = "active"
    template_installed = True
    guest_origin = PUBLIC_ORIGIN
    installation = hashlib.sha256(b"guest-id").hexdigest()
    ticket = "a" * 43

    def signed(request, purpose):
        """aBridge 端独立验证受众、用途和时效。"""
        claims = jwt.decode(
            request.headers["Authorization"].removeprefix("Bearer "),
            key.public_key(),
            algorithms=["RS256"],
            audience=PUBLIC_ORIGIN + request.url.path,
            issuer=ISSUER,
        )
        assert claims["purpose"] == purpose and claims["client_id"] == "bridge-test"
        assert claims["exp"] - claims["iat"] == 60
        assert claims["iat"] <= int(time.time())
        if purpose != "abridge-managed-browser-status":
            assert claims["installation_id"] == installation
            sent.append((purpose, claims["sub"]))
        return claims

    def view(uid):
        """状态查询只返回服务器已经拥有的事实。"""
        if uid not in registrations:
            return None
        status = "online" if uid in online else "offline"
        return {**registrations[uid], "status": status}

    @bridge.get("/api/managed-browsers/status")
    async def status(request: Request):
        """单独探测协议，已有设备在线与本地绑定由不同服务拥有。"""
        assert not await request.body()
        uid = signed(request, "abridge-managed-browser-status")["sub"]
        if upstream_mode == "legacy":
            return Response(status_code=404)
        if upstream_mode == "unexpected":
            return Response(status_code=401)
        if upstream_mode == "toggle-after-preflight":
            disable = await http.put(option_path, headers=admin_header, json={"value": {"allow_shadow_managed_browser": False}})
            assert disable.status_code == 200
        if upstream_mode == "timeout-after-launch" and uid in running:
            await asyncio.sleep(5.5)
        return {
            "version": 2,
            "enabled": upstream_mode != "disabled",
            "browser_origin": "https://other.example" if upstream_mode == "wrong-origin" else PUBLIC_ORIGIN,
            "binding": view(uid),
        }

    @bridge.post("/api/managed-browsers/provision")
    async def register(request: Request):
        """aBridge 比较当前版本，单独表示票据是否已经兑换。"""
        assert not await request.body()
        claims = signed(request, "abridge-managed-browser-provision")
        uid = claims["sub"]
        if upstream_mode == "revoked":
            return JSONResponse({"error": {"code": "managed_browser_revoked"}}, status_code=403)
        if upstream_mode in {"stale_binding", "device_paused"}:
            return JSONResponse({"error": {"code": upstream_mode}}, status_code=409)
        previous = registrations.get(uid)
        assert claims["revision"] == (previous["revision"] if previous else 0)
        if previous is None or claims["replace"]:
            registrations[uid] = {
                "installation_id": installation,
                "device_id": "browser-" + uid,
                "revision": claims["revision"] + 1,
                "key_issued": False,
            }
        if upstream_mode == "toggle-after-registration":
            disable = await http.put(option_path, headers=admin_header, json={"value": {"allow_shadow_managed_browser": False}})
            assert disable.status_code == 200
        response = view(uid)
        return {**response, **({} if response["key_issued"] else {"bootstrap_token": ticket})}

    @bridge.post("/mcp")
    async def mcp(request: Request):
        """通过原有 MCP 工具协议返回该版本绑定的设备。"""
        claims = signed(request, "abridge-delegation")
        uid = claims["sub"]
        assert claims["revision"] == registrations[uid]["revision"]
        body = await request.json()
        method = body["method"]
        if method == "initialize":
            if upstream_mode == "revoke-after-mcp-initialize":
                async with sessions() as db:
                    await db.execute(update(APIKey).where(APIKey.id == one.id).values(is_enabled=False))
                    await db.commit()
            result = {
                "protocolVersion": body["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aBridge fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "devices_list", "description": "Devices", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            assert body["params"]["name"] == "devices_list"
            result = {"content": [{"type": "text", "text": "browser-" + uid}]}
        else:
            assert method == "notifications/initialized"
            return Response(status_code=202)
        return {"jsonrpc": "2.0", "id": body["id"], "result": result}

    async with isolated_audit_store(monkeypatch) as (_, sessions), serve_http(bridge) as upstream:
        async with sessions() as db:
            department = Department(name="Managed browser test")
            db.add(department)
            await db.flush()
            owner = User(uid="owner", username="owner", password_hash="!disabled", department_id=department.id)
            admin = User(uid="admin", username="admin", password_hash="!disabled", role="superadmin", department_id=department.id)
            db.add_all([owner, admin])
            await db.flush()
            one = APIKey(
                key_hash=hashlib.sha256(b"yxkey_app_one").hexdigest(),
                key_prefix="yxkey_",
                name="one",
                user_id=owner.id,
                access_level="agents",
                app_id="app-one",
                created_by=owner.uid,
            )
            replacement = APIKey(
                key_hash=hashlib.sha256(b"yxkey_app_one_replacement").hexdigest(),
                key_prefix="yxkey_",
                name="replacement",
                user_id=owner.id,
                access_level="agents",
                app_id="app-one",
                created_by=owner.uid,
            )
            db.add_all(
                [
                    one,
                    replacement,
                    APIKey(
                        key_hash=hashlib.sha256(b"yxkey_app_two").hexdigest(),
                        key_prefix="yxkey_",
                        name="two",
                        user_id=owner.id,
                        access_level="agents",
                        app_id="app-two",
                        created_by=owner.uid,
                    ),
                    APIKey(
                        key_hash=hashlib.sha256(b"yxkey_personal").hexdigest(),
                        key_prefix="yxkey_",
                        name="personal",
                        user_id=owner.id,
                        access_level="agents",
                        created_by=owner.uid,
                    ),
                    ConfigOption(
                        key=abridge_options.key,
                        name=abridge_options.name,
                        params=abridge_options.params,
                        value={"url": str(upstream.base_url).rstrip("/")},
                    ),
                    MCPServer(
                        code_owned=True,
                        slug="abridge",
                        name="aBridge",
                        transport="streamable_http",
                        url="http://abridge:8765/mcp",
                        auth_mode="delegation",
                        enabled=1,
                        created_by="system",
                        updated_by="system",
                    ),
                ]
            )
            await db.commit()
            schema = await db.scalar(select(func.current_schema()))
        admin_header = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": str(admin.id), "auth_method": "password"})}

        class CubeFixture:
            """替换需要节点的两次调用，代次仍写入隔离 PostgreSQL。"""

            backend = "cube"

            def prepare_instance(self, uid):
                """写入 Cube 代次。"""
                preparations.append(uid)
                with psycopg.connect(postgres_conninfo(os.environ["POSTGRES_URL"]), options=f"-c search_path={schema}") as conn:
                    conn.execute(
                        "INSERT INTO user_sandboxes "
                        "(uid, generation, sandbox_id, traffic_token, envd_token, state, operation_id, last_active_at) "
                        "VALUES (%s, 7, 'guest-id', '', 'fixture', 'running', 'create', now()) ON CONFLICT (uid) DO NOTHING",
                        (uid,),
                    )
                return 7, "guest-id"

            def run_guest_program(self, uid, generation, *, user, command, arguments, timeout, stdin_data=None, touch_idle=False):
                """单独记录本地进程存活和 aBridge 设备在线。"""
                assert generation == 7
                if command == "/usr/bin/test":
                    assert user == "user" and arguments == ["-r", "/opt/yuxi-managed-browser/service-v2"] and not touch_idle
                    return (0 if template_installed else 1), b""
                assert command == "/usr/local/bin/python3" and user == "managed-browser"
                assert arguments[:2] == ["-I", "/opt/yuxi-managed-browser/abridge-client-browser"]
                action, handoff = arguments[-1], stdin_data
                assert touch_idle == (action == "start")
                if action == "start":
                    data = json.loads(handoff)
                    registration = registrations[uid]
                    assert data["browser_origin"] == PUBLIC_ORIGIN
                    assert data["device_id"] == registration["device_id"]
                    assert data["revision"] == registration["revision"]
                    if registration["key_issued"]:
                        assert "bootstrap_token" not in data and bound[uid] == registration["revision"]
                    else:
                        assert data["bootstrap_token"] == ticket
                        registration["key_issued"] = True
                        if not launches and not transient_launch_failures:
                            transient_launch_failures.append(uid)
                            raise UpstreamError("fixture loses the redeem response before recording the binding")
                        bound[uid] = registration["revision"]
                    launches.append(uid)
                    running.add(uid)
                    online.add(uid)
                else:
                    assert handoff is None
                    assert action == "status"
                if uid not in bound:
                    return 0, b'{"status":"unbound"}'
                return 0, json.dumps(
                    {
                        "status": "running" if uid in running else "bound",
                        "browser_origin": guest_origin,
                        "device_id": "browser-" + uid,
                        "revision": bound[uid],
                        "connected": uid in running and len(launches) > 1,
                    }
                ).encode()

        monkeypatch.setattr("yuxi.modules.extensions.plugins.abridge.managed_browser.get_sandbox_provider", CubeFixture)
        app = FastAPI()
        app.add_exception_handler(DomainError, domain_error_handler)
        app.include_router(public_agents_router, prefix="/api")
        app.include_router(system, prefix="/api")
        async with serve_http(app) as http:
            option_path = "/api/system/config/options/abridge_options"
            toggle = {"value": {"allow_shadow_managed_browser": True}}
            denied_header = {**APP_HEADER, "X-End-User-Id": "denied-fixture"}
            assert (await http.put(option_path, headers=APP_HEADER, json=toggle)).status_code == 403
            invalid = await http.put(option_path, headers=admin_header, json={"value": {"allow_shadow_managed_browser": "true"}})
            assert invalid.status_code == 400
            assert (await http.post(ENDPOINT, headers=denied_header)).status_code == 403
            assert not sent and not launches
            assert (await http.post(ENDPOINT, headers=admin_header)).status_code == 403
            assert (await http.put(option_path, headers=admin_header, json=toggle)).status_code == 200

            monkeypatch.setenv("ABRIDGE_PUBLIC_ORIGIN", "http://bridge.example")
            assert (await http.post(ENDPOINT, headers=denied_header)).status_code == 409
            assert not sent and not launches
            monkeypatch.setenv("ABRIDGE_PUBLIC_ORIGIN", PUBLIC_ORIGIN)

            for upstream_mode, status, code in [
                ("disabled", 409, "abridge_managed_browser_upstream_disabled"),
                ("legacy", 409, "abridge_managed_browser_upstream_disabled"),
                ("unexpected", 502, "abridge_managed_browser_unavailable"),
                ("toggle-after-preflight", 403, "abridge_managed_browser_disabled"),
            ]:
                rejected = await http.post(ENDPOINT, headers=denied_header)
                assert rejected.status_code == status and rejected.json()["detail"]["code"] == code
                assert not sent and not preparations
                async with sessions() as db:
                    assert await db.scalar(select(func.count()).select_from(UserSandbox)) == 0
            assert (await http.put(option_path, headers=admin_header, json=toggle)).status_code == 200
            upstream_mode = "active"

            template_installed = False
            unsupported = await http.post(ENDPOINT, headers=denied_header)
            assert unsupported.status_code == 409 and not sent and not launches
            template_installed = True

            def failed_environment(self, uid):
                """复现已持久化的失败环境，禁止重复登记。"""
                raise StateConflictError("请在设置中显式重建", code="sandbox_unavailable")

            with monkeypatch.context() as failed:
                failed.setattr(CubeFixture, "prepare_instance", failed_environment)
                unavailable = await http.post(ENDPOINT, headers=denied_header)
            assert unavailable.status_code == 409
            assert unavailable.json()["detail"]["code"] == "sandbox_unavailable"
            assert "部署管理员" in unavailable.json()["detail"]["message"] and not sent

            for upstream_mode, status, code in [
                ("wrong-origin", 502, "abridge_managed_browser_unavailable"),
                ("revoked", 403, "abridge_managed_browser_revoked"),
                ("stale_binding", 409, "abridge_managed_browser_stale"),
                ("device_paused", 409, "abridge_managed_browser_paused"),
            ]:
                rejected = await http.post(ENDPOINT, headers=denied_header)
                assert rejected.status_code == status and rejected.json()["detail"]["code"] == code and not launches
            upstream_mode = "toggle-after-registration"
            rejected = await http.post(ENDPOINT, headers=denied_header)
            assert rejected.status_code == 403 and not launches
            assert sent[-1][0] == "abridge-managed-browser-provision"
            assert (await http.put(option_path, headers=admin_header, json=toggle)).status_code == 200
            upstream_mode = "active"

            lost = await http.post(ENDPOINT, headers=APP_HEADER)
            assert lost.status_code == 502 and not launches
            # 上游保留在线时，首次启动仍须等待来宾扩展完成握手。
            first = await http.post(ENDPOINT, headers=APP_HEADER)
            assert first.status_code == 202 and len(launches) == 1
            uid = launches[0]
            assert transient_launch_failures == [uid]
            assert registrations[uid]["revision"] == 2
            assert first.json() == {"device_id": "browser-" + uid, "status": "connecting"}
            assert sent.count(("abridge-managed-browser-provision", uid)) == 2

            # 已绑定进程重启可复用原 profile，不再发出票据或轮换。
            running.discard(uid)
            retry = await http.post(ENDPOINT, headers=APP_HEADER)
            assert retry.status_code == 200 and launches == [uid, uid]
            assert retry.json() == {"device_id": "browser-" + uid, "status": "ready"}
            assert sent.count(("abridge-managed-browser-provision", uid)) == 2
            guest_origin = "https://previous.example"
            conflict = await http.post(ENDPOINT, headers=APP_HEADER)
            assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "abridge_managed_browser_binding_changed"
            assert sent.count(("abridge-managed-browser-provision", uid)) == 2
            guest_origin = PUBLIC_ORIGIN

            other = await http.post(ENDPOINT + "?uid=" + uid, headers={**APP_HEADER, "Authorization": "Bearer yxkey_app_two"})
            assert other.status_code == 200 and other.json()["device_id"] != retry.json()["device_id"]
            async with sessions() as db:
                users = (await db.scalars(select(User).where(User.end_user_id == APP_HEADER["X-End-User-Id"]).order_by(User.app_id))).all()
                assert [user.uid for user in users] == [launches[0], launches[2]]
                assert [user.app_id for user in users] == ["app-one", "app-two"]
                db.add(
                    Project(
                        id="project", uid=uid, name="Fixture", selection_status="implicit", workdir_path="project", directory_mode="managed"
                    )
                )
                await db.flush()
                thread = Session(thread_id="thread", uid=uid, app_id="app-one", agent_id="fixture", project_id="project")
                db.add(thread)
                await db.flush()
                db.add(AgentTurn(id="turn", thread_id=thread.thread_id, uid=uid, app_id="app-one"))
                await db.flush()
                run = AgentRun(
                    id="run",
                    uid=uid,
                    thread_id="thread",
                    runtime_scope_id="thread",
                    agent_slug="fixture",
                    turn_id="turn",
                    session_record_id=thread.id,
                    app_id="app-one",
                    api_key_id=one.id,
                    status="running",
                    worker_id="managed-worker",
                    lease_expires_at=utc_now() + timedelta(minutes=2),
                )
                db.add(run)
                await db.commit()

            context = BaseContext(
                uid=uid,
                actor_scope=ActorScope(uid=uid, app_id="app-one", api_key_id=one.id),
                run_id="run",
                worker_id="managed-worker",
                thread_id="thread",
            )
            with pytest.raises(ProviderError, match="access_denied"):
                await issue_abridge_delegation(uid, PUBLIC_ORIGIN + "/mcp")
            before = list(sent)
            missing_scope = BaseContext(uid=uid, run_id="run", worker_id="managed-worker", thread_id="thread")
            with pytest.raises(PermissionDeniedError, match="用户已失效"):
                await resolve_runtime_mcp_tools(["abridge"], uid=uid, context=missing_scope)
            assert sent == before

            tools = await resolve_runtime_mcp_tools(["abridge"], uid=uid, context=context)
            result = await tools["abridge"][0].ainvoke({})
            assert "browser-" + uid in str(result)

            # MCP SDK 单独发送 initialized 通知，期间发生的执行撤权也必须到达调用者。
            upstream_mode = "revoke-after-mcp-initialize"
            for call in (lambda: resolve_runtime_mcp_tools(["abridge"], uid=uid, context=context), lambda: tools["abridge"][0].ainvoke({})):
                before = sent.count(("abridge-delegation", uid))
                with pytest.raises(PermissionDeniedError, match="Key 不可用"):
                    try:
                        await call()
                    except Exception as exc:
                        runtime_mcp_failure(exc)
                assert sent.count(("abridge-delegation", uid)) == before + 1
                async with sessions() as db:
                    await db.execute(update(APIKey).where(APIKey.id == one.id).values(is_enabled=True))
                    await db.commit()
            upstream_mode = "active"

            config = await abridge_mcp_connection(uid=uid, context=context)
            async with httpx.AsyncClient(auth=config["auth"]) as mcp_http:
                before = list(sent)
                with monkeypatch.context() as deployment:
                    deployment.setattr(CubeFixture, "backend", "container")
                    deployment.setattr(managed_browser, "registered_sandbox", AsyncMock(return_value=None))
                    with pytest.raises(MCPUnavailableError, match="不支持 Cube"):
                        await mcp_http.post(config["url"], json={"method": "tools/list"})
                assert sent == before
                lease = {
                    "app_id": "app-one",
                    "api_key_id": one.id,
                    "worker_id": "managed-worker",
                    "status": "running",
                    "lease_expires_at": utc_now() + timedelta(minutes=2),
                }
                for changed in (
                    {"worker_id": "other-worker"},
                    {"lease_expires_at": utc_now() - timedelta(seconds=1)},
                    {"status": "cancel_requested"},
                    {"app_id": None},
                    {"app_id": "app-two"},
                    {"api_key_id": replacement.id},
                ):
                    async with sessions() as db:
                        await db.execute(update(AgentRun).where(AgentRun.id == "run").values(**(lease | changed)))
                        await db.commit()
                    with pytest.raises(PermissionDeniedError, match="有效运行租约"):
                        await mcp_http.post(config["url"], json={"method": "tools/list"})
                    assert sent == before
                async with sessions() as db:
                    await db.execute(update(AgentRun).where(AgentRun.id == "run").values(**lease))
                    await db.execute(update(APIKey).where(APIKey.id == one.id).values(is_enabled=False))
                    await db.commit()
                with pytest.raises(PermissionDeniedError, match="Key 不可用"):
                    await mcp_http.post(config["url"], json={"method": "tools/list"})
                assert sent == before
                async with sessions() as db:
                    await db.execute(update(APIKey).where(APIKey.id == one.id).values(is_enabled=True))
                    await db.commit()
                assert (
                    await http.put(option_path, headers=admin_header, json={"value": {"allow_shadow_managed_browser": False}})
                ).status_code == 200
                with pytest.raises(MCPUnavailableError, match="未允许"):
                    await mcp_http.post(config["url"], json={"method": "tools/list"})
                assert uid in running and uid in online  # 拒绝新委托不能终止来宾设备。
                assert sent == before
                assert launches == [uid, uid, users[1].uid]

            unavailable = {}
            previous_delegations = sent.count(("abridge-delegation", uid))
            assert await resolve_runtime_mcp_tools(["abridge"], uid=uid, context=context, unavailable=unavailable) == {}
            assert "未允许" in unavailable["abridge"]
            assert sent.count(("abridge-delegation", uid)) == previous_delegations

            before = list(preparations)
            personal = await http.post(ENDPOINT, headers={"Authorization": "Bearer yxkey_personal"})
            assert personal.status_code == 403 and personal.json()["detail"]["code"] == "abridge_managed_browser_app_required"
            assert preparations == before

            assert (await http.put(option_path, headers=admin_header, json=toggle)).status_code == 200
            resumed = await http.post(ENDPOINT, headers=APP_HEADER)
            assert resumed.status_code == 200 and resumed.json() == retry.json()
            assert registrations[uid]["revision"] == 2
            assert sent.count(("abridge-managed-browser-provision", uid)) == 2
            upstream_mode = "timeout-after-launch"
            timeout = await http.post(ENDPOINT, headers={**APP_HEADER, "X-End-User-Id": "slow-fixture"}, timeout=8)
            assert timeout.status_code == 504 and timeout.json()["detail"]["code"] == "abridge_managed_browser_timeout"
