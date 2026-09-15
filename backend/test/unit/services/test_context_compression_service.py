from __future__ import annotations

from yuxi.agents.context import BaseContext

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yuxi.services import context_compression_service as service


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
async def test_compress_thread_context_uses_locked_idle_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    events = []
    conversation = SimpleNamespace(
        uid="user-1",
        status="active",
        agent_id="assistant",
        extra_metadata={"model_spec": "provider:model"},
    )
    agent = SimpleNamespace(capabilities=["context_compression"], context_schema=_Context)

    class Db:
        async def commit(self):
            events.append("commit")

    class ConversationRepo:
        def __init__(self, _db):
            pass

        async def lock_conversation_by_thread_id(self, thread_id):
            events.append(("lock", thread_id))
            return conversation

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, **_kwargs):
            return SimpleNamespace(backend_id="ChatbotAgent", config_json={"context": {}})

    async def idle(**_kwargs):
        events.append("idle")

    async def resolve_model(*_args, **_kwargs):
        return "provider:model"

    async def workdir(**_kwargs):
        return "projects/project-1"

    async def runtime(**_kwargs):
        events.append("runtime")

    async def release(**_kwargs):
        events.append("release")

    async def compress(**kwargs):
        events.append(("compress", kwargs["context"].model))
        return {"status": "completed", "after_tokens": 300}

    monkeypatch.setattr(service, "ConversationRepository", ConversationRepo)
    monkeypatch.setattr(service, "AgentRepository", AgentRepo)
    monkeypatch.setattr(service, "_ensure_thread_idle", idle)
    monkeypatch.setattr(service, "resolve_agent_run_model_spec", resolve_model)
    monkeypatch.setattr(service, "ensure_conversation_workdir_available", workdir)
    monkeypatch.setattr(service, "_ensure_runtime_available", runtime)
    monkeypatch.setattr(service, "_release_runtime", release)
    monkeypatch.setattr(service, "_compress_agent_checkpoint", compress)
    monkeypatch.setattr(service.agent_manager, "get_agent", lambda _backend_id: agent)

    result = await service.compress_thread_context(
        thread_id="thread-1",
        current_user=SimpleNamespace(uid="user-1", role="user"),
        db=Db(),
    )

    assert result == {"status": "completed", "after_tokens": 300}
    assert events == [
        ("lock", "thread-1"),
        "idle",
        "runtime",
        ("compress", "provider:model"),
        "release",
        "commit",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runtime_is_released_when_checkpoint_compression_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []

    async def runtime(**_kwargs):
        events.append("runtime")

    async def compress(**_kwargs):
        events.append("compress")
        raise RuntimeError("summary failed")

    async def release(**_kwargs):
        events.append("release")

    monkeypatch.setattr(service, "_ensure_runtime_available", runtime)
    monkeypatch.setattr(service, "_compress_agent_checkpoint", compress)
    monkeypatch.setattr(service, "_release_runtime", release)

    with pytest.raises(RuntimeError, match="summary failed"):
        await service._compress_agent_checkpoint_in_runtime(
            agent=object(),
            context=BaseContext(**{}),
            thread_id="thread-1",
            uid="user-1",
            workdir_path="projects/project-1",
        )

    assert events == ["runtime", "compress", "release"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runtime_is_released_when_provisioning_fails_without_masking_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []

    async def runtime(**_kwargs):
        events.append("runtime")
        raise RuntimeError("provisioning failed")

    async def release(**_kwargs):
        events.append("release")
        raise asyncio.CancelledError("release cancelled")

    monkeypatch.setattr(service, "_ensure_runtime_available", runtime)
    monkeypatch.setattr(service, "_release_runtime", release)

    with pytest.raises(RuntimeError, match="provisioning failed"):
        await service._compress_agent_checkpoint_in_runtime(
            agent=object(),
            context=BaseContext(**{}),
            thread_id="thread-1",
            uid="user-1",
            workdir_path="projects/project-1",
        )

    assert events == ["runtime", "release"]


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
    ("active_run", "latest_run", "queued_requests"),
    [
        (SimpleNamespace(status="running"), None, []),
        (None, SimpleNamespace(status="interrupted"), []),
        (None, None, [SimpleNamespace(status="queued")]),
    ],
)
async def test_rejects_non_idle_thread(active_run, latest_run, queued_requests, monkeypatch) -> None:
    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_active_run_by_thread_for_user(self, **_kwargs):
            return active_run

        async def get_latest_chat_or_resume_run(self, **_kwargs):
            return latest_run

    class RequestRepo:
        def __init__(self, _db):
            pass

        async def list_queued(self, **_kwargs):
            return queued_requests

    monkeypatch.setattr(service, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(service, "AgentRunRequestRepository", RequestRepo)

    with pytest.raises(HTTPException) as exc_info:
        await service._ensure_thread_idle(
            db=object(),
            uid="user-1",
            agent_slug="main",
            thread_id="thread-1",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "thread_busy"
