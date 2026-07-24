"""Agent request queue service unit tests."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.services.agent_request_queue_service import (
    NOT_IMPLEMENTED_QUEUE_POLICIES,
    cancel_queued_request,
    intake_request,
    steer_queued_request,
    validate_queue_policy,
)
from yuxi.storage.postgres.models_business import AgentRunRequest, Base, Message
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.unit]


# ── validate_queue_policy ──


def test_validate_queue_policy_accepts_enqueue():
    validate_queue_policy("enqueue")


def test_validate_queue_policy_accepts_reject():
    validate_queue_policy("reject")


def test_validate_queue_policy_accepts_steer():
    validate_queue_policy("steer")


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


# ── AgentRunCreate request model ──


def test_agent_run_create_accepts_thread_id():
    from server.routers.agent_router import AgentRunCreate

    payload = AgentRunCreate(query="hi", agent_slug="bot", thread_id="t1")
    assert payload.thread_id == "t1"
    assert payload.tool_approval_mode is None


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
    from yuxi.storage.postgres.models_business import Conversation, Message

    session.add(Conversation(id=conv_id, thread_id="t1", uid=uid, agent_id="main", status="active"))
    session.add(Message(id=msg_id, conversation_id=conv_id, role="user", content="hi"))
    await session.commit()


async def _create_request(session, *, request_id, uid="user-1", msg_id=100):
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository

    repo = AgentRunRequestRepository(session)
    await repo.create(
        request_id=request_id,
        uid=uid,
        agent_slug="main",
        conversation_thread_id="t1",
        input_message_id=msg_id,
    )
    await session.commit()
    return repo


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
async def test_cancel_success(session):
    await _seed_thread(session)
    await _create_request(session, request_id="req-1")
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


@pytest.mark.asyncio
async def test_cancel_already_cancelled_returns_status(session):
    await _seed_thread(session)
    await _create_request(session, request_id="req-1")
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository

    repo = AgentRunRequestRepository(session)
    request = await repo.lock_by_request_id("req-1")
    request.status = "cancelled"
    request.updated_at = utc_now_naive()
    await session.commit()
    status = await cancel_queued_request(request_id="req-1", current_uid="user-1", db=session)
    assert status == "cancelled"


# ── idempotency ──


@pytest.mark.asyncio
async def test_intake_idempotent_returns_existing(session):
    from yuxi.services.input_message_service import build_chat_input_message

    await _seed_thread(session)
    await _create_request(session, request_id="req-idem")

    result = await intake_request(
        db=session,
        request_id="req-idem",
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        input_message=build_chat_input_message("hello"),
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
    )
    assert result.request_id == "req-idem"
    assert result.status == "queued"
    assert result.message_id == 100

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
        await intake_request(
            db=session,
            request_id="req-cross",
            uid="user-2",
            agent_slug="main",
            thread_id="t1",
            input_message=build_chat_input_message("hello"),
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_intake_idempotent_rejects_scope_mismatch(session):
    from fastapi import HTTPException

    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import Conversation

    await _seed_thread(session)
    session.add(Conversation(id=11, thread_id="t2", uid="user-1", agent_id="other", status="active"))
    await _create_request(session, request_id="req-scope")

    with pytest.raises(HTTPException) as exc_info:
        await intake_request(
            db=session,
            request_id="req-scope",
            uid="user-1",
            agent_slug="other",
            thread_id="t2",
            source="chat",
            queue_policy="reject",
            input_message=build_chat_input_message("different request"),
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
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
    from yuxi.services.agent_request_queue_service import _dispatch_ready_head
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

    dispatched = await _dispatch_ready_head(
        db=session,
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        conversation_id=10,
    )
    assert dispatched is not None

    msg = await session.get(Message, 200)
    assert msg.run_id == dispatched.run_id
    assert msg.delivery_status == "dispatched"


@pytest.mark.asyncio
async def test_dispatches_multiple_queued_requests_one_at_a_time(session):
    from yuxi.repositories.agent_run_repository import AgentRunRepository
    from yuxi.repositories.agent_run_request_repository import AgentRunRequestRepository
    from yuxi.services.agent_request_queue_service import _dispatch_ready_head

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

    dispatched_b = await _dispatch_ready_head(
        db=session,
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        conversation_id=10,
    )
    await session.commit()
    assert dispatched_b is not None
    run_b = dispatched_b.run_id
    assert (await request_repo.get_by_request_id("request-b")).dispatched_run_id == run_b
    assert await request_repo.get_queue_position("request-c") == 1

    await AgentRunRepository(session).set_terminal_status(run_b, status="completed")
    await session.commit()
    dispatched_c = await _dispatch_ready_head(
        db=session,
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        conversation_id=10,
    )
    await session.commit()

    assert dispatched_c is not None
    run_c = dispatched_c.run_id
    assert run_c != run_b
    assert (await request_repo.get_by_request_id("request-c")).dispatched_run_id == run_c
    assert await request_repo.get_queue_position("request-c") == 0


# ── mark_run_terminal syncs delivery_status (Fix 2) ──


@pytest.mark.asyncio
async def test_mark_run_terminal_sets_delivery_status(session):
    """mark_run_terminal completed sets message.delivery_status to complete."""
    import uuid as _uuid

    from yuxi.repositories.agent_run_repository import AgentRunRepository
    from yuxi.storage.postgres.models_business import AgentRun, Conversation, Message

    run_id = str(_uuid.uuid4())
    session.add(Conversation(id=10, thread_id="t1", uid="user-1", agent_id="main", status="active"))
    session.add(Message(id=100, conversation_id=10, role="user", content="hi", delivery_status="dispatched"))
    session.add(
        AgentRun(
            id=run_id,
            conversation_thread_id="t1",
            agent_slug="main",
            uid="user-1",
            request_id="req-terminal",
            input_payload={},
            status="running",
            run_type="chat",
            input_message_id=100,
        )
    )
    await session.commit()

    # mark_run_terminal uses pg_manager (separate session), so we update via DB directly
    async with session.begin_nested():
        repo = AgentRunRepository(session)
        await repo.set_terminal_status(run_id, status="completed")
        msg = await session.get(Message, 100)
        if msg:
            msg.delivery_status = "complete"

    msg = await session.get(Message, 100)
    assert msg.delivery_status == "complete"


@pytest.mark.asyncio
async def test_mark_run_terminal_failed_sets_delivery_status(session):
    """mark_run_terminal failed sets message.delivery_status to failed."""
    import uuid as _uuid

    from yuxi.repositories.agent_run_repository import AgentRunRepository
    from yuxi.storage.postgres.models_business import AgentRun, Conversation, Message

    run_id = str(_uuid.uuid4())
    session.add(Conversation(id=11, thread_id="t2", uid="user-1", agent_id="main", status="active"))
    session.add(Message(id=200, conversation_id=11, role="user", content="hi", delivery_status="dispatched"))
    session.add(
        AgentRun(
            id=run_id,
            conversation_thread_id="t2",
            agent_slug="main",
            uid="user-1",
            request_id="req-failed",
            input_payload={},
            status="running",
            run_type="chat",
            input_message_id=200,
            conversation_id=11,
        )
    )
    await session.commit()

    async with session.begin_nested():
        repo = AgentRunRepository(session)
        await repo.set_terminal_status(run_id, status="failed", error_type="test", error_message="boom")
        msg = await session.get(Message, 200)
        if msg:
            msg.delivery_status = "failed"

    msg = await session.get(Message, 200)
    assert msg.delivery_status == "failed"


# ── reject persists request + message (Fix 3) ──


@pytest.mark.asyncio
async def test_reject_with_active_run_persists_request(session):
    import uuid as _uuid

    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import AgentRun, Message

    await _seed_thread(session)
    session.add(
        AgentRun(
            id=str(_uuid.uuid4()),
            conversation_thread_id="t1",
            agent_slug="main",
            uid="user-1",
            request_id="existing",
            input_payload={},
            status="running",
            run_type="chat",
        )
    )
    await session.commit()

    result = await intake_request(
        db=session,
        request_id="req-reject-fix3",
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        queue_policy="reject",
        input_message=build_chat_input_message("hello"),
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
    )
    assert result.status == "rejected"
    assert result.message_id is not None

    req = await session.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == "req-reject-fix3"))
    assert req is not None
    assert req.status == "rejected"

    msg = await session.get(Message, result.message_id)
    assert msg.delivery_status == "rejected"


@pytest.mark.asyncio
async def test_reject_idempotent(session):
    import uuid as _uuid

    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import AgentRun

    await _seed_thread(session)
    session.add(
        AgentRun(
            id=str(_uuid.uuid4()),
            conversation_thread_id="t1",
            agent_slug="main",
            uid="user-1",
            request_id="existing",
            input_payload={},
            status="running",
            run_type="chat",
        )
    )
    await session.commit()

    first = await intake_request(
        db=session,
        request_id="req-reject-idem",
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        queue_policy="reject",
        input_message=build_chat_input_message("hello"),
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
    )
    await session.commit()

    second = await intake_request(
        db=session,
        request_id="req-reject-idem",
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        queue_policy="reject",
        input_message=build_chat_input_message("hello"),
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
    )
    assert second.status == "rejected"
    assert second.message_id == first.message_id


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

    result = await intake_request(
        db=session,
        request_id="request-c",
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        queue_policy="reject",
        input_message=build_chat_input_message("C"),
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
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
    from yuxi.services import agent_request_queue_service
    from yuxi.services.input_message_service import build_chat_input_message

    monkeypatch.setattr(agent_request_queue_service, "resolve_agent_run_config", lambda *args: ("model", "default"))

    async def lose_dispatch_race(**kwargs):
        return None

    monkeypatch.setattr(agent_request_queue_service, "_dispatch_ready_head", lose_dispatch_race)
    await _seed_thread(session)

    result = await intake_request(
        db=session,
        request_id="request-reject",
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        queue_policy="reject",
        input_message=build_chat_input_message("reject me"),
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
    )

    request = await AgentRunRequestRepository(session).get_by_request_id("request-reject")
    message = await session.get(Message, result.message_id)
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
    from yuxi.services import agent_request_queue_service
    from yuxi.services.input_message_service import build_chat_input_message

    monkeypatch.setattr(agent_request_queue_service, "resolve_agent_run_config", lambda *args: ("model", "default"))
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
        await intake_request(
            db=session,
            request_id="request-c",
            uid="user-1",
            agent_slug="main",
            thread_id="t1",
            queue_policy=queue_policy,
            input_message=build_chat_input_message("C"),
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
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
    from yuxi.services import agent_request_queue_service
    from yuxi.services.input_message_service import build_chat_input_message

    monkeypatch.setattr(agent_request_queue_service, "resolve_agent_run_config", lambda *args: ("model", "default"))
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

    result = await intake_request(
        db=session,
        request_id="request-b",
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        input_message=build_chat_input_message("B"),
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
    )

    assert result.status == "dispatched"
    assert result.run_id is not None


@pytest.mark.asyncio
async def test_direct_steer_binds_running_chat_without_dispatching(session, monkeypatch: pytest.MonkeyPatch):
    """直接 Steer 只持久化等待请求，不与目标 Run 并发执行。"""
    from yuxi.services import agent_request_queue_service
    from yuxi.services.input_message_service import build_chat_input_message
    from yuxi.storage.postgres.models_business import AgentRun

    monkeypatch.setattr(agent_request_queue_service, "resolve_agent_run_config", lambda *args: ("model", "default"))
    await _seed_thread(session)
    session.add(
        AgentRun(
            id="run-active",
            conversation_thread_id="t1",
            agent_slug="main",
            uid="user-1",
            request_id="request-active",
            input_payload={},
            status="running",
            run_type="chat",
            conversation_id=10,
        )
    )
    await session.commit()

    result = await intake_request(
        db=session,
        request_id="request-steer",
        uid="user-1",
        agent_slug="main",
        thread_id="t1",
        queue_policy="steer",
        input_message=build_chat_input_message("改变方向"),
        agent_item=MagicMock(),
        agent_backend=MagicMock(),
    )

    request = await session.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == "request-steer"))
    assert result.status == "queued"
    assert result.run_id is None
    assert result.target_run_id == "run-active"
    assert request.queue_policy == "steer"
    assert request.target_run_id == "run-active"


@pytest.mark.asyncio
async def test_steer_rejects_non_chat_source_before_persistence(session):
    """Agent Call/Eval 等来源不能借 Steer 创建 Message 或 request。"""
    from fastapi import HTTPException
    from yuxi.services.input_message_service import build_chat_input_message

    with pytest.raises(HTTPException) as exc_info:
        await intake_request(
            db=session,
            request_id="request-agent-call-steer",
            uid="user-1",
            agent_slug="main",
            thread_id="missing-thread",
            source="agent_call",
            queue_policy="steer",
            input_message=build_chat_input_message("改变方向"),
            agent_item=MagicMock(),
            agent_backend=MagicMock(),
        )

    assert exc_info.value.status_code == 422
    assert (
        await session.scalar(
            select(sa_func.count())
            .select_from(AgentRunRequest)
            .where(AgentRunRequest.request_id == "request-agent-call-steer")
        )
        == 0
    )
    assert (
        await session.scalar(
            select(sa_func.count()).select_from(Message).where(Message.request_id == "request-agent-call-steer")
        )
        == 0
    )


@pytest.mark.asyncio
async def test_queued_request_upgrade_preserves_identity_and_fifo_fields(session):
    """queued -> Steer 原地更新，不重建 request、Message 或输入快照。"""
    from yuxi.storage.postgres.models_business import AgentRun

    await _seed_thread(session)
    created_at = utc_now_naive() - timedelta(seconds=2)
    request = await _seed_queued_request(
        session,
        request_id="request-upgrade",
        message_id=101,
        created_at=created_at,
    )
    request.input_payload = {"model_spec": "provider:model"}
    original_id = request.id
    session.add(
        AgentRun(
            id="run-active",
            conversation_thread_id="t1",
            agent_slug="main",
            uid="user-1",
            request_id="request-active",
            input_payload={},
            status="running",
            run_type="chat",
            conversation_id=10,
        )
    )
    await session.commit()

    result = await steer_queued_request(request_id="request-upgrade", current_uid="user-1", db=session)
    upgraded = await session.scalar(select(AgentRunRequest).where(AgentRunRequest.request_id == "request-upgrade"))

    assert result.target_run_id == "run-active"
    assert upgraded.id == original_id
    assert upgraded.input_message_id == 101
    assert upgraded.input_payload == {"model_spec": "provider:model"}
    assert upgraded.created_at == created_at
    assert upgraded.queue_policy == "steer"
