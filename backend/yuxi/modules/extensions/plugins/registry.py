"""部署插件的唯一注册目录，不接受用户传入的代码路径。"""

import inspect
import json
from functools import lru_cache
from pathlib import Path

from yuxi.modules.extensions.mcp.config import BuiltinMCP
from yuxi.modules.extensions.plugins.base import YuxiPlugin
from yuxi.modules.extensions.plugins.builtin.abridge.plugin import AbridgePlugin
from yuxi.modules.extensions.plugins.builtin.knowledge.plugin import KnowledgePlugin

# 历史 Skill 依赖离线迁移产生的声明；新插件在 REGISTERED_PLUGINS 登记。
MIGRATED_PLUGIN_DIRECTORY = Path(__file__).parent / "deployed"

REGISTERED_PLUGINS = (KnowledgePlugin, AbridgePlugin)


def get_plugin_skill_directories() -> tuple[Path, ...]:
    """仅发布插件入口包，其内部文件随入口统一授权。"""
    directories = []
    for plugin in REGISTERED_PLUGINS:
        directory = Path(inspect.getfile(plugin)).parent / "skills"
        if directory.is_dir():
            for source in sorted(directory.iterdir()):
                if source.is_dir() and not source.name.startswith(("_", ".")) and source.name != plugin.entry_skill:
                    raise ValueError(f"插件 {plugin.plugin_id} 的私有 Skill {source.name} 须放在入口包 {plugin.entry_skill} 内")
            directories.append(directory)
    return tuple(directories)


def plugin_mcp_definitions() -> dict[str, BuiltinMCP]:
    """从注册的代码声明收集专属连接，拒绝同名所有者。"""
    definitions = {}
    for plugin in REGISTERED_PLUGINS:
        for mcp in plugin.mcps:
            if isinstance(mcp, BuiltinMCP):
                if mcp.slug in definitions:
                    raise ValueError(f"插件专属 MCP 标识重复：{mcp.slug}")
                definitions[mcp.slug] = mcp
    return definitions


def validate_plugin(plugin: type[YuxiPlugin]) -> None:
    """在部署边界校验资源声明与工具身份。"""
    from yuxi.modules.extensions.tools import builtin  # noqa: F401
    from yuxi.modules.extensions.tools.registry import get_all_tool_instances

    owned_names = [tool.name for tool in getattr(plugin, "tools", ())]
    reserved = {tool.name for tool in get_all_tool_instances()} | _platform_tool_names()
    conflicts = reserved.intersection(owned_names)
    if conflicts:
        raise ValueError(f"插件专属工具与平台工具名冲突：{', '.join(sorted(conflicts))}")
    if len(owned_names) != len(set(owned_names)):
        raise ValueError(f"插件 {plugin.plugin_id} 的专属工具名称重复")
    for attribute in ("plugin_id", "name", "description", "entry_skill"):
        if not isinstance(getattr(plugin, attribute), str) or not getattr(plugin, attribute).strip():
            raise ValueError(f"插件缺少 {attribute}")
    for attribute in ("skills", "mcps"):
        entries = getattr(plugin, attribute)
        if not isinstance(entries, tuple):
            raise ValueError(f"插件 {plugin.plugin_id} 的 {attribute} 必须是元组")
        slugs = plugin.mcp_slugs() if attribute == "mcps" else entries
        if any(not isinstance(slug, str) or not slug.strip() for slug in slugs):
            raise ValueError(f"插件 {plugin.plugin_id} 的 {attribute} 包含无效标识")
        if len(set(slugs)) != len(slugs):
            raise ValueError(f"插件 {plugin.plugin_id} 的 {attribute} 包含重复项")
    if plugin.entry_skill not in plugin.skills:
        raise ValueError(f"插件 {plugin.plugin_id} 的入口必须包含在 skills 中")


@lru_cache(maxsize=1)
def get_plugins() -> tuple[type[YuxiPlugin], ...]:
    """按固定部署顺序加载代码插件和经过审阅的历史迁移声明。"""
    plugins = list(REGISTERED_PLUGINS)
    fields = {"plugin_id", "name", "description", "entry_skill", "skills", "mcps"}
    for path in sorted(MIGRATED_PLUGIN_DIRECTORY.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError(f"插件声明字段不完整或含未知字段：{path.name}")
        for field in ("skills", "mcps"):
            if not isinstance(data[field], list):
                raise ValueError(f"插件声明 {field} 必须是数组")
            data[field] = tuple(data[field])
        plugins.append(type(f"MigratedPlugin{len(plugins)}", (YuxiPlugin,), data))
    private_mcps = plugin_mcp_definitions()
    private_entries = {plugin.entry_skill for plugin in REGISTERED_PLUGINS}
    seen_ids, seen_entries, seen_names, seen_tools = set(), set(), set(), set()
    for plugin in plugins:
        validate_plugin(plugin)
        for slug in plugin.skills:
            if slug != plugin.entry_skill and slug in private_entries:
                raise ValueError(f"插件 {plugin.plugin_id} 不能引用其他代码插件的入口 Skill：{slug}")
        for mcp in plugin.mcps:
            if isinstance(mcp, str) and mcp in private_mcps:
                raise ValueError(f"插件 {plugin.plugin_id} 不能引用专属 MCP：{mcp}")
        if plugin.plugin_id in seen_ids or plugin.entry_skill in seen_entries or plugin.name in seen_names:
            raise ValueError(f"重复的插件标识或入口：{plugin.plugin_id}")
        owned_names = {tool.name for tool in getattr(plugin, "tools", ())}
        if seen_tools.intersection(owned_names):
            raise ValueError(f"插件专属工具名称重复：{plugin.plugin_id}")
        seen_tools.update(owned_names)
        seen_names.add(plugin.name)
        seen_ids.add(plugin.plugin_id)
        seen_entries.add(plugin.entry_skill)
    return tuple(plugins)


def get_plugin(plugin_id: str) -> type[YuxiPlugin]:
    """严格解析插件标识，部署缺失不能静默降级。"""
    for plugin in get_plugins():
        if plugin.plugin_id == plugin_id:
            return plugin
    raise ValueError(f"未知插件：{plugin_id}")


@lru_cache(maxsize=1)
def _platform_tool_names() -> set[str]:
    """从工具所属中间件读取名称，包含当前会话未开启的可选能力。"""
    from yuxi.modules.agents.runtime.middlewares.cooperation import CooperationMiddleware
    from yuxi.modules.agents.runtime.middlewares.filesystem import create_agent_filesystem_middleware
    from yuxi.modules.agents.runtime.middlewares.memory import YuxiMemoryMiddleware
    from yuxi.modules.agents.runtime.middlewares.plugins import PluginLoaderMiddleware

    middlewares = (
        create_agent_filesystem_middleware(backend=None),
        CooperationMiddleware(),
        YuxiMemoryMiddleware(""),
        PluginLoaderMiddleware(),
    )
    return {tool.name for middleware in middlewares for tool in middleware.tools}
