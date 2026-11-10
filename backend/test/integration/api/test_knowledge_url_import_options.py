"""真实配置 HTTP、PostgreSQL 与 Redis 验证 URL 导入策略的保存和执行。"""

import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import Header
from sqlalchemy import select

from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.dependencies.auth import get_current_user
from yuxi.api.main import app
from yuxi.bootstrap.options import ensure_options_in_db
from yuxi.modules.knowledge.options import knowledge_url_import_policy
from yuxi.modules.knowledge.utils import url_fetcher
from yuxi.modules.system.models import ConfigOption
from yuxi.modules.system.services import options

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]
OPTION_PATH = f"/api/system/config/options/{knowledge_url_import_policy.key}"
FETCH_PATH = "/api/knowledge/files/fetch-url"
SUPERADMIN = {"X-Test-Role": "superadmin"}


@pytest_asyncio.fixture
async def policy_http(monkeypatch):
    """隔离数据库和缓存，只替换可信身份入口，保留实际角色校验。"""
    prefix = f"pytest:url_import:{uuid.uuid4().hex}:"
    monkeypatch.setattr(options, "OPTION_CACHE_PREFIX", prefix + "value:")
    monkeypatch.setattr(options, "OPTION_CACHE_VERSION_PREFIX", prefix + "version:")
    monkeypatch.setenv("YUXI_URL_WHITELIST", "legacy.example")

    async def actor(x_test_role: str | None = Header(None)):
        """提供固定测试身份，授权仍由生产依赖执行。"""
        if x_test_role is None:
            return None
        return SimpleNamespace(uid="url-policy-fixture", username="fixture-superadmin", role=x_test_role, department_id=1)

    monkeypatch.setattr(app, "dependency_overrides", {get_current_user: actor})
    async with isolated_audit_store(monkeypatch) as (manager, sessions):
        async with manager.get_async_session_context() as db:
            await ensure_options_in_db(db)
        try:
            async with serve_http(app) as client:
                yield client, sessions
        finally:
            redis = await options.get_async_redis_client()
            await redis.delete(prefix + "value:" + knowledge_url_import_policy.key, prefix + "version:" + knowledge_url_import_policy.key)


async def test_save_policy_controls_next_fetch_and_explicit_empty_overrides_environment(policy_http, monkeypatch):
    """回读数据库、实际内容及拒绝协议，证明缓存刷新且空列表关闭导入。"""
    client, sessions = policy_http
    before = await client.get("/api/system/config/options", headers=SUPERADMIN)
    assert before.status_code == 200, before.text
    option = next(item for item in before.json()["options"] if item["key"] == knowledge_url_import_policy.key)
    assert option["value"] == {}
    assert option["effective_value"] == {"allowed_hosts": ["legacy.example"]}
    assert await knowledge_url_import_policy.get() == {"allowed_hosts": ["legacy.example"]}

    saved = await client.put(OPTION_PATH, headers=SUPERADMIN, json={"value": {"allowed_hosts": ["allowed.test"]}})
    assert saved.status_code == 200, saved.text
    async with sessions() as db:
        record = await db.scalar(select(ConfigOption).where(ConfigOption.key == knowledge_url_import_policy.key))
        assert record.value == {"allowed_hosts": ["allowed.test"]}
        assert record.updated_by == "fixture-superadmin"

    requests = []
    html = b"<html><body>URL policy fixture</body></html>"

    def respond(request):
        """网络边界提供固定内容与越权跳转，不触碰真实网页和对象存储。"""
        requests.append(str(request.url))
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "https://blocked.test/private"})
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=html)

    monkeypatch.setattr(url_fetcher, "SSRFGuardTransport", lambda: httpx.MockTransport(respond))
    content, final_url = await url_fetcher.fetch_url_content("https://allowed.test/page")
    assert content == html and final_url == "https://allowed.test/page"
    redirect = await client.post(FETCH_PATH, headers=SUPERADMIN, json={"url": "https://allowed.test/redirect"})
    assert redirect.status_code == 400, redirect.text
    assert redirect.json()["detail"] == {
        "code": "url_fetch_rejected",
        "message": "重定向 URL 校验失败：网址域名不在 URL 导入白名单中，请联系管理员配置",
    }
    assert requests == ["https://allowed.test/page", "https://allowed.test/redirect"]

    cleared = await client.put(OPTION_PATH, headers=SUPERADMIN, json={"value": {"allowed_hosts": []}})
    assert cleared.status_code == 200, cleared.text
    reloaded = await client.get("/api/system/config/options", headers=SUPERADMIN)
    option = next(item for item in reloaded.json()["options"] if item["key"] == knowledge_url_import_policy.key)
    assert option["value"] == option["effective_value"] == {"allowed_hosts": []}
    async with sessions() as db:
        assert await db.scalar(select(ConfigOption.value).where(ConfigOption.key == knowledge_url_import_policy.key)) == {
            "allowed_hosts": []
        }
    rejected = await client.post(FETCH_PATH, headers=SUPERADMIN, json={"url": "https://legacy.example/page"})
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["detail"] == {"code": "url_fetch_rejected", "message": "URL 导入未启用，请联系管理员配置域名白名单"}
    assert requests == ["https://allowed.test/page", "https://allowed.test/redirect"]


@pytest.mark.parametrize(("role", "status"), [(None, 401), ("user", 403), ("admin", 403)])
async def test_only_superadmin_can_read_or_change_import_policy(policy_http, role, status):
    """普通角色与匿名写入均拒绝，数据库策略不变。"""
    client, sessions = policy_http
    headers = {} if role is None else {"X-Test-Role": role}
    read = await client.get("/api/system/config/options", headers=headers)
    saved = await client.put(OPTION_PATH, headers=headers, json={"value": {"allowed_hosts": ["attacker.test"]}})
    assert read.status_code == saved.status_code == status
    async with sessions() as db:
        assert await db.scalar(select(ConfigOption.value).where(ConfigOption.key == knowledge_url_import_policy.key)) == {}


@pytest.mark.parametrize("hosts", ["allowed.test", [123], None])
async def test_invalid_list_does_not_change_persisted_policy(policy_http, hosts):
    """配置边界拒绝错误列表类型，事务回滚不留下不可执行的策略。"""
    client, sessions = policy_http
    rejected = await client.put(OPTION_PATH, headers=SUPERADMIN, json={"value": {"allowed_hosts": hosts}})
    assert rejected.status_code == 400, rejected.text
    async with sessions() as db:
        assert await db.scalar(select(ConfigOption.value).where(ConfigOption.key == knowledge_url_import_policy.key)) == {}
