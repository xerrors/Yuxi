"""
Conversation thread status mapping and viewed-marking unit tests.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.conversation_repository import ConversationRepository, UNVIEWED_RUN_MARKER
from yuxi.services import conversation_service as svc
from yuxi.storage.postgres.models_business import AgentRun, Base, Conversation

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed_conversation(db, *, thread_id: str, last_viewed_run_id: str | None = None) -> Conversation:
    conversation = Conversation(
        thread_id=thread_id,
        uid="user-1",
        agent_id="main",
        title=f"conv-{thread_id}",
        status="active",
        extra_metadata={},
        last_viewed_run_id=last_viewed_run_id,
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def _seed_run(db, *, thread_id: str, run_id: str, status: str, run_type: str = "chat") -> AgentRun:
    run = AgentRun(
        id=run_id,
        conversation_thread_id=thread_id,
        agent_slug="main",
        uid="user-1",
        status=status,
        request_id=f"req-{run_id}",
        run_type=run_type,
        input_payload={},
    )
    db.add(run)
    await db.flush()
    return run


@pytest.mark.parametrize(
    ("run_id", "run_status", "last_viewed_run_id", "expected"),
    [
        (None, None, None, "done"),
        ("r1", "running", None, "loading"),
        ("r1", "pending", None, "loading"),
        ("r1", "cancel_requested", None, "loading"),
        ("r1", "completed", None, "ready"),
        ("r1", "completed", "r1", "done"),
        ("r1", "failed", None, "ready"),
        ("r1", "cancelled", None, "ready"),
        ("r1", "interrupted", None, "ready"),
        ("r1", "interrupted", "r1", "done"),
    ],
)
async def test_thread_status_mapping(run_id, run_status, last_viewed_run_id, expected):
    assert svc._thread_status(run_id, run_status, last_viewed_run_id) == expected


async def test_list_threads_view_maps_run_states(session):
    await _seed_conversation(session, thread_id="thread-running")
    await _seed_run(session, thread_id="thread-running", run_id="run-running", status="running")

    await _seed_conversation(session, thread_id="thread-ready")
    await _seed_run(session, thread_id="thread-ready", run_id="run-ready", status="completed")

    await _seed_conversation(session, thread_id="thread-done", last_viewed_run_id="run-done")
    await _seed_run(session, thread_id="thread-done", run_id="run-done", status="completed")

    await _seed_conversation(session, thread_id="thread-no-run")
    await session.commit()

    items = await svc.list_threads_view(db=session, current_uid="user-1", agent_slug=None, limit=100)
    status_by_id = {item["id"]: item["thread_status"] for item in items}

    assert status_by_id["thread-running"] == "loading"
    assert status_by_id["thread-ready"] == "ready"
    assert status_by_id["thread-done"] == "done"
    assert status_by_id["thread-no-run"] == "done"


async def test_list_threads_view_ignores_subagent_and_other_users(session):
    await _seed_conversation(session, thread_id="thread-main")
    await _seed_run(session, thread_id="thread-main", run_id="run-main", status="completed")
    await _seed_run(session, thread_id="thread-main", run_id="run-sub", status="running", run_type="subagent")
    await _seed_conversation(session, thread_id="thread-other-user")
    db = session
    db.add(
        AgentRun(
            id="run-other",
            conversation_thread_id="thread-other-user",
            agent_slug="main",
            uid="user-2",
            status="running",
            request_id="req-other",
            run_type="chat",
            input_payload={},
        )
    )
    await db.commit()

    items = await svc.list_threads_view(db=session, current_uid="user-1", agent_slug=None, limit=100)
    status_by_id = {item["id"]: item["thread_status"] for item in items}

    assert status_by_id["thread-main"] == "ready"
    assert status_by_id["thread-other-user"] == "done"


async def test_latest_run_wins_when_multiple_runs_exist(session):
    await _seed_conversation(session, thread_id="thread-latest", last_viewed_run_id="run-old")
    await _seed_run(session, thread_id="thread-latest", run_id="run-old", status="completed")
    await _seed_run(session, thread_id="thread-latest", run_id="run-new", status="completed")
    await session.commit()

    items = await svc.list_threads_view(db=session, current_uid="user-1", agent_slug=None, limit=100)
    item = next(item for item in items if item["id"] == "thread-latest")

    assert item["thread_status"] == "ready"


async def test_mark_thread_viewed_turns_ready_to_done(session):
    await _seed_conversation(session, thread_id="thread-view")
    await _seed_run(session, thread_id="thread-view", run_id="run-view", status="completed")
    await session.commit()

    before = await svc.list_threads_view(db=session, current_uid="user-1", agent_slug=None, limit=100)
    assert next(item for item in before if item["id"] == "thread-view")["thread_status"] == "ready"

    result = await svc.mark_thread_viewed_view(db=session, thread_id="thread-view", current_uid="user-1")
    assert result["thread_status"] == "done"

    after = await svc.list_threads_view(db=session, current_uid="user-1", agent_slug=None, limit=100)
    assert next(item for item in after if item["id"] == "thread-view")["thread_status"] == "done"


async def test_mark_thread_viewed_keeps_loading_when_run_active(session):
    await _seed_conversation(session, thread_id="thread-active")
    await _seed_run(session, thread_id="thread-active", run_id="run-active", status="running")
    await session.commit()

    result = await svc.mark_thread_viewed_view(db=session, thread_id="thread-active", current_uid="user-1")

    assert result["thread_status"] == "loading"


async def test_new_thread_creation_uses_unviewed_marker(session):
    conversation = await ConversationRepository(session).add_conversation(
        uid="user-1",
        agent_id="main",
        title="new-thread",
        thread_id="thread-new",
    )

    assert conversation.last_viewed_run_id == UNVIEWED_RUN_MARKER


async def test_marker_thread_with_terminal_run_shows_ready_then_done(session):
    await _seed_conversation(session, thread_id="thread-marker", last_viewed_run_id=UNVIEWED_RUN_MARKER)
    await _seed_run(session, thread_id="thread-marker", run_id="run-marker", status="completed")
    await session.commit()

    before = await svc.list_threads_view(db=session, current_uid="user-1", agent_slug=None, limit=100)
    assert next(item for item in before if item["id"] == "thread-marker")["thread_status"] == "ready"

    await svc.mark_thread_viewed_view(db=session, thread_id="thread-marker", current_uid="user-1")
    after = await svc.list_threads_view(db=session, current_uid="user-1", agent_slug=None, limit=100)
    assert next(item for item in after if item["id"] == "thread-marker")["thread_status"] == "done"
