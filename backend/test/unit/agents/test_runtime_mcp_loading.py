import os
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "test")

from yuxi.agents.middlewares import runtime_config_middleware as runtime_module
from yuxi.agents.middlewares.runtime_config_middleware import RuntimeConfigMiddleware


class _FakeRequest:
    def __init__(self, *, context, tools):
        self.runtime = SimpleNamespace(context=context)
        self.tools = tools
        self.system_message = None

    def override(self, **overrides):
        return SimpleNamespace(
            runtime=self.runtime,
            tools=overrides.get("tools", self.tools),
            system_message=overrides.get("system_message", self.system_message),
        )


async def test_runtime_config_middleware_adds_context_mcp_tools(monkeypatch):
    base_tool = SimpleNamespace(name="base_tool")
    mcp_tool = SimpleNamespace(name="mcp_tool")
    loaded_servers: list[str] = []

    async def fake_get_enabled_mcp_tools(server_name: str):
        loaded_servers.append(server_name)
        return [mcp_tool]

    monkeypatch.setattr(runtime_module, "get_all_tool_instances", lambda: [base_tool])
    monkeypatch.setattr(runtime_module, "get_enabled_mcp_tools", fake_get_enabled_mcp_tools)

    middleware = RuntimeConfigMiddleware(
        enable_model_override=False,
        enable_system_prompt_override=False,
    )
    context = SimpleNamespace(tools=["base_tool"], mcps=["alpha"])
    request = _FakeRequest(context=context, tools=[base_tool])
    captured = {}

    async def handler(updated_request):
        captured["tools"] = updated_request.tools
        return SimpleNamespace()

    await middleware.awrap_model_call(request, handler)

    assert loaded_servers == ["alpha"]
    assert [tool.name for tool in captured["tools"]] == ["base_tool", "mcp_tool"]


async def test_chatbot_graph_build_does_not_scan_all_mcp_servers(monkeypatch):
    from yuxi.agents.buildin.chatbot import graph as chatbot_graph

    async def fail_if_global_mcp_scan_runs():
        raise AssertionError("graph build must not scan all enabled MCP servers")

    def named(name):
        return SimpleNamespace(name=name)

    async def fake_get_subagents_from_names(names):
        return []

    runtime_kwargs = []

    monkeypatch.setattr(chatbot_graph, "get_tools_from_all_servers", fail_if_global_mcp_scan_runs, raising=False)
    monkeypatch.setattr(chatbot_graph, "load_chat_model", lambda *args, **kwargs: named("model"))
    monkeypatch.setattr(chatbot_graph, "SummaryOffloadMiddleware", lambda **kwargs: named("summary"))
    monkeypatch.setattr(chatbot_graph, "get_subagents_from_names", fake_get_subagents_from_names)
    monkeypatch.setattr(chatbot_graph, "SubAgentMiddleware", lambda **kwargs: named("subagents"))
    monkeypatch.setattr(chatbot_graph, "FilesystemMiddleware", lambda **kwargs: named("filesystem"))
    monkeypatch.setattr(chatbot_graph, "PatchToolCallsMiddleware", lambda: named("patch"))
    monkeypatch.setattr(chatbot_graph, "KnowledgeBaseMiddleware", lambda: named("knowledge"))
    monkeypatch.setattr(chatbot_graph, "SkillsMiddleware", lambda: named("skills"))
    monkeypatch.setattr(chatbot_graph, "TodoListMiddleware", lambda **kwargs: named("todo"))
    monkeypatch.setattr(chatbot_graph, "ModelRetryMiddleware", lambda: named("retry"))
    monkeypatch.setattr(
        chatbot_graph,
        "RuntimeConfigMiddleware",
        lambda **kwargs: runtime_kwargs.append(kwargs) or named("runtime"),
    )

    context = SimpleNamespace(model="model", subagents_model="sub-model", subagents=[], summary_threshold=100)

    await chatbot_graph._build_middlewares(context)

    assert runtime_kwargs == [{}]


async def test_deep_agent_graph_build_does_not_scan_all_mcp_servers(monkeypatch):
    from yuxi.agents.buildin.deep_agent import graph as deep_graph

    async def fail_if_global_mcp_scan_runs():
        raise AssertionError("graph build must not scan all enabled MCP servers")

    def named(name):
        return SimpleNamespace(name=name)

    async def fake_get_tools(self):
        return []

    async def fake_get_checkpointer(self):
        return None

    async def fake_get_subagents_from_names(names):
        return []

    runtime_kwargs = []

    monkeypatch.setattr(deep_graph, "get_tools_from_all_servers", fail_if_global_mcp_scan_runs, raising=False)
    monkeypatch.setattr(deep_graph, "load_chat_model", lambda *args, **kwargs: named("model"))
    monkeypatch.setattr(deep_graph.DeepAgent, "get_tools", fake_get_tools)
    monkeypatch.setattr(deep_graph.DeepAgent, "_get_checkpointer", fake_get_checkpointer)
    monkeypatch.setattr(deep_graph, "get_subagents_from_names", fake_get_subagents_from_names)
    monkeypatch.setattr(deep_graph, "SummaryOffloadMiddleware", lambda **kwargs: named("summary"))
    monkeypatch.setattr(deep_graph, "SubAgentMiddleware", lambda **kwargs: named("subagents"))
    monkeypatch.setattr(deep_graph, "FilesystemMiddleware", lambda **kwargs: named("filesystem"))
    monkeypatch.setattr(deep_graph, "PatchToolCallsMiddleware", lambda: named("patch"))
    monkeypatch.setattr(deep_graph, "KnowledgeBaseMiddleware", lambda: named("knowledge"))
    monkeypatch.setattr(deep_graph, "SkillsMiddleware", lambda: named("skills"))
    monkeypatch.setattr(deep_graph, "TodoListMiddleware", lambda **kwargs: named("todo"))
    monkeypatch.setattr(deep_graph, "ToolCallLimitMiddleware", lambda **kwargs: named("limit"))
    monkeypatch.setattr(
        deep_graph,
        "RuntimeConfigMiddleware",
        lambda **kwargs: runtime_kwargs.append(kwargs) or named("runtime"),
    )
    monkeypatch.setattr(deep_graph, "create_agent", lambda **kwargs: kwargs)

    context = SimpleNamespace(model="model", subagents_model="sub-model", subagents=[])
    agent = deep_graph.DeepAgent()

    await agent.get_graph(context=context)

    assert runtime_kwargs == [{}]
