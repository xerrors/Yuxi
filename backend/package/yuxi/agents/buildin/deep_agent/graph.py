"""Deep Agent - 基于create_deep_agent的深度分析智能体"""

from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.subagents import SubAgentMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import (
    TodoListMiddleware,
    ToolCallLimitMiddleware,
)

from yuxi.agents.common import BaseAgent, load_chat_model
from yuxi.agents.common.backends import create_agent_composite_backend
from yuxi.agents.common.middlewares import RuntimeConfigMiddleware, SummaryOffloadMiddleware, save_attachments_to_fs
from yuxi.agents.common.middlewares.knowledge_base_middleware import KnowledgeBaseMiddleware
from yuxi.agents.common.middlewares.skills_middleware import SkillsMiddleware
from yuxi.agents.common.toolkits.buildin.tools import _create_tavily_search
from yuxi.services.mcp_service import get_tools_from_all_servers
from yuxi.services.subagent_service import get_subagents_from_names
from yuxi.utils import logger

from .context import DeepContext


def _create_fs_backend(rt):
    """创建文件存储后端"""
    return create_agent_composite_backend(rt)


class DeepAgent(BaseAgent):
    name = "深度分析智能体"
    description = "具备规划、深度分析和子智能体协作能力的智能体，可以处理复杂的多步骤任务"
    context_schema = DeepContext
    capabilities = [
        "file_upload",
        "todo",
        "files",
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.graph = None
        self.checkpointer = None

    async def get_tools(self):
        """返回 Deep Agent 的专用工具"""
        from yuxi import config

        tools = []
        if config.enable_web_search:
            tavily = _create_tavily_search()
            if tavily:
                tools.append(tavily)

        if not tools:
            logger.warning("No search tools configured, DeepAgent will work without web search")
        return tools

    async def get_graph(self, context=None, **kwargs):

        context = context or self.context_schema()  # 获取上下文配置

        model = load_chat_model(context.model)
        sub_model = load_chat_model(context.subagents_model)
        search_tools = await self.get_tools()
        all_mcp_tools = await get_tools_from_all_servers()
        # 合并搜索工具和 MCP 工具

        # 从数据库加载 subagent specs（工具名称已解析）
        user_subagents = await get_subagents_from_names(context.subagents)

        # 主 Agent 上下文优化：90k tokens 触发压缩（128k context window 的 70%）
        summary_middleware = SummaryOffloadMiddleware(
            model=model,
            trigger=("tokens", 90000),
            trim_tokens_to_summarize=4000,
            summary_offload_threshold=500,
            max_retention_ratio=0.5,
        )

        # 子 Agent 独立的上下文优化：更激进的压缩策略
        sub_summary_middleware = SummaryOffloadMiddleware(
            model=sub_model,
            trigger=("tokens", 50000),
            trim_tokens_to_summarize=2000,
            summary_offload_threshold=300,
            max_retention_ratio=0.4,
        )

        subagents_middleware = SubAgentMiddleware(
            default_model=sub_model,
            default_tools=search_tools,
            subagents=user_subagents,
            default_middleware=[
                RuntimeConfigMiddleware(
                    model_context_name="subagents_model",
                    enable_model_override=True,
                    enable_system_prompt_override=False,
                    enable_tools_override=False,
                ),
                PatchToolCallsMiddleware(),
                sub_summary_middleware,
                # 子 Agent 搜索工具限制：tavily_search 最多 8 次
                ToolCallLimitMiddleware(
                    tool_name="tavily_search",
                    run_limit=8,
                    exit_behavior="continue",
                ),
            ],
            general_purpose_agent=True,
        )

        # 使用 create_deep_agent 创建深度智能体
        graph = create_agent(
            model=model,
            system_prompt=context.system_prompt,
            middleware=[
                FilesystemMiddleware(backend=_create_fs_backend),  # 文件系统后端
                RuntimeConfigMiddleware(extra_tools=all_mcp_tools),
                SkillsMiddleware(),  # Skills 中间件（提示词注入、依赖展开、动态激活）
                save_attachments_to_fs,  # 附件注入提示词
                TodoListMiddleware(),
                PatchToolCallsMiddleware(),
                KnowledgeBaseMiddleware(),  # 知识库工具
                subagents_middleware,
                summary_middleware,
                # 工具调用限制：tavily_search 总调用最多 20 次
                ToolCallLimitMiddleware(
                    tool_name="tavily_search",
                    thread_limit=20,
                    exit_behavior="continue",
                ),
                # 总工具调用轮次限制：防止单次运行无限循环
                ToolCallLimitMiddleware(
                    run_limit=50,
                    exit_behavior="end",
                ),
            ],
            checkpointer=await self._get_checkpointer(),
        )

        return graph
