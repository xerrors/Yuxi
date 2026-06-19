from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import agent_eval_run_service as svc


def test_normalize_request_id_rejects_too_long_value():
    with pytest.raises(HTTPException) as exc:
        svc._normalize_request_id({"request_id": "x" * 65})

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_run_agent_eval_creates_conversation_and_returns_result(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, object] = {}
    current_user = SimpleNamespace(id=1, uid="user-1", role="user")

    class AgentRepo:
        def __init__(self, db):
            self.db = db

        async def get_visible_by_slug(self, *, slug: str, user):
            assert slug == "default-chatbot"
            assert user is current_user
            return SimpleNamespace(slug=slug, backend_id="ChatbotAgent")

    class ConvRepo:
        def __init__(self, db):
            self.db = db

        async def create_conversation(self, *, uid: str, agent_id: str, title: str, thread_id: str, metadata: dict):
            calls["conversation"] = {
                "uid": uid,
                "agent_id": agent_id,
                "title": title,
                "thread_id": thread_id,
                "metadata": metadata,
            }
            return SimpleNamespace(id=42, thread_id=thread_id)

    async def fake_create_agent_run_view(**kwargs):
        calls["run_kwargs"] = kwargs
        return {
            "run_id": "run-1",
            "thread_id": kwargs["thread_id"],
            "status": "pending",
            "request_id": kwargs["meta"]["request_id"],
            "stream_url": "/api/agent/runs/run-1/events",
        }

    async def fake_await_run_result(*, run_id: str, current_uid: str):
        calls["await_kwargs"] = {"run_id": run_id, "current_uid": current_uid}
        return {
            "status": "completed",
            "output": "final answer",
            "agent_run_id": run_id,
            "request_id": "req-1",
        }

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "create_agent_run_view", fake_create_agent_run_view)
    monkeypatch.setattr(svc, "await_agent_run_result", fake_await_run_result)

    result = await svc.run_agent_eval(
        query="2+2=?",
        agent_slug=" default-chatbot ",
        evaluation={
            "dataset_name": "agent-eval-smoke",
            "dataset_item_id": "item-1",
            "experiment_name": "exp-1",
            "ignored": "nope",
        },
        meta={"request_id": "req-1", "attachment_file_ids": ["file-1"]},
        image_content=None,
        model_spec=None,
        current_user=current_user,
        db=object(),
    )

    expected_eval = {
        "dataset_name": "agent-eval-smoke",
        "dataset_item_id": "item-1",
        "experiment_name": "exp-1",
    }
    assert calls["conversation"]["metadata"] == {"source": "agent_evaluation", "evaluation": expected_eval}
    assert calls["run_kwargs"]["meta"] == {
        "request_id": "req-1",
        "source": "agent_evaluation",
        "evaluation": expected_eval,
        "attachment_file_ids": ["file-1"],
    }
    assert calls["await_kwargs"] == {"run_id": "run-1", "current_uid": "user-1"}
    assert result == {
        "status": "completed",
        "output": "final answer",
        "agent_run_id": "run-1",
        "request_id": "req-1",
    }
