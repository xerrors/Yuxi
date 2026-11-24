"""可选源码探针：真实 Yuxi Provider、aBridge Server 与设备产品入口。"""

import asyncio
import json
import os
import socket
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from websockets.sync.client import connect as connect_websocket

from test.integration.services.test_oidc_provider import scenario as provider_scenario  # noqa: F401
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.extensions.devices import devices as device_router
from yuxi.api.routers.system import system
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.models.sandbox import UserSandbox
from yuxi.modules.extensions.mcp.models import MCPConnection, MCPServer
from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools
from yuxi.modules.extensions.plugins.abridge.options import abridge_options
from yuxi.modules.extensions.plugins.abridge.service import personal_devices
from yuxi.modules.identity.models import User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.system.models import ConfigOption
from yuxi.shared.errors import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def bridge(provider_scenario, monkeypatch, tmp_path):  # noqa: F811
    """只启动指定开发源码，数据库、端口和管理员凭证均为测试自有。"""
    scenario = provider_scenario
    monkeypatch.setenv("YUXI_OIDC_PROVIDER_CLIENT_ID", "abridge")
    scenario["app"].add_exception_handler(DomainError, domain_error_handler)
    scenario["app"].include_router(device_router, prefix="/api")
    scenario["app"].include_router(system, prefix="/api")
    if not os.environ.get("ABRIDGE_TEST_SOURCE"):
        pytest.skip("需要 ABRIDGE_TEST_SOURCE 指向已构建的独立 aBridge 源码")
    source = Path(os.environ["ABRIDGE_TEST_SOURCE"])
    assert (source / "dist/server/cli.js").is_file(), "指定的 aBridge 源码缺少构建产物"
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    sock.close()
    monkeypatch.setenv("YUXI_OIDC_PROVIDER_REDIRECT_URI", origin + "/api/auth/oidc/callback")
    monkeypatch.setenv("ABRIDGE_PUBLIC_ORIGIN", origin)
    environment = os.environ | {
        "ABRIDGE_ADMIN_TOKEN": "fixture-admin-token-" + "x" * 32,
        "ABRIDGE_OIDC_ISSUER_URL": scenario["origin"] + "/api/auth/provider",
        "ABRIDGE_OIDC_CLIENT_ID": "abridge",
        "ABRIDGE_OIDC_CLIENT_SECRET": os.environ["YUXI_OIDC_PROVIDER_CLIENT_SECRET"],
        "ABRIDGE_PUBLIC_ORIGIN": origin,
        "ABRIDGE_DELEGATION_ENABLED": "true",
        "ABRIDGE_MANAGED_BROWSER_ENABLED": "true",
    }
    for name in ("ABRIDGE_OIDC_BACKCHANNEL_ORIGIN", "ABRIDGE_ALLOWED_HOSTS", "ABRIDGE_ALLOWED_ORIGINS"):
        environment.pop(name, None)

    @asynccontextmanager
    async def sessions():
        """领域服务复用真实测试数据库，不访问现有部署。"""
        async with scenario["db"]() as db:
            yield db

    monkeypatch.setattr(pg_manager, "get_async_session_context", sessions)
    async with scenario["db"]() as db:
        await db.run_sync(lambda session: ConfigOption.__table__.create(session.connection()))
        for model in (MCPServer, MCPConnection):
            await db.run_sync(lambda session, model=model: model.__table__.create(session.connection()))
        db.add(
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
            )
        )
        db.add(
            ConfigOption(
                key=abridge_options.key,
                name="aBridge test",
                params=abridge_options.params,
                value={"url": origin},
            )
        )
        await db.commit()
    with (tmp_path / "bridge.log").open("w") as log:
        child = subprocess.Popen(
            [
                "node",
                "dist/server/cli.js",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(urlsplit(origin).port),
                "--db",
                str(tmp_path / "bridge.db"),
            ],
            cwd=source,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(base_url=origin, trust_env=False, follow_redirects=False, timeout=10) as http:
                async with asyncio.timeout(15):
                    while True:
                        if child.poll() is not None:
                            pytest.fail("aBridge 开发实例启动失败")
                        try:
                            response = await http.get("/health")
                            if response.status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(0.05)
                yield {"http": http, "scenario": scenario, "origin": origin}
        finally:
            child.terminate()
            await asyncio.to_thread(child.wait, 5)


async def login(bridge, token):
    """浏览器协议真实经过两边的授权、PKCE 兑换和会话发布。"""
    http, provider = bridge["http"], bridge["scenario"]["http"]
    start = await http.get("/api/auth/oidc/login")
    assert start.status_code == 302, start.text
    authorization = await provider.get(start.headers["location"])
    assert authorization.status_code == 302, authorization.text
    query = {key: values[0] for key, values in parse_qs(urlsplit(authorization.headers["location"]).query).items()}
    approved = await provider.post("/api/auth/provider/authorize", json=query, headers={"Authorization": "Bearer " + token})
    assert approved.status_code == 200, approved.text
    callback = await http.get(approved.json()["redirect_url"])
    assert callback.status_code == 302, callback.text
    me = await http.get("/api/me")
    assert me.status_code == 200
    return me.json()


async def test_real_login_personal_device_isolation_and_revocation(bridge):
    """开户、授权读取与撤销走真实两边；重登录不能恢复设备权限。"""
    scenario = bridge["scenario"]
    assert (await personal_devices("stable-person"))["state"] == "login_required"
    first = await login(bridge, scenario["token"])
    assert await login(bridge, scenario["token"]) == first
    assert (await personal_devices("stable-person"))["devices"] == []
    http = bridge["http"]
    created = await http.post("/api/devices", json={"name": "Personal desktop", "kind": "computer", "capabilities": ["files"]})
    assert created.status_code == 200
    device = created.json()
    # 探针也用于默认部署的旧版，两个版本的管理端点名称不同。
    connection_path, list_key = "/api/agent-connections", "connections"
    listed = await http.get(connection_path)
    if listed.status_code == 404:
        connection_path, list_key = "/api/client-grants", "grants"
        listed = await http.get(connection_path)
    listed.raise_for_status()
    platform = next(connection for connection in listed.json()[list_key] if connection["name"] == "abridge 平台连接")
    platform_path = f"{connection_path}/{platform['id']}"
    assert platform["policy"] == {"mode": "all", "capabilities": ["screen", "input", "shell", "files", "browser"], "overrides": []}
    excluded = await http.post("/api/devices", json={"name": "Excluded desktop", "kind": "computer", "capabilities": ["files"]})
    assert excluded.status_code == 200
    excluded_device = excluded.json()
    assert {row["id"] for row in (await personal_devices("stable-person"))["devices"]} == {device["id"], excluded_device["id"]}
    policy = {"mode": "selected", "devices": [{"device_id": device["id"], "capabilities": ["files"]}]}
    updated = await http.patch(platform_path, json={"name": platform["name"], "policy": policy})
    assert updated.status_code == 200, updated.text
    devices = await personal_devices("stable-person")
    assert [row["id"] for row in devices["devices"]] == [device["id"]]
    assert devices["devices"][0]["status"] == "offline"
    headers = {"Authorization": "Bearer " + scenario["token"]}
    product = await scenario["http"].get("/api/devices?uid=second-person", headers=headers)
    assert product.status_code == 200
    assert [row["id"] for row in product.json()["devices"]] == [device["id"]]
    assert (await scenario["http"].get("/api/devices/overview", headers=headers)).status_code == 404
    assert (await scenario["http"].get("/api/system/config/options", headers=headers)).status_code == 403
    runtime = await resolve_runtime_mcp_tools(["abridge"], uid="stable-person")
    listed = await next(tool for tool in runtime["abridge"] if tool.name == "devices_list").ainvoke({})
    assert device["id"] in str(listed)
    async with scenario["db"]() as db:
        first_user = await db.scalar(select(User).where(User.id == scenario["id"]))
        other = User(uid="second-person", username="Second", department_id=first_user.department_id, password_hash="!disabled")
        db.add(other)
        await db.commit()
        other_token = AuthUtils.create_access_token({"sub": str(other.id), "auth_method": "password"})
    second = await login(bridge, other_token)
    assert first["id"] != second["id"]
    assert (await personal_devices("second-person"))["devices"] == []
    assert [row["id"] for row in (await personal_devices("stable-person"))["devices"]] == [device["id"]]
    await login(bridge, scenario["token"])
    revoked = await http.post(platform_path + "/revoke", json={})
    assert revoked.status_code == 200
    await login(bridge, scenario["token"])
    assert (await personal_devices("stable-person"))["state"] == "login_required"
    reconnected = await http.post(platform_path + "/reconnect", json={})
    assert reconnected.status_code == 200
    new_platform = reconnected.json()
    assert new_platform["id"] != platform["id"]
    assert new_platform["policy"] == {"mode": "all", "capabilities": ["screen", "input", "shell", "files", "browser"], "overrides": []}
    current = (await http.get(connection_path)).raise_for_status().json()[list_key]
    retired = next(connection for connection in current if connection["id"] == platform["id"])
    assert retired["revoked_at"] is not None
    assert {row["id"] for row in (await personal_devices("stable-person"))["devices"]} == {device["id"], excluded_device["id"]}
    async with scenario["db"]() as db:
        user = await db.get(User, scenario["id"])
        user.role = "superadmin"
        await db.commit()
    admin_overview = await scenario["http"].get("/api/devices/overview", headers=headers)
    assert admin_overview.status_code == 404
    options = await scenario["http"].get("/api/system/config/options", headers=headers)
    assert options.status_code == 200
    assert "fixture-admin-token-" not in options.text


async def test_startup_removes_retired_admin_secret_before_options_are_served(bridge):
    """真实启动同步清理历史凭据，HTTP 与持久行都不再包含该字段。"""
    from yuxi.bootstrap.options import ensure_options_in_db

    scenario = bridge["scenario"]
    async with scenario["db"]() as db:
        option = await db.scalar(select(ConfigOption).where(ConfigOption.key == "abridge_options"))
        option.value = {"url": bridge["origin"], "admin_token": "retired-private-token"}
        option.params = {"fields": [{"key": "url"}, {"key": "admin_token", "sensitive": True}]}
        user = await db.get(User, scenario["id"])
        user.role = "superadmin"
        await db.commit()
        await ensure_options_in_db(db)
        await db.commit()
        # 再次同步证明清理幂等且保留实际连接配置。
        await ensure_options_in_db(db)
        await db.commit()
    async with scenario["db"]() as db:
        option = await db.scalar(select(ConfigOption).where(ConfigOption.key == "abridge_options"))
        assert option.value == {"url": bridge["origin"]}
        assert {field["key"]: field["default"] for field in option.params["fields"]} == {
            "url": "",
            "allow_shadow_managed_browser": False,
        }
    headers = {"Authorization": "Bearer " + scenario["token"]}
    response = await scenario["http"].get("/api/system/config/options", headers=headers)
    assert response.status_code == 200
    assert "retired-private-token" not in response.text
    assert "admin_token" not in response.text
    rejected = await scenario["http"].put(
        "/api/system/config/options/abridge_options", headers=headers, json={"value": {"admin_token": "new-secret"}}
    )
    assert rejected.status_code == 400
    async with scenario["db"]() as db:
        option = await db.scalar(select(ConfigOption).where(ConfigOption.key == "abridge_options"))
        assert option.value == {"url": bridge["origin"]}


async def test_personal_cube_connection_is_an_ordinary_device(bridge, monkeypatch):
    """Yuxi 登记的设备由 aBridge 本人账号拥有，管理与运行读取同一普通授权。"""
    origin = bridge["origin"]
    scenario = bridge["scenario"]
    product = scenario["http"]
    headers = {"Authorization": "Bearer " + scenario["token"]}
    endpoint = "/api/devices/sandbox-browser"
    monkeypatch.setenv("ABRIDGE_ALLOW_INSECURE_HTTP", "true")

    class CubeGuest:
        """替换 VM，使用真实一次性兑换和浏览器设备传输。"""

        backend = "cube"
        socket = None
        binding = None
        key = None

        def prepare_instance(self, uid):
            """复用已登记的本人测试环境。"""
            assert uid == "stable-person"
            return 1, "personal-cube"

        def run_guest_program(self, uid, generation, *, user, command, arguments, timeout, stdin_data=None, touch_idle=False):
            """旧版来宾状态与私有输入协议保持可用。"""
            assert (uid, generation) == ("stable-person", 1)
            if command == "/usr/bin/test":
                assert user == "user" and arguments == ["-r", "/opt/yuxi-managed-browser/service-v2"] and stdin_data is None
                return 0, b""
            assert command == "/usr/local/bin/python3" and user == "managed-browser"
            assert arguments[:2] == ["-I", "/opt/yuxi-managed-browser/abridge-client-browser"]
            action, handoff = arguments[-1], stdin_data
            assert touch_idle == (action == "start")
            if action == "start":
                requested = json.loads(handoff)
                assert requested.pop("browser_origin") == requested.pop("http_confirmed_origin") == origin
                ticket = requested.pop("bootstrap_token", None)
                if ticket is not None:
                    assert self.binding is None
                    with httpx.Client(base_url=origin, trust_env=False, timeout=5) as guest_http:
                        redeemed = (
                            guest_http.post(
                                "/api/managed-browsers/redeem",
                                json={"device_id": requested["device_id"], "bootstrap_token": ticket},
                            )
                            .raise_for_status()
                            .json()
                        )
                    assert redeemed["device_id"] == requested["device_id"]
                    assert redeemed["kind"] == "browser" and redeemed["capabilities"] == ["browser"]
                    self.key = redeemed["key"]
                    self.binding = requested
                assert self.binding == requested
                self.socket = connect_websocket(
                    origin.replace("http:", "ws:") + "/ws/browser",
                    origin="chrome-extension://" + "a" * 32,
                    proxy=None,
                    open_timeout=5,
                    close_timeout=1,
                )
                self.socket.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "protocol": "abridge/1",
                            "platform": "Browser extension",
                            "version": "0.5.0",
                            "paused": False,
                            "instance_id": "personal-cube",
                            "device_key": self.key,
                            "capabilities": {"browser": True},
                        }
                    )
                )
                assert json.loads(self.socket.recv(timeout=5))["type"] == "welcome"
            if self.binding is None:
                return 0, b'{"status":"unbound"}'
            return 0, json.dumps(
                {
                    **self.binding,
                    "browser_origin": origin,
                    "status": "running" if self.socket else "bound",
                    "connected": self.socket is not None,
                }
            ).encode()

        def disconnect(self):
            """关闭来宾连接，保留同一 Browser Device Key。"""
            if self.socket is not None:
                self.socket.close()
                self.socket = None

    guest = CubeGuest()
    monkeypatch.setattr("yuxi.modules.extensions.plugins.abridge.managed_browser.get_sandbox_provider", lambda: guest)
    async with scenario["db"]() as db:
        await db.run_sync(lambda session: UserSandbox.__table__.create(session.connection()))
        db.add(
            UserSandbox(
                uid="stable-person", generation=1, sandbox_id="personal-cube", state="running", operation_id="fixture", envd_token="fixture"
            )
        )
        await db.commit()

    try:
        assert (await product.get(endpoint, headers=headers)).raise_for_status().json() == {"status": "not_connected"}
        guest.binding = {"device_id": "unrelated-browser", "revision": 1}
        conflict = await product.post(endpoint, headers=headers)
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "abridge_managed_browser_binding_changed"
        assert (await product.get("/api/devices", headers=headers)).raise_for_status().json()["state"] == "login_required"

        guest.binding = None
        connected = await product.post(endpoint, headers=headers, json={"uid": "other-person", "name": "Caller input"})
        assert connected.raise_for_status().json() == {"status": "ready"}
        device_id = guest.binding["device_id"]

        shown = (await product.get("/api/devices", headers=headers)).raise_for_status().json()["devices"]
        assert [(row["id"], row["status"], row["kind"]) for row in shown] == [(device_id, "online", "browser")]
        runtime = await resolve_runtime_mcp_tools(["abridge"], uid="stable-person")
        devices_list = next(tool for tool in runtime["abridge"] if tool.name == "devices_list")
        call = {"type": "tool_call", "name": "devices_list", "id": "list-personal-devices", "args": {}}
        listed = await devices_list.ainvoke(call)
        assert listed.status == "success"
        runtime_devices = json.loads(listed.content[0]["text"])["devices"]
        assert [(row["id"], row["status"], row["kind"]) for row in runtime_devices] == [(device_id, "online", "browser")]

        await asyncio.to_thread(guest.disconnect)
        assert (await product.get(endpoint, headers=headers)).raise_for_status().json() == {"status": "offline"}
        assert (await product.post(endpoint, headers=headers)).raise_for_status().json() == {"status": "ready"}
        assert guest.binding == {"device_id": device_id, "revision": 1}

        account = await login(bridge, scenario["token"])
        assert account["username"].partition(" · ")[0] == "Person"
        http = bridge["http"]
        owned = (await http.get("/api/devices")).raise_for_status().json()["devices"]
        assert [row["id"] for row in owned] == [device_id]
        (platform,) = (await http.get("/api/agent-connections")).raise_for_status().json()["connections"]
        update = {"name": platform["name"], "policy": {"mode": "selected", "devices": []}}
        (await http.patch(f"/api/agent-connections/{platform['id']}", json=update)).raise_for_status()
        assert (await product.get("/api/devices", headers=headers)).raise_for_status().json()["devices"] == []
        narrowed = await devices_list.ainvoke(call)
        assert narrowed.status == "success" and json.loads(narrowed.content[0]["text"])["devices"] == []

        # 旧本地绑定丢失后的实际换钥也不得恢复平台授权。
        platform_path = f"/api/agent-connections/{platform['id']}"
        (await http.post(platform_path + "/revoke", json={})).raise_for_status()
        previous_key = guest.key
        await asyncio.to_thread(guest.disconnect)
        guest.binding = guest.key = None
        assert (await product.post(endpoint, headers=headers)).raise_for_status().json() == {"status": "ready"}
        assert guest.binding == {"device_id": device_id, "revision": 2}
        assert (await product.get("/api/devices", headers=headers)).raise_for_status().json()["state"] == "login_required"
        assert (await http.get("/api/device", headers={"Authorization": "Bearer " + previous_key})).status_code == 401

        (await http.post(f"/api/devices/{device_id}/revoke", json={})).raise_for_status()
        assert (await product.get(endpoint, headers=headers)).raise_for_status().json() == {"status": "revoked"}
        assert (await product.post(endpoint, headers=headers)).status_code == 403
    finally:
        await asyncio.to_thread(guest.disconnect)
