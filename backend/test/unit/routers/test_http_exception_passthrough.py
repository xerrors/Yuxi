"""验证路由保留显式 HTTP 错误与已有业务异常映射。"""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient

import yuxi.api.routers.extensions.mcp as mcp_router
import yuxi.api.routers.extensions.skills as skill_router
import yuxi.api.routers.identity.auth as auth_router
import yuxi.api.routers.knowledge.dashboard as knowledge_dashboard_router
import yuxi.api.routers.knowledge.evaluation as knowledge_eval_router
import yuxi.api.routers.knowledge.graphs as graph_router
import yuxi.api.routers.knowledge.management as knowledge_router
import yuxi.api.routers.models as model_provider_router
import yuxi.api.routers.system as system_router
import yuxi.modules.knowledge.services.document_actions as document_actions


@pytest.mark.parametrize("status_code", [403, 404, 429])
@pytest.mark.parametrize(
    ("endpoint", "kwargs", "target", "method"),
    [
        (
            knowledge_router.configure_graph_build,
            {"kb_id": "kb-1", "data": {}},
            knowledge_router.KnowledgeGraphService,
            "configure",
        ),
        (
            knowledge_router.get_knowledge_bases,
            {},
            knowledge_router.knowledge_base_manager,
            "get_knowledge_bases_by_uid",
        ),
        (
            knowledge_eval_router.list_evaluation_datasets,
            {"kb_id": "kb-1"},
            knowledge_eval_router.EvaluationService,
            "list_datasets",
        ),
        (graph_router.get_graphs, {}, graph_router.knowledge_base, "get_knowledge_bases_by_uid"),
        (
            knowledge_dashboard_router.read_knowledge_stats,
            {},
            knowledge_dashboard_router,
            "get_knowledge_stats",
        ),
        (
            skill_router.get_skill_dependency_options_route,
            {"slug": "private", "db": None},
            skill_router,
            "get_manageable_skill_or_raise",
        ),
        (mcp_router.get_mcp_servers, {"db": None}, mcp_router, "get_all_mcp_servers"),
        (system_router.reload_info_config, {}, system_router, "load_info_config"),
        (
            model_provider_router.get_model_status_by_spec,
            {"spec": "provider/model"},
            model_provider_router,
            "test_model_status_by_spec",
        ),
        (
            auth_router.upload_user_avatar,
            {"file": UploadFile(filename="avatar.png", file=BytesIO(b"image")), "db": None},
            auth_router.user_service,
            "upload_avatar",
        ),
    ],
)
@pytest.mark.asyncio
async def test_route_preserves_http_exception(monkeypatch, status_code, endpoint, kwargs, target, method):
    """服务拒绝须保留同一异常对象，包含结构化详情和重试响应头。"""
    error = HTTPException(status_code, detail={"code": "service_rejected"}, headers={"Retry-After": "30"})
    monkeypatch.setattr(target, method, AsyncMock(side_effect=error))

    with pytest.raises(HTTPException) as caught:
        await endpoint(**kwargs, current_user=SimpleNamespace(uid="user-1", id=1, role="admin"))

    assert caught.value is error


@pytest.mark.asyncio
async def test_nested_mcp_connection_preserves_http_exception(monkeypatch):
    """内层连接捕获不能把 429 改写成 500。"""
    error = HTTPException(429, "请求过于频繁", headers={"Retry-After": "30"})
    monkeypatch.setattr(mcp_router, "get_server_or_404", AsyncMock(return_value=SimpleNamespace(auth_mode="headers")))
    monkeypatch.setattr(mcp_router, "inspect_mcp_server_tools", AsyncMock(side_effect=error))

    with pytest.raises(HTTPException) as caught:
        await mcp_router.test_mcp_server("remote", current_user=SimpleNamespace(uid="user-1", role="superadmin"), db=None)

    assert caught.value is error


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.asyncio
async def test_document_submission_preserves_http_exception(monkeypatch, pending):
    """请求共用的提交函数不能把 HTTP 拒绝包装成成功状态码。"""
    error = HTTPException(503, "队列不可用", headers={"Retry-After": "30"})
    monkeypatch.setattr(document_actions, "submit_knowledge_job", AsyncMock(side_effect=error))
    kwargs = {
        "kb_id": "kb-1",
        "params": {},
        "operator_id": "user-1",
        "kb_info": SimpleNamespace(name="测试知识库", pending_parse_count=1),
        "action": "parse",
    }

    with pytest.raises(HTTPException) as caught:
        if pending:
            await document_actions.submit_pending_document_action(**kwargs)
        else:
            await document_actions.submit_document_action(**kwargs, file_ids=["file-1"])

    assert caught.value is error


@pytest.mark.asyncio
async def test_document_download_preserves_inner_http_exception(monkeypatch):
    """下载内层不能将 HTTP 拒绝改成 StorageError 再转换为 500。"""
    error = HTTPException(404, "对象不存在")
    monkeypatch.setattr(knowledge_router, "_ensure_knowledge_base_supports_documents", AsyncMock())
    monkeypatch.setattr(
        knowledge_router.documents,
        "load_file_meta",
        AsyncMock(return_value={"path": "http://minio:9000/documents/file.txt"}),
    )
    monkeypatch.setattr(knowledge_router.documents, "is_minio_url", lambda _path: True)
    monkeypatch.setattr(knowledge_router.documents, "parse_minio_url", lambda _path: ("documents", "file.txt"))
    client = SimpleNamespace(adownload_response=AsyncMock(side_effect=error))
    monkeypatch.setattr(knowledge_router.documents, "get_minio_client", lambda: client)

    with pytest.raises(HTTPException) as caught:
        await knowledge_router.download_document("kb-1", "file-1", current_user=SimpleNamespace(uid="user-1"))

    assert caught.value is error


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (HTTPException(429, {"code": "busy"}, headers={"Retry-After": "30"}), 429, {"code": "busy"}),
        (ValueError("参数无效"), 400, "参数无效"),
        (ValueError("配置已锁定"), 409, "配置已锁定"),
        (RuntimeError("服务故障"), 500, "配置图谱构建失败: 服务故障"),
    ],
)
def test_graph_configuration_error_response(monkeypatch, error, status_code, detail):
    """通过实际 Router 检查响应协议，保持非 HTTP 异常原有映射。"""
    app = FastAPI()
    app.include_router(knowledge_router.knowledge)
    app.dependency_overrides[knowledge_router.require_knowledge_base_manage] = lambda: SimpleNamespace(uid="user-1")
    monkeypatch.setattr(knowledge_router.KnowledgeGraphService, "configure", AsyncMock(side_effect=error))

    response = TestClient(app).post("/knowledge/knowledge-bases/kb-1/graph-build/config", json={})

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    if isinstance(error, HTTPException):
        assert response.headers["Retry-After"] == "30"


@pytest.mark.parametrize(
    ("path", "method", "asynchronous"),
    [
        ("/knowledge/knowledge-bases", "get_knowledge_bases_by_uid", True),
        ("/knowledge/knowledge-bases/accessible", "get_knowledge_bases_by_uid", True),
        ("/knowledge/types", "get_supported_kb_types", False),
        ("/knowledge/stats", "get_statistics", True),
    ],
)
def test_knowledge_read_failure_is_not_empty_success(monkeypatch, path, method, asynchronous):
    """读取故障返回 500，避免前端把不可用误判成空知识库。"""
    app = FastAPI()
    app.include_router(knowledge_router.knowledge)
    user = SimpleNamespace(uid="user-1", role="admin")
    app.dependency_overrides[knowledge_router.get_admin_user] = lambda: user
    app.dependency_overrides[knowledge_router.get_required_user] = lambda: user
    mock_type = AsyncMock if asynchronous else Mock
    monkeypatch.setattr(knowledge_router.knowledge_base_manager, method, mock_type(side_effect=RuntimeError("storage down")))

    response = TestClient(app).get(path)

    assert response.status_code == 500
    assert set(response.json()) == {"detail"}
