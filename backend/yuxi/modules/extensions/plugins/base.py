"""随部署交付的可信插件开发接口。"""

from langchain_core.tools import BaseTool


class YuxiPlugin:
    """声明配套资源和调用钩子，由平台适配到运行图。"""

    plugin_id: str = ""
    name: str = ""
    description: str = ""
    entry_skill: str = ""
    skills: tuple[str, ...] = ()
    mcps: tuple[str, ...] = ()
    tools: tuple[BaseTool, ...] = ()

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
