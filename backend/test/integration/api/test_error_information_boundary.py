"""真实 TCP HTTP 验证未知异常与提供者诊断不会带出内部细节。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from test.integration.support.module_audit import serve_http
from yuxi.api.dependencies.auth import get_admin_user, get_db, get_required_user, get_superadmin_user
from yuxi.api.dependencies.knowledge import require_knowledge_base_manage, require_knowledge_base_read
from yuxi.api.main import app
from yuxi.api.routers import models as model_routes
from yuxi.api.routers.knowledge import management as knowledge_routes
from yuxi.modules.documents import service as document_service
from yuxi.modules.knowledge.options import knowledge_url_import_policy
from yuxi.modules.knowledge.utils import url_fetcher
from yuxi.modules.models.providers.cache import model_cache
from yuxi.shared.errors import StateConflictError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]
PRIVATE_DETAIL = "AUDIT_ONLY internal.example.test/config api_key=fixture-private-value"
BASE = "/api/knowledge/knowledge-bases/kb_fixture"


@pytest_asyncio.fixture
async def error_http(monkeypatch):
    """启动实际应用；仅隔离身份与无需使用的数据库依赖。"""
    user = SimpleNamespace(uid="audit-user", role="superadmin")

    async def url_policy():
        """错误文案测试隔离配置持久化，配置保存由独立集成测试验证。"""
        return knowledge_url_import_policy.resolve({})

    monkeypatch.setattr(url_fetcher, "knowledge_url_import_policy", SimpleNamespace(get=url_policy))

    async def database():
        """本例不访问持久化，未知失败由业务调用边界注入。"""
        yield SimpleNamespace()

    monkeypatch.setattr(
        app,
        "dependency_overrides",
        {
            get_required_user: lambda: user,
            get_admin_user: lambda: user,
            get_superadmin_user: lambda: user,
            require_knowledge_base_read: lambda: user,
            require_knowledge_base_manage: lambda: user,
            get_db: database,
        },
    )
    monkeypatch.setattr(knowledge_routes, "_ensure_knowledge_base_supports_documents", AsyncMock(return_value=SimpleNamespace()))
    async with serve_http(app) as client:
        yield client


@pytest.mark.parametrize(
    ("method", "suffix", "target", "payload", "expected_status"),
    [
        ("POST", "/query", "retrieve", {"query": "fixture", "meta": {}}, 500),
        ("GET", "/query-params", "get_kb_query_params_config", None, 500),
        ("PUT", "/query-params", "update_kb_query_params", {"top_k": 4}, 500),
        ("POST", "/sample-questions", "generate_knowledge_base_sample_questions", {}, 500),
        ("GET", "/sample-questions", "get_knowledge_base_sample_questions", None, 500),
        ("POST", "/folders", "create_folder", {"folder_name": "fixture"}, 500),
        ("POST", "/documents/parse", "submit_document_action", {"file_ids": ["file_fixture"]}, 500),
        ("POST", "/documents/parse-pending", "submit_pending_document_action", {}, 500),
        ("PUT", "/folders/folder_fixture/rename", "rename_folder", {"folder_name": "fixture"}, 500),
        ("PUT", "/documents/doc_fixture/move", "move_file", {"new_parent_id": None}, 500),
        ("POST", "/api/knowledge/files/fetch-url", "fetch_url_content", {"url": "https://example.test"}, 500),
    ],
)
async def test_unknown_knowledge_failure_hides_internal_detail(error_http, monkeypatch, method, suffix, target, payload, expected_status):
    """独立人工敏感标记不得出现在真实 HTTP 响应中。"""
    local_targets = {
        "generate_knowledge_base_sample_questions",
        "get_knowledge_base_sample_questions",
        "submit_document_action",
        "submit_pending_document_action",
        "fetch_url_content",
    }
    owner = knowledge_routes if target in local_targets else knowledge_routes.knowledge_base_manager
    failure = AsyncMock(side_effect=RuntimeError(PRIVATE_DETAIL))
    monkeypatch.setattr(owner, target, failure)
    path = suffix if suffix.startswith("/api/") else BASE + suffix
    response = await error_http.request(method, path, json=payload)
    failure.assert_awaited_once()
    assert response.status_code == expected_status, response.text
    assert PRIVATE_DETAIL not in response.text
    if response.headers.get("content-type", "").startswith("application/json"):
        assert response.json()["detail"]
    else:
        assert response.text == "Internal Server Error"


@pytest.mark.parametrize("suffix", ["/query", "/documents/parse", "/documents/parse-pending"])
async def test_known_domain_rejection_keeps_public_meaning(error_http, monkeypatch, suffix):
    """已知领域错误继续使用原有状态码和可公开文本。"""
    error = StateConflictError("已有任务，请稍后重试")
    monkeypatch.setattr(knowledge_routes.knowledge_base_manager, "retrieve", AsyncMock(side_effect=error))
    monkeypatch.setattr(knowledge_routes, "submit_document_action", AsyncMock(side_effect=error))
    monkeypatch.setattr(knowledge_routes, "submit_pending_document_action", AsyncMock(side_effect=error))
    payload = {"query": "fixture", "meta": {}} if suffix == "/query" else {"file_ids": ["file_fixture"]}
    response = await error_http.post(BASE + suffix, json=payload)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "已有任务，请稍后重试"}


@pytest.mark.parametrize("suffix", ["/query", "/query-test"])
async def test_query_preserves_top_level_scoring_and_highlights(error_http, monkeypatch, suffix):
    """管理检索保留旧调用方所需的顶层字段与标准来源身份。"""
    highlights = [[{"text": "命中", "matched": True}]]
    metadata = {"score": 0, "score_type": "cosine", "highlights": highlights}
    chunk = {"id": "chunk", "kb_id": "kb_fixture", "file_id": "file", "content": "命中", "metadata": metadata}
    monkeypatch.setattr(knowledge_routes.knowledge_base_manager, "retrieve", AsyncMock(return_value={"results": [chunk]}))
    response = await error_http.post(BASE + suffix, json={"query": "命中"})
    assert response.status_code == 200, response.text
    results = response.json()["result"] if suffix == "/query" else response.json()
    assert results == [{**chunk, "score": 0, "score_type": "cosine", "highlights": highlights}]


@pytest.mark.parametrize(
    ("error", "public_message"),
    [(RuntimeError(PRIVATE_DETAIL), "删除失败，请稍后重试"), (StateConflictError("文件正在处理中"), "文件正在处理中")],
)
async def test_partial_delete_keeps_item_results_without_internal_error(error_http, monkeypatch, error, public_message):
    """部分失败保留成功数量与文件身份，未知原因不直接发送。"""
    monkeypatch.setattr(knowledge_routes.knowledge_base_manager, "get_file_basic_info", AsyncMock(return_value={"meta": {}}))
    monkeypatch.setattr(
        knowledge_routes.knowledge_base_manager,
        "delete_file",
        AsyncMock(side_effect=[None, error]),
    )
    response = await error_http.request("DELETE", BASE + "/documents/batch", json=["file_ok", "file_failed"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_count"] == 1
    assert body["failed_items"] == [{"doc_id": "file_failed", "error": public_message}]
    assert PRIVATE_DETAIL not in response.text


@pytest.mark.parametrize("failure", ["configuration", "exception", "returned_details", "returned_unknown_status"])
async def test_logged_in_ocr_health_hides_provider_details(error_http, monkeypatch, failure):
    """配置异常、引擎异常与引擎直接返回载荷都遵守同一公开边界。"""
    kwargs = AsyncMock(side_effect=RuntimeError(PRIVATE_DETAIL)) if failure == "configuration" else AsyncMock(return_value={})
    monkeypatch.setattr(document_service, "_build_processor_kwargs", kwargs)

    def engine(*args, **kwargs):
        """返回可包含任意远端诊断文本的引擎。"""

        def health():
            """模拟引擎自行捕获异常后返回敏感 details。"""
            if failure == "exception":
                raise RuntimeError(PRIVATE_DETAIL)
            status = "unexpected-provider-state" if failure == "returned_unknown_status" else "configured"
            return {"status": status, "message": PRIVATE_DETAIL, "details": {"server_url": PRIVATE_DETAIL}}

        return SimpleNamespace(check_health=health)

    monkeypatch.setattr(document_service, "get_engine", engine)
    response = await error_http.get("/api/system/ocr/health")
    assert response.status_code == 200, response.text
    assert PRIVATE_DETAIL not in response.text
    health = response.json()["health"]
    assert set(health) == set(document_service.ENGINE_SPECS)
    expected = "configured" if failure == "returned_details" else "error"
    assert all(item["status"] == expected and item["message"] and item["details"] == {} for item in health.values())


async def test_model_diagnostic_hides_unexpected_provider_error(error_http, monkeypatch):
    """模型诊断保留身份和失败状态，不把上游异常发送给客户端。"""
    import yuxi.modules.models.chat as chat

    monkeypatch.setattr(model_cache, "get_model_info", lambda _spec: SimpleNamespace(model_type="chat"))
    monkeypatch.setattr(chat, "select_model", lambda **kwargs: SimpleNamespace(call=AsyncMock(side_effect=RuntimeError(PRIVATE_DETAIL))))
    response = await error_http.get("/api/system/model-providers/models/status", params={"spec": "fixture/model"})
    assert response.status_code == 200, response.text
    assert PRIVATE_DETAIL not in response.text
    assert response.json()["data"] == {
        "spec": "fixture/model",
        "status": "error",
        "message": "模型连接检查失败，请查看服务端日志",
        "model_type": "chat",
    }


@pytest.mark.parametrize("model_type", ["embedding", "rerank"])
@pytest.mark.parametrize("success", [False, True])
async def test_model_connection_result_hides_provider_text(error_http, monkeypatch, model_type, success):
    """连接探针的返回文本也可能含凭据，成功和失败状态保持独立。"""
    import yuxi.modules.models.embed as embed
    import yuxi.modules.models.rerank as rerank

    monkeypatch.setattr(model_cache, "get_model_info", lambda _spec: SimpleNamespace(model_type=model_type))
    model = SimpleNamespace(test_connection=AsyncMock(return_value=(success, PRIVATE_DETAIL)))
    monkeypatch.setattr(embed, "select_embedding_model", lambda _spec: model)
    monkeypatch.setattr(rerank, "get_reranker", lambda _spec: model)
    response = await error_http.get("/api/system/model-providers/models/status", params={"spec": "fixture/model"})
    assert response.status_code == 200, response.text
    assert PRIVATE_DETAIL not in response.text
    assert response.json()["data"] == {
        "spec": "fixture/model",
        "model_type": model_type,
        "status": "available" if success else "unavailable",
        "message": "连接正常" if success else "模型服务不可用，请检查供应商配置",
    }


async def test_model_status_route_hides_lookup_failure(error_http, monkeypatch):
    """模型检查尚未开始时的异常也不能绕过 HTTP 出口。"""
    monkeypatch.setattr(model_routes, "test_model_status_by_spec", AsyncMock(side_effect=RuntimeError(PRIVATE_DETAIL)))
    response = await error_http.get("/api/system/model-providers/models/status", params={"spec": "fixture/model"})
    assert response.status_code == 200, response.text
    assert PRIVATE_DETAIL not in response.text
    assert response.json()["success"] is False
    assert response.json()["data"]["status"] == "error"


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, httpx.ConnectError])
async def test_real_url_fetch_helper_hides_transport_failure(error_http, monkeypatch, error_type):
    """经过实际抓取 helper，第三方异常包装成 ValueError 也不能泄露。"""
    from yuxi.modules.knowledge.utils import url_fetcher

    monkeypatch.setenv("YUXI_URL_WHITELIST", "example.test")

    def broken_transport():
        """模拟网络组件初始化失败，位于真实 helper 的下游。"""
        raise error_type(PRIVATE_DETAIL)

    monkeypatch.setattr(url_fetcher, "SSRFGuardTransport", broken_transport)
    response = await error_http.post("/api/knowledge/files/fetch-url", json={"url": "https://example.test"})
    assert response.status_code == 400, response.text
    assert PRIVATE_DETAIL not in response.text
    detail = response.json()["detail"]
    assert detail["code"] == "url_fetch_failed"
    assert detail["message"] in {"网页抓取失败，请确认网址可访问后重试", "网页抓取失败，请稍后重试"}


@pytest.mark.parametrize(
    ("whitelist", "expected_message"),
    [
        ("", "URL 导入未启用，请联系管理员配置域名白名单"),
        ("example.test", "URL 校验失败：网址域名不在 URL 导入白名单中，请联系管理员配置"),
    ],
)
async def test_real_url_fetch_keeps_controlled_validation_message(error_http, monkeypatch, whitelist, expected_message):
    """停用与白名单拒绝通过真实 HTTP 返回现有网页可展示的业务格式。"""
    monkeypatch.setenv("YUXI_URL_WHITELIST", whitelist)
    response = await error_http.post("/api/knowledge/files/fetch-url", json={"url": "https://blocked.test"})
    assert response.status_code == 400, response.text
    assert response.json() == {"detail": {"code": "url_fetch_rejected", "message": expected_message}}


async def test_url_parser_failure_hides_internal_detail(error_http, monkeypatch):
    """可公开的校验拒绝也不能夹带底层 parser 异常正文。"""
    from yuxi.modules.knowledge.utils import url_validator

    monkeypatch.setenv("YUXI_URL_WHITELIST", "example.test")

    def broken_parser(_url):
        """以独立敏感标记模拟 URL parser 失败。"""
        raise ValueError(PRIVATE_DETAIL)

    monkeypatch.setattr(url_validator, "urlparse", broken_parser)
    response = await error_http.post("/api/knowledge/files/fetch-url", json={"url": "https://example.test"})
    assert response.status_code == 400 and PRIVATE_DETAIL not in response.text
    assert response.json() == {"detail": {"code": "url_fetch_rejected", "message": "URL 校验失败：URL 格式不正确"}}
