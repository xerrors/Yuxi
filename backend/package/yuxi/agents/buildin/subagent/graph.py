from typing import Any

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from yuxi.agents import BaseAgent, BaseState
from yuxi.agents.backends import (
    create_agent_composite_backend,
    create_agent_filesystem_middleware,
    sync_agent_context_skills,
)
from yuxi.agents.buildin.chatbot.prompt import TODO_MID_PROMPT, build_prompt_with_context
from yuxi.agents.buildin.subagent.context import SubAgentContext
from yuxi.agents.context import (
    DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS,
    prepare_agent_runtime_context,
)
from yuxi.agents.middlewares import (
    ImageInputCompatibilityMiddleware,
    NetworkRetryMiddleware,
    TokenUsageMiddleware,
    create_summary_middleware_from_context,
)
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.tool_approval import SENSITIVE_BACKEND_TOOLS, normalize_tool_approval_mode
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools
from yuxi.models.chat import load_chat_model, resolve_chat_model_spec

_SUBAGENT_DISABLED_TOOLS = frozenset({"present_artifacts", "ask_user_question", "install_skill"})
# 默认审批模式额外隐藏敏感 backend 工具，避免子智能体绕过主线程逐项审批。
_SUBAGENT_DISABLED_TOOLS_DEFAULT_MODE = _SUBAGENT_DISABLED_TOOLS | SENSITIVE_BACKEND_TOOLS


def _tool_name(tool) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
    else:
        name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


def _disabled_tools_for(mode: str) -> frozenset[str]:
    # 调用方已在边界 normalize 过 mode，这里直接按值选择隐藏集合。
    if mode == "always_trust":
        return _SUBAGENT_DISABLED_TOOLS
    return _SUBAGENT_DISABLED_TOOLS_DEFAULT_MODE


def _filter_disabled_tools(tools, disabled_tools: frozenset[str]):
    return [tool for tool in tools if _tool_name(tool) not in disabled_tools]


class _SubAgentToolFilterMiddleware(AgentMiddleware[Any, Any, Any]):
    def __init__(self, tool_approval_mode: str = "default"):
        self.disabled_tools = _disabled_tools_for(tool_approval_mode)

    def wrap_model_call(self, request, handler):
        return handler(request.override(tools=_filter_disabled_tools(request.tools or [], self.disabled_tools)))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(tools=_filter_disabled_tools(request.tools or [], self.disabled_tools)))

    # 工具列表隐藏不构成执行边界；显式传入的禁用工具调用也必须拒绝。
    def wrap_tool_call(self, request, handler):
        denial = self._denied_tool_message(request)
        return denial if denial is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        denial = self._denied_tool_message(request)
        return denial if denial is not None else await handler(request)

    def _denied_tool_message(self, request) -> ToolMessage | None:
        """为禁用调用生成与原 tool call 绑定的拒绝结果。"""
        name = _tool_name(request.tool_call)
        if name not in self.disabled_tools:
            return None
        return ToolMessage(
            content=(
                f"工具 {name} 在当前审批模式下对子智能体不可用；请把结果交回主智能体，由主线程按审批流程执行该操作。"
            ),
            tool_call_id=request.tool_call.get("id") or "",
            name=name,
            status="error",
        )


async def _build_middlewares(context, backend, tool_approval_mode: str):
    # tool_approval_mode is normalized once by the caller (get_graph / SubAgentBackend.get_graph).

    return [
        create_agent_filesystem_middleware(
            getattr(context, "tool_token_limit", DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS) * 1024,
            backend=backend,
            disabled_tools=_disabled_tools_for(tool_approval_mode),
        ),
        SkillsMiddleware(),
        create_summary_middleware_from_context(context, backend=backend),
        TodoListMiddleware(system_prompt=TODO_MID_PROMPT),
        PatchToolCallsMiddleware(),
        _SubAgentToolFilterMiddleware(tool_approval_mode),
        NetworkRetryMiddleware(),
        ImageInputCompatibilityMiddleware(),
        TokenUsageMiddleware(),
    ]


class SubAgentBackend(BaseAgent):
    name = "子智能体"
    description = "用于被主智能体通过 task 工具调用的专用智能体后端。"
    capabilities = ["file_upload", "files"]
    context_schema = SubAgentContext

    async def get_info(
        self,
        include_configurable_items: bool = True,
        user_role: str | None = None,
        db=None,
        user=None,
    ):
        info = await super().get_info(
            include_configurable_items=include_configurable_items,
            user_role=user_role,
            db=db,
            user=user,
        )
        tools_item = (info.get("configurable_items") or {}).get("tools")
        if isinstance(tools_item, dict):
            tools_item["options"] = [
                option
                for option in tools_item.get("options") or []
                if option.get("key") not in _SUBAGENT_DISABLED_TOOLS
            ]
        return info

    async def get_graph(self, context=None, **kwargs):
        context = await prepare_agent_runtime_context(
            context or self.context_schema(),
            context_schema=self.context_schema,
        )
        await sync_agent_context_skills(context)
        model_spec = resolve_chat_model_spec(context.model)
        tool_approval_mode = normalize_tool_approval_mode(getattr(context, "tool_approval_mode", "default"))
        disabled_tools = _disabled_tools_for(tool_approval_mode)
        backend = create_agent_composite_backend(context)

        return create_agent(
            model=load_chat_model(fully_specified_name=model_spec, session_id=context.thread_id),
            tools=_filter_disabled_tools(await resolve_configured_runtime_tools(context), disabled_tools),
            system_prompt=build_prompt_with_context(context),
            middleware=await _build_middlewares(context, backend, tool_approval_mode),
            state_schema=BaseState,
            checkpointer=await self._get_checkpointer(),
        )
