"""随代码发布的固定远程 MCP 连接定义。"""

BUILTIN_MCP_SERVERS = {
    "deepwiki-official": {
        "name": "DeepWiki",
        "transport": "streamable_http",
        "url": "https://mcp.deepwiki.com/mcp",
        "description": "查询公开 GitHub 仓库的文档、架构与代码，支持针对仓库提问。",
        "icon": "📚",
        "tags": ["内置", "代码", "文档"],
    },
}
