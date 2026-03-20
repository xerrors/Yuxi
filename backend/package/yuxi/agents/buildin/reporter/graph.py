from dataclasses import dataclass, field
from typing import Annotated

from deepagents.backends import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent

from yuxi.agents import BaseAgent, BaseContext, load_chat_model
from yuxi.agents.middlewares import (
    RuntimeConfigMiddleware,
    save_attachments_to_fs,
)
from yuxi.agents.toolkits.mysql import get_mysql_tools
from yuxi.services.mcp_service import get_mcp_server_names, get_tools_from_all_servers
from yuxi.utils import logger


def _create_fs_backend(rt):
    """创建文件存储后端"""
    return StateBackend(rt)


PROMPT = """你的任务是根据用户的指令，使用数据库工具和图表绘制工具，构建 SQL 查询报告。
你需要根据用户的指令，生成相应的 SQL 查询，并将查询结果以报表的形式返回给用户。
在生成报表时，你可以调用工具生成图表，以更直观地展示数据。
务必确保生成的 SQL 查询是正确且高效的，以避免对数据库造成不必要的负担。
在生成报表时，请遵循以下原则：
1. 理解用户的指令，明确报表的需求和目标。
2. 图表生成工具的返回结果不会默认渲染，需要在最终的报表中以图片形式(markdown格式)嵌入。
3. 必要时，使用网络检索相关工具补充信息。
"""


@dataclass(kw_only=True)
class ReporterContext(BaseContext):
    """覆盖 BaseContext，定义数据库报表助手智能体的可配置参数"""

    # 覆盖 system_prompt，提供更具体的默认值
    system_prompt: Annotated[str, {"__template_metadata__": {"kind": "prompt"}}] = field(
        default=PROMPT,
        metadata={"name": "系统提示词", "description": "用来描述智能体的角色和行为"},
    )

    mcps: Annotated[list[str], {"__template_metadata__": {"kind": "mcps"}}] = field(
        default_factory=lambda: ["mcp-server-chart"],
        metadata={
            "name": "MCP服务器",
            "options": lambda: get_mcp_server_names(),
            "description": (
                "MCP服务器列表，建议使用支持 SSE 的 MCP 服务器，"
                "如果需要使用 uvx 或 npx 运行的服务器，也请在项目外部启动 MCP 服务器，并在项目中配置 MCP 服务器。"
            ),
        },
    )


class SqlReporterAgent(BaseAgent):
    name = "数据库报表助手"
    description = (
        "一个能够生成 SQL 查询报告的智能体助手。同时调用 Charts MCP 生成图表。"
        "MySQL 工具默认启用，无法选择，mcp 默认启用 Charts MCPs。"
    )
    context_schema = ReporterContext
    capabilities = [
        "file_upload",
        "files",
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def get_graph(self, context=None, **kwargs):

        context = context or self.context_schema()  # 获取上下文配置

        all_mcp_tools = await get_tools_from_all_servers()

        graph = create_agent(
            model=load_chat_model(context.model),
            system_prompt=context.system_prompt,
            tools=get_mysql_tools(),  # MySQL 工具默认启用，这里添加的 tools，不会在工具选择框中出现
            middleware=[
                FilesystemMiddleware(backend=_create_fs_backend),  # 文件系统后端
                RuntimeConfigMiddleware(extra_tools=all_mcp_tools),
                save_attachments_to_fs,  # 附件保存到文件系统
            ],
            checkpointer=await self._get_checkpointer(),
        )

        logger.info("SqlReporterAgent 构建成功")
        return graph
