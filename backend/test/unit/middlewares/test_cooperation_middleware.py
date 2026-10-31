"""协作工具只接受直属成员名称，路径由执行身份生成。"""

import operator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Annotated, TypedDict
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import ValidationError

from yuxi.modules.agents.runtime.middlewares.cooperation import CooperationMiddleware, create_cooperation_middleware
from yuxi.modules.agents.services.execution import _extract_interrupts

pytestmark = pytest.mark.unit


def test_ordinary_cooperation_always_registers_agent_discovery():
    """普通协作显式提供 Agent 目录，不按另一份白名单裁剪。"""
    middleware = create_cooperation_middleware(SimpleNamespace(uid="user", run_id="run"))
    assert "list_agents" in {tool.name for tool in middleware.tools}


def test_independent_tasks_expose_only_nonblocking_cooperation_tools():
    """独立任务使用同一协作入口，但不暴露树创建参数、持久等待或用户沟通。"""
    middleware = create_cooperation_middleware(SimpleNamespace(uid="user", run_id="run"), independent_tasks=True)
    tools = {tool.name: tool for tool in middleware.tools}
    assert set(tools) == {"create_session", "submit_input", "send_message", "cancel_turn", "list_sessions", "list_agents", "get_result"}
    schema = tools["create_session"].tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {"name", "description", "agent_id"}
    assert set(schema["required"]) == {"name", "description"}
    assert schema["properties"]["agent_id"]["default"] is None


@pytest.mark.asyncio
async def test_identity_prompt_uses_persisted_member_and_refreshes_for_new_run(monkeypatch):
    """身份来自持久成员，同 Run 复用，换 Run 后更新父子身份。"""
    from yuxi.modules.agents.runtime.middlewares import cooperation
    from yuxi.modules.agents.services.cooperation import SessionCooperationService

    @asynccontextmanager
    async def database():
        yield object()

    root = SimpleNamespace(
        thread_id="root-id",
        cooperation_name="root",
        cooperation_path="/root",
        parent_thread_id=None,
        tree_root_thread_id="root-id",
        project_id="project-id",
    )
    child = SimpleNamespace(
        thread_id="child-id",
        cooperation_name="researcher",
        cooperation_path="/root/researcher",
        parent_thread_id="root-id",
        tree_root_thread_id="root-id",
        project_id="project-id",
    )
    from yuxi.modules.agents.repositories.input import AgentInputRepository

    monkeypatch.setattr(
        AgentInputRepository, "input_ids_for_runs", AsyncMock(side_effect=[{"run-1": ["input-1"]}, {"run-2": ["input-2", "input-3"]}])
    )
    caller = AsyncMock(
        side_effect=[
            (SimpleNamespace(id="run-1", turn_id="turn-1", input_id="input-1"), root),
            (SimpleNamespace(id="run-2", turn_id="turn-2", input_id="input-2"), child),
        ]
    )
    monkeypatch.setattr(cooperation.pg_manager, "get_async_session_context", database)
    monkeypatch.setattr(cooperation, "system_options", SimpleNamespace(get=AsyncMock(return_value={"max_cooperation_concurrency": 4})))
    monkeypatch.setattr(SessionCooperationService, "caller", caller)
    context = SimpleNamespace(run_id="run-1", uid="user-id", worker_id="worker")
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        system_message=SystemMessage(content="原始 Agent 指令"),
        override=lambda **values: SimpleNamespace(**values),
    )
    middleware = CooperationMiddleware()
    handler = AsyncMock()
    await middleware.awrap_model_call(request, handler)
    await middleware.awrap_model_call(request, handler)
    assert caller.await_count == 1
    first = "\n".join(block["text"] for block in handler.call_args.args[0].system_message.content)
    assert '"role": "根会话"' in first and '"session_id": "root-id"' in first
    assert '"tree_execution_slots": 4' in first
    assert '"project_id": "project-id"' in first
    assert "只有同项目共享工作目录" in first
    assert "原始 Agent 指令" in first
    assert "@agent:<id>" in first
    assert "create_session 的 agent_id" in first
    assert "不要求无条件创建子会话" in first
    context.run_id = "run-2"
    await middleware.awrap_model_call(request, handler)
    second = "\n".join(block["text"] for block in handler.call_args.args[0].system_message.content)
    assert caller.await_count == 2
    assert '"role": "子会话"' in second
    assert '"name": "researcher"' in second and '"path": "/root/researcher"' in second
    assert '"parent_session_id": "root-id"' in second and '"run_id": "run-2"' in second
    assert '"run_id": "run-1"' not in second
    assert '"input_ids": ["input-2", "input-3"]' in second


@pytest.mark.asyncio
async def test_identity_lookup_failure_does_not_call_model(monkeypatch):
    """执行身份失效时保留来源错误，不调用模型或缓存伪造身份。"""
    from yuxi.modules.agents.runtime.middlewares import cooperation
    from yuxi.modules.agents.services.cooperation import SessionCooperationService

    @asynccontextmanager
    async def database():
        yield object()

    monkeypatch.setattr(cooperation.pg_manager, "get_async_session_context", database)
    monkeypatch.setattr(SessionCooperationService, "caller", AsyncMock(side_effect=ValueError("执行归属已失效")))
    request = SimpleNamespace(runtime=SimpleNamespace(context=SimpleNamespace(run_id="run-1", uid="user-id", worker_id="worker")))
    handler = AsyncMock()
    with pytest.raises(ValueError, match="执行归属已失效"):
        await CooperationMiddleware().awrap_model_call(request, handler)
    handler.assert_not_awaited()


def test_list_sessions_schema_has_no_pagination_arguments():
    """模型一次读取完整协作摘要，无需携带游标或页大小。"""
    tool = next(tool for tool in CooperationMiddleware().tools if tool.name == "list_sessions")
    assert tool.tool_call_schema.model_json_schema()["properties"] == {}


def test_agent_directory_schema_uses_persisted_execution_identity():
    """目录参数不能由模型指定用户或 APP 扩大范围。"""
    tool = next(tool for tool in CooperationMiddleware().tools if tool.name == "list_agents")
    assert tool.tool_call_schema.model_json_schema()["properties"] == {}


def test_create_session_schema_exposes_local_name_without_parent_or_path():
    """模型仅看到带局部名称约束的创建参数。"""
    tool = CooperationMiddleware().tools[0]
    schema = tool.tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {"name", "description", "agent_id", "mode"}
    assert set(schema["required"]) == {"name", "description"}
    assert schema["properties"]["agent_id"]["default"] is None
    assert schema["properties"]["mode"]["default"] == "child"
    assert schema["properties"]["mode"]["enum"] == ["child", "root"]
    name = schema["properties"]["name"]
    assert name["pattern"] == "^[A-Za-z0-9_-]+$"
    assert name["minLength"] == 1 and name["maxLength"] == 64
    assert "局部" in name["description"] and "路径" in name["description"]


@pytest.mark.parametrize("mode", ["other", "", None])
def test_create_session_tool_rejects_unknown_creation_mode(mode):
    """工具边界只接受明确的直属或顶层模式。"""
    tool = CooperationMiddleware().tools[0]
    with pytest.raises(ValidationError) as rejected:
        tool.tool_call_schema.model_validate({"name": "member", "description": "任务", "mode": mode})
    assert any(error["loc"] == ("mode",) for error in rejected.value.errors())


@pytest.mark.parametrize("name", ["/root/aaa/bbb", "aaa/bbb", "/root/name", "../bbb", r"aaa\bbb", "", "a" * 65])
def test_create_session_tool_arguments_reject_paths(name):
    """路径与无效名称在工具参数边界被拒绝。"""
    tool = CooperationMiddleware().tools[0]
    with pytest.raises(ValidationError) as rejected:
        tool.tool_call_schema.model_validate({"name": name, "description": "完成独立工作"})
    assert any(error["loc"] == ("name",) for error in rejected.value.errors())


@pytest.mark.parametrize("name", ["aaa", "worker-1", "review_2", "a" * 64])
def test_create_session_tool_arguments_accept_local_names(name):
    """合法局部名称保留原值供服务生成路径。"""
    tool = CooperationMiddleware().tools[0]
    arguments = tool.tool_call_schema.model_validate({"name": name, "description": "完成独立工作"})
    assert arguments.name == name


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [True, False])
async def test_create_session_exposes_only_owned_depth_denial_as_error_tool_result(monkeypatch, known):
    """有明确来源的深度拒绝可给模型，其他异常继续由通用保护层脱敏。"""
    from yuxi.modules.agents.runtime.middlewares import cooperation
    from yuxi.modules.agents.services.cooperation import SessionCooperationService
    from yuxi.shared.errors import InvalidOperationError

    @asynccontextmanager
    async def database():
        """隔离事务边界，不创建测试之外的持久资源。"""
        yield object()

    code = "cooperation_depth_exceeded" if known else "untrusted_failure"
    error = InvalidOperationError("协作深度 3 超过最大协作深度 2" if known else "PRIVATE_ERROR_DETAIL", code=code)
    monkeypatch.setattr(cooperation.pg_manager, "get_async_session_context", database)
    monkeypatch.setattr(SessionCooperationService, "create_session", AsyncMock(side_effect=error))
    runtime = SimpleNamespace(
        context=SimpleNamespace(run_id="run", uid="user", worker_id="worker", _cooperation_config_snapshot={}), tool_call_id="create"
    )
    tool = next(tool for tool in CooperationMiddleware().tools if tool.name == "create_session")
    if known:
        result = await tool.coroutine(name="child", description="task", runtime=runtime)
        assert result.status == "error" and result.tool_call_id == "create"
        assert result.content == error.message
    else:
        with pytest.raises(InvalidOperationError) as rejected:
            await tool.coroutine(name="child", description="task", runtime=runtime)
        assert rejected.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["wait_inputs", "wait_sessions"])
async def test_parallel_wait_tools_preserve_deadline_and_fixed_resume_result(monkeypatch, tool_name):
    """真实图重放不能延长另一等待的 deadline，也不能用迟到查询替换已固定的超时结果。"""
    from yuxi.modules.agents.runtime.middlewares import cooperation
    from yuxi.modules.agents.services.cooperation import SessionCooperationService

    class WaitState(TypedDict):
        """独立保存两个工具的返回值。"""

        results: Annotated[list, operator.add]

    @asynccontextmanager
    async def database():
        """只隔离外部数据库，图执行与中断恢复使用真实 LangGraph。"""
        yield object()

    phase = "initial"
    deadline = "2026-10-24T00:00:00+00:00"
    waits = [
        {
            "kind": "cooperation",
            **(
                {"target_inputs": [f"input-{index}"]}
                if tool_name == "wait_inputs"
                else {"target_sessions": [f"session-{index}"], "after_cursor": 3}
            ),
            "deadline": deadline,
        }
        for index in range(2)
    ]

    async def prepare(_service, **arguments):
        """模拟恢复时目标已变化，首次等待请求仍须由 checkpoint 固定。"""
        target = (arguments.get("input_ids") or arguments["targets"])[0]
        index = int(target[-1])
        if phase == "initial":
            return waits[index]
        if index == 0:
            return {"late_database_result": "completed after the fixed timeout", "wait_timed_out": False}
        return {**waits[index], "deadline": "2026-10-24T00:10:00+00:00"}

    monkeypatch.setattr(cooperation.pg_manager, "get_async_session_context", database)
    monkeypatch.setattr(SessionCooperationService, "wait_inputs" if tool_name == "wait_inputs" else "wait_updates", prepare)

    def build_graph():
        """重建生产 middleware，验证恢复不依赖上一次图实例或 Python 闭包。"""
        tool = next(tool for tool in CooperationMiddleware().tools if tool.name == tool_name)
        builder = StateGraph(WaitState)

        def node(index):
            """给每个工具提供独立执行调用身份。"""

            async def run(_state):
                """通过生产工具产生和恢复中断。"""
                runtime = SimpleNamespace(
                    context=SimpleNamespace(run_id=f"run-{phase}", uid="user", worker_id="worker", _cooperation_config_snapshot={}),
                    tool_call_id=f"wait-{index}",
                )
                arguments = (
                    {"input_ids": [f"input-{index}"]}
                    if tool_name == "wait_inputs"
                    else {"targets": [f"session-{index}"], "after_cursor": 3}
                )
                result = await tool.coroutine(runtime=runtime, **arguments)
                return {"results": [{"index": index, "value": result}]}

            return run

        for index in range(2):
            builder.add_node(f"wait-{index}", node(index))
            builder.add_edge(START, f"wait-{index}")
            builder.add_edge(f"wait-{index}", END)
        return builder.compile(checkpointer=saver)

    saver = InMemorySaver()
    config = {"configurable": {"thread_id": tool_name}}
    graph = build_graph()
    await graph.ainvoke({"results": []}, config)
    pending = _extract_interrupts(await graph.aget_state(config))
    ids = [next(item.id for item in pending if item.value == wait) for wait in waits]
    phase = "resumed"
    frozen_timeout = {"results": [], "wait_timed_out": True}
    graph = build_graph()
    await graph.ainvoke(Command(resume={ids[0]: frozen_timeout}), config)
    snapshot = await graph.aget_state(config)
    finished = next(task.result for task in snapshot.tasks if task.name == "wait-0")
    assert finished["results"] == [{"index": 0, "value": frozen_timeout}]
    remaining = _extract_interrupts(snapshot)
    assert [(item.id, item.value) for item in remaining] == [(ids[1], waits[1])]
    second_result = {"results": ["second exact result"], "wait_timed_out": False}
    graph = build_graph()
    await graph.ainvoke(Command(resume={ids[1]: second_result}), config)
    final = await graph.aget_state(config)
    assert {item["index"]: item["value"] for item in final.values["results"]} == {0: frozen_timeout, 1: second_result}
    assert _extract_interrupts(final) == []
