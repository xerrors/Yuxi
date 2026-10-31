"""两个对话 Backend 共用的构图和执行中间件顺序。"""

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents import create_agent

from yuxi.infrastructure.postgres.checkpointer import get_langgraph_checkpointer
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.runtime.checkpoint_cleanup import CheckpointCleanupModel
from yuxi.modules.agents.runtime.context import DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS
from yuxi.modules.agents.runtime.middlewares import (
    ImageInputCompatibilityMiddleware,
    NetworkRetryMiddleware,
    SteerMiddleware,
    TokenUsageMiddleware,
    ToolErrorGuardMiddleware,
    create_summary_middleware_from_context,
)
from yuxi.modules.agents.runtime.middlewares.authorization import RuntimeAuthorizationMiddleware
from yuxi.modules.agents.runtime.middlewares.filesystem import AGENT_FS_TOOLS, YuxiFilesystemMiddleware
from yuxi.modules.agents.runtime.middlewares.memory import create_memory_middleware
from yuxi.modules.agents.runtime.middlewares.skills import SkillsMiddleware
from yuxi.modules.agents.runtime.sandbox.backend import create_agent_composite_backend
from yuxi.modules.agents.runtime.tool_approval import create_tool_approval_middleware, normalize_tool_approval_mode
from yuxi.modules.extensions.skills.runtime import sync_agent_context_skills
from yuxi.modules.extensions.tools.runtime import resolve_configured_runtime_tools
from yuxi.modules.models.chat import load_chat_model, resolve_chat_model_spec
from yuxi.modules.workspace.runtime_paths import runtime_workdir_path


async def build_agent_graph(
    context,
    *,
    prompt_builder,
    state_schema,
    middleware=(),
    file_tools=AGENT_FS_TOOLS,
    memory_read_only=False,
    checkpoint_only=False,
):
    """集中准备运行或清理所需资源，再按同一顺序装配 Backend 声明的能力。"""
    if checkpoint_only:
        model = cleanup_model = CheckpointCleanupModel()
        backend = memory = None
        tools, prompt = [], ""
    else:
        if not getattr(context, "_runtime_prepared", False):
            raise ValueError("构图需要已准备的 Context")
        await sync_agent_context_skills(context)
        backend = create_agent_composite_backend(context)
        memory = await create_memory_middleware(context, read_only=memory_read_only)
        prompt = prompt_builder(context)
        model = load_chat_model(
            fully_specified_name=resolve_chat_model_spec(context.model),
            session_id=context.thread_id,
            uid=context.uid,
            max_retries=0,
        )
        tools = await resolve_configured_runtime_tools(context)
        cleanup_model = None

    middlewares = [
        # 最外层隔离普通工具异常，保留取消与 interrupt 的传播。
        ToolErrorGuardMiddleware(),
        SteerMiddleware(),
        YuxiFilesystemMiddleware(
            backend=backend,
            tools=file_tools,
            tool_token_limit_before_evict=getattr(context, "tool_token_limit", DEFAULT_TOOL_RESULT_EVICTION_K_TOKENS) * 1024,
        ),
        SkillsMiddleware(),
    ]
    if memory:
        middlewares.append(memory)
    # Backend 声明的中间件可能拥有节点；清理也保留节点，状态 API 不执行钩子。
    middlewares.extend(item for item in middleware if item is not None)
    middlewares.extend(
        [
            create_summary_middleware_from_context(context, backend=backend, model=cleanup_model),
            PatchToolCallsMiddleware(),
            # 网络错误重试预算与普通重试次数由同一个中间件拥有。
            NetworkRetryMiddleware(max_retries=getattr(context, "model_retry_times", 2)),
        ]
    )
    approval = create_tool_approval_middleware(
        normalize_tool_approval_mode(getattr(context, "tool_approval_mode", "default")),
        current_project_path=runtime_workdir_path(context.workdir_relative_path),
    )
    if approval:
        middlewares.append(approval)
    middlewares.extend(
        [
            # after_model 逆序执行；模型请求中授权过滤先于图片兼容和用量统计。
            RuntimeAuthorizationMiddleware(),
            ImageInputCompatibilityMiddleware(),
            TokenUsageMiddleware(),
        ]
    )
    context.middleware_tool_names = {tool.name for middleware in middlewares for tool in getattr(middleware, "tools", [])}
    for tool in tools:
        if tool.name in context.middleware_tool_names and tool.name in context.mcp_tool_servers:
            server = context.mcp_tool_servers[tool.name]
            raise RuntimeError(f"工具名冲突：MCP '{server}' 的 '{tool.name}' 与 Backend 工具同名")
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=prompt,
        middleware=middlewares,
        state_schema=state_schema,
        checkpointer=get_langgraph_checkpointer(pg_manager),
    )
