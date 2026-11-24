"""真实 HTTP 与数据库验证个人浏览器入口的登录边界。"""

import hashlib

import pytest
from fastapi import FastAPI

from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.extensions.devices import devices
from yuxi.modules.extensions.plugins.abridge.managed_browser import PersonalBrowserStatus
from yuxi.modules.identity.models import APIKey, Department, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.shared.errors import DomainError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]


async def test_personal_browser_requires_login_and_cannot_target_another_user(monkeypatch):
    """带完整权限的 Key 也无法走个人入口，查询和连接只接收当前主体。"""
    calls = []

    async def status(uid):
        calls.append(("status", uid))
        return PersonalBrowserStatus(status="not_connected")

    async def provision(uid):
        calls.append(("connect", uid))
        return PersonalBrowserStatus(status="connecting")

    monkeypatch.setattr("yuxi.api.routers.extensions.devices.personal_browser_status", status)
    monkeypatch.setattr("yuxi.api.routers.extensions.devices.provision_personal_browser", provision)
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(devices, prefix="/api")
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions() as db:
            department = Department(name="Personal browser test")
            db.add(department)
            await db.flush()
            owner = User(uid="browser-owner", username="browser-owner", department_id=department.id, password_hash="!disabled")
            db.add(owner)
            await db.flush()
            db.add(
                APIKey(
                    key_hash=hashlib.sha256(b"yxkey_browser_full").hexdigest(),
                    key_prefix="yxkey_",
                    name="full",
                    user_id=owner.id,
                    access_level="full",
                    app_id="browser-app",
                    created_by=owner.uid,
                )
            )
            await db.commit()
        header = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": str(owner.id), "auth_method": "password"})}
        api_key = {"Authorization": "Bearer yxkey_browser_full"}
        endpoint = "/api/devices/sandbox-browser"
        async with serve_http(app) as client:
            assert (await client.get(endpoint)).status_code == 401
            assert (await client.get(endpoint, headers=api_key)).status_code == 403
            assert (await client.post(endpoint, headers=api_key)).status_code == 403
            assert calls == []

            result = await client.get(endpoint + "?uid=somebody-else", headers=header)
            assert result.status_code == 200 and result.json() == {"status": "not_connected"}
            result = await client.post(endpoint, json={"uid": "somebody-else"}, headers=header)
            assert result.status_code == 202 and result.json() == {"status": "connecting"}
            assert calls == [("status", owner.uid), ("connect", owner.uid)]
