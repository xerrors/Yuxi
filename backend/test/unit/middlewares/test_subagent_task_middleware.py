from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import yuxi.agents.middlewares.subagent_task as subagent_task_middleware
import yuxi.services.agent_run_service as agent_run_service
import yuxi.services.subagent_run_service as subagent_run_service
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from yuxi.agents.middlewares.subagent_task import YuxiSubAgentMiddleware
from yuxi.repositories.agent_repository import SUB_AGENT_BACKEND_ID
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.utils.hash_utils import subagent_child_thread_id


def make_child_thread_id(parent_thread_id: str, agent_slug: str, tool_call_id: str) -> str:
    return subagent_child_thread_id(parent_thread_id, agent_slug, tool_call_id)


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _patch_session(monkeypatch):
    monkeypatch.setattr(
        subagent_task_middleware,
        "pg_manager",
        SimpleNamespace(get_async_session_context=lambda: _SessionContext()),
    )


def _patch_subagent_run_service(monkeypatch, service_class) -> None:
    monkeypatch.setattr(
        subagent_task_middleware,
        "_subagent_run_service_module",
        lambda: SimpleNamespace(
            SubagentRunService=service_class,
            SubagentRunBusy=subagent_run_service.SubagentRunBusy,
            serialize_subagent_run_state=subagent_run_service.serialize_subagent_run_state,
            subagent_run_urls=subagent_run_service.subagent_run_urls,
        ),
    )


def _async_tool_middleware(*, model: str | None = None) -> YuxiSubAgentMiddleware:
    parent_context = SimpleNamespace(
        thread_id="parent-thread",
        runtime_scope_id="parent-thread",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        uid="user-1",
        run_id="parent-run",
    )
    if model:
        parent_context.model = model
    return YuxiSubAgentMiddleware(
        parent_context=parent_context,
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


async def test_fixed_tool_schemas_reused_without_sharing_parent_run(monkeypatch):
    """重复构造不再推导固定参数，实际工具闭包仍使用各自父 Run。"""
    import langchain_core.tools.structured as structured

    first = _async_tool_middleware()
    first.parent_context.run_id = "first-parent"
    first_schemas = {tool.name: tool.tool_call_schema.model_json_schema() for tool in first.tools}

    def reject_inference(*args, **kwargs):
        raise AssertionError("固定的子智能体工具 Schema 不应逐 Run 重新推导")

    monkeypatch.setattr(structured, "create_schema_from_function", reject_inference)
    second = _async_tool_middleware()
    second.parent_context.run_id = "second-parent"

    _patch_session(monkeypatch)

    class CaptureParentService:
        def __init__(self, db):
            pass

        async def start(self, **kwargs):
            raise ValueError(kwargs["created_by_run_id"])

    _patch_subagent_run_service(monkeypatch, CaptureParentService)
    for left, right in zip(first.tools, second.tools, strict=True):
        assert left is not right
        assert left.args_schema is right.args_schema
        assert right.tool_call_schema.model_json_schema() == first_schemas[right.name]
        assert "runtime" not in first_schemas[right.name]["properties"]

    assert (
        await first.tools[0].coroutine(
            description="test", subagent_slug="worker", runtime=SimpleNamespace(tool_call_id="test-call")
        )
        == "first-parent"
    )
    assert (
        await second.tools[0].coroutine(
            description="test", subagent_slug="worker", runtime=SimpleNamespace(tool_call_id="test-call")
        )
        == "second-parent"
    )


def _subagent_run(
    *,
    status: str = "running",
    thread_id: str = "child-thread",
    tool_call_id: str = "tool-async",
    subagent_slug: str = "worker",
    subagent_name: str = "Worker",
    description: str = "run in background",
    error_message: str | None = None,
):
    return SimpleNamespace(
        id="child-run",
        conversation_thread_id=thread_id,
        agent_slug=subagent_slug,
        status=status,
        created_by_run_id="parent-run",
        subagent_thread_relation_id=77,
        input_payload={
            "runtime": {
                "tool_call_id": tool_call_id,
                "subagent_name": subagent_name,
                "description": description,
            },
        },
        created_at=None,
        finished_at=None,
        error_message=error_message,
    )


def _patch_start(monkeypatch, captured: dict, *, thread_id: str = "child-thread"):
    """模拟已提交的派发结果，并拒绝 start 隐式等待子任务。"""

    class Service:
        def __init__(self, db):
            captured["db"] = db

        async def start(self, **kwargs):
            captured["start"] = kwargs
            return SimpleNamespace(
                run=_subagent_run(status="pending", thread_id=thread_id, tool_call_id=kwargs["tool_call_id"]),
                created=True,
                continuing=bool(kwargs.get("requested_thread_id")),
                relation=SimpleNamespace(id=77, child_thread_id=thread_id),
            )

    async def reject_wait(**kwargs):
        pytest.fail("subagent_start 不得等待子 Run")

    _patch_session(monkeypatch)
    _patch_subagent_run_service(monkeypatch, Service)
    monkeypatch.setattr(agent_run_service, "await_agent_run_result", reject_wait)


@pytest.mark.asyncio
async def test_create_task_middleware_loads_all_visible_subagents_when_empty(monkeypatch) -> None:
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
            ]

        async def get_visible_by_slug(self, *, slug, user, kind="main"):
            del slug
            del user
            del kind
            raise AssertionError("empty subagents should load all visible subagents")

    _patch_session(monkeypatch)
    monkeypatch.setattr(subagent_task_middleware, "UserRepository", _UserRepository)
    monkeypatch.setattr(subagent_task_middleware, "AgentRepository", _AgentRepository)

    middleware = await subagent_task_middleware.create_subagent_task_middleware(
        SimpleNamespace(thread_id="parent-thread", uid="user-1", subagents=[]),
    )

    assert isinstance(middleware, YuxiSubAgentMiddleware)
    assert {tool.name for tool in middleware.tools} == {
        "subagent_start",
        "subagent_status",
        "subagent_cancel",
        "subagent_await",
    }


@pytest.mark.asyncio
async def test_subagent_start_rejects_unconfigured_subagent() -> None:
    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(thread_id="parent-thread", uid="user-1", model=""),
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
        {"description": "do work", "subagent_slug": "missing", "runtime": runtime}
    )

    assert result == "无法调用子智能体 missing，可用子智能体只有：`worker`"


@pytest.mark.asyncio
async def test_subagent_start_invokes_subagent_with_child_scope(monkeypatch) -> None:
    captured = {}
    _patch_start(monkeypatch, captured, thread_id="child-thread")

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(
            thread_id="child-runtime-thread",
            runtime_scope_id="parent-thread",
            workdir_path="projects/11111111-1111-4111-8111-111111111111",
            uid="user-1",
            run_id="parent-run",
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
    runtime = SimpleNamespace(tool_call_id="tool-1", state={}, config={})

    result = await middleware.tools[0].coroutine(
        description="write a report",
        subagent_slug="worker.agent",
        runtime=runtime,
    )

    assert isinstance(result, Command)
    assert json.loads(result.update["messages"][0].content)["run_id"] == "child-run"
    assert result.update["messages"][0].tool_call_id == "tool-1"
    assert captured["start"]["uid"] == "user-1"
    assert captured["start"]["created_by_run_id"] == "parent-run"
    assert captured["start"]["requested_thread_id"] is None
    assert "model_spec" not in captured["start"]
    assert "await" not in captured
    assert result.update["subagent_runs"][0]["run_id"] == "child-run"
    assert result.update["subagent_runs"][0]["child_thread_id"] == "child-thread"
    assert result.update["subagent_runs"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_subagent_start_leaves_model_resolution_to_service(monkeypatch) -> None:
    captured = {}
    _patch_start(monkeypatch, captured)

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(
            thread_id="parent-thread",
            runtime_scope_id="parent-thread",
            workdir_path="projects/11111111-1111-4111-8111-111111111111",
            uid="user-1",
            run_id="parent-run",
            model="parent:model",
        ),
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

    await middleware.tools[0].coroutine(
        description="write a report",
        subagent_slug="worker",
        runtime=SimpleNamespace(tool_call_id="tool-1", state={}, config={}),
    )

    assert "model_spec" not in captured["start"]


@pytest.mark.asyncio
async def test_subagent_start_continues_existing_subagent_thread(monkeypatch) -> None:
    captured = {}
    _patch_start(monkeypatch, captured, thread_id="child-thread")

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(
            thread_id="parent-thread",
            runtime_scope_id="parent-thread",
            workdir_path="projects/11111111-1111-4111-8111-111111111111",
            uid="user-1",
            run_id="parent-run",
            model="",
        ),
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

    result = await middleware.tools[0].coroutine(
        description="continue the report",
        subagent_slug="worker.agent",
        runtime=SimpleNamespace(tool_call_id="tool-2", state={}, config={}),
        thread_id="child-thread",
    )

    assert isinstance(result, Command)
    assert json.loads(result.update["messages"][0].content)["continuing"] is True
    assert captured["start"]["requested_thread_id"] == "child-thread"
    assert result.update["subagent_runs"][0]["child_thread_id"] == "child-thread"


@pytest.mark.asyncio
async def test_subagent_start_rejects_invalid_continuation_thread(monkeypatch) -> None:
    class _SubagentRunService:
        def __init__(self, db):
            del db

        async def start(self, **kwargs):
            raise ValueError(f"无法继续子智能体线程 {kwargs['requested_thread_id']}：当前对话中没有找到对应的运行记录")

    _patch_session(monkeypatch)

    _patch_subagent_run_service(monkeypatch, _SubagentRunService)
    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(
            thread_id="parent-thread",
            runtime_scope_id="parent-thread",
            workdir_path="projects/11111111-1111-4111-8111-111111111111",
            uid="user-1",
            run_id="parent-run",
        ),
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
        subagent_slug="worker",
        runtime=runtime,
        thread_id=unknown_thread_id,
    )

    assert result == f"无法继续子智能体线程 {unknown_thread_id}：当前对话中没有找到对应的运行记录"


@pytest.mark.asyncio
async def test_subagent_start_creates_child_run_and_enqueues(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _SubagentRunService:
        def __init__(self, db):
            captured["db"] = db

        async def start(self, **kwargs):
            captured["start"] = kwargs
            child_thread_id = make_child_thread_id("parent-thread", "worker", "tool-async")
            return SimpleNamespace(
                run=_subagent_run(status="pending", thread_id=child_thread_id),
                created=True,
                continuing=False,
                relation=SimpleNamespace(id=77, child_thread_id=child_thread_id),
            )

    _patch_session(monkeypatch)
    _patch_subagent_run_service(monkeypatch, _SubagentRunService)

    middleware = _async_tool_middleware(model="provider:parent-model")
    runtime = SimpleNamespace(tool_call_id="tool-async", state={}, config={})
    tool = next(item for item in middleware.tools if item.name == "subagent_start")

    result = await tool.coroutine(
        description="run in background",
        subagent_slug="worker",
        runtime=runtime,
    )

    child_thread_id = make_child_thread_id("parent-thread", "worker", "tool-async")
    assert isinstance(result, Command)
    payload = json.loads(result.update["messages"][0].content)
    assert payload["status"] == "started"
    assert payload["run_id"] == "child-run"
    assert payload["thread_id"] == child_thread_id
    assert payload["events_url"] == "/api/agent/runs/child-run/events"
    assert payload["subagent_thread_relation_id"] == 77
    assert captured["start"]["uid"] == "user-1"
    assert captured["start"]["created_by_run_id"] == "parent-run"
    assert isinstance(captured["start"]["input_message"], AgentRunInputMessage)
    assert captured["start"]["input_message"].content == "run in background"
    assert captured["start"]["input_message"].raw_message()["type"] == "human"
    assert captured["start"]["input_message"].raw_message()["content"] == "run in background"
    assert "description" not in captured["start"]
    assert captured["start"]["requested_thread_id"] is None
    assert "model_spec" not in captured["start"]
    assert result.update["subagent_runs"][0]["child_thread_id"] == child_thread_id
    assert result.update["subagent_runs"][0]["run_id"] == "child-run"


@pytest.mark.asyncio
async def test_subagent_start_replay_reuses_existing_run_without_file_checkpoint(monkeypatch) -> None:

    class _SubagentRunService:
        def __init__(self, db):
            del db

        async def start(self, **kwargs):
            run = _subagent_run(status="pending")
            return SimpleNamespace(
                run=run,
                created=False,
                continuing=False,
                relation=SimpleNamespace(id=77, child_thread_id=run.conversation_thread_id),
            )

    _patch_session(monkeypatch)

    _patch_subagent_run_service(monkeypatch, _SubagentRunService)

    tool = next(item for item in _async_tool_middleware().tools if item.name == "subagent_start")
    result = await tool.coroutine(
        description="replayed task",
        subagent_slug="worker",
        runtime=SimpleNamespace(tool_call_id="tool-async", state={}, config={}),
    )

    assert json.loads(result.update["messages"][0].content)["status"] == "existing"


@pytest.mark.asyncio
async def test_subagent_status_returns_terminal_result(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _SubagentRunService:
        def __init__(self, db):
            captured["db"] = db

        async def get_run_for_creator(self, **kwargs):
            captured["get_run_for_creator"] = kwargs
            return _subagent_run(status="completed")

    async def fake_get_agent_run_result(*, run_id: str, current_uid: str, db):
        captured["get_agent_run_result"] = {"run_id": run_id, "current_uid": current_uid, "db": db}
        return {"status": "completed", "output": "final result"}

    async def fake_get_agent_run_progress(run_id: str):
        captured["get_agent_run_progress"] = run_id
        return {
            "last_seq": "3-0",
            "messages": [{"seq": "3-0", "kind": "assistant_message", "message_id": "msg-1", "content": "working"}],
        }

    _patch_session(monkeypatch)
    _patch_subagent_run_service(monkeypatch, _SubagentRunService)
    monkeypatch.setattr(agent_run_service, "get_agent_run_result", fake_get_agent_run_result)
    monkeypatch.setattr(agent_run_service, "get_agent_run_progress", fake_get_agent_run_progress)

    tool = next(item for item in _async_tool_middleware().tools if item.name == "subagent_status")
    result = await tool.coroutine(run_id="child-run", runtime=SimpleNamespace(tool_call_id="status-call"))

    assert isinstance(result, Command)
    payload = json.loads(result.update["messages"][0].content)
    assert payload["status"] == "completed"
    assert payload["progress"]["messages"][0]["content"] == "working"
    assert payload["result"]["output"] == "final result"
    assert captured["get_agent_run_progress"] == "child-run"
    assert captured["get_run_for_creator"] == {
        "uid": "user-1",
        "created_by_run_id": "parent-run",
        "run_id": "child-run",
    }
    assert captured["get_agent_run_result"]["current_uid"] == "user-1"
    assert result.update["subagent_runs"] == [
        {
            "id": "tool-async",
            "run_id": "child-run",
            "subagent_slug": "worker",
            "subagent_name": "Worker",
            "child_thread_id": "child-thread",
            "status": "completed",
            "events_url": "/api/agent/runs/child-run/events",
            "result_url": "/api/agent/runs/child-run/result",
        }
    ]


@pytest.mark.asyncio
async def test_subagent_status_returns_progress_for_running_run(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _SubagentRunService:
        def __init__(self, db):
            captured["db"] = db

        async def get_run_for_creator(self, **kwargs):
            captured["get_run_for_creator"] = kwargs
            return _subagent_run(status="running")

    async def fake_get_agent_run_result(**kwargs):
        raise AssertionError(f"running status should not load terminal result: {kwargs}")

    async def fake_get_agent_run_progress(run_id: str):
        captured["get_agent_run_progress"] = run_id
        return {
            "last_seq": "9-0",
            "messages": [
                {"seq": "8-0", "kind": "tool_call", "tool_call_id": "call-1", "content": "调用工具 read_file"},
                {"seq": "9-0", "kind": "assistant_message", "message_id": "msg-2", "content": "正在整理结果"},
            ],
        }

    _patch_session(monkeypatch)
    _patch_subagent_run_service(monkeypatch, _SubagentRunService)
    monkeypatch.setattr(agent_run_service, "get_agent_run_result", fake_get_agent_run_result)
    monkeypatch.setattr(agent_run_service, "get_agent_run_progress", fake_get_agent_run_progress)

    tool = next(item for item in _async_tool_middleware().tools if item.name == "subagent_status")
    result = await tool.coroutine(run_id="child-run", runtime=SimpleNamespace(tool_call_id="status-call"))

    payload = json.loads(result.update["messages"][0].content)
    assert payload["status"] == "running"
    assert "result" not in payload
    assert payload["progress"]["last_seq"] == "9-0"
    assert [item["content"] for item in payload["progress"]["messages"]] == ["调用工具 read_file", "正在整理结果"]
    assert captured["get_run_for_creator"] == {
        "uid": "user-1",
        "created_by_run_id": "parent-run",
        "run_id": "child-run",
    }


@pytest.mark.asyncio
async def test_subagent_cancel_and_await_use_parent_run_scope(monkeypatch) -> None:
    captured: dict[str, object] = {"loads": []}

    async def fake_get_verified_subagent_run(self, *, run_id: str, uid: str, created_by_run_id: str):
        del self
        captured["loads"].append(
            {
                "run_id": run_id,
                "uid": uid,
                "created_by_run_id": created_by_run_id,
            }
        )
        return _subagent_run(status="completed")

    async def fake_request_cancel_agent_run(*, run_id: str, current_uid: str, db):
        captured["cancel"] = {"run_id": run_id, "current_uid": current_uid, "db": db}
        return _subagent_run(status="cancelling")

    async def fake_await_agent_run_result(*, run_id: str, current_uid: str):
        captured["await"] = {"run_id": run_id, "current_uid": current_uid}
        return {"status": "completed", "output": "awaited result"}

    _patch_session(monkeypatch)
    monkeypatch.setattr(YuxiSubAgentMiddleware, "_get_verified_subagent_run", fake_get_verified_subagent_run)
    monkeypatch.setattr(agent_run_service, "request_cancel_agent_run", fake_request_cancel_agent_run)
    monkeypatch.setattr(agent_run_service, "await_agent_run_result", fake_await_agent_run_result)

    tools = {item.name: item for item in _async_tool_middleware().tools}

    cancel_result = await tools["subagent_cancel"].coroutine(
        run_id="child-run",
        runtime=SimpleNamespace(tool_call_id="cancel-call"),
    )
    cancel_payload = json.loads(cancel_result.update["messages"][0].content)
    assert cancel_payload["status"] == "cancelling"
    assert captured["cancel"]["current_uid"] == "user-1"

    await_result = await tools["subagent_await"].coroutine(
        run_id="child-run",
        runtime=SimpleNamespace(tool_call_id="await-call"),
    )
    await_payload = json.loads(await_result.update["messages"][0].content)
    assert await_payload["result"]["output"] == "awaited result"
    assert captured["await"] == {"run_id": "child-run", "current_uid": "user-1"}
    assert captured["loads"]
    assert all(
        load == {"run_id": "child-run", "uid": "user-1", "created_by_run_id": "parent-run"}
        for load in captured["loads"]
    )


@pytest.mark.asyncio
async def test_subagent_await_reports_timeout_when_run_is_still_active(monkeypatch) -> None:
    captured: dict[str, object] = {"loads": []}

    async def fake_get_verified_subagent_run(self, *, run_id: str, uid: str, created_by_run_id: str):
        del self
        captured["loads"].append(
            {
                "run_id": run_id,
                "uid": uid,
                "created_by_run_id": created_by_run_id,
            }
        )
        return _subagent_run(status="running")

    async def fake_await_agent_run_result(*, run_id: str, current_uid: str):
        captured["await"] = {"run_id": run_id, "current_uid": current_uid}
        raise agent_run_service.AgentRunWaitTimeout(
            {"status": "running", "agent_run_id": run_id, "thread_id": "child-thread", "output": ""}
        )

    _patch_session(monkeypatch)
    monkeypatch.setattr(YuxiSubAgentMiddleware, "_get_verified_subagent_run", fake_get_verified_subagent_run)
    monkeypatch.setattr(agent_run_service, "await_agent_run_result", fake_await_agent_run_result)

    result = await {item.name: item for item in _async_tool_middleware().tools}["subagent_await"].coroutine(
        run_id="child-run",
        runtime=SimpleNamespace(tool_call_id="await-call"),
    )

    payload = json.loads(result.update["messages"][0].content)
    assert payload["status"] == "running"
    assert payload["wait_timed_out"] is True
    assert payload["message"] == "子智能体仍在运行，等待最终结果超时；请稍后继续查询。"
    assert payload["result"]["status"] == "running"
    assert captured["await"] == {"run_id": "child-run", "current_uid": "user-1"}
    assert len(captured["loads"]) == 2


@pytest.mark.asyncio
async def test_historical_unexecuted_task_is_rejected_by_current_tool_node():
    """旧 checkpoint 的 task 调用明确报错，不隐式再次派发子 Run。"""
    from langchain_core.messages import AIMessage
    from langgraph.prebuilt import ToolNode

    from langgraph.graph import END, START, MessagesState, StateGraph

    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode(_async_tool_middleware().tools))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    result = await graph.compile().ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "old-task",
                            "name": "task",
                            "args": {"description": "old", "subagent_slug": "worker"},
                        }
                    ],
                )
            ],
        }
    )
    message = result["messages"][-1]
    assert message.status == "error"
    assert message.tool_call_id == "old-task"
    assert "task is not a valid tool" in message.content


@pytest.mark.asyncio
async def test_subagent_start_returns_busy_through_session_context(monkeypatch) -> None:
    """忙异常跨过真实异步上下文管理器后仍返回结构化工具结果。"""

    @asynccontextmanager
    async def session():
        """使用标准库上下文管理器传播异常。"""
        yield object()

    monkeypatch.setattr(
        subagent_task_middleware,
        "pg_manager",
        SimpleNamespace(get_async_session_context=session),
    )

    class BusyService:
        """模拟已有活跃子任务的服务结果。"""

        def __init__(self, db):
            """接收会话以匹配实际服务接口。"""

        async def start(self, **kwargs):
            """抛出携带活跃任务信息的忙异常。"""
            raise subagent_run_service.SubagentRunBusy(
                thread_id="child-thread",
                active_run_id="active-run",
                active_run_status="running",
                message="already running",
            )

    _patch_subagent_run_service(monkeypatch, BusyService)
    result = (
        await _async_tool_middleware()
        .tools[0]
        .coroutine(
            description="continue work",
            subagent_slug="worker",
            thread_id="child-thread",
            runtime=SimpleNamespace(tool_call_id="busy-call"),
        )
    )

    assert isinstance(result, Command)
    message = result.update["messages"][0]
    assert message.tool_call_id == "busy-call"
    assert json.loads(message.content) == {
        "status": "busy",
        "thread_id": "child-thread",
        "active_run_id": "active-run",
        "active_run_status": "running",
        "message": "already running",
    }
