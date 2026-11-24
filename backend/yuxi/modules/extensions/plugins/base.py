"""随部署交付的可信插件开发接口。"""

from langchain_core.tools import BaseTool

from yuxi.modules.extensions.mcp.config import BuiltinMCP


class YuxiPlugin:
    """声明配套资源和调用钩子，由平台适配到运行图。"""

    plugin_id: str = ""
    name: str = ""
    description: str = ""
    entry_skill: str = ""
    skills: tuple[str, ...] = ()
    mcps: tuple[BuiltinMCP | str, ...] = ()
    tools: tuple[BaseTool, ...] = ()

    @classmethod
    def mcp_slugs(cls) -> tuple[str, ...]:
        """按声明顺序返回专属连接和共享引用的标识。"""
        return tuple(mcp.slug if isinstance(mcp, BuiltinMCP) else mcp for mcp in cls.mcps)

    async def on_model_call(self, request, handler):
        """处理已激活插件的模型请求，可由部署插件覆盖。"""
        return await handler(request)

    async def on_tool_call(self, request, handler):
        """处理已激活插件的工具请求，可由部署插件覆盖。"""
        return await handler(request)


def is_plugin_active(plugin: type[YuxiPlugin], context, state) -> bool:
    """平台按当前选择、激活记录和资源访问权统一判定可用性。"""
    snapshot = getattr(context, "skill_runtime_snapshot", None) or {}
    return (
        plugin.plugin_id in getattr(context, "plugins", [])
        and plugin.plugin_id in (state or {}).get("activated_plugins", [])
        and plugin.plugin_id in snapshot.get("plugin_entry_contents", {})
        and set(plugin.skills).issubset(snapshot.get("runtime_skills", {}))
        and all(snapshot.get("skill_metadata", {}).get(slug, {}).get("source_scope") != "personal" for slug in plugin.skills)
    )
