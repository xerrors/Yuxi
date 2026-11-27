"""恢复扫描必须尊重子 Run 的持久执行树状态。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import yuxi.modules.agents.services.scheduler as scheduler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parent_status", "current_status", "expected_delivery", "expected_terminal"),
    [
        ("running", "pending", ["child-run"], []),
        ("completed", "pending", ["child-run"], []),
        ("running", "cancelled", [], []),
    ],
)
async def test_recovery_republishes_owned_child_after_parent_completion(
    monkeypatch, parent_status, current_status, expected_delivery, expected_terminal
):
    """崩溃遗留子 Run 可补投；父树取消和扫描后的取消均不能投递。"""
    candidate = SimpleNamespace(
        id="child-run",
        uid="user-1",
        app_id=None,
        status="pending",
        run_type="chat",
        thread_id="child-thread",
        runtime_scope_id="child-thread",
        agent_slug="child",
        turn_id="turn-1",
        created_by_run_id="parent-run",
    )
    current = SimpleNamespace(**{**vars(candidate), "status": current_status})
    parent = SimpleNamespace(
        id="parent-run",
        uid="user-1",
        app_id=None,
        status=parent_status,
        run_type="chat",
        session_record_id=1,
        thread_id="root-thread",
        turn_id="turn-1",
        runtime_scope_id="root-thread",
    )
    root = SimpleNamespace(id=1, thread_id="root-thread", uid="user-1", app_id=None, status="active")
    child_thread = SimpleNamespace(thread_id="child-thread", uid="user-1", app_id=None, agent_id="child", status="active")
    delivered = []
    terminal = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self.rows

        def all(self):
            return self.rows

    class Session:
        def __init__(self):
            self.queries = 0

        async def execute(self, statement):
            self.queries += 1
            if self.queries == 1:
                return Result([candidate])
            return Result([])

    @asynccontextmanager
    async def session_context():
        yield Session()

    class RunRepo:
        def __init__(self, db):
            pass

        async def get_run(self, run_id):
            return current

        async def lock_run_for_user(self, run_id, uid):
            return parent if run_id == "parent-run" else current

        async def set_terminal_status(self, run_id, *, status, **kwargs):
            terminal.append((run_id, status))
            current.status = status

    class ConvRepo:
        def __init__(self, db):
            pass

        async def lock_session_by_thread_id(self, thread_id):
            return root

        async def get_session_by_thread_id(self, thread_id):
            return child_thread

    class TurnRepo:
        def __init__(self, db):
            pass

        async def get_for_scope(self, **kwargs):
            return SimpleNamespace(status="running", current_run_id="child-run")

    async def deliver(run_id):
        delivered.append(run_id)
        return True

    monkeypatch.setattr(scheduler.pg_manager, "get_async_session_context", session_context)
    monkeypatch.setattr(scheduler, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(scheduler, "SessionRepository", ConvRepo)
    monkeypatch.setattr(scheduler, "AgentTurnRepository", TurnRepo)
    monkeypatch.setattr(scheduler, "deliver", deliver)

    await scheduler.recover_pending_dispatches()

    assert delivered == expected_delivery
    assert terminal == expected_terminal


@pytest.mark.asyncio
async def test_delivery_reports_queue_publication_failure_without_rejecting_committed_command(monkeypatch):
    """队列不可达只返回发布失败，不能覆盖命令已经提交的事实。"""
    cause = ConnectionError("queue unavailable")

    async def enqueue(_run_id):
        """在实际发布边界注入队列不可达。"""
        raise cause

    monkeypatch.setattr("yuxi.modules.agents.services.transport.enqueue_agent_run", enqueue)
    assert await scheduler.deliver("pending-run") is False


@pytest.mark.asyncio
async def test_delivery_reports_success_after_publication(monkeypatch):
    """发布成功结果供恢复扫描确认本轮发布没有失败。"""
    published = []

    async def enqueue(run_id):
        """记录实际发布目标。"""
        published.append(run_id)

    monkeypatch.setattr("yuxi.modules.agents.services.transport.enqueue_agent_run", enqueue)
    assert await scheduler.deliver("pending-run") is True
    assert published == ["pending-run"]
