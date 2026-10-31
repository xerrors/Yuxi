"""个人助理的运行身份、工具与提示词边界。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from yuxi.modules.agents.runtime import graph as runtime_graph
from yuxi.modules.agents.runtime.agent_backends.chatbot import graph as chatbot_graph
from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
from yuxi.modules.agents.runtime.agent_backends.personal_assistant import graph as personal_graph
from yuxi.modules.agents.runtime.agent_backends.personal_assistant.graph import (
    PersonalAssistantAgent,
    PersonalAssistantContext,
)
from yuxi.modules.agents.runtime.agent_backends.personal_assistant.prompt import build_personal_prompt
from yuxi.modules.agents.runtime.checkpoint_cleanup import CheckpointCleanupModel
from yuxi.modules.agents.runtime.context import filter_declared_config
from yuxi.modules.agents.runtime.middlewares import authorization
from yuxi.modules.agents.runtime.middlewares import summary as summary_module
from yuxi.modules.agents.runtime.sandbox import backend as sandbox_backend
from yuxi.modules.agents.runtime.sandbox.backend import UserSandboxBackend, create_agent_composite_backend
from yuxi.modules.workspace.paths import ensure_user_workspace, user_workspace_dir


def test_runtime_identity_cannot_be_set_through_agent_configuration():
    """伪造配置不能把普通 Agent 变成个人助理或修改可信名字。"""
    supplied = {
        "context": {
            "personal_assistant": True,
            "personal_assistant_name": "伪造",
            "system_prompt": "偏好简洁",
        }
    }
    filtered = filter_declared_config(supplied, ChatBotContext)["context"]
    assert filtered == {"system_prompt": "偏好简洁"}
    context = ChatBotContext()
    context.update_config(supplied["context"])
    assert not hasattr(context, "personal_assistant")
    assert not hasattr(context, "personal_assistant_name")
    assert context.system_prompt == "偏好简洁"


def test_personal_defaults_use_normal_configuration_fields():
    """页面默认能力不另设执行禁令，显式配置仍走普通字段和授权。"""
    context = PersonalAssistantContext()
    assert set(context.tools) == {"web_search", "web_fetch"}
    assert context.skills == context.preload_skills == context.mcps == context.knowledges == []
    context.update_config({"tools": ["web_fetch"], "skills": ["plugin"], "tool_approval_mode": "always_trust", "uid": "forged"})
    assert context.tools == ["web_fetch"]
    assert context.skills == ["plugin"]
    assert context.tool_approval_mode == "always_trust"
    assert context.uid != "forged"


async def test_personal_preparation_preserves_frozen_model(monkeypatch):
    """已冻结的专用模型经过普通准备流程时不再读取或选择模型。"""
    from contextlib import asynccontextmanager

    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.modules.agents.runtime import context as context_module
    from yuxi.modules.identity.repositories.users import UserRepository

    @asynccontextmanager
    async def database():
        """隔离数据库连接，保留实际 Context 默认模型解析。"""
        yield object()

    monkeypatch.setattr(pg_manager, "get_async_session_context", database)
    monkeypatch.setattr(context_module, "_append_workspace_agent_prompt", AsyncMock())
    monkeypatch.setattr(context_module, "refresh_agent_context_resources", AsyncMock())
    monkeypatch.setattr(UserRepository, "get_by_uid_with_db", AsyncMock(return_value=SimpleNamespace(is_deleted=0)))
    monkeypatch.setattr(
        context_module,
        "system_options",
        SimpleNamespace(get=AsyncMock(side_effect=AssertionError("不能重新选择模型"))),
    )
    context = await context_module.prepare_agent_runtime_context(PersonalAssistantContext(uid="person", model="p:frozen"))
    assert context.model == "p:frozen"


async def test_personal_resources_use_current_resource_authorization(monkeypatch):
    """隐藏的配置字段仍与当前可用资源求交集。"""
    from yuxi.modules.agents.runtime import context as context_module

    monkeypatch.setattr(context_module, "resolve_agent_resource_options", AsyncMock(return_value={"tools": [{"key": "web_fetch"}]}))
    actual = await context_module.normalize_agent_context_config(
        {"tools": ["execute", "web_fetch"], "skills": []}, db=object(), user=object(), context_schema=PersonalAssistantContext
    )
    assert actual["tools"] == ["web_fetch"]
    assert actual["skills"] == actual["mcps"] == actual["knowledges"] == []
    assert not {"tools", "skills", "mcps", "knowledges"} & PersonalAssistantContext.get_configurable_items().keys()


@pytest.mark.parametrize("name", ["execute", "ask_user_question", "wait_inputs", "remote_tool"])
def test_personal_authorization_rejects_unregistered_tools(name):
    """删除第二套白名单后，伪造调用仍不能执行未装配的工具。"""
    context = PersonalAssistantContext(
        registered_builtin_tool_names={"web_search", "web_fetch"},
        mcp_tool_servers={},
        skill_runtime_snapshot={"effective_skills": [], "preloaded_skills": [], "runtime_skills": {}},
    )
    assert not authorization.tool_allowed(context, name, {"activated_skills": ["forged"]}, {})


async def test_personal_memory_context_is_read_only(monkeypatch):
    """保留自动注入的用户记忆，但不向个人助理注册或推荐记忆工具。"""
    from yuxi.modules.agents.runtime.middlewares import memory

    monkeypatch.setattr(memory, "load_memory_prompt", AsyncMock(return_value="既有偏好"))
    context = PersonalAssistantContext(uid="person")
    middleware = await memory.create_memory_middleware(context, read_only=True)
    assert "既有偏好" in middleware.system_prompt
    assert middleware.tools == []
    assert "remember_memory" not in middleware.system_prompt
    assert "search_thread_messages" not in middleware.system_prompt


@pytest.mark.asyncio
async def test_personal_graph_registers_only_supported_tools(monkeypatch, tmp_path):
    """实际构图的 ToolNode 包含网页与协作，不包含命令、提问或改进工具。"""
    from yuxi.modules.extensions.mcp import service as mcp_service
    from yuxi.modules.extensions.web import services as web_services

    context = PersonalAssistantContext(
        personal_assistant_name="小语",
        uid="personal-test-user",
        run_id="personal-test-run",
        thread_id="personal-test-thread",
        model="test-provider:test-model",
        workdir_relative_path="agents/assistant",
        workdir_path="/home/user/workspace/agents/assistant",
        _runtime_prepared=True,
    )
    saver = InMemorySaver()
    monkeypatch.setattr(runtime_graph, "get_langgraph_checkpointer", lambda _: saver)
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "users"))
    ensure_user_workspace(context.uid)
    monkeypatch.setattr(sandbox_backend, "get_sandbox_provider", lambda: object())
    monkeypatch.setattr(runtime_graph, "load_chat_model", lambda **_: CheckpointCleanupModel())
    monkeypatch.setattr(summary_module, "load_chat_model", lambda **_: CheckpointCleanupModel())
    from yuxi.modules.agents.runtime.middlewares.memory import YuxiMemoryMiddleware

    memory = AsyncMock(return_value=YuxiMemoryMiddleware("既有偏好", read_only=True))
    monkeypatch.setattr(runtime_graph, "create_memory_middleware", memory)
    resolve_mcp = AsyncMock(return_value={})
    monkeypatch.setattr(mcp_service, "resolve_runtime_mcp_tools", resolve_mcp)
    monkeypatch.setattr(web_services, "available_web_tools", AsyncMock(return_value={"web_search", "web_fetch"}))
    from yuxi.modules.agents.runtime.context import refresh_agent_context_resources
    from yuxi.modules.extensions.skills import runtime as skill_runtime

    read_skills = AsyncMock(return_value=[])
    sync_skills = AsyncMock()
    monkeypatch.setattr(skill_runtime, "list_personal_skills", read_skills)
    monkeypatch.setattr(skill_runtime, "refresh_user_skill_projection_async", sync_skills)
    monkeypatch.setattr(skill_runtime, "lock_accessible_shared_skills_for_runtime", AsyncMock(return_value=[]))
    await refresh_agent_context_resources(context, db=object(), user=SimpleNamespace(uid=context.uid))
    assert context.mcps == []
    assert context.skill_runtime_snapshot["effective_skills"] == []
    read_skills.assert_awaited_once_with(context.uid)
    graph = await PersonalAssistantAgent().get_graph(context=context)

    tools = set(graph.nodes["tools"].bound.tools_by_name)
    assert tools == {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "web_search",
        "web_fetch",
        "create_session",
        "submit_input",
        "send_message",
        "cancel_turn",
        "list_sessions",
        "get_result",
        "list_agents",
        "send_to_user",
    }
    assert "activated_skills" in graph.channels
    assert "cooperation_cursor" not in graph.channels
    assert not any(name.startswith("CooperationMiddleware.") for name in graph.nodes)
    create_tool = graph.nodes["tools"].bound.tools_by_name["create_session"]
    assert set(create_tool.tool_call_schema.model_json_schema()["properties"]) == {"name", "description", "agent_id"}
    assert "wait_inputs" not in tools and "wait_sessions" not in tools
    assert resolve_mcp.await_args.args[0] == []
    sync_skills.assert_awaited_once_with(context.uid)
    memory.assert_awaited_once_with(context, read_only=True)

    def unexpected(*args, **kwargs):
        """清理图不允许读取运行时外部资源。"""
        raise AssertionError("清理图读取了模型或沙盒")

    with monkeypatch.context() as cleanup_patch:
        for name in (
            "load_chat_model",
            "resolve_chat_model_spec",
            "sync_agent_context_skills",
            "create_memory_middleware",
            "resolve_configured_runtime_tools",
        ):
            cleanup_patch.setattr(runtime_graph, name, unexpected)
        cleanup_patch.setattr(runtime_graph, "create_agent_composite_backend", unexpected)
        cleanup_patch.setattr(summary_module, "load_chat_model", unexpected)
        cleanup_patch.setattr(personal_graph, "build_personal_prompt", unexpected)
        cleanup = await PersonalAssistantAgent().get_graph(context=context, checkpoint_only=True)
    assert set(graph.channels) <= set(cleanup.channels)
    assert set(cleanup.nodes) == set(graph.nodes)
    from langchain_core.messages import AIMessage

    from yuxi.modules.agents.runtime.checkpoint_cleanup import cancel_waitpoint_checkpoint

    config = {"configurable": {"thread_id": context.thread_id}}
    await graph.aupdate_state(
        config,
        {"messages": [AIMessage(id="pending", content="", tool_calls=[{"id": "write", "name": "write_file", "args": {}}])]},
        as_node="model",
    )
    await cancel_waitpoint_checkpoint(cleanup, config)
    saved = await graph.aget_state(config)
    assert not saved.next and not saved.interrupts
    assert saved.values["messages"][-1].tool_call_id == "write"
    assert saved.values["messages"][-1].content == "[已取消]"

    # 同样的普通 Agent 图仍保留既有协作树能力。
    ordinary = ChatBotContext(
        tools=[],
        uid=context.uid,
        run_id=context.run_id,
        thread_id=context.thread_id,
        model=context.model,
        workdir_relative_path=context.workdir_relative_path,
        workdir_path=context.workdir_path,
        _runtime_prepared=True,
    )
    ordinary.skill_runtime_snapshot = context.skill_runtime_snapshot
    monkeypatch.setattr(runtime_graph, "sync_agent_context_skills", AsyncMock())
    monkeypatch.setattr(runtime_graph, "create_memory_middleware", AsyncMock(return_value=None))
    unrestricted = await chatbot_graph.ChatbotAgent().get_graph(context=ordinary)
    assert {"execute", "list_agents", "wait_inputs", "ask_user_question"} <= set(unrestricted.nodes["tools"].bound.tools_by_name)
    assert "activated_skills" in unrestricted.channels
    assert "cooperation_cursor" in unrestricted.channels

    from yuxi.modules.extensions.tools.builtin import ask_user_question

    ordinary.mcps = ["remote"]
    resolve_mcp.return_value = {"remote": [ask_user_question]}
    with pytest.raises(RuntimeError, match="工具名冲突.*ask_user_question"):
        await chatbot_graph.ChatbotAgent().get_graph(context=ordinary)


def test_personal_prompt_keeps_identity_and_personalization_below_fixed_rules():
    """名字和偏好可见，但个性化不得取代固定约束。"""
    context = SimpleNamespace(
        personal_assistant_name="小语",
        workdir_path="/home/user/workspace/agents/assistant",
        system_prompt="请用简短句子回答。",
    )
    prompt = build_personal_prompt(context)
    assert "小语" in prompt
    assert context.workdir_path in prompt
    assert prompt.index("固定规则") < prompt.index(context.system_prompt)
    assert context.system_prompt in prompt


async def test_personal_preparation_uses_snapshot_and_bound_project(monkeypatch):
    """普通准备流程直接使用冻结配置和已授权 Project，不查助理登记或固定目录。"""
    from yuxi.modules.agents.services import preparation

    agent = SimpleNamespace(backend_id="PersonalAssistantAgent", name="更新后的名字")
    monkeypatch.setattr(preparation, "AgentRepository", lambda db: SimpleNamespace(get_visible_by_slug=AsyncMock(return_value=agent)))
    monkeypatch.setattr(preparation, "get_agent_backend", lambda _: PersonalAssistantAgent())

    async def prepare(context):
        """替代资源 I/O，保留真实快照与 manifest 装配。"""
        context._runtime_prepared = True
        context.skill_runtime_snapshot = {"preloaded_skills": []}
        return context

    monkeypatch.setattr(preparation, "prepare_agent_runtime_context", prepare)
    snapshot = {"model": "p:assistant", "tool_approval_mode": "default", "system_prompt": "简洁回答", "personal_assistant_name": "冻结名字"}
    run = SimpleNamespace(
        id="run",
        agent_slug="assistant",
        thread_id="thread",
        runtime_scope_id="thread",
        run_type="chat",
        input_payload={"context_snapshot": snapshot},
    )
    result = await preparation.prepare_run_execution(
        run=run,
        user=SimpleNamespace(uid="person"),
        db=object(),
        worker_id="worker",
        workdir_binding=SimpleNamespace(workdir_path="projects/bound-assistant"),
    )
    assert result.manifest["resources"]["tools"] == ["web_search", "web_fetch"]
    assert result.context.model == "p:assistant"
    assert result.context.personal_assistant_name == "冻结名字"
    assert result.context.workdir_relative_path == "projects/bound-assistant"
    assert result.context._cooperation_config_snapshot["personal_assistant_name"] == "冻结名字"
    assert "名字是 冻结名字" in build_personal_prompt(result.context)

    async def prepare_less(context):
        """模拟授权后只剩一个网页工具。"""
        context = await prepare(context)
        context.tools = ["web_fetch"]
        return context

    monkeypatch.setattr(preparation, "prepare_agent_runtime_context", prepare_less)
    reduced = await preparation.prepare_run_execution(
        run=run,
        user=SimpleNamespace(uid="person"),
        db=object(),
        worker_id="worker",
        workdir_binding=SimpleNamespace(workdir_path="projects/bound-assistant"),
    )
    assert reduced.manifest["resources"]["tools"] == ["web_fetch"]
    assert reduced.manifest["config_digest"] != result.manifest["config_digest"]


def test_personal_backend_uses_user_workspace_and_project_workdir(tmp_path, monkeypatch):
    """助理使用普通用户沙盒，同用户目录可共享且命令工作目录来自项目绑定。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "users"))
    ensure_user_workspace("personal-test")
    ensure_user_workspace("another-user")
    root = user_workspace_dir("personal-test")
    other_root = user_workspace_dir("another-user")
    (root / "agents/assistant").mkdir(parents=True)
    (root / "projects").mkdir()
    (root / "projects/private.txt").write_text("same-user")
    (other_root / "secret.txt").write_text("other-user")

    class Provider:
        def execute(self, uid, command, cwd, timeout, cancelled, *, workdir):
            self.call = (uid, cwd, workdir)
            return "ok", 0, False

    provider = Provider()
    monkeypatch.setattr(sandbox_backend, "get_sandbox_provider", lambda: provider)
    context = SimpleNamespace(uid="personal-test", workdir_relative_path="agents/assistant", personal_assistant=True)
    composite = create_agent_composite_backend(context)
    backend = composite.default
    assert type(backend) is UserSandboxBackend
    assert backend.id == "personal-test"
    assert composite.artifacts_root == "/home/user/workspace/agents/assistant/outputs"
    assert backend.write("/home/user/workspace/agents/assistant/note.txt", "record").error is None
    assert (root / "agents/assistant/note.txt").read_text() == "record"
    assert backend.read("/home/user/workspace/projects/private.txt").file_data["content"] == "same-user"
    assert backend.write("/home/user/workspace/projects/private.txt", "updated").error is None
    assert (root / "projects/private.txt").read_text() == "updated"
    assert (other_root / "secret.txt").read_text() == "other-user"
    assert backend.execute("pwd").exit_code == 0
    assert provider.call == ("personal-test", "/home/user/workspace/agents/assistant", "agents/assistant")


async def test_send_to_user_returns_only_validated_communication_content():
    """成功工具结果是用户沟通事实，空白内容不能成为消息。"""
    from yuxi.modules.agents.runtime.agent_backends.personal_assistant.middleware import PersonalAssistantMiddleware

    tools = {tool.name: tool for tool in PersonalAssistantMiddleware().tools}
    assert set(tools) == {"send_to_user"}
    assert await tools["send_to_user"].ainvoke({"content": "给用户的内容"}) == {"content": "给用户的内容"}
    with pytest.raises(ValueError, match="不能为空"):
        await tools["send_to_user"].ainvoke({"content": "   "})
