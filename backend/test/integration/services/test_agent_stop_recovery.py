"""停止完成、空队列与用户优先恢复的 PostgreSQL 回归。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from test.integration.services.agent_input_fixtures import SCOPE, queue_inputs, start_input_run
import yuxi.modules.agents.services.run_lifecycle as lifecycle
from yuxi.modules.agents.services.run_lifecycle import settle_run
from test.integration.services.test_arq_worker_dispatch import isolated_queue as isolated_queue
from yuxi.modules.agents.input_messages import build_chat_input_message
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.services import inputs, leases, runner, runs, scheduler, threads, turns

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
pytest_plugins = ["test.integration.services.agent_input_fixtures"]


async def test_cancel_pending_with_empty_queue_does_not_pause_future_work(sessions):
    """空队列停止后无需继续命令即可接收下一轮。"""
    run = await start_input_run(sessions, running=False)
    async with sessions() as db:
        await turns.cancel_turn(db=db, scope=SCOPE, thread_id="input-thread", turn_id=run.turn_id, idempotency_key="stop-empty")
    async with sessions() as db:
        assert (await db.get(AgentTurn, run.turn_id)).status == "cancelled"
        assert not (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused


async def test_running_cancel_finishes_turn_after_cleanup_without_reconciler(sessions, monkeypatch):
    """真实清理入口应直接收敛 Turn，不调用周期恢复。"""
    run = await start_input_run(sessions)
    monkeypatch.setattr(lifecycle, "get_sandbox_provider", lambda: type("Provider", (), {"cleanup_run": staticmethod(lambda *_: None)})())
    async with sessions() as db:
        await turns.cancel_turn(db=db, scope=SCOPE, thread_id="input-thread", turn_id=run.turn_id, idempotency_key="stop-running")
    async with sessions() as db:
        current = await db.get(AgentRun, run.id)
        await settle_run(db=db, run=current, worker_id="owner", status="cancelled", token_usage=None)
        await db.commit()
    async with sessions() as db:
        current = await db.get(AgentRun, run.id)
        assert (await db.get(AgentTurn, run.turn_id)).status == "cancelling"
        assert current.runtime_cleanup_pending
    assert await leases.complete_run_runtime_cleanup(current)
    async with sessions() as db:
        assert (await db.get(AgentTurn, run.turn_id)).status == "cancelled"
        assert not (await db.get(AgentRun, run.id)).runtime_cleanup_pending


async def test_user_message_resumes_paused_queue_before_old_steer(sessions):
    """新用户消息独立执行，不能被旧引导抢先或合并。"""
    await queue_inputs(sessions, count=2)
    async with sessions() as db:
        (await db.get(AgentInput, "input-0")).kind = "steer"
        await db.commit()
        accepted = await inputs.accept_message(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            idempotency_key="new-user",
            mode=None,
            messages=[build_chat_input_message("new user instruction")],
        )
    async with sessions() as db:
        new = await db.get(AgentInput, accepted["input_id"])
        assert new.status == "consumed"
        assert not (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused
        assert (await db.get(AgentInput, "input-0")).status == "pending"
        assert (await db.get(AgentInput, "input-1")).status == "pending"
        consumed = list(await db.scalars(select(AgentInput).where(AgentInput.consumed_run_id == new.consumed_run_id)))
        assert [item.id for item in consumed] == [new.id]
        assert (await AgentRunRepository(db).mark_running(new.consumed_run_id, worker_id="resume-owner", lease_seconds=60))[1]
        await db.commit()
    assert not await runs.should_yield_for_steer(new.consumed_run_id), "旧引导不能抢占恢复消息"
    async with sessions() as db:
        current = await db.get(AgentRun, new.consumed_run_id)
        output = Message(
            session_record_id=current.session_record_id, role="assistant", content="new done", run_id=current.id, turn_id=current.turn_id
        )
        db.add(output)
        await db.flush()
        current.output_message_id = output.id
        result = await settle_run(db=db, run=current, worker_id="resume-owner", status="completed", token_usage=None)
        assert result.status == "completed", "旧引导不能把恢复消息完成改写成 yielded"
        current.runtime_cleanup_pending = False
        await db.commit()
    async with sessions() as db:
        session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
        old_run_id = await scheduler.claim_next_input(db=db, agent_session=session)
        await db.commit()
    async with sessions() as db:
        previous = await db.get(AgentInput, "input-0")
        assert previous.consumed_run_id == old_run_id and previous.turn_id != new.turn_id
        assert (await db.get(AgentInput, "input-1")).status == "pending"


async def test_removing_last_paused_input_clears_pause(sessions):
    """删除最后一条待办后不遗留空暂停。"""
    await queue_inputs(sessions, count=1)
    async with sessions() as db:
        await threads.cancel_input(db=db, scope=SCOPE, thread_id="input-thread", input_id="input-0", idempotency_key="remove-last")
    async with sessions() as db:
        assert (await db.get(AgentInput, "input-0")).status == "cancelled"
        assert not (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused


@pytest.mark.parametrize("pending_cleanup", [False, True])
async def test_waiting_cancel_with_empty_queue_finishes_without_pause(sessions, monkeypatch, pending_cleanup):
    """人工等待的清理条件独立于是否有排队输入。"""
    run = await start_input_run(sessions)
    async with sessions() as db:
        current = await db.get(AgentRun, run.id)
        await settle_run(
            db=db,
            run=current,
            worker_id="owner",
            status="interrupted",
            token_usage=None,
            waitpoint={"id": "wait", "kind": "answer", "run_id": run.id},
        )
        current.runtime_cleanup_pending = pending_cleanup
        await db.commit()

    async def clear_checkpoint(current):
        """独立回读外部清理事实，取消不能仅靠 checkpoint 更新完成。"""
        async with sessions() as db:
            assert not (await db.get(AgentRun, current.id)).runtime_cleanup_pending

    monkeypatch.setattr(lifecycle, "get_sandbox_provider", lambda: type("Provider", (), {"cleanup_run": staticmethod(lambda *_: None)})())
    monkeypatch.setattr(lifecycle, "_clear_waitpoint_checkpoint", clear_checkpoint)
    async with sessions() as db:
        await turns.cancel_turn(db=db, scope=SCOPE, thread_id="input-thread", turn_id=run.turn_id, idempotency_key="stop-wait")
    async with sessions() as db:
        turn = await db.get(AgentTurn, run.turn_id)
        assert turn.status == "cancelled" and turn.waitpoint is None
        assert not (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused


@pytest.mark.parametrize("kind", ["answer", "approval", "cooperation"])
async def test_waiting_cancellation_http_snapshot_keeps_stopping_fact(sessions, monkeypatch, kind):
    """Run 保持 interrupted 时，HTTP 快照仍明确表达 Turn 的取消收尾。"""
    import httpx
    from fastapi import FastAPI

    from yuxi.api.dependencies.auth import get_db
    from yuxi.api.routers.public_v1.agents.auth import require_public_context
    from yuxi.api.routers.public_v1.agents.turns import router

    run = await start_input_run(sessions)
    async with sessions() as db:
        current = await db.get(AgentRun, run.id)
        await settle_run(
            db=db,
            run=current,
            worker_id="owner",
            status="interrupted",
            token_usage=None,
            waitpoint={"id": "wait", "kind": kind, "run_id": run.id},
        )
        current.runtime_cleanup_pending = False
        await db.commit()
        await turns.cancel_turn(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            turn_id=run.turn_id,
            idempotency_key="stop-http-wait",
            cleanup_waiting=False,
        )

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/agents")

    async def database():
        """使用隔离 PostgreSQL 的真实读取事务。"""
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_public_context] = lambda: SimpleNamespace(scope=SCOPE)
    monkeypatch.setattr(lifecycle, "_clear_waitpoint_checkpoint", AsyncMock())
    url = f"/api/v1/agents/sessions/input-thread/turns/{run.turn_id}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(url)
        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["status"] == "in_progress"
        assert snapshot["yuxi"]["runs"][0]["status"] == "interrupted"
        assert snapshot["yuxi"]["cancellation_pending"] is True
        assert await lifecycle.settle_waiting_cancel(thread_id="input-thread", turn_id=run.turn_id, uid=SCOPE.uid, app_id=None)
        snapshot = (await client.get(url)).json()
        assert snapshot["status"] == "cancelled" and snapshot["yuxi"]["cancellation_pending"] is False


async def test_waiting_checkpoint_cleanup_excludes_other_thread_writers(sessions, monkeypatch):
    """真实行锁阻止迟到的等待清理越过终态并改写下一轮 checkpoint。"""
    run = await start_input_run(sessions)
    async with sessions() as db:
        current = await db.get(AgentRun, run.id)
        await settle_run(
            db=db,
            run=current,
            worker_id="owner",
            status="interrupted",
            token_usage=None,
            waitpoint={"id": "wait", "kind": "answer", "run_id": run.id},
        )
        current.runtime_cleanup_pending = False
        await db.commit()
        await turns.cancel_turn(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            turn_id=run.turn_id,
            idempotency_key="stop-wait-serial",
            cleanup_waiting=False,
        )
    clearing, allow_clear = asyncio.Event(), asyncio.Event()

    async def clear_checkpoint(_):
        """在真实 checkpoint 副作用边界固定并发窗口。"""
        clearing.set()
        await allow_clear.wait()

    monkeypatch.setattr(lifecycle, "_clear_waitpoint_checkpoint", clear_checkpoint)
    arguments = dict(thread_id="input-thread", turn_id=run.turn_id, uid=SCOPE.uid, app_id=None)
    cleanup = asyncio.create_task(lifecycle.settle_waiting_cancel(**arguments))
    try:
        await asyncio.wait_for(clearing.wait(), 5)
        async with sessions() as db:
            with pytest.raises(DBAPIError) as error:
                await db.scalar(select(Session).where(Session.thread_id == "input-thread").with_for_update(key_share=True, nowait=True))
            assert error.value.orig.sqlstate == "55P03", "Thread 锁必须覆盖 checkpoint 副作用"
    finally:
        allow_clear.set()
        assert await asyncio.wait_for(cleanup, 5)
    assert not await lifecycle.settle_waiting_cancel(**arguments)
    async with sessions() as db:
        assert (await db.get(AgentTurn, run.turn_id)).status == "cancelled"
        accepted = await inputs.accept_message(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            idempotency_key="after-serialized-cleanup",
            mode=None,
            messages=[build_chat_input_message("new work")],
        )
    async with sessions() as db:
        assert (await db.get(AgentInput, accepted["input_id"])).turn_id != run.turn_id


async def test_failure_cleanup_blocks_next_turn_even_when_queue_is_resumed(sessions):
    """前轮失败但外部清理未完成时，继续队列不能启动新轮。"""
    run = await start_input_run(sessions)
    async with sessions() as db:
        current = await db.get(AgentRun, run.id)
        await settle_run(db=db, run=current, worker_id="owner", status="failed", token_usage=None)
        await db.commit()
    async with sessions() as db:
        accepted = await inputs.accept_message(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            idempotency_key="queued-after-failure",
            mode="follow_up",
            messages=[build_chat_input_message("pending")],
            source="cooperation",
        )
    async with sessions() as db:
        session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
        session.queue_paused = False
        assert await scheduler.claim_next_input(db=db, agent_session=session) is None
        assert (await db.get(AgentInput, accepted["input_id"])).status == "pending"


async def test_failure_before_model_preparation_finishes_cleanup_without_scan(sessions, monkeypatch):
    """执行准备前明确失败也由本次 worker 释放清理责任。"""
    run = await start_input_run(sessions, running=False)
    async with sessions() as db:
        current = await db.get(AgentRun, run.id)
        current.input_payload = {"runtime": "invalid"}
        await db.commit()
    monkeypatch.setattr(lifecycle, "get_sandbox_provider", lambda: type("Provider", (), {"cleanup_run": staticmethod(lambda *_: None)})())
    await runner.process_agent_run({"job_try": 1}, run.id)
    async with sessions() as db:
        current = await db.get(AgentRun, run.id)
        assert current.status == "failed" and current.error_type == "invalid_runtime_payload"
        assert not current.runtime_cleanup_pending and current.worker_id is None
        assert (await db.get(AgentTurn, run.turn_id)).status == "failed"


async def test_cancel_between_failure_check_and_terminal_commit_is_settled_by_owner(sessions, monkeypatch):
    """失败或超时已选定后到达的取消，仍须由当前 owner 完整收敛。"""
    run = await start_input_run(sessions)
    assert not await runner._confirmed_user_cancel(run.id)
    async with sessions() as db:
        await turns.cancel_turn(db=db, scope=SCOPE, thread_id="input-thread", turn_id=run.turn_id, idempotency_key="late-cancel")
    monkeypatch.setattr(lifecycle, "get_sandbox_provider", lambda: type("Provider", (), {"cleanup_run": staticmethod(lambda *_: None)})())
    transition = await runner._settle_uncommitted_run(
        run.id,
        "failed",
        thread_id="input-thread",
        current_user=None,
        worker_id="owner",
        error_type="execution_timeout",
    )
    assert transition.status == "cancelled"
    async with sessions() as db:
        current = await db.get(AgentRun, run.id)
        assert (current.status, current.error_type) == ("cancelled", "cancelled")
        assert not current.runtime_cleanup_pending and current.worker_id is None
        assert (await db.get(AgentTurn, run.turn_id)).status == "cancelled"


async def test_replaying_accepted_message_does_not_resume_a_later_pause(sessions):
    """响应丢失的重试只返回旧回执，不能撤销用户后来的停止。"""
    await queue_inputs(sessions, count=1)
    arguments = dict(
        scope=SCOPE,
        thread_id="input-thread",
        idempotency_key="resume-once",
        mode=None,
        messages=[build_chat_input_message("new")],
    )
    async with sessions() as db:
        accepted = await inputs.accept_message(db=db, **arguments)
    async with sessions() as db:
        current = await db.get(AgentInput, accepted["input_id"])
        await turns.cancel_turn(db=db, scope=SCOPE, thread_id="input-thread", turn_id=current.turn_id, idempotency_key="stop-again")
    async with sessions() as db:
        assert await inputs.accept_message(db=db, **arguments) == accepted
    async with sessions() as db:
        assert (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused
        assert (await db.get(AgentInput, "input-0")).status == "pending"


async def test_resuming_input_waits_for_its_attachments_before_old_queue(sessions):
    """持久优先目标的附件未就绪时，重建调度也不能跳过它。"""
    from yuxi.modules.agents.models.attachments import AgentAttachment
    from yuxi.shared.datetime import utc_now

    await queue_inputs(sessions, count=2)
    async with sessions() as db:
        newest = await db.get(AgentInput, "input-1")
        newest.input_payload = {**newest.input_payload, "resume_queue": True}
        db.add(
            AgentAttachment(
                id="resume-file",
                uid=SCOPE.uid,
                filename="new.txt",
                mime_type="text/plain",
                size_bytes=1,
                expires_at=utc_now(),
                status="preparing",
                input_id=newest.id,
                object_name="source",
            )
        )
        await db.commit()
    async with sessions() as db:
        session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
        session.queue_paused = False
        assert await scheduler.claim_next_input(db=db, agent_session=session) is None
        attachment = await db.get(AgentAttachment, "resume-file")
        attachment.status, attachment.path, attachment.original_path = "ready", "new.txt", "new.txt"
        await db.commit()
    async with sessions() as db:
        session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
        claimed_run_id = await scheduler.claim_next_input(db=db, agent_session=session)
        await db.commit()
    async with sessions() as db:
        assert (await db.get(AgentInput, "input-1")).consumed_run_id == claimed_run_id
        assert (await db.get(AgentInput, "input-0")).status == "pending"


async def test_arq_execution_timeout_persists_failed_turn_and_releases_cleanup(sessions, isolated_queue, monkeypatch):
    """真实 ARQ 与 PostgreSQL 证明超时不会留下 pending 或被再次领取。"""
    from arq.worker import func

    from yuxi.workers.arq import YuxiWorker

    run = await start_input_run(sessions, running=False)
    entered, closed = asyncio.Event(), asyncio.Event()

    async def blocked_stream(**_):
        """用受控模型流测试业务时限，执行状态和清理由真实 Owner 维护。"""
        try:
            entered.set()
            await asyncio.Event().wait()
            yield b"unreachable"
        finally:
            closed.set()

    prepared = SimpleNamespace(
        context=SimpleNamespace(
            model="test:chat",
            tool_approval_mode="default",
            runtime_scope_id="input-thread",
            workdir_relative_path="projects/input-project",
            workdir_path="/home/user/workspace/projects/input-project",
        )
    )
    monkeypatch.setattr(runner, "RUN_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(
        runner, "_validate_run_workdir_binding", AsyncMock(return_value=SimpleNamespace(workdir_path="projects/input-project"))
    )
    monkeypatch.setattr(runner, "prepare_and_record_run_execution", AsyncMock(return_value=prepared))
    monkeypatch.setattr(runner, "stream_agent_execution", blocked_stream)
    monkeypatch.setattr(runner, "start_session_title", lambda **_: None)
    monkeypatch.setattr(runner, "get_agent_state_view", AsyncMock(return_value={"agent_state": None}))
    monkeypatch.setattr(lifecycle, "get_sandbox_provider", lambda: type("Provider", (), {"cleanup_run": staticmethod(lambda *_: None)})())
    redis, namespace, queue_name, workers = isolated_queue
    worker = YuxiWorker(
        functions=[func(runner.process_agent_run, timeout=5)],
        redis_pool=redis,
        queue_name=queue_name,
        health_check_key=f"arq:{namespace}:health",
        handle_signals=False,
    )
    workers.append(worker)
    job = await redis.enqueue_job("process_agent_run", run.id, _queue_name=queue_name, _job_id=f"{namespace}-timeout")
    await worker._poll_iteration()
    await asyncio.wait_for(job.result(poll_delay=0.02, timeout=10), 11)
    async with sessions() as db:
        persisted = await db.get(AgentRun, run.id)
        assert entered.is_set() and closed.is_set(), (persisted.status, persisted.error_type, persisted.error_message)
        assert (persisted.status, persisted.error_type) == ("failed", "execution_timeout")
        assert persisted.worker_id is None and not persisted.runtime_cleanup_pending
        assert (await db.get(AgentTurn, run.turn_id)).status == "failed"
        assert not (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused
        _, acquired = await AgentRunRepository(db).mark_running(run.id, worker_id="late", lease_seconds=60)
        assert not acquired
        assert await AgentInputRepository(db).list_pending_inputs(thread_id="input-thread", uid=SCOPE.uid, app_id=None) == []
