"""Public Knowledge 工具的 HTTP 错误分类。"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from server.routers.public_v1.knowledge import tool_router
from server.utils.auth_middleware import get_required_user
from yuxi.knowledge.base import KBNotFoundError
from yuxi.services.knowledge import tools as knowledge_tools


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "status_code"),
    [(KBNotFoundError("gone"), 404), (RuntimeError("storage unavailable"), 500)],
)
async def test_tool_distinguishes_deleted_resource_from_service_failure(monkeypatch, failure, status_code):
    """资源消失返回 404，未知存储故障保持 5xx。"""
    app = FastAPI()
    app.include_router(tool_router, prefix="/api/v1")
    app.dependency_overrides[get_required_user] = lambda: SimpleNamespace(uid="owner")

    async def visible(_uid):
        """提供已通过权限查询的测试资源。"""
        return [{"kb_id": "kb-1", "name": "Test", "kb_type": "milvus"}]

    async def fail_query(*_args, **_kwargs):
        """模拟可见性检查后的存储边界失败。"""
        raise failure

    monkeypatch.setattr(knowledge_tools, "visible_knowledge_bases", visible)
    monkeypatch.setattr(knowledge_tools, "query_kb", fail_query)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/knowledge/tools/query_kb",
            json={"kb_id": "kb-1", "query_text": "test"},
        )
    assert response.status_code == status_code, response.text
