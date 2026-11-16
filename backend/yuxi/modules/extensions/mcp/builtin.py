"""随代码发布的固定远程 MCP 连接定义。"""

from yuxi.bootstrap.capabilities import knowledge_enabled

KNOWLEDGE_MCP_SLUG = "knowledge"

BUILTIN_MCP_MANIFEST = {
    "mcpServers": {
        KNOWLEDGE_MCP_SLUG: {
            "type": "http",
            # Knowledge 是本平台自有服务，容器内地址固定，不经环境变量改写。
            "url": "http://api:5050/api/mcp",
            "extra_data": {"name": "知识库", "description": "知识库检索：按当前用户权限与会话选择提供只读检索、原文阅读与原生媒体工具。"},
        },
        "deepwiki-official": {
            "type": "http",
            "url": "https://mcp.deepwiki.com/mcp",
            "extra_data": {
                "name": "DeepWiki",
                "description": "查询公开 GitHub 仓库的文档、架构与代码，支持针对仓库提问。",
            },
        },
    }
}
BUILTIN_MCP_DEFAULTS = {KNOWLEDGE_MCP_SLUG: {"auth_mode": "delegation", "enabled": True}}


def builtin_mcp_available(slug: str) -> bool:
    """部署关闭不改变内置服务身份与管理员开关。"""
    return slug != KNOWLEDGE_MCP_SLUG or knowledge_enabled()
