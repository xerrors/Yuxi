from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.utils.auth_middleware import get_db, get_required_user

agent_invocation_router_module = importlib.import_module("server.routers.agent_invocation_router")


def _build_app(monkeypatch: pytest.MonkeyPatch, *, authenticated: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(agent_invocation_router_module.agent_invocation_router, prefix="/api")

    async def fake_db():
        return object()

    app.dependency_overrides[get_db] = fake_db
    if authenticated:

        async def fake_user():
            return SimpleNamespace(uid="user-1", role="user", department_id=1)

        app.dependency_overrides[get_required_user] = fake_user

    return TestClient(app)


def test_agent_call_run_requires_authentication(monkeypatch: pytest.MonkeyPatch):
    client = _build_app(monkeypatch, authenticated=False)

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={"agent_slug": "translator", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 401


def test_agent_eval_run_returns_service_payload(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_create_agent_eval_run_view(**kwargs):
        calls["kwargs"] = kwargs
        return {
            "status": "completed",
            "output": "final answer",
            "agent_run_id": "run-1",
            "request_id": "req-1",
        }

    monkeypatch.setattr(
        agent_invocation_router_module,
        "create_agent_eval_run_view",
        fake_create_agent_eval_run_view,
    )
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/eval/runs",
        json={
            "query": "2+2=?",
            "agent_slug": " default-chatbot ",
            "evaluation": {"dataset_name": "dataset-1", "dataset_item_id": "item-1", "ignored": "nope"},
            "meta": {"request_id": "req-1", "attachment_file_ids": ["file-1"]},
            "model_spec": "provider:model",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["output"] == "final answer"
    assert calls["kwargs"]["query"] == "2+2=?"
    assert calls["kwargs"]["agent_slug"] == " default-chatbot "
    assert calls["kwargs"]["evaluation"] == {"dataset_name": "dataset-1", "dataset_item_id": "item-1"}
    assert calls["kwargs"]["meta"] == {"request_id": "req-1", "attachment_file_ids": ["file-1"]}
    assert calls["kwargs"]["model_spec"] == "provider:model"
    assert calls["kwargs"]["current_user"].uid == "user-1"


def test_agent_eval_run_rejects_too_long_request_id(monkeypatch: pytest.MonkeyPatch):
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/eval/runs",
        json={
            "query": "2+2=?",
            "agent_slug": "default-chatbot",
            "meta": {"request_id": "x" * 65},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "request_id 不能超过 64 个字符"


def test_agent_eval_run_returns_504_when_wait_times_out(monkeypatch: pytest.MonkeyPatch):
    async def fake_create_agent_eval_run_view(**_kwargs):
        raise HTTPException(
            status_code=504,
            detail={"message": "运行仍在进行中，等待最终结果超时", "run": {"status": "running"}},
        )

    monkeypatch.setattr(
        agent_invocation_router_module,
        "create_agent_eval_run_view",
        fake_create_agent_eval_run_view,
    )
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/eval/runs",
        json={"query": "2+2=?", "agent_slug": "default-chatbot", "meta": {"request_id": "req-1"}},
    )

    assert response.status_code == 504
    assert response.json()["detail"]["message"] == "运行仍在进行中，等待最终结果超时"
    assert response.json()["detail"]["run"]["status"] == "running"


def test_legacy_agent_eval_run_path_is_not_registered(monkeypatch: pytest.MonkeyPatch):
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent/eval/runs",
        json={"query": "2+2=?", "agent_slug": "default-chatbot"},
    )

    assert response.status_code == 404


def test_legacy_agent_call_run_path_is_not_registered(monkeypatch: pytest.MonkeyPatch):
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-call/runs",
        json={"agent_slug": "translator", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 404


def test_agent_call_run_creates_async_run_and_returns_agent_call_payload(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_create_agent_call_run_view(**kwargs):
        calls["kwargs"] = kwargs
        return {
            "run_id": "run-1",
            "agent_slug": "translator",
            "thread_id": "thread-1",
            "status": "pending",
            "request_id": "req-1",
            "output": "",
            "choices": [{"index": 0, "messages": [{"role": "assistant", "content": ""}], "finish_reason": None}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    monkeypatch.setattr(
        agent_invocation_router_module,
        "create_agent_call_run_view",
        fake_create_agent_call_run_view,
    )
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={
            "agent_slug": " translator ",
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ignored"},
                {"role": "user", "content": "Hello"},
            ],
            "agent_call_meta": {"trace_id": "trace-1"},
            "thread_id": " thread-1 ",
            "request_id": " req-1 ",
            "async_mode": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == "run-1"
    assert response.json()["choices"][0]["finish_reason"] is None
    assert calls["kwargs"]["agent_slug"] == " translator "
    assert calls["kwargs"]["messages"][-1] == {"role": "user", "content": "Hello"}
    assert calls["kwargs"]["agent_call_meta"] == {"trace_id": "trace-1"}
    assert calls["kwargs"]["requested_thread_id"] == " thread-1 "
    assert calls["kwargs"]["request_id"] == " req-1 "
    assert calls["kwargs"]["async_mode"] is True
    assert calls["kwargs"]["stream"] is False
    assert calls["kwargs"]["current_user"].uid == "user-1"


def test_agent_call_run_waits_and_wraps_final_result(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_create_agent_call_run_view(**kwargs):
        calls["kwargs"] = kwargs
        return {
            "run_id": "run-1",
            "agent_slug": "translator",
            "thread_id": "thread-1",
            "status": "completed",
            "request_id": "req-1",
            "output": "你好",
            "choices": [{"index": 0, "messages": [{"role": "assistant", "content": "你好"}], "finish_reason": "stop"}],
            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        }

    monkeypatch.setattr(
        agent_invocation_router_module,
        "create_agent_call_run_view",
        fake_create_agent_call_run_view,
    )
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={
            "agent_slug": "translator",
            "messages": [{"role": "user", "content": "Hello"}],
            "request_id": "req-1",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == "run-1"
    assert response.json()["output"] == "你好"
    assert response.json()["choices"][0]["messages"] == [{"role": "assistant", "content": "你好"}]
    assert response.json()["choices"][0]["finish_reason"] == "stop"
    assert response.json()["usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    assert calls["kwargs"]["request_id"] == "req-1"


def test_agent_call_run_returns_504_when_wait_times_out(monkeypatch: pytest.MonkeyPatch):
    async def fake_create_agent_call_run_view(**_kwargs):
        raise HTTPException(
            status_code=504,
            detail={"message": "运行仍在进行中，等待最终结果超时", "run": {"status": "running"}},
        )

    monkeypatch.setattr(
        agent_invocation_router_module,
        "create_agent_call_run_view",
        fake_create_agent_call_run_view,
    )
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={"agent_slug": "translator", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 504
    assert response.json()["detail"]["message"] == "运行仍在进行中，等待最终结果超时"
    assert response.json()["detail"]["run"]["status"] == "running"


def test_agent_call_run_rejects_context_override(monkeypatch: pytest.MonkeyPatch):
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={
            "agent_slug": "translator",
            "messages": [{"role": "user", "content": "Hello"}],
            "agent_call_meta": {"context": {"system_prompt": "只回答中文"}},
        },
    )

    assert response.status_code == 422
    assert "agent_call_meta.context 不允许覆盖 Agent context" in response.json()["detail"]


def test_agent_call_run_accepts_openai_text_content_parts(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_create_agent_call_run_view(**kwargs):
        calls["kwargs"] = kwargs
        return {
            "run_id": "run-1",
            "agent_slug": kwargs["agent_slug"],
            "thread_id": "thread-1",
            "status": "pending",
            "request_id": "req-1",
            "output": "",
            "choices": [{"index": 0, "messages": [{"role": "assistant", "content": ""}], "finish_reason": None}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    monkeypatch.setattr(agent_invocation_router_module, "create_agent_call_run_view", fake_create_agent_call_run_view)
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={
            "agent_slug": "translator",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        },
    )

    assert response.status_code == 200, response.text
    assert calls["kwargs"]["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]


def test_agent_call_run_accepts_openai_multimodal_content_parts(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_create_agent_call_run_view(**kwargs):
        calls["kwargs"] = kwargs
        return {
            "run_id": "run-1",
            "agent_slug": kwargs["agent_slug"],
            "thread_id": "thread-1",
            "status": "pending",
            "request_id": "req-1",
            "output": "",
            "choices": [{"index": 0, "messages": [{"role": "assistant", "content": ""}], "finish_reason": None}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    monkeypatch.setattr(agent_invocation_router_module, "create_agent_call_run_view", fake_create_agent_call_run_view)
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={
            "agent_slug": "translator",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,base64-image", "detail": "low"},
                        },
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert calls["kwargs"]["messages"][0]["content"][1]["image_url"] == {
        "url": "data:image/png;base64,base64-image",
        "detail": "low",
    }


def test_agent_call_run_propagates_agent_not_found(monkeypatch: pytest.MonkeyPatch):
    async def fake_create_agent_call_run_view(**_kwargs):
        raise HTTPException(status_code=404, detail="智能体不存在")

    monkeypatch.setattr(agent_invocation_router_module, "create_agent_call_run_view", fake_create_agent_call_run_view)
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={"agent_slug": "missing", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "智能体不存在"


def test_agent_call_run_rejects_invalid_boundary_payload(monkeypatch: pytest.MonkeyPatch):
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={"agent_slug": " ", "messages": [{"role": "user", "content": "Hello"}]},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "agent_slug 不能为空"

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={"agent_slug": "translator", "messages": [{"role": "user", "content": "Hello"}], "stream": True},
    )
    assert response.status_code == 422
    assert "stream=true" in response.json()["detail"]

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={"agent_slug": "translator", "messages": []},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "messages 不能为空"

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={"agent_slug": "translator", "messages": [{"role": "assistant", "content": "hello"}]},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "messages 必须包含 user 消息"

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={"agent_slug": "translator", "messages": [{"role": "user", "content": ""}]},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "user message content 必须是非空字符串或多模态数组"

    response = client.post(
        "/api/agent-invocation/agent-call/runs",
        json={
            "agent_slug": "translator",
            "messages": [{"role": "user", "content": "Hello"}],
            "request_id": "x" * 65,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "request_id 不能超过 64 个字符"


def test_agent_call_result_returns_service_payload(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}

    async def fake_get_agent_call_run_result_view(**kwargs):
        calls["kwargs"] = kwargs
        return {
            "run_id": kwargs["run_id"],
            "agent_slug": kwargs["agent_slug"],
            "thread_id": "thread-1",
            "status": "completed",
            "request_id": "req-1",
            "output": "done",
            "choices": [{"index": 0, "messages": [{"role": "assistant", "content": "done"}], "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    monkeypatch.setattr(
        agent_invocation_router_module,
        "get_agent_call_run_result_view",
        fake_get_agent_call_run_result_view,
    )
    client = _build_app(monkeypatch)

    response = client.post(
        "/api/agent-invocation/agent-call/runs/result",
        json={"run_id": "run-1", "agent_slug": "translator"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["output"] == "done"
    assert calls["kwargs"] == {
        "run_id": "run-1",
        "agent_slug": "translator",
        "current_uid": "user-1",
        "db": calls["kwargs"]["db"],
    }
