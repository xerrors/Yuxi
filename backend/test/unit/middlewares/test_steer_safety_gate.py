"""Steer 实施 Gate：验证 LangGraph 的工具后 ``before_model`` 安全点。"""

from __future__ import annotations

import asyncio

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import add_messages
from yuxi.services.input_message_service import restore_chat_input_message

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _ParallelToolModel(BaseChatModel):
    """首次模型调用生成两个并行工具；安全接替后不应再次调用模型。"""

    call_count: int = 0

    @property
    def _llm_type(self) -> str:
        return "steer-safety-gate"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        self.call_count += 1
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {"id": "call-one", "name": "gate_tool_one", "args": {}},
                            {"id": "call-two", "name": "gate_tool_two", "args": {}},
                        ],
                    )
                )
            ]
        )


class _SafetyPointMiddleware(AgentMiddleware):
    """测试专用安全点：Steer 到达后在下一次模型调用前结束 Graph。"""

    def __init__(self) -> None:
        self.steer_requested = False
        self.messages_at_safe_point: list = []

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):  # noqa: ARG002
        if not self.steer_requested:
            return None
        self.messages_at_safe_point = list(state["messages"])
        return {"jump_to": "end"}


async def test_parallel_tools_finish_before_steer_ends_graph_and_checkpoint_is_complete():
    """Steer 只能在整批工具结果进入 checkpoint 后阻止下一次模型调用。"""
    both_started = asyncio.Event()
    release_tools = asyncio.Event()
    started = 0
    started_lock = asyncio.Lock()

    async def wait_for_release(result: str) -> str:
        nonlocal started
        async with started_lock:
            started += 1
            if started == 2:
                both_started.set()
        await release_tools.wait()
        return result

    @tool
    async def gate_tool_one() -> str:
        """返回第一个 Gate 工具结果。"""
        return await wait_for_release("result-one")

    @tool
    async def gate_tool_two() -> str:
        """返回第二个 Gate 工具结果。"""
        return await wait_for_release("result-two")

    model = _ParallelToolModel()
    middleware = _SafetyPointMiddleware()
    checkpointer = InMemorySaver()
    agent = create_agent(
        model=model,
        tools=[gate_tool_one, gate_tool_two],
        middleware=[middleware],
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": "steer-gate-thread"}}

    async def consume_graph() -> None:
        async for _ in agent.astream(
            {"messages": [HumanMessage("执行两个工具")]},
            config=config,
            stream_mode="updates",
        ):
            pass

    graph_task = asyncio.create_task(consume_graph())
    await asyncio.wait_for(both_started.wait(), timeout=5)
    middleware.steer_requested = True
    release_tools.set()
    await asyncio.wait_for(graph_task, timeout=5)

    safe_point_results = {
        message.tool_call_id: message.content
        for message in middleware.messages_at_safe_point
        if isinstance(message, ToolMessage)
    }
    persisted_state = await agent.aget_state(config)
    checkpoint_results = {
        message.tool_call_id: message.content
        for message in persisted_state.values["messages"]
        if isinstance(message, ToolMessage)
    }

    assert model.call_count == 1
    assert safe_point_results == {"call-one": "result-one", "call-two": "result-two"}
    assert checkpoint_results == safe_point_results


async def test_retrying_legacy_input_reuses_stable_langgraph_message_id():
    """Steer 标 ready 前的 job 重试不会把旧用户输入再次追加到 checkpoint。"""
    metadata = {
        "request_id": "retry-request",
        "raw_message": {"type": "human", "content": "原任务"},
    }
    first = restore_chat_input_message(content="原任务", image_content=None, metadata=metadata)
    retried = restore_chat_input_message(content="原任务", image_content=None, metadata=metadata)

    messages = add_messages([], [first.require_langchain_message()])
    messages = add_messages(messages, [retried.require_langchain_message()])

    assert len(messages) == 1
    assert messages[0].id == "request:retry-request"
