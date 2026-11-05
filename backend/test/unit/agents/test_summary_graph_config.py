from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.modules.agents.runtime.agent_backends.chatbot import graph as chatbot_graph
from yuxi.modules.agents.runtime.middlewares import summary as summary_module


def _context(summary_threshold: int = 123) -> SimpleNamespace:
    return SimpleNamespace(
        model="test-provider:test-model",
        summary_threshold=summary_threshold,
        summary_keep_messages=7,
        summary_prompt="SUMMARY {messages}",
        summary_tool_result_token_limit=300,
        tool_token_limit=3,
        model_retry_times=1,
        workdir_relative_path="projects/11111111-1111-4111-8111-111111111111",
        workdir_path="/home/user/workspace/projects/11111111-1111-4111-8111-111111111111",
    )


def _patch_common_graph_deps(monkeypatch: pytest.MonkeyPatch, graph_module, captured: dict) -> None:
    monkeypatch.setattr(graph_module, "create_agent_filesystem_middleware", lambda *_args, **_kwargs: object())

    def create_summary_middleware_from_context(context, *, backend):
        captured["summary_context"] = context
        captured["summary_backend"] = backend
        return object()

    monkeypatch.setattr(
        graph_module,
        "create_summary_middleware_from_context",
        create_summary_middleware_from_context,
    )


@pytest.mark.parametrize(
    ("graph_module", "threshold", "build_args", "patch_cooperation"),
    [
        (chatbot_graph, 123, (object(),), True),
    ],
)
@pytest.mark.unit
@pytest.mark.asyncio
async def test_graph_uses_shared_summary_middleware_factory(
    monkeypatch: pytest.MonkeyPatch, graph_module, threshold: int, build_args, patch_cooperation: bool
) -> None:
    captured: dict = {}
    _patch_common_graph_deps(monkeypatch, graph_module, captured)

    def no_cooperation_middleware(_context):
        return None

    if patch_cooperation:
        monkeypatch.setattr(graph_module, "create_cooperation_middleware", no_cooperation_middleware)

    middlewares = await graph_module._build_middlewares(_context(summary_threshold=threshold), *build_args)

    assert captured["summary_context"].summary_threshold == threshold
    assert captured["summary_backend"] is build_args[0]
    middleware_names = [type(middleware).__name__ for middleware in middlewares]
    assert middleware_names.index("NetworkRetryMiddleware") < middleware_names.index("ImageInputCompatibilityMiddleware")


@pytest.mark.unit
def test_shared_summary_factory_uses_one_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def load_model(fully_specified_name, *, session_id, uid, max_retries):
        """记录摘要模型实际接收的会话 ID 与用户 UID。"""
        captured["session_id"] = session_id
        captured["uid"] = uid
        return object()

    monkeypatch.setattr(summary_module, "load_chat_model", load_model)

    def create_summary_middleware(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(summary_module, "create_summary_middleware", create_summary_middleware)
    context = _context(summary_threshold=96)
    context.thread_id = "summary-thread"
    context.uid = "summary-uid"
    backend = object()

    summary_module.create_summary_middleware_from_context(context, backend=backend)

    assert captured["backend"] is backend
    assert captured["session_id"] == "summary-thread"
    assert captured["uid"] == "summary-uid"
    assert captured["trigger"] == ("tokens", 96 * 1024)
    assert "trim_tokens_to_summarize" not in captured
    assert "l1_l2_trigger_ratio" not in captured


@pytest.mark.parametrize(
    "graph_module,agent_class",
    [
        (chatbot_graph, chatbot_graph.ChatbotAgent),
    ],
)
@pytest.mark.asyncio
async def test_graph_passes_session_session_to_model(monkeypatch, graph_module, agent_class):
    """统一 Agent 构图使用实际线程的模型会话。"""
    context = _context()
    context.thread_id = "graph-thread"
    context.uid = "graph-uid"
    context._runtime_prepared = True
    monkeypatch.setattr(graph_module, "sync_agent_context_skills", AsyncMock())
    monkeypatch.setattr(graph_module, "resolve_configured_runtime_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(graph_module, "create_agent_composite_backend", lambda _context: object())
    monkeypatch.setattr(graph_module, "build_prompt_with_context", lambda _context: "test")
    monkeypatch.setattr(graph_module, "_build_middlewares", AsyncMock(return_value=[]))
    monkeypatch.setattr(graph_module, "get_langgraph_checkpointer", lambda _manager: None)
    captured = {}

    def load_model(fully_specified_name, *, session_id, uid, max_retries):
        """用装配参数作为模型占位，核对传给图的对象。"""
        captured.update(spec=fully_specified_name, session_id=session_id, uid=uid)
        return captured

    monkeypatch.setattr(graph_module, "load_chat_model", load_model)
    monkeypatch.setattr(graph_module, "create_agent", lambda **kwargs: kwargs)
    graph = await agent_class().get_graph(context=context)
    assert graph["model"] == {"spec": context.model, "session_id": context.thread_id, "uid": context.uid}


@pytest.mark.parametrize("agent_class", [chatbot_graph.ChatbotAgent])
@pytest.mark.asyncio
async def test_graph_rejects_unprepared_context(agent_class):
    """未经权限资源准备的对象不能构建执行图。"""
    with pytest.raises(ValueError, match="已准备"):
        await agent_class().get_graph(context=_context())


@pytest.mark.parametrize("tool_name", ["read_file", "create_session", "remote_probe"])
async def test_graph_rejects_mcp_tools_that_shadow_middleware(monkeypatch, tool_name):
    """远端工具经过真实 loader 后不能覆盖文件和协作工具。"""
    from langchain_core.tools import StructuredTool

    from yuxi.modules.extensions.mcp import service as mcp_service
    from yuxi.modules.extensions.tools.builtin import BASE_TOOL_NAMES

    assert tool_name not in BASE_TOOL_NAMES
    context = _context()
    context.thread_id, context.uid, context.run_id = "collision-thread", "collision-user", "collision-run"
    context._runtime_prepared = True
    context.mcps = ["remote"]
    context.tools = []
    remote = StructuredTool.from_function(lambda: "remote", name=tool_name, description="远端测试工具")
    monkeypatch.setattr(mcp_service, "resolve_runtime_mcp_tools", AsyncMock(return_value={"remote": [remote]}))
    monkeypatch.setattr(chatbot_graph, "sync_agent_context_skills", AsyncMock())
    monkeypatch.setattr(chatbot_graph, "create_agent_composite_backend", lambda _context: object())
    monkeypatch.setattr(chatbot_graph, "load_chat_model", lambda **_kwargs: object())
    monkeypatch.setattr(chatbot_graph, "create_memory_middleware", AsyncMock(return_value=None))
    monkeypatch.setattr(chatbot_graph, "build_prompt_with_context", lambda _context: "")
    monkeypatch.setattr(chatbot_graph, "create_summary_middleware_from_context", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(chatbot_graph, "get_langgraph_checkpointer", lambda _manager: None)
    monkeypatch.setattr(chatbot_graph, "create_agent", lambda **kwargs: kwargs)

    if tool_name == "remote_probe":
        graph = await chatbot_graph.ChatbotAgent().get_graph(context=context)
        assert remote in graph["tools"]
        assert context.mcp_tool_servers[tool_name] == "remote"
    else:
        with pytest.raises(RuntimeError, match=f"工具名冲突.*remote.*{tool_name}"):
            await chatbot_graph.ChatbotAgent().get_graph(context=context)


@pytest.mark.parametrize(
    "graph_module,agent_class",
    [
        (chatbot_graph, chatbot_graph.ChatbotAgent),
    ],
)
@pytest.mark.unit
@pytest.mark.asyncio
async def test_checkpoint_cleanup_preserves_original_tasks_and_state(monkeypatch, graph_module, agent_class):
    """维护图保留真实拓扑与状态，不依赖模型、资源或有效账号。"""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from yuxi.modules.agents.runtime.checkpoint_cleanup import CheckpointCleanupModel, cancel_waitpoint_checkpoint

    context = _context()
    context.thread_id, context.uid = "cleanup-thread", "deleted-user"
    context.run_id = "cleanup-run"
    context._runtime_prepared = True
    context.tool_approval_mode = "default"
    saver = InMemorySaver()
    monkeypatch.setattr(graph_module, "get_langgraph_checkpointer", lambda _manager: saver)
    monkeypatch.setattr(graph_module, "sync_agent_context_skills", AsyncMock())
    monkeypatch.setattr(graph_module, "resolve_configured_runtime_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(graph_module, "create_agent_composite_backend", lambda _context: None)
    monkeypatch.setattr(graph_module, "build_prompt_with_context", lambda _context: "")
    monkeypatch.setattr(graph_module, "load_chat_model", lambda **_kwargs: CheckpointCleanupModel())
    monkeypatch.setattr(summary_module, "load_chat_model", lambda **_kwargs: CheckpointCleanupModel())
    if graph_module is chatbot_graph:
        monkeypatch.setattr(graph_module, "create_memory_middleware", AsyncMock(return_value=None))
    original = await agent_class().get_graph(context=context)
    assert "CooperationMiddleware.before_model" in original.nodes
    config = {"configurable": {"thread_id": context.thread_id}}
    await original.aupdate_state(
        config,
        {
            "messages": [AIMessage(id="pending", content="", tool_calls=[{"id": "one", "name": "write_file", "args": {}}])],
            "activated_skills": ["retained"],
        },
        as_node="model",
    )
    assert (await original.aget_state(config)).next

    def unexpected(*_args, **_kwargs):
        """维护构图不得解析外部模型或沙盒。"""
        raise AssertionError("清理读取了运行资源")

    for name in (
        "load_chat_model",
        "create_agent_composite_backend",
        "resolve_chat_model_spec",
        "sync_agent_context_skills",
        "create_memory_middleware",
        "resolve_configured_runtime_tools",
        "build_prompt_with_context",
    ):
        monkeypatch.setattr(graph_module, name, unexpected)
    monkeypatch.setattr(summary_module, "load_chat_model", unexpected)
    cleanup = await agent_class().get_graph(context=context, checkpoint_only=True)
    assert set(cleanup.nodes) == set(original.nodes)
    assert set(cleanup.channels) == set(original.channels)
    await cancel_waitpoint_checkpoint(cleanup, config)
    saved = await original.aget_state(config)
    assert saved.next == () and saved.interrupts == ()
    assert saved.values["messages"][-1].content == "[已取消]"
    assert saved.values["messages"][-1].tool_call_id == "one"
    assert saved.values["activated_skills"] == ["retained"]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_image_fallback_uses_authorized_tools_from_shipping_middleware_order(monkeypatch, enabled):
    """生产装配下，图片回退与实际模型看到的授权工具集一致。"""
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import ToolMessage
    from yuxi.modules.agents.runtime.middlewares import authorization
    from yuxi.modules.extensions.tools.builtin.ocr_parse_file import ocr_parse_file

    context = _context()
    context.tools = ["ocr_parse_file"] if enabled else []
    context.mcps = []
    context.registered_builtin_tool_names = {"ocr_parse_file"}
    context.mcp_tool_servers = {}
    context.skill_runtime_snapshot = {"preloaded_skills": [], "effective_skills": [], "runtime_skills": {}}
    _patch_common_graph_deps(monkeypatch, chatbot_graph, {})
    monkeypatch.setattr(chatbot_graph, "create_memory_middleware", AsyncMock(return_value=None))
    monkeypatch.setattr(chatbot_graph, "create_cooperation_middleware", lambda _context: None)
    monkeypatch.setattr(authorization, "refresh_execution_authorization", AsyncMock(return_value={}))
    middlewares = await chatbot_graph._build_middlewares(context, object())
    wrappers = [
        middleware
        for middleware in middlewares
        if isinstance(middleware, (authorization.RuntimeAuthorizationMiddleware, chatbot_graph.ImageInputCompatibilityMiddleware))
    ]
    seen_tools = []

    async def reject(request):
        seen_tools.extend(tool.name for tool in request.tools)
        error = RuntimeError("This model does not support image input")
        error.status_code = 400
        raise error

    handler = reject
    for middleware in reversed(wrappers):
        next_handler = handler

        async def handler(request, middleware=middleware, next_handler=next_handler):
            return await middleware.awrap_model_call(request, next_handler)

    request = ModelRequest(
        model=SimpleNamespace(),
        tools=[ocr_parse_file],
        messages=[
            ToolMessage(
                content_blocks=[{"type": "image", "base64": "abc", "mime_type": "image/png"}],
                tool_call_id="read-image",
                additional_kwargs={"read_file_path": "/home/user/workspace/uploads/image.png"},
            )
        ],
        runtime=SimpleNamespace(context=context),
        state={},
    )
    response = await handler(request)
    assert seen_tools == (["ocr_parse_file"] if enabled else [])
    assert [call["name"] for call in response.result[0].tool_calls] == seen_tools


@pytest.mark.asyncio
async def test_usage_counts_actual_authorized_and_bridged_model_request(monkeypatch):
    """统计的工具和图片消息与 provider 收到的同一请求一致。"""
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_openai import ChatOpenAI
    from yuxi.modules.agents.runtime.middlewares import authorization
    from yuxi.modules.extensions.tools.builtin.ocr_parse_file import ocr_parse_file

    context = _context()
    context.tools, context.mcps = [], []
    context.registered_builtin_tool_names = {"ocr_parse_file"}
    context.mcp_tool_servers = {}
    context.skill_runtime_snapshot = {"preloaded_skills": [], "effective_skills": [], "runtime_skills": {}}
    _patch_common_graph_deps(monkeypatch, chatbot_graph, {})
    monkeypatch.setattr(chatbot_graph, "create_memory_middleware", AsyncMock(return_value=None))
    monkeypatch.setattr(chatbot_graph, "create_cooperation_middleware", lambda _context: None)
    monkeypatch.setattr(authorization, "refresh_execution_authorization", AsyncMock(return_value={}))
    middlewares = await chatbot_graph._build_middlewares(context, object())
    wrappers = [
        m
        for m in middlewares
        if isinstance(
            m,
            (
                authorization.RuntimeAuthorizationMiddleware,
                chatbot_graph.ImageInputCompatibilityMiddleware,
                chatbot_graph.TokenUsageMiddleware,
            ),
        )
    ]
    received = []

    async def provider(request):
        received.append(request)
        return ModelResponse(result=[AIMessage(content="ok")])

    handler = provider
    for middleware in reversed(wrappers):
        previous = handler

        async def handler(request, middleware=middleware, previous=previous):
            return await middleware.awrap_model_call(request, previous)

    image = ToolMessage(content_blocks=[{"type": "image", "base64": "abc", "mime_type": "image/png"}], tool_call_id="image")
    request = ModelRequest(
        model=ChatOpenAI(model="fixture", api_key="test", metadata={"yuxi_provider_id": "fixture"}),
        tools=[ocr_parse_file],
        messages=[image],
        runtime=SimpleNamespace(context=context),
        state={"messages": [image]},
    )
    response = await handler(request)
    actual = received[0]
    usage = response.command.update["token_usage"]
    counter = next(m for m in wrappers if isinstance(m, chatbot_graph.TokenUsageMiddleware))
    assert actual.tools == []
    assert [message.type for message in actual.messages] == ["tool", "human"]
    assert usage["tool_count"] == len(actual.tools)
    assert usage["llm_message_count"] == len(actual.messages)
    assert usage["llm_input_tokens"] == counter._count_tokens(actual.messages, tools=actual.tools)
