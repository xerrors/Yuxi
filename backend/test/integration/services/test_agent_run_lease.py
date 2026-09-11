"""真实 PostgreSQL 上的 AgentRun lease ownership 与过期收敛测试。"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from agent_run_test_helpers import create_agent_run
from langchain.messages import AIMessage
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.conversation_repository import ConversationRepository
from yuxi.repositories.model_message_audit_repository import ModelMessageAuditRepository
from yuxi.repositories.tool_message_audit_repository import ToolMessageAuditRepository
from yuxi.services import chat_service, run_worker
from yuxi.storage.postgres.manager import (
    AGENT_RUN_LANGFUSE_SCHEMA_STATEMENTS,
    AGENT_RUN_LEASE_SCHEMA_STATEMENTS,
    MESSAGE_AUDIT_SCHEMA_STATEMENTS,
    RUNTIME_SCOPE_SCHEMA_STATEMENTS,
)
from yuxi.storage.postgres.models_business import (
    AgentRun,
    Conversation,
    Message,
    Project,
    SubagentThread,
    ToolCall,
    User,
)
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture()
async def lease_database():
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    async with engine.begin() as connection:
        for _ in range(2):
            for statement in (
                *AGENT_RUN_LEASE_SCHEMA_STATEMENTS,
                *AGENT_RUN_LANGFUSE_SCHEMA_STATEMENTS,
                *MESSAGE_AUDIT_SCHEMA_STATEMENTS,
            ):
                await connection.execute(text(statement))
        await connection.execute(text(RUNTIME_SCOPE_SCHEMA_STATEMENTS[-1]))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, session_factory
    finally:
        await engine.dispose()


@asynccontextmanager
async def _session_context(session_factory):
    async with session_factory() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def _create_run(
    session_factory,
    *,
    status: str = "pending",
    worker_id: str | None = None,
    lease_expires_at=None,
) -> tuple[str, str, int]:
    return await create_agent_run(
        session_factory,
        prefix="lease",
        message_content="lease input",
        input_payload={},
        status=status,
        worker_id=worker_id,
        lease_expires_at=lease_expires_at,
    )


async def test_root_terminal_atomically_cancels_live_child_and_clears_lease(
    lease_database,
    monkeypatch: pytest.MonkeyPatch,
):
    """根 Run 终态提交不得留下仍占用共享 runtime 的子 Run。"""

    _, session_factory = lease_database
    now = utc_now_naive()
    parent_owner = "worker-tree-parent"
    child_owner = "worker-tree-child"
    parent_id, parent_thread_id, _ = await _create_run(session_factory)
    child_thread_id = f"pytest-tree-child-{uuid.uuid4()}"

    try:
        async with session_factory() as db:
            parent = await db.get(AgentRun, parent_id)
            parent_conversation = await db.get(Conversation, parent.conversation_id)
            assert parent_conversation is not None
            child_conversation = Conversation(
                thread_id=child_thread_id,
                uid=parent.uid,
                project_id=parent_conversation.project_id,
                agent_id="worker",
                status="subagent",
            )
            db.add(child_conversation)
            await db.flush()
            child_message = Message(
                conversation_id=child_conversation.id,
                role="user",
                content="long-running child",
                request_id=f"tree-child-{uuid.uuid4()}",
                delivery_status="dispatched",
            )
            db.add(child_message)
            await db.flush()
            relation = SubagentThread(
                uid=parent.uid,
                parent_conversation_id=parent_conversation.id,
                child_conversation_id=child_conversation.id,
                child_thread_id=child_thread_id,
                subagent_slug="worker",
                created_by_run_id=parent.id,
            )
            db.add(relation)
            await db.flush()
            child = AgentRun(
                id=str(uuid.uuid4()),
                conversation_thread_id=child_thread_id,
                runtime_scope_id=parent_thread_id,
                agent_slug="worker",
                uid=parent.uid,
                request_id=child_message.request_id,
                conversation_id=child_conversation.id,
                created_by_run_id=parent.id,
                subagent_thread_relation_id=relation.id,
                run_type="subagent",
                input_message_id=child_message.id,
                input_payload={},
                status="pending",
            )
            db.add(child)
            await db.flush()
            repo = AgentRunRepository(db)
            _, parent_acquired = await repo.mark_running(
                parent.id,
                worker_id=parent_owner,
                lease_seconds=60,
                now=now,
            )
            _, child_acquired = await repo.mark_running(
                child.id,
                worker_id=child_owner,
                lease_seconds=60,
                now=now,
            )
            child_id = child.id
            child_message_id = child_message.id
            await db.commit()

        monkeypatch.setattr(
            run_worker.pg_manager, "get_async_session_context", lambda: _session_context(session_factory)
        )
        publish_cancel = AsyncMock()
        monkeypatch.setattr(run_worker, "publish_cancel_signals", publish_cancel)

        transition = await run_worker.mark_run_terminal(
            parent_id,
            "failed",
            error_type="parent_failed",
            worker_id=parent_owner,
        )

        async with session_factory() as db:
            parent = await db.get(AgentRun, parent_id)
            child = await db.get(AgentRun, child_id)
            child_message = await db.get(Message, child_message_id)

        assert parent_acquired is True
        assert child_acquired is True
        assert transition.changed is True
        assert parent.status == "failed"
        assert child.status == "cancel_requested"
        assert child.error_type == "execution_tree_closed"
        assert child.worker_id == child_owner
        assert child.lease_expires_at is not None
        assert child_message.delivery_status == "dispatched"
        publish_cancel.assert_awaited_once_with([child_id])
    finally:
        await _cleanup_runs(session_factory, [parent_thread_id, child_thread_id])


async def _cleanup_runs(session_factory, thread_ids: list[str]) -> None:
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(Conversation.project_id, Conversation.uid).where(Conversation.thread_id.in_(thread_ids))
            )
        ).all()
        conversation_ids = list(
            (await db.scalars(select(Conversation.id).where(Conversation.thread_id.in_(thread_ids)))).all()
        )
        if conversation_ids:
            message_ids = select(Message.id).where(Message.conversation_id.in_(conversation_ids))
            await db.execute(delete(ToolCall).where(ToolCall.message_id.in_(message_ids)))
            await db.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
        await db.execute(delete(AgentRun).where(AgentRun.conversation_thread_id.in_(thread_ids)))
        await db.execute(delete(SubagentThread).where(SubagentThread.child_thread_id.in_(thread_ids)))
        await db.execute(delete(Conversation).where(Conversation.thread_id.in_(thread_ids)))
        await db.execute(delete(Project).where(Project.id.in_({row.project_id for row in rows})))
        await db.execute(delete(User).where(User.uid.in_({row.uid for row in rows})))
        await db.commit()


@pytest.mark.parametrize("run_type", ["chat", "resume"])
async def test_approval_flush_overlap_preserves_terminal_publication(lease_database, monkeypatch, run_type):
    """本 attempt 已提交审批终态时，flush 与心跳重叠仍完成清理和发布。"""
    _, session_factory = lease_database
    run_id, thread_id, message_id = await _create_run(session_factory)
    owner = "approval-flush:attempt-owner"
    release_flush = threading.Event()
    flush_finished = threading.Event()
    flush_started = asyncio.Event()
    heartbeat_finished = asyncio.Event()
    contexts = []
    published = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", lambda: _session_context(session_factory))
    monkeypatch.setattr(run_worker, "_run_owner_token", lambda _ctx: owner)
    monkeypatch.setattr(run_worker, "RUN_HEARTBEAT_SECONDS", 0)
    monkeypatch.setattr(run_worker, "persist_run_manifest", AsyncMock(return_value={}))
    monkeypatch.setattr(
        run_worker,
        "_validate_run_workdir_binding",
        AsyncMock(return_value=SimpleNamespace(workdir_path="projects/test")),
    )
    monkeypatch.setattr(run_worker, "_read_run_token_usage_from_state", AsyncMock(return_value=None))

    async def capture_context(context):
        """只在审批落库后启动真实心跳，固定重叠时序。"""
        contexts.append(context)

    async def release_runtime(run):
        """模拟 provisioner 释放后回写真实 cleanup fence，事件必须在它之后发布。"""
        async with _session_context(session_factory) as db:
            persisted = await db.get(AgentRun, run.id)
            persisted.runtime_cleanup_pending = False

    async def publish(_run_id, event_type, payload, **_kwargs):
        """回读 durable cleanup 后保存实际协议结果。"""
        if event_type in {"interrupt", "end"}:
            async with session_factory() as db:
                persisted = await db.get(AgentRun, run_id)
                assert persisted.status == "interrupted"
                assert persisted.runtime_cleanup_pending is False
        published.append((event_type, payload))

    def blocking_flush():
        """让心跳确实发生在上报线程尚未退出时。"""
        loop.call_soon_threadsafe(flush_started.set)
        release_flush.wait(timeout=5)
        flush_finished.set()

    async def approval_stream(**_kwargs):
        """保留真实 Worker、终态事务和事件映射，仅替代 Agent 与 exporter。"""
        yield json.dumps({"status": "human_approval_required", "thread_id": thread_id, "questions": []}).encode()
        async with _session_context(session_factory) as db:
            _, changed = await AgentRunRepository(db).set_terminal_status(
                run_id, status="interrupted", worker_id=owner, error_type="human_approval_required"
            )
            assert changed
        await asyncio.to_thread(blocking_flush)

    async def heartbeat_during_flush():
        """由独立任务运行心跳，避免生成器自身模拟取消结果。"""
        await asyncio.wait_for(flush_started.wait(), 5)
        await contexts[0]._heartbeat_lease()
        heartbeat_finished.set()

    monkeypatch.setattr(run_worker.RunContext, "start", capture_context)
    monkeypatch.setattr(run_worker, "_release_runtime_before_terminal_event", release_runtime)
    monkeypatch.setattr(run_worker, "append_run_event", publish)
    monkeypatch.setattr(run_worker, "stream_agent_chat", approval_stream)
    monkeypatch.setattr(run_worker, "stream_agent_resume", approval_stream)
    try:
        async with _session_context(session_factory) as db:
            run = await db.get(AgentRun, run_id)
            run.run_type = run_type
            if run_type == "resume":
                run.created_by_run_id = str(uuid.uuid4())
            message = await db.get(Message, message_id)
            message.extra_metadata = {"resume": {"decisions": [{"type": "approve"}]}}
    except Exception:
        await _cleanup_runs(session_factory, [thread_id])
        raise

    execution = asyncio.create_task(run_worker.process_agent_run({}, run_id))
    heartbeat = asyncio.create_task(heartbeat_during_flush())
    try:
        await asyncio.wait_for(heartbeat_finished.wait(), 5)
        assert not contexts[0].lease_lost, "本 attempt 已提交终态，不应误判为失去 ownership"
        assert not flush_finished.is_set()
        release_flush.set()
        await asyncio.wait_for(execution, 5)
        assert [event for event, _payload in published if event in {"interrupt", "end"}] == ["interrupt", "end"]
        async with session_factory() as db:
            persisted = await db.get(AgentRun, run_id)
            attempts = await AgentRunRepository(db).list_run_attempts(run_id)
        assert persisted.status == "interrupted" and persisted.runtime_cleanup_pending is False
        assert len(attempts) == 1 and attempts[0].worker_id == owner and attempts[0].outcome == "interrupted"
    finally:
        release_flush.set()
        await asyncio.wait_for(asyncio.gather(execution, heartbeat, return_exceptions=True), 5)
        if flush_started.is_set():
            assert await asyncio.to_thread(flush_finished.wait, 5)
        await _cleanup_runs(session_factory, [thread_id])


async def test_first_model_request_timing_survives_owner_cancellation(lease_database, monkeypatch):
    """取消前已发生的模型调用仍归属原 Run，过期与其他 owner 不得补写。"""
    from yuxi.agents.callbacks.model_request_timing import FirstModelRequestRecorder

    _, session_factory = lease_database
    owner = "timing-owner"
    run_id, thread_id, _ = await _create_run(
        session_factory,
        status="running",
        worker_id=owner,
        lease_expires_at=utc_now_naive() + timedelta(minutes=1),
    )
    monkeypatch.setattr(chat_service.pg_manager, "get_async_session_context", lambda: _session_context(session_factory))
    try:
        recorder = FirstModelRequestRecorder()
        await recorder.on_chat_model_start({}, [[]], run_id=uuid.uuid4())
        async with session_factory() as db:
            run = await db.get(AgentRun, run_id)
            await AgentRunRepository(db).request_cancel_execution_tree(
                run_id=run_id,
                uid=run.uid,
                cascade_descendants=False,
            )
            await db.commit()

        for worker_id, checked_at in (
            ("stale-owner", utc_now_naive()),
            (owner, utc_now_naive() + timedelta(minutes=2)),
        ):
            async with session_factory() as db:
                with pytest.raises(ValueError, match="lease owner"):
                    await AgentRunRepository(db).record_first_model_request(
                        run_id,
                        worker_id=worker_id,
                        observed_at=recorder.first_model_request_at,
                        checked_at=checked_at,
                    )

        await recorder.persist(run_id=run_id, worker_id=owner)
        async with session_factory() as db:
            run = await db.get(AgentRun, run_id)
            assert run.status == "cancel_requested"
            assert run.first_model_request_at == recorder.first_model_request_at
            with pytest.raises(ValueError, match="lease owner"):
                await AgentRunRepository(db).lock_output_persistence(
                    run_id=run_id,
                    worker_id=owner,
                    conversation_thread_id=thread_id,
                    request_id=run.request_id,
                )
            await AgentRunRepository(db).set_terminal_status(run_id, status="cancelled", worker_id=owner)
            await db.commit()

        async with session_factory() as db:
            persisted = await db.get(AgentRun, run_id)
            assert persisted.status == "cancelled"
            assert persisted.first_model_request_at == recorder.first_model_request_at
    finally:
        await _cleanup_runs(session_factory, [thread_id])


@pytest.mark.parametrize("case", ["other_owner", "expired", "reconciled", "missing", "old_attempt"])
async def test_heartbeat_terminal_check_does_not_keep_lost_owner_alive(lease_database, monkeypatch, case):
    """非终态、其他 owner 的终态与历史 attempt 均不得通过收尾例外。"""
    _, session_factory = lease_database
    run_id, thread_id, _ = await _create_run(session_factory)
    owner = "heartbeat:current-attempt"
    old_owner = "heartbeat:old-attempt"
    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", lambda: _session_context(session_factory))
    monkeypatch.setattr(run_worker, "RUN_HEARTBEAT_SECONDS", 0)
    now = utc_now_naive()
    try:
        async with _session_context(session_factory) as db:
            repo = AgentRunRepository(db)
            if case == "old_attempt":
                await repo.mark_running(run_id, worker_id=old_owner, lease_seconds=60, now=now)
                assert await repo.release_lease_for_retry(run_id, worker_id=old_owner, now=now)
                run = await db.get(AgentRun, run_id)
                run.runtime_cleanup_pending = False
            await repo.mark_running(
                run_id,
                worker_id=owner,
                lease_seconds=60,
                now=now - timedelta(seconds=120) if case in {"expired", "reconciled"} else now,
            )
            if case in {"other_owner", "old_attempt"}:
                _, changed = await repo.set_terminal_status(run_id, status="interrupted", worker_id=owner, now=now)
                assert changed
            if case == "reconciled":
                reconciled, _descendants = await repo.reconcile_expired_leases(now=now)
                assert run_id in {run.id for run in reconciled}
        if case == "reconciled":
            async with session_factory() as db:
                run = await db.get(AgentRun, run_id)
                [attempt] = await AgentRunRepository(db).list_run_attempts(run_id)
            assert run.status == "failed" and attempt.outcome == "lease_expired"
            assert attempt.worker_id == owner and attempt.finished_at == run.finished_at
        context = run_worker.RunContext(
            run_id=str(uuid.uuid4()) if case == "missing" else run_id,
            worker_id=owner if case in {"expired", "reconciled", "missing"} else old_owner,
        )

        await asyncio.wait_for(context._heartbeat_lease(), 5)

        assert context.lease_lost and context.cancel_event.is_set()
        async with session_factory() as db:
            persisted = await db.get(AgentRun, run_id)
        expected_status = "running"
        if case in {"other_owner", "old_attempt"}:
            expected_status = "interrupted"
        elif case == "reconciled":
            expected_status = "failed"
        assert persisted.status == expected_status
    finally:
        await _cleanup_runs(session_factory, [thread_id])


async def test_model_audit_lifecycle_is_idempotent_and_lease_fenced(lease_database):
    """Model start/finish 只允许当前 owner，并保持同一来源键单行。"""
    _engine, session_factory = lease_database
    now = utc_now_naive()
    owner = "model-audit-owner"
    run_id, thread_id, _message_id = await _create_run(
        session_factory,
        status="running",
        worker_id=owner,
        lease_expires_at=now + timedelta(minutes=1),
    )
    try:
        async with session_factory() as db:
            run = await db.get(AgentRun, run_id)
            repo = ModelMessageAuditRepository(db)
            message, created = await repo.start(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                operation_id="model-operation-1",
                sequence=7,
                started_at=now,
                metadata={"id": "model-operation-1"},
            )
            duplicate, duplicate_created = await repo.start(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                operation_id="model-operation-1",
                sequence=7,
                started_at=now,
            )
            assert duplicate.id == message.id
            assert created is True
            assert duplicate_created is False

            with pytest.raises(ValueError, match="sequence"):
                await repo.start(
                    run_id=run_id,
                    request_id=run.request_id,
                    thread_id=thread_id,
                    worker_id=owner,
                    operation_id="model-operation-1",
                    sequence=8,
                    started_at=now,
                )

            completed = await repo.finish(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                operation_id="model-operation-1",
                content="answer",
                finished_at=now + timedelta(seconds=1),
                duration_ms=321,
                usage={"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
            )
            replayed = await repo.finish(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                operation_id="model-operation-1",
                content="answer",
                finished_at=now + timedelta(seconds=2),
                duration_ms=999,
                usage={"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
            )
            assert replayed.id == completed.id
            assert completed.execution_status == "completed"
            assert completed.duration_ms == 321
            assert [item.id for item in await repo.list_for_run(run_id)] == [message.id]

            with pytest.raises(ValueError, match="不同结果覆盖"):
                await repo.finish(
                    run_id=run_id,
                    request_id=run.request_id,
                    thread_id=thread_id,
                    worker_id=owner,
                    operation_id="model-operation-1",
                    content="different",
                    finished_at=now + timedelta(seconds=3),
                    duration_ms=400,
                    usage=None,
                )
            await db.commit()

        async with session_factory() as db:
            run = await db.get(AgentRun, run_id)
            with pytest.raises(ValueError, match="lease owner"):
                await ModelMessageAuditRepository(db).start(
                    run_id=run_id,
                    request_id=run.request_id,
                    thread_id=thread_id,
                    worker_id="other-owner",
                    operation_id="model-operation-2",
                    sequence=8,
                    started_at=now,
                )
            with pytest.raises(ValueError, match="同一 thread 和 request"):
                await ModelMessageAuditRepository(db).start(
                    run_id=run_id,
                    request_id="other-request",
                    thread_id=thread_id,
                    worker_id=owner,
                    operation_id="model-operation-2",
                    sequence=8,
                    started_at=now,
                )
            with pytest.raises(ValueError, match="同一 thread 和 request"):
                await ModelMessageAuditRepository(db).start(
                    run_id=run_id,
                    request_id=run.request_id,
                    thread_id="other-thread",
                    worker_id=owner,
                    operation_id="model-operation-2",
                    sequence=8,
                    started_at=now,
                )

        async with session_factory() as db:
            run = await db.get(AgentRun, run_id)
            db.add(
                Message(
                    conversation_id=run.conversation_id,
                    role="assistant",
                    content="duplicate",
                    message_type="model_audit",
                    run_id=run_id,
                    request_id=run.request_id,
                    operation_id="model-operation-1",
                    execution_status="completed",
                )
            )
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()
    finally:
        await _cleanup_runs(session_factory, [thread_id])


async def test_tool_audit_lifecycle_owns_compatibility_projection_and_is_lease_fenced(lease_database):
    """ToolMessage 保存真实执行事实，ToolCall 只作为同源兼容投影。"""
    _engine, session_factory = lease_database
    now = utc_now_naive()
    owner = "tool-audit-owner"
    run_id, thread_id, _message_id = await _create_run(
        session_factory,
        status="running",
        worker_id=owner,
        lease_expires_at=now + timedelta(minutes=1),
    )
    try:
        async with session_factory() as db:
            run = await db.get(AgentRun, run_id)
            model_repo = ModelMessageAuditRepository(db)
            model_message, _created = await model_repo.start(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                operation_id="call-tool-1",
                sequence=3,
                started_at=now,
                metadata={"tool_calls": [{"id": "call-tool-1", "name": "search", "args": {"q": "Yuxi"}}]},
            )
            await model_repo.finish(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                operation_id="call-tool-1",
                content="",
                finished_at=now + timedelta(milliseconds=50),
                duration_ms=50,
                usage={"input_tokens": 2, "output_tokens": 1},
            )

            repository = ToolMessageAuditRepository(db)
            tool_message, created = await repository.start(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                tool_call_id="call-tool-1",
                tool_name="search",
                tool_input={"q": "effective Yuxi"},
                sequence=5,
                started_at=now + timedelta(milliseconds=60),
            )
            duplicate, duplicate_created = await repository.start(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                tool_call_id="call-tool-1",
                tool_name="search",
                tool_input={"q": "effective Yuxi"},
                sequence=5,
                started_at=now + timedelta(milliseconds=70),
            )
            completed = await repository.complete(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                tool_call_id="call-tool-1",
                output={"id": None, "type": "tool", "content": "result", "status": "success"},
                content="result",
                finished_at=now + timedelta(milliseconds=160),
                duration_ms=100,
                finished_sequence=6,
            )
            replayed = await repository.complete(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                tool_call_id="call-tool-1",
                output={
                    "id": "state-id",
                    "name": "search",
                    "type": "tool",
                    "content": "result",
                    "status": "success",
                },
                content="result",
                finished_at=now + timedelta(milliseconds=200),
                duration_ms=140,
                finished_sequence=7,
            )
            tool_call = (
                await db.execute(
                    select(ToolCall)
                    .join(Message, ToolCall.message_id == Message.id)
                    .where(
                        Message.run_id == run_id,
                        ToolCall.langgraph_tool_call_id == "call-tool-1",
                    )
                )
            ).scalar_one()

            assert created is True
            assert duplicate_created is False
            assert duplicate.id == tool_message.id == completed.id == replayed.id
            assert completed.role == "tool"
            assert completed.message_type == "tool_audit"
            assert completed.execution_status == "completed"
            assert completed.content == "result"
            assert completed.duration_ms == 100
            assert completed.extra_metadata["input"] == {"q": "effective Yuxi"}
            assert completed.extra_metadata["source_model_operation_id"] == "call-tool-1"
            persisted_model = await model_repo.get(run_id=run_id, operation_id="call-tool-1")
            assert persisted_model.id == model_message.id
            assert tool_call.message_id == model_message.id
            assert tool_call.tool_input == {"q": "effective Yuxi"}
            assert tool_call.tool_output == "result"
            assert tool_call.status == "success"

            with pytest.raises(ValueError, match="不同结果覆盖"):
                await repository.complete(
                    run_id=run_id,
                    request_id=run.request_id,
                    thread_id=thread_id,
                    worker_id=owner,
                    tool_call_id="call-tool-1",
                    output={
                        "id": "state-id",
                        "type": "tool",
                        "content": "result",
                        "artifact": {"version": 2},
                        "status": "success",
                    },
                    content="result",
                    finished_at=now + timedelta(milliseconds=250),
                    duration_ms=190,
                    finished_sequence=8,
                )

            with pytest.raises(ValueError, match="无法关联"):
                await repository.start(
                    run_id=run_id,
                    request_id=run.request_id,
                    thread_id=thread_id,
                    worker_id=owner,
                    tool_call_id="call-without-model",
                    tool_name="search",
                    tool_input={},
                    sequence=9,
                    started_at=now,
                )

            with pytest.raises(ValueError, match="重复 Tool start"):
                await repository.start(
                    run_id=run_id,
                    request_id=run.request_id,
                    thread_id=thread_id,
                    worker_id=owner,
                    tool_call_id="call-tool-1",
                    tool_name="search",
                    tool_input={"q": "different"},
                    sequence=5,
                    started_at=now,
                )
            await db.commit()

        async with session_factory() as db:
            run = await db.get(AgentRun, run_id)
            with pytest.raises(ValueError, match="lease owner"):
                await ToolMessageAuditRepository(db).start(
                    run_id=run_id,
                    request_id=run.request_id,
                    thread_id=thread_id,
                    worker_id="other-owner",
                    tool_call_id="call-tool-2",
                    tool_name="search",
                    tool_input={},
                    sequence=8,
                    started_at=now,
                )
    finally:
        await _cleanup_runs(session_factory, [thread_id])


async def test_terminal_failure_closes_running_model_and_tool_audits(lease_database):
    """Run 终态 owning transaction 不得留下 running Model/Tool 行。"""
    _engine, session_factory = lease_database
    now = utc_now_naive()
    owner = "model-audit-terminal-owner"
    run_id, thread_id, _message_id = await _create_run(
        session_factory,
        status="running",
        worker_id=owner,
        lease_expires_at=now + timedelta(minutes=1),
    )
    try:
        async with session_factory() as db:
            run = await db.get(AgentRun, run_id)
            model_audit, _created = await ModelMessageAuditRepository(db).start(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                operation_id="model-operation-running",
                sequence=11,
                started_at=now,
                metadata={"tool_calls": [{"id": "tool-operation-running", "name": "search", "args": {"q": "pending"}}]},
            )
            tool_audit, _created = await ToolMessageAuditRepository(db).start(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                tool_call_id="tool-operation-running",
                tool_name="search",
                tool_input={"q": "pending"},
                sequence=12,
                started_at=now,
            )
            terminal_run, changed = await AgentRunRepository(db).set_terminal_status(
                run_id,
                status="failed",
                error_type="model_error",
                error_message="provider failed",
                worker_id=owner,
                now=now + timedelta(seconds=1),
            )
            await db.commit()
            assert terminal_run is not None
            assert changed is True
            await db.refresh(model_audit)
            await db.refresh(tool_audit)
            tool_call = (
                await db.execute(
                    select(ToolCall)
                    .join(Message, ToolCall.message_id == Message.id)
                    .where(
                        Message.run_id == run_id,
                        ToolCall.langgraph_tool_call_id == "tool-operation-running",
                    )
                )
            ).scalar_one()
            assert model_audit.execution_status == "failed"
            assert tool_audit.execution_status == "failed"
            assert model_audit.finished_at == now + timedelta(seconds=1)
            assert tool_audit.finished_at == now + timedelta(seconds=1)
            assert tool_call.status == "error"
            assert tool_call.error_message == "Tool 审计由 Run 终态收敛为 failed"
    finally:
        await _cleanup_runs(session_factory, [thread_id])


async def test_interrupted_tool_keeps_pending_projection_for_resume(lease_database):
    """裸 tool-error 随 Run interrupted 收敛后，同一 tool_call_id 可在 resume 继续。"""
    _engine, session_factory = lease_database
    now = utc_now_naive()
    parent_owner = "tool-interrupt-parent"
    resume_owner = "tool-interrupt-resume"
    parent_id, thread_id, _message_id = await _create_run(
        session_factory,
        status="running",
        worker_id=parent_owner,
        lease_expires_at=now + timedelta(minutes=1),
    )
    try:
        async with session_factory() as db:
            parent = await db.get(AgentRun, parent_id)
            model_message, _created = await ModelMessageAuditRepository(db).start(
                run_id=parent_id,
                request_id=parent.request_id,
                thread_id=thread_id,
                worker_id=parent_owner,
                operation_id="interrupt-model",
                sequence=2,
                started_at=now,
                metadata={"tool_calls": [{"id": "interrupt-tool", "name": "ask_user_question", "args": {}}]},
            )
            repository = ToolMessageAuditRepository(db)
            parent_tool, _created = await repository.start(
                run_id=parent_id,
                request_id=parent.request_id,
                thread_id=thread_id,
                worker_id=parent_owner,
                tool_call_id="interrupt-tool",
                tool_name="ask_user_question",
                tool_input={"questions": [{"question": "继续吗"}]},
                sequence=4,
                started_at=now + timedelta(milliseconds=10),
            )
            error_time = now + timedelta(milliseconds=20)
            await repository.observe_error(
                run_id=parent_id,
                request_id=parent.request_id,
                thread_id=thread_id,
                worker_id=parent_owner,
                tool_call_id="interrupt-tool",
                error_message="Interrupt",
                finished_at=error_time,
                duration_ms=10,
                finished_sequence=5,
            )
            _run, changed = await AgentRunRepository(db).set_terminal_status(
                parent_id,
                status="interrupted",
                error_type="ask_user_question_required",
                worker_id=parent_owner,
                now=now + timedelta(seconds=1),
            )
            assert changed is True
            await db.commit()

            await db.refresh(parent_tool)
            parent_tool_call = await db.get(
                ToolCall,
                parent_tool.extra_metadata["compatibility_tool_call_id"],
            )
            assert parent_tool.execution_status == "interrupted"
            assert parent_tool.finished_at == error_time
            assert parent_tool.duration_ms == 10
            assert parent_tool_call.status == "pending"
            assert parent_tool_call.message_id == model_message.id
            parent_tool_call_id = parent_tool_call.id

        resume_id = str(uuid.uuid4())
        resume_request_id = f"resume-{uuid.uuid4()}"
        async with session_factory() as db:
            parent = await db.get(AgentRun, parent_id)
            input_message = Message(
                conversation_id=parent.conversation_id,
                role="user",
                content="继续",
                request_id=resume_request_id,
                delivery_status="dispatched",
            )
            db.add(input_message)
            await db.flush()
            db.add(
                AgentRun(
                    id=resume_id,
                    conversation_thread_id=thread_id,
                    runtime_scope_id=thread_id,
                    agent_slug=parent.agent_slug,
                    uid=parent.uid,
                    request_id=resume_request_id,
                    conversation_id=parent.conversation_id,
                    input_message_id=input_message.id,
                    input_payload={},
                    status="running",
                    run_type="resume",
                    created_by_run_id=parent_id,
                    worker_id=resume_owner,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(minutes=1),
                )
            )
            await db.flush()
            repository = ToolMessageAuditRepository(db)
            resumed_tool, _created = await repository.start(
                run_id=resume_id,
                request_id=resume_request_id,
                thread_id=thread_id,
                worker_id=resume_owner,
                tool_call_id="interrupt-tool",
                tool_name="ask_user_question",
                tool_input={"questions": [{"question": "继续吗"}]},
                sequence=2,
                started_at=now + timedelta(seconds=2),
            )
            await repository.complete(
                run_id=resume_id,
                request_id=resume_request_id,
                thread_id=thread_id,
                worker_id=resume_owner,
                tool_call_id="interrupt-tool",
                output={"type": "tool", "content": "已继续", "status": "success"},
                content="已继续",
                finished_at=now + timedelta(seconds=3),
                duration_ms=1000,
                finished_sequence=3,
            )
            await db.commit()

            resumed_tool_call = await db.get(
                ToolCall,
                resumed_tool.extra_metadata["compatibility_tool_call_id"],
            )
            assert resumed_tool.execution_status == "completed"
            assert resumed_tool_call.id == parent_tool_call_id
            assert resumed_tool_call.status == "success"
            assert resumed_tool_call.tool_output == "已继续"
    finally:
        await _cleanup_runs(session_factory, [thread_id])


async def _create_live_child(
    session_factory,
    *,
    parent_id: str,
    runtime_scope_id: str,
    owner: str,
    now,
    lease_seconds: float,
) -> tuple[str, str, int]:
    child_thread_id = f"pytest-tree-child-{uuid.uuid4()}"
    async with session_factory() as db:
        parent = await db.get(AgentRun, parent_id)
        parent_conversation = await db.get(Conversation, parent.conversation_id)
        assert parent_conversation is not None
        child_conversation = Conversation(
            thread_id=child_thread_id,
            uid=parent.uid,
            project_id=parent_conversation.project_id,
            agent_id="worker",
            status="subagent",
        )
        db.add(child_conversation)
        await db.flush()
        child_message = Message(
            conversation_id=child_conversation.id,
            role="user",
            content="long-running child",
            request_id=f"tree-child-{uuid.uuid4()}",
            delivery_status="dispatched",
        )
        db.add(child_message)
        await db.flush()
        relation = SubagentThread(
            uid=parent.uid,
            parent_conversation_id=parent.conversation_id,
            child_conversation_id=child_conversation.id,
            child_thread_id=child_thread_id,
            subagent_slug="worker",
            created_by_run_id=parent.id,
        )
        db.add(relation)
        await db.flush()
        child = AgentRun(
            id=str(uuid.uuid4()),
            conversation_thread_id=child_thread_id,
            runtime_scope_id=runtime_scope_id,
            agent_slug="worker",
            uid=parent.uid,
            request_id=child_message.request_id,
            conversation_id=child_conversation.id,
            created_by_run_id=parent.id,
            subagent_thread_relation_id=relation.id,
            run_type="subagent",
            input_message_id=child_message.id,
            input_payload={},
            status="pending",
        )
        db.add(child)
        await db.flush()
        _, acquired = await AgentRunRepository(db).mark_running(
            child.id,
            worker_id=owner,
            lease_seconds=lease_seconds,
            now=now,
        )
        assert acquired is True
        child_id = child.id
        child_message_id = child_message.id
        await db.commit()
    return child_id, child_thread_id, child_message_id


async def test_expired_root_reconciliation_cancels_live_child_before_runtime_release(
    lease_database,
    monkeypatch: pytest.MonkeyPatch,
):
    """失联根 Run 必须先持久收敛执行树，再释放共享 runtime。"""

    _, session_factory = lease_database
    now = utc_now_naive()
    parent_id, parent_thread_id, _ = await _create_run(session_factory)
    child_thread_id = ""
    try:
        async with session_factory() as db:
            _, acquired = await AgentRunRepository(db).mark_running(
                parent_id,
                worker_id="worker-expired-tree-parent",
                lease_seconds=10,
                now=now,
            )
            await db.commit()
        assert acquired is True
        child_id, child_thread_id, child_message_id = await _create_live_child(
            session_factory,
            parent_id=parent_id,
            runtime_scope_id=parent_thread_id,
            owner="worker-live-tree-child",
            now=now,
            lease_seconds=120,
        )

        monkeypatch.setattr(
            run_worker.pg_manager, "get_async_session_context", lambda: _session_context(session_factory)
        )
        publish_cancel = AsyncMock()
        release_runtime = AsyncMock(return_value=False)
        monkeypatch.setattr(run_worker, "publish_cancel_signals", publish_cancel)
        monkeypatch.setattr(run_worker, "_release_runtime_if_idle", release_runtime)

        reconciled_ids = await run_worker.reconcile_expired_run_leases(now=now + timedelta(seconds=11))

        async with session_factory() as db:
            parent = await db.get(AgentRun, parent_id)
            child = await db.get(AgentRun, child_id)
            child_message = await db.get(Message, child_message_id)

        assert reconciled_ids == [parent_id]
        assert parent.status == "failed"
        assert child.status == "cancel_requested"
        assert child.worker_id == "worker-live-tree-child"
        assert child.lease_expires_at is not None
        assert child_message.delivery_status == "dispatched"
        publish_cancel.assert_awaited_once_with([child_id])
        release_runtime.assert_awaited_once()
        assert release_runtime.await_args.args[0].id == parent_id
    finally:
        await _cleanup_runs(session_factory, [parent_thread_id, child_thread_id])


async def test_agent_run_lease_schema_evolution_is_idempotent(lease_database):
    engine, _ = lease_database
    async with engine.connect() as connection:
        columns = set(
            (
                await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'agent_runs' "
                        "AND column_name IN ('worker_id', 'heartbeat_at', 'lease_expires_at')"
                    )
                )
            ).scalars()
        )
        index_exists = await connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_indexes "
                "WHERE tablename = 'agent_runs' AND indexname = 'ix_agent_runs_status_lease_expires')"
            )
        )

    assert columns == {"worker_id", "heartbeat_at", "lease_expires_at"}
    assert index_exists is True


async def test_langfuse_trace_is_idempotent_and_lease_fenced(lease_database):
    """Trace 只能由当前 attempt 固化，且重复事件不能改写既有绑定。"""
    _, session_factory = lease_database
    now = utc_now_naive()
    owner = "worker-trace:attempt-owner"
    run_id, thread_id, _ = await _create_run(session_factory)

    try:
        async with session_factory() as db:
            run, acquired = await AgentRunRepository(db).mark_running(
                run_id,
                worker_id=owner,
                lease_seconds=60,
                now=now,
            )
            assert acquired is True
            await db.commit()

        async with session_factory() as db:
            repository = AgentRunRepository(db)
            await repository.set_langfuse_trace_id(
                run_id,
                "trace-1",
                worker_id=owner,
                now=now + timedelta(seconds=1),
            )
            await repository.set_langfuse_trace_id(
                run_id,
                "trace-1",
                worker_id=owner,
                now=now + timedelta(seconds=2),
            )
            await db.commit()

        async with session_factory() as db:
            with pytest.raises(ValueError, match="当前有效 AgentRun lease owner"):
                await AgentRunRepository(db).set_langfuse_trace_id(
                    run_id,
                    "trace-1",
                    worker_id="worker-trace:stale-attempt",
                    now=now + timedelta(seconds=3),
                )
            await db.rollback()

        async with session_factory() as db:
            with pytest.raises(ValueError, match="已绑定不同"):
                await AgentRunRepository(db).set_langfuse_trace_id(
                    run_id,
                    "trace-2",
                    worker_id=owner,
                    now=now + timedelta(seconds=4),
                )
            await db.rollback()

        async with session_factory() as db:
            persisted = await db.get(AgentRun, run_id)
            columns = {
                row.column_name
                for row in (
                    await db.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'agent_runs' AND column_name = 'langfuse_trace_id'"
                        )
                    )
                )
            }

        assert persisted.langfuse_trace_id == "trace-1"
        assert columns == {"langfuse_trace_id"}
    finally:
        await _cleanup_runs(session_factory, [thread_id])


async def test_heartbeat_and_terminal_transition_require_exact_attempt_owner(
    lease_database,
    monkeypatch: pytest.MonkeyPatch,
):
    _, session_factory = lease_database
    now = utc_now_naive()
    owner = "worker-stable:attempt-owner"
    other_owner = "worker-stable:attempt-other"
    run_id, thread_id, message_id = await _create_run(session_factory)
    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", lambda: _session_context(session_factory))

    try:
        async with session_factory() as db:
            run, acquired = await AgentRunRepository(db).mark_running(
                run_id,
                worker_id=owner,
                lease_seconds=60,
                now=now,
            )
            await db.commit()
        assert acquired is True
        assert run.worker_id == owner

        async with session_factory() as db:
            other_renewed = await AgentRunRepository(db).renew_lease(
                run_id,
                worker_id=other_owner,
                lease_seconds=60,
                now=now + timedelta(seconds=10),
            )
            await db.commit()
        async with session_factory() as db:
            owner_renewed = await AgentRunRepository(db).renew_lease(
                run_id,
                worker_id=owner,
                lease_seconds=60,
                now=now + timedelta(seconds=10),
            )
            await db.commit()

        async with session_factory() as db:
            persisted_before_completion = await db.get(AgentRun, run_id)
            wrong_output = Message(
                conversation_id=persisted_before_completion.conversation_id,
                run_id=run_id,
                request_id=f"wrong-{persisted_before_completion.request_id}",
                role="assistant",
                content="wrong request output",
            )
            exact_output = Message(
                conversation_id=persisted_before_completion.conversation_id,
                run_id=run_id,
                request_id=persisted_before_completion.request_id,
                role="assistant",
                content="exact run output",
            )
            db.add_all([wrong_output, exact_output])
            await db.flush()
            repository = AgentRunRepository(db)
            with pytest.raises(ValueError, match="同一 conversation"):
                await repository.set_output_message(
                    run_id,
                    wrong_output.id,
                    worker_id=owner,
                    now=now + timedelta(seconds=11),
                )
            assert persisted_before_completion.output_message_id is None
            await repository.set_output_message(
                run_id,
                exact_output.id,
                worker_id=owner,
                now=now + timedelta(seconds=11),
            )
            exact_output_id = exact_output.id
            await db.commit()

        missing_owner = await run_worker.mark_run_terminal(run_id, "failed")
        other_owner_result = await run_worker.mark_run_terminal(run_id, "failed", worker_id=other_owner)
        owner_result = await run_worker.mark_run_terminal(run_id, "completed", worker_id=owner)

        async with session_factory() as db:
            persisted_run = await db.get(AgentRun, run_id)
            persisted_message = await db.get(Message, message_id)

        assert other_renewed is False
        assert owner_renewed is True
        assert missing_owner.changed is False
        assert other_owner_result.changed is False
        assert owner_result.changed is True
        assert persisted_run.status == "completed"
        assert persisted_run.output_message_id == exact_output_id
        assert persisted_run.worker_id is None
        assert persisted_run.heartbeat_at is None
        assert persisted_run.lease_expires_at is None
        assert persisted_message.delivery_status == "complete"
    finally:
        await _cleanup_runs(session_factory, [thread_id])


@pytest.mark.parametrize(
    ("run_status", "lease_offset"),
    [("running", -1), ("cancel_requested", 60)],
)
async def test_invalid_attempt_cannot_leave_assistant_message(
    lease_database,
    run_status: str,
    lease_offset: int,
):
    """过期或已取消 attempt 必须在任何 assistant Message 写入前被拒绝。"""

    _, session_factory = lease_database
    now = utc_now_naive()
    owner = f"worker-invalid:{run_status}"
    run_id, thread_id, _ = await _create_run(
        session_factory,
        status=run_status,
        worker_id=owner,
        lease_expires_at=now + timedelta(seconds=lease_offset),
    )

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [AIMessage(id=f"output-{run_id}", content="must rollback")]})

    try:
        async with session_factory() as db:
            run = await db.get(AgentRun, run_id)
            with pytest.raises(ValueError, match="有效 AgentRun lease owner"):
                await chat_service.save_messages_from_langgraph_state(
                    state=await FakeGraph().aget_state({}),
                    thread_id=thread_id,
                    conv_repo=ConversationRepository(db),
                    run_id=run_id,
                    request_id=run.request_id,
                    worker_id=owner,
                    complete_run=True,
                )

        async with session_factory() as db:
            persisted_run = await db.get(AgentRun, run_id)
            assistant_messages = list(
                (await db.scalars(select(Message).where(Message.run_id == run_id, Message.role == "assistant"))).all()
            )

        assert persisted_run.output_message_id is None
        assert persisted_run.status == run_status
        assert assistant_messages == []
    finally:
        await _cleanup_runs(session_factory, [thread_id])


async def test_interrupt_message_and_run_terminal_commit_together(lease_database):
    """真实事务中断点必须同时推进 Message 与 Run 终态。"""
    _, session_factory = lease_database
    owner = "worker-interrupt:attempt-owner"
    run_id, thread_id, _ = await _create_run(session_factory)

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [AIMessage(id=f"output-{run_id}", content="waiting")]})

    try:
        async with session_factory() as db:
            run, acquired = await AgentRunRepository(db).mark_running(
                run_id,
                worker_id=owner,
                lease_seconds=60,
            )
            await db.commit()
            request_id = run.request_id
        assert acquired is True

        async with session_factory() as db:
            committed = await chat_service.save_messages_from_langgraph_state(
                state=await FakeGraph().aget_state({}),
                thread_id=thread_id,
                conv_repo=ConversationRepository(db),
                run_id=run_id,
                request_id=request_id,
                worker_id=owner,
                interrupt_run=True,
                interrupt_error_type="ask_user_question_required",
                interrupt_error_message="请选择",
            )
        assert committed is True

        async with session_factory() as db:
            run = await db.get(AgentRun, run_id)
            output_message = await db.get(Message, run.output_message_id)

        assert run.status == "interrupted"
        assert run.error_type == "ask_user_question_required"
        assert output_message.content == "waiting"
    finally:
        await _cleanup_runs(session_factory, [thread_id])


async def test_expired_owner_cannot_finish_or_publish_retry_before_reconciliation(lease_database):
    """真实行锁下，过期 attempt 不能抢在 reconciler 前改写结局。"""
    _, session_factory = lease_database
    now = utc_now_naive()
    owner = "worker-expired:attempt-owner"
    run_id, thread_id, message_id = await _create_run(session_factory)

    try:
        async with session_factory() as db:
            run, acquired = await AgentRunRepository(db).mark_running(
                run_id,
                worker_id=owner,
                lease_seconds=10,
                now=now,
            )
            audit, _created = await ModelMessageAuditRepository(db).start(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                operation_id="expired-model-operation",
                sequence=3,
                started_at=now,
                metadata={"tool_calls": [{"id": "expired-tool-operation", "name": "search", "args": {"q": "unknown"}}]},
            )
            audit_id = audit.id
            tool_audit, _created = await ToolMessageAuditRepository(db).start(
                run_id=run_id,
                request_id=run.request_id,
                thread_id=thread_id,
                worker_id=owner,
                tool_call_id="expired-tool-operation",
                tool_name="search",
                tool_input={"q": "unknown"},
                sequence=4,
                started_at=now,
            )
            tool_audit_id = tool_audit.id
            await db.commit()

        async with session_factory() as db:
            released = await AgentRunRepository(db).release_lease_for_retry(
                run_id,
                worker_id=owner,
                now=now + timedelta(seconds=11),
            )
            await db.commit()
        async with session_factory() as db:
            _, completed = await AgentRunRepository(db).set_terminal_status(
                run_id,
                status="completed",
                worker_id=owner,
                now=now + timedelta(seconds=11),
            )
            await db.commit()
        async with session_factory() as db:
            reconciled, cancelled_descendants = await AgentRunRepository(db).reconcile_expired_leases(
                now=now + timedelta(seconds=11)
            )
            await db.commit()

        async with session_factory() as db:
            persisted_run = await db.get(AgentRun, run_id)
            persisted_message = await db.get(Message, message_id)
            persisted_audit = await db.get(Message, audit_id)
            persisted_tool_audit = await db.get(Message, tool_audit_id)
            persisted_tool_call = await db.get(
                ToolCall,
                persisted_tool_audit.extra_metadata["compatibility_tool_call_id"],
            )

        assert acquired is True
        assert released is False
        assert completed is False
        assert [run.id for run in reconciled] == [run_id]
        assert cancelled_descendants == []
        assert persisted_run.status == "failed"
        assert persisted_run.error_type == "worker_lease_expired"
        assert persisted_message.delivery_status == "failed"
        assert persisted_audit.execution_status == "abandoned"
        assert persisted_tool_audit.execution_status == "abandoned"
        assert persisted_tool_call.status == "error"
    finally:
        await _cleanup_runs(session_factory, [thread_id])


async def test_pending_cancel_is_terminal_and_durable_cancel_wins_completion_race(lease_database):
    """未执行取消直接完成；已执行取消在终态行锁竞争中优先于 completed。"""
    _, session_factory = lease_database
    now = utc_now_naive()
    pending_run_id, pending_thread_id, pending_message_id = await _create_run(session_factory)
    running_run_id, running_thread_id, running_message_id = await _create_run(session_factory)
    owner = "worker-cancel:attempt-owner"

    try:
        async with session_factory() as db:
            pending_uid = (await db.get(AgentRun, pending_run_id)).uid
            pending, pending_cancelled_ids = await AgentRunRepository(db).request_cancel_execution_tree(
                run_id=pending_run_id,
                uid=pending_uid,
                cascade_descendants=False,
            )
            await db.commit()
        async with session_factory() as db:
            pending_reconciled, cancelled_descendants = await AgentRunRepository(db).reconcile_expired_leases(
                now=now + timedelta(minutes=5)
            )
            await db.commit()

        async with session_factory() as db:
            running_run, acquired = await AgentRunRepository(db).mark_running(
                running_run_id,
                worker_id=owner,
                lease_seconds=60,
                now=now,
            )
            await ModelMessageAuditRepository(db).start(
                run_id=running_run_id,
                request_id=running_run.request_id,
                thread_id=running_thread_id,
                worker_id=owner,
                operation_id="cancelled-model-operation",
                sequence=2,
                started_at=now,
                metadata={
                    "tool_calls": [{"id": "cancelled-tool-operation", "name": "search", "args": {"q": "cancel"}}]
                },
            )
            running_tool_audit, _created = await ToolMessageAuditRepository(db).start(
                run_id=running_run_id,
                request_id=running_run.request_id,
                thread_id=running_thread_id,
                worker_id=owner,
                tool_call_id="cancelled-tool-operation",
                tool_name="search",
                tool_input={"q": "cancel"},
                sequence=3,
                started_at=now,
            )
            running_tool_audit_id = running_tool_audit.id
            await db.commit()
        async with session_factory() as db:
            running_uid = (await db.get(AgentRun, running_run_id)).uid
            requested, running_cancelled_ids = await AgentRunRepository(db).request_cancel_execution_tree(
                run_id=running_run_id,
                uid=running_uid,
                cascade_descendants=False,
            )
            await db.commit()
        async with session_factory() as db:
            _, completed = await AgentRunRepository(db).set_terminal_status(
                running_run_id,
                status="completed",
                worker_id=owner,
                now=now + timedelta(seconds=1),
            )
            await db.commit()
        async with session_factory() as db:
            _, cancelled = await AgentRunRepository(db).set_terminal_status(
                running_run_id,
                status="cancelled",
                error_type="cancelled",
                worker_id=owner,
                now=now + timedelta(seconds=1),
            )
            await db.commit()

        async with session_factory() as db:
            pending_persisted = await db.get(AgentRun, pending_run_id)
            pending_message = await db.get(Message, pending_message_id)
            running_persisted = await db.get(AgentRun, running_run_id)
            running_message = await db.get(Message, running_message_id)
            running_tool_audit = await db.get(Message, running_tool_audit_id)
            running_tool_call = await db.get(
                ToolCall,
                running_tool_audit.extra_metadata["compatibility_tool_call_id"],
            )

        assert pending.status == "cancelled"
        assert pending_cancelled_ids == [pending_run_id]
        assert pending_reconciled == []
        assert cancelled_descendants == []
        assert pending_persisted.status == "cancelled"
        assert pending_message.delivery_status == "cancelled"
        assert acquired is True
        assert requested.status == "cancel_requested"
        assert running_cancelled_ids == [running_run_id]
        assert completed is False
        assert cancelled is True
        assert running_persisted.status == "cancelled"
        assert running_message.delivery_status == "cancelled"
        assert running_tool_audit.execution_status == "interrupted"
        assert running_tool_call.status == "error"
    finally:
        await _cleanup_runs(session_factory, [pending_thread_id, running_thread_id])


async def test_concurrent_reconciliation_fails_each_expired_lease_once_and_projects_message_failure(
    lease_database,
    monkeypatch: pytest.MonkeyPatch,
):
    _, session_factory = lease_database
    now = utc_now_naive()
    live = await _create_run(
        session_factory,
        status="running",
        worker_id="worker-live:attempt",
        lease_expires_at=now + timedelta(minutes=5),
    )
    expired_running = await _create_run(
        session_factory,
        status="running",
        worker_id="worker-dead:running",
        lease_expires_at=now - timedelta(seconds=1),
    )
    expired_cancel = await _create_run(
        session_factory,
        status="cancel_requested",
        worker_id="worker-dead:cancel",
        lease_expires_at=now - timedelta(seconds=1),
    )
    all_runs = [live, expired_running, expired_cancel]
    monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", lambda: _session_context(session_factory))

    try:
        results = await asyncio.gather(
            run_worker.reconcile_expired_run_leases(now=now),
            run_worker.reconcile_expired_run_leases(now=now),
        )
        repeated = await run_worker.reconcile_expired_run_leases(now=now)
        reconciled_ids = [run_id for result in results for run_id in result]

        async with session_factory() as db:
            persisted_runs = {
                run.id: run
                for run in (
                    await db.scalars(select(AgentRun).where(AgentRun.id.in_([item[0] for item in all_runs])))
                ).all()
            }
            persisted_messages = {
                message.id: message
                for message in (
                    await db.scalars(select(Message).where(Message.id.in_([item[2] for item in all_runs])))
                ).all()
            }

        assert sorted(reconciled_ids) == sorted([expired_running[0], expired_cancel[0]])
        assert repeated == []
        assert persisted_runs[live[0]].status == "running"
        assert persisted_runs[live[0]].worker_id == "worker-live:attempt"
        for run_id, _, message_id in (expired_running, expired_cancel):
            run = persisted_runs[run_id]
            assert run.status == "failed"
            assert run.error_type == "worker_lease_expired"
            assert "at-least-once" in run.error_message
            assert run.worker_id is None
            assert run.heartbeat_at is None
            assert run.lease_expires_at is None
            assert persisted_messages[message_id].delivery_status == "failed"
    finally:
        await _cleanup_runs(session_factory, [item[1] for item in all_runs])


async def test_nonterminal_run_shape_constraint_preserves_terminal_legacy_rows(lease_database):
    """数据库允许历史终态形状，但拒绝新的非法非终态写入。"""
    _, session_factory = lease_database
    suffix = uuid.uuid4().hex
    legacy_id = f"shape-legacy-{suffix}"
    async with session_factory() as db:
        legacy = AgentRun(
            id=legacy_id,
            conversation_thread_id=f"legacy-thread-{suffix}",
            runtime_scope_id=f"foreign-scope-{suffix}",
            agent_slug="main",
            uid=f"shape-user-{suffix}",
            status="completed",
            request_id=f"shape-legacy-request-{suffix}",
            run_type="subagent",
            input_payload={},
        )
        db.add(legacy)
        await db.commit()

        db.add(
            AgentRun(
                id=f"shape-invalid-{suffix}",
                conversation_thread_id=f"invalid-thread-{suffix}",
                runtime_scope_id=f"foreign-scope-{suffix}",
                agent_slug="main",
                uid=f"shape-user-{suffix}",
                status="pending",
                request_id=f"shape-invalid-request-{suffix}",
                run_type="chat",
                input_payload={},
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()

        persisted = await db.get(AgentRun, legacy_id)
        assert persisted is not None
        await db.delete(persisted)
        await db.commit()


async def test_cancel_execution_tree_locks_root_before_descendants(lease_database):
    """取消执行树等待 root 时不能提前持有 child 行锁。"""
    _, session_factory = lease_database
    suffix = uuid.uuid4().hex
    application_name = f"yuxi-lock-order-{suffix}"
    now = utc_now_naive()
    root_id, root_thread, _ = await _create_run(session_factory)
    async with session_factory() as db:
        uid = (await db.get(AgentRun, root_id)).uid
    child_id, child_thread, _ = await _create_live_child(
        session_factory,
        parent_id=root_id,
        runtime_scope_id=root_thread,
        owner="worker-lock-child",
        now=now,
        lease_seconds=60,
    )

    cancel_started = asyncio.Event()

    async def cancel_tree():
        async with session_factory() as db:
            await db.execute(
                text("SELECT set_config('application_name', :name, true)"),
                {"name": application_name},
            )
            cancel_started.set()
            _run, cancelled_ids = await AgentRunRepository(db).request_cancel_execution_tree(
                run_id=root_id,
                uid=uid,
                cascade_descendants=True,
            )
            await db.commit()
            return cancelled_ids

    cancel_task = None
    try:
        async with session_factory() as root_locker:
            await root_locker.execute(select(AgentRun).where(AgentRun.id == root_id).with_for_update())
            cancel_task = asyncio.create_task(cancel_tree())
            await asyncio.wait_for(cancel_started.wait(), timeout=2)

            async with session_factory() as observer:
                for _ in range(100):
                    wait_event = await observer.scalar(
                        text("SELECT wait_event_type FROM pg_stat_activity WHERE application_name = :name"),
                        {"name": application_name},
                    )
                    if wait_event == "Lock":
                        break
                    await asyncio.sleep(0.02)
                else:
                    pytest.fail("取消事务没有在 root 行锁上等待")

            async with session_factory() as child_probe:
                assert await child_probe.scalar(
                    select(AgentRun).where(AgentRun.id == child_id).with_for_update(nowait=True)
                )
                await child_probe.rollback()
            await root_locker.rollback()

        assert await asyncio.wait_for(cancel_task, timeout=5) == [root_id, child_id]
        async with session_factory() as db:
            statuses = dict(
                (
                    await db.execute(select(AgentRun.id, AgentRun.status).where(AgentRun.id.in_([root_id, child_id])))
                ).all()
            )
        assert statuses == {root_id: "cancelled", child_id: "cancel_requested"}
    finally:
        if cancel_task is not None and not cancel_task.done():
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
        await _cleanup_runs(session_factory, [root_thread, child_thread])


async def test_root_failed_without_cascade_keeps_live_child_running(
    lease_database,
    monkeypatch: pytest.MonkeyPatch,
):
    """显式关闭级联时，failed 根 Run 必须让活跃子 Run 继续执行并回读为仍 running。

    这是「错误失败不连坐子 Run」的直接证据：父 Run 落 failed 后，子 Run 的最终状态
    仍是 running（没有被收敛成 cancel_requested），且没有发出取消信号。
    """

    _, session_factory = lease_database
    now = utc_now_naive()
    parent_owner = "worker-error-parent"
    child_owner = "worker-error-child"
    parent_id, parent_thread_id, _ = await _create_run(session_factory)
    child_thread_id = f"pytest-error-child-{uuid.uuid4()}"

    try:
        async with session_factory() as db:
            parent = await db.get(AgentRun, parent_id)
            parent_conversation = await db.get(Conversation, parent.conversation_id)
            assert parent_conversation is not None
            child_conversation = Conversation(
                thread_id=child_thread_id,
                uid=parent.uid,
                project_id=parent_conversation.project_id,
                agent_id="worker",
                status="subagent",
            )
            db.add(child_conversation)
            await db.flush()
            child_message = Message(
                conversation_id=child_conversation.id,
                role="user",
                content="long-running child survives parent failure",
                request_id=f"tree-error-child-{uuid.uuid4()}",
                delivery_status="dispatched",
            )
            db.add(child_message)
            await db.flush()
            relation = SubagentThread(
                uid=parent.uid,
                parent_conversation_id=parent_conversation.id,
                child_conversation_id=child_conversation.id,
                child_thread_id=child_thread_id,
                subagent_slug="worker",
                created_by_run_id=parent.id,
            )
            db.add(relation)
            await db.flush()
            child = AgentRun(
                id=str(uuid.uuid4()),
                conversation_thread_id=child_thread_id,
                runtime_scope_id=parent_thread_id,
                agent_slug="worker",
                uid=parent.uid,
                request_id=child_message.request_id,
                conversation_id=child_conversation.id,
                created_by_run_id=parent.id,
                subagent_thread_relation_id=relation.id,
                run_type="subagent",
                input_message_id=child_message.id,
                input_payload={},
                status="pending",
            )
            db.add(child)
            await db.flush()
            repo = AgentRunRepository(db)
            _, parent_acquired = await repo.mark_running(
                parent.id,
                worker_id=parent_owner,
                lease_seconds=60,
                now=now,
            )
            _, child_acquired = await repo.mark_running(
                child.id,
                worker_id=child_owner,
                lease_seconds=60,
                now=now,
            )
            child_id = child.id
            await db.commit()

        monkeypatch.setattr(
            run_worker.pg_manager, "get_async_session_context", lambda: _session_context(session_factory)
        )
        publish_cancel = AsyncMock()
        monkeypatch.setattr(run_worker, "publish_cancel_signals", publish_cancel)

        transition = await run_worker.mark_run_terminal(
            parent_id,
            "failed",
            error_type="parent_failed",
            worker_id=parent_owner,
            cascade_cancel_descendants=False,
        )

        async with session_factory() as db:
            parent = await db.get(AgentRun, parent_id)
            child = await db.get(AgentRun, child_id)

        assert parent_acquired is True
        assert child_acquired is True
        assert transition.changed is True
        assert parent.status == "failed"
        # 子 Run 保持运行：未被收敛成 cancel_requested，也未被剥夺 lease。
        assert child.status == "running"
        assert child.worker_id == child_owner
        assert child.lease_expires_at is not None
        # 无级联后代：取消信号为空，子 Run 不会被通知停止。
        publish_cancel.assert_awaited_once_with([])
    finally:
        await _cleanup_runs(session_factory, [parent_thread_id, child_thread_id])
