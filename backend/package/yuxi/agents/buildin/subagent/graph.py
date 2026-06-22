from typing import Any

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware
from langchain.agents.middleware.types import AgentMiddleware

from yuxi.agents import BaseAgent, BaseState, load_chat_model, resolve_chat_model_spec
from yuxi.agents.backends import create_agent_filesystem_middleware
from yuxi.agents.buildin.chatbot.prompt import TODO_MID_PROMPT, build_prompt_with_context
from yuxi.agents.buildin.subagent.context import SubAgentContext
from yuxi.agents.context import (
    DEFAULT_SUMMARY_KEEP_MESSAGES,
    DEFAULT_SUMMARY_THRESHOLD_K,
    DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
    DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS,
    DEFAULT_YUXI_SUMMARY_PROMPT,
    prepare_agent_runtime_context,
)
from yuxi.agents.middlewares import TokenUsageMiddleware, create_summary_middleware, save_attachments_to_fs
from yuxi.agents.middlewares.knowledge_base import KnowledgeBaseMiddleware
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools

_SUBAGENT_DISABLED_TOOLS = frozenset({"present_artifacts", "ask_user_question", "install_skill"})


def _tool_name(tool) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
    else:
        name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


def _filter_disabled_tools(tools):
    return [tool for tool in tools if _tool_name(tool) not in _SUBAGENT_DISABLED_TOOLS]


class _SubAgentToolFilterMiddleware(AgentMiddleware[Any, Any, Any]):
    def wrap_model_call(self, request, handler):
        return handler(request.override(tools=_filter_disabled_tools(request.tools or [])))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(tools=_filter_disabled_tools(request.tools or [])))


async def _build_middlewares(context):
    summary_trigger_tokens = getattr(context, "summary_threshold", DEFAULT_SUMMARY_THRESHOLD_K) * 1024
    summary_keep_messages = getattr(context, "summary_keep_messages", DEFAULT_SUMMARY_KEEP_MESSAGES)
    summary_prompt = getattr(context, "summary_prompt", None) or DEFAULT_YUXI_SUMMARY_PROMPT
    summary_tool_result_token_limit = getattr(
        context,
        "summary_tool_result_token_limit",
        DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
    )
    model_spec = resolve_chat_model_spec(context.model)
    summary_middleware = create_summary_middleware(
        model=load_chat_model(fully_specified_name=model_spec),
        trigger=("tokens", summary_trigger_tokens),
        keep=("messages", summary_keep_messages),
        summary_prompt=summary_prompt,
        trim_tokens_to_summarize=4000,
        tool_result_offload_token_limit=summary_tool_result_token_limit,
    )

    return [
        create_agent_filesystem_middleware(
            getattr(context, "tool_token_limit", DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS) * 1024,
            context=context,
        ),
        save_attachments_to_fs,
        KnowledgeBaseMiddleware(),
        SkillsMiddleware(),
        summary_middleware,
        TodoListMiddleware(system_prompt=TODO_MID_PROMPT),
        PatchToolCallsMiddleware(),
        _SubAgentToolFilterMiddleware(),
        ModelRetryMiddleware(),
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
        model_spec = resolve_chat_model_spec(context.model)

        return create_agent(
            model=load_chat_model(fully_specified_name=model_spec),
            tools=_filter_disabled_tools(await resolve_configured_runtime_tools(context)),
            system_prompt=build_prompt_with_context(context),
            middleware=await _build_middlewares(context),
            state_schema=BaseState,
            checkpointer=await self._get_checkpointer(),
        )
