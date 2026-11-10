from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import yuxi.api.routers.system as system_router
from yuxi.api.dependencies.auth import get_db, get_superadmin_user
from yuxi.api.routers.system import system

pytestmark = pytest.mark.unit


def test_discovery_endpoint_is_public(monkeypatch):
    monkeypatch.setattr("yuxi.api.routers.system.get_version", lambda: "0.7.1.dev0")

    app = FastAPI()
    app.include_router(system, prefix="/api")
    response = TestClient(app).get("/api/system/discovery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Yuxi"
    assert payload["version"] == "0.7.1.dev0"
    assert payload["api_prefix"] == "/api"
    assert payload["capabilities"]["features"]["knowledge"] is True
    assert payload["capabilities"]["cli"]["browser_login"] is True
    assert payload["capabilities"]["cli"]["api_key_auth"] is True
    assert payload["capabilities"]["cli"]["agent_list"] is True
    assert payload["capabilities"]["cli"]["agent_show"] is True
    assert "kb_upload" not in payload["capabilities"]["cli"]
    assert payload["endpoints"]["cli_auth_sessions"] == "/api/auth/cli/sessions"
    assert payload["endpoints"]["readiness"] == "/api/system/ready"


def test_readiness_endpoint_returns_structured_503(monkeypatch):
    async def fake_readiness(*, startup_complete: bool, startup_components):
        assert startup_complete is False
        assert startup_components is None
        return {
            "status": "not_ready",
            "checks": {
                "startup": {"status": "error", "code": "not_complete"},
                "postgres": {"status": "ok"},
                "redis": {"status": "ok"},
            },
        }

    monkeypatch.setattr(system_router, "get_readiness", fake_readiness)
    monkeypatch.setattr(system_router, "get_version", lambda: "0.7.2.dev0")
    app = FastAPI()
    app.include_router(system, prefix="/api")

    response = TestClient(app).get("/api/system/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["version"] == "0.7.2.dev0"


def test_serialize_system_config_includes_field_metadata():
    result = system_router._serialize_system_config({"default_model": "test-provider:latest"})

    assert result["default_model"] == "test-provider:latest"
    assert result["_config_items"]["default_model"]["type"] == "model"


@pytest.mark.asyncio
async def test_brand_info_uses_packaged_template_outside_project_directory(monkeypatch, tmp_path):
    """品牌模板从包内读取，不依赖 API 进程的工作目录。"""
    monkeypatch.delenv("YUXI_BRAND_FILE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(system_router, "get_version", lambda: "0.7.3")

    config = await system_router.load_info_config()

    assert set(config) == {"branding", "footer"}
    assert config["branding"] == {
        "name": "语析",
        "logo": "/favicon.svg",
        "title": "让知识，成为行动。",
        "login_bg": "",
    }
    assert config["footer"]["copyright"].endswith("v0.7.3")


@pytest.mark.asyncio
async def test_web_provider_guard_normalizes_before_lookup(monkeypatch):
    """带空白的默认供应商不能绕过凭据检查。"""
    from yuxi.modules.extensions.options import WEB_PROVIDER_OPTIONS

    class FakeOption:
        def __init__(self, key: str):
            self.key = key

        async def get(self, db=None):
            return {"api_key": self.key}

    monkeypatch.setitem(WEB_PROVIDER_OPTIONS, "tavily", FakeOption(""))
    with pytest.raises(ValueError, match="API Key"):
        await system_router._ensure_web_provider_configured(None, {"web_search_provider": " tavily "})

    monkeypatch.setitem(WEB_PROVIDER_OPTIONS, "tavily", FakeOption("configured"))
    assert await system_router._ensure_web_provider_configured(None, {"web_search_provider": " tavily "}) is None


def test_generic_option_endpoint_rejects_system_options():
    """system_options 只能经专用系统配置入口写入。"""
    app = FastAPI()
    app.include_router(system, prefix="/api")

    async def fake_db():
        return None

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_superadmin_user] = lambda: SimpleNamespace(username="admin")
    response = TestClient(app).put(
        "/api/system/config/options/system_options",
        json={"value": {"web_search_provider": "tavily"}},
    )

    assert response.status_code == 404
