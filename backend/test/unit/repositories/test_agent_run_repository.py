from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.storage.postgres.models_business import AgentRun, Base, Conversation, SubagentThread

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


async def _seed_subagent_runs(db, *, relation_child_thread_id: str = "child-thread") -> AgentRun:
    child_run = AgentRun(
        id="child-run",
        conversation_thread_id="child-thread",
        agent_slug="worker",
        uid="user-1",
        status="completed",
        request_id="child-req",
        conversation_id=20,
        created_by_run_id="parent-run",
        subagent_thread_relation_id=77,
        run_type="subagent",
        input_payload={},
    )
    db.add_all(
        [
            Conversation(id=10, thread_id="parent-thread", uid="user-1", agent_id="main", status="active"),
            Conversation(id=20, thread_id="child-thread", uid="user-1", agent_id="worker", status="subagent"),
            SubagentThread(
                id=77,
                uid="user-1",
                parent_conversation_id=10,
                child_conversation_id=20,
                child_thread_id=relation_child_thread_id,
                subagent_slug="worker",
                created_by_run_id="parent-run",
            ),
            AgentRun(
                id="parent-run",
                conversation_thread_id="parent-thread",
                agent_slug="main",
                uid="user-1",
                status="completed",
                request_id="parent-req",
                conversation_id=10,
                run_type="chat",
                input_payload={},
            ),
            child_run,
        ]
    )
    await db.commit()
    return child_run


async def test_get_subagent_run_for_creator_returns_child_run(session):
    child_run = await _seed_subagent_runs(session)

    result = await AgentRunRepository(session).get_subagent_run_for_creator(
        uid="user-1",
        created_by_run_id="parent-run",
        run_id="child-run",
    )

    assert result is child_run


async def test_get_subagent_run_for_creator_returns_none_for_relation_mismatch(session):
    await _seed_subagent_runs(session, relation_child_thread_id="other-child-thread")

    result = await AgentRunRepository(session).get_subagent_run_for_creator(
        uid="user-1",
        created_by_run_id="parent-run",
        run_id="child-run",
    )

    assert result is None


async def test_get_subagent_run_for_creator_accepts_child_resume_run(session):
    await _seed_subagent_runs(session)
    resume_run = AgentRun(
        id="resume-run",
        conversation_thread_id="child-thread",
        agent_slug="worker",
        uid="user-1",
        status="completed",
        request_id="resume-req",
        conversation_id=20,
        created_by_run_id="parent-run",
        subagent_thread_relation_id=77,
        run_type="resume",
        input_payload={"resume_from_run_id": "child-run"},
    )
    session.add(resume_run)
    await session.commit()

    result = await AgentRunRepository(session).get_subagent_run_for_creator(
        uid="user-1",
        created_by_run_id="parent-run",
        run_id="resume-run",
    )

    assert result is resume_run
    latest = await AgentRunRepository(session).get_latest_subagent_run_by_thread_for_user(
        "child-thread",
        "user-1",
    )
    assert latest is resume_run
