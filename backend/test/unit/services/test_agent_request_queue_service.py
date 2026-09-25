"""Agent request queue service unit tests."""

from __future__ import annotations

from yuxi.services.agent_request_service import AgentRequestInput, RunOrigin, _persist_request
from yuxi.services import agent_request_service

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.services.agent_request_queue_service import (
    DispatchResult,
    NOT_IMPLEMENTED_QUEUE_POLICIES,
    cancel_queued_request,
    finalize_dispatch,
    steer_queued_request,
    validate_queue_policy,
)
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.services.workdir_service import WorkdirBinding
from yuxi.storage.postgres.models_business import AgentRunRequest, Base, Message
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.unit]


# ── finalize ordering ──


@pytest.mark.asyncio
async def test_finalize_dispatch_materializes_workdir_after_commit_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []

    class Db:
        async def commit(self):
            events.append("commit")

    def ensure_workdir(uid: str, workdir_path: str):
        assert uid == "user-1"
        assert workdir_path == "projects/11111111-1111-4111-8111-111111111111"
        events.append("materialize")

    async def enqueue(run_id: str):
        assert run_id == "run-1"
        events.append("enqueue")

    from yuxi.services import agent_request_queue_service as service

    monkeypatch.setattr(service, "ensure_bound_user_workdir", ensure_workdir)
    monkeypatch.setattr(service, "enqueue_agent_run", enqueue)

    await finalize_dispatch(
        db=Db(),
        dispatch=DispatchResult(
            request_id="request-1",
            run_id="run-1",
            workdir_binding=WorkdirBinding(
                conversation_id=1,
                thread_id="thread-1",
                uid="user-1",
                project_id="project-1",
                workdir_path="projects/11111111-1111-4111-8111-111111111111",
                directory_mode="managed",
            ),
        ),
    )

    assert events == ["commit", "materialize", "enqueue"]


@pytest.mark.asyncio
async def test_finalize_dispatch_does_not_materialize_when_commit_fails(monkeypatch: pytest.MonkeyPatch):
    """Owner 事务失败时不得留下无归属的 managed 目录。"""

    class Db:
        async def commit(self):
            raise RuntimeError("commit failed")

    from yuxi.services import agent_request_queue_service as service

    monkeypatch.setattr(
        service,
        "ensure_bound_user_workdir",
        lambda *_args: pytest.fail("commit 失败后不应物化目录"),
    )
    monkeypatch.setattr(
        service,
        "enqueue_agent_run",
        lambda *_args: pytest.fail("commit 失败后不应投递 Run"),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await finalize_dispatch(
            db=Db(),
            dispatch=DispatchResult(
                request_id="request-1",
                run_id="run-1",
                workdir_binding=WorkdirBinding(
                    conversation_id=1,
                    thread_id="thread-1",
                    uid="user-1",
                    project_id="project-1",
                    workdir_path="projects/project-1",
                    directory_mode="managed",
                ),
            ),
        )


@pytest.mark.asyncio
async def test_recover_pending_dispatches_isolates_failed_scope(monkeypatch: pytest.MonkeyPatch):
    """一个损坏 scope 不得阻断其他 pending Run 的恢复。"""

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class Db:
        calls = 0

        async def execute(self, _statement):
            self.calls += 1
            return Result(
                [("user-1", "main", "bad-thread"), ("user-1", "main", "good-thread")] if self.calls == 1 else []
            )

    @asynccontextmanager
    async def session_context():
        yield Db()

    recovered: list[str] = []

    async def dispatch_next_request(**kwargs):
        if kwargs["thread_id"] == "bad-thread":
            raise RuntimeError("broken scope")
        recovered.append(kwargs["thread_id"])
        return "run-good"

    from yuxi.services import agent_request_queue_service as service

    monkeypatch.setattr(service.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(service, "dispatch_next_request", dispatch_next_request)

    await service.recover_pending_dispatches()

    assert recovered == ["good-thread"]


@pytest.mark.asyncio
async def test_pending_linked_run_is_enqueued_without_opening_missing_directory(monkeypatch: pytest.MonkeyPatch):
    """linked 目录失效由 worker 记为终态，不能卡在 pending 且未投递。"""

    events: list[str] = []
    conversation = SimpleNamespace(
        id=1,
        uid="user-1",
        agent_id="main",
        status="active",
        thread_id="thread-1",
        project_id="project-1",
    )

    @asynccontextmanager
    async def session_context():
        yield object()
        events.append("commit")

    class ConversationRepo:
        def __init__(self, _db):
            pass

        async def lock_conversation_by_thread_id(self, _thread_id):
            return conversation

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_active_run_by_thread_for_user(self, **_kwargs):
            return SimpleNamespace(id="run-linked", status="pending")

    async def resolve_binding(**_kwargs):
        return WorkdirBinding(
            conversation_id=1,
            thread_id="thread-1",
            uid="user-1",
            project_id="project-1",
            workdir_path="clients/missing",
            directory_mode="linked",
        )

    async def enqueue(run_id):
        events.append(f"enqueue:{run_id}")

    from yuxi.services import agent_request_queue_service as service

    monkeypatch.setattr(service.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(service, "ConversationRepository", ConversationRepo)
    monkeypatch.setattr(service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(service, "resolve_conversation_workdir_binding", resolve_binding)
    monkeypatch.setattr(service, "enqueue_agent_run", enqueue)

    result = await service.dispatch_next_request(uid="user-1", agent_slug="main", thread_id="thread-1")

    assert result == "run-linked"
    assert events == ["commit", "enqueue:run-linked"]


# ── validate_queue_policy ──


@pytest.mark.parametrize("policy", ["enqueue", "reject", "steer"])
def test_validate_queue_policy_accepts_policy(policy):
    assert validate_queue_policy(policy) == policy


@pytest.mark.parametrize("policy", list(NOT_IMPLEMENTED_QUEUE_POLICIES))
def test_validate_queue_policy_rejects_unimplemented(policy):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        validate_queue_policy(policy)
    assert exc_info.value.status_code == 422


def test_validate_queue_policy_rejects_unknown():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        validate_queue_policy("unknown")
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_intake_rejects_steer_for_unsupported_source(session):
    from fastapi import HTTPException
    from yuxi.services.input_message_service import build_chat_input_message

    with pytest.raises(HTTPException) as exc_info:
        await _persist_request(
            db=session,
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
            request_input=AgentRequestInput(
                request_id="request-agent-call-steer",
                agent_slug="main",
                thread_id="t1",
                input_message=build_chat_input_message("steer"),
                queue_policy="steer",
                origin=RunOrigin(source="agent_call", channel="web"),
            ),
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("active_source", ["chat", "channel"])
async def test_channel_steer_is_accepted_for_active_message_run(
    session, monkeypatch: pytest.MonkeyPatch, active_source: str
):
    from yuxi.services.input_message_service import build_chat_input_message

    async def resolve_config(*_args):
        return "model", "default"

    monkeypatch.setattr(agent_request_service, "resolve_agent_run_config", resolve_config)
    await _seed_thread(session)
    await _seed_active_run(session, source=active_source)

    result, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="request-channel-steer",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("steer"),
            queue_policy="steer",
            origin=RunOrigin(source="channel", channel="cli"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result.status == "queued"
    assert result.queue_policy == "steer"


@pytest.mark.asyncio
async def test_intake_request_binds_resolved_model_to_conversation(session, monkeypatch: pytest.MonkeyPatch):
    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import Conversation

    resolved_requests = []

    async def resolve_config(model_spec, *_args):
        resolved_requests.append(model_spec)
        return model_spec or "provider:agent-default", "default"

    monkeypatch.setattr(agent_request_service, "resolve_agent_run_config", resolve_config)
    await _seed_thread(session)

    first, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="request-model-a",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("first"),
            model_spec="provider:conversation-model",
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )

    conversation = await session.get(Conversation, 10)
    await session.refresh(conversation)
    assert first.status == "dispatched"
    assert conversation.extra_metadata["model_spec"] == "provider:conversation-model"

    second, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="request-model-b",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("second"),
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )
    assert second.status == "queued"
    assert resolved_requests == ["provider:conversation-model", "provider:conversation-model"]

    rejected, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="request-model-rejected",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("rejected"),
            model_spec="provider:rejected-model",
            queue_policy="reject",
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )

    await session.refresh(conversation)
    assert rejected.status == "rejected"
    assert conversation.extra_metadata["model_spec"] == "provider:conversation-model"


@pytest.mark.asyncio
async def test_reject_dispatch_conflict_does_not_change_conversation_model(session, monkeypatch: pytest.MonkeyPatch):
    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import Conversation

    async def resolve_config(model_spec, *_args):
        return model_spec, "default"

    async def lose_dispatch_race(**_kwargs):
        return None

    monkeypatch.setattr(agent_request_service, "resolve_agent_run_config", resolve_config)
    monkeypatch.setattr(agent_request_service, "dispatch_ready_head", lose_dispatch_race)
    await _seed_thread(session)
    conversation = await session.get(Conversation, 10)
    conversation.extra_metadata = {"model_spec": "provider:existing-model"}
    await session.commit()

    result, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="request-reject-race",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("reject race"),
            model_spec="provider:rejected-model",
            queue_policy="reject",
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )

    await session.refresh(conversation)
    assert result.status == "rejected"
    assert conversation.extra_metadata["model_spec"] == "provider:existing-model"


@pytest.mark.asyncio
async def test_intake_request_binds_attachments_in_request_transaction(session, monkeypatch: pytest.MonkeyPatch):
    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import Conversation

    async def resolve_config(*_args):
        return "model", "default"

    monkeypatch.setattr(agent_request_service, "resolve_agent_run_config", resolve_config)
    await _seed_thread(session)
    conversation = await session.get(Conversation, 10)
    conversation.extra_metadata = {"attachments": [{"file_id": "file-1", "file_name": "notes.txt"}]}
    await session.commit()

    result, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="request-with-attachment",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("read it"),
            request_metadata={"attachment_file_ids": ["file-1"]},
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )

    await session.refresh(conversation)
    assert result.status == "dispatched"
    assert conversation.extra_metadata["attachments"][0]["request_id"] == "request-with-attachment"


@pytest.mark.asyncio
async def test_intake_request_rejects_missing_attachment_without_creating_request(
    session,
    monkeypatch: pytest.MonkeyPatch,
):
    from fastapi import HTTPException
    from yuxi.services.input_message_service import build_chat_input_message

    async def resolve_config(*_args):
        return "model", "default"

    monkeypatch.setattr(agent_request_service, "resolve_agent_run_config", resolve_config)
    await _seed_thread(session)

    with pytest.raises(HTTPException) as exc:
        await _persist_request(
            db=session,
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
            request_input=AgentRequestInput(
                request_id="request-missing-attachment",
                agent_slug="main",
                thread_id="t1",
                input_message=build_chat_input_message("read it"),
                request_metadata={"attachment_file_ids": ["missing"]},
                origin=RunOrigin(source="chat", channel="web"),
            ),
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc.value.status_code == 422
    assert (
        await session.scalar(
            select(sa_func.count())
            .select_from(AgentRunRequest)
            .where(AgentRunRequest.request_id == "request-missing-attachment")
        )
        == 0
    )


# ── AgentRunCreate request model ──


# ── fixtures ──


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed_thread(session, *, uid="user-1", msg_id=100, conv_id=10):
    from yuxi.storage.postgres.models_business import Conversation, Message, Project

    project_id = f"project-{uid}-t1"
    session.add(
        Project(
            id=project_id,
            uid=uid,
            selection_status="implicit",
            workdir_path=f"projects/workdir-{uid}-t1",
            directory_mode="managed",
        )
    )
    session.add(
        Conversation(
            id=conv_id,
            thread_id="t1",
            project_id=project_id,
            uid=uid,
            agent_id="main",
            status="active",
        )
    )
    session.add(Message(id=msg_id, conversation_id=conv_id, role="user", content="hi"))
    await session.commit()


async def _seed_active_run(session, *, source="chat", status="running", run_type="chat"):
    """在线程内创建可供 Steer 门禁识别的活跃 Run。"""
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
    from yuxi.storage.postgres.models_business import AgentRun

    session.add(Message(id=101, conversation_id=10, role="user", content="active"))
    await AgentRunRequestRepository(session).create(
        request_id="active-request",
        uid="user-1",
        agent_slug="main",
        conversation_thread_id="t1",
        source=source,
        input_message_id=101,
        status="dispatched",
    )
    session.add(
        AgentRun(
            id="active-run",
            conversation_thread_id="t1",
            runtime_scope_id="t1",
            agent_slug="main",
            uid="user-1",
            status=status,
            request_id="active-request",
            conversation_id=10,
            run_type=run_type,
            created_by_run_id="interrupted-run" if run_type == "resume" else None,
            input_payload={},
        )
    )
    await session.commit()


async def _create_request(session, *, request_id, uid="user-1", msg_id=100, queue_policy="enqueue"):
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository

    repo = AgentRunRequestRepository(session)
    await repo.create(
        request_id=request_id,
        uid=uid,
        agent_slug="main",
        conversation_thread_id="t1",
        input_message_id=msg_id,
        queue_policy=queue_policy,
    )
    await session.commit()
    return repo


# ── steer reuses the queued request model ──


@pytest.mark.asyncio
async def test_steer_request_is_prioritized_without_new_status(session):
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository

    await _seed_thread(session)
    session.add(Message(id=101, conversation_id=10, role="user", content="steer"))
    await session.commit()
    await _create_request(session, request_id="request-enqueue")
    await _create_request(session, request_id="request-steer", msg_id=101, queue_policy="steer")

    repo = AgentRunRequestRepository(session)
    queued = await repo.list_queued(uid="user-1", agent_slug="main", conversation_thread_id="t1")

    assert [request.request_id for request in queued] == ["request-steer", "request-enqueue"]
    assert queued[0].status == "queued"
    assert await repo.get_queue_position("request-steer") == 1
    assert await repo.get_queue_position("request-enqueue") == 2


@pytest.mark.asyncio
async def test_queued_request_can_be_upgraded_to_steer(session):
    await _seed_thread(session)
    await _seed_active_run(session)
    await _create_request(session, request_id="request-upgrade")

    result = await steer_queued_request(request_id="request-upgrade", current_uid="user-1", db=session)

    request = await session.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == "request-upgrade"))
    assert result["status"] == "queued"
    assert result["queue_policy"] == "steer"
    assert result["queue_position"] == 1
    assert request.queue_policy == "steer"

    replay, _ = await _persist_request(
        db=session,
        request_input=AgentRequestInput(
            agent_slug="main",
            thread_id="t1",
            request_id="request-upgrade",
            input_message=build_chat_input_message("changed input is ignored"),
            origin=RunOrigin(source="chat", channel="web"),
            queue_policy="enqueue",
            model_spec="missing:ignored",
        ),
        current_user=SimpleNamespace(uid="user-1"),
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
    )
    assert replay.queue_policy == "steer"
    assert replay.input_message_id == result["message_id"]
    assert (await session.get(Message, result["message_id"])).content == "hi"


@pytest.mark.asyncio
async def test_queued_request_upgrade_requires_running_main_chat(session):
    from fastapi import HTTPException

    await _seed_thread(session)
    await _create_request(session, request_id="request-upgrade")

    with pytest.raises(HTTPException) as exc_info:
        await steer_queued_request(request_id="request-upgrade", current_uid="user-1", db=session)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "run_not_steerable"


@pytest.mark.asyncio
async def test_second_pending_steer_is_rejected(session):
    from fastapi import HTTPException

    await _seed_thread(session)
    await _seed_active_run(session)
    session.add(Message(id=102, conversation_id=10, role="user", content="next"))
    await session.commit()
    await _create_request(session, request_id="request-steer", queue_policy="steer")
    await _create_request(session, request_id="request-next", msg_id=102)

    with pytest.raises(HTTPException) as exc_info:
        await steer_queued_request(request_id="request-next", current_uid="user-1", db=session)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "steer_already_pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "status", "run_type"),
    [
        ("agent_call", "running", "chat"),
        ("chat", "running", "resume"),
        ("chat", "cancel_requested", "chat"),
        ("chat", "pending", "chat"),
    ],
)
async def test_steer_rejects_unsupported_active_run_before_persisting(session, source, status, run_type):
    from fastapi import HTTPException
    from yuxi.services.input_message_service import build_chat_input_message

    await _seed_thread(session)
    await _seed_active_run(session, source=source, status=status, run_type=run_type)

    with pytest.raises(HTTPException) as exc_info:
        await _persist_request(
            db=session,
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
            request_input=AgentRequestInput(
                request_id="request-steer",
                agent_slug="main",
                thread_id="t1",
                input_message=build_chat_input_message("steer"),
                queue_policy="steer",
                origin=RunOrigin(source="chat", channel="web"),
            ),
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "run_not_steerable"
    assert (
        await session.scalar(
            select(sa_func.count()).select_from(AgentRunRequest).where(AgentRunRequest.request_id == "request-steer")
        )
        == 0
    )


@pytest.mark.asyncio
async def test_pending_steer_cannot_be_cancelled_until_active_run_finishes(session):
    from fastapi import HTTPException
    from yuxi.storage.postgres.models_business import AgentRun

    await _seed_thread(session)
    await _seed_active_run(session)
    await _create_request(session, request_id="request-steer", queue_policy="steer")

    with pytest.raises(HTTPException) as exc_info:
        await cancel_queued_request(request_id="request-steer", current_uid="user-1", db=session)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "steer_in_progress"

    active_run = await session.get(AgentRun, "active-run")
    active_run.status = "cancelled"
    active_run.finished_at = utc_now_naive()
    await session.flush()

    assert await cancel_queued_request(request_id="request-steer", current_uid="user-1", db=session) == "cancelled"


# ── cancel_queued_request ──


@pytest.mark.asyncio
async def test_cancel_returns_404_for_missing(session):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await cancel_queued_request(request_id="nope", current_uid="user-1", db=session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_cancel_returns_404_for_wrong_user(session):
    from fastapi import HTTPException

    await _seed_thread(session)
    await _create_request(session, request_id="req-1")

    with pytest.raises(HTTPException) as exc_info:
        await cancel_queued_request(request_id="req-1", current_uid="user-2", db=session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("already_cancelled", [False, True])
async def test_cancel_returns_cancelled_status(session, already_cancelled):
    await _seed_thread(session)
    await _create_request(session, request_id="req-1")
    if already_cancelled:
        from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository

        repo = AgentRunRequestRepository(session)
        request = await repo.lock_by_request_id("req-1")
        request.status = "cancelled"
        request.updated_at = utc_now_naive()
        await session.commit()
    status = await cancel_queued_request(request_id="req-1", current_uid="user-1", db=session)
    assert status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_dispatched_raises_409(session):
    from fastapi import HTTPException

    await _seed_thread(session)
    repo = await _create_request(session, request_id="req-1")
    await repo.mark_dispatched("req-1", run_id="run-abc")
    await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await cancel_queued_request(request_id="req-1", current_uid="user-1", db=session)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "request_already_dispatched"


# ── idempotency ──


@pytest.mark.asyncio
async def test_intake_idempotent_returns_existing(session):
    from yuxi.services.input_message_service import build_chat_input_message

    await _seed_thread(session)
    await _create_request(session, request_id="req-idem")
    binding = WorkdirBinding(
        conversation_id=10,
        thread_id="t1",
        uid="user-1",
        project_id="project-user-1-t1",
        workdir_path="projects/workdir-user-1-t1",
        directory_mode="managed",
    )

    result, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        workdir_binding=binding,
        request_input=AgentRequestInput(
            request_id="req-idem",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("hello"),
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )
    assert result.request_id == "req-idem"
    assert result.status == "queued"
    assert result.input_message_id == 100

    count = await session.scalar(
        select(sa_func.count(AgentRunRequest.id)).where(AgentRunRequest.request_id == "req-idem")
    )
    assert count == 1


@pytest.mark.asyncio
async def test_intake_idempotent_rejects_cross_user(session):
    from fastapi import HTTPException

    from yuxi.services.input_message_service import build_chat_input_message

    await _seed_thread(session)
    await _create_request(session, request_id="req-cross")

    with pytest.raises(HTTPException) as exc_info:
        await _persist_request(
            db=session,
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
            request_input=AgentRequestInput(
                request_id="req-cross",
                agent_slug="main",
                thread_id="t1",
                input_message=build_chat_input_message("hello"),
                origin=RunOrigin(source="chat", channel="web"),
            ),
            current_user=SimpleNamespace(uid="user-2"),
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_intake_idempotent_rejects_scope_mismatch(session):
    from fastapi import HTTPException

    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import Conversation

    await _seed_thread(session)
    session.add(
        Conversation(
            id=11,
            thread_id="t2",
            project_id="project-user-1-t2",
            uid="user-1",
            agent_id="other",
            status="active",
        )
    )
    await _create_request(session, request_id="req-scope")

    with pytest.raises(HTTPException) as exc_info:
        await _persist_request(
            db=session,
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
            request_input=AgentRequestInput(
                request_id="req-scope",
                agent_slug="other",
                thread_id="t2",
                input_message=build_chat_input_message("different request"),
                queue_policy="reject",
                origin=RunOrigin(source="chat", channel="web"),
            ),
            current_user=SimpleNamespace(uid="user-1"),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "request_id_conflict"


# ── delivery_status: create_message ──


@pytest.mark.asyncio
async def test_create_message_with_queued_delivery_status(session):
    from yuxi.services.agent_run_service import create_agent_run_input_message
    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import Message

    await _seed_thread(session)
    msg = await create_agent_run_input_message(
        db=session,
        conversation_id=10,
        request_id="req-delivery",
        input_message=build_chat_input_message("hello"),
        delivery_status="queued",
    )
    await session.commit()
    loaded = await session.get(Message, msg.id)
    assert loaded.delivery_status == "queued"


# ── dispatch sets delivery_status=dispatched (Fix 2) ──


@pytest.mark.asyncio
async def test_dispatch_sets_delivery_status_dispatched(session):
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
    from yuxi.services.agent_request_queue_service import dispatch_ready_head
    from yuxi.storage.postgres.models_business import Message

    await _seed_thread(session, msg_id=200)
    repo = AgentRunRequestRepository(session)
    await repo.create(
        request_id="req-dispatch-test",
        uid="user-1",
        agent_slug="main",
        conversation_thread_id="t1",
        input_message_id=200,
    )
    await session.commit()

    dispatched = await dispatch_ready_head(
        db=session,
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        workdir_binding=WorkdirBinding(
            conversation_id=10,
            thread_id="t1",
            uid="user-1",
            project_id="project-user-1-t1",
            workdir_path="projects/workdir-user-1-t1",
            directory_mode="managed",
        ),
    )
    assert dispatched is not None

    msg = await session.get(Message, 200)
    assert msg.run_id == dispatched.run_id
    assert msg.delivery_status == "dispatched"


@pytest.mark.asyncio
async def test_dispatches_multiple_queued_requests_one_at_a_time(session):
    from yuxi.repositories.agent_run_repository import AgentRunRepository
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
    from yuxi.services.agent_request_queue_service import dispatch_ready_head

    await _seed_thread(session, msg_id=300)
    session.add_all(
        [
            Message(id=301, conversation_id=10, role="user", content="B", delivery_status="queued"),
            Message(id=302, conversation_id=10, role="user", content="C", delivery_status="queued"),
        ]
    )
    request_repo = AgentRunRequestRepository(session)
    for request_id, message_id in (("request-b", 301), ("request-c", 302)):
        await request_repo.create(
            request_id=request_id,
            uid="user-1",
            agent_slug="main",
            conversation_thread_id="t1",
            input_message_id=message_id,
        )
    await session.commit()

    dispatched_b = await dispatch_ready_head(
        db=session,
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        workdir_binding=WorkdirBinding(
            conversation_id=10,
            thread_id="t1",
            uid="user-1",
            project_id="project-user-1-t1",
            workdir_path="projects/workdir-user-1-t1",
            directory_mode="managed",
        ),
    )
    await session.commit()
    assert dispatched_b is not None
    run_b = dispatched_b.run_id
    assert (await request_repo.get_by_request_id("request-b")).dispatched_run_id == run_b
    assert await request_repo.get_queue_position("request-c") == 1

    run_repository = AgentRunRepository(session)
    worker_id = "queue-test-worker"
    _run, acquired = await run_repository.mark_running(
        run_b,
        worker_id=worker_id,
        lease_seconds=60,
    )
    assert acquired is True
    output_message = Message(
        conversation_id=10,
        run_id=run_b,
        request_id="request-b",
        role="assistant",
        content="B complete",
    )
    session.add(output_message)
    await session.flush()
    await run_repository.set_output_message(
        run_b,
        output_message.id,
        worker_id=worker_id,
    )
    _run, completed = await run_repository.set_terminal_status(
        run_b,
        status="completed",
        worker_id=worker_id,
    )
    assert completed is True
    await session.commit()
    blocked_c = await dispatch_ready_head(
        db=session,
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        workdir_binding=WorkdirBinding(
            conversation_id=10,
            thread_id="t1",
            uid="user-1",
            project_id="project-user-1-t1",
            workdir_path="projects/workdir-user-1-t1",
            directory_mode="managed",
        ),
    )
    assert blocked_c is None
    persisted_b = await run_repository.get_run(run_b)
    persisted_b.runtime_cleanup_pending = False
    await session.commit()
    dispatched_c = await dispatch_ready_head(
        db=session,
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        workdir_binding=WorkdirBinding(
            conversation_id=10,
            thread_id="t1",
            uid="user-1",
            project_id="project-user-1-t1",
            workdir_path="projects/workdir-user-1-t1",
            directory_mode="managed",
        ),
    )
    await session.commit()

    assert dispatched_c is not None
    run_c = dispatched_c.run_id
    assert run_c != run_b
    assert (await request_repo.get_by_request_id("request-c")).dispatched_run_id == run_c
    assert await request_repo.get_queue_position("request-c") == 0


# ── reject persists request + message (Fix 3) ──


@pytest.mark.asyncio
async def test_reject_with_active_run_persists_request_and_is_idempotent(session):
    import uuid as _uuid

    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import AgentRun, Message

    await _seed_thread(session)
    session.add(
        AgentRun(
            id=str(_uuid.uuid4()),
            conversation_thread_id="t1",
            runtime_scope_id="t1",
            agent_slug="main",
            uid="user-1",
            request_id="existing",
            input_payload={},
            status="running",
            run_type="chat",
        )
    )
    await session.commit()

    first, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="req-reject",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("hello"),
            queue_policy="reject",
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )
    await session.commit()
    assert first.status == "rejected"
    assert first.input_message_id is not None

    req = await session.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == "req-reject"))
    assert req is not None
    assert req.status == "rejected"

    msg = await session.get(Message, first.input_message_id)
    assert msg.delivery_status == "rejected"

    second, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="req-reject",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("hello"),
            queue_policy="reject",
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )
    assert second.status == "rejected"
    assert second.input_message_id == first.input_message_id


# ── queue snapshot and manual continue ──


async def _seed_queued_request(session, *, request_id: str, message_id: int, created_at):
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository

    session.add(Message(id=message_id, conversation_id=10, role="user", content=request_id, delivery_status="queued"))
    await session.flush()
    request = await AgentRunRequestRepository(session).create(
        request_id=request_id,
        uid="user-1",
        agent_slug="main",
        conversation_thread_id="t1",
        input_message_id=message_id,
    )
    request.created_at = created_at
    request.updated_at = created_at
    await session.flush()
    return request


async def _seed_terminal_run(session, *, run_id: str, status: str, created_at, finished_at):
    from yuxi.storage.postgres.models_business import AgentRun

    session.add(
        AgentRun(
            id=run_id,
            conversation_thread_id="t1",
            runtime_scope_id="t1",
            agent_slug="main",
            uid="user-1",
            request_id=f"request-{run_id}",
            input_payload={},
            status=status,
            run_type="chat",
            created_at=created_at,
            finished_at=finished_at,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_snapshot_marks_existing_backlog_paused_after_failed_run(session):
    from yuxi.services.agent_request_queue_service import get_thread_queue_snapshot

    await _seed_thread(session)
    now = utc_now_naive()
    await _seed_queued_request(session, request_id="request-b", message_id=101, created_at=now - timedelta(seconds=2))
    await _seed_terminal_run(
        session,
        run_id="run-a",
        status="failed",
        created_at=now - timedelta(seconds=3),
        finished_at=now,
    )
    await session.commit()

    snapshot = await get_thread_queue_snapshot(db=session, uid="user-1", agent_slug="main", thread_id="t1")

    assert snapshot["queue"] == {
        "status": "paused",
        "paused_reason": "failed",
        "blocking_run_id": "run-a",
        "can_continue": True,
    }


@pytest.mark.asyncio
async def test_snapshot_marks_interrupted_queue_as_non_continuable(session):
    from yuxi.services.agent_request_queue_service import get_thread_queue_snapshot

    await _seed_thread(session)
    now = utc_now_naive()
    await _seed_queued_request(session, request_id="request-b", message_id=101, created_at=now)
    await _seed_terminal_run(
        session,
        run_id="run-a",
        status="interrupted",
        created_at=now - timedelta(seconds=1),
        finished_at=now,
    )
    await session.commit()

    snapshot = await get_thread_queue_snapshot(db=session, uid="user-1", agent_slug="main", thread_id="t1")

    assert snapshot["queue"]["status"] == "interrupted"
    assert snapshot["queue"]["blocking_run_id"] == "run-a"
    assert snapshot["queue"]["can_continue"] is False


@pytest.mark.asyncio
async def test_snapshot_marks_post_failure_request_ready(session):
    from yuxi.services.agent_request_queue_service import get_thread_queue_snapshot

    await _seed_thread(session)
    now = utc_now_naive()
    await _seed_terminal_run(
        session,
        run_id="run-a",
        status="cancelled",
        created_at=now - timedelta(seconds=2),
        finished_at=now - timedelta(seconds=1),
    )
    await _seed_queued_request(session, request_id="request-b", message_id=101, created_at=now)
    await session.commit()

    snapshot = await get_thread_queue_snapshot(db=session, uid="user-1", agent_slug="main", thread_id="t1")

    assert snapshot["queue"]["status"] == "ready"
    assert snapshot["queue"]["can_continue"] is False


@pytest.mark.asyncio
async def test_snapshot_rejects_terminal_run_without_finished_at(session):
    from yuxi.services.agent_request_queue_service import get_thread_queue_snapshot

    await _seed_thread(session)
    now = utc_now_naive()
    await _seed_terminal_run(
        session,
        run_id="run-a",
        status="failed",
        created_at=now - timedelta(seconds=1),
        finished_at=None,
    )
    await _seed_queued_request(session, request_id="request-b", message_id=101, created_at=now)
    await session.commit()

    with pytest.raises(RuntimeError, match="run-a.*missing finished_at"):
        await get_thread_queue_snapshot(db=session, uid="user-1", agent_slug="main", thread_id="t1")


@pytest.mark.asyncio
async def test_continue_dispatches_only_paused_fifo_head(session):
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
    from yuxi.services.agent_request_queue_service import continue_thread_queue

    await _seed_thread(session)
    now = utc_now_naive()
    await _seed_queued_request(session, request_id="request-b", message_id=101, created_at=now - timedelta(seconds=2))
    await _seed_queued_request(session, request_id="request-c", message_id=102, created_at=now - timedelta(seconds=1))
    await _seed_terminal_run(
        session,
        run_id="run-a",
        status="cancelled",
        created_at=now - timedelta(seconds=3),
        finished_at=now,
    )
    await session.commit()

    dispatched = await continue_thread_queue(
        db=session,
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
    )

    repo = AgentRunRequestRepository(session)
    assert dispatched.request_id == "request-b"
    assert dispatched.workdir_binding.uid == "user-1"
    assert dispatched.workdir_binding.workdir_path == "projects/workdir-user-1-t1"
    assert dispatched.workdir_binding.materialize_managed is True
    assert (await repo.get_by_request_id("request-b")).status == "dispatched"
    assert await repo.get_queue_position("request-c") == 1


@pytest.mark.asyncio
async def test_reject_does_not_resume_paused_queue(session):
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
    from yuxi.services.input_message_service import build_chat_input_message

    await _seed_thread(session)
    now = utc_now_naive()
    await _seed_queued_request(session, request_id="request-b", message_id=101, created_at=now - timedelta(seconds=2))
    await _seed_terminal_run(
        session,
        run_id="run-a",
        status="failed",
        created_at=now - timedelta(seconds=3),
        finished_at=now,
    )
    await session.commit()

    result, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="request-c",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("C"),
            queue_policy="reject",
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )

    repo = AgentRunRequestRepository(session)
    assert result.status == "rejected"
    assert (await repo.get_by_request_id("request-b")).status == "queued"
    assert (await repo.get_by_request_id("request-c")).status == "rejected"


@pytest.mark.asyncio
async def test_reject_marks_request_rejected_when_immediate_dispatch_loses_race(
    session,
    monkeypatch: pytest.MonkeyPatch,
):
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
    from yuxi.services.input_message_service import build_chat_input_message

    async def resolve_config(*_args):
        return "model", "default"

    monkeypatch.setattr(agent_request_service, "resolve_agent_run_config", resolve_config)

    async def lose_dispatch_race(**kwargs):
        return None

    monkeypatch.setattr(agent_request_service, "dispatch_ready_head", lose_dispatch_race)
    await _seed_thread(session)

    result, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="request-reject",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("reject me"),
            queue_policy="reject",
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )

    request = await AgentRunRequestRepository(session).get_by_request_id("request-reject")
    message = await session.get(Message, result.input_message_id)
    assert result.status == "rejected"
    assert request.status == "rejected"
    assert request.input_payload == {}
    assert message.delivery_status == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_policy", ["enqueue", "reject"])
async def test_intake_rejects_message_while_run_is_interrupted(
    session, monkeypatch: pytest.MonkeyPatch, queue_policy: str
):
    from fastapi import HTTPException
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
    from yuxi.services.input_message_service import build_chat_input_message

    async def resolve_config(*_args):
        return "model", "default"

    monkeypatch.setattr(agent_request_service, "resolve_agent_run_config", resolve_config)
    await _seed_thread(session)
    now = utc_now_naive()
    await _seed_queued_request(session, request_id="request-b", message_id=101, created_at=now)
    await _seed_terminal_run(
        session,
        run_id="run-a",
        status="interrupted",
        created_at=now - timedelta(seconds=1),
        finished_at=now,
    )
    await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await _persist_request(
            db=session,
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
            request_input=AgentRequestInput(
                request_id="request-c",
                agent_slug="main",
                thread_id="t1",
                input_message=build_chat_input_message("C"),
                queue_policy=queue_policy,
                origin=RunOrigin(source="chat", channel="web"),
            ),
            current_user=SimpleNamespace(uid="user-1"),
        )

    repo = AgentRunRequestRepository(session)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "run_interrupted",
        "message": "线程正在等待用户回答或审批",
    }
    assert (await repo.get_by_request_id("request-b")).status == "queued"
    assert await repo.get_by_request_id("request-c") is None
    message_count = await session.scalar(select(sa_func.count()).select_from(Message).where(Message.content == "C"))
    assert message_count == 0


@pytest.mark.asyncio
async def test_enqueue_after_empty_failed_queue_dispatches_new_request(session, monkeypatch: pytest.MonkeyPatch):
    from yuxi.services.input_message_service import build_chat_input_message

    async def resolve_config(*_args):
        return "model", "default"

    monkeypatch.setattr(agent_request_service, "resolve_agent_run_config", resolve_config)
    await _seed_thread(session)
    now = utc_now_naive()
    await _seed_terminal_run(
        session,
        run_id="run-a",
        status="failed",
        created_at=now - timedelta(seconds=2),
        finished_at=now - timedelta(seconds=1),
    )
    await session.commit()

    result, _ = await _persist_request(
        db=session,
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
        request_input=AgentRequestInput(
            request_id="request-b",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("B"),
            origin=RunOrigin(source="chat", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )

    assert result.status == "dispatched"
    assert result.dispatched_run_id is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_model", "configured_model", "expected_model"),
    [
        ("request:model", "agent:model", "request:model"),
        (None, "agent:model", "agent:model"),
        (None, "", "system:model"),
    ],
)
async def test_intake_persists_multimodal_input_and_effective_config(
    session, monkeypatch, requested_model, configured_model, expected_model
):
    """普通消息通过 Request 保存图片、命名空间元数据和最终选用的配置。"""
    from yuxi.agents.context import BaseContext
    from yuxi.services import agent_run_service
    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import AgentRun

    async def system_defaults(_options, _db=None):
        """提供确定的系统模型默认值。"""
        return {"default_model": "system:model"}

    monkeypatch.setattr(type(agent_run_service.system_options), "get", system_defaults)
    monkeypatch.setattr(
        agent_run_service.model_cache, "get_model_info", lambda _spec: SimpleNamespace(model_type="chat")
    )
    await _seed_thread(session)
    result, _ = await _persist_request(
        db=session,
        agent_item=SimpleNamespace(config_json={"context": {"model": configured_model}}),
        agent_backend=SimpleNamespace(context_schema=BaseContext),
        request_input=AgentRequestInput(
            request_id="multimodal-request",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("看图", "base64-image"),
            model_spec=requested_model,
            tool_approval_mode="always_trust",
            request_metadata={
                "agent_invocation_meta": {"trace_id": "trace-1"},
                "evaluation": {"legacy": True},
                "custom_variables": {"legacy": True},
            },
            origin=RunOrigin(source="agent_call", channel="web"),
        ),
        current_user=SimpleNamespace(uid="user-1"),
    )
    await session.commit()
    request_id = result.request_id
    session.expire_all()
    request = await session.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == request_id))
    run = await session.get(AgentRun, result.dispatched_run_id)
    message = await session.get(Message, result.input_message_id)
    assert request.dispatched_run_id == run.id == message.run_id
    assert request.input_message_id == run.input_message_id == message.id
    assert (
        request.input_payload
        == run.input_payload
        == {
            "model_spec": expected_model,
            "tool_approval_mode": "always_trust",
        }
    )
    assert message.message_type == "multimodal_image"
    assert message.image_content == "base64-image"
    assert message.extra_metadata["raw_message"]["content"] == [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,base64-image"}},
    ]
    assert message.extra_metadata["source"] == "agent_call"
    assert message.extra_metadata["agent_invocation_meta"] == {"trace_id": "trace-1"}
    assert "evaluation" not in message.extra_metadata
    assert "custom_variables" not in message.extra_metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy, active, expected, older",
    [
        ("enqueue", False, "dispatched", False),
        ("enqueue", True, "queued", False),
        ("reject", True, "rejected", False),
        ("enqueue", False, "queued", True),
    ],
)
async def test_submission_publishes_committed_request_and_replays_same_view(
    session, monkeypatch, policy, active, expected, older
):
    """完整入口提交后可读持久化消息；重发保持首次输入与当前视图。"""
    from unittest.mock import AsyncMock
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository

    await _seed_thread(session)
    if active:
        await _seed_active_run(session)
    if older:
        await _create_request(session, request_id="request-older")
    agent = SimpleNamespace(slug="main", backend_id="ChatbotAgent")
    monkeypatch.setattr(agent_request_service.AgentRepository, "get_visible_by_slug", AsyncMock(return_value=agent))
    monkeypatch.setattr(agent_request_service, "get_agent_backend", lambda _: object())
    monkeypatch.setattr(
        agent_request_service, "resolve_agent_run_config", AsyncMock(return_value=("provider:model", "default"))
    )
    effects = []

    def materialize(*_):
        assert not session.in_transaction()
        effects.append("materialize")

    async def enqueue(run_id):
        assert not session.in_transaction()
        request = await AgentRunRequestRepository(session).get_by_request_id(
            "request-older" if older else "request-complete"
        )
        message = await session.get(Message, request.input_message_id)
        assert request.status == "dispatched"
        assert request.dispatched_run_id == message.run_id == run_id
        assert message.content == ("hi" if older else "first input")
        effects.append("enqueue")

    monkeypatch.setattr(agent_request_service, "ensure_bound_user_workdir", materialize)
    monkeypatch.setattr(agent_request_service, "enqueue_agent_run", enqueue)
    request_input = AgentRequestInput(
        request_id="request-complete",
        agent_slug="main",
        thread_id="t1",
        input_message=build_chat_input_message("first input"),
        origin=RunOrigin(source="chat", channel="web"),
        queue_policy=policy,
    )
    user = SimpleNamespace(uid="user-1")
    result = await agent_request_service.submit_agent_request(
        request_input=request_input, current_user=user, db=session
    )
    assert result["status"] == expected
    assert effects == (["materialize", "enqueue"] if expected == "dispatched" or older else ["materialize"])
    session.expire_all()
    from dataclasses import replace

    replay = await agent_request_service.submit_agent_request(
        request_input=replace(
            request_input, input_message=build_chat_input_message("ignored"), model_spec="invalid:ignored"
        ),
        current_user=user,
        db=session,
    )
    assert replay == result
    assert (await session.get(Message, result["message_id"])).content == "first input"
    assert effects == (["materialize", "enqueue"] if expected == "dispatched" or older else ["materialize"])
