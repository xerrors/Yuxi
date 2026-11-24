"""插件渐进披露与平台拥有的激活状态。"""

from typing import Annotated, NotRequired

from deepagents.middleware._utils import append_to_system_message
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command

from yuxi.modules.extensions.mcp.builtin import supports_direct_mcp_selection
from yuxi.modules.extensions.mcp.service import resolve_runtime_mcp_tools
from yuxi.modules.extensions.plugins.base import YuxiPlugin, is_plugin_active
from yuxi.modules.extensions.plugins.registry import get_plugin, get_plugins


def merge_activated_plugins(left: list[str] | None, right: list[str] | None) -> list[str]:
    """合并同批工具激活结果，保持顺序并去重。"""
    return list(dict.fromkeys([*(left or []), *(right or [])]))


class PluginsState(AgentState):
    """平台统一持久化插件激活，不允许插件新增状态。"""

    activated_plugins: NotRequired[Annotated[list[str], merge_activated_plugins]]


class PluginMiddleware(AgentMiddleware):
    """适配部署插件，只向运行图开放平台拥有的包装入口。"""

    def __init__(self, plugin: type[YuxiPlugin]):
        """从同一类声明注册工具并创建本图独享的业务实例。"""
        self.definition = plugin
        self.plugin = plugin()
        self.tools = list(plugin.tools)

    @property
    def name(self) -> str:
        """使用注册时已校验唯一的插件名称。"""
        return self.definition.name

    async def awrap_model_call(self, request, handler):
        """激活后才执行业务模型钩子。"""
        if is_plugin_active(self.definition, request.runtime.context, request.state):
            return await self.plugin.on_model_call(request, handler)
        return await handler(request)

    async def awrap_tool_call(self, request, handler):
        """激活后才执行业务工具钩子，最终授权仍由执行边界复核。"""
        if is_plugin_active(self.definition, request.runtime.context, request.state):
            return await self.plugin.on_tool_call(request, handler)
        return await handler(request)


class PluginLoaderMiddleware(AgentMiddleware):
    """通过显式加载披露正文，并为下一模型轮开放插件能力。"""

    state_schema = PluginsState

    def __init__(self):
        """注册无外部副作用的统一加载入口。"""

        async def load_plugin(plugin_id: str, runtime: ToolRuntime) -> Command | ToolMessage:
            """加载已启用插件的说明及能力，工具从下一次模型请求开始可用。"""
            context = runtime.context
            if plugin_id not in getattr(context, "plugins", []):
                return ToolMessage(content="插件未启用，不能加载。", tool_call_id=runtime.tool_call_id, status="error")
            plugin = get_plugin(plugin_id)
            snapshot = context.skill_runtime_snapshot or {}
            content = snapshot.get("plugin_entry_contents", {}).get(plugin_id)
            if content is None:
                return ToolMessage(content="插件入口不可访问，不能加载。", tool_call_id=runtime.tool_call_id, status="error")
            missing_skills = [
                slug
                for slug in plugin.skills
                if slug not in snapshot.get("runtime_skills", {})
                or snapshot.get("skill_metadata", {}).get(slug, {}).get("source_scope") == "personal"
            ]
            if missing_skills:
                return ToolMessage(
                    content="插件配套 Skill 不可用：" + "、".join(missing_skills),
                    tool_call_id=runtime.tool_call_id,
                    status="error",
                )
            loaded = await self._load_mcps(context, plugin.mcp_slugs())
            missing = [server for server in plugin.mcp_slugs() if server not in loaded]
            if missing:
                details = [
                    f"连接器 {server} 不可用：{context.mcp_unavailable.get(server, '服务连接或发现失败，请检查连接器后重试')}"
                    for server in missing
                ]
                return ToolMessage(content="插件未激活：" + "；".join(details), tool_call_id=runtime.tool_call_id, status="error")
            try:
                self._register_mcp_sources(context, loaded)
            except ValueError as exc:
                return ToolMessage(content=f"插件未激活：{exc}", tool_call_id=runtime.tool_call_id, status="error")
            entry_path = snapshot["runtime_skills"][plugin.entry_skill]["path"]
            content = f"入口文件：{entry_path}（文内相对路径以此文件为基准）\n\n{content}"
            references = [
                f"- {slug}: {snapshot['runtime_skills'][slug]['path']}"
                for slug in plugin.skills
                if slug != plugin.entry_skill and slug in snapshot.get("runtime_skills", {})
            ]
            if references:
                content += "\n\n配套说明（按需读取）：\n" + "\n".join(references)
            return Command(
                update={
                    "activated_plugins": [plugin_id],
                    "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
                }
            )

        self.tools = [StructuredTool.from_function(coroutine=load_plugin)]

    async def awrap_model_call(self, request, handler):
        """注入插件目录并装配已经激活的 MCP 工具。"""
        context = request.runtime.context
        selected = set(getattr(context, "plugins", []))
        plugins = [plugin for plugin in get_plugins() if plugin.plugin_id in selected]
        if plugins:
            lines = [
                "## 可加载插件",
                "按需调用 load_plugin(plugin_id) 读取说明并激活能力；下一轮才能调用其工具。",
                "用户消息中的 @plugin:<插件ID> 表示明确要求使用该插件；先通过 load_plugin 加载，再按说明完成任务。"
                "提及未启用或不可用的插件时说明原因，不能绕过加载与权限检查。",
            ]
            lines.extend(f"- {plugin.plugin_id}: {plugin.description}" for plugin in plugins)
            request = request.override(system_message=append_to_system_message(request.system_message, "\n".join(lines)))
        servers = [slug for slug in getattr(context, "mcps", []) if supports_direct_mcp_selection(slug)]
        for plugin in plugins:
            if is_plugin_active(plugin, context, request.state):
                servers.extend(plugin.mcp_slugs())
        loaded = await self._load_mcps(context, tuple(dict.fromkeys(servers)))
        try:
            self._register_mcp_sources(context, loaded)
        except ValueError as exc:
            raise RuntimeError(f"插件 MCP 装配失败：{exc}") from exc
        tools = list(request.tools)
        names = {tool.name for tool in tools}
        for server_tools in loaded.values():
            for tool in server_tools:
                if tool.name in names:
                    continue
                names.add(tool.name)
                tools.append(tool)
        return await handler(request.override(tools=tools))

    @staticmethod
    def _register_mcp_sources(context, loaded):
        """校验完整目录后同步登记来源，使同批并行加载也不能争用工具名称。"""
        sources = dict(context.mcp_tool_servers)
        local_names = set(getattr(context, "registered_builtin_tool_names", None) or ())
        local_names.update(getattr(context, "middleware_tool_names", ()))
        for plugin in get_plugins():
            if plugin.plugin_id in getattr(context, "plugins", ()):
                local_names.update(tool.name for tool in getattr(plugin, "tools", ()))
        for server, tools in loaded.items():
            server_names = set()
            for tool in tools:
                if tool.name in local_names or tool.name in server_names or sources.get(tool.name, server) != server:
                    raise ValueError(f"连接器 {server} 工具名冲突：{tool.name}，请联系管理员调整工具名称")
                server_names.add(tool.name)
                sources[tool.name] = server
        # 无 await 的检查和发布共享本 Run 上下文；失败时不占用任何名称。
        context.mcp_tool_servers.update(sources)

    async def _load_mcps(self, context, servers):
        """沿用当前用户连接和本 Run 工具缓存，不创建新授权。"""
        if not servers:
            return {}
        if getattr(context, "mcp_tool_servers", None) is None:
            raise RuntimeError("MCP 工具来源尚未装配")
        return await resolve_runtime_mcp_tools(
            list(servers),
            uid=context.uid,
            run_tools=context.mcp_tools,
            unavailable=context.mcp_unavailable,
            context=context,
        )

    async def awrap_tool_call(self, request, handler):
        """为动态 MCP 工具绑定本 Run 中已发现的执行对象。"""
        if request.tool is None:
            context = request.runtime.context
            name = request.tool_call["name"]
            server = (getattr(context, "mcp_tool_servers", None) or {}).get(name)
            if server:
                tool = next((tool for tool in context.mcp_tools.get(server, []) if tool.name == name), None)
                if tool is not None:
                    request = request.override(tool=tool)
        return await handler(request)
