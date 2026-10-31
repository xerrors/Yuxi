"""已注册工具也必须符合最新授权与 Skill 激活状态。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.agents.runtime.context import BaseContext
from yuxi.modules.agents.runtime.middlewares.authorization import tool_allowed


@pytest.fixture
def context():
    """构图时注册工具、执行时权限独立变化的 Context。"""
    return BaseContext(
        tools=[],
        mcps=[],
        registered_builtin_tool_names={"skill_tool"},
        mcp_tool_servers={"remote_tool": "remote"},
        skill_runtime_snapshot={
            "effective_skills": ["skill"],
            "preloaded_skills": [],
            "runtime_skills": {"skill": {"tools": ["skill_tool"], "mcps": ["remote"]}},
        },
    )


@pytest.fixture
def mcp_policy():
    """本次 service 查询结果只在当前调用内传递。"""
    return {"remote": set()}


def test_unactivated_skill_cannot_execute_registered_local_or_mcp_dependency(context, mcp_policy):
    """直接调用隐藏工具不能代替读取或预加载 Skill。"""
    assert not tool_allowed(context, "skill_tool", {}, mcp_policy)
    assert not tool_allowed(context, "remote_tool", {}, mcp_policy)
    assert tool_allowed(context, "skill_tool", {"activated_skills": ["skill"]}, mcp_policy)
    context.skill_runtime_snapshot["effective_skills"] = []
    assert not tool_allowed(context, "skill_tool", {"activated_skills": ["skill"]}, mcp_policy)


def test_mcp_single_tool_or_server_disable_rejects_old_registration(context, mcp_policy):
    """启停服务器和单工具都不能被既有对象绕过。"""
    state = {"activated_skills": ["skill"]}
    assert tool_allowed(context, "remote_tool", state, mcp_policy)
    mcp_policy["remote"] = {"remote_tool"}
    assert not tool_allowed(context, "remote_tool", state, mcp_policy)
    mcp_policy.clear()
    assert not tool_allowed(context, "remote_tool", state, mcp_policy)


def test_direct_mcp_disconnect_rejects_registered_tool_before_outbound(context, mcp_policy):
    """直接配置的旧工具也复用本轮连接查询，断开后不能继续出站。"""
    context.mcps = ["remote"]
    mcp_policy = {}
    assert not tool_allowed(context, "remote_tool", {}, mcp_policy)


@pytest.mark.parametrize("name", ["ask_user_question", "mark_for_improvement"])
def test_base_tool_is_allowed_without_configuration_skill_or_run(context, mcp_policy, name):
    """Backend 显式装配的基础工具不依赖资源选择，MCP 同名仍按来源授权。"""
    context.middleware_tool_names = {name}
    context.registered_builtin_tool_names.add(name)
    assert tool_allowed(context, name, {}, mcp_policy)
    context.mcp_tool_servers[name] = "remote"
    assert not tool_allowed(context, name, {}, mcp_policy)


def test_registered_tool_does_not_bypass_mcp_source_authorization(context, mcp_policy):
    """工具名称已允许时，MCP 来源仍需在当前资源与权限内。"""
    assert not tool_allowed(context, "remote_tool", {}, mcp_policy)
    context.mcps = ["remote"]
    assert tool_allowed(context, "remote_tool", {}, mcp_policy)
    mcp_policy["remote"].add("remote_tool")
    assert not tool_allowed(context, "remote_tool", {}, mcp_policy)


@pytest.mark.asyncio
async def test_network_retry_rechecks_before_each_model_request(monkeypatch, context):
    """网络失败同时撤权后，不得发送第二个实际模型请求。"""
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from yuxi.modules.agents.runtime.middlewares import authorization
    from yuxi.modules.agents.runtime.middlewares.network_retry import NetworkRetryMiddleware

    requests = []
    revoked = False

    class ReplayModel(BaseChatModel):
        """首次网络失败将身份置为撤权，独立记录真正的模型调用。"""

        @property
        def _llm_type(self):
            """返回本地测试模型类型。"""
            return "authorization-retry"

        def _generate(self, *args, **kwargs):
            """本测试只使用真实异步图。"""
            raise NotImplementedError

        async def _agenerate(self, *args, **kwargs):
            """记录请求后模拟连接失败。"""
            nonlocal revoked
            requests.append(revoked)
            if len(requests) == 1:
                revoked = True
                raise ConnectionError("network interrupted")
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="forbidden"))])

    async def check(_context, **_kwargs):
        """授权 oracle 与模型请求记录独立。"""
        if revoked:
            raise authorization.AgentExecutionRevoked("revoked")
        return {}

    monkeypatch.setattr(authorization, "refresh_execution_authorization", check)
    monkeypatch.setattr(authorization, "_refresh_execution_resources", check)
    graph = create_agent(
        model=ReplayModel(),
        middleware=[
            NetworkRetryMiddleware(max_retries=0, network_initial_delay=0.001, network_budget_seconds=1),
            authorization.RuntimeAuthorizationMiddleware(),
        ],
    )
    with pytest.raises(authorization.AgentExecutionRevoked):
        await graph.ainvoke({"messages": [HumanMessage(content="test")]}, context=context)
    assert requests == [False]


@pytest.mark.asyncio
async def test_context_overflow_does_not_send_summary_after_revocation(monkeypatch, context):
    """上下文溢出期间撤权后，独立摘要模型也不能发送新请求。"""
    from deepagents.backends import CompositeBackend
    from deepagents.backends.protocol import WriteResult
    from langchain.agents import create_agent
    from langchain_core.exceptions import ContextOverflowError
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from yuxi.modules.agents.runtime.middlewares import authorization
    from yuxi.modules.agents.runtime.middlewares import summary as summary_module

    requests, summaries = [], []
    revoked = False

    class ReplayModel(BaseChatModel):
        """独立模拟撤权后的上下文溢出响应。"""

        @property
        def _llm_type(self):
            """返回测试模型类型。"""
            return "authorization-overflow"

        def _generate(self, *args, **kwargs):
            """本测试只使用异步执行。"""
            raise NotImplementedError

        async def _agenerate(self, *args, **kwargs):
            """首个请求失败并撤销权限。"""
            nonlocal revoked
            requests.append(revoked)
            if len(requests) == 1:
                revoked = True
                raise ContextOverflowError("context overflow after revocation")
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="forbidden"))])

    class SummaryModel:
        """独立记录摘要请求。"""

        _llm_type = "review-summary"
        profile = {"max_input_tokens": 128000, "max_output_tokens": 32768}

        def _get_ls_params(self):
            """提供测试模型的 tracing 类型。"""
            return {"ls_provider": "openai"}

        def with_retry(self, **kwargs):
            """保持摘要构造器协议。"""
            return self

        async def ainvoke(self, *args, **kwargs):
            """记录实际摘要调用而不是中间件次数。"""
            summaries.append(revoked)
            return AIMessage(content="forbidden summary")

    class MemoryBackend:
        """提供摘要文件协议，隔离模型调用 oracle。"""

        async def awrite(self, path, content):
            """返回已写入协议结果。"""
            return WriteResult(path=path, error=None)

        async def adownload_files(self, paths):
            """测试历史不含已存在媒体。"""
            return [SimpleNamespace(content=None, error="file_not_found") for _ in paths]

    async def check(_context, **_kwargs):
        """模型返回后按当前事实拒绝授权。"""
        if revoked:
            raise authorization.AgentExecutionRevoked("revoked")
        return {}

    monkeypatch.setattr(authorization, "refresh_execution_authorization", check)
    monkeypatch.setattr(authorization, "_refresh_execution_resources", check)
    summary = summary_module.YuxiSummarizationMiddleware(
        model=SummaryModel(),
        backend=CompositeBackend(default=MemoryBackend(), routes={}, artifacts_root="/outputs"),
        trigger=("tokens", 100_000),
        keep=("messages", 1),
    )
    # 与 shipping factory 注入同一真实调用者 Context。
    summary.authorization_context = context
    graph = create_agent(model=ReplayModel(), middleware=[summary, authorization.RuntimeAuthorizationMiddleware()])
    with pytest.raises(authorization.AgentExecutionRevoked):
        await graph.ainvoke(
            {"messages": [HumanMessage(content="old"), AIMessage(content="old answer"), HumanMessage(content="now")]},
            context=context,
        )
    assert summaries == []
    assert requests == [False]


@pytest.mark.asyncio
async def test_mcp_failure_becomes_safe_tool_result_and_disables_service(monkeypatch, context):
    """工具边界保留安全原因并关闭本轮失败 MCP。"""
    from unittest.mock import AsyncMock

    from yuxi.modules.agents.runtime.middlewares import authorization
    from yuxi.shared.errors import StateConflictError

    context.mcps, context.mcp_unavailable, context.run_id = ["remote"], {}, "run"
    monkeypatch.setattr(authorization, "_refresh_execution_resources", AsyncMock(return_value={"remote": set()}))
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        state={},
        tool_call={"name": "remote_tool", "id": "call"},
    )
    logs = []
    sink = authorization.logger.add(lambda message: logs.append(str(message)))
    try:
        result = await authorization.RuntimeAuthorizationMiddleware().awrap_tool_call(
            request,
            AsyncMock(side_effect=ExceptionGroup("TaskGroup", [StateConflictError("secret", code="mcp_reconnect_required")])),
        )
    finally:
        authorization.logger.remove(sink)
    assert "secret" not in "".join(logs) and "run_id=run" in "".join(logs)
    assert result.status == "error" and result.tool_call_id == "call"
    assert "remote" in result.content and "重新连接" in result.content
    assert "secret" not in result.content and "TaskGroup" not in result.content
    assert "remote" in context.mcp_unavailable


@pytest.mark.asyncio
async def test_failed_mcp_filters_skill_dependencies_and_updates_warning_state(monkeypatch, context):
    """失败服务仍在数据库显示已连接时，模型与执行权限以发现结果收敛。"""
    from unittest.mock import AsyncMock

    from langchain.agents.middleware import ModelRequest
    from langchain_core.messages import SystemMessage

    from yuxi.modules.agents.runtime.middlewares import authorization

    context.mcps, context.mcp_unavailable = ["remote"], {"remote": "授权凭据不可用，请重新连接"}
    context._capability_limited = False
    run = SimpleNamespace(id="run", thread_id="thread", turn_id="turn")

    class SessionContext:
        """为服务查询提供异步 session 边界。"""

        async def __aenter__(self):
            """返回测试查询上下文。"""
            return None

        async def __aexit__(self, *_):
            """保持异常传播。"""
            return False

    monkeypatch.setattr(authorization.pg_manager, "get_async_session_context", SessionContext)
    monkeypatch.setattr(authorization, "_require_execution_actor", AsyncMock(return_value=(SimpleNamespace(id=1), None, run)))
    monkeypatch.setattr(authorization, "refresh_agent_context_resources", AsyncMock())
    monkeypatch.setattr(authorization, "sync_agent_context_skills", AsyncMock())
    monkeypatch.setattr(authorization, "available_mcp_servers", AsyncMock(return_value={"remote": SimpleNamespace(disabled_tools=[])}))
    state = {"activated_skills": ["skill"]}
    policy = await authorization.refresh_execution_authorization(context, state=state)
    assert policy == {}
    assert not tool_allowed(context, "remote_tool", state, policy)
    assert not tool_allowed(context, "skill_tool", state, policy)
    request = ModelRequest(
        model=None,
        messages=[],
        system_message=SystemMessage(content="原始提示"),
        tools=[SimpleNamespace(name="remote_tool"), SimpleNamespace(name="skill_tool")],
        state=state,
        runtime=SimpleNamespace(context=context),
    )
    captured = []

    async def handler(actual):
        """回读发往模型的工具与提示。"""
        captured.append(actual)
        return "普通聊天继续"

    result = await authorization.RuntimeAuthorizationMiddleware().awrap_model_call(request, handler)
    assert result == "普通聊天继续" and not captured[0].tools
    assert "原始提示" in str(captured[0].system_message.content)
    middleware = authorization.RuntimeAuthorizationMiddleware()
    runtime = SimpleNamespace(context=context)
    update = await middleware.abefore_model(state, runtime)
    assert update["warnings"] == [{"code": "mcp_unavailable", "source": "mcp:remote", "message": "MCP remote：授权凭据不可用，请重新连接"}]
    assert await middleware.aafter_model(state, runtime) == update
    context.mcp_unavailable = {}
    assert await middleware.abefore_model(state, runtime) == {"warnings": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["revoked", "interrupt", "cancelled"])
async def test_mcp_tool_preserves_execution_control_exceptions(monkeypatch, context, kind):
    """取消、人工等待和撤权不能转换成可继续聊天的 MCP 故障。"""
    import asyncio
    from unittest.mock import AsyncMock

    from langgraph.errors import GraphInterrupt

    from yuxi.modules.agents.runtime.middlewares import authorization

    failure = {
        "revoked": authorization.AgentExecutionRevoked("执行权限撤销"),
        "interrupt": GraphInterrupt(),
        "cancelled": asyncio.CancelledError(),
    }[kind]
    context.mcps, context.mcp_unavailable = ["remote"], {}
    monkeypatch.setattr(authorization, "_refresh_execution_resources", AsyncMock(return_value={"remote": set()}))
    request = SimpleNamespace(runtime=SimpleNamespace(context=context), state={}, tool_call={"name": "remote_tool", "id": "call"})
    with pytest.raises(type(failure)):
        await authorization.RuntimeAuthorizationMiddleware().awrap_tool_call(request, AsyncMock(side_effect=failure))
    assert context.mcp_unavailable == {}


@pytest.mark.asyncio
async def test_warning_checkpoint_is_replaced_after_capability_recovers(monkeypatch, context):
    """同一线程下一轮恢复能力后，checkpoint 不残留上一轮警告。"""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from yuxi.modules.agents.runtime.middlewares import authorization

    @asynccontextmanager
    async def session():
        """隔离数据库查询，保留真实图状态提交。"""
        yield None

    monkeypatch.setattr(authorization.pg_manager, "get_async_session_context", session)
    monkeypatch.setattr(authorization, "_require_execution_actor", AsyncMock(return_value=(SimpleNamespace(id=1), None, None)))
    monkeypatch.setattr(authorization, "_refresh_execution_resources", AsyncMock(return_value={}))
    monkeypatch.setattr(authorization, "sync_agent_context_skills", AsyncMock())
    context.mcp_unavailable = {"remote": "未连接，请重新连接", "github": "连接失败"}
    graph = create_agent(
        model=FakeListChatModel(responses=["普通聊天正常"]),
        middleware=[authorization.RuntimeAuthorizationMiddleware()],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "warning-thread"}}
    await graph.ainvoke({"messages": [HumanMessage(content="你好")]}, config, context=context)
    warnings = (await graph.aget_state(config)).values["warnings"]
    assert [item["source"] for item in warnings] == ["mcp:remote", "mcp:github"]
    assert all(item["code"] == "mcp_unavailable" for item in warnings)
    context.mcp_unavailable.clear()
    await graph.ainvoke({"messages": [HumanMessage(content="继续")]}, config, context=context)
    assert (await graph.aget_state(config)).values["warnings"] == []


@pytest.mark.asyncio
async def test_failed_session_cleanup_does_not_publish_skill_snapshot(monkeypatch):
    """事务退出失败时，临时解析结果不能污染下一次授权刷新。"""
    from unittest.mock import AsyncMock

    from yuxi.modules.agents.runtime.context import BaseContext
    from yuxi.modules.agents.runtime.middlewares import authorization

    context = BaseContext()
    original = {"skill_metadata": {"a": {"source_scope": "agent_bound"}}}
    context.skill_runtime_snapshot = original

    class FailedSession:
        """模拟查询成功而事务退出失败。"""

        async def __aenter__(self):
            return None

        async def __aexit__(self, *_):
            raise ConnectionError("connection lost during cleanup")

    async def refresh(candidate, **_kwargs):
        candidate.skill_runtime_snapshot = {"skill_metadata": {}}

    monkeypatch.setattr(authorization.pg_manager, "get_async_session_context", FailedSession)
    monkeypatch.setattr(authorization, "_require_execution_actor", AsyncMock(return_value=(None, None, None)))
    monkeypatch.setattr(authorization, "refresh_agent_context_resources", refresh)
    monkeypatch.setattr(authorization, "_resolve_mcp_policy", AsyncMock(return_value={}))
    with pytest.raises(ConnectionError):
        await authorization._refresh_execution_resources(context)
    assert context.skill_runtime_snapshot is original


@pytest.mark.asyncio
async def test_successful_resource_refresh_publishes_context_fields_after_transaction(monkeypatch, context):
    """刷新使用浅拷贝，事务退出前不发布，成功后替换契约字段。"""
    from unittest.mock import AsyncMock

    from yuxi.modules.agents.runtime.middlewares import authorization

    original = context.skill_runtime_snapshot
    updated = {"skill_metadata": {}}
    context.mcp_tools = {"remote": [object()]}

    class SessionContext:
        """在事务退出点核对尚未发布的真实 Context。"""

        async def __aenter__(self):
            return None

        async def __aexit__(self, *_):
            assert context.skill_runtime_snapshot is original
            assert context.agent_slug is None
            return False

    async def refresh(candidate, **_kwargs):
        """只替换待发布资源，保留运行内对象引用。"""
        assert isinstance(candidate, BaseContext) and candidate is not context
        assert candidate.mcp_tools is context.mcp_tools
        candidate.skill_runtime_snapshot = updated
        candidate.tools = ["new_tool"]
        candidate._resource_selections = {"tools": ["new_tool"]}
        candidate._capability_limited = True
        candidate.agent_slug = "current-agent"

    monkeypatch.setattr(authorization.pg_manager, "get_async_session_context", SessionContext)
    monkeypatch.setattr(authorization, "_require_execution_actor", AsyncMock(return_value=(None, None, None)))
    monkeypatch.setattr(authorization, "refresh_agent_context_resources", refresh)
    monkeypatch.setattr(authorization, "_resolve_mcp_policy", AsyncMock(return_value={"remote": set()}))
    policy = await authorization._refresh_execution_resources(context)
    assert policy == {"remote": set()}
    assert context.skill_runtime_snapshot is updated
    assert context.tools == ["new_tool"]
    assert context._resource_selections == {"tools": ["new_tool"]}
    assert context._capability_limited is True
    assert context.agent_slug == "current-agent"


@pytest.mark.asyncio
async def test_tool_boundary_refreshes_resources_without_syncing_projection(monkeypatch, context):
    """工具边界刷新授权资源与 MCP 策略，不重建文件投影。"""
    from unittest.mock import AsyncMock

    from yuxi.modules.agents.runtime.middlewares import authorization

    class SessionContext:
        """为工具边界的身份复查提供异步 session 边界。"""

        async def __aenter__(self):
            """返回测试查询上下文。"""
            return None

        async def __aexit__(self, *_):
            """保持异常传播。"""
            return False

    context.mcps, context.run_id = ["remote"], "run"
    actor = AsyncMock(return_value=(SimpleNamespace(id=1), None, None))
    resources, sync = AsyncMock(return_value={}), AsyncMock()
    monkeypatch.setattr(authorization.pg_manager, "get_async_session_context", SessionContext)
    monkeypatch.setattr(authorization, "_require_execution_actor", actor)
    monkeypatch.setattr(authorization, "refresh_agent_context_resources", resources)
    monkeypatch.setattr(authorization, "sync_agent_context_skills", sync)
    monkeypatch.setattr(authorization, "available_mcp_servers", AsyncMock(return_value={"remote": SimpleNamespace(disabled_tools=[])}))
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        state={"activated_skills": ["skill"]},
        tool_call={"name": "remote_tool", "id": "call"},
    )
    result = await authorization.RuntimeAuthorizationMiddleware().awrap_tool_call(
        request,
        AsyncMock(return_value="工具继续执行"),
    )
    assert result == "工具继续执行"
    assert actor.await_count == 1
    assert resources.await_count == 1 and sync.await_count == 0


@pytest.mark.asyncio
async def test_transient_mcp_failure_does_not_disable_later_call(monkeypatch, context):
    from unittest.mock import AsyncMock

    from langchain_core.messages import ToolMessage

    from yuxi.modules.agents.runtime.middlewares import authorization

    context.mcps, context.mcp_unavailable, context.run_id = ["remote"], {}, "run"
    monkeypatch.setattr(authorization, "_refresh_execution_resources", AsyncMock(return_value={"remote": set()}))
    request = SimpleNamespace(runtime=SimpleNamespace(context=context), state={}, tool_call={"name": "remote_tool", "id": "call"})
    middleware = authorization.RuntimeAuthorizationMiddleware()
    first = await middleware.awrap_tool_call(request, AsyncMock(side_effect=ConnectionError("private")))
    second = await middleware.awrap_tool_call(request, AsyncMock(return_value=ToolMessage(content="ok", tool_call_id="call")))
    assert first.status == "error" and "private" not in first.content
    assert second.content == "ok"
    assert context.mcp_unavailable == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoked", "source_replaced", "temporary_grant"])
async def test_skill_request_identity_survives_authorization_retry(monkeypatch, change):
    """已注入身份固定，撤权或换来源拒绝，未注入的临时授权不触发误判。"""
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.messages import AIMessage

    from yuxi.modules.agents.runtime.middlewares import authorization
    from yuxi.modules.agents.runtime.middlewares.network_retry import NetworkRetryMiddleware
    from yuxi.modules.agents.runtime.middlewares.skills import SkillsMiddleware

    def snapshot(slugs, scope="shared"):
        return {
            "effective_skills": slugs,
            "preloaded_skills": ["a"],
            "preloaded_skill_contents": {"a": "A-INJECTED"},
            "skill_metadata": {slug: {"source_scope": scope, "version": "1", "content_hash": slug} for slug in slugs},
            "runtime_skills": {
                slug: {"name": slug, "description": slug, "path": f"/{slug}/SKILL.md", "tools": [], "mcps": [], "skills": []}
                for slug in slugs
            },
        }

    context = BaseContext(
        skill_runtime_snapshot=snapshot(["a"]),
        tools=[],
        mcps=[],
        mcp_tool_servers={},
        registered_builtin_tool_names=set(),
        mcp_tools={},
        mcp_unavailable={},
        uid="fixture",
        middleware_tool_names=set(),
    )
    attempt = 0
    received = []

    async def refresh(_context, **_kwargs):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            context.skill_runtime_snapshot = snapshot(["a"])
        elif change == "revoked":
            context.skill_runtime_snapshot = snapshot([])
        elif change == "source_replaced":
            context.skill_runtime_snapshot = snapshot(["a"], scope="personal")
        elif attempt == 2:
            context.skill_runtime_snapshot = snapshot(["a", "b"])
            raise ConnectionError("transaction cleanup interrupted")
        else:
            context.skill_runtime_snapshot = snapshot(["a"])
        return {}

    async def no_projection(_context):
        pass

    async def model(request):
        received.append(request.system_message.text)
        if len(received) == 1:
            raise ConnectionError("network interrupted")
        return ModelResponse(result=[AIMessage(content="ok")])

    monkeypatch.setattr(authorization, "_refresh_execution_resources", refresh)
    monkeypatch.setattr(authorization, "sync_agent_context_skills", no_projection)
    auth = authorization.RuntimeAuthorizationMiddleware()
    retry = NetworkRetryMiddleware(max_retries=0, network_initial_delay=0.001, network_budget_seconds=1)
    skills = SkillsMiddleware()
    request = ModelRequest(model=SimpleNamespace(), messages=[], tools=[], state={}, runtime=SimpleNamespace(context=context))

    async def authorized(req):
        return await auth.awrap_model_call(req, model)

    async def retried(req):
        return await retry.awrap_model_call(req, authorized)

    if change == "temporary_grant":
        result = await skills.awrap_model_call(request, retried)
        assert result.result[0].content == "ok"
        assert len(received) == 2 and all("A-INJECTED" in prompt and "B-INJECTED" not in prompt for prompt in received)
    else:
        with pytest.raises(authorization.AgentExecutionRevoked):
            await skills.awrap_model_call(request, retried)
        assert len(received) == 1 and "A-INJECTED" in received[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_kind", ["python", "domain"])
async def test_mcp_permission_error_is_not_converted_to_tool_failure(monkeypatch, context, error_kind):
    """真实外层 Guard 不得把 MCP 权限拒绝转为可继续运行的工具结果。"""
    from unittest.mock import AsyncMock

    from yuxi.modules.agents.runtime.middlewares import authorization
    from yuxi.modules.agents.runtime.middlewares.tool_error_guard import ToolErrorGuardMiddleware
    from yuxi.shared.errors import PermissionDeniedError

    context.mcps, context.mcp_unavailable, context.run_id = ["remote"], {}, "run"
    monkeypatch.setattr(authorization, "_refresh_execution_resources", AsyncMock(return_value={"remote": set()}))
    request = SimpleNamespace(
        runtime=SimpleNamespace(context=context, config={}), state={}, tool_call={"name": "remote_tool", "id": "call"}
    )
    error_type = PermissionError if error_kind == "python" else PermissionDeniedError
    failure = error_type("revoked")
    failure.__cause__ = ConnectionError("disconnected")

    async def authorized(current):
        """按生产顺序执行 MCP 授权与工具异常保护。"""
        return await authorization.RuntimeAuthorizationMiddleware().awrap_tool_call(current, AsyncMock(side_effect=failure))

    with pytest.raises(authorization.AgentExecutionRevoked) as caught:
        await ToolErrorGuardMiddleware().awrap_tool_call(request, authorized)
    assert caught.value.__cause__ is failure
    assert context.mcp_unavailable == {}
