"""独立发布的 MCP 与已注册插件自带的专属连接。"""

from yuxi.modules.extensions.mcp.config import BuiltinMCP
from yuxi.modules.extensions.plugins.registry import plugin_mcp_definitions

_SHARED_MCPS = {
    "deepwiki-official": {
        "type": "http",
        "url": "https://mcp.deepwiki.com/mcp",
        "extra_data": {
            "name": "DeepWiki",
            "description": "查询公开 GitHub 仓库的文档、架构与代码，支持针对仓库提问。",
        },
    },
}


def builtin_mcp_servers() -> dict[str, BuiltinMCP]:
    """合并部署定义，拒绝共享与专属连接争用同一身份。"""
    servers = plugin_mcp_definitions()
    for slug, manifest in _SHARED_MCPS.items():
        if slug in servers:
            raise ValueError(f"内置 MCP 标识重复：{slug}")
        servers[slug] = BuiltinMCP(slug=slug, manifest=manifest, enabled=False)
    return servers


BUILTIN_MCP_SERVERS = builtin_mcp_servers()


def supports_direct_mcp_selection(slug: str) -> bool:
    """Agent 只能直接选择独立连接；专属连接由其插件贡献。"""
    return slug not in BUILTIN_MCP_SERVERS or slug in _SHARED_MCPS
