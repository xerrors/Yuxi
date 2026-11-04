from __future__ import annotations

from yuxi.modules.agents.services import thread_access
from types import SimpleNamespace

import pytest

import yuxi.modules.agents.services.compression as service
from yuxi.modules.agents.runtime.context import BaseContext
from yuxi.shared.errors import DomainError, ResourceNotFoundError, StateConflictError

_Context = BaseContext


@pytest.fixture(autouse=True)
def prepared_context(monkeypatch):
    """隔离资源准备，压缩测试只验证事务与checkpoint更新。"""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(service, "prepare_agent_runtime_context", AsyncMock())


class _Graph:
    def __init__(self, values):
        self.checkpointer = object()
        self.values = values
        self.writes = []

    async def aget_state(self, _config):
        return SimpleNamespace(values=self.values)

    async def aupdate_state(self, config, values):
        self.writes.append((config, values))


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", ["thread-1", "child-thread"])
@pytest.mark.parametrize("compression_fails", [False, True])
@pytest.mark.parametrize(
    "config_snapshot",
    [
        None,
        {"model": "provider:model", "tool_approval_mode": "default"},
        {"model": "provider:model", "tool_approval_mode": "always_trust", "summary_prompt": "FROZEN_SUMMARY"},
    ],
)
async def test_compress_thread_context_uses_locked_idle_thread(
    monkeypatch: pytest.MonkeyPatch, thread_id: str, compression_fails: bool, config_snapshot: dict | None
) -> None:
    """空快照与摘要配置保持冻结，压缩只操作已锁定的空闲会话。"""
    events = []
    agent_session = SimpleNamespace(
        uid="user-1",
        app_id=None,
        status="active",
        agent_id="assistant",
        config_snapshot=config_snapshot,
        tree_root_thread_id="thread-1",
        extra_metadata={},
    )
    agent = SimpleNamespace(context_schema=_Context)

    class Db:
        async def commit(self):
            events.append("commit")

    class SessionRepo:
        def __init__(self, _db):
            pass

        async def lock_session_by_thread_id(self, thread_id):
            events.append(("lock", thread_id))
            return agent_session

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(backend_id="ChatbotAgent", config_json={"context": {"summary_prompt": "CHANGED_SUMMARY"}})

    async def idle(**_kwargs):
        events.append("idle")

    async def workdir(**_kwargs):
        return "projects/project-1"

    async def prepare(context):
        """记录准备顺序并核对已装配的线程目录。"""
        assert context.thread_id == thread_id
        assert context.workdir_relative_path == "projects/project-1"
        events.append("prepare")

    async def compress(**kwargs):
        assert kwargs["context"].runtime_scope_id is None
        assert kwargs["context"].thread_id == thread_id
        expected_prompt = "CHANGED_SUMMARY"
        if config_snapshot is not None:
            expected_prompt = config_snapshot.get("summary_prompt", _Context().summary_prompt)
        assert kwargs["context"].summary_prompt == expected_prompt
        assert kwargs["context"].tool_approval_mode == config_snapshot["tool_approval_mode"]
        events.append(("compress", kwargs["context"].model))
        if compression_fails:
            raise RuntimeError("compression failed")
        return {"status": "completed", "after_tokens": 300}

    monkeypatch.setattr(thread_access, "SessionRepository", SessionRepo)
    monkeypatch.setattr(service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(service, "_ensure_thread_idle", idle)
    monkeypatch.setattr(service, "ensure_session_workdir_available", workdir)
    monkeypatch.setattr(service, "prepare_agent_runtime_context", prepare)
    monkeypatch.setattr(service, "_compress_agent_checkpoint", compress)
    monkeypatch.setattr(service, "get_agent_backend", lambda _backend_id: agent)

    if config_snapshot is None:
        with pytest.raises(ValueError, match="Session 缺少配置快照"):
            await service.compress_thread_context(thread_id=thread_id, current_user=SimpleNamespace(uid="user-1", role="user"), db=Db())
        assert events == [("lock", thread_id), "idle"]
        return

    if compression_fails:
        with pytest.raises(RuntimeError, match="compression failed"):
            await service.compress_thread_context(
                thread_id=thread_id,
                current_user=SimpleNamespace(uid="user-1", role="user"),
                db=Db(),
            )
        assert events == [("lock", thread_id), "idle", "prepare", ("compress", "provider:model")]
        return

    result = await service.compress_thread_context(
        thread_id=thread_id,
        current_user=SimpleNamespace(uid="user-1", role="user"),
        db=Db(),
    )

    assert result == {"status": "completed", "after_tokens": 300}
    assert events == [
        ("lock", thread_id),
        "idle",
        "prepare",
        ("compress", "provider:model"),
        "commit",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compress_rejects_same_user_from_other_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """压缩副作用在 service 锁内重新核对 APP。"""

    class SessionRepo:
        def __init__(self, _db):
            pass

        async def lock_session_by_thread_id(self, _thread_id):
            return SimpleNamespace(uid="user-1", app_id="app-a", status="active")

    monkeypatch.setattr(thread_access, "SessionRepository", SessionRepo)
    with pytest.raises(DomainError) as exc:
        await service.compress_thread_context(thread_id="thread-1", current_user=SimpleNamespace(uid="user-1"), db=object(), app_id="app-b")
    assert isinstance(exc.value, ResourceNotFoundError)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compresses_checkpoint_through_canonical_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _Graph(
        {
            "messages": ["old"],
            "token_usage": {
                "system_tokens": 100,
                "tools_tokens": 20,
                "summary_trigger_tokens": 1000,
                "thread": {"total": {"total_tokens": 500}},
            },
        }
    )

    class Agent:
        context_schema = _Context

        async def get_graph(self, *, context):
            assert context.uid == "user-1"
            assert context.thread_id == "thread-1"
            return graph

    class Compressor:
        async def aforce_summarize(self, values):
            assert values["messages"] == ["old"]
            return {"_summarization_event": {"cutoff_index": 1}}, {
                "status": "completed",
                "after_tokens": 300,
            }

    monkeypatch.setattr(service, "create_agent_composite_backend", lambda _context: object())
    monkeypatch.setattr(
        service,
        "create_summary_middleware_from_context",
        lambda _context, *, backend: Compressor(),
    )

    result = await service._compress_agent_checkpoint(
        agent=Agent(),
        context=BaseContext(**{"uid": "user-1", "thread_id": "thread-1", "summary_threshold": 2}),
    )

    assert result == {"status": "completed", "after_tokens": 300}
    assert graph.writes == [
        (
            {"configurable": {"uid": "user-1", "thread_id": "thread-1"}},
            {
                "_summarization_event": {"cutoff_index": 1},
                "token_usage": {
                    "thread": {"total": {"total_tokens": 500}},
                    "compression": {"status": "completed", "after_tokens": 300},
                    "summary_active": True,
                    "summary_trigger_tokens": 2048,
                },
            },
        )
    ]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active_turn", "pending_input"),
    [
        ("turn-running", None),
        ("turn-waiting", None),
        (None, "input-pending"),
    ],
)
async def test_rejects_non_idle_thread(active_turn, pending_input) -> None:
    class Db:
        def __init__(self):
            self.results = [active_turn, pending_input]

        async def scalar(self, _statement):
            return self.results.pop(0)

    with pytest.raises(DomainError) as exc_info:
        await service._ensure_thread_idle(
            db=Db(),
            thread_id="thread-1",
        )

    assert isinstance(exc_info.value, StateConflictError)
    assert exc_info.value.code == "thread_busy"
