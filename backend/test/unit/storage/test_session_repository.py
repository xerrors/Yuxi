from __future__ import annotations

from yuxi.modules.agents.repositories.runs import AgentRunRepository

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.modules.agents.services.memory_history import MemoryHistoryService
from yuxi.modules.agents.repositories.messages import MessageRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository, MAX_SESSION_TITLE_LENGTH
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.infrastructure.postgres.base import Base
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.messages import Message
from yuxi.shared.datetime import utc_now
from yuxi.modules.agents.services.memory_history import MEMORY_HISTORY_READ_RESPONSE_MAX_BYTES
from test.support.sqlite import create_utc_sqlite_engine

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture()
async def session():
    engine = create_utc_sqlite_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def test_normalize_title_truncates_when_too_long():
    repo = SessionRepository(None)  # type: ignore[arg-type]
    raw = "a" * (MAX_SESSION_TITLE_LENGTH + 50)

    normalized = repo._normalize_title(raw)

    assert normalized is not None
    assert len(normalized) == MAX_SESSION_TITLE_LENGTH
    assert normalized == "a" * MAX_SESSION_TITLE_LENGTH


def test_normalize_title_trims_spaces():
    repo = SessionRepository(None)  # type: ignore[arg-type]

    normalized = repo._normalize_title("   hello world   ")

    assert normalized == "hello world"


@pytest.mark.asyncio
async def test_list_agent_runs_for_trace_returns_latest_bounded_window_in_order(session):
    now = utc_now()
    agent_session = Session(
        thread_id="thread-run-trace-window",
        project_id="project-run-trace-window",
        uid="user-a",
        agent_id="agent-a",
        title="Run trace window",
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add(agent_session)
    await session.flush()
    session.add(AgentTurn(id="turn-trace", thread_id=agent_session.thread_id, uid=agent_session.uid, status="completed"))
    await session.flush()
    for index in range(3):
        created_at = now + timedelta(seconds=index)
        session.add(
            AgentRun(
                id=f"run-trace-{index}",
                thread_id=agent_session.thread_id,
                runtime_scope_id=agent_session.thread_id,
                agent_slug="main",
                uid=agent_session.uid,
                status="completed",
                turn_id="turn-trace",
                session_record_id=agent_session.id,
                input_payload={},
                created_at=created_at,
                started_at=created_at,
                finished_at=created_at,
            )
        )
    await session.commit()

    runs, truncated = await AgentRunRepository(session).list_agent_runs_for_trace(
        agent_session.id,
        limit=2,
    )

    assert [run.id for run in runs] == ["run-trace-1", "run-trace-2"]
    assert truncated is True


@pytest.mark.asyncio
async def test_lock_session_refreshes_cached_lifecycle_state(tmp_path):
    """加锁读取必须刷新同一 Session 中已缓存的生命周期状态。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent_session-lock.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as reader, factory() as writer:
            agent_session = Session(
                thread_id="thread-refresh-lock",
                project_id="project-refresh-lock",
                uid="user-a",
                agent_id="agent-a",
                title="Refresh lock",
                status="active",
            )
            reader.add(agent_session)
            await reader.commit()

            repository = SessionRepository(reader)
            cached = await repository.get_session_by_id(agent_session.id)
            assert cached is agent_session
            assert cached.status == "active"

            await writer.execute(update(Session).where(Session.id == agent_session.id).values(status="deleted"))
            await writer.commit()

            locked = await repository.lock_session_by_thread_id(agent_session.thread_id)

            assert locked is agent_session
            assert locked.status == "deleted"
    finally:
        await engine.dispose()


def _seed_source_filter_sessions() -> tuple[Session, Session, Session, datetime]:
    now = utc_now()
    normal = Session(
        thread_id="thread-normal",
        project_id="project-thread-normal",
        uid="user-a",
        agent_id="agent-a",
        title="Normal",
        status="active",
        created_at=now,
        updated_at=now,
        extra_metadata={},
    )
    scheduled = Session(
        thread_id="thread-scheduled",
        project_id="project-thread-scheduled",
        uid="user-a",
        agent_id="agent-a",
        title="Scheduled Thread",
        status="active",
        is_pinned=True,
        created_at=now,
        updated_at=now + timedelta(minutes=2),
        extra_metadata={"source": "scheduled"},
    )
    public_api = Session(
        thread_id="thread-public",
        project_id="project-thread-public",
        uid="user-a",
        agent_id="agent-a",
        title="Public API Thread",
        status="active",
        created_at=now,
        updated_at=now + timedelta(minutes=1),
        extra_metadata={"source": "public_api"},
    )
    return normal, scheduled, public_api, now


@pytest.mark.asyncio
async def test_model_audit_messages_are_hidden_from_history_and_message_count(session):
    now = utc_now()
    agent_session = Session(
        thread_id="thread-model-audit",
        project_id="project-model-audit",
        uid="user-a",
        agent_id="agent-a",
        title="Model Audit",
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add(agent_session)
    await session.flush()
    visible_message = Message(
        session_record_id=agent_session.id,
        role="user",
        content="visible",
        message_type="text",
    )
    audit_message = Message(
        session_record_id=agent_session.id,
        role="assistant",
        content="hidden intermediate",
        message_type="model_audit",
        operation_id="model-1",
        execution_status="completed",
    )
    tool_audit = Message(
        session_record_id=agent_session.id,
        role="tool",
        content="", extra_metadata={"output": "hidden tool output"},
        message_type="tool_audit",
        operation_id="tool-1",
        started_at=now,
        sequence=2,
        execution_status="completed",
    )
    session.add_all([visible_message, audit_message, tool_audit])
    await session.commit()

    messages = await MessageRepository(session).get_messages(agent_session.id)

    assert [message.content for message in messages] == ["visible"]

    previous_updated_at = agent_session.updated_at
    await MessageRepository(session).publish_assistant_output(audit_message)
    published_messages = await MessageRepository(session).get_messages(agent_session.id)

    assert [message.content for message in published_messages] == ["visible", "hidden intermediate"]
    assert audit_message.message_type == "text"
    assert agent_session.updated_at > previous_updated_at


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_session_page_includes_different_sources(session):
    normal, scheduled, public_api, _ = _seed_source_filter_sessions()
    session.add_all([normal, scheduled, public_api])
    await session.commit()

    repo = SessionRepository(session)
    items, _ = await repo.list_public_sessions(uid="user-a", app_id=None, agent_id=None, limit=20, after=None, order="desc")

    assert {item.thread_id for item in items} == {"thread-normal", "thread-public", "thread-scheduled"}


@pytest.mark.asyncio
async def test_session_page_bounds_pinned_items_and_uses_stable_cursor(session):
    now = utc_now()
    pinned = Session(
        thread_id="thread-pinned",
        project_id="project-pinned",
        uid="user-a",
        agent_id="agent-a",
        title="Pinned",
        status="active",
        is_pinned=True,
        created_at=now,
        updated_at=now + timedelta(minutes=10),
    )
    regular = [
        Session(
            thread_id=f"thread-{index}",
            project_id=f"project-{index}",
            uid="user-a",
            agent_id="agent-a",
            title=f"Thread {index}",
            status="active",
            created_at=now,
            updated_at=now + timedelta(minutes=index),
        )
        for index in range(4)
    ]
    session.add_all([pinned, *regular])
    await session.commit()

    repository = SessionRepository(session)
    first_page, has_more = await repository.list_public_sessions(
        uid="user-a", app_id=None, agent_id=None, limit=2, after=None, order="desc"
    )
    second_page, _ = await repository.list_public_sessions(
        uid="user-a", app_id=None, agent_id=None, limit=2, after=first_page[-1].thread_id, order="desc"
    )

    assert has_more is True
    assert [item.thread_id for item in first_page] == ["thread-pinned", "thread-3"]
    assert [item.thread_id for item in second_page] == ["thread-2", "thread-1"]


@pytest.mark.asyncio
async def test_search_sessions_filters_user_status_and_tool_messages(session):
    now = utc_now()
    active = Session(
        thread_id="thread-active",
        project_id="project-thread-active",
        uid="user-a",
        agent_id="agent-a",
        title="Active Thread",
        status="active",
        created_at=now,
        updated_at=now,
    )
    deleted = Session(
        thread_id="thread-deleted",
        project_id="project-thread-deleted",
        uid="user-a",
        agent_id="agent-a",
        title="Deleted Thread",
        status="deleted",
        created_at=now,
        updated_at=now,
    )
    other_user = Session(
        thread_id="thread-other-user",
        project_id="project-thread-other-user",
        uid="user-b",
        agent_id="agent-a",
        title="Other User Thread",
        status="active",
        created_at=now,
        updated_at=now,
    )
    tool_only = Session(
        thread_id="thread-tool-only",
        project_id="project-thread-tool-only",
        uid="user-a",
        agent_id="agent-a",
        title="Tool Only Thread",
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add_all([active, deleted, other_user, tool_only])
    await session.flush()
    session.add_all(
        [
            Message(
                agent_session=active,
                role="assistant",
                content="大陆部署方案需要保留",
                message_type="text",
                created_at=now,
            ),
            Message(
                agent_session=deleted,
                role="assistant",
                content="大陆 deleted should not show",
                message_type="text",
                created_at=now,
            ),
            Message(
                agent_session=other_user,
                role="assistant",
                content="大陆 other user should not show",
                message_type="text",
                created_at=now,
            ),
            Message(
                agent_session=tool_only,
                role="tool",
                content="大陆 tool output should not show",
                message_type="tool_result",
                created_at=now,
            ),
        ]
    )
    await session.commit()

    repo = SessionRepository(session)
    items, has_more = await repo.search_sessions(
        uid="user-a",
        query="大陆",
        limit=20,
        offset=0,
    )

    assert has_more is False
    assert [item["agent_session"].thread_id for item in items] == ["thread-active"]
    assert items[0]["matched_count"] == 1
    assert items[0]["message_id"] is not None
    assert "大陆部署方案" in items[0]["snippets"][0]["content"]


@pytest.mark.asyncio
async def test_search_sessions_includes_different_sources(session):
    normal, scheduled, public_api, now = _seed_source_filter_sessions()
    session.add_all([normal, scheduled, public_api])
    await session.flush()
    session.add_all(
        [
            Message(agent_session=normal, role="user", content="导航隐藏检查", message_type="text", created_at=now),
            Message(
                agent_session=scheduled,
                role="user",
                content="导航隐藏检查 call",
                message_type="text",
                created_at=now,
            ),
            Message(
                agent_session=public_api,
                role="user",
                content="导航隐藏检查 eval",
                message_type="text",
                created_at=now,
            ),
        ]
    )
    await session.commit()

    repo = SessionRepository(session)
    items, has_more = await repo.search_sessions(
        uid="user-a",
        query="导航隐藏检查",
        limit=20,
        offset=0,
    )

    assert has_more is False
    assert {item["agent_session"].thread_id for item in items} == {"thread-normal", "thread-public", "thread-scheduled"}


@pytest.mark.asyncio
async def test_search_sessions_filters_agent_and_paginates(session):
    now = utc_now()
    old = now - timedelta(days=1)
    first = Session(
        thread_id="thread-first",
        project_id="project-thread-first",
        uid="user-a",
        agent_id="agent-a",
        title="First",
        status="active",
        created_at=old,
        updated_at=old,
    )
    second = Session(
        thread_id="thread-second",
        project_id="project-thread-second",
        uid="user-a",
        agent_id="agent-a",
        title="Second",
        status="active",
        created_at=now,
        updated_at=now,
    )
    other_agent = Session(
        thread_id="thread-other-agent",
        project_id="project-thread-other-agent",
        uid="user-a",
        agent_id="agent-b",
        title="Other Agent",
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add_all([first, second, other_agent])
    await session.flush()
    session.add_all(
        [
            Message(
                agent_session=first,
                role="user",
                content="大陆关键词 old",
                message_type="text",
                created_at=old,
            ),
            Message(
                agent_session=second,
                role="assistant",
                content="大陆关键词 latest",
                message_type="text",
                created_at=now,
            ),
            Message(
                agent_session=other_agent,
                role="assistant",
                content="大陆关键词 other agent",
                message_type="text",
                created_at=now,
            ),
        ]
    )
    await session.commit()

    repo = SessionRepository(session)
    first_page, has_more = await repo.search_sessions(
        uid="user-a",
        agent_id="agent-a",
        query="大陆",
        limit=1,
        offset=0,
    )
    second_page, second_has_more = await repo.search_sessions(
        uid="user-a",
        agent_id="agent-a",
        query="大陆",
        limit=1,
        offset=1,
    )

    assert has_more is True
    assert [item["agent_session"].thread_id for item in first_page] == ["thread-second"]
    assert second_has_more is False
    assert [item["agent_session"].thread_id for item in second_page] == ["thread-first"]


@pytest.mark.asyncio
async def test_search_batches_snippets_without_query_growth(session):
    """命中会话增加时 SQL 数量固定，片段有界且保留所属会话。"""
    from sqlalchemy import event
    from yuxi.modules.agents.repositories.sessions import MESSAGE_SEARCH_SNIPPETS_PER_THREAD

    now = utc_now()
    sessions = [
        Session(
            thread_id=f"batch-{i}",
            project_id=f"project-batch-{i}",
            uid="batch-user",
            agent_id="agent",
            status="active",
            created_at=now,
            updated_at=now,
        )
        for i in range(3)
    ]
    session.add_all(sessions)
    await session.flush()
    for i, agent_session in enumerate(sessions):
        session.add_all(
            [
                Message(
                    agent_session=agent_session,
                    role="user",
                    content=f"batch-key-{i}-{j}",
                    message_type="text",
                    created_at=now + timedelta(seconds=j),
                )
                for j in range(5)
            ]
        )
    await session.commit()
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        for limit in [1, 3]:
            statements.clear()
            rows, more = await SessionRepository(session).search_sessions(uid="batch-user", query="batch-key", limit=limit, app_id=None)
            assert len(rows) == limit and more is (limit == 1)
            assert len(statements) == 2
            for row in rows:
                index = row["agent_session"].thread_id.split("-")[-1]
                assert len(row["snippets"]) == MESSAGE_SEARCH_SNIPPETS_PER_THREAD
                assert all(f"batch-key-{index}-" in snippet["content"] for snippet in row["snippets"])
                assert row["snippets"][0]["content"].endswith("-4")
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def _session(db, *, thread_id: str, uid: str = "user-1", metadata: dict | None = None):
    agent_session = Session(
        thread_id=thread_id,
        project_id=f"project-{thread_id}",
        uid=uid,
        agent_id="main",
        status="active",
        extra_metadata=metadata,
    )
    db.add(agent_session)
    await db.flush()
    return agent_session


async def test_memory_search_includes_public_source_and_excludes_hidden_messages(session):
    visible = await _session(session, thread_id="visible")
    other = await _session(session, thread_id="other", uid="user-2")
    public = await _session(session, thread_id="public", metadata={"source": "public_api"})
    child = await _session(session, thread_id="child")
    session.add_all(
        [
            Message(session_record_id=visible.id, role="user", content="needle visible", message_type="text"),
            Message(session_record_id=visible.id, role="tool", content="needle tool", message_type="text"),
            Message(session_record_id=visible.id, role="assistant", content="needle result", message_type="tool_result"),
            Message(session_record_id=other.id, role="user", content="needle other", message_type="text"),
            Message(session_record_id=public.id, role="user", content="needle public", message_type="text"),
            Message(session_record_id=child.id, role="assistant", content="needle child", message_type="text"),
        ]
    )
    await session.commit()

    result = await MemoryHistoryService(session).search_memory_messages(uid="user-1", query="needle")

    assert {item["thread_id"]: item["content"] for item in result["items"]} == {
        "visible": "needle visible",
        "public": "needle public",
        "child": "needle child",
    }
    assert all("truncated" not in item for item in result["items"])
    assert "truncated" not in result
    assert all(set(item) == {"thread_id", "title", "message_id", "role", "content"} for item in result["items"])


async def test_memory_read_uses_allowlist_and_only_explicit_toolcall_table(session):
    agent_session = await _session(session, thread_id="visible")
    assistant = Message(
        session_record_id=agent_session.id,
        role="assistant",
        content="safe assistant",
        message_type="text",
        extra_metadata={"tool_calls": [{"args": {"secret": "METADATA-SECRET"}}]},
        image_content="IMAGE-SECRET",
    )
    session.add_all(
        [
            Message(session_record_id=agent_session.id, role="user", content="safe user", message_type="text"),
            assistant,
            Message(session_record_id=agent_session.id, role="tool", content="TOOL-ROLE-SECRET", message_type="text"),
            Message(
                session_record_id=agent_session.id,
                role="assistant",
                content="TOOL-TYPE-SECRET",
                message_type="tool_result",
            ),
        ]
    )
    await session.flush()
    session.add(
        Message(
            session_record_id=assistant.session_record_id,
            turn_id=assistant.turn_id,
            run_id=assistant.run_id,
            role="tool",
            message_type="tool_audit",
            operation_id="call-1",
            content="",
            execution_status="completed",
            started_at=assistant.created_at,
            extra_metadata={"output": "TOOLCALL-OUTPUT", "source_model_message_id": assistant.id, "tool_name": "secret_tool", "input": {"secret": "TOOLCALL-INPUT"}},
        )
    )
    await session.commit()

    default_result = await MemoryHistoryService(session).read_memory_messages(uid="user-1", thread_id="visible")
    explicit_result = await MemoryHistoryService(session).read_memory_messages(
        uid="user-1",
        thread_id="visible",
        include_tools=True,
    )

    default_json = json.dumps(default_result, ensure_ascii=False)
    explicit_json = json.dumps(explicit_result, ensure_ascii=False)
    assert [item["content"] for item in default_result["messages"]] == ["safe user", "safe assistant"]
    assert default_result["tool_calls"] == []
    assert "truncated" not in default_result
    assert all(set(item) == {"message_id", "role", "content"} for item in default_result["messages"])
    assert "METADATA-SECRET" not in default_json
    assert "IMAGE-SECRET" not in default_json
    assert "TOOL-ROLE-SECRET" not in default_json
    assert "TOOL-TYPE-SECRET" not in default_json
    assert "METADATA-SECRET" not in explicit_json
    assert "truncated" not in explicit_result["tool_calls"][0]
    assert set(explicit_result["tool_calls"][0]) == {
        "tool_call_id",
        "name",
        "input",
        "output",
        "status",
        "error",
    }
    assert explicit_result["tool_calls"][0]["input"] == '{"secret":"TOOLCALL-INPUT"}'
    assert explicit_result["tool_calls"][0]["output"] == "TOOLCALL-OUTPUT"


async def test_memory_read_enforces_utf8_and_final_response_budget(session):
    agent_session = await _session(session, thread_id="large")
    session.add_all(
        [
            Message(
                session_record_id=agent_session.id,
                role="user" if index % 2 == 0 else "assistant",
                content="记" * 20_000,
                message_type="text",
            )
            for index in range(20)
        ]
    )
    await session.commit()

    result = await MemoryHistoryService(session).read_memory_messages(uid="user-1", thread_id="large")
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    assert len(encoded) <= MEMORY_HISTORY_READ_RESPONSE_MAX_BYTES
    assert result["truncated"] is True
    assert all(len(item["content"].encode("utf-8")) <= 8 * 1024 for item in result["messages"])
    assert len(b"".join(item["content"].encode("utf-8") for item in result["messages"])) <= 32 * 1024


async def test_memory_tools_exclude_unproven_or_active_model_audits(session):
    """历史与 Memory 两种读取入口都只公开终态 State 已证明的 Model 工具调用。"""
    agent_session = await _session(session, thread_id="audits")
    session.add_all(
        [
            AgentTurn(id="turn-active", thread_id="audits", uid="user-1", status="running"),
            AgentTurn(id="turn-unproven", thread_id="audits", uid="user-1", status="completed"),
            AgentTurn(id="turn-proven", thread_id="audits", uid="user-1", status="cancelled"),
        ]
    )
    await session.flush()
    session.add_all(
        [
            AgentRun(
                id="run-active",
                thread_id="audits",
                runtime_scope_id="audits",
                agent_slug="main",
                uid="user-1",
                status="running",
                turn_id="turn-active",
                session_record_id=agent_session.id,
                input_payload={},
            ),
            AgentRun(
                id="run-unproven",
                thread_id="audits",
                runtime_scope_id="audits",
                agent_slug="main",
                uid="user-1",
                status="completed",
                turn_id="turn-unproven",
                session_record_id=agent_session.id,
                input_payload={},
            ),
            AgentRun(
                id="run-proven",
                thread_id="audits",
                runtime_scope_id="audits",
                agent_slug="main",
                uid="user-1",
                status="interrupted",
                turn_id="turn-proven",
                session_record_id=agent_session.id,
                input_payload={},
            ),
        ]
    )
    await session.flush()
    messages = [
        Message(
            session_record_id=agent_session.id,
            role="assistant",
            content=label,
            message_type="model_audit",
            extra_metadata={"state_reconciled": proven, "tool_calls": [{"id": f"call-{label}", "name": "search", "args": {}}]},
            run_id=run_id,
            turn_id=turn_id,
            operation_id=f"model-{label}",
            execution_status="completed",
        )
        for label, run_id, turn_id, proven in [
            ("active", "run-active", "turn-active", True),
            ("unproven", "run-unproven", "turn-unproven", False),
            ("proven", "run-proven", "turn-proven", True),
        ]
    ]
    session.add_all(messages)
    await session.flush()
    session.add_all(
        [
            Message(
                session_record_id=message.session_record_id,
                turn_id=message.turn_id,
                run_id=message.run_id,
                role="tool",
                message_type="tool_audit",
                operation_id=f"call-{message.content}",
                content="",
                execution_status="completed",
                started_at=message.created_at,
                extra_metadata={"output": f"output-{message.content}", "source_model_message_id": message.id, "tool_name": "search", "input": {}},
            )
            for message in messages
        ]
    )
    await session.commit()

    history = await MessageRepository(session).get_messages(agent_session.id)

    assert [message.content for message in history] == ["proven"]
    executions = await MessageRepository(session).list_tool_executions([history[0].id])
    assert executions[0].operation_id == "call-proven"

    result = await MemoryHistoryService(session).read_memory_messages(
        uid="user-1",
        thread_id="audits",
        include_tools=True,
    )

    assert [message["content"] for message in result["messages"]] == ["proven"]
    assert [tool_call["tool_call_id"] for tool_call in result["tool_calls"]] == ["call-proven"]


async def test_new_session_persists_project_binding(session):
    agent_session = await SessionRepository(session).add_session(
        uid="oidc:user@example.com",
        agent_id="main",
        thread_id="thread-1",
        project_id="11111111-1111-4111-8111-111111111111",
    )

    assert agent_session.project_id == "11111111-1111-4111-8111-111111111111"


async def test_session_requires_project_binding(session):
    with pytest.raises(TypeError, match="project_id"):
        await SessionRepository(session).add_session(
            uid="user-1",
            agent_id="main",
            thread_id="thread-missing-binding",
        )


async def test_failed_session_flush_propagates(session, monkeypatch):
    monkeypatch.setattr(session, "flush", AsyncMock(side_effect=RuntimeError("db failure")))

    with pytest.raises(RuntimeError, match="db failure"):
        await SessionRepository(session).add_session(
            uid="user-1",
            agent_id="main",
            thread_id="thread-failed",
            project_id="33333333-3333-4333-8333-333333333333",
        )


async def test_outer_transaction_rollback_removes_new_session(session):
    await SessionRepository(session).add_session(
        uid="user-1",
        agent_id="main",
        thread_id="thread-outer-rollback",
        project_id="55555555-5555-4555-8555-555555555555",
    )
    await session.rollback()

    assert await SessionRepository(session).get_session_by_thread_id("thread-outer-rollback") is None


async def test_personal_sessions_are_excluded_before_pagination_search_and_project_history(session):
    """主会话和子会话不进入通用列表，按 Agent 筛选也不能绕过。"""
    from yuxi.modules.agents.models.definitions import Agent
    from yuxi.modules.agents.models.personal_assistants import PersonalAssistant
    from yuxi.modules.agents.scope import ActorScope
    from yuxi.modules.agents.services.threads import list_session_page, search_threads
    from yuxi.modules.workspace.models import Project
    from yuxi.modules.workspace.repositories.projects import ProjectRepository

    now = utc_now()
    agent = Agent(slug="personal", name="助手", backend_id="ChatbotAgent", share_config={}, created_by="user-a")
    session.add_all([
        agent,
        Project(id="personal-project", uid="user-a", selection_status="implicit", workdir_path="agents/assistant", directory_mode="linked"),
        Project(id="regular-project", uid="user-a", selection_status="implicit", workdir_path="regular", directory_mode="linked"),
    ])
    await session.flush()
    for thread, slug, project, root, parent, path, seconds in [
        ("regular", "ordinary", "regular-project", "regular", None, "/root", 0),
        ("personal-root", "personal", "personal-project", "personal-root", None, "/root", 1),
        ("personal-child", "personal", "personal-project", "personal-root", "personal-root", "/root/child", 2),
        ("regular-2", "ordinary", "regular-project", "regular-2", None, "/root", 3),
    ]:
        session.add(Session(
            thread_id=thread, agent_id=slug, project_id=project, uid="user-a", tree_root_thread_id=root,
            parent_thread_id=parent, cooperation_path=path, title="needle", status="active", is_pinned=True,
            config_snapshot={"model": "test:model", "tool_approval_mode": "default"},
            created_at=now + timedelta(seconds=seconds), updated_at=now + timedelta(seconds=seconds),
        ))
    await session.flush()
    session.add(PersonalAssistant(uid="user-a", agent_id=agent.id, project_id="personal-project", main_thread_id="personal-root"))
    await session.commit()

    scope = ActorScope(uid="user-a", app_id=None)
    page, more = await list_session_page(db=session, scope=scope, agent_slug=None, after=None, limit=1, order="desc", is_pinned=True)
    assert [item["id"] for item in page] == ["regular-2"]
    assert more
    page, more = await list_session_page(db=session, scope=scope, agent_slug=None, after="regular-2", limit=1, order="desc", is_pinned=True)
    assert [item["id"] for item in page] == ["regular"]
    assert not more
    filtered, _ = await list_session_page(db=session, scope=scope, agent_slug="personal", after=None, limit=10, order="desc")
    assert filtered == []
    search = await search_threads(db=session, scope=scope, query="needle", agent_slug=None, limit=1, offset=1)
    assert [item["session"]["id"] for item in search["items"]] == ["regular"]
    assert not search["has_more"]
    assert (await search_threads(db=session, scope=scope, query="needle", agent_slug="personal"))["items"] == []
    candidates = await ProjectRepository(session).list_history_candidates("user-a")
    assert {row.thread_id for row, _ in candidates} == {"regular", "regular-2"}
    assert (await SessionRepository(session).get_session_by_thread_id("personal-child")).thread_id == "personal-child"
