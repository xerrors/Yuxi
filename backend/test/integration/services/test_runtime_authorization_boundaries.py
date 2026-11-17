"""真实数据库、Skill 字节与执行图的授权刷新边界。"""

import asyncio
import hashlib
import os
from types import SimpleNamespace

import pytest
import pytest_asyncio
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from sqlalchemy import event, select, text

from test.integration.support.module_audit import isolated_audit_store
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.runtime.agent_backends.chatbot import graph as chatbot_graph
from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
from yuxi.modules.agents.runtime.context import prepare_agent_runtime_context
from yuxi.modules.agents.runtime.middlewares.authorization import (
    AgentExecutionRevoked,
    RuntimeAuthorizationMiddleware,
    refresh_execution_authorization,
)
from yuxi.modules.extensions.skills import projection
from yuxi.modules.extensions.skills.edit import edit_shared_skill_file
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.tools.runtime import resolve_configured_runtime_tools
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.models import Project

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]

SHARED = {"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None}


@pytest_asyncio.fixture
async def authorized_context(tmp_path, monkeypatch):
    """隔离持久权限与来源文件，保留生产解析和投影。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "users"))
    monkeypatch.setenv("YUXI_SKILL_DATA_DIR", str(tmp_path / "sources"))
    monkeypatch.setenv("YUXI_SKILL_PROJECTION_DIR", str(tmp_path / "projections"))
    source = tmp_path / "sources/shared/live-skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# CURRENT_AUTHORIZED_SKILL\n")
    async with isolated_audit_store(monkeypatch) as (manager, sessions):
        async with sessions() as db:
            db.add_all(
                [
                    User(uid="owner", username="owner", password_hash="fixture", role="user"),
                    User(uid="caller", username="caller", password_hash="fixture", role="user"),
                    Agent(
                        slug="live-agent",
                        name="Live",
                        backend_id="ChatbotAgent",
                        created_by="owner",
                        visibility="shared",
                        share_config=SHARED,
                    ),
                    Skill(
                        slug="live-skill",
                        name="Live",
                        description="fixture",
                        source_type="upload",
                        created_by="owner",
                        dir_path="shared/live-skill",
                        share_config=SHARED,
                    ),
                ]
            )
            await db.flush()
            db.add(
                Project(
                    id="live-project", uid="caller", selection_status="implicit", directory_mode="managed", workdir_path="projects/live"
                )
            )
            await db.flush()
            db.add(
                Session(
                    thread_id="live-thread",
                    tree_root_thread_id="live-thread",
                    uid="caller",
                    agent_id="live-agent",
                    status="active",
                    project_id="live-project",
                )
            )
            await db.commit()
        context = ChatBotContext(
            uid="caller",
            thread_id="live-thread",
            agent_slug="live-agent",
            workdir_relative_path="projects/live",
            model="fixture:model",
            tools=[],
            mcps=[],
            knowledges=[],
            skills=["live-skill"],
            preload_skills=["live-skill"],
        )
        await prepare_agent_runtime_context(context)
        tools = await resolve_configured_runtime_tools(context)
        yield SimpleNamespace(context=context, sessions=sessions, engine=manager.async_engine, source=source, tools=tools)


async def test_shipping_model_round_syncs_projection_once_and_checks_identity_after_return(authorized_context, monkeypatch):
    """真实模型请求看到最新 Skill，普通轮次只同步一次投影。"""
    fixture = authorized_context
    model = CapturingModel()
    sql, hash_reads = [], []
    original_hash = projection.compute_skill_directory_hash

    def record_sql(_connection, _cursor, statement, _parameters, _execution, _many):
        """测量实际发出的 SQL，不依赖 service mock 次数。"""
        sql.append(statement.lower())

    def read_hash(path):
        """记录实际字节扫描，同时执行原投影比较。"""
        hash_reads.append(path)
        return original_hash(path)

    event.listen(fixture.engine.sync_engine, "before_cursor_execute", record_sql)
    monkeypatch.setattr(projection, "compute_skill_directory_hash", read_hash)
    backend = CompositeBackend(default=StateBackend, routes={})
    try:
        # 使用正式 factory 的授权、Skill、重试和审批装配；摘要模型为本地可观察模型。
        middlewares = await chatbot_graph._build_middlewares(fixture.context, backend, cleanup_model=model)
        graph = create_agent(model=model, tools=fixture.tools, middleware=middlewares)
        result = await graph.ainvoke({"messages": [HumanMessage(content="hello")]}, context=fixture.context)
    finally:
        event.remove(fixture.engine.sync_engine, "before_cursor_execute", record_sql)

    assert result["messages"][-1].content == "AUTHORIZATION_OK"
    assert len(model.requests) == 1
    assert "CURRENT_AUTHORIZED_SKILL" in "\n".join(str(message.content) for message in model.requests[0])
    assert len(hash_reads) == 2, {"hash_reads": len(hash_reads), "agent_queries": sum("from agents" in statement for statement in sql)}
    assert sum("from agents" in statement for statement in sql) == 3
    projected = projection.get_user_skills_root_dir("caller") / "live-skill/SKILL.md"
    assert projected.read_text() == "# CURRENT_AUTHORIZED_SKILL\n"
    assert result["token_usage"]["llm_input_tokens"] == count_tokens_approximately(model.requests[0], tools=model.bound_tools)


async def test_summary_threshold_counts_authorized_preloaded_root(authorized_context):
    """摘要阈值消费包含预加载正文的最终输入。"""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    fixture = authorized_context
    (fixture.source / "SKILL.md").write_text("# CURRENT_AUTHORIZED_SKILL\n" + "body " * 16000)
    fixture.context.summary_threshold = 8
    fixture.context.summary_keep_messages = 1
    model = CapturingModel()
    workdir = fixture.source.parent / "workdir"
    backend = CompositeBackend(default=FilesystemBackend(root_dir=workdir, virtual_mode=True), routes={})
    async with fixture.sessions() as db:
        schema = await db.scalar(text("SELECT current_schema()"))
    async with await AsyncConnection.connect(
        os.environ["POSTGRES_URL"].replace("+asyncpg", ""),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options=f"-csearch_path={schema}",
    ) as connection:
        checkpointer = AsyncPostgresSaver(connection)
        await checkpointer.setup()
        graph = create_agent(
            model=model,
            tools=fixture.tools,
            middleware=await chatbot_graph._build_middlewares(fixture.context, backend, cleanup_model=model),
            checkpointer=checkpointer,
        )
        config = {"configurable": {"thread_id": "isolated-summary-checkpoint"}}
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="old question"), AIMessage(content="old answer"), HumanMessage(content="new question")]},
            config=config,
            context=fixture.context,
        )
        saved = await graph.aget_state(config)
        assert [message.content for message in saved.values["messages"][:3]] == ["old question", "old answer", "new question"]
        event = saved.values["_summarization_event"]
        assert event["cutoff_index"] == 2
        assert "old question" in (workdir / event["file_path"].lstrip("/")).read_text()
    assert len(model.requests) == 2
    assert "old question" in str(model.requests[0][0].content)
    assert "CURRENT_AUTHORIZED_SKILL" in str(model.requests[1][0].content)
    assert result["token_usage"]["llm_input_tokens"] == count_tokens_approximately(model.requests[1], tools=model.bound_tools)
    assert "old question" in next((workdir / "conversation_history").glob("*.md")).read_text()


@pytest.mark.parametrize("replace_binding", [False, True])
async def test_refresh_preserves_prepared_bound_root_but_reads_current_shared_root(authorized_context, replace_binding):
    """同次绑定冻结已准备正文，替换绑定与新 Run 使用当前根文件。"""
    fixture = authorized_context
    bound_source = fixture.source.parent / "bound-skill"
    bound_source.mkdir()
    prepared = "---\nslug: bound-skill\nname: Bound\ndescription: fixture\n---\n# PREPARED_BOUND_ROOT\n"
    updated = prepared.replace("PREPARED_BOUND_ROOT", "UPDATED_BOUND_ROOT")
    (bound_source / "SKILL.md").write_text(prepared)
    async with fixture.sessions() as db:
        agent = await db.scalar(select(Agent).where(Agent.slug == "live-agent"))
        agent.share_config = {**SHARED, "manage_scope": {"access_level": "user", "user_uids": ["owner"]}}
        owner = await db.scalar(select(User).where(User.uid == "owner"))
        owner.role = "admin"
        bound_values = {
            "slug": "bound-skill",
            "name": "Bound",
            "description": "fixture",
            "source_type": "upload",
            "created_by": "owner",
            "bound_agent_id": agent.id,
            "dir_path": "shared/bound-skill",
            "share_config": {},
        }
        db.add(Skill(**bound_values))
        await db.commit()
    # 专属绑定在首次准备前已存在；与上一个已准备对象保持独立。
    context_values = {
        "uid": "caller",
        "thread_id": "live-thread",
        "agent_slug": "live-agent",
        "model": "fixture:model",
        "tools": [],
        "mcps": [],
        "knowledges": [],
        "skills": ["live-skill"],
        "preload_skills": ["live-skill"],
    }
    context = ChatBotContext(**context_values)
    await prepare_agent_runtime_context(context)
    assert context.skill_runtime_snapshot["preloaded_skill_contents"]["bound-skill"] == prepared
    prepared_metadata = dict(context.skill_runtime_snapshot["skill_metadata"]["bound-skill"])
    async with fixture.sessions() as db:
        owner = await db.scalar(select(User).where(User.uid == "owner"))
        edited, _ = await edit_shared_skill_file(
            db,
            slug="bound-skill",
            relative_path="SKILL.md",
            content=updated,
            expected_revision=hashlib.sha256(prepared.encode()).hexdigest(),
            operator=owner,
        )
        assert edited.content_hash is not None
        assert edited.dir_path.startswith("packages/bound-skill/")
        if replace_binding:
            old_id = edited.id
            replacement = Skill(**{**bound_values, "dir_path": edited.dir_path, "content_hash": edited.content_hash})
            await db.delete(edited)
            await db.flush()
            db.add(replacement)
            await db.commit()
            assert replacement.id != old_id
    (fixture.source / "SKILL.md").write_text("# UPDATED_SHARED_ROOT\n")
    # 模型边界顺序：完整刷新资源与快照发生在 abefore_model，投影同步发生在实际模型请求入口。
    await RuntimeAuthorizationMiddleware().abefore_model({}, SimpleNamespace(context=context))
    await refresh_execution_authorization(context)
    contents = context.skill_runtime_snapshot["preloaded_skill_contents"]
    assert contents["bound-skill"] == (updated if replace_binding else prepared)
    assert contents["live-skill"] == "# UPDATED_SHARED_ROOT\n"
    assert (projection.get_user_skills_root_dir("caller") / "bound-skill/SKILL.md").read_text() == updated
    metadata = context.skill_runtime_snapshot["skill_metadata"]["bound-skill"]
    if replace_binding:
        assert metadata["source_identity"].split(":", 1)[0] == str(replacement.id)
    else:
        assert metadata == prepared_metadata
    next_context = ChatBotContext(**context_values)
    await prepare_agent_runtime_context(next_context)
    assert next_context.skill_runtime_snapshot["preloaded_skill_contents"]["bound-skill"] == updated


async def test_skill_runtime_skips_locked_invisible_row(authorized_context):
    """其他用户持有私有行写锁时，候选筛选不得阻塞运行时。"""
    from yuxi.modules.extensions.skills.access import lock_accessible_shared_skills_for_runtime

    fixture = authorized_context
    async with fixture.sessions() as db:
        db.add(
            Skill(
                slug="hidden-skill",
                name="Hidden",
                description="fixture",
                source_type="upload",
                created_by="owner",
                dir_path="shared/hidden-skill",
                share_config={"version": 2, "read_scope": None, "manage_scope": None},
            )
        )
        await db.commit()
    async with fixture.sessions() as editor, fixture.sessions() as runtime:
        await editor.execute(select(Skill).where(Skill.slug == "hidden-skill").with_for_update())
        reader = await runtime.scalar(select(User).where(User.uid == "caller"))
        rows = await asyncio.wait_for(lock_accessible_shared_skills_for_runtime(runtime, reader, ["hidden-skill"]), timeout=2)
        assert rows == []


async def test_skill_runtime_rechecks_revocation_after_waiting_for_lock(authorized_context):
    """初始可见的候选在等待行锁期间被撤权，锁后必须拒绝。"""
    from yuxi.modules.extensions.skills.access import lock_accessible_shared_skills_for_runtime

    fixture = authorized_context
    async with fixture.sessions() as editor, fixture.sessions() as runtime:
        skill = await editor.scalar(select(Skill).where(Skill.slug == "live-skill").with_for_update())
        skill.share_config = {"version": 2, "read_scope": None, "manage_scope": None}
        await editor.flush()
        reader = await runtime.scalar(select(User).where(User.uid == "caller"))
        pending = asyncio.create_task(lock_accessible_shared_skills_for_runtime(runtime, reader, ["live-skill"]))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(pending), timeout=0.2)
            await editor.commit()
            assert await asyncio.wait_for(pending, timeout=3) == []
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


async def test_skill_prompt_is_built_after_current_request_authorization(authorized_context):
    """已准备 Skill 被撤销时，实际模型请求不能携带旧的预加载正文。"""
    fixture = authorized_context
    assert "CURRENT_AUTHORIZED_SKILL" in fixture.context.skill_runtime_snapshot["preloaded_skill_contents"]["live-skill"]
    async with fixture.sessions() as db:
        skill = await db.scalar(select(Skill).where(Skill.slug == "live-skill"))
        skill.share_config = {"version": 2, "read_scope": None, "manage_scope": None}
        await projection.commit_skill_policy_and_refresh_projections(db, skill.slug)
    model = CapturingModel()
    backend = CompositeBackend(default=StateBackend, routes={})
    middlewares = await chatbot_graph._build_middlewares(fixture.context, backend, cleanup_model=model)
    graph = create_agent(model=model, tools=fixture.tools, middleware=middlewares)
    result = await graph.ainvoke({"messages": [HumanMessage(content="hello")]}, context=fixture.context)
    assert result["messages"][-1].content == "AUTHORIZATION_OK"
    assert len(model.requests) == 1
    assert "CURRENT_AUTHORIZED_SKILL" not in "\n".join(str(message.content) for message in model.requests[0])
    assert fixture.context.skills == []
    assert not (projection.get_user_skills_root_dir("caller") / "live-skill").exists()


@pytest.mark.parametrize("change", ["revoked", "personal_replacement"])
async def test_model_retry_rejects_changed_injected_skill_with_real_sources(authorized_context, change):
    """实际数据库撤权或个人同名替换后，网络重试不能发送旧提示。"""
    from yuxi.modules.agents.runtime.middlewares.network_retry import NetworkRetryMiddleware
    from yuxi.modules.workspace.paths import user_workspace_dir

    fixture = authorized_context

    class ChangingModel(CapturingModel):
        """首次请求后改变真实来源或权限，再返回网络错误。"""

        async def _agenerate(self, messages, **kwargs):
            """让后续重试必须经过当前授权与来源复核。"""
            self.requests.append(messages)
            if len(self.requests) == 1:
                if change == "revoked":
                    async with fixture.sessions() as db:
                        skill = await db.scalar(select(Skill).where(Skill.slug == "live-skill"))
                        skill.share_config = {"version": 2, "read_scope": None, "manage_scope": None}
                        await db.commit()
                else:
                    personal = user_workspace_dir("caller") / "agents" / "skills" / "live-skill"
                    personal.mkdir(parents=True)
                    (personal / "SKILL.md").write_text("---\nname: live-skill\ndescription: replacement\n---\nPERSONAL_REPLACEMENT")
                raise ConnectionError("retry after source change")
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="UNEXPECTED_OLD_PROMPT"))])

    model = ChangingModel()
    middlewares = await chatbot_graph._build_middlewares(
        fixture.context, CompositeBackend(default=StateBackend, routes={}), cleanup_model=model
    )
    for middleware in middlewares:
        if isinstance(middleware, NetworkRetryMiddleware):
            middleware._network_initial_delay = 0.001
    graph = create_agent(model=model, tools=fixture.tools, middleware=middlewares)
    with pytest.raises(AgentExecutionRevoked, match="Skill 权限或来源"):
        await graph.ainvoke({"messages": [HumanMessage(content="hello")]}, context=fixture.context)
    assert len(model.requests) == 1
    assert "CURRENT_AUTHORIZED_SKILL" in "\n".join(str(message.content) for message in model.requests[0])


@pytest.mark.parametrize("revoke", [False, True])
async def test_model_return_checks_permission_before_approval_interrupt(authorized_context, revoke):
    """模型等待后撤权必须先失败，仍获授权时才产生审批等待点。"""
    fixture = authorized_context

    class ApprovalModel(CapturingModel):
        """模型等待内提交真实撤权，再返回需要审批的工具调用。"""

        async def _agenerate(self, messages, **kwargs):
            """提交外部权限变化，独立记录实际模型请求。"""
            self.requests.append(messages)
            if revoke:
                async with fixture.sessions() as db:
                    agent = await db.scalar(select(Agent).where(Agent.slug == "live-agent"))
                    agent.share_config = {"version": 2, "read_scope": None, "manage_scope": None}
                    await db.commit()
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "write_file",
                                    "args": {"file_path": "/home/user/workspace/outside.md", "content": "denied"},
                                    "id": "approval-call",
                                }
                            ],
                        )
                    )
                ]
            )

    model = ApprovalModel()
    backend = CompositeBackend(default=StateBackend, routes={})
    graph = create_agent(
        model=model, tools=fixture.tools, middleware=await chatbot_graph._build_middlewares(fixture.context, backend, cleanup_model=model)
    )
    if revoke:
        with pytest.raises(AgentExecutionRevoked, match="Agent 使用权限已撤销"):
            await graph.ainvoke({"messages": [HumanMessage(content="request approval")]}, context=fixture.context)
    else:
        result = await graph.ainvoke({"messages": [HumanMessage(content="request approval")]}, context=fixture.context)
        assert result["__interrupt__"][0].value["action_requests"][0]["name"] == "write_file"
    assert len(model.requests) == 1


async def test_prepare_and_refresh_restore_original_selection_after_policy_regrant(authorized_context):
    """撤销清理投影，重授后按原始选择恢复有效资源。"""
    fixture = authorized_context
    assert fixture.context.skills == ["live-skill"]

    async def refresh_boundary():
        """按执行边界顺序刷新：资源与快照重建在模型边界，投影同步在实际模型请求入口。"""
        await RuntimeAuthorizationMiddleware().abefore_model({}, SimpleNamespace(context=fixture.context))
        await refresh_execution_authorization(fixture.context)

    await refresh_boundary()
    projected = projection.get_user_skills_root_dir("caller") / "live-skill"
    assert (projected / "SKILL.md").is_file()
    async with fixture.sessions() as db:
        skill = await db.scalar(select(Skill).where(Skill.slug == "live-skill"))
        skill.share_config = {"version": 2, "read_scope": None, "manage_scope": None}
        await projection.commit_skill_policy_and_refresh_projections(db, skill.slug)
    assert not projected.exists()
    await refresh_boundary()
    assert fixture.context.skills == []
    assert fixture.context.preload_skills == []
    assert fixture.context._capability_limited
    assert fixture.context._resource_selections["skills"] == ["live-skill"]
    async with fixture.sessions() as db:
        skill = await db.scalar(select(Skill).where(Skill.slug == "live-skill"))
        skill.share_config = SHARED
        await projection.commit_skill_policy_and_refresh_projections(db, skill.slug)
    await refresh_boundary()
    assert fixture.context.skills == ["live-skill"]
    assert fixture.context.preload_skills == ["live-skill"]
    assert not fixture.context._capability_limited
    assert (projected / "SKILL.md").read_text() == "# CURRENT_AUTHORIZED_SKILL\n"


@pytest.mark.parametrize("revocation", ["account", "agent"])
async def test_model_return_rejects_revocation_without_resource_or_projection_work(authorized_context, revocation):
    """等待后的身份撤销仍在审批前拒绝，成功返回阶段只读身份。"""
    fixture = authorized_context
    async with fixture.sessions() as db:
        if revocation == "account":
            user = await db.scalar(select(User).where(User.uid == "caller"))
            user.is_deleted = 1
        else:
            agent = await db.scalar(select(Agent).where(Agent.slug == "live-agent"))
            agent.share_config = {"version": 2, "read_scope": None, "manage_scope": None}
        await db.commit()
    with pytest.raises(AgentExecutionRevoked, match="账号已失效|权限已撤销"):
        await RuntimeAuthorizationMiddleware().aafter_model({}, SimpleNamespace(context=fixture.context))


class CapturingModel(BaseChatModel):
    """记录最终模型输入，避免把中间件完成报告当成事实。"""

    requests: list = Field(default_factory=list)
    bound_tools: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        """返回确定性测试模型类型。"""
        return "authorization-contract"

    def bind_tools(self, tools, **kwargs):
        """接受真实图注册的工具，测试不产生工具调用。"""
        self.bound_tools = list(tools)
        return self

    def _generate(self, *args, **kwargs):
        """只验证异步执行图。"""
        raise NotImplementedError

    async def _agenerate(self, messages, **kwargs):
        """记录最终请求并返回固定内容。"""
        self.requests.append(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="AUTHORIZATION_OK"))])


@pytest.mark.parametrize("enabled", [False, True])
async def test_shipping_graph_does_not_discover_unactivated_plugin_mcp(authorized_context, enabled, monkeypatch):
    """生产装配不发现未激活插件的 MCP，不可达地址不能阻止普通聊天。"""
    from yuxi.modules.agents.runtime.middlewares import authorization, plugins
    from yuxi.modules.extensions.mcp.models import MCPServer
    from yuxi.modules.extensions.plugins import registry
    from yuxi.modules.extensions.plugins.base import YuxiPlugin

    class InactivePlugin(YuxiPlugin):
        """部署声明替身，数据库与运行图仍走真实实现。"""

        plugin_id = "inactive"
        name = "InactivePlugin"
        description = "Inactive integration fixture"
        entry_skill = "live-skill"
        skills = ("live-skill", "nested-skill")
        mcps = ("nested-mcp",)

    for module in (registry, plugins, authorization, chatbot_graph):
        monkeypatch.setattr(module, "get_plugins", lambda: (InactivePlugin,))
    fixture = authorized_context
    child_source = fixture.source.parent / "nested-skill"
    child_source.mkdir()
    (child_source / "SKILL.md").write_text("# NESTED_INACTIVE_SKILL\n")
    async with fixture.sessions() as db:
        db.add_all(
            [
                Skill(
                    slug="nested-skill",
                    name="Nested",
                    description="fixture",
                    source_type="upload",
                    created_by="owner",
                    dir_path="shared/nested-skill",
                    share_config=SHARED,
                ),
                MCPServer(
                    slug="nested-mcp",
                    name="Nested",
                    transport="streamable_http",
                    url="http://127.0.0.1:1/unreachable-mcp",
                    enabled=int(enabled),
                    created_by="owner",
                    updated_by="owner",
                ),
            ]
        )
        await db.commit()
    context = ChatBotContext(
        uid="caller",
        thread_id="live-thread",
        agent_slug="live-agent",
        workdir_relative_path="projects/live",
        model="fixture:model",
        tools=[],
        mcps=[],
        knowledges=[],
        plugins=["inactive"],
        skills=[],
        preload_skills=[],
    )
    await prepare_agent_runtime_context(context)
    tools = await resolve_configured_runtime_tools(context)
    model = CapturingModel()
    middlewares = await chatbot_graph._build_middlewares(context, CompositeBackend(default=StateBackend, routes={}), cleanup_model=model)
    graph = create_agent(model=model, tools=tools, middleware=middlewares)
    result = await graph.ainvoke({"messages": [HumanMessage(content="hello")]}, context=context)
    assert result["messages"][-1].content == "AUTHORIZATION_OK"
    assert len(model.requests) == 1
    assert "ocr_parse_file" not in {tool.name for tool in model.bound_tools}
    assert context.mcp_tool_servers == {}
