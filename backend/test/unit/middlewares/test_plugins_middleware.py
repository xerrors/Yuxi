"""用真实 LangChain 图验证插件披露、工具门控与恢复状态。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from yuxi.modules.agents.runtime.middlewares import authorization, plugins, skills
from yuxi.modules.extensions.plugins.base import YuxiPlugin, is_plugin_active
from yuxi.modules.extensions.plugins.registry import validate_plugin


@tool
def private_tool() -> str:
    """返回可验证的插件执行结果。"""
    return "private-result"


class ExamplePlugin(YuxiPlugin):
    """测试部署插件。"""

    plugin_id = "example"
    name = "ExamplePlugin"
    description = "示例业务能力"
    entry_skill = "example-guide"
    skills = ("example-guide",)
    tools = [private_tool]


class RecordingModel(BaseChatModel):
    """记录真正绑定给模型的 schema 并提供确定性调用。"""

    responses: list[AIMessage]
    requests: list[list[str]] = Field(default_factory=list)

    @property
    def _llm_type(self):
        """标识测试模型。"""
        return "plugin-test"

    def bind_tools(self, tools, **kwargs):
        """观察模型实际收到的工具集合。"""
        self.requests.append([tool.name for tool in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """依次返回协议消息。"""
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


@pytest.fixture
def context(monkeypatch):
    """替换部署目录与数据库权限读取，保留真实图和执行授权。"""
    monkeypatch.setattr(plugins, "get_plugins", lambda: (ExamplePlugin,))
    monkeypatch.setattr(plugins, "get_plugin", lambda value: ExamplePlugin)
    monkeypatch.setattr(skills, "get_plugin", lambda value: ExamplePlugin)
    monkeypatch.setattr(authorization, "get_plugins", lambda: (ExamplePlugin,))
    monkeypatch.setattr(authorization, "_refresh_execution_resources", AsyncMock(return_value={}))
    monkeypatch.setattr(authorization, "refresh_execution_authorization", AsyncMock(return_value={}))

    async def after_model(self, state, runtime):
        """数据库身份已在测试边界替代。"""
        return None

    monkeypatch.setattr(authorization.RuntimeAuthorizationMiddleware, "aafter_model", after_model)
    return SimpleNamespace(
        uid="caller",
        plugins=["example"],
        tools=[],
        mcps=[],
        mcp_tools={},
        mcp_tool_servers={},
        mcp_unavailable={},
        middleware_tool_names={"load_plugin"},
        registered_builtin_tool_names=set(),
        skill_runtime_snapshot={
            "plugin_entry_contents": {"example": "Exact entry instructions"},
            "runtime_skills": {"example-guide": {"path": "/guide/SKILL.md"}},
        },
    )


@pytest.mark.asyncio
async def test_load_opens_tools_only_next_model_request_and_survives_rebuild(context):
    """工具结果、真实模型 schema 与 checkpoint 独立证明激活成功。"""
    model = RecordingModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "load_plugin", "args": {"plugin_id": "example"}, "id": "load"}]),
            AIMessage(content="", tool_calls=[{"name": "private_tool", "args": {}, "id": "use"}]),
            AIMessage(content="done"),
        ]
    )
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "plugin-thread"}}

    def build():
        """重建真实执行图，模拟进程内实例全部丢失。"""
        return create_agent(
            model,
            middleware=[
                plugins.PluginLoaderMiddleware(),
                plugins.PluginMiddleware(ExamplePlugin),
                authorization.RuntimeAuthorizationMiddleware(),
            ],
            checkpointer=saver,
        )

    graph = build()
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "run"}]}, config, context=context)
    assert "private_tool" not in model.requests[0]
    assert "private_tool" in model.requests[1]
    assert any(message.content == "Exact entry instructions" for message in result["messages"])
    assert any(message.content == "private-result" for message in result["messages"])
    saved = await build().aget_state(config)
    assert saved.values["activated_plugins"] == ["example"]
    assert "activated_skills" not in saved.values


@pytest.mark.asyncio
async def test_same_batch_cannot_use_newly_activated_tools(context):
    """强制同批加载和调用不能绕过旧状态授权。"""
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "load_plugin", "args": {"plugin_id": "example"}, "id": "load"},
                    {"name": "private_tool", "args": {}, "id": "forced"},
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(
        model,
        middleware=[
            plugins.PluginLoaderMiddleware(),
            plugins.PluginMiddleware(ExamplePlugin),
            authorization.RuntimeAuthorizationMiddleware(),
        ],
    )
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "run"}]}, context=context)
    forced = next(message for message in result["messages"] if getattr(message, "tool_call_id", None) == "forced")
    assert forced.status == "error"
    assert "无权" in forced.content


def test_revoked_entry_and_disabled_plugin_close_tools(context):
    """checkpoint 的旧激活不能绕过当前配置与入口权限。"""
    state = {"activated_plugins": ["example"]}
    assert authorization.tool_allowed(context, "private_tool", state, {})
    context.plugins = []
    assert not authorization.tool_allowed(context, "private_tool", state, {})
    context.plugins = ["example"]
    context.skill_runtime_snapshot["plugin_entry_contents"] = {}
    assert not authorization.tool_allowed(context, "private_tool", state, {})


@pytest.mark.asyncio
async def test_adapter_owns_activation_nodes_and_declared_tools(context):
    """业务对象的框架同名属性不能新增节点、状态或绕过平台门控。"""
    from langchain.agents import AgentState

    class ExtraState(AgentState):
        """插件自行声明的字段不进入平台图。"""

        extra: str

    class BusinessPlugin(ExamplePlugin):
        """包含框架同名属性的普通业务对象。"""

        state_schema = ExtraState

        def __init__(self):
            """实例字段不替换类拥有的工具集合。"""
            self.tools = []

        def before_model(self, state, runtime):
            """该方法不是插件接口，不能进入图。"""
            raise AssertionError("业务对象不能新增节点")

        async def awrap_model_call(self, request, handler):
            """该方法不参与平台包装。"""
            raise AssertionError("业务对象不能替换包装器")

        @classmethod
        def is_active(cls, context, state):
            """该方法不参与平台激活判定。"""
            return True

        async def on_model_call(self, request, handler):
            """激活前业务钩子不能执行。"""
            raise AssertionError("未激活业务钩子")

    validate_plugin(BusinessPlugin)
    adapter = plugins.PluginMiddleware(BusinessPlugin)
    assert adapter.tools == [private_tool]
    model = RecordingModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "private_tool", "args": {}, "id": "early"}]),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(model, middleware=[adapter, authorization.RuntimeAuthorizationMiddleware()])
    result = await graph.ainvoke({"messages": [("user", "run")]}, context=context)
    assert not any(f"{adapter.name}.before_model" in name for name in graph.nodes)
    assert "extra" not in graph.channels
    assert all("private_tool" not in names for names in model.requests)
    assert next(message for message in result["messages"] if getattr(message, "tool_call_id", None) == "early").status == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,readable", [(False, True), (True, False)])
async def test_load_rejects_disabled_or_unreadable_plugin(context, selected, readable):
    """模型伪造加载不能扩大启用范围或跨过入口权限。"""
    context.plugins = ["example"] if selected else []
    if not readable:
        context.skill_runtime_snapshot["plugin_entry_contents"] = {}
    model = RecordingModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "load_plugin", "args": {"plugin_id": "example"}, "id": "load"}]),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(
        model,
        middleware=[
            plugins.PluginLoaderMiddleware(),
            plugins.PluginMiddleware(ExamplePlugin),
            authorization.RuntimeAuthorizationMiddleware(),
        ],
    )
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "run"}]}, context=context)
    assert not result.get("activated_plugins")
    assert next(message for message in result["messages"] if getattr(message, "tool_call_id", None) == "load").status == "error"


@pytest.mark.asyncio
async def test_mcp_tool_name_collision_fails_before_model(context, monkeypatch):
    """远程目录不得替换已注册插件工具。"""
    from langchain.agents.middleware import ModelRequest

    context.mcps = ["remote"]
    monkeypatch.setattr(plugins, "resolve_runtime_mcp_tools", AsyncMock(return_value={"remote": [private_tool]}))
    request = ModelRequest(model=None, messages=[], tools=[private_tool], runtime=SimpleNamespace(context=context), state={})
    handler = AsyncMock()
    with pytest.raises(RuntimeError, match="工具名冲突"):
        await plugins.PluginLoaderMiddleware().awrap_model_call(request, handler)
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_mcp_does_not_activate_plugin(context, monkeypatch):
    """未连接是可观察失败，不保存虚假的激活成功。"""
    monkeypatch.setattr(ExamplePlugin, "mcps", ("remote",))
    monkeypatch.setattr(plugins, "resolve_runtime_mcp_tools", AsyncMock(return_value={}))
    context.uid = "caller"
    context.mcp_unavailable["remote"] = "授权凭据不可用或已失效，请在扩展页面重新连接"
    model = RecordingModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "load_plugin", "args": {"plugin_id": "example"}, "id": "load"}]),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(
        model,
        middleware=[
            plugins.PluginLoaderMiddleware(),
            plugins.PluginMiddleware(ExamplePlugin),
            authorization.RuntimeAuthorizationMiddleware(),
        ],
    )
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "run"}]}, context=context)
    assert not result.get("activated_plugins")
    message = next(message for message in result["messages"] if getattr(message, "tool_call_id", None) == "load")
    assert message.status == "error" and "remote" in message.content
    assert "扩展页面重新连接" in message.content


@pytest.mark.asyncio
async def test_loaded_mcp_is_callable_and_repeated_load_is_idempotent(context, monkeypatch):
    """协议替身只替换外部发现，真实图绑定动态工具并返回执行结果。"""

    @tool
    def remote_query() -> str:
        """模拟外部协议执行结果。"""
        return "remote-result"

    monkeypatch.setattr(ExamplePlugin, "mcps", ("remote",))

    async def resolve(servers, **kwargs):
        """模拟 MCP service 向本 Run 缓存发布发现结果。"""
        context.mcp_tools["remote"] = [remote_query]
        return {"remote": [remote_query]}

    monkeypatch.setattr(plugins, "resolve_runtime_mcp_tools", resolve)
    monkeypatch.setattr(authorization, "_refresh_execution_resources", AsyncMock(return_value={"remote": set()}))
    monkeypatch.setattr(authorization, "refresh_execution_authorization", AsyncMock(return_value={"remote": set()}))
    model = RecordingModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "load_plugin", "args": {"plugin_id": "example"}, "id": "load"}]),
            AIMessage(content="", tool_calls=[{"name": "load_plugin", "args": {"plugin_id": "example"}, "id": "again"}]),
            AIMessage(content="", tool_calls=[{"name": "remote_query", "args": {}, "id": "remote"}]),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(
        model,
        middleware=[
            plugins.PluginLoaderMiddleware(),
            plugins.PluginMiddleware(ExamplePlugin),
            authorization.RuntimeAuthorizationMiddleware(),
        ],
    )
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "run"}]}, context=context)
    assert "remote_query" not in model.requests[0]
    assert model.requests[1].count("remote_query") == 1
    assert result["activated_plugins"] == ["example"]
    assert any(message.content == "remote-result" for message in result["messages"])


@pytest.mark.parametrize("unknown_field", ["python_module", "tool_names"])
def test_deployed_catalog_rejects_duplicate_identity_and_unknown_fields(tmp_path, monkeypatch, unknown_field):
    """部署JSON不能覆盖内置身份，也不能引入动态代码字段。"""
    import json
    from yuxi.modules.extensions.plugins import registry

    monkeypatch.setattr(registry, "MIGRATED_PLUGIN_DIRECTORY", tmp_path)
    payload = {
        "plugin_id": "knowledge-base",
        "name": "Duplicate",
        "description": "Duplicate",
        "entry_skill": "new-entry",
        "skills": ["new-entry"],
        "mcps": [],
    }
    declaration = tmp_path / "example.json"
    try:
        declaration.write_text(json.dumps(payload))
        registry.get_plugins.cache_clear()
        with pytest.raises(ValueError, match="重复"):
            registry.get_plugins()
        payload[unknown_field] = "untrusted"
        declaration.write_text(json.dumps(payload))
        registry.get_plugins.cache_clear()
        with pytest.raises(ValueError, match="未知字段"):
            registry.get_plugins()
    finally:
        registry.get_plugins.cache_clear()


@pytest.mark.asyncio
async def test_reading_skill_body_does_not_activate_plugin(context):
    """普通读取即使命中入口路径，也只能取得内容而不能扩大能力。"""
    from yuxi.modules.agents.runtime.middlewares.skills import SkillsMiddleware

    @tool
    def read_file(file_path: str) -> str:
        """模拟文件系统返回说明正文。"""
        return "Exact entry instructions"

    context.skill_runtime_snapshot.update(
        {
            "context_skills": [],
            "effective_skills": [],
            "preloaded_skills": [],
            "skill_metadata": {},
        }
    )
    model = RecordingModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"file_path": "/guide/SKILL.md"}, "id": "read"}]),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(model, tools=[read_file], middleware=[SkillsMiddleware(), plugins.PluginLoaderMiddleware()])
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "read"}]}, context=context)
    assert any(message.content == "Exact entry instructions" for message in result["messages"])
    assert not result.get("activated_plugins")
    assert "activated_skills" not in result


@pytest.mark.parametrize("name", ["present_artifacts", "read_file", "load_plugin", "remember_memory", "create_session"])
def test_registry_rejects_owned_tool_using_platform_name(name):
    """即使用户未开启通用或可选中间件能力，插件也不能占用其工具身份。"""
    from langchain_core.tools import StructuredTool

    candidate = type(
        "ConflictingPlugin", (ExamplePlugin,), {"tools": [StructuredTool.from_function(lambda: "bad", name=name, description="冲突")]}
    )
    with pytest.raises(ValueError, match="工具名冲突"):
        validate_plugin(candidate)


def test_unselected_plugin_cannot_revoke_platform_tool(context, monkeypatch):
    """未选择的部署插件不参与当前图工具授权，避免全局目录污染。"""
    context.plugins = []
    context.middleware_tool_names = {"private_tool"}
    assert authorization.tool_allowed(context, "private_tool", {}, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("personal_override", [False, True])
async def test_missing_auxiliary_skill_reports_resource_without_activation(context, monkeypatch, personal_override):
    """入口可读时缺失配套说明应指向真正需要修复的资源。"""
    monkeypatch.setattr(ExamplePlugin, "skills", ("example-guide", "missing-guide"))
    if personal_override:
        context.skill_runtime_snapshot["runtime_skills"]["missing-guide"] = {"path": "/personal/SKILL.md"}
        context.skill_runtime_snapshot["skill_metadata"] = {"missing-guide": {"source_scope": "personal"}}
        assert not is_plugin_active(ExamplePlugin, context, {"activated_plugins": ["example"]})
    model = RecordingModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "load_plugin", "args": {"plugin_id": "example"}, "id": "load"}]),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(
        model,
        middleware=[
            plugins.PluginLoaderMiddleware(),
            plugins.PluginMiddleware(ExamplePlugin),
            authorization.RuntimeAuthorizationMiddleware(),
        ],
    )
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "run"}]}, context=context)
    message = next(message for message in result["messages"] if getattr(message, "tool_call_id", None) == "load")
    assert message.status == "error"
    assert "配套 Skill" in message.content and "missing-guide" in message.content
    assert not result.get("activated_plugins")


@pytest.mark.asyncio
@pytest.mark.parametrize("collision", ["local", "duplicate", "other-server"])
async def test_load_rejects_mcp_collision_before_activation(context, monkeypatch, collision):
    """冲突在 load 返回前失败，下一模型轮仍能继续且不会得到冲突工具。"""
    from langchain_core.tools import StructuredTool

    remote = StructuredTool.from_function(lambda: "remote", name="conflict", description="远程工具")
    monkeypatch.setattr(ExamplePlugin, "mcps", ("remote",))
    if collision == "local":
        context.middleware_tool_names.add("conflict")
    if collision == "other-server":
        context.mcp_tool_servers["conflict"] = "other"
    remote_tools = [remote, remote] if collision == "duplicate" else [remote]

    async def resolve(servers, **kwargs):
        """只返回被请求的连接器，保持实际服务的发现边界。"""
        return {"remote": remote_tools} if "remote" in servers else {}

    monkeypatch.setattr(plugins, "resolve_runtime_mcp_tools", resolve)
    model = RecordingModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "load_plugin", "args": {"plugin_id": "example"}, "id": "load"}]),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(
        model,
        middleware=[
            plugins.PluginLoaderMiddleware(),
            plugins.PluginMiddleware(ExamplePlugin),
            authorization.RuntimeAuthorizationMiddleware(),
        ],
    )
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "run"}]}, context=context)
    message = next(message for message in result["messages"] if getattr(message, "tool_call_id", None) == "load")
    assert message.status == "error" and "工具名冲突" in message.content
    assert not result.get("activated_plugins")
    assert "conflict" not in model.requests[-1]


@pytest.mark.asyncio
async def test_parallel_plugin_loads_cannot_activate_conflicting_mcps(context, monkeypatch):
    """真实 ToolNode 并行加载同名工具时只允许一个插件激活。"""
    import asyncio
    from langchain_core.tools import StructuredTool

    class OtherPlugin(ExamplePlugin):
        """另一份独立插件依赖另一个远程服务。"""

        plugin_id = "other"
        name = "OtherPlugin"
        entry_skill = "other-guide"
        skills = ("other-guide",)
        tools = []
        mcps = ("other",)

    monkeypatch.setattr(ExamplePlugin, "mcps", ("remote",))
    candidates = (ExamplePlugin, OtherPlugin)
    monkeypatch.setattr(plugins, "get_plugins", lambda: candidates)
    monkeypatch.setattr(plugins, "get_plugin", lambda value: next(p for p in candidates if p.plugin_id == value))
    context.plugins.append("other")
    context.skill_runtime_snapshot["plugin_entry_contents"]["other"] = "Other instructions"
    context.skill_runtime_snapshot["runtime_skills"]["other-guide"] = {"path": "/other/SKILL.md"}

    async def resolve(servers, **kwargs):
        """让两个发现请求交错，分别发布真实来源名称。"""
        await asyncio.sleep(0)
        return {server: [StructuredTool.from_function(lambda: "remote", name="shared_name", description="远程工具")] for server in servers}

    monkeypatch.setattr(plugins, "resolve_runtime_mcp_tools", resolve)
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "load_plugin", "args": {"plugin_id": "example"}, "id": "one"},
                    {"name": "load_plugin", "args": {"plugin_id": "other"}, "id": "two"},
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(model, middleware=[plugins.PluginLoaderMiddleware()])
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "run"}]}, context=context)
    assert len(result["activated_plugins"]) == 1
    results = [m for m in result["messages"] if getattr(m, "tool_call_id", None) in {"one", "two"}]
    assert sorted(m.status for m in results) == ["error", "success"]
    assert "工具名冲突" in next(m.content for m in results if m.status == "error")


def test_registry_rejects_duplicate_tools_inside_plugin():
    """同一插件的重复声明也必须在注册时失败。"""
    candidate = type("DuplicatePlugin", (ExamplePlugin,), {"tools": [private_tool, private_tool]})
    with pytest.raises(ValueError, match="名称重复"):
        validate_plugin(candidate)


def test_registry_rejects_tool_name_shared_by_two_plugins(monkeypatch, tmp_path):
    """即便入口不同，两个部署插件也不能注册同一个工具身份。"""
    from yuxi.modules.extensions.plugins import registry

    other = type(
        "OtherPlugin",
        (ExamplePlugin,),
        {
            "plugin_id": "other",
            "name": "Other",
            "entry_skill": "other",
            "skills": ("other",),
        },
    )
    monkeypatch.setattr(registry, "REGISTERED_PLUGINS", (ExamplePlugin, other))
    monkeypatch.setattr(registry, "MIGRATED_PLUGIN_DIRECTORY", tmp_path)
    registry.get_plugins.cache_clear()
    try:
        with pytest.raises(ValueError, match="专属工具名称重复"):
            registry.get_plugins()
    finally:
        registry.get_plugins.cache_clear()


@pytest.mark.asyncio
async def test_independent_plugin_hooks_follow_registration_order_only_after_activation(context, monkeypatch):
    """真实图按顺序执行独立包装器，未激活时不调用插件钩子。"""
    events = []

    class FirstPlugin(ExamplePlugin):
        """记录第一个插件的调用包装。"""

        async def on_model_call(self, request, handler):
            """记录模型包装进入与返回顺序。"""
            events.append("first:model:enter")
            result = await handler(request)
            events.append("first:model:exit")
            return result

        async def on_tool_call(self, request, handler):
            """记录真实工具节点的包装顺序。"""
            events.append("first:tool:enter")
            result = await handler(request)
            events.append("first:tool:exit")
            return result

    class SecondPlugin(FirstPlugin):
        """独立实例具有自己的入口和包装行为。"""

        plugin_id = "second"
        name = "SecondPlugin"
        entry_skill = "second-guide"
        skills = ("second-guide",)
        tools = []

        async def on_model_call(self, request, handler):
            """记录第二个模型包装。"""
            events.append("second:model:enter")
            result = await handler(request)
            events.append("second:model:exit")
            return result

        async def on_tool_call(self, request, handler):
            """记录第二个工具包装。"""
            events.append("second:tool:enter")
            result = await handler(request)
            events.append("second:tool:exit")
            return result

    candidates = (FirstPlugin, SecondPlugin)
    for candidate in candidates:
        validate_plugin(candidate)
    monkeypatch.setattr(plugins, "get_plugins", lambda: candidates)
    monkeypatch.setattr(authorization, "get_plugins", lambda: candidates)
    context.plugins.append("second")
    context.skill_runtime_snapshot["plugin_entry_contents"]["second"] = "Second instructions"
    context.skill_runtime_snapshot["runtime_skills"]["second-guide"] = {"path": "/second/SKILL.md"}
    for active in ([], ["example", "second"]):
        events.clear()
        model = RecordingModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "private_tool", "args": {}, "id": "use"}]),
                AIMessage(content="done"),
            ]
        )
        graph = create_agent(
            model,
            middleware=[
                plugins.PluginLoaderMiddleware(),
                *(plugins.PluginMiddleware(candidate) for candidate in candidates),
                authorization.RuntimeAuthorizationMiddleware(),
            ],
        )
        result = await graph.ainvoke({"messages": [("user", "run")], "activated_plugins": active}, context=context)
        tool_result = next(message for message in result["messages"] if getattr(message, "tool_call_id", None) == "use")
        if not active:
            assert events == []
            assert tool_result.status == "error"
        else:
            assert tool_result.content == "private-result"
            assert events == [
                "first:model:enter",
                "second:model:enter",
                "second:model:exit",
                "first:model:exit",
                "first:tool:enter",
                "second:tool:enter",
                "second:tool:exit",
                "first:tool:exit",
                "first:model:enter",
                "second:model:enter",
                "second:model:exit",
                "first:model:exit",
            ]
