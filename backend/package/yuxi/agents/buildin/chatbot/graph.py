from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware

from yuxi.agents import BaseAgent, load_chat_model, resolve_chat_model_spec
from yuxi.agents.backends import create_agent_filesystem_middleware
from yuxi.agents.context import (
    DEFAULT_SUMMARY_KEEP_MESSAGES,
    DEFAULT_SUMMARY_L2_TRIGGER_RATIO,
    DEFAULT_SUMMARY_THRESHOLD_K,
    DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
    DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS,
    DEFAULT_YUXI_SUMMARY_PROMPT,
    prepare_agent_runtime_context,
)
from yuxi.agents.middlewares import (
    ImageInputCompatibilityMiddleware,
    SteerMiddleware,
    TokenUsageMiddleware,
    create_summary_middleware,
    save_attachments_to_fs,
)
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.middlewares.subagent_task import create_subagent_task_middleware
from yuxi.agents.tool_approval import create_tool_approval_middleware, normalize_tool_approval_mode
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools

from .context import ChatBotContext
from .prompt import TODO_MID_PROMPT, build_prompt_with_context
from .state import ChatBotState


async def _build_middlewares(context):
    """构建中间件列表"""
    # summary middleware
    # 主 Agent 上下文优化：默认 100k tokens 触发压缩，压缩后保留最近 10 条消息
    summary_trigger_tokens = getattr(context, "summary_threshold", DEFAULT_SUMMARY_THRESHOLD_K) * 1024
    summary_keep_messages = getattr(context, "summary_keep_messages", DEFAULT_SUMMARY_KEEP_MESSAGES)
    summary_prompt = getattr(context, "summary_prompt", None) or DEFAULT_YUXI_SUMMARY_PROMPT
    summary_tool_result_token_limit = getattr(
        context,
        "summary_tool_result_token_limit",
        DEFAULT_SUMMARY_TOOL_RESULT_TOKEN_LIMIT,
    )
    summary_l2_trigger_ratio = getattr(context, "summary_l2_trigger_ratio", DEFAULT_SUMMARY_L2_TRIGGER_RATIO)
    model_spec = resolve_chat_model_spec(context.model)
    summary_middleware = create_summary_middleware(
        model=load_chat_model(fully_specified_name=model_spec),
        trigger=("tokens", summary_trigger_tokens),
        keep=("messages", summary_keep_messages),
        summary_prompt=summary_prompt,
        trim_tokens_to_summarize=summary_trigger_tokens,
        tool_result_offload_token_limit=summary_tool_result_token_limit,
        l1_l2_trigger_ratio=summary_l2_trigger_ratio,
    )

    middlewares = [
        SteerMiddleware(),
        create_agent_filesystem_middleware(
            getattr(context, "tool_token_limit", DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS) * 1024,
            context=context,
        ),
        save_attachments_to_fs,
        SkillsMiddleware(),
    ]
    subagent_middleware = await create_subagent_task_middleware(context)
    if subagent_middleware:
        middlewares.append(subagent_middleware)
    middlewares.extend(
        [
            summary_middleware,
            TodoListMiddleware(system_prompt=TODO_MID_PROMPT),
            PatchToolCallsMiddleware(),
            ModelRetryMiddleware(max_retries=getattr(context, "model_retry_times", 2)),
            ImageInputCompatibilityMiddleware(),
            TokenUsageMiddleware(),
        ]
    )
    approval_middleware = create_tool_approval_middleware(
        normalize_tool_approval_mode(getattr(context, "tool_approval_mode", "default"))
    )
    if approval_middleware:
        middlewares.append(approval_middleware)
    return middlewares


class ChatbotAgent(BaseAgent):
    name = "智能助手"
    description = "基础的对话机器人，可以回答问题，可在配置中启用需要的工具。"
    capabilities = ["file_upload", "files"]  # 支持文件上传功能
    context_schema = ChatBotContext

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def get_graph(self, context=None, **kwargs):

        context = await prepare_agent_runtime_context(
            context or self.context_schema(),
            context_schema=self.context_schema,
        )

        # 使用 create_agent 创建智能体
        model_spec = resolve_chat_model_spec(context.model)
        graph = create_agent(
            model=load_chat_model(fully_specified_name=model_spec),
            tools=await resolve_configured_runtime_tools(context),
            system_prompt=build_prompt_with_context(context),
            middleware=await _build_middlewares(context),
            state_schema=ChatBotState,
            checkpointer=await self._get_checkpointer(),
        )

        return graph


def main():
    pass


if __name__ == "__main__":
    main()
    # asyncio.run(main())
