"""随代码发布的固定远程 MCP 连接定义。"""

from yuxi.bootstrap.capabilities import knowledge_enabled

KNOWLEDGE_MCP_SLUG = "knowledge"
ABRIDGE_MCP_SLUG = "abridge"

BUILTIN_MCP_MANIFEST = {
    "mcpServers": {
        ABRIDGE_MCP_SLUG: {
            "type": "http",
            "url": "http://abridge:8765/mcp",
            "extra_data": {"name": "aBridge", "description": "使用当前用户授权的电脑和浏览器设备。"},
        },
        KNOWLEDGE_MCP_SLUG: {
            "type": "http",
            # Knowledge 是本平台自有服务，容器内地址固定，不经环境变量改写。
            "url": "http://api:5050/api/mcp",
            "extra_data": {"name": "知识库", "description": "知识库检索：按当前用户权限与会话选择提供五个只读工具。"},
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
BUILTIN_MCP_DEFAULTS = {
    KNOWLEDGE_MCP_SLUG: {"auth_mode": "delegation", "enabled": True},
    ABRIDGE_MCP_SLUG: {"auth_mode": "delegation", "enabled": True},
}


def builtin_mcp_available(slug: str) -> bool:
    """部署关闭不改变内置服务身份与管理员开关。"""
    return slug != KNOWLEDGE_MCP_SLUG or knowledge_enabled()


def supports_direct_mcp_selection(slug: str) -> bool:
    """设备能力必须经插件加载操作约束，其他连接器可由智能体直接选择。"""
    return slug != ABRIDGE_MCP_SLUG
