"""工具显示名 HTTP 管理员权限边界。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.routers import tool_router
from server.utils.auth_middleware import get_required_user


def test_only_admin_can_write_display_name(monkeypatch):
    """普通用户直接PUT也拒绝；管理员持稳定slug调用。"""
    write = AsyncMock()
    monkeypatch.setattr(tool_router, "set_display_name", write)
    app = FastAPI()
    app.include_router(tool_router.tools)
    user = SimpleNamespace(uid="u", role="user")
    app.dependency_overrides[get_required_user] = lambda: user
    with TestClient(app) as client:
        assert client.put("/system/tools/tool_slug/display-name", json={"name": "显示名"}).status_code == 403
        write.assert_not_called()
        user.role = "admin"
        assert client.put("/system/tools/tool_slug/display-name", json={"name": "显示名"}).status_code == 200
        write.assert_awaited_once_with("tool_slug", "显示名", "u")


def test_mcp_display_name_admin_and_narrow_response(monkeypatch):
    """MCP窄路由拒绝普通用户，返回不带连接密钥。"""
    from server.routers import mcp_router
    from server.utils.auth_middleware import get_db
    from yuxi.services import mcp_display_service

    write = AsyncMock(return_value={"slug": "stable", "name": "显示名"})
    monkeypatch.setattr(mcp_display_service, "set_mcp_display_name", write)
    app = FastAPI()
    app.include_router(mcp_router.mcp)
    user = SimpleNamespace(uid="u", role="user")
    db = object()
    app.dependency_overrides[get_required_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        url = "/system/mcp-servers/stable/display-name"
        assert client.put(url, json={"name": "显示名"}).status_code == 403
        write.assert_not_called()
        user.role = "admin"
        response = client.put(url, json={"name": "显示名"})
        assert response.status_code == 200
        assert response.json() == {"success": True, "data": {"slug": "stable", "name": "显示名"}}
        write.assert_awaited_once_with(db, "stable", "显示名", "u")
