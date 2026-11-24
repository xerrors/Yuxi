"""可选源码探针：真实 Yuxi Provider、aBridge Server 与设备产品入口。"""

import asyncio
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

from test.integration.services.test_oidc_provider import scenario as provider_scenario  # noqa: F401
from yuxi.api.routers.extensions.devices import devices as device_router
from yuxi.api.routers.system import system
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.extensions.abridge.options import abridge_options
from yuxi.modules.extensions.abridge.service import personal_devices
from yuxi.modules.extensions.mcp.models import MCPConnection, MCPServer
from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools
from yuxi.modules.identity.models import User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.system.models import ConfigOption

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def bridge(provider_scenario, monkeypatch, tmp_path):  # noqa: F811
    """只启动指定开发源码，数据库、端口和管理员凭证均为测试自有。"""
    scenario = provider_scenario
    monkeypatch.setenv("YUXI_OIDC_PROVIDER_CLIENT_ID", "abridge")
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
    grants = (await http.get("/api/client-grants")).json()["grants"]
    platform = next(grant for grant in grants if grant["name"] == "abridge 平台连接")
    assert platform["policy"] == {"mode": "all", "capabilities": ["screen", "input", "shell", "files", "browser"], "overrides": []}
    excluded = await http.post("/api/devices", json={"name": "Excluded desktop", "kind": "computer", "capabilities": ["files"]})
    assert excluded.status_code == 200
    excluded_device = excluded.json()
    assert {row["id"] for row in (await personal_devices("stable-person"))["devices"]} == {device["id"], excluded_device["id"]}
    policy = {"mode": "selected", "devices": [{"device_id": device["id"], "capabilities": ["files"]}]}
    updated = await http.patch("/api/client-grants/" + platform["id"], json={"name": platform["name"], "policy": policy})
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
    revoked = await http.post("/api/client-grants/" + platform["id"] + "/revoke", json={})
    assert revoked.status_code == 200
    await login(bridge, scenario["token"])
    assert (await personal_devices("stable-person"))["state"] == "login_required"
    reconnected = await http.post("/api/client-grants/" + platform["id"] + "/reconnect", json={})
    assert reconnected.status_code == 200
    new_platform = reconnected.json()
    assert new_platform["id"] != platform["id"]
    assert new_platform["policy"] == {"mode": "all", "capabilities": ["screen", "input", "shell", "files", "browser"], "overrides": []}
    current_grants = (await http.get("/api/client-grants")).json()["grants"]
    retired = next(grant for grant in current_grants if grant["id"] == platform["id"])
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
        assert [field["key"] for field in option.params["fields"]] == ["url"]
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
