"""Public Session SSE 的失败传播边界。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.services.agents import session_events
from yuxi.utils.sse_utils import format_sse


@pytest.mark.asyncio
async def test_session_stream_ends_after_run_storage_error(monkeypatch):
    """底层 Run 流暂不可用时关闭上层流，让客户端重连同一游标。"""

    @asynccontextmanager
    async def session():
        """提供一次持久快照读取。"""
        yield object()

    async def user(_db, _uid):
        """返回当前用户。"""
        return SimpleNamespace(uid="user")

    async def turn(**_kwargs):
        """返回尚在运行的一轮。"""
        return {"turn_id": "turn", "run_ids": ["run"], "status": "in_progress"}

    async def run_events(**_kwargs):
        """模拟 Redis 故障事件后终止底层流。"""
        yield format_sse({"reason": "redis_error"}, event="error")

    monkeypatch.setattr(session_events.pg_manager, "get_async_session_context", session)
    monkeypatch.setattr(session_events, "_load_user_by_uid", user)
    monkeypatch.setattr(session_events, "get_public_turn", turn)
    monkeypatch.setattr(session_events, "stream_agent_run_events", run_events)
    stream = session_events.stream_public_session_events(
        thread_id="thread", uid="user", app_id=None, turn_id="turn", after_id="0-0", stream_url="/events"
    )
    events = [event async for event in stream]
    assert any(event.startswith("event: error\n") for event in events)
    assert len(events) == 2


@pytest.mark.asyncio
async def test_waiting_turn_emits_final_status_after_resume(monkeypatch):
    """等待审批的同一 Turn 在续跑终态时再次通知订阅者。"""

    @asynccontextmanager
    async def session():
        """提供逐次持久快照。"""
        yield object()

    statuses = iter(["waiting", "waiting", "in_progress", "in_progress", "completed", "completed"])

    async def turn(**_kwargs):
        """模拟等待、恢复和完成的持久状态。"""
        return {"turn_id": "turn", "run_ids": [], "status": next(statuses)}

    async def user(_db, _uid):
        """返回当前用户。"""
        return SimpleNamespace(uid="user")

    async def no_sleep(_seconds):
        """无时间等待地读取下一次状态。"""
        return None

    monkeypatch.setattr(session_events.pg_manager, "get_async_session_context", session)
    monkeypatch.setattr(session_events, "_load_user_by_uid", user)
    monkeypatch.setattr(session_events, "get_public_turn", turn)
    monkeypatch.setattr(session_events.asyncio, "sleep", no_sleep)
    events = [
        event
        async for event in session_events.stream_public_session_events(
            thread_id="thread", uid="user", app_id=None, turn_id="turn", after_id="0-0", stream_url="/events"
        )
    ]
    assert [event for event in events if event.startswith("event: turn_status\n")] == [
        format_sse({"session_id": "thread", "turn_id": "turn", "status": "waiting"}, event="turn_status"),
        format_sse({"session_id": "thread", "turn_id": "turn", "status": "completed"}, event="turn_status"),
    ]


@pytest.mark.asyncio
async def test_reconnect_after_prior_run_end_starts_with_resume_run(monkeypatch):
    """已确认的旧 Run 终止事件不能在续跑订阅前再次出现。"""

    @asynccontextmanager
    async def session():
        """提供持久 Turn 快照读取。"""
        yield object()

    async def user(_db, _uid):
        """返回当前用户。"""
        return SimpleNamespace(uid="user")

    async def turn(**_kwargs):
        """模拟同一 Turn 的初次执行与续跑。"""
        return {"turn_id": "turn", "run_ids": ["old", "new"], "status": "completed"}

    async def run_events(*, run_id, **_kwargs):
        """旧 Run 若被重订阅则使测试立即失败。"""
        assert run_id == "new"
        yield format_sse({"run_id": run_id}, event="end", event_id="99-0")

    monkeypatch.setattr(session_events.pg_manager, "get_async_session_context", session)
    monkeypatch.setattr(session_events, "_load_user_by_uid", user)
    monkeypatch.setattr(session_events, "get_public_turn", turn)
    monkeypatch.setattr(session_events, "stream_agent_run_events", run_events)
    events = [
        event
        async for event in session_events.stream_public_session_events(
            thread_id="thread", uid="user", app_id=None, turn_id="turn", after_id="old:end", stream_url="/events"
        )
    ]
    assert events[0].startswith("event: run_created\n")
    assert "id: new:0-0\n" in events[0]
    assert events[1] == format_sse({"run_id": "new"}, event="end", event_id="new:end")
    assert session_events._scope_event_id(format_sse({"run_id": "new"}, event="end"), "new") == events[1]
    assert events[2].startswith("event: turn_status\n")
    assert len(events) == 3
