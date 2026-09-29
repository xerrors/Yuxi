"""Public Session 状态投影不混用 Turn 终态。"""

from types import SimpleNamespace

import pytest

from yuxi.services.agents import public_api


class EmptyTurnRepository:
    """模拟尚未建立 Turn 的历史线程。"""

    def __init__(self, _db):
        pass

    async def get_latest_for_thread(self, **_kwargs):
        """历史线程没有 Turn 身份。"""
        return None


class EmptyRequestRepository:
    """模拟没有持久排队输入的历史线程。"""

    def __init__(self, _db):
        pass

    async def get_first_queued(self, **_kwargs):
        """返回空队列。"""
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_status", "run_status", "queued", "expected"),
    [
        ("queued", None, False, "in_progress"),
        ("queued", "interrupted", False, "requires_action"),
        ("waiting", "completed", False, "idle"),
        ("cancelled", "running", False, "in_progress"),
        ("cancel_requested", "cancel_requested", False, "in_progress"),
        ("cancelled", "failed", True, "in_progress"),
        ("completed", "completed", False, "idle"),
        ("failed", "failed", False, "idle"),
    ],
)
async def test_session_status_uses_current_run_and_request(monkeypatch, request_status, run_status, queued, expected):
    """排队请求不能遮蔽等待审批，Turn 终态不能终止 Session。"""

    async def get_thread(**_kwargs):
        return {"thread_id": "thread", "agent_id": "agent", "status": request_status, "request_id": "request"}

    class FakeRunRepository:
        """提供当前顶层 Run 的最小持久读数。"""

        def __init__(self, _db):
            pass

        async def get_latest_chat_or_resume_run(self, **_kwargs):
            return SimpleNamespace(status=run_status, turn_id=None) if run_status else None

        async def get_active_run_by_thread_for_user(self, **_kwargs):
            return None

    class FakeRequestRepository:
        """提供线程内未派发 Request 的存在性。"""

        def __init__(self, _db):
            pass

        async def has_queued_for_thread(self, **_kwargs):
            return queued

        async def get_first_queued(self, **_kwargs):
            return None

    monkeypatch.setattr(public_api, "get_public_thread", get_thread)
    monkeypatch.setattr(public_api, "AgentRunRepository", FakeRunRepository)
    monkeypatch.setattr(public_api, "AgentRunRequestRepository", FakeRequestRepository)
    monkeypatch.setattr(public_api, "AgentTurnRepository", EmptyTurnRepository)
    session = await public_api.get_public_session(
        thread_id="thread", user=SimpleNamespace(uid="user"), app_id="app", db=object()
    )
    assert session["status"] == expected


@pytest.mark.asyncio
async def test_session_status_rejects_unknown_request_state(monkeypatch):
    """新增 Request 状态必须显式确定 Session 语义。"""

    async def get_thread(**_kwargs):
        return {"thread_id": "thread", "agent_id": "agent", "status": "unexpected"}

    class FakeRunRepository:
        """模拟无顶层 Run 的读取。"""

        def __init__(self, _db):
            pass

        async def get_latest_chat_or_resume_run(self, **_kwargs):
            return None

        async def get_active_run_by_thread_for_user(self, **_kwargs):
            return None

    monkeypatch.setattr(public_api, "get_public_thread", get_thread)
    monkeypatch.setattr(public_api, "AgentRunRepository", FakeRunRepository)
    monkeypatch.setattr(public_api, "AgentTurnRepository", EmptyTurnRepository)
    monkeypatch.setattr(public_api, "AgentRunRequestRepository", EmptyRequestRepository)
    with pytest.raises(ValueError, match="未知的 Public Request 状态"):
        await public_api.get_public_session(
            thread_id="thread", user=SimpleNamespace(uid="user"), app_id="app", db=object()
        )
