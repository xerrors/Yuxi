"""Thread 优先队列在真实 PostgreSQL 上的接收、合批和消费证据。"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from test.integration.services.agent_input_fixtures import (
    SCOPE,
)
from test.integration.services.agent_input_fixtures import (
    input_workdir_binding as _binding,
)
from test.integration.services.agent_input_fixtures import (
    queue_inputs as _queue_inputs,
)
from test.integration.services.agent_input_fixtures import (
    send_input as _send,
)
from test.integration.services.agent_input_fixtures import (
    start_input_run as _start,
)
from yuxi.modules.agents.services.run_lifecycle import settle_run
from yuxi.api.dependencies.auth import get_db
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.public_v1.agents.auth import require_public_context
from yuxi.api.routers.public_v1.agents.events import router
from yuxi.api.routers.public_v1.agents.turns import router as turns_router
from yuxi.modules.agents.input_messages import build_chat_input_message, serialize_input_message
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.input_receipt import AgentInputReceiptRepository
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services import checkpoint_messages as messages
from yuxi.modules.agents.services import input_config, inputs, runs, scheduler, threads, turns
from yuxi.shared.errors import DomainError, StateConflictError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
pytest_plugins = ["test.integration.services.agent_input_fixtures"]


async def test_consumed_message_source_comes_from_persistent_input(sessions):
    """伪造消息来源不会覆盖新消费 Message 的持久来源。"""
    forged = {"source": "internal_wakeup", "channel": "internal", "external_id": "forged"}
    message = build_chat_input_message("普通用户消息").with_metadata({"input_source": forged, "tag": "retained"})
    async with sessions() as db:
        agent_session = await SessionRepository(db).get_session_by_thread_id("input-thread")
        turn = AgentTurn(id="forgery-turn", thread_id="input-thread", uid=SCOPE.uid, status="running")
        db.add(turn)
        await db.flush()
        run = AgentRun(
            id="forgery-run",
            thread_id="input-thread",
            runtime_scope_id="input-thread",
            agent_slug="main",
            uid=SCOPE.uid,
            turn_id=turn.id,
            session_record_id=agent_session.id,
            input_payload={},
            status="pending",
            run_type="chat",
        )
        db.add(run)
        await db.flush()
        item = await AgentInputRepository(db).create(
            input_id="forgery-input",
            thread_id="input-thread",
            uid=SCOPE.uid,
            app_id=None,
            agent_slug="main",
            kind="follow_up",
            source="public_api",
            channel="web",
            external_id=None,
            messages=[serialize_input_message(message)],
        )
        await AgentInputRepository(db).consume(inputs=[item], turn_id=turn.id, run_id=run.id)
        await db.commit()

    async with sessions() as db:
        [persisted] = await AgentInputRepository(db).list_messages("forgery-input")
        original = await db.get(AgentInput, "forgery-input")
        assert persisted.extra_metadata["input_source"] == {"source": "public_api", "channel": "web", "external_id": None}
        assert persisted.extra_metadata["tag"] == "retained"
        assert original.messages[0]["metadata"]["input_source"] == forged
        assert persisted.source_input_id == original.id and persisted.content == "普通用户消息"


@pytest.mark.parametrize("status", ["active", "archived", "deleted"])
async def test_http_input_snapshot_respects_thread_status(sessions, status):
    """实际 HTTP 只向可见会话返回 Input；删除状态保持不可见。"""
    accepted = await _send(sessions, "snapshot-input")
    async with sessions() as db:
        agent_session = await SessionRepository(db).get_session_by_thread_id("input-thread")
        agent_session.status = status
        await db.commit()
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(turns_router, prefix="/api/v1/agents")

    async def database():
        """每个请求独立读取 PostgreSQL 可见状态。"""
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_public_context] = lambda: SimpleNamespace(scope=SCOPE)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/agents/sessions/input-thread/inputs/{accepted['input_id']}")
    if status == "deleted":
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Thread 不存在"
    else:
        assert response.status_code == 200, response.text
        assert response.json()["input_id"] == accepted["input_id"]
        assert response.json()["messages"][0]["content"] == [{"type": "text", "text": "snapshot-input"}]
        assert response.json()["items"] == []
    async with sessions() as db:
        assert (await SessionRepository(db).get_session_by_thread_id("input-thread")).status == status
        assert (await db.get(AgentInput, accepted["input_id"])).status == "pending"


async def test_promote_keeps_original_identity_and_priority_while_paused(sessions):
    """转换只改类型；接收内容、顺序与暂停标记保持不变。"""
    first = await _send(sessions, "B", "follow_up")
    await _send(sessions, "C", "follow_up")
    later = await _send(sessions, "D", "steer")
    async with sessions() as db:
        original = await db.get(AgentInput, first["input_id"])
        before = (original.messages, original.received_seq, original.input_payload, original.created_at)
        promoted = await threads.promote_input(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            input_id=original.id,
            idempotency_key="promote-B",
        )
        replay = await threads.promote_input(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            input_id=original.id,
            idempotency_key="promote-B",
        )
        assert replay == promoted and promoted["input_id"] == first["input_id"]
        await threads.promote_input(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            input_id=original.id,
            idempotency_key="promote-B-again",
        )
        await db.refresh(original)
        assert (original.messages, original.received_seq, original.input_payload, original.created_at) == before
        assert original.kind == "steer" and original.status == "pending"
        assert (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        queue = await threads.get_queue_snapshot(db=db, scope=SCOPE, thread_id="input-thread")
        assert [item["content"] for item in queue["inputs"]] == ["B", "D", "C"]
        accepted = await threads.continue_queue(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="continue-batch")
        ids = await AgentInputRepository(db).input_ids_for_runs([accepted["run_id"]])
        assert ids[accepted["run_id"]] == [first["input_id"], later["input_id"]]
        with pytest.raises(DomainError) as error:
            await threads.promote_input(db=db, scope=SCOPE, thread_id="input-thread", input_id=original.id, idempotency_key="too-late")
        assert isinstance(error.value, StateConflictError)


@pytest.mark.parametrize("waiting", ["answer", "approval", "cooperation", "cancelling"])
async def test_promote_rejects_waiting_or_cleanup(sessions, waiting):
    """等待和取消边界拒绝转换并保留原始队列事实。"""
    current = await _start(sessions)
    queued = await _send(sessions, "pending", "follow_up")
    async with sessions() as db:
        turn = await db.get(AgentTurn, current.turn_id)
        turn.status = "cancelling" if waiting == "cancelling" else "waiting"
        turn.waitpoint = None if waiting == "cancelling" else {"kind": waiting, "run_id": current.id}
        await db.commit()
        with pytest.raises(DomainError) as error:
            await threads.promote_input(
                db=db, scope=SCOPE, thread_id="input-thread", input_id=queued["input_id"], idempotency_key="blocked"
            )
        assert isinstance(error.value, StateConflictError)
        await db.rollback()
        assert (await db.get(AgentInput, queued["input_id"])).kind == "follow_up"


async def test_cancel_one_steer_does_not_cancel_other_submissions(sessions):
    """取消仅针对选中 Input，未消费提交从不生成正式历史。"""
    first = await _send(sessions, "D")
    second = await _send(sessions, "E")
    async with sessions() as db:
        await threads.cancel_input(db=db, scope=SCOPE, thread_id="input-thread", input_id=first["input_id"], idempotency_key="cancel-D")
        snapshot = await inputs.get_input_snapshot(db=db, scope=SCOPE, thread_id="input-thread", input_id=first["input_id"])
        assert snapshot["status"] == "cancelled" and snapshot["items"] == []
        assert snapshot["messages"][0]["content"][0]["text"] == "D"
        assert (await db.get(AgentInput, second["input_id"])).status == "pending"
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        with pytest.raises(DomainError) as error:
            await threads.promote_input(
                db=db, scope=SCOPE, thread_id="input-thread", input_id=first["input_id"], idempotency_key="promote-cancelled"
            )
        assert isinstance(error.value, StateConflictError)


async def test_promote_after_completion_opens_next_turn(sessions):
    """已结束的旧 Turn 不限制仍 pending 输入的优先调度。"""
    current = await _start(sessions)
    queued = await _send(sessions, "next", "follow_up")
    async with sessions() as db:
        await SessionRepository(db).lock_session_by_thread_id("input-thread")
        run = await db.get(AgentRun, current.id)
        output = Message(session_record_id=run.session_record_id, role="assistant", content="finished", run_id=run.id, turn_id=run.turn_id)
        db.add(output)
        await db.flush()
        run.output_message_id = output.id
        await settle_run(db=db, run=run, worker_id="owner", status="completed", token_usage=None)
        run.runtime_cleanup_pending = False
        await db.commit()
        accepted = await threads.promote_input(
            db=db, scope=SCOPE, thread_id="input-thread", input_id=queued["input_id"], idempotency_key="next-turn"
        )
        assert accepted["run_id"] is None and accepted["turn_id"] is None
        consumed = await inputs.get_input_snapshot(db=db, scope=SCOPE, thread_id="input-thread", input_id=queued["input_id"])
        assert consumed["run_id"] and consumed["turn_id"] != current.turn_id
        projected = await AgentInputRepository(db).list_messages(queued["input_id"])
        assert len(projected) == 1 and projected[0].run_id == consumed["run_id"]


async def test_cancel_and_promote_race_keeps_one_submission(sessions):
    """同一 Session 的控制竞争不复制输入或创建取消消息。"""
    queued = await _send(sessions, "race", "follow_up")

    async def control(action):
        """使用独立连接竞争同一会话锁。"""
        async with sessions() as db:
            return await action(db=db, scope=SCOPE, thread_id="input-thread", input_id=queued["input_id"], idempotency_key=action.__name__)

    results = await asyncio.gather(control(threads.cancel_input), control(threads.promote_input), return_exceptions=True)
    assert isinstance(results[0], dict)
    if isinstance(results[1], DomainError):
        assert isinstance(results[1], StateConflictError)
    else:
        assert isinstance(results[1], dict)
    async with sessions() as db:
        assert (await db.get(AgentInput, queued["input_id"])).status == "cancelled"
        assert await db.scalar(select(func.count()).select_from(AgentInput)) == 1
        assert await db.scalar(select(func.count()).select_from(Message)) == 0


async def test_consume_failure_rolls_back_projection_and_ownership(sessions):
    """投影中途失败回滚全部消息和消费归属，原始输入仍可恢复。"""
    first = await _send(sessions, "D")
    second = await _send(sessions, "E")
    async with sessions() as db:
        item = await db.get(AgentInput, second["input_id"])
        item.messages = [{"content": [{"type": "unsupported"}], "metadata": {}, "yuxi": {"attachment_file_ids": []}}]
        await db.commit()
    async with sessions() as db:
        session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
        session.queue_paused = False
        with pytest.raises(ValueError, match="不支持"):
            await scheduler.claim_next_input(db=db, agent_session=session, binding=_binding())
        await db.rollback()
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 0
        assert (await db.get(AgentInput, first["input_id"])).status == "pending"


async def test_empty_session_freezes_system_default_before_first_input(sessions, monkeypatch):
    """创建后的默认模型与 Agent 修改不影响首个输入，投递前事务已提交。"""
    from dataclasses import replace
    from unittest.mock import AsyncMock

    defaults = {"default_model": "test:original-default"}
    monkeypatch.setattr(input_config, "system_options", SimpleNamespace(get=AsyncMock(return_value=defaults)))
    monkeypatch.setattr(inputs, "resolve_session_workdir_binding", AsyncMock(return_value=replace(_binding(), directory_mode="linked")))
    async with sessions() as db:
        agent = await db.scalar(select(Agent).where(Agent.slug == "main"))
        agent.config_json = {"context": {"model": "", "system_prompt": "ORIGINAL"}}
        await db.commit()
        created = await inputs.create_thread(
            db=db,
            scope=SCOPE,
            agent_slug="main",
            thread_id="default-snapshot-thread",
            idempotency_key="default-snapshot-create",
        )
        assert created["input_id"] is None
    async with sessions() as db:
        agent_session = await SessionRepository(db).get_session_by_thread_id("default-snapshot-thread")
        snapshot = agent_session.config_snapshot
        assert snapshot["model"] == "test:original-default"
        assert snapshot["system_prompt"] == "ORIGINAL"
        agent = await db.scalar(select(Agent).where(Agent.slug == "main"))
        agent.config_json = {"context": {"model": "", "system_prompt": "CHANGED"}}
        await db.commit()
    defaults["default_model"] = "test:changed-default"
    async with sessions() as db:
        accepted = await inputs.accept_message(
            db=db,
            scope=SCOPE,
            thread_id="default-snapshot-thread",
            idempotency_key="default-snapshot-input",
            mode=None,
            messages=[build_chat_input_message("first input")],
        )
    async with sessions() as db:
        consumed = await db.get(AgentInput, accepted["input_id"])
        run = await db.get(AgentRun, consumed.consumed_run_id)
        assert run.input_payload["context_snapshot"]["model"] == "test:original-default"
        assert run.input_payload["context_snapshot"] == snapshot
        assert (await db.get(Session, run.session_record_id)).config_snapshot == snapshot
        assert (await db.get(AgentInput, accepted["input_id"])).consumed_run_id == run.id
        receipt = await AgentInputReceiptRepository(db).get_for_scope(
            uid=SCOPE.uid,
            app_id=SCOPE.app_id,
            thread_id="default-snapshot-thread",
            idempotency_key="default-snapshot-input",
        )
        assert receipt.input_id == accepted["input_id"] and receipt.run_id is None and receipt.turn_id is None


async def test_idle_steer_batch_precedes_fifo_and_is_sealed_on_claim(sessions):
    """空闲暂停队列合并 S1/S2，恢复后优先消费；S3 形成新的批次。"""
    await _queue_inputs(sessions, count=2)
    first = await _send(sessions, "S1")
    second = await _send(sessions, "S2")
    assert first["input_id"] != second["input_id"]
    assert first["turn_id"] is None and first["run_id"] is None
    assert "mode" not in first
    async with sessions() as db:
        snapshot = await threads.get_queue_snapshot(db=db, scope=SCOPE, thread_id="input-thread")
        assert [(item["kind"], item["content"]) for item in snapshot["inputs"]] == [
            ("steer", "S1"),
            ("steer", "S2"),
            ("follow_up", "message-0"),
            ("follow_up", "message-1"),
        ]
        assert all(item["turn_id"] is None for item in snapshot["inputs"])
        accepted = await threads.continue_queue(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="continue")
    third = await _send(sessions, "S3")
    assert third["input_id"] != first["input_id"]
    async with sessions() as db:
        batch = await db.get(AgentInput, first["input_id"])
        assert batch.status == "consumed" and batch.consumed_run_id == accepted["run_id"]
        claimed_run = await db.get(AgentRun, accepted["run_id"])
        assert batch.turn_id == claimed_run.turn_id
        receipt = await db.get(AgentInputReceipt, accepted["event_id"])
        assert receipt.input_id is None and receipt.turn_id == claimed_run.turn_id and receipt.run_id == claimed_run.id
        replay = await threads.continue_queue(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="continue")
        assert replay == accepted
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 1
        messages = list(await db.scalars(select(Message).where(Message.run_id == claimed_run.id).order_by(Message.id)))
        assert [(item.content, item.run_id, item.delivery_status) for item in messages] == [
            ("S1", accepted["run_id"], "dispatched"),
            ("S2", accepted["run_id"], "dispatched"),
        ]
        pending = await AgentInputRepository(db).list_pending_inputs(thread_id="input-thread", uid=SCOPE.uid, app_id=None)
        assert [item.id for item in pending] == [third["input_id"], "input-0", "input-1"]
        assert (await db.get(AgentRun, accepted["run_id"])).resume_from_run_id is None
        retry = await inputs.accept_message(
            db=db,
            scope=SCOPE,
            thread_id="input-thread",
            idempotency_key="S1",
            mode="steer",
            messages=[build_chat_input_message("S1")],
        )
        assert retry == first and retry["input_id"] == batch.id and retry["run_id"] is None
        assert await db.scalar(select(func.count()).select_from(AgentInputReceipt).where(AgentInputReceipt.idempotency_key == "S1")) == 1


async def test_safe_handoff_consumes_batch_in_same_turn_with_current_config(sessions):
    """运行中领取全部 steer，旧 Run yielded，follow-up 保持 pending。"""
    current = await _start(sessions)
    follow = await _send(sessions, "F1", "follow_up")
    first = await _send(sessions, "S1")
    second = await _send(sessions, "S2")
    assert first["input_id"] != second["input_id"]
    assert await runs.should_yield_for_steer(current.id)
    async with sessions() as db:
        await SessionRepository(db).lock_session_by_thread_id("input-thread")
        run = await db.get(AgentRun, current.id)
        settled = await settle_run(db=db, run=run, worker_id="owner", status="completed", token_usage=None)
        assert settled.status == "yielded" and settled.next_dispatch.run_id
        await db.commit()
    async with sessions() as db:
        old = await db.get(AgentRun, current.id)
        next_run = await db.get(AgentRun, settled.next_dispatch.run_id)
        assert old.status == "yielded"
        assert old.runtime_cleanup_pending, "yielded 必须保留旧 Run 的环境清理责任"
        assert next_run.turn_id == current.turn_id and next_run.resume_from_run_id == current.id
        assert next_run.input_payload == current.input_payload
        assert next_run.input_payload["context_snapshot"]["model"] != "test:chat"
        assert (await db.get(AgentInput, follow["input_id"])).status == "pending"
        messages = list(await db.scalars(select(Message).where(Message.run_id == next_run.id).order_by(Message.id)))
        assert [message.content for message in messages] == ["S1", "S2"]
        turn = await db.get(AgentTurn, current.turn_id)
        assert turn.current_run_id == next_run.id and turn.result_run_id is None


async def test_steer_successor_waits_for_durable_runtime_cleanup(sessions, monkeypatch, tmp_path):
    """提前补投不能越过旧环境；清理失败保留责任，恢复后才能领取。"""
    from yuxi.modules.agents.services import run_lifecycle

    current = await _start(sessions)
    await _send(sessions, "steer-after-runtime")
    async with sessions() as db:
        run = await db.get(AgentRun, current.id)
        settled = await settle_run(db=db, run=run, worker_id="owner", status="completed", token_usage=None)
        # 独立构造待清理状态，使领取门禁的负向案例不依赖终态写入实现。
        run.runtime_cleanup_pending = True
        await db.commit()
    successor_id = settled.next_dispatch.run_id
    async with sessions() as db:
        _, acquired = await AgentRunRepository(db).mark_running(successor_id, worker_id="next-owner", lease_seconds=60)
        assert not acquired, "接续 Run 不能先于旧 Run 的清理确认执行"
        await db.commit()

    resource = tmp_path / "old-run-resource"
    resource.write_text("owned")

    def failed_cleanup(*_args):
        """模拟执行节点无法确认资源已释放。"""
        raise RuntimeError("runtime unavailable")

    monkeypatch.setattr(run_lifecycle, "get_sandbox_provider", lambda: SimpleNamespace(cleanup_run=failed_cleanup))
    with pytest.raises(RuntimeError, match="runtime unavailable"):
        await run_lifecycle.complete_run_runtime_cleanup(current)
    async with sessions() as db:
        assert (await db.get(AgentRun, current.id)).runtime_cleanup_pending
        assert (await db.get(AgentRun, successor_id)).status == "pending"
    assert resource.read_text() == "owned"

    def cleanup(uid, run_id):
        """释放指定旧 Run 的资源。"""
        assert (uid, run_id) == (current.uid, current.id)
        resource.unlink(missing_ok=True)

    monkeypatch.setattr(run_lifecycle, "get_sandbox_provider", lambda: SimpleNamespace(cleanup_run=cleanup))
    await run_lifecycle.complete_run_runtime_cleanup(current)
    await run_lifecycle.complete_run_runtime_cleanup(current)
    assert not resource.exists()
    async with sessions() as db:
        assert not (await db.get(AgentRun, current.id)).runtime_cleanup_pending
        successor, acquired = await AgentRunRepository(db).mark_running(successor_id, worker_id="next-owner", lease_seconds=60)
        assert acquired and successor.status == "running"
        await db.commit()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_terminal_failure_preserves_steer_for_explicit_continue(sessions, status):
    """失败或取消保留 Thread 的优先批次，解除暂停后创建新 Turn。"""
    current = await _start(sessions, running=status != "cancelled")
    follow = await _send(sessions, "F1", "follow_up")
    steer = await _send(sessions, "S1")
    async with sessions() as db:
        await SessionRepository(db).lock_session_by_thread_id("input-thread")
        run = await db.get(AgentRun, current.id)
        if status == "failed":
            await settle_run(db=db, run=run, worker_id="owner", status=status, token_usage=None)
        else:
            await turns.cancel_turn(
                db=db,
                scope=SCOPE,
                thread_id="input-thread",
                turn_id=current.turn_id,
                idempotency_key="cancel",
            )
        await db.commit()
    async with sessions() as db:
        agent_session = await SessionRepository(db).get_session_by_thread_id("input-thread")
        assert agent_session.queue_paused
        batch = await db.get(AgentInput, steer["input_id"])
        assert batch.status == "pending" and batch.turn_id is None
        assert await AgentInputRepository(db).list_messages(batch.id) == []
        result = await threads.continue_queue(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="continue")
    async with sessions() as db:
        consumed = await db.get(AgentInput, steer["input_id"])
        assert consumed.consumed_run_id == result["run_id"] and consumed.turn_id != current.turn_id
        assert (await db.get(AgentInput, follow["input_id"])).status == "pending"


async def test_recovery_claims_idle_steer_without_any_follow_up(sessions):
    """崩溃后仅剩 steer 的 ready Thread 也能由恢复扫描领取。"""
    steer = await _send(sessions, "S1")
    async with sessions() as db:
        agent_session = await SessionRepository(db).get_session_by_thread_id("input-thread")
        agent_session.queue_paused = False
        await db.commit()
    await scheduler.recover_pending_dispatches()
    async with sessions() as db:
        batch = await db.get(AgentInput, steer["input_id"])
        assert batch.status == "consumed" and batch.consumed_run_id
        run = await db.get(AgentRun, batch.consumed_run_id)
        assert run.input_payload["context_snapshot"]["model"] == "test:chat"
        assert run.resume_from_run_id is None


@pytest.mark.parametrize("cancel_batch,unready_tail", [(False, False), (True, False), (True, True)])
async def test_steer_before_first_model_is_saved_as_yielded_not_failed(sessions, monkeypatch, cancel_batch, unready_tail):
    """模型前让位允许无输出；提交前取消批次则继续原 Run 而非失败。"""
    from langchain.messages import AIMessage

    from yuxi.modules.agents.repositories.model_audit import ModelMessageAuditRepository
    from yuxi.modules.agents.runtime.middlewares.steer import SteerMiddleware
    from yuxi.shared.datetime import utc_now

    current = await _start(sessions)
    steer = await _send(sessions, "S3")
    context = SimpleNamespace(run_id=current.id)
    jumped = await SteerMiddleware().abefore_model({}, SimpleNamespace(context=context))
    assert jumped == {"jump_to": "end"} and context.steer_before_model
    if unready_tail:
        from yuxi.modules.agents.models.attachments import AgentAttachment
        from yuxi.shared.datetime import utc_now

        tail = await _send(sessions, "S4 preparing")
        async with sessions() as db:
            db.add(
                AgentAttachment(
                    id="unready-tail",
                    uid=SCOPE.uid,
                    app_id=None,
                    filename="tail.txt",
                    mime_type="text/plain",
                    size_bytes=1,
                    expires_at=utc_now(),
                    status="preparing",
                    input_id=tail["input_id"],
                    object_name="tail-source",
                )
            )
            await db.commit()
    if cancel_batch:
        async with sessions() as db:
            await threads.cancel_input(
                db=db,
                scope=SCOPE,
                thread_id="input-thread",
                input_id=steer["input_id"],
                idempotency_key="cancel-S3",
            )

    async def dispatch(target):
        run_id = target.run_id
        """投递时验证接续 Run 已提交，避免用通知冒充消费事实。"""
        async with sessions() as db:
            run = await db.get(AgentRun, run_id)
            assert run.status == "pending" and (await db.get(AgentInput, steer["input_id"])).consumed_run_id == run.id

    monkeypatch.setattr(messages, "deliver", dispatch)
    async with sessions() as db:
        status = await messages.save_messages_from_langgraph_state(
            state=SimpleNamespace(values={"messages": []}),
            thread_id="input-thread",
            db=db,
            run_id=current.id,
            turn_id=current.turn_id,
            worker_id="owner",
            complete_run=True,
            steer_before_model=context.steer_before_model,
        )
    assert status == ("running" if cancel_batch else "yielded")
    async with sessions() as db:
        old = await db.get(AgentRun, current.id)
        turn = await db.get(AgentTurn, current.turn_id)
        batch = await db.get(AgentInput, steer["input_id"])
        assert old.status == status and old.output_message_id is None
        assert turn.status == "running" and turn.result_run_id is None
        if cancel_batch:
            assert turn.current_run_id == old.id and old.worker_id == "owner"
            assert batch.status == "cancelled" and batch.consumed_run_id is None
            assert await db.scalar(select(func.count()).select_from(AgentRun)) == 1
            model = ModelMessageAuditRepository(db)
            await model.start(
                run_id=current.id,
                thread_id="input-thread",
                worker_id="owner",
                operation_id="after-cancel",
                sequence=0,
                started_at=utc_now(),
            )
            await model.finish(
                run_id=current.id,
                thread_id="input-thread",
                worker_id="owner",
                operation_id="after-cancel",
                content="original task done",
                finished_at=utc_now(),
                duration_ms=0,
                usage=None,
            )
            status = await messages.save_messages_from_langgraph_state(
                state=SimpleNamespace(values={"messages": [AIMessage(content="original task done", id="after-cancel")]}),
                thread_id="input-thread",
                db=db,
                run_id=current.id,
                turn_id=current.turn_id,
                worker_id="owner",
                complete_run=True,
            )
            assert status == "completed"
        else:
            assert turn.current_run_id == batch.consumed_run_id and batch.status == "consumed"
        assert not (await SessionRepository(db).get_session_by_thread_id("input-thread")).queue_paused
    if cancel_batch:
        async with sessions() as db:
            turn = await db.get(AgentTurn, current.turn_id)
            old = await db.get(AgentRun, current.id)
            assert turn.result_run_id == old.id and old.status == "completed"
            assert (await db.get(Message, old.output_message_id)).content == "original task done"


async def test_cancelled_steer_continuation_preserves_wire_ids_and_audit_order(sessions, monkeypatch):
    """图段重置 seq 后，真实投影 ID 唯一且 PG 审计仍按原执行顺序返回。"""
    from unittest.mock import AsyncMock

    from langchain.messages import AIMessage

    from test.support.agent_stream_fixtures import native
    from yuxi.modules.agents.input_messages import restore_chat_input_message
    from yuxi.modules.agents.repositories.model_audit import ModelMessageAuditRepository
    from yuxi.modules.agents.runtime.base import GraphExecutionResult
    from yuxi.modules.agents.runtime.context import BaseContext
    from yuxi.modules.agents.runtime.middlewares.steer import SteerMiddleware
    from yuxi.modules.agents.services import execution
    from yuxi.modules.agents.services.tracing import LangfuseRunContext

    current = await _start(sessions)
    steer = await _send(sessions, "wire-steer")
    context = BaseContext(thread_id="input-thread", uid=SCOPE.uid, run_id=current.id, worker_id="owner")
    raw_events, graph_inputs = [], []

    class AgentSource:
        """提供原生模型协议图段；只隔离图源，不替换 recorder 或 adapter。"""

        calls = 0
        history = []

        async def stream_messages_with_state(self, value, **kwargs):
            """第一段让位并取消批次，第二段从零开始原生序号。"""
            self.calls += 1
            graph_inputs.append([message.content for message in value])
            if self.calls == 1:
                self.history = list(value)
            operations = [("model-one", "first", 0), ("model-two", "second", 10)] if self.calls == 1 else [("model-three", "final", 0)]
            for operation, text, seq in operations:
                packets = [
                    {"event": "message-start", "id": operation},
                    {"event": "content-block-delta", "index": 0, "delta": {"type": "text-delta", "text": text}},
                    {"event": "content-block-finish", "index": 0, "content": {"type": "text", "text": text}},
                    {"event": "message-finish"},
                ]
                for index, packet in enumerate(packets):
                    event = native(seq + index, packet)
                    event["params"]["data"] = (packet, {"run_id": operation})
                    raw_events.append(event)
                    yield event
                self.history.append(AIMessage(content=text, id=operation))
            before_model = self.calls == 1
            if before_model:
                jump = await SteerMiddleware().abefore_model({}, SimpleNamespace(context=context))
                assert jump == {"jump_to": "end"} and context.steer_before_model
                async with sessions() as db:
                    await threads.cancel_input(
                        db=db,
                        scope=SCOPE,
                        thread_id="input-thread",
                        input_id=steer["input_id"],
                        idempotency_key="cancel-wire-steer",
                    )
            yield GraphExecutionResult(
                checkpoint=SimpleNamespace(values={"messages": list(self.history)}, tasks=()),
                steer_before_model=before_model,
            )

    source = AgentSource()

    async def resolve(**kwargs):
        """隔离模型发现，Thread、审计与输出持久化仍读取真实 PG。"""
        agent_session = await SessionRepository(kwargs["db"]).get_session_by_thread_id("input-thread")
        return SimpleNamespace(slug="main", name="test", backend_id="ChatbotAgent"), source, context, agent_session

    monkeypatch.setattr(execution, "_resolve_agent_runtime", resolve)
    monkeypatch.setattr(execution, "_build_langfuse_run_context", lambda **kwargs: LangfuseRunContext())
    monkeypatch.setattr(execution, "_flush_langfuse_best_effort", AsyncMock())
    async with sessions() as db:
        projected = await AgentInputRepository(db).list_messages("input-0")
        input_messages = [
            restore_chat_input_message(
                content=message.content,
                image_content=message.image_content,
                metadata={**message.extra_metadata, "message_id": message.id},
            )
            for message in projected
        ]
        events = [
            event
            async for event in execution.stream_agent_execution(
                agent_slug="main",
                input_messages=input_messages,
                thread_id="input-thread",
                meta={
                    "run_id": current.id,
                    "turn_id": current.turn_id,
                    "worker_id": "owner",
                    "input_ids": ["input-0"],
                },
                current_user=SimpleNamespace(uid=SCOPE.uid),
                db=db,
                prepared_execution=SimpleNamespace(context=context),
            )
        ]
    assert events[-1].status == "completed"
    deltas = [event for event in events if isinstance(event, dict) and event["type"] == "agent.session.turn.output_text.delta"]
    assert [event["delta"] for event in deltas] == ["first", "second", "final"]
    assert len({event["event_id"] for event in deltas}) == 3
    assert graph_inputs == [["message-0"], []]
    assert [event["seq"] for event in raw_events] == [0, 1, 2, 3, 10, 11, 12, 13, 0, 1, 2, 3]
    async with sessions() as db:
        audits = await ModelMessageAuditRepository(db).list_for_run(current.id)
        assert [audit.operation_id for audit in audits] == ["model-one", "model-two", "model-three"]
        assert [audit.sequence for audit in audits] == [0, 10, 14]
        turn = await db.get(AgentTurn, current.turn_id)
        run = await db.get(AgentRun, current.id)
        assert turn.result_run_id == run.id and run.status == "completed"
        assert (await db.get(Message, run.output_message_id)).content == "final"
        assert (await db.get(AgentInput, steer["input_id"])).status == "cancelled"


async def test_completion_winning_thread_lock_still_accepts_steer(sessions):
    """完成先提交时，在途 steer 创建新 Turn，旧结果保持不变。"""
    current = await _start(sessions)
    locked, sending = asyncio.Event(), asyncio.Event()

    async def finish():
        """在 steer 竞争锁之前固定旧 Turn 的最终输出。"""
        async with sessions() as db:
            agent_session = await SessionRepository(db).lock_session_by_thread_id("input-thread")
            locked.set()
            await sending.wait()
            run = await db.get(AgentRun, current.id)
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
            await AgentRunRepository(db).set_output_message(run.id, output.id, worker_id="owner")
            settled = await settle_run(db=db, run=run, worker_id="owner", status="completed", token_usage=None)
            assert settled.status == "completed"
            run.runtime_cleanup_pending = False
            await db.commit()
            return output.id

    async def send():
        """发送方不选择目标 Turn，接收事务与完成事务竞争 Thread 锁。"""
        await locked.wait()
        sending.set()
        return await _send(sessions, "late-steer")

    output_id, accepted = await asyncio.wait_for(asyncio.gather(finish(), send()), timeout=15)
    assert accepted["turn_id"] is None and accepted["run_id"] is None
    async with sessions() as db:
        consumed = await inputs.get_input_snapshot(db=db, scope=SCOPE, thread_id="input-thread", input_id=accepted["input_id"])
        assert consumed["turn_id"] != current.turn_id and consumed["run_id"]
        old_turn = await db.get(AgentTurn, current.turn_id)
        old_run = await db.get(AgentRun, current.id)
        assert old_turn.status == "completed" and old_turn.result_run_id == current.id
        assert old_run.output_message_id == output_id
        new_run = await db.get(AgentRun, consumed["run_id"])
        assert new_run.resume_from_run_id is None and new_run.input_payload["context_snapshot"]["model"] == "test:chat"


async def test_concurrent_steer_receipts_share_one_thread_batch(sessions):
    """并发接收由 Thread 锁串行化，消息顺序以持久接收序号为准。"""
    accepted = await asyncio.gather(*(_send(sessions, f"S{index}") for index in range(4)))
    assert len({item["input_id"] for item in accepted}) == 4
    async with sessions() as db:
        receipts = list(await db.scalars(select(AgentInputReceipt).order_by(AgentInputReceipt.receive_seq)))
        pending = list(await db.scalars(select(AgentInput).order_by(AgentInput.received_seq)))
        assert [item.messages[0]["content"][0]["text"] for item in pending] == [receipt.idempotency_key for receipt in receipts]
        assert await db.scalar(select(func.count()).select_from(Message)) == 0
        assert len(pending) == 4
        assert await db.scalar(select(func.count()).select_from(AgentTurn)) == 0


async def test_http_steer_has_no_target_or_effective_mode_and_keeps_scope(sessions):
    """实际 HTTP 适配器持久化优先输入，旧目标字段和越权作用域被拒绝。"""
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(router, prefix="/api/v1/agents")

    async def database():
        """每个 HTTP 请求使用独立 PostgreSQL session。"""
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_public_context] = lambda: SimpleNamespace(scope=SCOPE)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        body = {
            "events": [
                {
                    "type": "agent.session.input.message",
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": "S1"}]}],
                    "yuxi": {"mode": "steer"},
                }
            ]
        }
        url = "/api/v1/agents/sessions/input-thread/events"
        response = await client.post(url, headers={"Idempotency-Key": "http-S1"}, json=body)
        assert response.status_code == 202, response.text
        accepted = response.json()
        assert "mode" not in accepted and accepted["turn_id"] is None
        body["events"][0]["yuxi"]["turn_id"] = "stale-turn"
        assert (await client.post(url, headers={"Idempotency-Key": "http-stale"}, json=body)).status_code == 422
        del body["events"][0]["yuxi"]["turn_id"]
        app.dependency_overrides[require_public_context] = lambda: SimpleNamespace(scope=ActorScope(uid="another-user", app_id=None))
        assert (await client.post(url, headers={"Idempotency-Key": "http-spoof"}, json=body)).status_code == 404
    async with sessions() as db:
        persisted = await db.get(AgentInput, accepted["input_id"])
        assert persisted.kind == "steer" and persisted.status == "consumed" and persisted.turn_id is not None
        assert len(await AgentInputRepository(db).list_messages(persisted.id)) == 1
        assert persisted.messages[0]["content"] == [{"type": "text", "text": "S1"}]
        with pytest.raises(DomainError) as exc:
            await inputs.accept_message(
                db=db,
                scope=SCOPE,
                thread_id="input-thread",
                idempotency_key="http-S1",
                mode="follow_up",
                messages=[build_chat_input_message("S1")],
            )
        assert isinstance(exc.value, StateConflictError)
