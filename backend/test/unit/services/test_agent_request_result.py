"""按 Request 查询结果时只读取绑定的 Run。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from yuxi.services import agent_request_service
from yuxi.services.agent_request_queue_service import request_view


@pytest.mark.asyncio
async def test_queued_request_has_no_run_or_output(monkeypatch: pytest.MonkeyPatch):
    request = SimpleNamespace(
        request_id="req-2",
        turn_id="req-2",
        uid="user-1",
        conversation_thread_id="thread-1",
        agent_slug="agent-1",
        dispatched_run_id=None,
        status="queued",
        error_message=None,
    )

    class RequestRepo:
        def __init__(self, db):
            assert db is None

        async def get_by_request_id(self, request_id):
            assert request_id == "req-2"
            return request

    async def unexpected_run_result(**kwargs):
        raise AssertionError("排队请求不应读取相邻 Run")

    monkeypatch.setattr(agent_request_service, "AgentRunRequestRepository", RequestRepo)
    monkeypatch.setattr(agent_request_service, "get_agent_run_result", unexpected_run_result)

    result = await agent_request_service.get_agent_request_result(request_id="req-2", current_uid="user-1", db=None)
    assert result["status"] == "queued"
    assert result["run_id"] is None
    assert result["output"] is None
    assert result["usage"] is None


@pytest.mark.asyncio
async def test_request_result_rejects_other_user(monkeypatch: pytest.MonkeyPatch):
    class RequestRepo:
        def __init__(self, db):
            pass

        async def get_by_request_id(self, request_id):
            return SimpleNamespace(uid="user-1")

    monkeypatch.setattr(agent_request_service, "AgentRunRequestRepository", RequestRepo)
    with pytest.raises(HTTPException) as exc:
        await agent_request_service.get_agent_request_result(request_id="req-1", current_uid="user-2", db=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_dispatched_request_only_accepts_bound_run(monkeypatch: pytest.MonkeyPatch):
    request = SimpleNamespace(
        request_id="req-2",
        turn_id="req-2",
        uid="user-1",
        conversation_thread_id="thread-1",
        agent_slug="agent-1",
        dispatched_run_id="run-2",
        status="dispatched",
        error_message=None,
    )

    class RequestRepo:
        def __init__(self, db):
            pass

        async def get_by_request_id(self, request_id):
            return request

    async def run_result(*, run_id, current_uid, db):
        assert (run_id, current_uid) == ("run-2", "user-1")
        return {
            "request_id": "req-2",
            "status": "completed",
            "output": "second output",
            "final_message_id": 22,
            "token_usage": {"complete": True, "total": {"input_tokens": 1, "output_tokens": 2}},
        }

    monkeypatch.setattr(agent_request_service, "AgentRunRequestRepository", RequestRepo)
    monkeypatch.setattr(agent_request_service, "get_agent_run_result", run_result)
    result = await agent_request_service.get_agent_request_result(request_id="req-2", current_uid="user-1", db=None)
    assert (result["run_id"], result["status"], result["output"]) == ("run-2", "completed", "second output")
    assert result["usage"] == {"input_tokens": 1, "output_tokens": 2}

    async def wrong_run(*, run_id, current_uid, db):
        return {"request_id": "req-1", "status": "completed", "output": "first output"}

    monkeypatch.setattr(agent_request_service, "get_agent_run_result", wrong_run)
    with pytest.raises(HTTPException) as exc:
        await agent_request_service.get_agent_request_result(request_id="req-2", current_uid="user-1", db=None)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_request_result_url_encodes_custom_id():
    request = SimpleNamespace(
        request_id="req/?#1",
        turn_id="req/?#1",
        dispatched_run_id=None,
        status="queued",
        queue_policy="enqueue",
        input_message_id=1,
        conversation_thread_id="thread-1",
    )

    class RequestRepo:
        async def get_queue_position(self, request_id):
            assert request_id == "req/?#1"
            return 1

    response = await request_view(repo=RequestRepo(), request=request)
    assert response["result_url"] == "/api/agent/request-result?request_id=req%2F%3F%231"
