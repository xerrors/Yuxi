"""SteerMiddleware 单元测试。"""

from types import SimpleNamespace

import pytest

from yuxi.agents.middlewares.steer import SteerMiddleware
from yuxi.services import agent_request_queue_service

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_before_model_without_run_id_keeps_graph_running(monkeypatch: pytest.MonkeyPatch):
    """缺少主 Run 身份时不访问队列，也不改变 Graph。"""
    called = False

    async def fake_mark_ready(run_id: str) -> bool:
        nonlocal called
        called = True
        return False

    monkeypatch.setattr(agent_request_queue_service, "mark_pending_steer_ready", fake_mark_ready)
    result = await SteerMiddleware().abefore_model({}, SimpleNamespace(context=SimpleNamespace(run_id=None)))

    assert result is None
    assert called is False


async def test_before_model_jumps_to_end_only_when_steer_is_ready(monkeypatch: pytest.MonkeyPatch):
    """服务确认安全点后才返回 end 跳转。"""
    run_ids = []

    async def fake_mark_ready(run_id: str) -> bool:
        run_ids.append(run_id)
        return True

    monkeypatch.setattr(agent_request_queue_service, "mark_pending_steer_ready", fake_mark_ready)
    result = await SteerMiddleware().abefore_model(
        {},
        SimpleNamespace(context=SimpleNamespace(run_id="run-main")),
    )

    assert run_ids == ["run-main"]
    assert result == {"jump_to": "end"}
