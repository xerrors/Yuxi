from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware

from yuxi.agents import BaseAgent
from yuxi.agents.backends import (
    create_agent_composite_backend,
    create_agent_filesystem_middleware,
    sync_agent_context_skills,
)
from yuxi.agents.backends.paths import runtime_workdir_path
from yuxi.agents.context import (
    DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS,
    prepare_agent_runtime_context,
)
from yuxi.agents.middlewares import (
    ImageInputCompatibilityMiddleware,
    NetworkRetryMiddleware,
    SteerMiddleware,
    TokenUsageMiddleware,
    create_memory_middleware,
    create_summary_middleware_from_context,
    retry_non_network_errors,
)
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.middlewares.subagent_task import create_subagent_task_middleware
from yuxi.agents.tool_approval import create_tool_approval_middleware, normalize_tool_approval_mode
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools
from yuxi.models.chat import load_chat_model, resolve_chat_model_spec

from .context import ChatBotContext
from .prompt import TODO_MID_PROMPT, build_prompt_with_context
from .state import ChatBotState


async def _build_middlewares(context, backend):
    """构建中间件列表"""
    middlewares = [
        SteerMiddleware(),
        create_agent_filesystem_middleware(
            getattr(context, "tool_token_limit", DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS) * 1024,
            backend=backend,
        ),
        SkillsMiddleware(),
    ]
    memory_middleware = await create_memory_middleware(context)
    if memory_middleware:
        middlewares.append(memory_middleware)
    subagent_middleware = await create_subagent_task_middleware(context)
    if subagent_middleware:
        middlewares.append(subagent_middleware)
    middlewares.extend(
        [
            create_summary_middleware_from_context(context, backend=backend),
            TodoListMiddleware(system_prompt=TODO_MID_PROMPT),
            PatchToolCallsMiddleware(),
            # 网络类错误(断网/连接抖动)由内层 NetworkRetryMiddleware 按单次预算(默认600s)
            # 退避重试。langchain 中间件列表排后者为内层(先拦截)：
            #  - NetworkRetry 必须在 ModelRetry 之内，否则 ModelRetry 的 on_failure=continue
            #    会先把异常吞成错误 AIMessage，网络错误永远到不了 NetworkRetry；
            #  - ModelRetry 必须用 retry_on 排除网络错误，否则它会给同一次调用反复开启
            #    全新预算(600s × (max_retries+1))，且最终把耗尽后的网络错误吞成"假完成"。
            ModelRetryMiddleware(
                max_retries=getattr(context, "model_retry_times", 2),
                retry_on=retry_non_network_errors,
            ),
            NetworkRetryMiddleware(),
            ImageInputCompatibilityMiddleware(),
            TokenUsageMiddleware(),
        ]
    )
    approval_middleware = create_tool_approval_middleware(
        normalize_tool_approval_mode(getattr(context, "tool_approval_mode", "default")),
        current_project_path=runtime_workdir_path(context.workdir_relative_path),
    )
    if approval_middleware:
        middlewares.append(approval_middleware)
    return middlewares


class ChatbotAgent(BaseAgent):
    name = "智能助手"
    description = "基础的对话机器人，可以回答问题，可在配置中启用需要的工具。"
    capabilities = ["file_upload", "files", "context_compression"]
    context_schema = ChatBotContext

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def get_graph(self, context=None, **kwargs):
        context = await prepare_agent_runtime_context(
            context or self.context_schema(),
            context_schema=self.context_schema,
        )
        await sync_agent_context_skills(context)

        # DeepAgents 0.7 移除 backend factory：每次 graph 构造创建本 Run 独享的
        # CompositeBackend，filesystem 与 summary middleware 共用同一实例。
        backend = create_agent_composite_backend(context)
        model_spec = resolve_chat_model_spec(context.model)
        graph = create_agent(
            model=load_chat_model(fully_specified_name=model_spec, session_id=context.thread_id),
            tools=await resolve_configured_runtime_tools(context),
            system_prompt=build_prompt_with_context(context),
            middleware=await _build_middlewares(context, backend),
            state_schema=ChatBotState,
            checkpointer=await self._get_checkpointer(),
        )
        return graph


def main():
    pass


if __name__ == "__main__":
    main()
    # asyncio.run(main())
