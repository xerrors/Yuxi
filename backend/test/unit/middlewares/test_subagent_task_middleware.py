from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yuxi.agents.middlewares.subagent_task as subagent_task_middleware
import yuxi.services.agent_run_service as agent_run_service
import yuxi.services.subagent_run_service as subagent_run_service
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from yuxi.agents.buildin.chatbot.state import merge_subagent_runs
from yuxi.agents.middlewares.subagent_task import YuxiSubAgentMiddleware
from yuxi.repositories.agent_repository import SUB_AGENT_BACKEND_ID
from yuxi.services.input_message_service import AgentRunInputMessage
from yuxi.utils.hash_utils import subagent_child_thread_id


def make_child_thread_id(parent_thread_id: str, agent_slug: str, tool_call_id: str) -> str:
    return subagent_child_thread_id(parent_thread_id, agent_slug, tool_call_id)


class _ChildContext:
    def __init__(self):
        self.model = None

    def update_from_dict(self, values: dict):
        for key, value in values.items():
            if hasattr(self, key):
                setattr(self, key, value)


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
    parent_context = SimpleNamespace(thread_id="parent-thread", uid="user-1", run_id="parent-run")
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


def _patch_task_start_and_await(
    monkeypatch,
    captured: dict,
    *,
    status: str = "completed",
    output: str = "child done",
    thread_id: str = "child-thread",
    error_message: str | None = None,
    wait_timeout: bool = False,
):
    class _SubagentRunService:
        def __init__(self, db):
            captured["db"] = db

        async def start(self, **kwargs):
            captured["start"] = kwargs
            return SimpleNamespace(
                run=_subagent_run(
                    status="pending",
                    thread_id=thread_id,
                    tool_call_id=kwargs["tool_call_id"],
                    subagent_slug=kwargs["agent_item"].slug,
                    subagent_name=kwargs["agent_item"].name,
                    description=kwargs["input_message"].content,
                ),
                created=True,
                continuing=bool(kwargs.get("requested_thread_id")),
                relation=SimpleNamespace(id=77, child_thread_id=thread_id),
            )

        async def get_run_for_creator(self, **kwargs):
            captured["get_run_for_creator"] = kwargs
            started = captured["start"]
            return _subagent_run(
                status=status,
                thread_id=thread_id,
                tool_call_id=started["tool_call_id"],
                subagent_slug=started["agent_item"].slug,
                subagent_name=started["agent_item"].name,
                description=started["input_message"].content,
                error_message=error_message,
            )

    async def fake_await_agent_run_result(*, run_id: str, current_uid: str):
        captured["await"] = {"run_id": run_id, "current_uid": current_uid}
        result = {
            "status": status,
            "output": output,
            "agent_run_id": run_id,
            "thread_id": thread_id,
        }
        if error_message:
            result["error"] = {"type": "RuntimeError", "message": error_message}
        if wait_timeout:
            raise agent_run_service.AgentRunWaitTimeout(result)
        return result

    _patch_session(monkeypatch)
    _patch_subagent_run_service(monkeypatch, _SubagentRunService)
    monkeypatch.setattr(agent_run_service, "await_agent_run_result", fake_await_agent_run_result)


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
        "task",
        "subagent_start",
        "subagent_status",
        "subagent_cancel",
        "subagent_await",
    }


@pytest.mark.asyncio
async def test_task_tool_rejects_unconfigured_subagent() -> None:
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
async def test_task_tool_invokes_subagent_with_child_scope(monkeypatch) -> None:
    captured = {}
    _patch_task_start_and_await(monkeypatch, captured, thread_id="child-thread")

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(
            thread_id="child-runtime-thread",
            parent_thread_id="parent-thread",
            file_thread_id="parent-file-thread",
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
    assert result.update["messages"][0].content == "> 子智能体线程 ID: child-thread\n\n---\n\nchild done"
    assert result.update["messages"][0].tool_call_id == "tool-1"
    assert captured["start"]["uid"] == "user-1"
    assert captured["start"]["created_by_run_id"] == "parent-run"
    assert captured["start"]["requested_thread_id"] is None
    assert captured["start"]["file_thread_id"] == "parent-file-thread"
    assert captured["start"]["model_spec"] is None
    assert captured["await"] == {"run_id": "child-run", "current_uid": "user-1"}
    assert result.update["subagent_runs"][0]["run_id"] == "child-run"
    assert result.update["subagent_runs"][0]["child_thread_id"] == "child-thread"
    assert result.update["subagent_runs"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_task_tool_inherits_parent_model_when_subagent_model_empty(monkeypatch) -> None:
    captured = {}
    _patch_task_start_and_await(monkeypatch, captured)

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(
            thread_id="parent-thread",
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

    assert captured["start"]["model_spec"] == "parent:model"


@pytest.mark.asyncio
async def test_task_tool_records_failed_subagent_run(monkeypatch) -> None:
    captured = {}
    _patch_task_start_and_await(monkeypatch, captured, status="failed", output="", error_message="child boom")

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(thread_id="parent-thread", uid="user-1", run_id="parent-run", model=""),
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

    result = await middleware.tools[0].coroutine(
        description="write a report",
        subagent_slug="worker",
        runtime=SimpleNamespace(tool_call_id="tool-1", state={}, config={}),
    )

    assert isinstance(result, Command)
    assert result.update["messages"][0].content == "> 子智能体线程 ID: child-thread\n\n---\n\nchild boom"
    assert result.update["subagent_runs"][0]["status"] == "failed"
    assert result.update["subagent_runs"][0]["error"] == "child boom"


@pytest.mark.asyncio
async def test_task_tool_reports_running_subagent_after_wait_timeout(monkeypatch) -> None:
    captured = {}
    _patch_task_start_and_await(monkeypatch, captured, status="running", output="", wait_timeout=True)

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(thread_id="parent-thread", uid="user-1", run_id="parent-run", model=""),
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

    result = await middleware.tools[0].coroutine(
        description="write a long report",
        subagent_slug="worker",
        runtime=SimpleNamespace(tool_call_id="tool-1", state={}, config={}),
    )

    assert isinstance(result, Command)
    content = result.update["messages"][0].content
    assert "子智能体仍在运行" in content
    assert "run_id: child-run" in content
    assert "不要把当前结果视为任务已完成" in content
    assert "子智能体已完成任务" not in content
    assert result.update["subagent_runs"][0]["status"] == "running"


@pytest.mark.asyncio
async def test_task_tool_continues_existing_subagent_thread(monkeypatch) -> None:
    captured = {}
    _patch_task_start_and_await(monkeypatch, captured, output="continued done", thread_id="child-thread")

    middleware = YuxiSubAgentMiddleware(
        parent_context=SimpleNamespace(thread_id="parent-thread", uid="user-1", run_id="parent-run", model=""),
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
    assert result.update["messages"][0].content == "> 子智能体线程 ID: child-thread\n\n---\n\ncontinued done"
    assert captured["start"]["requested_thread_id"] == "child-thread"
    assert result.update["subagent_runs"][0]["child_thread_id"] == "child-thread"


@pytest.mark.asyncio
async def test_task_tool_rejects_invalid_continuation_thread(monkeypatch) -> None:
    class _SubagentRunService:
        def __init__(self, db):
            del db

        async def start(self, **kwargs):
            raise ValueError(
                f"无法继续子智能体线程 {kwargs['requested_thread_id']}：当前对话中没有找到对应的运行记录"
            )

    _patch_session(monkeypatch)
    _patch_subagent_run_service(monkeypatch, _SubagentRunService)
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
    assert captured["start"]["model_spec"] == "provider:parent-model"
    assert result.update["subagent_runs"][0]["child_thread_id"] == child_thread_id
    assert result.update["subagent_runs"][0]["run_id"] == "child-run"


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


def test_merge_subagent_runs_keeps_new_run_on_same_child_thread() -> None:
    child_thread_id = make_child_thread_id("parent-thread", "worker", "tool-old")

    merged = merge_subagent_runs(
        [
            {
                "id": "tool-old",
                "run_id": "run-old",
                "subagent_slug": "worker",
                "subagent_name": "Worker",
                "child_thread_id": child_thread_id,
                "description": "first task",
                "status": "completed",
                "created_at": "2026-05-31T01:00:00Z",
                "completed_at": "2026-05-31T01:01:00Z",
            }
        ],
        [
            {
                "id": "tool-new",
                "run_id": "run-new",
                "subagent_slug": "worker",
                "subagent_name": "Worker",
                "child_thread_id": child_thread_id,
                "description": "continue task",
                "status": "pending",
                "created_at": "2026-05-31T02:00:00Z",
            }
        ],
    )

    assert merged == [
        {
            "id": "tool-old",
            "run_id": "run-old",
            "subagent_slug": "worker",
            "subagent_name": "Worker",
            "child_thread_id": child_thread_id,
            "description": "first task",
            "status": "completed",
            "created_at": "2026-05-31T01:00:00Z",
            "completed_at": "2026-05-31T01:01:00Z",
        },
        {
            "id": "tool-new",
            "run_id": "run-new",
            "subagent_slug": "worker",
            "subagent_name": "Worker",
            "child_thread_id": child_thread_id,
            "description": "continue task",
            "status": "pending",
            "created_at": "2026-05-31T02:00:00Z",
        },
    ]
