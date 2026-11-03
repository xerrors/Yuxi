from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.stream.transformers import CustomTransformer

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.agents.runtime.context import DEFAULT_MAX_EXECUTION_STEPS, BaseContext, resolve_agent_resource_options


def json_safe(value: Any) -> Any:
    """把工具事件值转换成可序列化的数据。"""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(child) for child in value]
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump())
    return str(value)


@dataclass(frozen=True, slots=True)
class GraphExecutionResult:
    """携带同一图已经提交的 checkpoint，不进入公开事件流。"""

    checkpoint: Any
    steer_before_model: bool = False


def _recursion_limit_from_context(context: BaseContext, default: int) -> int:
    value = getattr(context, "max_execution_steps", default)
    return int(value) if isinstance(value, int) and value > 0 else default


class BaseAgent(ABC):
    """
    定义一个基础 Agent 供 各类 graph 继承
    """

    name = "base_agent"
    description = "base_agent"
    context_schema: type[BaseContext] = BaseContext  # 智能体上下文 schema

    @property
    def module_name(self) -> str:
        """Get the module name of the agent class."""
        return self.__class__.__module__.split(".")[-2]

    async def get_info(
        self,
        include_configurable_items: bool = True,
        user_role: str | None = None,
        db=None,
        user=None,
    ):
        # metadata 固定在代码中，由各 Agent 的类属性提供
        metadata = self.load_metadata()
        configurable_items = {}
        if include_configurable_items:
            configurable_items = self.context_schema.get_configurable_items(user_role=user_role)
            if db is not None and user is not None:
                resource_fields = {item["kind"] for item in configurable_items.values() if item.get("supports_all")}
                resource_options = await resolve_agent_resource_options(resource_fields, db=db, user=user)
                for item in configurable_items.values():
                    if item.get("kind") in resource_options:
                        item["options"] = resource_options[item["kind"]]

        # Merge metadata with class attributes, metadata takes precedence
        return {
            "name": getattr(self, "name", "Unknown"),
            "description": getattr(self, "description", "Unknown"),
            "metadata": metadata,
            "configurable_items": configurable_items,
        }

    async def _stream_input_with_state(
        self,
        graph_input,
        *,
        context: BaseContext,
        callbacks=None,
        metadata=None,
        tags=None,
        run_name=None,
        on_prepared=None,
    ):
        graph = await self.get_graph(context=context)
        logger.debug(f"stream_with_state: {context=}")

        input_config = {
            "configurable": {"thread_id": context.thread_id, "uid": context.uid},
            "recursion_limit": _recursion_limit_from_context(context, DEFAULT_MAX_EXECUTION_STEPS),
        }

        if callbacks:
            input_config["callbacks"] = list(callbacks)
        if metadata:
            input_config["metadata"] = dict(metadata)
        if tags:
            input_config["tags"] = list(tags)
        if run_name:
            input_config["run_name"] = run_name

        context.steer_before_model = False
        async with await graph.astream_events(
            graph_input,
            context=context,
            config=input_config,
            version="v3",
            transformers=[CustomTransformer],
        ) as run:
            if on_prepared:
                await on_prepared()
            async for event in run:
                method, params = event["method"], event["params"]
                if method == "tools" and params["data"].get("event") in {"tool-finished", "tool-error"}:
                    # 执行图须装配最外层 Guard：保留真实开始和进度，由 Guard 发布最终结局。
                    continue
                payload = params.get("data")
                if method == "custom" and isinstance(payload, dict) and payload.get("type") == "yuxi.tool_lifecycle":
                    event = {**event, "method": "tools", "params": {**params, "data": payload["data"]}}
                yield event

        yield GraphExecutionResult(checkpoint=await graph.aget_state(input_config), steer_before_model=context.steer_before_model)

    async def stream_messages_with_state(self, messages: list[str], *, context: BaseContext, **kwargs):
        graph_input = {"messages": messages}
        async with aclosing(self._stream_input_with_state(graph_input, context=context, **kwargs)) as stream:
            async for event in stream:
                yield event

    async def stream_resume_with_state(self, resume_input, *, context: BaseContext, **kwargs):
        async with aclosing(self._stream_input_with_state(resume_input, context=context, **kwargs)) as stream:
            async for event in stream:
                yield event

    @abstractmethod
    async def get_graph(self, **kwargs) -> CompiledStateGraph:
        """
        获取并编译对话图实例。
        必须确保在编译时设置 checkpointer，否则将无法获取历史记录。
        例如: graph = workflow.compile(checkpointer=checkpointer)
        """
        pass

    def load_metadata(self) -> dict:
        """Load metadata from agent class attribute."""
        metadata = getattr(self, "metadata", {})
        if isinstance(metadata, dict):
            return metadata
        logger.warning(f"Agent {self.module_name} metadata is not a dict, fallback to empty metadata")
        return {}
