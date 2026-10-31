"""ToolErrorGuardMiddleware 单元测试。

背景：langgraph 的 ToolNode 只把参数校验类错误转成工具消息，工具体内抛出的其它
异常会原样 raise → 整个 agent run 异常终局 → 前端"对话出错"，工具卡永久停在
"执行中"。ToolErrorGuardMiddleware 在工具调用包装层兜底，把运行时异常隔离为错误
ToolMessage，同时放行 GraphBubbleUp（interrupt 门禁）与取消/进程控制异常。

测试用 fake 模型 + 真实 create_agent 装配验证契约，不依赖网络与外部服务。
"""

import asyncio
import inspect
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt
from mcp import types as mcp_types

from yuxi.modules.agents.runtime import graph as runtime_graph
from yuxi.modules.agents.runtime.middlewares import ToolErrorGuardMiddleware
from yuxi.modules.agents.tool_results import normalize_tool_result

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@tool
def parse_sandbox_file(path: str) -> str:
    """解析沙盒文件，路径域不匹配时抛异常（复现线上事故的工具行为）。"""
    if not path.startswith("/home/user/workspace/"):
        raise ValueError(f"只允许解析 /home/user/workspace 下的沙盒虚拟路径，收到 {path}")
    return f"parsed:{path}"


@tool
def ask_human(reason: str) -> str:
    """人审门禁：通过 interrupt 挂起等待人工输入。"""
    answer = interrupt(reason)
    return f"approved:{answer}"


@tool
def cancel_now(reason: str) -> str:
    """抛出取消异常的工具。"""
    raise asyncio.CancelledError


_TOOL_ARGS = {
    "parse_sandbox_file": {"path": "/etc/passwd"},
    "ask_human": {"reason": "需要审批"},
    "cancel_now": {"reason": "取消"},
    "query_kb": {"kb_id": "review-kb", "query_text": "needle"},
}


def _knowledge_query_tool(session):
    """由官方 adapter 将 SDK 会话和工具协议转换为实际 Agent 工具。"""
    result = convert_mcp_tool_to_langchain_tool(
        session,
        mcp_types.Tool(
            name="query_kb",
            description="检索知识库",
            inputSchema={"type": "object", "properties": {"kb_id": {"type": "string"}, "query_text": {"type": "string"}}},
        ),
    )
    result.handle_tool_error = True
    return result


class _ScriptedToolModel(BaseChatModel):
    """第一轮调用指定工具，之后按上一轮结果给出答复的确定性 fake 模型。"""

    turn: int = 0
    tool_name: str = "parse_sandbox_file"

    @property
    def _llm_type(self) -> str:
        return "fake-scripted-tool-model"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        self.turn += 1
        if self.turn == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": self.tool_name,
                        "args": dict(_TOOL_ARGS[self.tool_name]),
                        "id": f"call-{self.turn}",
                    }
                ],
            )
        elif type(messages[-1]) is ToolMessage:
            message = AIMessage(content="已根据工具结果继续回答")
        else:
            message = AIMessage(content="final answer")
        return ChatResult(generations=[ChatGeneration(message=message)])


def _tool_call_request(tool_call: dict) -> ToolCallRequest:
    events = []
    return ToolCallRequest(
        tool_call=tool_call,
        tool=None,
        state=None,
        runtime=SimpleNamespace(context=None, config={}, stream_writer=events.append, emitted=events),
    )


async def _call_hook(hook: str, request: ToolCallRequest, handler):
    """按 hook 调同步/异步入口，统一为协程结果。"""
    result = getattr(ToolErrorGuardMiddleware(), hook)(request, handler)
    if inspect.isawaitable(result):
        return await result
    return result


async def _run_agent(middleware, tool_name: str, *, mcp_session=None) -> dict:
    model = _ScriptedToolModel(tool_name=tool_name)
    tools = [parse_sandbox_file, ask_human, cancel_now]
    if mcp_session is not None:
        tools.append(_knowledge_query_tool(mcp_session))
    agent = create_agent(
        model=model,
        tools=tools,
        middleware=list(middleware),
        context_schema=dict,
    )
    config = {"configurable": {"thread_id": f"thread-{tool_name}"}, "recursion_limit": 6}
    return await agent.ainvoke(
        {"messages": [HumanMessage("解析这个文件")]}, config=config, context=SimpleNamespace(uid="user", knowledges="all")
    )


async def test_tool_runtime_error_becomes_tool_message_and_run_continues():
    """工具体内抛出的运行时异常必须隔离为错误 ToolMessage，对话继续。"""
    state = await _run_agent([ToolErrorGuardMiddleware()], "parse_sandbox_file")

    tool_messages = [message for message in state["messages"] if message.type == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call-1"
    assert "parse_sandbox_file 执行失败" in tool_messages[0].content
    assert "ValueError" in tool_messages[0].content
    assert tool_messages[0].status == "error"
    # 模型拿到了工具结果并给出下一轮回答，而不是整场 run 被打死。
    assert state["messages"][-1].content == "已根据工具结果继续回答"


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "tool_error",
        "middleware_error",
        "returned_error",
        "command",
        "rewritten",
        "post_error",
        "post_command_error",
        "invalid_command",
        "interrupt",
    ],
)
async def test_guard_streams_final_middleware_result(outcome, monkeypatch):
    """真实图验证逻辑生命周期覆盖异常与短路返回，结果只取最外层结局。"""
    from langgraph.types import Command

    from yuxi.modules.agents.runtime.base import BaseAgent, GraphExecutionResult
    from yuxi.modules.agents.runtime.context import BaseContext
    from yuxi.modules.agents.services.message_recorder import tool_event_output

    class InnerMiddleware(AgentMiddleware):
        """覆盖有无执行 BaseTool 的真实中间件路径。"""

        async def awrap_tool_call(self, request, handler):
            """直接返回、前置抛错或执行工具。"""
            if outcome == "middleware_error":
                raise ValueError("private middleware detail")
            if outcome == "returned_error":
                return ToolMessage(content="permission denied", tool_call_id=request.tool_call["id"], status="error")
            if outcome == "command":
                return Command(update={"messages": [ToolMessage(content="command result", tool_call_id=request.tool_call["id"])]})
            result = await handler(request)
            if outcome == "rewritten":
                return result.model_copy(update={"content": "offloaded pointer"})
            if outcome in {"post_error", "post_command_error"}:
                result = result.model_copy(update={"status": "error", "content": "postprocessing failed"})
                return Command(update={"messages": [result]}) if outcome == "post_command_error" else result
            return result

    if outcome in {"success", "rewritten", "post_error", "post_command_error"}:
        monkeypatch.setitem(_TOOL_ARGS, "parse_sandbox_file", {"path": "/home/user/workspace/input.pdf"})

    @tool("parse_sandbox_file")
    def invalid_command(path: str) -> Command:
        """返回缺少本次 ToolMessage 的 Command，由 ToolNode 拒绝。"""
        return Command(update={"messages": []})

    tool_name = "ask_human" if outcome == "interrupt" else "parse_sandbox_file"
    selected_tool = invalid_command if outcome == "invalid_command" else parse_sandbox_file
    graph = create_agent(
        model=_ScriptedToolModel(tool_name=tool_name),
        tools=[selected_tool, ask_human],
        middleware=[ToolErrorGuardMiddleware(), InnerMiddleware()],
    )

    class Agent(BaseAgent):
        """使用生产运行流转接 Guard 生命周期。"""

        async def get_graph(self, **kwargs):
            """返回本例确定性图。"""
            return graph

    # 本测试不需要持久 checkpoint；只替代最终回读，图执行与事件流保持真实。
    async def final_state(_config):
        """标记流已耗尽。"""
        return None

    monkeypatch.setattr(graph, "aget_state", final_state)
    events = [
        event
        async for event in Agent().stream_messages_with_state(
            [HumanMessage("run")],
            context=BaseContext(uid="user", thread_id="guard-stream"),
        )
    ]
    tools = [event["params"]["data"] for event in events if not isinstance(event, GraphExecutionResult) and event["method"] == "tools"]
    unstarted = outcome in {"middleware_error", "returned_error", "command"}
    expected = ["tool-result"] if unstarted else ["tool-started", "tool-error" if outcome == "interrupt" else "tool-finished"]
    assert [event["event"] for event in tools] == expected
    assert all(event["tool_call_id"] == "call-1" for event in tools)
    if outcome != "interrupt":
        result = tool_event_output(tools[-1])
        failed = outcome in {"tool_error", "middleware_error", "returned_error", "post_error", "post_command_error", "invalid_command"}
        assert result["status"] == ("error" if failed else "success")
        if outcome in {"post_error", "post_command_error"}:
            assert result["content"] == "postprocessing failed"
        if outcome == "invalid_command":
            assert "ValueError" in result["content"]
        assert "private middleware detail" not in result["content"]
        if outcome == "returned_error":
            assert result["content"] == "permission denied"
        if outcome == "command":
            assert result["content"] == "command result"
        if outcome == "rewritten":
            assert result["content"] == "parsed:/home/user/workspace/input.pdf"
            values = [
                event["params"]["data"] for event in events if not isinstance(event, GraphExecutionResult) and event["method"] == "values"
            ]
            assert any(isinstance(message, ToolMessage) and message.content == "offloaded pointer" for message in values[-1]["messages"])


@pytest.mark.parametrize("hook", ["wrap_tool_call", "awrap_tool_call"])
@pytest.mark.parametrize("as_command", [False, True])
@pytest.mark.parametrize("original_status,final_status", [("success", "error"), ("error", "success"), ("error", "error")])
async def test_guard_publishes_final_status_and_error_text(hook, as_command, original_status, final_status):
    """同步与异步调用均以最终失败或恢复结果为准，Command 只取本次调用。"""
    from langgraph.types import Command

    from yuxi.modules.agents.services.message_recorder import tool_event_output

    original = ToolMessage(content="original result", tool_call_id="call", status=original_status)
    final = ToolMessage(content="final result", tool_call_id="call", status=final_status)

    @tool
    def execute() -> ToolMessage:
        """通过真实 BaseTool 回调返回原始结果。"""
        return original

    request = _tool_call_request({"id": "call", "name": "execute", "args": {}})
    expected = final
    if as_command:
        other = ToolMessage(content="other result", tool_call_id="other", status="error")
        expected = Command(update={"messages": [other, final]})

    def handler(req):
        """执行真实工具后返回中间件裁决。"""
        execute.invoke({**req.tool_call, "type": "tool_call"}, config=req.runtime.config)
        return expected

    async def async_handler(req):
        """异步执行真实工具后返回中间件裁决。"""
        await execute.ainvoke({**req.tool_call, "type": "tool_call"}, config=req.runtime.config)
        return expected

    returned = await _call_hook(hook, request, async_handler if hook == "awrap_tool_call" else handler)
    assert returned is expected
    [event] = [item["data"] for item in request.runtime.emitted]
    assert event["event"] == "tool-finished"
    published = tool_event_output(event)
    assert (published["status"], published["content"], published["tool_call_id"]) == (final_status, "final result", "call")
    assert (original.status, original.content) == (original_status, "original result")


async def test_tool_runtime_error_without_guard_kills_run():
    """对照组：不挂载中间件时同一异常必须打穿 run（否则上面一条没有证明力）。"""
    with pytest.raises(ValueError, match="只允许解析 /home/user/workspace 下的沙盒虚拟路径"):
        await _run_agent([], "parse_sandbox_file")


async def test_tool_error_message_binds_to_its_own_tool_call():
    """错误 ToolMessage 必须绑定同一 tool_call_id，禁止回退到猜测的关联。"""
    state = await _run_agent([ToolErrorGuardMiddleware()], "parse_sandbox_file")

    tool_calls = [call for message in state["messages"] if message.type == "ai" for call in message.tool_calls]
    tool_messages = [message for message in state["messages"] if message.type == "tool"]
    assert [call["id"] for call in tool_calls] == [message.tool_call_id for message in tool_messages]


async def test_interrupt_still_bubbles_up_through_guard():
    """GraphBubbleUp（interrupt 门禁）必须放行，不能被隔离成工具错误消息。"""
    state = await _run_agent([ToolErrorGuardMiddleware()], "ask_human")

    assert state["__interrupt__"], "interrupt 应正常冒泡并挂起 Graph"
    assert not any(message.type == "tool" for message in state["messages"])
    assert not any(message.type == "ai" and message.content for message in state["messages"])


async def test_cancelled_tool_call_is_not_isolated():
    """取消不得被隔离成工具消息，否则上层无法真正停掉这一次 run。"""
    with pytest.raises(Exception) as excinfo:  # noqa: PT011 - langgraph 会包一层 NodeCancelledError
        await _run_agent([ToolErrorGuardMiddleware()], "cancel_now")

    assert "Cancelled" in type(excinfo.value).__name__
    assert not isinstance(excinfo.value, ToolMessage)


@pytest.mark.parametrize(
    "exception",
    [asyncio.CancelledError, KeyboardInterrupt, SystemExit],
)
@pytest.mark.parametrize("hook", ["wrap_tool_call", "awrap_tool_call"])
async def test_guard_reraises_cancellation_and_process_control_exceptions(exception, hook):
    """取消与进程控制异常在包装层必须原样放行，不转换为工具消息。"""
    request = _tool_call_request({"name": "any_tool", "args": {"path": "/etc/passwd"}, "id": "call-1"})

    def raising_handler(_request):
        raise exception("must pass through")

    async def araising_handler(_request):
        raise exception("must pass through")

    handler = araising_handler if hook == "awrap_tool_call" else raising_handler
    with pytest.raises(exception):  # noqa: PT011
        await _call_hook(hook, request, handler)


@pytest.mark.parametrize("hook", ["wrap_tool_call", "awrap_tool_call"])
async def test_guard_reraises_graph_bubble_up(hook):
    """interrupt 依赖的 GraphBubbleUp 必须原样放行，否则人审门禁会被吞掉。"""
    request = _tool_call_request({"name": "any_tool", "args": {}, "id": "call-1"})

    def raising_handler(_request):
        raise GraphBubbleUp()

    async def araising_handler(_request):
        raise GraphBubbleUp()

    handler = araising_handler if hook == "awrap_tool_call" else raising_handler
    with pytest.raises(GraphBubbleUp):
        await _call_hook(hook, request, handler)


@pytest.mark.parametrize("hook", ["wrap_tool_call", "awrap_tool_call"])
async def test_guard_isolates_runtime_error(hook):
    """普通异常在包装层必须转换为带 tool_call_id 的错误 ToolMessage。"""
    request = _tool_call_request({"name": "any_tool", "args": {"path": "/etc/passwd"}, "id": "call-1"})

    def raising_handler(_request):
        raise ValueError("boom")

    async def araising_handler(_request):
        raise ValueError("boom")

    handler = araising_handler if hook == "awrap_tool_call" else raising_handler
    result = await _call_hook(hook, request, handler)

    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call-1"
    assert result.name == "any_tool"
    assert "any_tool 执行失败" in result.content
    assert result.status == "error"
    events = [item["data"] for item in request.runtime.emitted]
    assert [item["event"] for item in events] == ["tool-result"]
    assert events[-1]["output"] is result


async def test_guard_does_not_expose_exception_details(caplog):
    """异常中的凭据不能进入模型可读消息或日志。"""

    request = _tool_call_request({"name": "any_tool", "args": {}, "id": "call-1"})

    async def raising_handler(_request):
        raise RuntimeError("upstream failed: token=SECRET_SENTINEL")

    result = await _call_hook("awrap_tool_call", request, raising_handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "RuntimeError" in result.content
    assert "SECRET_SENTINEL" not in result.content
    assert "SECRET_SENTINEL" not in caplog.text
    assert "SECRET_SENTINEL" not in str(request.runtime.emitted)


async def test_graph_mounts_guard_as_outermost_tool_wrapper(monkeypatch):
    """通过公共实际装配确认异常隔离最外层、授权先于审批生效。"""
    from yuxi.modules.agents.runtime.middlewares.authorization import RuntimeAuthorizationMiddleware
    from yuxi.modules.agents.runtime.state import BaseState

    monkeypatch.setattr(runtime_graph, "create_summary_middleware_from_context", lambda *args, **kwargs: object())
    monkeypatch.setattr(runtime_graph, "create_agent", lambda **kwargs: kwargs)
    monkeypatch.setattr(runtime_graph, "get_langgraph_checkpointer", lambda _: None)
    context = SimpleNamespace(
        model="test-provider:test-model",
        tool_approval_mode="default",
        workdir_relative_path="projects/11111111-1111-4111-8111-111111111111",
    )
    graph = await runtime_graph.build_agent_graph(context, prompt_builder=lambda _: "", state_schema=BaseState, checkpoint_only=True)
    middlewares = graph["middleware"]
    assert isinstance(middlewares[-3], RuntimeAuthorizationMiddleware)
    assert isinstance(middlewares[-2], runtime_graph.ImageInputCompatibilityMiddleware)
    assert isinstance(middlewares[-1], runtime_graph.TokenUsageMiddleware)
    assert isinstance(middlewares[0], ToolErrorGuardMiddleware)


async def test_guard_keeps_normal_tool_result_untouched():
    """正常工具结果不得被改写。"""

    class _ValidPathModel(_ScriptedToolModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
            self.turn += 1
            if self.turn == 1:
                message = AIMessage(
                    content="",
                    tool_calls=[{"name": "parse_sandbox_file", "args": {"path": "/home/user/workspace/a.pdf"}, "id": "call-ok"}],
                )
            elif type(messages[-1]) is ToolMessage:
                message = AIMessage(content="已根据工具结果继续回答")
            else:
                message = AIMessage(content="final answer")
            return ChatResult(generations=[ChatGeneration(message=message)])

    agent = create_agent(model=_ValidPathModel(), tools=[parse_sandbox_file], middleware=[ToolErrorGuardMiddleware()])
    config = {"configurable": {"thread_id": "thread-ok"}, "recursion_limit": 4}
    state = await agent.ainvoke({"messages": [HumanMessage("解析这个文件")]}, config=config)

    tool_messages = [message for message in state["messages"] if message.type == "tool"]
    assert [message.content for message in tool_messages] == ["parsed:/home/user/workspace/a.pdf"]


async def test_cancel_reaches_sandbox_executor_and_waits_for_cleanup(monkeypatch):
    """取消通过 guard 到达真实 backend 的线程事件，并等待执行器清理。"""
    from yuxi.modules.agents.runtime.sandbox import backend as sandbox_module

    entered = threading.Event()
    cancelled = threading.Event()
    release = threading.Event()
    cleaned = threading.Event()

    class Provider:
        def execute(self, uid, command, cwd, timeout, cancel_event, **kwargs):
            entered.set()
            if not cancel_event.wait(10):
                raise TimeoutError("未收到取消")
            cancelled.set()
            if not release.wait(10):
                raise TimeoutError("清理未释放")
            cleaned.set()
            return "", 0, False

    monkeypatch.setattr(sandbox_module, "get_sandbox_provider", lambda: Provider())
    backend = sandbox_module.UserSandboxBackend(uid="test-user")

    async def handler(request):
        return await backend.aexecute("command")

    request = _tool_call_request({"id": "call", "name": "execute"})
    task = asyncio.create_task(ToolErrorGuardMiddleware().awrap_tool_call(request, handler))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        assert await asyncio.to_thread(cancelled.wait, 10)
        assert not task.done()
        assert not cleaned.is_set()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()


@pytest.mark.parametrize("failure_type", [RuntimeError, ValueError])
async def test_knowledge_query_failure_uses_error_message_and_public_projection(caplog, failure_type):
    """真实工具和中间件装配保留失败状态，并隐藏底层错误内容。"""
    private_detail = "private-provider-credential"
    session = SimpleNamespace(call_tool=AsyncMock(side_effect=failure_type(private_detail)))
    state = await _run_agent([ToolErrorGuardMiddleware()], "query_kb", mcp_session=session)

    [result] = [message for message in state["messages"] if isinstance(message, ToolMessage)]
    assert result.status == "error"
    assert result.tool_call_id == "call-1"
    assert normalize_tool_result(result.model_dump()).error
    assert private_detail not in result.content
    assert private_detail not in caplog.text
    assert state["messages"][-1].content == "已根据工具结果继续回答"


async def test_knowledge_empty_query_remains_success():
    """SDK 的空检索 structuredContent 经真实 adapter 后仍为成功。"""
    session = SimpleNamespace(call_tool=AsyncMock(return_value=mcp_types.CallToolResult(content=[], structuredContent={"results": []})))
    state = await _run_agent([ToolErrorGuardMiddleware()], "query_kb", mcp_session=session)
    [result] = [message for message in state["messages"] if isinstance(message, ToolMessage)]
    assert result.status == "success"
    assert result.artifact["structured_content"] == {"results": []}
    assert normalize_tool_result(result.model_dump()).error is None
    assert state["messages"][-1].content == "已根据工具结果继续回答"


async def test_knowledge_protocol_error_remains_tool_error():
    """MCP isError 经 adapter 保留单次工具失败，对话仍能继续。"""
    guidance = "Knowledge tool failed (404): 资源不存在或不可见"
    session = SimpleNamespace(
        call_tool=AsyncMock(
            return_value=mcp_types.CallToolResult(isError=True, content=[mcp_types.TextContent(type="text", text=guidance)])
        )
    )
    state = await _run_agent([ToolErrorGuardMiddleware()], "query_kb", mcp_session=session)
    [result] = [message for message in state["messages"] if isinstance(message, ToolMessage)]
    assert result.status == "error"
    assert result.tool_call_id == "call-1"
    assert guidance in str(result.content)
    assert normalize_tool_result(result.model_dump()).error
    assert state["messages"][-1].content == "已根据工具结果继续回答"
