from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from yuxi.services import agent_request_service as svc
from yuxi.storage.postgres.models_business import AgentRunRequest
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.services.workdir_service import WorkdirBinding


class _EmptyRequestRepo:
    def __init__(self, db):
        del db

    async def get_by_request_id(self, request_id):
        del request_id
        return None


class _EmptyRunRepo:
    def __init__(self, db):
        del db

    async def get_run_by_request_id(self, request_id):
        del request_id
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "channel", "detail"),
    [
        ("x" * 33, "web", "Run origin source 不能超过 32 个字符"),
        ("chat", "x" * 33, "Run origin channel 不能超过 32 个字符"),
    ],
)
async def test_submit_agent_request_rejects_overlong_origin_before_repository_access(source, channel, detail):
    request_input = svc.AgentRequestInput(
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-1",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source=source, channel=channel),
    )

    with pytest.raises(HTTPException) as exc_info:
        await svc.submit_agent_request(
            request_input=request_input,
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == detail


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_fails", [False, True])
async def test_submit_agent_request_owns_commit_and_publication(monkeypatch: pytest.MonkeyPatch, commit_fails):
    calls: dict[str, object] = {"effects": []}
    current_user = SimpleNamespace(uid="user-1", role="user")

    class Db:
        async def commit(self):
            calls["effects"].append("commit")
            if commit_fails:
                raise RuntimeError("commit failed")

        @asynccontextmanager
        async def begin_nested(self):
            yield

    class AgentRepo:
        def __init__(self, db):
            del db

        async def get_visible_by_slug(self, *, slug: str, user, kind="main"):
            assert user is current_user
            assert kind == "main"
            return SimpleNamespace(slug=slug, backend_id="ChatbotAgent")

    class ProjectRepo:
        def __init__(self, db):
            del db

        async def lock_active_for_user(self, project_id, uid):
            calls["project_lookup"] = (project_id, uid)
            return SimpleNamespace(
                id=project_id,
                uid="user-1",
                workdir_path=f"projects/{project_id}",
                directory_mode="managed",
            )

    class ConvRepo:
        def __init__(self, db):
            del db

        async def get_conversation_by_thread_id(self, thread_id: str):
            calls["thread_id"] = thread_id
            return None

        async def add_conversation(self, **kwargs):
            calls["conversation"] = kwargs
            return SimpleNamespace(
                id=1,
                thread_id=kwargs["thread_id"],
                project_id=kwargs["project_id"],
            )

    async def fake_persist_request(**kwargs):
        calls["persist"] = kwargs
        return AgentRunRequest(
            request_id="req-1",
            status="dispatched",
            queue_policy="enqueue",
            input_message_id=10,
            dispatched_run_id="run-1",
            conversation_thread_id="thread-1",
        ), svc.DispatchResult(request_id="req-1", run_id="run-1", workdir_binding=kwargs["workdir_binding"])

    async def enqueue(run_id):
        calls["effects"].append("enqueue")
        assert run_id == "run-1"

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "ProjectRepository", ProjectRepo)

    async def fake_create_implicit_project(**kwargs):
        calls["project"] = kwargs
        return SimpleNamespace(
            id="11111111-1111-4111-8111-111111111111",
            uid="user-1",
            workdir_path="projects/11111111-1111-4111-8111-111111111111",
            directory_mode="managed",
        )

    async def fake_resolve_binding(**kwargs):
        conversation = kwargs["conversation"]
        calls["binding_project"] = kwargs.get("project")
        return WorkdirBinding(
            conversation_id=conversation.id,
            thread_id=conversation.thread_id,
            uid="user-1",
            project_id=conversation.project_id,
            workdir_path="projects/11111111-1111-4111-8111-111111111111",
            directory_mode="managed",
        )

    monkeypatch.setattr(svc, "create_implicit_project", fake_create_implicit_project)
    monkeypatch.setattr(svc, "resolve_conversation_workdir_binding", fake_resolve_binding)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda backend_id: object())
    monkeypatch.setattr(svc, "_persist_request", fake_persist_request)
    monkeypatch.setattr(svc, "enqueue_agent_run", enqueue)
    monkeypatch.setattr(svc, "ensure_bound_user_workdir", lambda *_: calls["effects"].append("materialize"))

    request_input = svc.AgentRequestInput(
        agent_slug="translator",
        thread_id="thread-1",
        request_id="req-1",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(
            source="agent_call",
            channel="api",
            external_id="external-1",
            metadata={
                "source": "spoofed",
                "channel": "spoofed",
                "agent_invocation_meta": {"trace_id": "trace-1"},
            },
        ),
        request_metadata={"request_id": "req-1", "channel": "spoofed"},
        model_spec="provider:model",
        create_conversation=True,
        conversation_title="Agent Call Run",
        conversation_project_id="11111111-1111-4111-8111-111111111111",
    )

    if commit_fails:
        with pytest.raises(RuntimeError, match="commit failed"):
            await svc.submit_agent_request(request_input=request_input, current_user=current_user, db=Db())
        assert calls["effects"] == ["commit"]
        return

    result = await svc.submit_agent_request(request_input=request_input, current_user=current_user, db=Db())

    assert calls["conversation"]["metadata"] == {
        "source": "agent_call",
        "channel": "api",
        "agent_invocation_meta": {"trace_id": "trace-1"},
    }
    assert calls["project_lookup"] == ("11111111-1111-4111-8111-111111111111", "user-1")
    assert calls["binding_project"].id == "11111111-1111-4111-8111-111111111111"
    assert calls["conversation"]["project_id"] == "11111111-1111-4111-8111-111111111111"
    assert calls["persist"]["request_input"].origin.source == "agent_call"
    assert calls["persist"]["request_input"].origin.channel == "api"
    assert calls["persist"]["request_input"].origin.external_id == "external-1"
    assert calls["persist"]["request_input"].origin.metadata == {"agent_invocation_meta": {"trace_id": "trace-1"}}
    assert calls["persist"]["request_input"].request_metadata == {
        "request_id": "req-1",
        "channel": "api",
        "agent_invocation_meta": {"trace_id": "trace-1"},
    }
    assert calls["persist"]["workdir_binding"].workdir_path == ("projects/11111111-1111-4111-8111-111111111111")
    assert result == {
        "request_id": "req-1",
        "status": "dispatched",
        "queue_policy": "enqueue",
        "queue_position": None,
        "message_id": 10,
        "run_id": "run-1",
        "stream_url": "/api/agent/runs/run-1/events",
        "request_events_url": None,
        "thread_id": "thread-1",
    }
    assert calls["effects"] == ["commit", "materialize", "enqueue"]


@pytest.mark.asyncio
async def test_submit_agent_request_requires_existing_conversation_for_web_chat(
    monkeypatch: pytest.MonkeyPatch,
):
    current_user = SimpleNamespace(uid="user-1", role="user")

    class AgentRepo:
        def __init__(self, db):
            del db

        async def get_visible_by_slug(self, *, slug: str, user, kind="main"):
            del user, kind
            return SimpleNamespace(slug=slug, backend_id="ChatbotAgent")

    class ConvRepo:
        def __init__(self, db):
            del db

        async def get_conversation_by_thread_id(self, thread_id: str):
            del thread_id
            return None

        async def add_conversation(self, **kwargs):
            raise AssertionError(f"web chat must not create a conversation: {kwargs}")

    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "AgentRunRequestRepository", _EmptyRequestRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _EmptyRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda backend_id: object())

    request_input = svc.AgentRequestInput(
        agent_slug="translator",
        thread_id="missing-thread",
        request_id="req-1",
        input_message=build_chat_input_message("hello"),
        origin=svc.RunOrigin(source="chat", channel="web"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await svc.submit_agent_request(request_input=request_input, current_user=current_user, db=object())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,denied",
    [("queued", None), ("dispatched", None)]
    + [
        ("queued", denied)
        for denied in (
            "scope",
            "agent",
            "thread_missing",
            "thread_owner",
            "thread_deleted",
            "thread_agent",
            "project",
            "project_deleted",
        )
    ],
)
async def test_existing_request_returns_without_runtime_preparation(monkeypatch, status, denied):
    """重发只读既有请求；配置不可用不影响幂等，访问边界仍拒绝越权。"""
    from unittest.mock import AsyncMock

    request = SimpleNamespace(
        request_id="req",
        uid="user",
        agent_slug="agent",
        conversation_thread_id="thread",
        source="chat",
        channel="web",
        external_id=None,
        queue_policy="steer",
        status=status,
        input_message_id=10,
        dispatched_run_id="run" if status == "dispatched" else None,
        input_payload={"model_spec": "first:model"},
    )
    conversation = SimpleNamespace(
        uid="other" if denied == "thread_owner" else "user",
        status="deleted" if denied == "thread_deleted" else "active",
        agent_id="wrong" if denied == "thread_agent" else "agent",
        project_id="project",
    )
    monkeypatch.setattr(
        svc,
        "AgentRepository",
        lambda db: SimpleNamespace(
            get_visible_by_slug=AsyncMock(
                return_value=None if denied == "agent" else SimpleNamespace(slug="agent", backend_id="removed")
            )
        ),
    )
    monkeypatch.setattr(
        svc,
        "AgentRunRequestRepository",
        lambda db: SimpleNamespace(
            get_by_request_id=AsyncMock(return_value=request), get_queue_position=AsyncMock(return_value=1)
        ),
    )
    monkeypatch.setattr(
        svc,
        "ConversationRepository",
        lambda db: SimpleNamespace(
            get_conversation_by_thread_id=AsyncMock(return_value=None if denied == "thread_missing" else conversation)
        ),
    )
    monkeypatch.setattr(
        svc,
        "ProjectRepository",
        lambda db: SimpleNamespace(
            get_for_user=AsyncMock(
                return_value=None
                if denied == "project"
                else SimpleNamespace(status="deleted" if denied == "project_deleted" else "active")
            )
        ),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("重发不能加载运行后端、物化目录或创建新请求")

    monkeypatch.setattr(svc.agent_manager, "get_agent", forbidden)
    monkeypatch.setattr(svc, "resolve_conversation_workdir_binding", forbidden)
    monkeypatch.setattr(svc, "_persist_request", forbidden)
    monkeypatch.setattr(svc, "enqueue_agent_run", forbidden)
    monkeypatch.setattr(svc, "AgentRunRepository", forbidden)
    request_input = svc.AgentRequestInput(
        agent_slug="agent",
        thread_id="wrong" if denied == "scope" else "thread",
        request_id="req",
        input_message=build_chat_input_message("changed"),
        model_spec="invalid:ignored",
        origin=svc.RunOrigin(source="chat", channel="web"),
        queue_policy="enqueue",
    )
    if denied:
        with pytest.raises(HTTPException) as exc:
            await svc.submit_agent_request(
                request_input=request_input, current_user=SimpleNamespace(uid="user"), db=object()
            )
        assert exc.value.status_code == (409 if denied == "scope" else 404)
    else:
        result = await svc.submit_agent_request(
            request_input=request_input, current_user=SimpleNamespace(uid="user"), db=object()
        )
        assert result["queue_policy"] == "steer"
        assert result["status"] == status
        assert result["run_id"] == ("run" if status == "dispatched" else None)
        assert result["message_id"] == 10
        assert request.input_payload == {"model_spec": "first:model"}
