from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.subagents import SubAgentMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware, TodoListMiddleware

from yuxi.agents import BaseAgent, BaseState, load_chat_model
from yuxi.agents.backends import create_agent_composite_backend
from yuxi.agents.middlewares import (
    RuntimeConfigMiddleware,
    SummaryOffloadMiddleware,
    save_attachments_to_fs,
)
from yuxi.agents.middlewares.knowledge_base_middleware import KnowledgeBaseMiddleware
from yuxi.agents.middlewares.skills_middleware import SkillsMiddleware
from yuxi.services.mcp.tool_registry_service import get_tools_from_all_servers
from yuxi.services.subagent_service import get_subagents_from_names

from .prompt import TODO_MID_PROMPT, build_prompt_with_context


async def _build_middlewares(context):
    """构建中间件列表"""
    all_mcp_tools = await get_tools_from_all_servers()  # 因为异步加载，无法放在 RuntimeConfigMiddleware 的 __init__ 中

    # summary middleware
    # 主 Agent 上下文优化：90k tokens 触发压缩（128k context window 的 70%）
    summary_middleware = SummaryOffloadMiddleware(
        model=load_chat_model(fully_specified_name=context.model),
        trigger=("tokens", getattr(context, "summary_threshold", 100) * 1024),
        trim_tokens_to_summarize=4000,
        summary_offload_threshold=500,
        max_retention_ratio=0.5,
    )

    # subagents
    subagents = await get_subagents_from_names(context.subagents)
    subagents_middleware = SubAgentMiddleware(
        default_model=load_chat_model(fully_specified_name=context.subagents_model),
        subagents=subagents,
        general_purpose_agent=True,
        default_middleware=[
            FilesystemMiddleware(backend=create_agent_composite_backend),  # 文件系统后端
            PatchToolCallsMiddleware(),
            summary_middleware,
        ],
    )
    # all middlewares
    middlewares = [
        FilesystemMiddleware(backend=create_agent_composite_backend),  # 文件系统后端
        save_attachments_to_fs,  # 附件注入提示词
        KnowledgeBaseMiddleware(),  # 知识库工具
        RuntimeConfigMiddleware(extra_tools=all_mcp_tools),  # 运行时配置应用（模型/工具/MCP/提示词）
        SkillsMiddleware(),  # Skills 中间件（提示词注入、依赖展开、动态激活）
        subagents_middleware,
        summary_middleware,
        TodoListMiddleware(system_prompt=TODO_MID_PROMPT),  # 待办事项中间件
        PatchToolCallsMiddleware(),
        ModelRetryMiddleware(),  # 模型重试中间件
    ]

    return middlewares


class ChatbotAgent(BaseAgent):
    name = "智能助手"
    description = "基础的对话机器人，可以回答问题，可在配置中启用需要的工具。"
    capabilities = ["file_upload", "files"]  # 支持文件上传功能
    metadata = {
        "examples": [
            "你好，请介绍一下你自己",
            "帮我写一封商务邮件",
            "解释一下什么是机器学习",
            "创建一个冒泡排序 python 并保存结果",
        ]
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def get_graph(self, context=None, **kwargs):

        context = context or self.context_schema()  # 获取上下文配置

        # 使用 create_agent 创建智能体
        graph = create_agent(
            model=load_chat_model(fully_specified_name=context.model),
            system_prompt=build_prompt_with_context(context),
            middleware=await _build_middlewares(context),
            state_schema=BaseState,
            checkpointer=await self._get_checkpointer(),
        )

        return graph


def main():
    pass


if __name__ == "__main__":
    main()
    # asyncio.run(main())
