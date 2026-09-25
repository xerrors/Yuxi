from __future__ import annotations

import asyncio
from abc import abstractmethod
from contextlib import aclosing, suppress
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.stream.transformers import CustomTransformer
from langgraph.types import Command

from yuxi.agents.context import DEFAULT_MAX_EXECUTION_STEPS, BaseContext, resolve_agent_resource_options
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger
from yuxi.utils.hash_utils import subagent_child_thread_id
from yuxi.utils.thread_utils import extract_thread_id as _metadata_thread_id


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(child) for child in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    return str(value)


def _normalize_tool_event_data(data: Any) -> Any:
    """规整 tools 流事件：write_todos / task 等返回 Command 的工具，其 tool-finished
    output 是 Command 对象，_json_safe 只能退化成 repr 字符串，前端无法关联结果。
    这里从 Command.update["messages"] 取出真正的 ToolMessage，使其与普通工具一致。"""
    if not isinstance(data, dict) or data.get("event") != "tool-finished":
        return data
    output = data.get("output")
    if not isinstance(output, Command):
        return data
    update = output.update if isinstance(output.update, dict) else {}
    messages = update.get("messages")
    if not isinstance(messages, list):
        return data
    tool_call_id = data.get("tool_call_id")
    tool_message = next(
        (m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == tool_call_id),
        next((m for m in messages if isinstance(m, ToolMessage)), None),
    )
    if tool_message is None:
        return data
    return {**data, "output": tool_message}


def _subagent_route_for_namespace(
    routes: dict[tuple[str, ...], dict[str, str]], namespace: list[str]
) -> dict[str, str] | None:
    ns = tuple(namespace)
    for path, route in sorted(routes.items(), key=lambda item: len(item[0]), reverse=True):
        if ns[: len(path)] == path:
            return route
    return None


async def _collect_subagent_routes(run, parent_thread_id: str, routes: dict[tuple[str, ...], dict[str, str]]) -> None:
    subagents = getattr(run, "subagents", None)
    if subagents is None:
        return

    try:
        async for subagent in subagents:
            path = tuple(getattr(subagent, "path", ()) or ())
            subagent_slug = getattr(subagent, "name", None) or getattr(subagent, "graph_name", None)
            cause = getattr(subagent, "cause", None)
            tool_call_id = (
                cause.get("tool_call_id") if isinstance(cause, dict) else getattr(subagent, "trigger_call_id", None)
            )
            state = getattr(subagent, "state", None)
            metadata = getattr(subagent, "metadata", None)
            thread_id = _metadata_thread_id(metadata) or _metadata_thread_id(state)
            if not thread_id and isinstance(subagent_slug, str) and isinstance(tool_call_id, str) and tool_call_id:
                thread_id = subagent_child_thread_id(parent_thread_id, subagent_slug, tool_call_id)
            if path and isinstance(subagent_slug, str) and isinstance(tool_call_id, str) and tool_call_id and thread_id:
                routes[path] = {
                    "thread_id": thread_id,
                    "parent_thread_id": parent_thread_id,
                    "subagent_slug": subagent_slug,
                    "tool_call_id": tool_call_id,
                }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug(f"collect subagent stream routes failed: {exc}")


def _recursion_limit_from_context(context: BaseContext, default: int) -> int:
    value = getattr(context, "max_execution_steps", default)
    return int(value) if isinstance(value, int) and value > 0 else default


class BaseAgent:
    """
    定义一个基础 Agent 供 各类 graph 继承
    """

    name = "base_agent"
    description = "base_agent"
    capabilities: list[str] = []  # 智能体能力列表，如 ["file_upload", "web_search"] 等
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
                resource_fields = {
                    item["kind"]
                    for item in configurable_items.values()
                    if item.get("kind") in {"tools", "knowledges", "mcps", "skills", "subagents"}
                }
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
            "capabilities": getattr(self, "capabilities", []),  # 智能体能力列表
        }

    async def stream_messages(
        self, messages: list[str], *, context: BaseContext, callbacks=None, metadata=None, tags=None, run_name=None
    ):
        graph = await self.get_graph(context=context)
        logger.debug(f"stream_messages: {context=}")

        # 构建配置：LangGraph 会自动从 checkpointer 恢复 state
        input_config = {
            "configurable": {"thread_id": context.thread_id, "uid": context.uid},
            "recursion_limit": _recursion_limit_from_context(context, DEFAULT_MAX_EXECUTION_STEPS),
        }

        # langfuse metadata and callbacks integration
        if callbacks:
            input_config["callbacks"] = list(callbacks)
        if metadata:
            input_config["metadata"] = dict(metadata)
        if tags:
            input_config["tags"] = list(tags)
        # run_name 让 Langfuse/LangSmith 等 tracer 用智能体名而非默认的 "LangGraph" 命名 trace
        if run_name:
            input_config["run_name"] = run_name

        async for msg, metadata in graph.astream(
            {"messages": messages},
            stream_mode="messages",
            context=context,
            config=input_config,
        ):
            yield msg, metadata

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

        async with await graph.astream_events(
            graph_input,
            context=context,
            config=input_config,
            version="v3",
            transformers=[CustomTransformer],
        ) as run:
            if on_prepared:
                await on_prepared()
            subagent_routes: dict[tuple[str, ...], dict[str, str]] = {}
            route_task = asyncio.create_task(_collect_subagent_routes(run, context.thread_id, subagent_routes))
            try:
                async for event in run:
                    params = event.get("params") or {}
                    namespace = list(params.get("namespace") or [])
                    method = event.get("method")
                    data = params.get("data")
                    sequence = event.get("seq")
                    timestamp = params.get("timestamp")
                    subagent_route = _subagent_route_for_namespace(subagent_routes, namespace)

                    if method == "custom":
                        yield "custom", data
                        continue
                    if method == "messages":
                        msg, metadata = data
                        metadata = dict(metadata or {})
                        actual_thread_id = (subagent_route or {}).get("thread_id") or _metadata_thread_id(metadata)
                        metadata["namespace"] = namespace
                        metadata["stream_event"] = {
                            "method": method,
                            "namespace": namespace,
                            "seq": sequence,
                            "timestamp": timestamp,
                        }
                        if subagent_route:
                            metadata.update(subagent_route)
                        if actual_thread_id:
                            metadata["thread_id"] = actual_thread_id
                        yield "messages", (msg, metadata)
                    elif method == "values" and not namespace:
                        yield "values", data
                    elif method in {"tasks", "tools", "lifecycle"}:
                        if method == "tools":
                            data = _normalize_tool_event_data(data)
                        event_payload = {
                            "method": method,
                            "namespace": namespace,
                            "seq": sequence,
                            "timestamp": timestamp,
                            "data": _json_safe(data),
                        }
                        actual_thread_id = (subagent_route or {}).get("thread_id") or _metadata_thread_id(params)
                        if subagent_route:
                            event_payload.update(subagent_route)
                        if actual_thread_id:
                            event_payload["thread_id"] = actual_thread_id
                        yield "stream_event", event_payload
            finally:
                route_task.cancel()
                with suppress(asyncio.CancelledError):
                    await route_task

        # 流已耗尽、checkpoint 写入已完成；收尾消费者共享本次图的持久状态。
        yield "checkpoint", await graph.aget_state(input_config)

    async def stream_messages_with_state(self, messages: list[str], *, context: BaseContext, **kwargs):
        graph_input = {"messages": messages}
        async with aclosing(self._stream_input_with_state(graph_input, context=context, **kwargs)) as stream:
            async for event in stream:
                yield event

    async def stream_resume_with_state(self, resume_input, *, context: BaseContext, **kwargs):
        async with aclosing(self._stream_input_with_state(resume_input, context=context, **kwargs)) as stream:
            async for event in stream:
                yield event

    async def invoke_messages(
        self, messages: list[str], *, context: BaseContext, callbacks=None, metadata=None, tags=None, run_name=None
    ):
        graph = await self.get_graph(context=context)
        logger.debug(f"invoke_messages: {context}")

        # 构建配置
        input_config = {
            "configurable": {"thread_id": context.thread_id, "uid": context.uid},
            "recursion_limit": _recursion_limit_from_context(context, DEFAULT_MAX_EXECUTION_STEPS),
        }

        # langfuse metadata and callbacks integration
        if callbacks:
            input_config["callbacks"] = list(callbacks)
        if metadata:
            input_config["metadata"] = dict(metadata)
        if tags:
            input_config["tags"] = list(tags)
        if run_name:
            input_config["run_name"] = run_name

        msg = await graph.ainvoke(
            {"messages": messages},
            context=context,
            config=input_config,
        )
        return msg

    @abstractmethod
    async def get_graph(self, **kwargs) -> CompiledStateGraph:
        """
        获取并编译对话图实例。
        必须确保在编译时设置 checkpointer，否则将无法获取历史记录。
        例如: graph = workflow.compile(checkpointer=checkpointer)
        """
        pass

    async def _get_checkpointer(self):
        """每次构图独享 saver，避免全局 Agent 缓存把不同用户的 I/O 串行化。"""
        return pg_manager.get_langgraph_checkpointer()

    def load_metadata(self) -> dict:
        """Load metadata from agent class attribute."""
        metadata = getattr(self, "metadata", {})
        if isinstance(metadata, dict):
            return metadata
        logger.warning(f"Agent {self.module_name} metadata is not a dict, fallback to empty metadata")
        return {}
