"""真实 PostgreSQL 上的 FIFO 领取竞争与 pending 投递补偿。"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import yuxi.modules.agents.services.scheduler as scheduler
import yuxi.modules.agents.services.threads as threads
import yuxi.modules.agents.services.turns as turns
from test.integration.services.agent_input_fixtures import (
    create_input_schema as _create_schema,
)
from test.integration.services.agent_input_fixtures import (
    drop_input_schema as _drop_schema,
)
from test.integration.services.agent_input_fixtures import prepare_input_workdir
from test.integration.services.agent_input_fixtures import (
    queue_inputs as _queue_inputs,
)
from yuxi.modules.agents.services.run_lifecycle import settle_run
from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.input_receipt import AgentInputReceiptRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.shared.errors import StateConflictError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture(autouse=True)
def input_workdir(monkeypatch, tmp_path):
    """为数据库夹具绑定的 Project 准备真实工作目录。"""
    prepare_input_workdir(monkeypatch, tmp_path)


async def test_concurrent_claims_consume_only_fifo_head() -> None:
    """两个领取者持同一 Thread 锁时，只能创建一轮和一个首段 Run。"""
    schema, admin_engine, engine = await _create_schema()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _queue_inputs(sessions, count=2)
        start = asyncio.Event()

        async def claim() -> str | None:
            """模拟两个完成接收事务后的独立调度者。"""
            async with sessions() as db:
                await start.wait()
                agent_session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
                claimed_run_id = await scheduler.claim_next_input(db=db, agent_session=agent_session)
                await db.commit()
                return claimed_run_id

        contenders = [asyncio.create_task(claim()), asyncio.create_task(claim())]
        start.set()
        first, second = await asyncio.gather(*contenders)
        assert len([run_id for run_id in (first, second) if run_id]) == 1
        async with sessions() as db:
            inputs = list((await db.scalars(select(AgentInput).order_by(AgentInput.received_seq))).all())
            assert [(item.id, item.status) for item in inputs] == [("input-0", "consumed"), ("input-1", "pending")]
            assert inputs[0].consumed_run_id in {first, second}
            assert inputs[1].turn_id is None and inputs[1].consumed_run_id is None
            assert await db.scalar(select(func.count()).select_from(AgentTurn)) == 1
            assert await db.scalar(select(func.count()).select_from(AgentRun)) == 1
            messages = list((await db.scalars(select(Message).order_by(Message.id))).all())
            assert [(message.content, message.delivery_status) for message in messages] == [
                ("message-0", "dispatched"),
            ]
    finally:
        await _drop_schema(schema, admin_engine, engine)


async def test_recovery_republishes_committed_pending_run(monkeypatch) -> None:
    """Run 已提交但没有 Redis 任务时，恢复扫描仍找到同一个 Run。"""
    schema, admin_engine, engine = await _create_schema()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _queue_inputs(sessions, count=1)
        async with sessions() as db:
            agent_session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
            claimed_run_id = await scheduler.claim_next_input(db=db, agent_session=agent_session)
            assert claimed_run_id is not None
            await db.commit()

        @asynccontextmanager
        async def test_session():
            """让恢复用例读取隔离 Schema 的真实已提交记录。"""
            async with sessions() as db:
                yield db

        sent = []

        async def record_delivery(run_id: str) -> bool:
            """记录补偿将要投递的持久 Run ID。"""
            sent.append(run_id)
            return True

        monkeypatch.setattr(scheduler.pg_manager, "get_async_session_context", test_session)
        monkeypatch.setattr(scheduler, "deliver", record_delivery)
        await scheduler.recover_pending_dispatches()
        assert sent == [claimed_run_id]
        async with sessions() as db:
            run = await db.get(AgentRun, claimed_run_id)
            assert run.status == "pending" and (await db.get(AgentInput, "input-0")).consumed_run_id == run.id
    finally:
        await _drop_schema(schema, admin_engine, engine)


async def test_cancelling_newer_input_keeps_older_queue_head_visible() -> None:
    """取消较新的 Input 后，较早的待领取输入仍出现在持久队列快照。"""
    schema, admin_engine, engine = await _create_schema()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _queue_inputs(sessions, count=2)
        scope = ActorScope(uid="input-user", app_id=None)
        async with sessions() as db:
            await threads.cancel_input(
                db=db,
                scope=scope,
                thread_id="input-thread",
                input_id="input-1",
                idempotency_key="cancel-newer-input",
            )
            snapshot = await threads.get_queue_snapshot(db=db, scope=scope, thread_id="input-thread")
            assert snapshot["queue_paused"] is False
            assert [(item["input_id"], item["content"]) for item in snapshot["inputs"]] == [("input-0", "message-0")]
        async with sessions() as db:
            persisted = list((await db.scalars(select(AgentInput).order_by(AgentInput.received_seq))).all())
            assert [(item.id, item.status) for item in persisted] == [("input-0", "pending"), ("input-1", "cancelled")]
    finally:
        await _drop_schema(schema, admin_engine, engine)


async def test_completion_winning_thread_lock_rejects_stale_cancel() -> None:
    """完成与取消竞争时，后来者不能改变已提交的 Turn 结果。"""
    schema, admin_engine, engine = await _create_schema()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _queue_inputs(sessions, count=1)
        async with sessions() as db:
            agent_session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
            claimed_run_id = await scheduler.claim_next_input(db=db, agent_session=agent_session)
            assert claimed_run_id is not None
            run = await db.get(AgentRun, claimed_run_id)
            _, acquired = await AgentRunRepository(db).mark_running(run.id, worker_id="owner-a", lease_seconds=60)
            assert acquired is True
            output = Message(
                session_record_id=agent_session.id,
                role="assistant",
                content="done",
                run_id=run.id,
                turn_id=run.turn_id,
                delivery_status="complete",
            )
            db.add(output)
            await db.flush()
            await AgentRunRepository(db).set_output_message(run.id, output.id, worker_id="owner-a")
            turn_id = run.turn_id
            await db.commit()

        scope = ActorScope(uid="input-user", app_id=None)
        lock_held = asyncio.Event()
        control_started = asyncio.Event()

        async def finish():
            """持有 Thread 锁直到控制方已开始竞争，再提交最终结果。"""
            async with sessions() as db:
                await SessionRepository(db).lock_session_by_thread_id("input-thread")
                lock_held.set()
                await control_started.wait()
                await asyncio.sleep(0.05)
                current = await AgentRunRepository(db).get_run(claimed_run_id)
                settled = await settle_run(db=db, run=current, worker_id="owner-a", status="completed", token_usage=None)
                await db.commit()
                return settled

        async def send_control():
            """通过真实领域用例竞争同一 Thread 锁。"""
            await lock_held.wait()
            control_started.set()
            async with sessions() as db:
                try:
                    return await turns.cancel_turn(
                        db=db,
                        scope=scope,
                        thread_id="input-thread",
                        turn_id=turn_id,
                        idempotency_key=str(uuid.uuid4()),
                    )
                except StateConflictError:
                    return "state_conflict"

        settled, rejected = await asyncio.gather(finish(), send_control())
        assert (settled.status, settled.changed, rejected) == ("completed", True, "state_conflict")
        async with sessions() as db:
            run = await db.get(AgentRun, claimed_run_id)
            turn = await db.get(AgentTurn, turn_id)
            agent_session = await SessionRepository(db).get_session_by_thread_id("input-thread")
            assert run.status == "completed" and turn.status == "completed"
            assert turn.result_run_id == run.id and agent_session.queue_paused is False
            assert await db.scalar(select(func.count()).select_from(AgentRun)) == 1
            assert await db.scalar(select(func.count()).select_from(AgentInput)) == 1
    finally:
        await _drop_schema(schema, admin_engine, engine)


@pytest.mark.parametrize("control", ["cancel_turn", "cancel_input"])
async def test_same_key_concurrent_cancel_replays_receipt_after_thread_lock(monkeypatch, control):
    """首次读取都未命中时，后来者在锁内重放，不能按已取消的状态重新拒绝。"""
    schema, admin_engine, engine = await _create_schema()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _queue_inputs(sessions, count=1)
        if control == "cancel_turn":
            async with sessions() as db:
                agent_session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
                claimed_run_id = await scheduler.claim_next_input(db=db, agent_session=agent_session)
                await db.commit()

            async def ignore_external(*args, **kwargs):
                """本用例仅验证真实事务并发，隔离事务提交后的 tracing/Redis 通知。"""

            monkeypatch.setattr(turns, "finish_turn_observation_if_terminal", ignore_external)
            monkeypatch.setattr(turns, "publish_cancel_signals", ignore_external)
        original = AgentInputReceiptRepository.get_for_scope
        seen_sessions, start = set(), asyncio.Event()

        async def initial_miss_barrier(self, **kwargs):
            """两个请求先看到相同的未提交状态，再竞争实际 PostgreSQL Thread 锁。"""
            result = await original(self, **kwargs)
            if id(self.db) not in seen_sessions:
                assert result is None
                seen_sessions.add(id(self.db))
                if len(seen_sessions) == 2:
                    start.set()
                await asyncio.wait_for(start.wait(), timeout=5)
            return result

        monkeypatch.setattr(AgentInputReceiptRepository, "get_for_scope", initial_miss_barrier)

        async def cancel():
            """每个请求持有独立连接和事务。"""
            async with sessions() as db:
                common = dict(
                    db=db,
                    scope=ActorScope(uid="input-user", app_id=None),
                    thread_id="input-thread",
                    idempotency_key="same-cancel",
                )
                if control == "cancel_turn":
                    return await turns.cancel_turn(**common, turn_id=None)
                return await threads.cancel_input(**common, input_id="input-0")

        first, second = await asyncio.gather(cancel(), cancel())
        assert first == second
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(AgentInputReceipt)) == 2
            if control == "cancel_turn":
                target = await db.get(AgentRun, claimed_run_id)
                assert first["turn_id"] == target.turn_id
                assert (await db.get(AgentTurn, target.turn_id)).status == "cancelled"
            else:
                assert (await db.get(AgentInput, "input-0")).status == "cancelled"
    finally:
        await _drop_schema(schema, admin_engine, engine)


@pytest.mark.parametrize("case", ["valid", "wrong_owner", "expired", "new_item", "tool"])
async def test_cancel_partial_snapshot_only_updates_existing_public_message(case):
    """取消快照保留可见正文；失效 owner、新 item 与工具写入均在真实 PG 边界拒绝。"""
    from datetime import timedelta

    from yuxi.modules.agents.repositories.public_items import PublicItemRepository
    from yuxi.shared.datetime import utc_now

    schema, admin_engine, engine = await _create_schema()
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await _queue_inputs(sessions, count=1)
        async with sessions() as db:
            agent_session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
            claimed_run_id = await scheduler.claim_next_input(db=db, agent_session=agent_session)
            run, acquired = await AgentRunRepository(db).mark_running(claimed_run_id, worker_id="owner", lease_seconds=60)
            assert acquired
            message = Message(
                session_record_id=agent_session.id,
                turn_id=run.turn_id,
                run_id=run.id,
                role="assistant",
                operation_id="model",
                content="",
            )
            db.add(message)
            await db.flush()
            initial = await PublicItemRepository(db).save(
                run_id=run.id,
                worker_id="owner",
                operation_id="model",
                role="assistant",
                key="message",
                item={
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "status": "in_progress",
                    "phase": "commentary",
                },
            )
            run.status = "cancel_requested"
            if case == "expired":
                run.lease_expires_at = utc_now() - timedelta(seconds=1)
            await db.commit()
        async with sessions() as db:
            snapshot = {**initial, "status": "incomplete", "content": [{"type": "output_text", "text": "shown"}]}
            if case == "tool":
                snapshot["type"] = "function_call"
            kwargs = dict(
                run_id=claimed_run_id,
                worker_id="wrong" if case == "wrong_owner" else "owner",
                operation_id="model",
                role="assistant",
                key="new" if case == "new_item" else "message",
                item=snapshot,
                cancelled_partial=True,
            )
            if case == "valid":
                result = await PublicItemRepository(db).save(**kwargs)
                assert result["id"] == initial["id"] and result["content"][0]["text"] == "shown"
                await db.commit()
            else:
                with pytest.raises(ValueError, match="取消快照"):
                    await PublicItemRepository(db).save(**kwargs)
        async with sessions() as db:
            persisted = await db.get(Message, message.id)
            assert persisted.extra_metadata["public_items"]["message"]["content"] == (
                [{"type": "output_text", "text": "shown"}] if case == "valid" else []
            )
    finally:
        await _drop_schema(schema, admin_engine, engine)
