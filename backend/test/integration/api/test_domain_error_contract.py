"""实际应用的领域失败响应及幂等持久化兼容性。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from test.integration.support.module_audit import serve_http
from yuxi.api.dependencies.auth import get_required_user
from yuxi.api.dependencies.knowledge import require_knowledge_base_manage, require_knowledge_base_read
from yuxi.api.main import app
from yuxi.api.routers.knowledge import management as knowledge_routes
from yuxi.api.routers.public_v1.agents import turns as query_routes
from yuxi.api.routers.public_v1.agents.auth import require_public_context
from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.shared.errors import (
    ContentTooLargeError,
    InvalidInputError,
    InvalidOperationError,
    OperationFailedError,
    OperationTimeoutError,
    PermissionDeniedError,
    ResourceNotFoundError,
    StateConflictError,
    UpstreamError,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.self_managed_resources]
pytest_plugins = ["test.integration.services.agent_input_fixtures"]


@pytest_asyncio.fixture
async def domain_http(sessions, monkeypatch):
    """运行真实应用路由与异常 handler，身份由隔离测试注入。"""
    from test.integration.services.agent_input_fixtures import SCOPE

    user = SimpleNamespace(uid=SCOPE.uid, role="admin")
    monkeypatch.setattr(
        app,
        "dependency_overrides",
        {
            require_public_context: lambda: SimpleNamespace(scope=SCOPE),
            get_required_user: lambda: user,
            require_knowledge_base_manage: lambda: user,
            require_knowledge_base_read: lambda: user,
        },
    )
    async with serve_http(app) as client:
        yield client


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (InvalidOperationError("内容不支持此操作"), 400, "内容不支持此操作"),
        (PermissionDeniedError("无权访问"), 403, "无权访问"),
        (ResourceNotFoundError("目标不存在"), 404, "目标不存在"),
        (StateConflictError("标注正在进行", code="references_busy"), 409, {"code": "references_busy", "message": "标注正在进行"}),
        (
            ContentTooLargeError("输入过长", code="references_input_too_large"),
            413,
            {"code": "references_input_too_large", "message": "输入过长"},
        ),
        (InvalidInputError("模型不存在", code="chat_model_not_found"), 422, {"code": "chat_model_not_found", "message": "模型不存在"}),
        (OperationFailedError("未产生结果"), 500, "未产生结果"),
        (
            UpstreamError("来源标注调用失败", code="references_call_failed"),
            502,
            {"code": "references_call_failed", "message": "来源标注调用失败"},
        ),
        (OperationTimeoutError("来源标注超时", code="references_timeout"), 504, {"code": "references_timeout", "message": "来源标注超时"}),
    ],
)
async def test_shipping_http_preserves_domain_error_response(domain_http, monkeypatch, error, status, detail):
    """真实 HTTP 装配保留字符串及结构化业务错误的公开表达。"""
    monkeypatch.setattr(query_routes, "get_input_snapshot", AsyncMock(side_effect=error))
    response = await domain_http.get("/api/v1/agents/sessions/input-thread/inputs/input-id")
    assert response.status_code == status
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize("method", ["post", "get"])
async def test_broad_route_catch_preserves_domain_rejection(domain_http, monkeypatch, method):
    """宽泛捕获不把已知知识用例拒绝包装成新的 500。"""
    name = "generate_knowledge_base_sample_questions" if method == "post" else "get_knowledge_base_sample_questions"
    monkeypatch.setattr(knowledge_routes, name, AsyncMock(side_effect=ResourceNotFoundError("知识库不存在")))
    response = await domain_http.request(method, "/api/knowledge/knowledge-bases/missing/sample-questions", json={})
    assert response.status_code == 404
    assert response.json() == {"detail": "知识库不存在"}


async def test_http_idempotency_conflict_preserves_one_input_and_receipt(domain_http, sessions):
    """异意图重放保持 409，普通入口只接收和消费一次。"""
    url = "/api/v1/agents/sessions/input-thread/events"
    headers = {"Idempotency-Key": "domain-contract-input"}
    body = {
        "events": [
            {
                "type": "agent.session.input.message",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "original"}]}],
                "yuxi": {"mode": "follow_up"},
            }
        ]
    }
    accepted = await domain_http.post(url, headers=headers, json=body)
    replayed = await domain_http.post(url, headers=headers, json=body)
    assert accepted.status_code == replayed.status_code == 202, (accepted.text, replayed.text)
    assert accepted.json() == replayed.json()
    body["events"][0]["input"][0]["content"][0]["text"] = "changed"
    rejected = await domain_http.post(url, headers=headers, json=body)
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "Idempotency-Key 已用于其他输入"}
    async with sessions() as db:
        [item] = (await db.scalars(select(AgentInput))).all()
        [receipt] = (await db.scalars(select(AgentInputReceipt))).all()
        [turn] = (await db.scalars(select(AgentTurn))).all()
        [run] = (await db.scalars(select(AgentRun))).all()
        assert item.messages[0]["content"] == [{"type": "text", "text": "original"}]
        assert receipt.input_id == item.id == accepted.json()["input_id"]
        assert item.status == "consumed"
        assert (item.turn_id, item.consumed_run_id) == (turn.id, run.id)
        assert run.turn_id == turn.id and turn.current_run_id == run.id


async def test_http_missing_scope_and_invalid_sse_cursor_are_rejected_before_response(domain_http):
    """资源不可见仍为 404，非法游标在 SSE 建立前返回 422。"""
    missing = await domain_http.get("/api/v1/agents/sessions/missing/inputs/input-id")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Thread 不存在"}
    invalid_cursor = await domain_http.get("/api/v1/agents/sessions/input-thread/events", headers={"Last-Event-ID": "not-a-cursor"})
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json() == {"detail": "事件游标无效或版本不兼容"}
    assert "text/event-stream" not in invalid_cursor.headers.get("content-type", "")
