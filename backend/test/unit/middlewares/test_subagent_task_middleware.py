from __future__ import annotations

from types import SimpleNamespace

import pytest
import yuxi.agents.middlewares.subagent_task as subagent_task_middleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from yuxi.agents.buildin.chatbot.state import merge_subagent_runs
from yuxi.agents.middlewares.subagent_task import YuxiSubAgentMiddleware
from yuxi.utils.subagent_thread_utils import make_child_thread_id
from yuxi.repositories.agent_repository import SUB_AGENT_BACKEND_ID


class _ChildContext:
    def __init__(self):
        self.model = None

    def update_from_dict(self, values: dict):
        for key, value in values.items():
            if hasattr(self, key):
                setattr(self, key, value)


def _patch_subagent_run_tracking(monkeypatch):
    async def create_run(_self, **kwargs):
        return SimpleNamespace(
            id=f"sub-run-{kwargs['tool_call_id']}",
            request_id=f"sub-req-{kwargs['tool_call_id']}",
            status="running",
            parent_agent_run_id="parent-run",
            created_at=None,
            finished_at=None,
            error_message=None,
        ), True

    async def set_status(_self, run_id, status, *, error_type=None, error_message=None):
        del error_type
        return SimpleNamespace(
            id=run_id,
            request_id=run_id.replace("sub-run", "sub-req"),
            status=status,
            parent_agent_run_id="parent-run",
            created_at=None,
            finished_at=None,
            error_message=error_message,
        )

    monkeypatch.setattr(YuxiSubAgentMiddleware, "_create_subagent_run", create_run)
    monkeypatch.setattr(YuxiSubAgentMiddleware, "_set_subagent_run_status", set_status)


@pytest.mark.asyncio
async def test_create_task_middleware_loads_all_visible_subagents_when_empty(monkeypatch) -> None:
    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _UserRepository:
        async def get_by_uid_with_db(self, _db, uid):
            assert uid == "user-1"
            return SimpleNamespace(uid="user-1", role="user")

    class _AgentRepository:
        def __init__(self, _db):
            pass

        async def list_visible_subagents(self, *, user):
            assert user.uid == "user-1"
            return [
                SimpleNamespace(
                    slug="worker",
                    name="Worker",
                    description="work on scoped tasks",
                    backend_id=SUB_AGENT_BACKEND_ID,
                    config_json={},
                ),
                SimpleNamespace(
                    slug="invalid",
                    name="Invalid",
                    description="invalid backend",
                    backend_id="ChatbotAgent",
                    config_json={},
                ),
            ]

        async def get_visible_subagent_by_slug(self, *, slug, user):
            raise AssertionError("empty subagents should load all visible subagents")

    monkeypatch.setattr(
        subagent_task_middleware,
        "pg_manager",
        SimpleNamespace(get_async_session_context=lambda: _SessionContext()),
    )
    monkeypatch.setattr(subagent_task_middleware, "UserRepository", _UserRepository)
    monkeypatch.setattr(subagent_task_middleware, "AgentRepository", _AgentRepository)

    middleware = await subagent_task_middleware.create_subagent_task_middleware(
        SimpleNamespace(thread_id="parent-thread", uid="user-1", subagents=[])
    )

    assert isinstance(middleware, YuxiSubAgentMiddleware)
    assert middleware.subagent_names == frozenset({"worker"})
    assert middleware.transformers


@pytest.mark.asyncio
async def test_task_tool_rejects_unconfigured_subagent() -> None:
    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(thread_id="parent-thread", uid="user-1"),
        subagents=[
            SimpleNamespace(
                slug="worker",
                name="Worker",
                description="work on scoped tasks",
                backend_id=SUB_AGENT_BACKEND_ID,
                config_json={},
            )
        ],
    )
    runtime = ToolRuntime(
        state={},
        context=None,
        tool_call_id="tool-1",
        store=None,
        stream_writer=lambda _: None,
        config={},
    )

    result = await middleware.tools[0].ainvoke(
        {"description": "do work", "subagent_type": "missing", "runtime": runtime}
    )

    assert result == "无法调用子智能体 missing，可用子智能体只有：`worker`"


@pytest.mark.asyncio
async def test_task_tool_invokes_subagent_with_child_scope(monkeypatch) -> None:
    _patch_subagent_run_tracking(monkeypatch)
    captured = {}

    class _Graph:
        async def ainvoke(self, state, *, config, context):
            captured["state"] = state
            captured["config"] = config
            captured["context"] = context
            return {
                "messages": [AIMessage(content="child done")],
                "artifacts": ["/home/gem/user-data/outputs/report.md"],
                "todos": ["should not merge"],
            }

    class _Backend:
        context_schema = _ChildContext

        async def get_graph(self, *, context):
            captured["graph_context"] = context
            return _Graph()

    monkeypatch.setattr(
        subagent_task_middleware,
        "_get_agent_backend",
        lambda backend_id: _Backend() if backend_id == SUB_AGENT_BACKEND_ID else None,
    )
    times = iter(["2026-05-31T01:00:00Z", "2026-05-31T01:00:03Z"])
    monkeypatch.setattr(subagent_task_middleware, "utc_isoformat", lambda: next(times))

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(
            thread_id="child-runtime-thread",
            parent_thread_id="parent-thread",
            file_thread_id="parent-file-thread",
            uid="user-1",
        ),
        subagents=[
            SimpleNamespace(
                slug="worker.agent",
                name="Worker",
                description="work on scoped tasks",
                backend_id=SUB_AGENT_BACKEND_ID,
                config_json={"context": {"model": "provider:model", "subagents": ["nested"]}},
            )
        ],
    )
    runtime = SimpleNamespace(
        tool_call_id="tool-1",
        state={
            "messages": [HumanMessage(content="parent")],
            "todos": ["parent todo"],
            "activated_skills": ["parent-skill"],
            "kept": "value",
        },
        config={
            "callbacks": ["stream-callback"],
            "tags": ["parent"],
            "recursion_limit": 42,
            "configurable": {"checkpoint_ns": "parent-ns", "__pregel_task_id": "parent-task"},
        },
    )

    result = await middleware.tools[0].coroutine(
        description="write a report",
        subagent_type="worker.agent",
        runtime=runtime,
    )

    child_thread_id = make_child_thread_id("parent-thread", "worker.agent", "tool-1")
    assert isinstance(result, Command)
    assert result.update["messages"][0].content == f"> 子智能体线程 ID: {child_thread_id}\n\n---\n\nchild done"
    assert result.update["messages"][0].tool_call_id == "tool-1"
    assert result.update["artifacts"] == ["/home/gem/user-data/outputs/report.md"]
    assert result.update["subagent_runs"] == [
        {
            "id": "tool-1",
            "subagent_type": "worker.agent",
            "subagent_name": "Worker",
            "child_thread_id": child_thread_id,
            "description": "write a report",
            "created_at": "2026-05-31T01:00:00Z",
            "run_id": "sub-run-tool-1",
            "parent_agent_run_id": "parent-run",
            "status": "completed",
            "completed_at": "2026-05-31T01:00:03Z",
            "result_preview": "child done",
            "error": None,
            "artifacts": ["/home/gem/user-data/outputs/report.md"],
        }
    ]
    assert "kept" not in captured["state"]
    assert captured["state"]["parent_thread_id"] == "parent-thread"
    assert captured["state"]["file_thread_id"] == "parent-file-thread"
    assert captured["state"]["skills_thread_id"] == child_thread_id
    assert captured["state"]["messages"] == [HumanMessage(content="write a report")]
    assert "todos" not in captured["state"]
    assert "activated_skills" not in captured["state"]
    assert captured["config"]["callbacks"] == ["stream-callback"]
    assert captured["config"]["tags"] == ["parent"]
    assert captured["config"]["recursion_limit"] == 42
    assert captured["config"]["configurable"] == {
        "thread_id": child_thread_id,
        "uid": "user-1",
        "parent_thread_id": "parent-thread",
        "file_thread_id": "parent-file-thread",
        "skills_thread_id": child_thread_id,
        "subagent_type": "worker.agent",
        "subagent_thread_id": child_thread_id,
        "subagent_tool_call_id": "tool-1",
        "run_id": "sub-run-tool-1",
        "request_id": "sub-req-tool-1",
        "ls_agent_type": "subagent",
    }
    assert captured["context"] is captured["graph_context"]
    assert captured["context"].model == "provider:model"
    assert captured["context"].thread_id == child_thread_id
    assert captured["context"].parent_thread_id == "parent-thread"
    assert captured["context"].file_thread_id == "parent-file-thread"
    assert captured["context"].skills_thread_id == child_thread_id
    assert not hasattr(captured["context"], "subagents")
    assert captured["context"].is_subagent_runtime is True


@pytest.mark.asyncio
async def test_task_tool_inherits_parent_model_when_subagent_model_empty(monkeypatch) -> None:
    _patch_subagent_run_tracking(monkeypatch)
    captured = {}

    class _Graph:
        async def ainvoke(self, state, *, config, context):
            del state, config
            captured["context"] = context
            return {"messages": [AIMessage(content="child done")]}

    class _Backend:
        context_schema = _ChildContext

        async def get_graph(self, *, context):
            captured["graph_context"] = context
            return _Graph()

    monkeypatch.setattr(
        subagent_task_middleware,
        "_get_agent_backend",
        lambda backend_id: _Backend() if backend_id == SUB_AGENT_BACKEND_ID else None,
    )

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(thread_id="parent-thread", uid="user-1", model="parent:model"),
        subagents=[
            SimpleNamespace(
                slug="worker",
                name="Worker",
                description="work on scoped tasks",
                backend_id=SUB_AGENT_BACKEND_ID,
                config_json={"context": {"model": ""}},
            )
        ],
    )
    runtime = SimpleNamespace(tool_call_id="tool-1", state={}, config={})

    result = await middleware.tools[0].coroutine(
        description="write a report",
        subagent_type="worker",
        runtime=runtime,
    )

    assert isinstance(result, Command)
    assert captured["context"] is captured["graph_context"]
    assert captured["context"].model == "parent:model"


@pytest.mark.asyncio
async def test_task_tool_records_failed_subagent_run(monkeypatch) -> None:
    _patch_subagent_run_tracking(monkeypatch)

    class _Graph:
        async def ainvoke(self, state, *, config, context):
            del state, config, context
            raise RuntimeError("child boom")

    class _Backend:
        context_schema = _ChildContext

        async def get_graph(self, *, context):
            del context
            return _Graph()

    monkeypatch.setattr(
        subagent_task_middleware,
        "_get_agent_backend",
        lambda backend_id: _Backend() if backend_id == SUB_AGENT_BACKEND_ID else None,
    )
    times = iter(["2026-05-31T02:00:00Z", "2026-05-31T02:00:04Z"])
    monkeypatch.setattr(subagent_task_middleware, "utc_isoformat", lambda: next(times))

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(thread_id="parent-thread", uid="user-1"),
        subagents=[
            SimpleNamespace(
                slug="worker",
                name="Worker",
                description="work on scoped tasks",
                backend_id=SUB_AGENT_BACKEND_ID,
                config_json={},
            )
        ],
    )
    runtime = SimpleNamespace(tool_call_id="tool-1", state={}, config={})

    result = await middleware.tools[0].coroutine(
        description="write a report",
        subagent_type="worker",
        runtime=runtime,
    )

    assert isinstance(result, Command)
    child_thread_id = make_child_thread_id("parent-thread", "worker", "tool-1")
    assert (
        result.update["messages"][0].content
        == f"> 子智能体线程 ID: {child_thread_id}\n\n---\n\n子智能体 worker 调用失败：child boom"
    )
    assert result.update["subagent_runs"] == [
        {
            "id": "tool-1",
            "subagent_type": "worker",
            "subagent_name": "Worker",
            "child_thread_id": child_thread_id,
            "description": "write a report",
            "created_at": "2026-05-31T02:00:00Z",
            "run_id": "sub-run-tool-1",
            "parent_agent_run_id": "parent-run",
            "status": "failed",
            "completed_at": "2026-05-31T02:00:04Z",
            "result_preview": "子智能体 worker 调用失败：child boom",
            "error": "child boom",
            "artifacts": [],
        }
    ]


@pytest.mark.asyncio
async def test_task_tool_continues_existing_subagent_thread(monkeypatch) -> None:
    _patch_subagent_run_tracking(monkeypatch)
    captured = {}

    class _Graph:
        async def ainvoke(self, state, *, config, context):
            captured["state"] = state
            captured["config"] = config
            captured["context"] = context
            return {"messages": [AIMessage(content="continued done")]}

    class _Backend:
        context_schema = _ChildContext

        async def get_graph(self, *, context):
            captured["graph_context"] = context
            return _Graph()

    monkeypatch.setattr(
        subagent_task_middleware,
        "_get_agent_backend",
        lambda backend_id: _Backend() if backend_id == SUB_AGENT_BACKEND_ID else None,
    )
    times = iter(["2026-05-31T03:00:00Z", "2026-05-31T03:00:05Z"])
    monkeypatch.setattr(subagent_task_middleware, "utc_isoformat", lambda: next(times))

    child_thread_id = make_child_thread_id("parent-thread", "worker.agent", "tool-old")
    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(thread_id="parent-thread", uid="user-1"),
        subagents=[
            SimpleNamespace(
                slug="worker.agent",
                name="Worker",
                description="work on scoped tasks",
                backend_id=SUB_AGENT_BACKEND_ID,
                config_json={},
            )
        ],
    )
    runtime = SimpleNamespace(
        tool_call_id="tool-2",
        state={
            "subagent_runs": [
                {
                    "id": "tool-old",
                    "subagent_type": "worker.agent",
                    "child_thread_id": child_thread_id,
                    "status": "completed",
                }
            ],
            "kept": "parent-value",
        },
        config={"configurable": {"checkpoint_ns": "parent-ns"}},
    )

    result = await middleware.tools[0].coroutine(
        description="continue the report",
        subagent_type="worker.agent",
        runtime=runtime,
        thread_id=child_thread_id,
    )

    assert isinstance(result, Command)
    assert result.update["messages"][0].content == f"> 子智能体线程 ID: {child_thread_id}\n\n---\n\ncontinued done"
    assert result.update["subagent_runs"] == [
        {
            "id": "tool-2",
            "subagent_type": "worker.agent",
            "subagent_name": "Worker",
            "child_thread_id": child_thread_id,
            "description": "continue the report",
            "created_at": "2026-05-31T03:00:00Z",
            "run_id": "sub-run-tool-2",
            "parent_agent_run_id": "parent-run",
            "status": "completed",
            "completed_at": "2026-05-31T03:00:05Z",
            "result_preview": "continued done",
            "error": None,
            "artifacts": [],
        }
    ]
    assert captured["state"] == {
        "parent_thread_id": "parent-thread",
        "file_thread_id": "parent-thread",
        "skills_thread_id": child_thread_id,
        "messages": [HumanMessage(content="continue the report")],
    }
    assert captured["config"]["configurable"] == {
        "thread_id": child_thread_id,
        "uid": "user-1",
        "parent_thread_id": "parent-thread",
        "file_thread_id": "parent-thread",
        "skills_thread_id": child_thread_id,
        "subagent_type": "worker.agent",
        "subagent_thread_id": child_thread_id,
        "subagent_tool_call_id": "tool-2",
        "run_id": "sub-run-tool-2",
        "request_id": "sub-req-tool-2",
        "ls_agent_type": "subagent",
    }
    assert captured["context"] is captured["graph_context"]
    assert captured["context"].thread_id == child_thread_id


@pytest.mark.asyncio
async def test_task_tool_rejects_invalid_continuation_thread(monkeypatch) -> None:
    graph_called = False

    class _Backend:
        context_schema = _ChildContext

        async def get_graph(self, *, context):
            nonlocal graph_called
            del context
            graph_called = True
            raise AssertionError("invalid continuation should not invoke graph")

    async def reject_continuation(_self, **kwargs):
        raise ValueError(f"无法继续子智能体线程 {kwargs['child_thread_id']}：当前对话中没有找到对应的子智能体运行记录")

    monkeypatch.setattr(
        subagent_task_middleware,
        "_get_agent_backend",
        lambda backend_id: _Backend() if backend_id == SUB_AGENT_BACKEND_ID else None,
    )
    monkeypatch.setattr(YuxiSubAgentMiddleware, "_create_subagent_run", reject_continuation)

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(thread_id="parent-thread", uid="user-1", run_id="parent-run"),
        subagents=[
            SimpleNamespace(
                slug="worker",
                name="Worker",
                description="work on scoped tasks",
                backend_id=SUB_AGENT_BACKEND_ID,
                config_json={},
            )
        ],
    )

    unknown_thread_id = "opaque-child-thread"
    runtime = SimpleNamespace(tool_call_id="tool-2", state={}, config={})
    result = await middleware.tools[0].coroutine(
        description="continue",
        subagent_type="worker",
        runtime=runtime,
        thread_id=unknown_thread_id,
    )

    assert result == f"无法继续子智能体线程 {unknown_thread_id}：当前对话中没有找到对应的子智能体运行记录"
    assert graph_called is False


def test_make_child_thread_id_fits_agent_run_thread_column() -> None:
    child_thread_id = make_child_thread_id(
        "fa62c751-d124-476f-a58c-855890aebcc4",
        "agent-with-a-very-long-slug-that-would-overflow-the-column",
        "019e86570b418b4ea6b5aee3ef87b1fa",
    )

    assert len(child_thread_id) <= 64
    assert child_thread_id == make_child_thread_id(
        "fa62c751-d124-476f-a58c-855890aebcc4",
        "agent-with-a-very-long-slug-that-would-overflow-the-column",
        "019e86570b418b4ea6b5aee3ef87b1fa",
    )


def test_merge_subagent_runs_reuses_child_thread_entry() -> None:
    child_thread_id = make_child_thread_id("parent-thread", "worker", "tool-old")

    merged = merge_subagent_runs(
        [
            {
                "id": "tool-old",
                "subagent_type": "worker",
                "subagent_name": "Worker",
                "child_thread_id": child_thread_id,
                "description": "first task",
                "status": "completed",
                "created_at": "2026-05-31T01:00:00Z",
            }
        ],
        [
            {
                "id": "tool-new",
                "subagent_type": "worker",
                "subagent_name": "Worker",
                "child_thread_id": child_thread_id,
                "description": "continue task",
                "status": "completed",
                "created_at": "2026-05-31T02:00:00Z",
            }
        ],
    )

    assert merged == [
        {
            "id": "tool-new",
            "subagent_type": "worker",
            "subagent_name": "Worker",
            "child_thread_id": child_thread_id,
            "description": "continue task",
            "status": "completed",
            "created_at": "2026-05-31T02:00:00Z",
        }
    ]
