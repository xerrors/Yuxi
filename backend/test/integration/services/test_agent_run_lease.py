"""真实 PostgreSQL 上的 Run owner、lease 与审计因果边界。"""

from __future__ import annotations

import asyncio
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from agent_run_test_helpers import cleanup_agent_run_threads, create_agent_run
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from sqlalchemy import select

import yuxi.modules.agents.services.run_lifecycle as lifecycle
import yuxi.modules.agents.services.leases as lease_worker
import yuxi.modules.agents.services.runner as run_worker
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.model_audit import ModelMessageAuditRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.tool_audit import ToolMessageAuditRepository
from yuxi.modules.agents.services.message_recorder import RunMessageRecorder
from yuxi.modules.agents.services.openai_events import OpenAIEventAdapter
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@asynccontextmanager
async def _session_context(session_factory):
    """模拟 worker 顶层拥有的短事务。"""
    async with session_factory() as db:
        async with db.begin():
            yield db


async def _create_run(session_factory, *, status="pending", worker_id=None, lease_expires_at=None):
    """创建 Input、Turn、Run 闭合链路。"""
    return await create_agent_run(
        session_factory,
        prefix="lease",
        message_content="lease input",
        input_payload={},
        status=status,
        worker_id=worker_id,
        lease_expires_at=lease_expires_at,
    )


async def test_owner_heartbeat_and_terminal_are_lease_fenced(postgres_sessions):
    """旧 attempt 不能续租、写输出或覆盖新 owner 的终态。"""
    sessions = postgres_sessions
    run_id, thread_id, _ = await _create_run(sessions)
    now = utc_now()
    try:
        async with sessions() as db:
            repo = AgentRunRepository(db)
            _, acquired = await repo.mark_running(run_id, worker_id="owner-a", lease_seconds=60, now=now)
            assert acquired is True
            assert await repo.renew_lease(run_id, worker_id="owner-b", lease_seconds=60, now=now) is False
            with pytest.raises(ValueError, match="lease owner"):
                await repo.lock_output_persistence(run_id, worker_id="owner-b", thread_id=thread_id, now=now)
            _, changed = await repo.set_terminal_status(run_id, status="failed", worker_id="owner-b", now=now)
            assert changed is False
            assert await repo.renew_lease(run_id, worker_id="owner-a", lease_seconds=60, now=now) is True
            _, changed = await repo.set_terminal_status(run_id, status="failed", worker_id="owner-a", now=now)
            assert changed is True
            await db.commit()

        async with sessions() as db:
            run = await db.get(AgentRun, run_id)
            attempts = await AgentRunRepository(db).list_run_attempts(run_id)
            assert run.status == "failed"
            assert run.worker_id is None and run.lease_expires_at is None
            assert [(item.worker_id, item.outcome) for item in attempts] == [("owner-a", "failed")]
    finally:
        await cleanup_agent_run_threads(sessions, [thread_id])


async def test_cancel_requested_run_cannot_be_completed_by_owner(postgres_sessions):
    """取消请求一旦持久化，后到的完成提交不能抢赢。"""
    sessions = postgres_sessions
    run_id, thread_id, _ = await _create_run(sessions)
    try:
        async with sessions() as db:
            repo = AgentRunRepository(db)
            run = await db.get(AgentRun, run_id)
            _, acquired = await repo.mark_running(run_id, worker_id="owner-a", lease_seconds=60)
            assert acquired is True
            cancelled, ids = await repo.request_cancel_run(run_id=run_id, uid=run.uid)
            assert cancelled.status == "cancel_requested" and ids == [run_id]
            _, changed = await repo.set_terminal_status(run_id, status="completed", worker_id="owner-a")
            assert changed is False
            _, changed = await repo.set_terminal_status(run_id, status="cancelled", worker_id="owner-a")
            assert changed is True
            await db.commit()

        async with sessions() as db:
            assert (await db.get(AgentRun, run_id)).status == "cancelled"
            assert (await AgentRunRepository(db).list_run_attempts(run_id))[-1].outcome == "cancelled"
    finally:
        await cleanup_agent_run_threads(sessions, [thread_id])


async def test_expired_lease_reconciliation_is_single_winner_and_closes_audit(postgres_sessions, monkeypatch):
    """并发 reconciler 只能收敛一次，同事务关闭审计、Turn 与 lease。"""
    sessions = postgres_sessions
    run_id, thread_id, _ = await _create_run(sessions)
    now = utc_now()
    try:
        async with sessions() as db:
            repo = AgentRunRepository(db)
            _, acquired = await repo.mark_running(run_id, worker_id="lost-owner", lease_seconds=60, now=now)
            assert acquired is True
            await ModelMessageAuditRepository(db).start(
                run_id=run_id,
                thread_id=thread_id,
                worker_id="lost-owner",
                operation_id="lost-model",
                sequence=1,
                started_at=now,
            )
            await db.commit()
        expired_at = now + timedelta(seconds=61)
        monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", lambda: _session_context(sessions))
        monkeypatch.setattr(lease_worker, "reconcile_pending_runtime_cleanups", AsyncMock(return_value=[]))
        outcomes = await asyncio.gather(
            lease_worker.reconcile_expired_run_leases(now=expired_at),
            lease_worker.reconcile_expired_run_leases(now=expired_at),
        )
        assert sorted(len(item) for item in outcomes) == [0, 1]
        assert [run_id] in outcomes
        async with sessions() as db:
            run = await db.get(AgentRun, run_id)
            turn = await db.get(AgentTurn, run.turn_id)
            agent_session = await db.get(Session, run.session_record_id)
            [audit] = await ModelMessageAuditRepository(db).list_for_run(run_id)
            [attempt] = await AgentRunRepository(db).list_run_attempts(run_id)
            assert (run.status, run.error_type, run.worker_id) == ("failed", "worker_lease_expired", None)
            assert turn.status == "failed" and agent_session.queue_paused is False
            assert audit.execution_status == "abandoned"
            assert attempt.outcome == "lease_expired"
    finally:
        await cleanup_agent_run_threads(sessions, [thread_id])


async def test_model_audit_is_idempotent_and_keeps_turn_run_owner(postgres_sessions):
    """同一模型操作只有一条审计事实，且旧 owner 不能改写。"""
    sessions = postgres_sessions
    run_id, thread_id, _ = await _create_run(sessions)
    now = utc_now()
    try:
        async with sessions() as db:
            repo = AgentRunRepository(db)
            _, acquired = await repo.mark_running(run_id, worker_id="model-owner", lease_seconds=60, now=now)
            assert acquired is True
            audit = ModelMessageAuditRepository(db)
            first, created = await audit.start(
                run_id=run_id,
                thread_id=thread_id,
                worker_id="model-owner",
                operation_id="model-op",
                sequence=1,
                started_at=now,
            )
            duplicate, duplicate_created = await audit.start(
                run_id=run_id,
                thread_id=thread_id,
                worker_id="model-owner",
                operation_id="model-op",
                sequence=1,
                started_at=now,
            )
            assert created is True and duplicate_created is False and duplicate.id == first.id
            with pytest.raises(ValueError, match="sequence"):
                await audit.start(
                    run_id=run_id,
                    thread_id=thread_id,
                    worker_id="model-owner",
                    operation_id="model-op",
                    sequence=2,
                    started_at=now,
                )
            result = await audit.finish(
                run_id=run_id,
                thread_id=thread_id,
                worker_id="model-owner",
                operation_id="model-op",
                content="answer",
                finished_at=now + timedelta(seconds=1),
                duration_ms=100,
                usage={"input_tokens": 3, "output_tokens": 2},
            )
            assert result.id == first.id
            with pytest.raises(ValueError, match="不同结果覆盖"):
                await audit.finish(
                    run_id=run_id,
                    thread_id=thread_id,
                    worker_id="model-owner",
                    operation_id="model-op",
                    content="different",
                    finished_at=now + timedelta(seconds=2),
                    duration_ms=200,
                    usage=None,
                )
            with pytest.raises(ValueError):
                await audit.start(
                    run_id=run_id,
                    thread_id=thread_id,
                    worker_id="other-owner",
                    operation_id="other-op",
                    sequence=2,
                    started_at=now,
                )
            await db.commit()
        async with sessions() as db:
            run = await db.get(AgentRun, run_id)
            [audit] = await ModelMessageAuditRepository(db).list_for_run(run_id)
            assert (audit.turn_id, audit.run_id, audit.usage) == (
                run.turn_id,
                run_id,
                {"input_tokens": 3, "output_tokens": 2},
            )
    finally:
        await cleanup_agent_run_threads(sessions, [thread_id])


async def test_tool_execution_is_one_message_bound_to_model_declaration(postgres_sessions):
    """工具执行须由同 Run 模型声明，并仅拥有一条持久消息。"""
    sessions = postgres_sessions
    run_id, thread_id, _ = await _create_run(sessions)
    now = utc_now()
    try:
        async with sessions() as db:
            repo = AgentRunRepository(db)
            _, acquired = await repo.mark_running(run_id, worker_id="tool-owner", lease_seconds=60, now=now)
            assert acquired is True
            model = ModelMessageAuditRepository(db)
            await model.start(
                run_id=run_id,
                thread_id=thread_id,
                worker_id="tool-owner",
                operation_id="model-op",
                sequence=1,
                started_at=now,
                metadata={"tool_calls": [{"id": "call-1", "name": "test_tool", "args": {"x": 1}}]},
            )
            tool = ToolMessageAuditRepository(db)
            with pytest.raises(ValueError, match="无法关联"):
                await tool.start(
                    run_id=run_id,
                    thread_id=thread_id,
                    worker_id="tool-owner",
                    tool_call_id="unclaimed",
                    tool_name="test_tool",
                    tool_input={},
                    sequence=2,
                    started_at=now,
                )
            first, created = await tool.start(
                run_id=run_id,
                thread_id=thread_id,
                worker_id="tool-owner",
                tool_call_id="call-1",
                tool_name="test_tool",
                tool_input={"x": 1},
                sequence=2,
                started_at=now,
            )
            duplicate, duplicate_created = await tool.start(
                run_id=run_id,
                thread_id=thread_id,
                worker_id="tool-owner",
                tool_call_id="call-1",
                tool_name="test_tool",
                tool_input={"x": 1},
                sequence=2,
                started_at=now,
            )
            assert created is True and duplicate_created is False and first.id == duplicate.id
            completed = await tool.complete(
                run_id=run_id,
                thread_id=thread_id,
                worker_id="tool-owner",
                tool_call_id="call-1",
                output={"ok": True},
                finished_at=now + timedelta(seconds=1),
                duration_ms=20,
                finished_sequence=3,
            )
            assert completed.execution_status == "completed" and completed.content == ""
            with pytest.raises(ValueError):
                await tool.start(
                    run_id=run_id,
                    thread_id=thread_id,
                    worker_id="other-owner",
                    tool_call_id="call-1",
                    tool_name="test_tool",
                    tool_input={"x": 1},
                    sequence=2,
                    started_at=now,
                )
            await db.commit()
        async with sessions() as db:
            [audit] = await ToolMessageAuditRepository(db).list_for_run(run_id)
            assert "compatibility_tool_call_id" not in audit.extra_metadata
            assert audit.turn_id == (await db.get(AgentRun, run_id)).turn_id
            assert audit.execution_status == "completed" and audit.content == ""
            assert audit.extra_metadata["input"] == {"x": 1}
            assert audit.extra_metadata["output"] == {"ok": True}
            from sqlalchemy.exc import IntegrityError

            with pytest.raises(IntegrityError, match="ck_messages_tool_result_single_representation"):
                async with db.begin_nested():
                    audit.content = "第二份结果不得保存"
                    await db.flush()
    finally:
        await cleanup_agent_run_threads(sessions, [thread_id])


async def test_message_recorder_commits_facts_before_public_projection(postgres_sessions, monkeypatch):
    """同一记录器写入真实 PG，公开投影失败保留已提交结果且错误 owner 不能覆盖。"""
    sessions = postgres_sessions
    run_id, thread_id, _ = await _create_run(sessions)
    owner = "recorder-owner"
    try:
        async with sessions() as db:
            run, acquired = await AgentRunRepository(db).mark_running(run_id, worker_id=owner, lease_seconds=60)
            assert acquired is True
            turn_id = run.turn_id
            await db.commit()
        monkeypatch.setattr(pg_manager, "get_async_session_context", lambda: _session_context(sessions))
        recorder = RunMessageRecorder(run_id=run_id, thread_id=thread_id, worker_id=owner)
        adapter = OpenAIEventAdapter(run_id=run_id, turn_id=turn_id, thread_id=thread_id, worker_id=owner)

        def event(seq, data, method="messages"):
            """使用固定原生来源，不从 mapper 生成预期值。"""
            return {
                "method": method,
                "seq": seq,
                "params": {
                    "timestamp": 1_777_000_123_456 + seq,
                    "namespace": [],
                    "data": (data, {"run_id": "model", "thread_id": thread_id}) if method == "messages" else data,
                },
            }

        start = event(0, {"event": "message-start", "id": "model"})
        tool_start = event(
            3,
            {"event": "tool-started", "tool_call_id": "call", "tool_name": "search", "input": {"q": "actual"}},
            "tools",
        )
        for raw in [
            start,
            start,
            event(
                1,
                {
                    "event": "content-block-finish",
                    "index": 0,
                    "content": {"type": "tool_call", "id": "call", "name": "search", "args": {"q": "actual"}},
                },
            ),
            event(2, {"event": "message-finish", "usage": {"input_tokens": 5}}),
            tool_start,
            tool_start,
        ]:
            await adapter.consume(await recorder.consume(raw))

        command = Command(
            update={
                "messages": [
                    ToolMessage(content="other", tool_call_id="other"),
                    ToolMessage(content="result", tool_call_id="call"),
                ],
            }
        )
        raw = event(4, {"event": "tool-finished", "tool_call_id": "call", "output": command}, "tools")
        normalized = await recorder.consume(raw)
        assert raw["params"]["data"]["output"] is command
        save = adapter._save

        async def reject_projection(*args, **kwargs):
            """公开投影拒绝不应回滚独立消息事实。"""
            raise ValueError("projection rejected")

        monkeypatch.setattr(adapter, "_save", reject_projection)
        with pytest.raises(ValueError, match="projection rejected"):
            await adapter.consume(normalized)
        async with sessions() as db:
            [model] = await ModelMessageAuditRepository(db).list_for_run(run_id)
            [tool] = await ToolMessageAuditRepository(db).list_for_run(run_id)
            assert model.execution_status == tool.execution_status == "completed"
            assert model.usage == {"input_tokens": 5}
            assert tool.content == "" and tool.extra_metadata["output"]["tool_call_id"] == "call"
            assert tool.extra_metadata["source_model_message_id"] == model.id
            assert tool.extra_metadata["public_items"]["output"]["status"] == "in_progress"
            assert tool.duration_ms is not None and tool.duration_ms >= 0
            assert "compatibility_tool_call_id" not in tool.extra_metadata
            assert tool.operation_id == "call"

        wrong_owner = RunMessageRecorder(run_id=run_id, thread_id=thread_id, worker_id="other-owner")
        with pytest.raises(ValueError):
            await wrong_owner.consume(raw)
        monkeypatch.setattr(adapter, "_save", save)
        public = await adapter.consume(normalized)
        assert public[-1]["item"]["output"] == "result" and public[-1]["item"]["call_id"] == "call"
        async with sessions() as db:
            [tool] = await ToolMessageAuditRepository(db).list_for_run(run_id)
            assert tool.content == "" and tool.execution_status == "completed"
            assert tool.extra_metadata["public_items"]["output"]["status"] == "completed"
            assert tool.extra_metadata["public_items"]["output"]["id"] == public[-1]["item"]["id"]
    finally:
        await cleanup_agent_run_threads(sessions, [thread_id])


async def test_langfuse_identity_is_write_once_by_live_owner(postgres_sessions):
    """Trace 与 Run observation 只能由当前 lease owner 固化并幂等重放。"""
    sessions = postgres_sessions
    run_id, thread_id, _ = await _create_run(sessions)
    try:
        async with sessions() as db:
            repo = AgentRunRepository(db)
            _, acquired = await repo.mark_running(run_id, worker_id="trace-owner", lease_seconds=60)
            assert acquired is True
            assert await repo.set_langfuse_trace_id(run_id, "trace-1", worker_id="trace-owner")
            assert await repo.set_langfuse_trace_id(run_id, "trace-1", worker_id="trace-owner")
            with pytest.raises(ValueError, match="不同"):
                await repo.set_langfuse_trace_id(run_id, "trace-2", worker_id="trace-owner")
            with pytest.raises(ValueError):
                await repo.set_langfuse_trace_id(run_id, "trace-1", worker_id="old-owner")
            assert await repo.set_langfuse_observation_id(run_id, "0123456789abcdef", worker_id="trace-owner")
            with pytest.raises(ValueError, match="不同"):
                await repo.set_langfuse_observation_id(run_id, "fedcba9876543210", worker_id="trace-owner")
            await db.commit()
        async with sessions() as db:
            run = await db.get(AgentRun, run_id)
            assert (run.langfuse_trace_id, run.langfuse_observation_id) == ("trace-1", "0123456789abcdef")
    finally:
        await cleanup_agent_run_threads(sessions, [thread_id])


async def test_root_failure_preserves_independent_child_turn(postgres_sessions, monkeypatch):
    """父 Run 失败不取消拥有独立 Turn 和 runtime 的子任务。"""
    sessions = postgres_sessions
    parent_id, parent_thread_id, _ = await _create_run(sessions)
    child_thread_id = f"pytest-child-{uuid.uuid4()}"
    child_id = str(uuid.uuid4())
    try:
        async with sessions() as db:
            parent = await db.get(AgentRun, parent_id)
            parent_thread = await db.get(Session, parent.session_record_id)
            child_thread = Session(
                thread_id=child_thread_id,
                uid=parent.uid,
                project_id=parent_thread.project_id,
                agent_id="worker",
                tree_root_thread_id=parent_thread_id,
                parent_thread_id=parent_thread_id,
                cooperation_name="worker",
                cooperation_path="/root/worker",
                status="active",
            )
            db.add(child_thread)
            await db.flush()
            message = Message(session_record_id=child_thread.id, role="user", content="child input", delivery_status="dispatched")
            db.add(message)
            await db.flush()
            child_turn = AgentTurn(
                id=f"child-turn-{uuid.uuid4()}",
                thread_id=child_thread_id,
                uid=parent.uid,
                status="running",
                current_run_id=child_id,
            )
            db.add(child_turn)
            await db.flush()
            db.add(
                AgentRun(
                    id=child_id,
                    thread_id=child_thread_id,
                    runtime_scope_id=parent_thread_id,
                    agent_slug="worker",
                    uid=parent.uid,
                    app_id=None,
                    turn_id=child_turn.id,
                    session_record_id=child_thread.id,
                    created_by_run_id=parent_id,
                    run_type="chat",
                    input_message_id=message.id,
                    input_payload={},
                    status="pending",
                )
            )
            message.run_id = child_id
            message.turn_id = child_turn.id
            await db.flush()
            repo = AgentRunRepository(db)
            assert (await repo.mark_running(parent_id, worker_id="parent-owner", lease_seconds=60))[1]
            assert (await repo.mark_running(child_id, worker_id="child-owner", lease_seconds=60))[1]
            await db.commit()

        monkeypatch.setattr(run_worker.pg_manager, "get_async_session_context", lambda: _session_context(sessions))
        transition = await run_worker.mark_run_terminal(parent_id, "failed", error_type="parent_failed", worker_id="parent-owner")
        assert transition.changed is True
        async with sessions() as db:
            parent = await db.get(AgentRun, parent_id)
            child = await db.get(AgentRun, child_id)
            assert parent.status == "failed"
            assert child.status == "running" and child.error_type is None
            assert child.worker_id == "child-owner"
            assert (await db.get(AgentTurn, child.turn_id)).status == "running"
            assert (await db.get(AgentTurn, parent.turn_id)).status == "failed"
    finally:
        await cleanup_agent_run_threads(sessions, [parent_thread_id, child_thread_id])


async def test_cancelling_recovery_isolates_bad_checkpoint(postgres_sessions, monkeypatch):
    """首条 checkpoint 清理失败仍收敛后续 Turn，同时拒绝报告整轮成功。"""
    sessions = postgres_sessions
    bad_id, bad_thread, _ = await _create_run(sessions, status="interrupted")
    good_id, good_thread, _ = await _create_run(sessions, status="cancelled")
    monkeypatch.setattr(lifecycle.pg_manager, "get_async_session_context", lambda: _session_context(sessions))
    monkeypatch.setattr(lifecycle, "_clear_waitpoint_checkpoint", AsyncMock(side_effect=RuntimeError("broken checkpoint")))
    monkeypatch.setattr(lifecycle, "finish_turn_observation_if_terminal", AsyncMock())
    try:
        async with sessions() as db:
            for run_id in [bad_id, good_id]:
                run = await db.get(AgentRun, run_id)
                run.runtime_cleanup_pending = False
                turn = await db.get(AgentTurn, run.turn_id)
                turn.status = "cancelling"
                if run_id == bad_id:
                    turn.waitpoint = {"kind": "answer", "id": "broken", "run_id": bad_id}
            await db.commit()
        with pytest.raises(RuntimeError, match="cancelling Turn recovery failed"):
            await lifecycle.reconcile_cancelling_turns()
        async with sessions() as db:
            bad = await db.get(AgentRun, bad_id)
            good = await db.get(AgentRun, good_id)
            assert (await db.get(AgentTurn, bad.turn_id)).status == "cancelling"
            assert (await db.get(AgentTurn, good.turn_id)).status == "cancelled"
    finally:
        await cleanup_agent_run_threads(sessions, [bad_thread, good_thread])


async def test_cancelled_cleanup_never_releases_responsibility_early(postgres_sessions, monkeypatch, tmp_path):
    """外部命令未结束时取消清理协程，不得提前交还清理责任；销毁期间不持有数据库事务/行锁，也不误清已领取的新 attempt。"""
    from sqlalchemy import text

    sessions = postgres_sessions
    run_id, thread_id, _ = await _create_run(sessions)
    started, finish = threading.Event(), threading.Event()
    command = tmp_path / "command"
    command.write_text("old attempt")

    def cancel_commands(*_):
        """模拟仍在运行的外部清理，以文件作为命令存活证据。"""
        started.set()
        if not finish.wait(10):
            raise TimeoutError("test cleanup was not released")
        command.unlink(missing_ok=True)

    task = None
    try:
        async with sessions() as db:
            stale = await db.get(AgentRun, run_id)
            stale.runtime_cleanup_pending = True
            await db.commit()
        monkeypatch.setattr(lease_worker.pg_manager, "get_async_session_context", lambda: _session_context(sessions))
        monkeypatch.setattr(
            lifecycle, "get_sandbox_provider", lambda: type("Provider", (), {"cleanup_run": staticmethod(cancel_commands)})()
        )
        task = asyncio.create_task(lease_worker.complete_run_runtime_cleanup(stale))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        async with sessions() as db:
            # 销毁在事务外进行：外部命令仍在跑时行锁已可 NOWAIT 取得；随后取消不得交还责任。
            locked = await db.execute(text("SELECT id FROM agent_runs WHERE id=:id FOR NO KEY UPDATE NOWAIT"), {"id": run_id})
            assert locked.scalar() == run_id
            await db.rollback()
        assert command.read_text() == "old attempt"
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with sessions() as db:
            assert (await db.get(AgentRun, run_id)).runtime_cleanup_pending is True
        assert await lease_worker.complete_run_runtime_cleanup(stale)
        async with sessions() as db:
            run, acquired = await AgentRunRepository(db).mark_running(run_id, worker_id="new-attempt", lease_seconds=60)
            assert acquired
            await db.commit()
        command.write_text("new attempt")
        assert await lease_worker.complete_run_runtime_cleanup(stale)
        assert command.read_text() == "new attempt"
        async with sessions() as db:
            current = await db.get(AgentRun, run_id)
            assert current.status == "running" and current.worker_id == "new-attempt"
            assert current.runtime_cleanup_pending is False
    finally:
        finish.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        await cleanup_agent_run_threads(sessions, [thread_id])


async def test_runtime_cleanup_does_not_hold_db_transaction_during_release(postgres_sessions, monkeypatch: pytest.MonkeyPatch):
    """负向守卫：cleanup_run 阻塞期间不持有 Run 行锁与 idle in transaction 连接。

    沙盒销毁是阻塞 HTTP（现场实测 22~26 秒）；若它仍在事务内，下面的 NOWAIT 取行锁
    会 LockNotAvailableError，且 cleanup 责任标志的确认写会被同一事务连坐。
    """
    run_id, thread_id, _ = await _create_run(postgres_sessions, status="completed")
    try:
        async with postgres_sessions() as db:
            staged = await db.get(AgentRun, run_id)
            staged.runtime_cleanup_pending = True
            await db.commit()

        started = threading.Event()
        proceed = threading.Event()
        calls: list[tuple[str, str]] = []

        def cleanup_run(uid: str, target_run_id: str) -> None:
            calls.append((uid, target_run_id))
            started.set()
            assert proceed.wait(timeout=10)

        monkeypatch.setattr(lifecycle, "get_sandbox_provider", lambda: type("Provider", (), {"cleanup_run": staticmethod(cleanup_run)})())
        monkeypatch.setattr(lifecycle.pg_manager, "get_async_session_context", lambda: _session_context(postgres_sessions))

        async with postgres_sessions() as db:
            run = await db.get(AgentRun, run_id)
        task = asyncio.create_task(lifecycle.complete_run_runtime_cleanup(run))
        assert await asyncio.to_thread(started.wait, 10)
        try:
            async with postgres_sessions() as probe:
                locked = await probe.scalar(select(AgentRun.id).where(AgentRun.id == run_id).with_for_update(nowait=True))
                assert locked == run_id
                assert (await probe.get(AgentRun, run_id)).runtime_cleanup_pending is True
                await probe.rollback()
        finally:
            proceed.set()
        assert await asyncio.wait_for(task, 10) is True

        async with postgres_sessions() as db:
            assert (await db.get(AgentRun, run_id)).runtime_cleanup_pending is False
        assert calls and calls[0][1] == run_id
    finally:
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])


async def test_runtime_cleanup_does_not_release_another_generations_flag(postgres_sessions, monkeypatch: pytest.MonkeyPatch):
    """越代守卫：旧清理者苏醒时 Run 已被新 attempt 领取并再次挂起责任，不得清除新一代标志。"""
    run_id, thread_id, _ = await _create_run(postgres_sessions, status="pending")
    let_finish = threading.Event()
    try:
        async with postgres_sessions() as db:
            staged = await db.get(AgentRun, run_id)
            staged.runtime_cleanup_pending = True
            await db.commit()

        entered = threading.Event()

        def cleanup_run(*_):
            entered.set()
            assert let_finish.wait(timeout=10)

        monkeypatch.setattr(lifecycle, "get_sandbox_provider", lambda: type("Provider", (), {"cleanup_run": staticmethod(cleanup_run)})())
        monkeypatch.setattr(lifecycle.pg_manager, "get_async_session_context", lambda: _session_context(postgres_sessions))
        async with postgres_sessions() as db:
            run = await db.get(AgentRun, run_id)
        task = asyncio.create_task(lifecycle.complete_run_runtime_cleanup(run))
        assert await asyncio.to_thread(entered.wait, 10)
        async with postgres_sessions() as db:
            # 模拟第二清理者已交还责任（标志 False 才可领取），新 attempt 落库后再次挂起为新一代责任。
            waiting = await db.get(AgentRun, run_id)
            waiting.runtime_cleanup_pending = False
            _, acquired = await AgentRunRepository(db).mark_running(run_id, worker_id="new-attempt", lease_seconds=60)
            assert acquired
            gen2 = await db.get(AgentRun, run_id)
            gen2.runtime_cleanup_pending = True
            await db.commit()
        let_finish.set()
        assert await asyncio.wait_for(task, 10) is True
        async with postgres_sessions() as db:
            assert (await db.get(AgentRun, run_id)).runtime_cleanup_pending is True
    finally:
        let_finish.set()
        async with postgres_sessions() as db:
            current = await db.get(AgentRun, run_id)
            if current is not None:
                current.runtime_cleanup_pending = False
                await db.commit()
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])
