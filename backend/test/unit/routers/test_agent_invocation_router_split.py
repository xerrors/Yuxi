from __future__ import annotations

import importlib
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.utils.auth_middleware import get_db, get_required_user

call_module = importlib.import_module("server.routers.agent_invocation_call_router")
eval_module = importlib.import_module("server.routers.agent_invocation_eval_router")


def _build_app(*, authenticated: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(call_module.agent_invocation_call_router, prefix="/api")
    app.include_router(eval_module.agent_invocation_eval_router, prefix="/api")

    async def fake_db():
        return object()

    app.dependency_overrides[get_db] = fake_db
    if authenticated:

        async def fake_user():
            return SimpleNamespace(uid="user-1", role="user", department_id=1)

        app.dependency_overrides[get_required_user] = fake_user
    return TestClient(app)


def test_invocation_call_requires_authentication():
    response = _build_app(authenticated=False).post(
        "/api/agent-invocation/agent-call/runs",
        json={"agent_slug": "translator", "messages": [{"role": "user", "content": "Hello"}]},
    )
    assert response.status_code == 401


def test_split_routers_keep_public_paths_and_remove_legacy_paths():
    client = _build_app()
    assert client.post("/api/agent/eval/runs", json={}).status_code == 404
    assert client.post("/api/agent-call/runs", json={}).status_code == 404


def test_agent_call_router_adapts_payload(monkeypatch):
    calls: dict[str, object] = {}

    async def fake_submit_agent_request(*, request_input, **_kwargs):
        calls["request_input"] = request_input
        return {"run_id": "run-1", "thread_id": request_input.thread_id, "status": "dispatched", "request_id": "req-1"}

    async def fake_wait(**_kwargs):
        return {
            "status": "completed",
            "agent_run_id": "run-1",
            "request_id": "req-1",
            "agent_slug": "translator",
            "thread_id": "thread-1",
            "output": "done",
        }

    monkeypatch.setattr(call_module, "submit_agent_request", fake_submit_agent_request)
    monkeypatch.setattr(call_module, "await_agent_run_result", fake_wait)
    response = _build_app().post(
        "/api/agent-invocation/agent-call/runs",
        json={
            "agent_slug": " translator ",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
            "request_id": "req-1",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["output"] == "done"
    assert calls["request_input"].origin.source == "agent_call"


def test_agent_eval_router_adapts_payload(monkeypatch):
    calls: dict[str, object] = {}

    async def fake_submit_agent_request(*, request_input, **_kwargs):
        calls["request_input"] = request_input
        return {"run_id": "run-1", "thread_id": request_input.thread_id, "status": "dispatched", "request_id": "eval-1"}

    async def fake_wait(**_kwargs):
        return {"status": "completed", "agent_run_id": "run-1", "request_id": "eval-1", "output": "ok"}

    monkeypatch.setattr(eval_module, "submit_agent_request", fake_submit_agent_request)
    monkeypatch.setattr(eval_module, "await_agent_run_result", fake_wait)
    response = _build_app().post(
        "/api/agent-invocation/eval/runs",
        json={
            "query": "2+2=?",
            "agent_slug": "default-chatbot",
            "thread_id": "YUXI_TEST_eval-thread",
            "evaluation": {"dataset_name": "dataset-1", "ignored": "drop"},
            "meta": {"request_id": "eval-1"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["output"] == "ok"
    assert calls["request_input"].thread_id == "YUXI_TEST_eval-thread"
    assert calls["request_input"].origin.metadata == {
        "agent_invocation_meta": {"evaluation": {"dataset_name": "dataset-1"}}
    }


def test_agent_eval_router_rejects_thread_id_longer_than_database_limit():
    response = _build_app().post(
        "/api/agent-invocation/eval/runs",
        json={
            "query": "2+2=?",
            "agent_slug": "default-chatbot",
            "thread_id": "t" * 65,
        },
    )

    assert response.status_code == 422
