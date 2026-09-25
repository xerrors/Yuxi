"""ToolErrorGuardMiddleware 单元测试。

背景：langgraph 的 ToolNode 只把参数校验类错误转成工具消息，工具体内抛出的其它
异常会原样 raise → 整个 agent run 异常终局 → 前端"对话出错"，工具卡永久停在
"执行中"。ToolErrorGuardMiddleware 在工具调用包装层兜底，把运行时异常隔离为错误
ToolMessage，同时放行 GraphBubbleUp（interrupt 门禁）与取消/进程控制异常。

测试用 fake 模型 + 真实 create_agent 装配验证契约，不依赖网络与外部服务。
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt
from yuxi.agents.buildin.chatbot import graph as chatbot_graph
from yuxi.agents.buildin.subagent import graph as subagent_graph
from yuxi.agents.middlewares import ToolErrorGuardMiddleware

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@tool
def parse_sandbox_file(path: str) -> str:
    """解析沙盒文件，路径域不匹配时抛异常（复现线上事故的工具行为）。"""
    if not path.startswith("/home/gem/user-data/"):
        raise ValueError(f"只允许解析 /home/gem/user-data 下的沙盒虚拟路径，收到 {path}")
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
}


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
    return ToolCallRequest(tool_call=tool_call, tool=None, state=None, runtime=SimpleNamespace(context=None))


async def _call_hook(hook: str, request: ToolCallRequest, handler):
    """按 hook 调同步/异步入口，统一为协程结果。"""
    result = getattr(ToolErrorGuardMiddleware(), hook)(request, handler)
    if inspect.isawaitable(result):
        return await result
    return result


async def _run_agent(middleware, tool_name: str) -> dict:
    model = _ScriptedToolModel(tool_name=tool_name)
    agent = create_agent(
        model=model,
        tools=[parse_sandbox_file, ask_human, cancel_now],
        middleware=list(middleware),
    )
    config = {"configurable": {"thread_id": f"thread-{tool_name}"}, "recursion_limit": 6}
    return await agent.ainvoke({"messages": [HumanMessage("解析这个文件")]}, config=config)


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


async def test_tool_runtime_error_without_guard_kills_run():
    """对照组：不挂载中间件时同一异常必须打穿 run（否则上面一条没有证明力）。"""
    with pytest.raises(ValueError, match="只允许解析 /home/gem/user-data 下的沙盒虚拟路径"):
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


@pytest.mark.parametrize(
    "module,build_kwargs",
    [
        (chatbot_graph, {"extra_async_patches": ("create_memory_middleware", "create_subagent_task_middleware")}),
        (subagent_graph, {"tool_approval_mode": "default", "extra_async_patches": ()}),
    ],
)
async def test_graph_mounts_guard_as_outermost_tool_wrapper(monkeypatch, module, build_kwargs):
    """主 Agent 与子 Agent 都必须挂载异常隔离，且位于最外层包装位置。

    langchain 的中间件约定是列表中第一个为最外层；只有最外层才能在其它中间件与
    工具体之后兜住异常，同时仍然让 interrupt 冒泡出去。
    """
    monkeypatch.setattr(module, "create_agent_filesystem_middleware", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "create_summary_middleware_from_context", lambda _context, **_kwargs: object())

    async def no_optional_middleware(_context):
        return None

    for name in build_kwargs["extra_async_patches"]:
        monkeypatch.setattr(module, name, no_optional_middleware)

    context = SimpleNamespace(
        model="test-provider:test-model",
        tool_approval_mode="default",
        workdir_relative_path="projects/11111111-1111-4111-8111-111111111111",
        tool_token_limit=8,
    )
    tool_approval_mode = build_kwargs.get("tool_approval_mode")
    if tool_approval_mode is None:
        middlewares = await module._build_middlewares(context, object())
    else:
        middlewares = await module._build_middlewares(context, object(), tool_approval_mode)

    assert isinstance(middlewares[0], ToolErrorGuardMiddleware), "异常隔离必须是工具调用的最外层包装"


async def test_guard_keeps_normal_tool_result_untouched():
    """正常工具结果不得被改写。"""

    class _ValidPathModel(_ScriptedToolModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
            self.turn += 1
            if self.turn == 1:
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "parse_sandbox_file", "args": {"path": "/home/gem/user-data/a.pdf"}, "id": "call-ok"}
                    ],
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
    assert [message.content for message in tool_messages] == ["parsed:/home/gem/user-data/a.pdf"]
