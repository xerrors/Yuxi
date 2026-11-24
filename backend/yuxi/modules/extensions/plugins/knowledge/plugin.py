"""内置插件声明。"""

from yuxi.bootstrap.capabilities import knowledge_enabled
from yuxi.modules.extensions.mcp.config import BuiltinMCP
from yuxi.modules.extensions.plugins.base import YuxiPlugin
from yuxi.modules.knowledge.mcp import KNOWLEDGE_MCP_URL


async def _available(db) -> bool:
    """知识库随部署开关启停。"""
    return knowledge_enabled()


async def _delegation(*, uid, context, management, before_request) -> dict:
    """按可信运行上下文或管理身份装配知识库委托。"""
    from yuxi.modules.knowledge.mcp.client import knowledge_mcp_connection

    return knowledge_mcp_connection(uid=uid, context=context, management=management, before_request=before_request)


class KnowledgePlugin(YuxiPlugin):
    """按当前用户连接提供知识库查询。"""

    plugin_id = "knowledge-base"
    name = "知识库"
    description = "检索当前会话选择的知识库，并引用原始资料。"
    entry_skill = "knowledge-base"
    skills = ("knowledge-base",)
    mcps = (
        BuiltinMCP(
            slug="knowledge",
            manifest={
                "type": "http",
                "url": KNOWLEDGE_MCP_URL,
                "extra_data": {"name": "知识库", "description": "知识库检索：按当前用户权限与会话选择提供五个只读工具。"},
            },
            delegation=_delegation,
            available=_available,
        ),
    )
