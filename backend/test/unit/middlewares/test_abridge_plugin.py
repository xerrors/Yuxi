"""验证设备插件在真实模型请求中的渐进披露与当前权限门控。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from yuxi.modules.agents.runtime.middlewares import plugins
from yuxi.modules.extensions.plugins.builtin.abridge.plugin import AbridgePlugin
from yuxi.modules.extensions.plugins.registry import get_plugin, get_plugin_skill_directories


@pytest.fixture
def context():
    """提供已授权插件入口与配套说明的运行快照。"""
    entry = Path(__file__).parents[3] / "yuxi/modules/extensions/plugins/builtin/abridge/skills/abridge/SKILL.md"
    return SimpleNamespace(
        uid="caller",
        plugins=["abridge"],
        mcps=[],
        mcp_tools={},
        mcp_tool_servers={},
        mcp_unavailable={},
        middleware_tool_names={"load_plugin"},
        registered_builtin_tool_names=set(),
        skill_runtime_snapshot={
            "plugin_entry_contents": {"abridge": entry.read_text(encoding="utf-8")},
            "runtime_skills": {
                "abridge": {"path": "/home/user/skills/abridge/SKILL.md"},
                "chat-widgets": {"path": "/home/user/skills/chat-widgets/SKILL.md"},
            },
            "skill_metadata": {},
        },
    )


class DeviceModel(BaseChatModel):
    """记录模型真正收到的系统消息和工具后返回确定性回复。"""

    responses: list[AIMessage]
    prompts: list[str] = Field(default_factory=list)
    tool_names: list[list[str]] = Field(default_factory=list)

    @property
    def _llm_type(self):
        """标识设备插件测试模型。"""
        return "abridge-plugin-test"

    def bind_tools(self, tools, **kwargs):
        """记录当前请求公开的工具集合。"""
        self.tool_names.append([item.name for item in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """读取实际系统消息并消费下一份回复。"""
        self.prompts.append("\n".join(message.text for message in messages if isinstance(message, SystemMessage)))
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


def test_abridge_registered_with_packaged_entry_and_shared_widgets():
    """部署目录同时提供设备插件和可同步的入口说明。"""
    assert get_plugin("abridge") is AbridgePlugin
    assert AbridgePlugin.entry_skill == "abridge"
    assert AbridgePlugin.skills == ("abridge", "chat-widgets")
    assert AbridgePlugin.mcps == ("abridge",)
    assert any((directory / "abridge/SKILL.md").is_file() for directory in get_plugin_skill_directories())


@pytest.mark.asyncio
async def test_load_abridge_discloses_entry_then_injects_device_prompt(context, monkeypatch):
    """真实图证明加载前没有设备提示，加载后才公开提示与设备工具。"""

    @tool
    def device_observe() -> str:
        """读取测试设备的结构化状态。"""
        return "设备页面已读取"

    async def resolve(servers, **kwargs):
        """替代外部 MCP 发现，保留真实插件加载与模型请求。"""
        if "abridge" not in servers:
            return {}
        context.mcp_tools["abridge"] = [device_observe]
        return {"abridge": [device_observe]}

    monkeypatch.setattr(plugins, "resolve_runtime_mcp_tools", resolve)
    model = DeviceModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "load_plugin", "args": {"plugin_id": "abridge"}, "id": "load"}]),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(
        model,
        system_prompt="保留原始系统规则",
        middleware=[plugins.PluginLoaderMiddleware(), plugins.PluginMiddleware(AbridgePlugin)],
    )
    result = await graph.ainvoke({"messages": [("user", "查看设备页面")]}, context=context)

    assert "设备控制（aBridge）" not in model.prompts[0]
    assert "device_observe" not in model.tool_names[0]
    assert "设备控制（aBridge）" in model.prompts[1]
    assert "device_observe" in model.tool_names[1]
    assert "保留原始系统规则" in model.prompts[1]
    assert "沙盒文件系统" in model.prompts[1]
    assert "await 每个浏览器调用" in model.prompts[1]
    assert "等待超时不表示远端操作已停止" in model.prompts[1]
    assert "无需读取 Skill" not in model.prompts[1]
    assert result["activated_plugins"] == ["abridge"]
    entry_message = next(message for message in result["messages"] if getattr(message, "tool_call_id", None) == "load")
    assert entry_message.status == "success"
    assert "操作后重新读取" in entry_message.content
    assert "/home/user/skills/chat-widgets/SKILL.md" in entry_message.content


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled_by", ["not-activated", "selection", "entry-permission", "auxiliary-permission", "personal-override"])
async def test_abridge_does_not_inject_prompt_after_current_access_is_lost(context, disabled_by):
    """历史激活或已缓存设备目录不能替代当前选择与配套 Skill 权限。"""
    state = {"activated_plugins": ["abridge"]}
    context.mcp_tool_servers = {"device_observe": "abridge"}
    if disabled_by == "not-activated":
        state = {}
    elif disabled_by == "selection":
        context.plugins = []
    elif disabled_by == "entry-permission":
        context.skill_runtime_snapshot["plugin_entry_contents"] = {}
    elif disabled_by == "auxiliary-permission":
        del context.skill_runtime_snapshot["runtime_skills"]["chat-widgets"]
    else:
        context.skill_runtime_snapshot["skill_metadata"]["abridge"] = {"source_scope": "personal"}
    request = ModelRequest(
        model=None,
        messages=[],
        tools=[],
        system_message=SystemMessage(content="原始规则"),
        runtime=SimpleNamespace(context=context),
        state=state,
    )

    async def receive(forwarded):
        """返回真正送到下一层的请求供断言。"""
        return forwarded

    forwarded = await plugins.PluginMiddleware(AbridgePlugin).awrap_model_call(request, receive)
    assert forwarded is request
    assert forwarded.system_message.text == "原始规则"
