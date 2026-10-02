from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
from yuxi.knowledge.read_models import KnowledgeBaseSummary


def _knowledge_summary(kb_id: str) -> KnowledgeBaseSummary:
    return KnowledgeBaseSummary(
        kb_id=kb_id,
        name=kb_id,
        description=None,
        kb_type="milvus",
        embedding_model_spec=None,
        llm_model_spec=None,
        query_params={},
        additional_params={},
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
        created_by=None,
        created_at=None,
    )


def _load_context_module():
    return importlib.import_module("yuxi.agents.context")


context_module = _load_context_module()
BaseContext = context_module.BaseContext
filter_config_by_role = context_module.filter_config_by_role
normalize_agent_context_config = context_module.normalize_agent_context_config


@dataclass(kw_only=True)
class ChatBotContext(BaseContext):
    subagents: context_module.ResourceSelection = field(default="all", metadata={"kind": "subagents"})


@dataclass
class SuperAdminOnlyContext(BaseContext):
    secret_setting: str = field(default="hidden", metadata={"name": "Secret", "auth": "superadmin"})


@pytest.mark.parametrize("role", ["user", "admin", "superadmin", None])
@pytest.mark.asyncio
async def test_saved_restricted_settings_survive_runtime_but_writes_require_role(role):
    """运行保留已保存参数，写入仍受角色限制且不接收未知字段。"""
    saved = {
        "tool_approval_mode": "always_trust",
        "summary_threshold": 37,
        "summary_keep_messages": 7,
        "summary_prompt": "摘要 {messages}",
        "summary_tool_result_token_limit": 123,
        "max_execution_steps": 42,
        "model_retry_times": 5,
    }
    context = {**saved, "secret_setting": "restricted", "unknown": 1}
    readable = context_module.filter_declared_config({"context": context}, SuperAdminOnlyContext)["context"]
    assert readable == {**saved, "secret_setting": "restricted"}
    normalized = await normalize_agent_context_config(
        {**context, "tools": [], "knowledges": [], "mcps": [], "skills": []},
        db=object(),
        user=types.SimpleNamespace(role=role),
        context_schema=SuperAdminOnlyContext,
    )
    assert {key: normalized[key] for key in readable} == readable
    assert "unknown" not in normalized
    writable = filter_config_by_role({"context": context}, role, SuperAdminOnlyContext)["context"]
    expected = saved if role in {"admin", "superadmin"} else {}
    if role == "superadmin":
        expected = {**expected, "secret_setting": "restricted"}
    assert writable == expected


def test_get_configurable_items_filters_admin_fields_for_user():
    items = BaseContext.get_configurable_items(user_role="user")

    assert "system_prompt" in items
    assert items["preload_skills"]["default"] == []
    assert items["preload_skills"]["kind"] == "skills"
    assert items["mcps"]["default"] == []
    assert "默认不直接加载" in items["mcps"]["description"]
    assert "MCP 依赖在激活后开放" in items["skills"]["description"]
    assert "summary_threshold" not in items
    assert "summary_keep_messages" not in items
    assert "summary_prompt" not in items
    assert "summary_tool_result_token_limit" not in items
    assert "max_execution_steps" not in items


def test_get_configurable_items_allows_admin_and_superadmin_fields():
    admin_items = BaseContext.get_configurable_items(user_role="admin")
    superadmin_items = SuperAdminOnlyContext.get_configurable_items(user_role="superadmin")

    assert "summary_threshold" in admin_items
    assert "summary_keep_messages" in admin_items
    assert "summary_prompt" in admin_items
    assert "summary_tool_result_token_limit" in admin_items
    assert "max_execution_steps" in admin_items
    assert "secret_setting" in superadmin_items


def test_filter_config_by_role_removes_unauthorized_context_values():
    config_json = {
        "context": {
            "system_prompt": "visible",
            "summary_threshold": 10,
            "summary_keep_messages": 8,
            "summary_prompt": "custom summary",
            "summary_tool_result_token_limit": 500,
            "max_execution_steps": 50,
            "secret_setting": "nope",
        },
        "other": {"keep": True},
    }

    filtered = filter_config_by_role(config_json, "user", context_schema=SuperAdminOnlyContext)

    assert filtered == {"context": {"system_prompt": "visible"}, "other": {"keep": True}}
    assert config_json["context"]["summary_threshold"] == 10


def test_filter_config_by_role_keeps_admin_context_values_for_admin():
    filtered = filter_config_by_role(
        {
            "context": {
                "summary_threshold": 10,
                "summary_keep_messages": 8,
                "summary_prompt": "custom summary",
                "summary_tool_result_token_limit": 500,
                "summary_l2_trigger_ratio": 0.4,
                "max_execution_steps": 50,
                "secret_setting": "nope",
            }
        },
        "admin",
        context_schema=SuperAdminOnlyContext,
    )

    assert filtered == {
        "context": {
            "summary_threshold": 10,
            "summary_keep_messages": 8,
            "summary_prompt": "custom summary",
            "summary_tool_result_token_limit": 500,
            "max_execution_steps": 50,
        }
    }


@pytest.mark.asyncio
async def test_resolve_agent_resource_options_empty_fields_loads_nothing(monkeypatch):
    async def fail_if_loaded(*_args, **_kwargs):
        raise AssertionError("empty resource_fields should not load resources")

    monkeypatch.setitem(
        sys.modules,
        "yuxi.knowledge.runtime",
        types.SimpleNamespace(knowledge_base=types.SimpleNamespace(get_databases_by_user=fail_if_loaded)),
    )

    assert await context_module.resolve_agent_resource_options(set(), db=object(), user=object()) == {}


@pytest.mark.asyncio
async def test_normalize_agent_context_config_defaults_mcps_off_and_filters_explicit_lists(monkeypatch):
    async def fake_get_databases_by_user(_user):
        return [_knowledge_summary("kb-a"), _knowledge_summary("kb-b")]

    async def fake_get_all_mcp_servers(_db):
        return [
            types.SimpleNamespace(slug="mcp-a", name="MCP A", description="", enabled=True),
            types.SimpleNamespace(slug="mcp-b", name="MCP B", description="", enabled=True),
        ]

    async def fake_get_enabled_mcp_server_slugs(*, db=None):
        del db
        return ["mcp-a"]

    async def fake_list_skills(_db, _user):
        return [
            types.SimpleNamespace(slug="skill-a", name="Skill A", description=""),
            types.SimpleNamespace(slug="skill-b", name="Skill B", description=""),
        ]

    class FakeAgentRepository:
        def __init__(self, _db):
            pass

        async def list_visible_subagents(self, *, user):
            assert user.uid == "u1"
            return [
                types.SimpleNamespace(slug="research-agent", name="Research", description=""),
                types.SimpleNamespace(slug="critique-agent", name="Critique", description=""),
            ]

    monkeypatch.setitem(
        sys.modules,
        "yuxi.agents.toolkits.service",
        types.SimpleNamespace(
            get_tool_metadata=lambda category=None: [
                {"slug": "ask_user_question", "name": "Ask User", "description": ""},
                {"slug": "web_search", "name": "Web Search", "description": ""},
            ]
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.knowledge.runtime",
        types.SimpleNamespace(knowledge_base=types.SimpleNamespace(get_databases_by_user=fake_get_databases_by_user)),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.agents.mcp.service",
        types.SimpleNamespace(
            get_all_mcp_servers=fake_get_all_mcp_servers,
            get_enabled_mcp_server_slugs=fake_get_enabled_mcp_server_slugs,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.repositories.skill_repository",
        types.SimpleNamespace(
            SkillRepository=lambda db: types.SimpleNamespace(
                list_enabled_readable=lambda user: fake_list_skills(db, user)
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.repositories.agent_repository",
        types.SimpleNamespace(AgentRepository=FakeAgentRepository),
    )

    normalized = await normalize_agent_context_config(
        {
            "tools": "all",
            "knowledges": ["kb-b", "missing", "kb-b"],
            "mcps": [],
            "skills": [],
            "preload_skills": ["skill-a"],
            "subagents": ["research-agent", "missing"],
            "summary_threshold": 10,
            "summary_keep_messages": 8,
            "summary_prompt": "custom summary",
            "summary_tool_result_token_limit": 500,
            "summary_l2_trigger_ratio": 0.4,
            "max_execution_steps": 50,
        },
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None),
        context_schema=ChatBotContext,
    )

    assert normalized["tools"] == ["ask_user_question", "web_search"]
    assert normalized["knowledges"] == ["kb-b"]
    assert normalized["mcps"] == []
    assert normalized["skills"] == []
    assert normalized["preload_skills"] == []
    assert normalized["subagents"] == ["research-agent"]
    assert normalized["summary_threshold"] == 10
    assert normalized["summary_keep_messages"] == 8
    assert normalized["summary_prompt"] == "custom summary"
    assert normalized["summary_tool_result_token_limit"] == 500
    assert "summary_l2_trigger_ratio" not in normalized
    assert normalized["max_execution_steps"] == 50

    selected_mcp = await normalize_agent_context_config(
        {"tools": [], "knowledges": [], "mcps": ["mcp-a", "mcp-b"], "skills": []},
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None),
        context_schema=ChatBotContext,
    )
    assert selected_mcp["mcps"] == ["mcp-a"]

    omitted_mcp = await normalize_agent_context_config(
        {"tools": [], "knowledges": [], "skills": []},
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None),
        context_schema=ChatBotContext,
    )
    assert omitted_mcp["mcps"] == []

    empty_subagents_normalized = await normalize_agent_context_config(
        {"tools": [], "knowledges": [], "mcps": [], "skills": [], "subagents": []},
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None),
        context_schema=ChatBotContext,
    )

    assert empty_subagents_normalized["mcps"] == []
    assert empty_subagents_normalized["subagents"] == []

    preloaded_normalized = await normalize_agent_context_config(
        {
            "tools": [],
            "knowledges": [],
            "mcps": [],
            "skills": ["skill-a"],
            "preload_skills": ["skill-b", "skill-a", "skill-a", "missing"],
            "subagents": ["research-agent"],
        },
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None),
        context_schema=ChatBotContext,
    )

    assert preloaded_normalized["skills"] == ["skill-a"]
    assert preloaded_normalized["preload_skills"] == ["skill-a"]


@pytest.mark.asyncio
async def test_prepare_agent_runtime_context_filters_resources_and_derives_runtime_scope(monkeypatch):
    from yuxi.agents import context as context_module

    monkeypatch.setattr(context_module, "_load_workspace_agent_context", lambda uid: "workspace policy")

    async def fake_get_databases_by_user(_user):
        return [_knowledge_summary("kb-a"), _knowledge_summary("kb-b")]

    async def fake_get_all_mcp_servers(_db):
        return [types.SimpleNamespace(slug="mcp-a", name="MCP A", description="", enabled=True)]

    async def fake_get_enabled_mcp_server_slugs(*, db=None):
        del db
        return ["mcp-a"]

    async def fake_list_skills(_db, _user):
        return [
            types.SimpleNamespace(slug="skill-a", name="Skill A", description=""),
            types.SimpleNamespace(slug="skill-b", name="Skill B", description=""),
        ]

    async def fake_resolve_visible_knowledge_bases(context):
        assert context.knowledges == ["kb-a"]
        context._visible_knowledge_bases = [{"slug": "kb-a", "name": "Docs A"}]
        return context._visible_knowledge_bases

    async def fake_resolve_runtime_skills_for_context(
        context,
        *,
        db=None,
        user=None,
    ):
        del db
        assert user.uid == "u1"
        assert context.skills == ["skill-a"]
        assert context.preload_skills == ["skill-a"]
        return {
            "context_skills": ["skill-a"],
            "context_preload_skills": ["skill-a"],
            "effective_skills": ["skill-a", "skill-b"],
            "runtime_skills": {
                "skill-a": {
                    "name": "Skill A",
                    "description": "",
                    "path": "/home/gem/skills/skill-a/SKILL.md",
                    "tools": [],
                    "mcps": [],
                    "skills": ["skill-b"],
                }
            },
            "preloaded_skills": ["skill-a", "skill-b"],
            "preloaded_skill_contents": {"skill-a": "# Skill A", "skill-b": "# Skill B"},
        }

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeSystemOptions:
        async def get(self, _db=None):
            return {"default_model": "fake-model"}

    context_module = _load_context_module()
    monkeypatch.setattr(context_module, "system_options", FakeSystemOptions())

    class FakeUserRepository:
        async def get_by_uid_with_db(self, _db, uid):
            assert uid == "u1"
            return types.SimpleNamespace(role="user", uid="u1", department_id=None)

    class FakeAgentRepository:
        def __init__(self, _db):
            pass

        async def list_visible_subagents(self, *, user):
            assert user.uid == "u1"
            return [types.SimpleNamespace(slug="research-agent", name="Research", description="")]

    monkeypatch.setitem(
        sys.modules,
        "yuxi.agents.backends.knowledge_base_backend",
        types.SimpleNamespace(resolve_visible_knowledge_bases_for_context=fake_resolve_visible_knowledge_bases),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.agents.skills.runtime",
        types.SimpleNamespace(
            resolve_runtime_skills_for_context=fake_resolve_runtime_skills_for_context,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.repositories.user_repository",
        types.SimpleNamespace(UserRepository=FakeUserRepository),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.storage.postgres.manager",
        types.SimpleNamespace(pg_manager=types.SimpleNamespace(get_async_session_context=lambda: FakeSessionContext())),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.agents.toolkits.service",
        types.SimpleNamespace(
            get_tool_metadata=lambda category=None: [
                {"slug": "ask_user_question", "name": "Ask User", "description": ""}
            ]
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.knowledge.runtime",
        types.SimpleNamespace(knowledge_base=types.SimpleNamespace(get_databases_by_user=fake_get_databases_by_user)),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.agents.mcp.service",
        types.SimpleNamespace(
            get_all_mcp_servers=fake_get_all_mcp_servers,
            get_enabled_mcp_server_slugs=fake_get_enabled_mcp_server_slugs,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.repositories.skill_repository",
        types.SimpleNamespace(
            SkillRepository=lambda db: types.SimpleNamespace(
                list_enabled_readable=lambda user: fake_list_skills(db, user)
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.repositories.agent_repository",
        types.SimpleNamespace(AgentRepository=FakeAgentRepository),
    )
    context = ChatBotContext(
        uid="u1",
        tools=["ask_user_question", "missing"],
        knowledges=["kb-a", "missing"],
        mcps=[],
        skills=["skill-a", "missing"],
        preload_skills=["skill-a", "missing"],
        subagents="all",
    )

    prepared = await context_module.prepare_agent_runtime_context(context)

    assert prepared.tools == ["ask_user_question"]
    assert prepared.knowledges == ["kb-a"]
    assert prepared.mcps == []
    assert prepared.skills == ["skill-a"]
    assert prepared.preload_skills == ["skill-a"]
    assert prepared.subagents == ["research-agent"]
    assert prepared._visible_knowledge_bases == [{"slug": "kb-a", "name": "Docs A"}]
    assert prepared._skill_runtime_snapshot.get("effective_skills", []) == ["skill-a", "skill-b"]
    assert prepared._skill_runtime_snapshot.get("runtime_skills", {})["skill-a"]["name"] == "Skill A"
    assert prepared._skill_runtime_snapshot.get("runtime_skills", {})["skill-a"]["skills"] == ["skill-b"]
    assert prepared._skill_runtime_snapshot.get("preloaded_skills", []) == ["skill-a", "skill-b"]

    # 已准备对象保留同次执行内容，后续构图不得重新读取配置或 Skill。
    monkeypatch.setattr(
        context_module,
        "normalize_agent_context_config",
        AsyncMock(side_effect=AssertionError("同次执行不得再次规范化")),
    )
    monkeypatch.setattr(
        sys.modules["yuxi.agents.skills.runtime"],
        "resolve_runtime_skills_for_context",
        AsyncMock(side_effect=AssertionError("同次执行不得重新读取 Skill")),
    )
    prompt = prepared.system_prompt
    assert "workspace policy" in prompt
    assert await context_module.prepare_agent_runtime_context(prepared) is prepared
    assert prepared.system_prompt == prompt


@pytest.mark.asyncio
async def test_prepare_agent_runtime_context_clears_resources_for_missing_user(monkeypatch):
    from yuxi.agents import context as context_module

    monkeypatch.setattr(context_module, "_load_workspace_agent_context", lambda uid: "")

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeSystemOptions:
        async def get(self, _db=None):
            return {"default_model": "fake-model"}

    context_module = _load_context_module()
    monkeypatch.setattr(context_module, "system_options", FakeSystemOptions())

    class FakeUserRepository:
        async def get_by_uid_with_db(self, _db, _uid):
            return None

    monkeypatch.setitem(
        sys.modules,
        "yuxi.agents.backends.knowledge_base_backend",
        types.SimpleNamespace(resolve_visible_knowledge_bases_for_context=lambda _context: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.agents.skills.runtime",
        types.SimpleNamespace(resolve_runtime_skills_for_context=lambda _context, db=None, user=None: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.repositories.user_repository",
        types.SimpleNamespace(UserRepository=FakeUserRepository),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.storage.postgres.manager",
        types.SimpleNamespace(pg_manager=types.SimpleNamespace(get_async_session_context=lambda: FakeSessionContext())),
    )

    context = ChatBotContext(
        uid="missing",
        tools=["tool"],
        knowledges=["kb"],
        mcps=["mcp"],
        skills=["skill"],
        preload_skills=["skill"],
        subagents=["agent"],
    )

    prepared = await context_module.prepare_agent_runtime_context(context)

    assert prepared.tools == []
    assert prepared.knowledges == []
    assert prepared.mcps == []
    assert prepared.skills == []
    assert prepared.preload_skills == []
    assert prepared.subagents == []
    assert prepared._visible_knowledge_bases == []
    assert prepared._skill_runtime_snapshot.get("effective_skills", []) == []
    assert prepared._skill_runtime_snapshot.get("runtime_skills", {}) == {}


def test_persistent_config_cannot_replace_runtime_identity():
    """接入与执行共用的配置装载只接受可配置字段。"""
    from yuxi.agents.context import BaseContext

    context = BaseContext(uid="owner", worker_id="worker")
    context.update_config({"uid": "forged", "worker_id": "forged", "model": "chosen:model", "update": None})
    assert context.uid == "owner"
    assert context.worker_id == "worker"
    assert context.model == "chosen:model"
    assert callable(context.update)


@pytest.mark.asyncio
async def test_normalized_persistent_config_drops_subagent_runtime_flags():
    """状态查询与主动压缩的配置归一化不接受运行标记。"""
    from yuxi.agents.buildin.subagent.context import SubAgentContext
    from yuxi.agents.context import normalize_agent_context_config

    normalized = await normalize_agent_context_config(
        {
            "parent_thread_id": "forged",
            "is_subagent_runtime": True,
            "tools": [],
            "knowledges": [],
            "mcps": [],
            "skills": [],
        },
        db=None,
        user=None,
        context_schema=SubAgentContext,
    )
    assert "parent_thread_id" not in normalized
    assert "is_subagent_runtime" not in normalized


@pytest.mark.asyncio
async def test_all_selection_resolves_new_resources_without_mutating_config(monkeypatch):
    """all 跟随资源变化，固定列表和空列表保留意图。"""
    available = ["first"]

    async def options(names, **kwargs):
        return {name: [{"key": key} for key in available] for name in names}

    monkeypatch.setattr(context_module, "resolve_agent_resource_options", options)
    config = {"tools": "all", "skills": ["first", "hidden"], "subagents": [], "mcps": "all"}
    first = await normalize_agent_context_config(config, db=None, user=None, context_schema=ChatBotContext)
    assert first["tools"] == ["first"]
    available.append("second")
    second = await normalize_agent_context_config(config, db=None, user=None, context_schema=ChatBotContext)
    assert second["tools"] == second["mcps"] == ["first", "second"]
    assert second["skills"] == ["first"]
    assert second["subagents"] == []
    assert config["tools"] == "all"
    assert config["skills"] == ["first", "hidden"]


@pytest.mark.parametrize("invalid", [None, "full", [1], [""]])
def test_update_config_rejects_invalid_resource_input(invalid):
    """配置装载边界不把非法值静默当作禁用或全部。"""
    context = BaseContext()
    with pytest.raises(ValueError, match="skills"):
        context.update_config({"skills": invalid})
    assert context.skills == "all"


@pytest.mark.asyncio
async def test_preload_all_follows_enabled_skills_and_keeps_default_off(monkeypatch):
    """MCP 与预加载同为默认空；预加载全部跟随有效 Skill 范围变化。"""
    available = ["alpha"]

    async def options(names, **kwargs):
        return {name: [{"key": key} for key in available] for name in names}

    monkeypatch.setattr(context_module, "resolve_agent_resource_options", options)
    defaults = await normalize_agent_context_config({}, db=None, user=None)
    assert defaults["mcps"] == defaults["preload_skills"] == []
    config = {"skills": "all", "preload_skills": "all", "mcps": "all"}
    first = await normalize_agent_context_config(config, db=None, user=None)
    assert first["skills"] == first["preload_skills"] == first["mcps"] == ["alpha"]
    assert first["skills"] is not first["preload_skills"]
    available.append("beta")
    second = await normalize_agent_context_config(config, db=None, user=None)
    assert second["preload_skills"] == second["mcps"] == ["alpha", "beta"]
    assert config == {"skills": "all", "preload_skills": "all", "mcps": "all"}
    disabled = await normalize_agent_context_config({"skills": [], "preload_skills": "all"}, db=None, user=None)
    assert disabled["preload_skills"] == []


@dataclass(kw_only=True)
class ResourceFieldContext(BaseContext):
    """用别名资源与普通列表验证字段声明是唯一来源。"""

    selected_tools: context_module.ResourceSelection = field(default="all", metadata={"kind": "tools"})
    labels: list[str] = field(default_factory=list, metadata={"type": "list"})
    skills: context_module.ResourceSelection = field(default_factory=list, metadata={"kind": "skills"})


@pytest.mark.asyncio
async def test_resource_field_declarations_drive_write_runtime_and_schema(monkeypatch):
    """新增声明自动参与写入校验和展开，普通列表保持原样。"""
    from types import SimpleNamespace

    from yuxi.repositories.agent_repository import merge_agent_config_json
    from yuxi.services.agent_config_service import prepare_agent_config_write

    async def options(names, **kwargs):
        return {name: [{"key": "visible"}] for name in names}

    monkeypatch.setattr(context_module, "resolve_agent_resource_options", options)
    monkeypatch.setattr("yuxi.services.agent_config_service.resolve_agent_resource_options", options)
    context = ResourceFieldContext()
    with pytest.raises(ValueError, match="selected_tools"):
        context.update_config({"selected_tools": None})
    context.update_config({"labels": ["plain"], "selected_tools": ["visible"]})
    assert context.labels == ["plain"]
    schema = context.get_configurable_items()
    assert schema["selected_tools"]["supports_all"] is True
    assert schema["labels"]["supports_all"] is False
    assert schema["skills"]["default"] == []
    patch, access = await prepare_agent_config_write(
        {"context": {"selected_tools": ["visible"], "labels": ["plain"]}},
        context_schema=ResourceFieldContext,
        db=None,
        user=SimpleNamespace(role="user"),
    )
    merged = merge_agent_config_json({}, patch, resource_access=access, context_schema=ResourceFieldContext)
    assert merged == patch
    with pytest.raises(ValueError, match="未经过权限校验"):
        merge_agent_config_json({}, patch, resource_access={}, context_schema=ResourceFieldContext)
    normalized = await normalize_agent_context_config(
        {"selected_tools": "all", "labels": ["plain"]},
        db=None,
        user=None,
        context_schema=ResourceFieldContext,
    )
    assert normalized["selected_tools"] == ["visible"]
    assert normalized["labels"] == ["plain"]
    assert normalized["skills"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection, expected", [(None, ["general-purpose"]), ("all", ["general-purpose", "specialist"]), ([], [])]
)
async def test_chatbot_defaults_to_general_purpose_subagent(monkeypatch, selection, expected):
    """真实 Chatbot 默认仅通用角色，显式全部与空选择保持原意。"""
    from yuxi.agents.buildin.chatbot.context import ChatBotContext

    async def options(names, **kwargs):
        return {name: [{"key": slug} for slug in ["general-purpose", "specialist"]] for name in names}

    monkeypatch.setattr(context_module, "resolve_agent_resource_options", options)
    config = {} if selection is None else {"subagents": selection}
    normalized = await normalize_agent_context_config(config, db=None, user=None, context_schema=ChatBotContext)
    assert normalized["subagents"] == expected
    assert ChatBotContext.get_configurable_items()["subagents"]["default"] == ["general-purpose"]


@pytest.mark.asyncio
async def test_invisible_default_subagent_does_not_enable_other_roles(monkeypatch):
    """默认通用角色不可见时不扩大选择范围。"""
    from yuxi.agents.buildin.chatbot.context import ChatBotContext

    async def options(names, **kwargs):
        return {name: [{"key": "specialist"}] for name in names}

    monkeypatch.setattr(context_module, "resolve_agent_resource_options", options)
    normalized = await normalize_agent_context_config({}, db=None, user=None, context_schema=ChatBotContext)
    assert normalized["subagents"] == []
