"""真实 PostgreSQL 验证发布故障不改变已接收命令及恢复健康。"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from test.integration.services.agent_input_fixtures import SCOPE, send_input
from yuxi.api.dependencies.auth import get_db
from yuxi.api.errors import domain_error_handler
from yuxi.api.routers.public_v1.agents.auth import require_public_context
from yuxi.api.routers.public_v1.agents.events import router
from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.services import scheduler, threads
from yuxi.modules.agents.services.scheduler import deliver
from yuxi.shared.errors import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]
pytest_plugins = ["test.integration.services.agent_input_fixtures"]


@pytest.fixture
def publication(sessions, monkeypatch):
    """仅在提交后的发布点制造故障，回读证明 Run 已独立可见。"""
    state = SimpleNamespace(failed=True, attempts=[])

    async def enqueue(run_id):
        """每次发布都必须绑定已提交的 pending Run 与当前 Turn。"""
        async with sessions() as db:
            run = await db.get(AgentRun, run_id)
            turn = await db.get(AgentTurn, run.turn_id)
            assert run.status == "pending" and turn.current_run_id == run_id
        state.attempts.append(run_id)
        if state.failed:
            raise ConnectionError("queue unavailable")

    monkeypatch.setattr(scheduler, "deliver", deliver)
    monkeypatch.setattr(threads, "deliver", deliver)
    monkeypatch.setattr("yuxi.modules.agents.services.transport.enqueue_agent_run", enqueue)
    return state


async def test_http_input_publication_failure_keeps_receipt_and_exact_run(sessions, publication):
    """普通 HTTP 输入得到稳定回执；恢复复用同一 Input、Turn、Run。"""
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(router, prefix="/api/v1/agents")

    async def database():
        """请求结束前由真实接收用例提交数据库事实。"""
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_public_context] = lambda: SimpleNamespace(scope=SCOPE)
    payload = {
        "events": [
            {"type": "agent.session.input.message", "input": [{"role": "user", "content": [{"type": "input_text", "text": "WORK"}]}]}
        ]
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/agents/sessions/input-thread/events", headers={"Idempotency-Key": "http-work"}, json=payload)
        assert response.status_code == 202, response.text
        accepted = response.json()
        replay = await client.post("/api/v1/agents/sessions/input-thread/events", headers={"Idempotency-Key": "http-work"}, json=payload)
        assert replay.status_code == 202 and replay.json() == accepted

    async with sessions() as db:
        item = await db.get(AgentInput, accepted["input_id"])
        receipt = await db.get(AgentInputReceipt, accepted["event_id"])
        turn_id, run_id = item.turn_id, item.consumed_run_id
        assert item.status == "consumed" and turn_id and run_id
        assert receipt.input_id == item.id and receipt.run_id is None and receipt.turn_id is None
        assert (await db.get(AgentTurn, turn_id)).current_run_id == run_id
        assert (await db.get(AgentRun, run_id)).status == "pending"

    publication.failed = False
    await scheduler.recover_pending_dispatches()
    async with sessions() as db:
        item = await db.get(AgentInput, accepted["input_id"])
        assert (item.turn_id, item.consumed_run_id) == (turn_id, run_id)
        assert await db.scalar(select(func.count()).select_from(AgentInput)) == 1
        assert await db.scalar(select(func.count()).select_from(AgentInputReceipt)) == 1
        assert await db.scalar(select(func.count()).select_from(AgentTurn)) == 1
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 1
    assert publication.attempts == [run_id, run_id]


async def test_continue_queue_publication_failure_keeps_committed_control_receipt(sessions, publication):
    """继续命令发布失败后仍可重放，暂停解除和执行归属不会重建。"""
    queued = await send_input(sessions, "QUEUED", mode="follow_up")
    async with sessions() as db:
        accepted = await threads.continue_queue(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="continue")
        replay = await threads.continue_queue(db=db, scope=SCOPE, thread_id="input-thread", idempotency_key="continue")
        assert accepted == replay and accepted["run_id"] and accepted["turn_id"]
    async with sessions() as db:
        item = await db.get(AgentInput, queued["input_id"])
        receipt = await db.get(AgentInputReceipt, accepted["event_id"])
        assert (item.turn_id, item.consumed_run_id) == (accepted["turn_id"], accepted["run_id"])
        assert (receipt.turn_id, receipt.run_id) == (item.turn_id, item.consumed_run_id)
        thread = await db.scalar(select(Session).where(Session.thread_id == "input-thread"))
        assert thread.queue_paused is False
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 1
    assert publication.attempts == [accepted["run_id"]]


async def test_recovery_rejects_failed_publication_for_ready_input_and_pending_run(sessions, publication):
    """首次领取和后续补投失败均在当轮阻止恢复健康，并保留同一 Run。"""
    queued = await send_input(sessions, "QUEUED", mode="follow_up")
    async with sessions() as db:
        thread = await db.scalar(select(Session).where(Session.thread_id == "input-thread"))
        thread.queue_paused = False
        await db.commit()

    with pytest.raises(RuntimeError, match="待执行任务补投失败：1 条"):
        await scheduler.recover_pending_dispatches()
    async with sessions() as db:
        item = await db.get(AgentInput, queued["input_id"])
        turn_id, run_id = item.turn_id, item.consumed_run_id
        assert item.status == "consumed" and turn_id and run_id
        assert (await db.get(AgentRun, run_id)).status == "pending"

    with pytest.raises(RuntimeError, match="待执行任务补投失败：1 条"):
        await scheduler.recover_pending_dispatches()
    publication.failed = False
    await scheduler.recover_pending_dispatches()
    async with sessions() as db:
        item = await db.get(AgentInput, queued["input_id"])
        assert (item.turn_id, item.consumed_run_id) == (turn_id, run_id)
        assert (await db.get(AgentTurn, turn_id)).current_run_id == run_id
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 1
    assert publication.attempts == [run_id, run_id, run_id]
