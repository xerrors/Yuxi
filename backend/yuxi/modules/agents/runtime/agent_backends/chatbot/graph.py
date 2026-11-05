from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent

from yuxi.infrastructure.postgres.checkpointer import get_langgraph_checkpointer
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
from yuxi.modules.agents.runtime.agent_backends.chatbot.prompt import build_prompt_with_context
from yuxi.modules.agents.runtime.agent_backends.chatbot.state import ChatBotState
from yuxi.modules.agents.runtime.base import BaseAgent
from yuxi.modules.agents.runtime.checkpoint_cleanup import CheckpointCleanupModel
from yuxi.modules.agents.runtime.context import DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS
from yuxi.modules.agents.runtime.middlewares import (
    ImageInputCompatibilityMiddleware,
    NetworkRetryMiddleware,
    SteerMiddleware,
    TokenUsageMiddleware,
    ToolErrorGuardMiddleware,
    create_memory_middleware,
    create_summary_middleware_from_context,
)
from yuxi.modules.agents.runtime.middlewares.authorization import RuntimeAuthorizationMiddleware
from yuxi.modules.agents.runtime.middlewares.cooperation import create_cooperation_middleware
from yuxi.modules.agents.runtime.middlewares.filesystem import create_agent_filesystem_middleware
from yuxi.modules.agents.runtime.middlewares.skills import SkillsMiddleware
from yuxi.modules.agents.runtime.sandbox.backend import create_agent_composite_backend
from yuxi.modules.agents.runtime.tool_approval import create_tool_approval_middleware, normalize_tool_approval_mode
from yuxi.modules.extensions.skills.runtime import sync_agent_context_skills
from yuxi.modules.extensions.tools.runtime import resolve_configured_runtime_tools
from yuxi.modules.models.chat import load_chat_model, resolve_chat_model_spec
from yuxi.modules.workspace.runtime_paths import runtime_workdir_path


async def _build_middlewares(context, backend, *, cleanup_model=None):
    """构建中间件列表"""
    middlewares = [
        # 最外层隔离普通工具异常，保留取消与 interrupt 的传播。
        ToolErrorGuardMiddleware(),
        SteerMiddleware(),
        create_agent_filesystem_middleware(
            getattr(context, "tool_token_limit", DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS) * 1024,
            backend=backend,
        ),
        SkillsMiddleware(),
    ]
    memory_middleware = None if cleanup_model else await create_memory_middleware(context)
    if memory_middleware:
        middlewares.append(memory_middleware)
    # 清理只调用状态 API，也必须保留持久 checkpoint 的协作节点和通道。
    cooperation_middleware = create_cooperation_middleware(context)
    if cooperation_middleware:
        middlewares.append(cooperation_middleware)
    middlewares.extend(
        [
            create_summary_middleware_from_context(context, backend=backend, model=cleanup_model)
            if cleanup_model
            else create_summary_middleware_from_context(context, backend=backend),
            PatchToolCallsMiddleware(),
            # 网络类错误(断网/连接抖动)按预算(默认600s)持续重试，非网络错误按 max_retries
            # 次数重试——两者合并进 NetworkRetryMiddleware，避免拆成两个中间件后因装配顺序
            # 或外层重试网络错误而放大预算。
            NetworkRetryMiddleware(
                max_retries=getattr(context, "model_retry_times", 2),
            ),
        ]
    )
    approval_middleware = create_tool_approval_middleware(
        normalize_tool_approval_mode(getattr(context, "tool_approval_mode", "default")),
        current_project_path=runtime_workdir_path(context.workdir_relative_path),
    )
    if approval_middleware:
        middlewares.append(approval_middleware)
    # after_model 按逆序执行，授权必须先于审批产生等待点。
    middlewares.append(RuntimeAuthorizationMiddleware())
    # 图片回退读取授权过滤后的工具集，保持与实际模型请求一致。
    middlewares.append(ImageInputCompatibilityMiddleware())
    # 统计经过授权和图片转换后实际交给 provider 的请求。
    middlewares.append(TokenUsageMiddleware())
    context.middleware_tool_names = {tool.name for middleware in middlewares for tool in getattr(middleware, "tools", [])}
    return middlewares


class ChatbotAgent(BaseAgent):
    name = "智能助手"
    description = "基础的对话机器人，可以回答问题，可在配置中启用需要的工具。"
    context_schema = ChatBotContext

    async def get_graph(self, *, context, checkpoint_only: bool = False, **kwargs):
        """构建执行图；checkpoint_only 仅供归属已验证的取消服务使用状态 API。"""
        if checkpoint_only:
            model = CheckpointCleanupModel()
            tools = []
            prompt = ""
            middlewares = await _build_middlewares(context, None, cleanup_model=model)
        else:
            if not getattr(context, "_runtime_prepared", False):
                raise ValueError("构图需要已准备的 Context")
            await sync_agent_context_skills(context)
            backend = create_agent_composite_backend(context)
            model = load_chat_model(
                fully_specified_name=resolve_chat_model_spec(context.model),
                session_id=context.thread_id,
                uid=context.uid,
                max_retries=0,
            )
            tools = await resolve_configured_runtime_tools(context)
            prompt = build_prompt_with_context(context)
            middlewares = await _build_middlewares(context, backend)
        for tool in tools:
            if tool.name in context.middleware_tool_names and tool.name in context.mcp_tool_servers:
                server = context.mcp_tool_servers[tool.name]
                raise RuntimeError(f"工具名冲突：MCP '{server}' 的 '{tool.name}' 与 Backend 工具同名")
        return create_agent(
            model=model,
            tools=tools,
            system_prompt=prompt,
            middleware=middlewares,
            state_schema=ChatBotState,
            checkpointer=get_langgraph_checkpointer(pg_manager),
        )
