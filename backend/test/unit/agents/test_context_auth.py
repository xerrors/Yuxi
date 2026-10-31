from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from yuxi.modules.agents.runtime import context as context_module
from yuxi.modules.knowledge.read_models import KnowledgeBaseSummary
from yuxi.modules.workspace.paths import ensure_user_workspace


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


BaseContext = context_module.BaseContext
filter_config_by_role = context_module.filter_config_by_role
normalize_agent_context_config = context_module.normalize_agent_context_config


def test_summary_default_and_schema_preserve_explicit_overrides():
    """缺省上下文使用 256K，已有显式阈值保持原值。"""
    assert BaseContext().summary_threshold == 256
    assert BaseContext.get_configurable_items(user_role="admin")["summary_threshold"]["default"] == 256
    assert BaseContext(summary_threshold=37).summary_threshold == 37


def test_tool_approval_is_hidden_in_form_but_preserved_in_context():
    """审批字段仅从表单隐藏，仍可保存并用于运行。"""
    for role in ("user", "admin", "superadmin", None):
        assert "tool_approval_mode" not in BaseContext.get_configurable_items(user_role=role)
    saved = {"context": {"tool_approval_mode": "always_trust"}}
    assert filter_config_by_role(saved, "admin", BaseContext) == saved
    context = BaseContext()
    context.update_config(saved["context"])
    assert context.tool_approval_mode == "always_trust"


@dataclass(kw_only=True)
class ChatBotContext(BaseContext):
    pass


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
        "yuxi.modules.knowledge.mcp.client",
        types.SimpleNamespace(
            knowledge_resource_options=fail_if_loaded, KnowledgeMCPError=RuntimeError, KnowledgeMCPUnavailable=RuntimeError
        ),
    )

    assert await context_module.resolve_agent_resource_options(set(), db=object(), user=object()) == {}


@pytest.mark.asyncio
async def test_normalize_agent_context_config_defaults_mcps_off_and_filters_explicit_lists(monkeypatch):
    async def fake_get_knowledge_bases_by_user(_user):
        return [{"key": "kb-a", "name": "kb-a", "description": ""}, {"key": "kb-b", "name": "kb-b", "description": ""}]

    async def fake_get_all_mcp_servers(_db):
        return [
            types.SimpleNamespace(slug="mcp-a", name="MCP A", description="", enabled=True),
            types.SimpleNamespace(slug="mcp-b", name="MCP B", description="", enabled=True),
        ]

    async def fake_get_enabled_mcp_servers(*, db=None):
        return [server for server in await fake_get_all_mcp_servers(db) if server.slug == "mcp-a"]

    async def fake_list_skills(_db, _user):
        return [
            types.SimpleNamespace(slug="skill-a", name="Skill A", description=""),
            types.SimpleNamespace(slug="skill-b", name="Skill B", description=""),
        ]

    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.extensions.tools.catalog",
        types.SimpleNamespace(
            get_tool_metadata=lambda category=None: [
                {"slug": "ask_user_question", "name": "Ask User", "description": ""},
                {"slug": "web_search", "name": "Web Search", "description": ""},
            ]
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.knowledge.mcp.client",
        types.SimpleNamespace(
            knowledge_resource_options=fake_get_knowledge_bases_by_user,
            KnowledgeMCPError=RuntimeError,
            KnowledgeMCPUnavailable=RuntimeError,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.extensions.mcp.repository",
        types.SimpleNamespace(get_all_mcp_servers=fake_get_all_mcp_servers),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.extensions.mcp.service",
        types.SimpleNamespace(get_enabled_mcp_servers=fake_get_enabled_mcp_servers),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.extensions.skills.repository",
        types.SimpleNamespace(
            SkillRepository=lambda db: types.SimpleNamespace(list_enabled_readable=lambda user: fake_list_skills(db, user))
        ),
    )

    normalized = await normalize_agent_context_config(
        {
            "tools": "all",
            "knowledges": ["kb-b", "missing", "kb-b"],
            "mcps": [],
            "skills": [],
            "preload_skills": ["skill-a"],
            "summary_threshold": 10,
            "summary_keep_messages": 8,
            "summary_prompt": "custom summary",
            "summary_tool_result_token_limit": 500,
            "summary_l2_trigger_ratio": 0.4,
            "max_execution_steps": 50,
        },
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None, is_deleted=0),
        context_schema=ChatBotContext,
    )

    assert normalized["tools"] == ["ask_user_question", "web_search"]
    assert normalized["knowledges"] == ["kb-b", "missing"]
    assert normalized["mcps"] == []
    assert normalized["skills"] == []
    assert normalized["preload_skills"] == []
    assert normalized["summary_threshold"] == 10
    assert normalized["summary_keep_messages"] == 8
    assert normalized["summary_prompt"] == "custom summary"
    assert normalized["summary_tool_result_token_limit"] == 500
    assert "summary_l2_trigger_ratio" not in normalized
    assert normalized["max_execution_steps"] == 50

    selected_mcp = await normalize_agent_context_config(
        {"tools": [], "knowledges": [], "mcps": ["mcp-a", "mcp-b"], "skills": []},
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None, is_deleted=0),
        context_schema=ChatBotContext,
    )
    assert selected_mcp["mcps"] == ["mcp-a"]

    omitted_mcp = await normalize_agent_context_config(
        {"tools": [], "knowledges": [], "skills": []},
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None, is_deleted=0),
        context_schema=ChatBotContext,
    )
    assert omitted_mcp["mcps"] == []

    empty_resources_normalized = await normalize_agent_context_config(
        {"tools": [], "knowledges": [], "mcps": [], "skills": []},
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None, is_deleted=0),
        context_schema=ChatBotContext,
    )

    assert empty_resources_normalized["mcps"] == []

    preloaded_normalized = await normalize_agent_context_config(
        {
            "tools": [],
            "knowledges": [],
            "mcps": [],
            "skills": ["skill-a"],
            "preload_skills": ["skill-b", "skill-a", "skill-a", "missing"],
        },
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None, is_deleted=0),
        context_schema=ChatBotContext,
    )

    assert preloaded_normalized["skills"] == ["skill-a"]
    assert preloaded_normalized["preload_skills"] == ["skill-a"]


@pytest.mark.asyncio
async def test_prepare_agent_runtime_context_filters_resources_and_derives_runtime_scope(monkeypatch):
    from yuxi.modules.agents.runtime import context as context_module

    monkeypatch.setattr(context_module, "_load_workspace_agent_context", lambda uid: "workspace policy")

    async def fake_get_knowledge_bases_by_user(_user):
        return [{"key": "kb-a", "name": "kb-a", "description": ""}, {"key": "kb-b", "name": "kb-b", "description": ""}]

    async def fake_get_all_mcp_servers(_db):
        return [types.SimpleNamespace(slug="mcp-a", name="MCP A", description="", enabled=True)]

    async def fake_get_enabled_mcp_servers(*, db=None):
        return [server for server in await fake_get_all_mcp_servers(db) if server.slug == "mcp-a"]

    async def fake_list_skills(_db, _user):
        return [
            types.SimpleNamespace(slug="skill-a", name="Skill A", description=""),
            types.SimpleNamespace(slug="skill-b", name="Skill B", description=""),
        ]

    async def fake_resolve_runtime_skills_for_context(
        context,
        *,
        db=None,
        user=None,
        agent=None,
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
                    "path": "/home/user/skills/skill-a/SKILL.md",
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

    monkeypatch.setattr(context_module, "system_options", FakeSystemOptions())

    class FakeUserRepository:
        async def get_by_uid_with_db(self, _db, uid):
            assert uid == "u1"
            return types.SimpleNamespace(role="user", uid="u1", department_id=None, is_deleted=0)

    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.extensions.skills.runtime",
        types.SimpleNamespace(
            resolve_runtime_skills_for_context=fake_resolve_runtime_skills_for_context,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.identity.repositories.users",
        types.SimpleNamespace(UserRepository=FakeUserRepository),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.infrastructure.postgres.manager",
        types.SimpleNamespace(pg_manager=types.SimpleNamespace(get_async_session_context=lambda: FakeSessionContext())),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.extensions.tools.catalog",
        types.SimpleNamespace(
            get_tool_metadata=lambda category=None: [{"slug": "ask_user_question", "name": "Ask User", "description": ""}]
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.knowledge.mcp.client",
        types.SimpleNamespace(
            knowledge_resource_options=fake_get_knowledge_bases_by_user,
            KnowledgeMCPError=RuntimeError,
            KnowledgeMCPUnavailable=RuntimeError,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.extensions.mcp.repository",
        types.SimpleNamespace(get_all_mcp_servers=fake_get_all_mcp_servers),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.extensions.mcp.service",
        types.SimpleNamespace(get_enabled_mcp_servers=fake_get_enabled_mcp_servers),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.extensions.skills.repository",
        types.SimpleNamespace(
            SkillRepository=lambda db: types.SimpleNamespace(list_enabled_readable=lambda user: fake_list_skills(db, user))
        ),
    )
    context = ChatBotContext(
        uid="u1",
        tools=["ask_user_question", "missing"],
        knowledges=["kb-a", "missing"],
        mcps=[],
        skills=["skill-a", "missing"],
        preload_skills=["skill-a", "missing"],
    )

    prepared = await context_module.prepare_agent_runtime_context(context)

    assert prepared.tools == ["ask_user_question"]
    assert prepared.knowledges == ["kb-a", "missing"]
    assert prepared.mcps == []
    assert prepared.skills == ["skill-a"]
    assert prepared.preload_skills == ["skill-a"]
    assert not hasattr(prepared, "_visible_knowledge_bases")
    assert prepared.skill_runtime_snapshot.get("effective_skills", []) == ["skill-a", "skill-b"]
    assert prepared.skill_runtime_snapshot.get("runtime_skills", {})["skill-a"]["name"] == "Skill A"
    assert prepared.skill_runtime_snapshot.get("runtime_skills", {})["skill-a"]["skills"] == ["skill-b"]
    assert prepared.skill_runtime_snapshot.get("preloaded_skills", []) == ["skill-a", "skill-b"]

    # 已准备对象保留同次执行内容，后续构图不得重新读取配置或 Skill。
    monkeypatch.setattr(
        context_module,
        "normalize_agent_context_config",
        AsyncMock(side_effect=AssertionError("同次执行不得再次规范化")),
    )
    monkeypatch.setattr(
        sys.modules["yuxi.modules.extensions.skills.runtime"],
        "resolve_runtime_skills_for_context",
        AsyncMock(side_effect=AssertionError("同次执行不得重新读取 Skill")),
    )
    prompt = prepared.system_prompt
    assert "workspace policy" in prompt
    assert await context_module.prepare_agent_runtime_context(prepared) is prepared
    assert prepared.system_prompt == prompt


@pytest.mark.asyncio
async def test_prepare_agent_runtime_context_clears_resources_for_missing_user(monkeypatch):
    from yuxi.modules.agents.runtime import context as context_module

    monkeypatch.setattr(context_module, "_load_workspace_agent_context", lambda uid: "")

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeSystemOptions:
        async def get(self, _db=None):
            return {"default_model": "fake-model"}

    monkeypatch.setattr(context_module, "system_options", FakeSystemOptions())

    class FakeUserRepository:
        async def get_by_uid_with_db(self, _db, _uid):
            return None

    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.extensions.skills.runtime",
        types.SimpleNamespace(resolve_runtime_skills_for_context=lambda _context, db=None, user=None: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.modules.identity.repositories.users",
        types.SimpleNamespace(UserRepository=FakeUserRepository),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.infrastructure.postgres.manager",
        types.SimpleNamespace(pg_manager=types.SimpleNamespace(get_async_session_context=lambda: FakeSessionContext())),
    )

    context = ChatBotContext(
        uid="missing",
        tools=["tool"],
        knowledges=["kb"],
        mcps=["mcp"],
        skills=["skill"],
        preload_skills=["skill"],
    )

    with pytest.raises(PermissionError, match="账号已失效"):
        await context_module.prepare_agent_runtime_context(context)


def test_persistent_config_cannot_replace_runtime_identity():
    """接入与执行共用的配置装载只接受可配置字段。"""
    from yuxi.modules.agents.runtime.context import BaseContext

    context = BaseContext(uid="owner", worker_id="worker")
    context.update_config({"uid": "forged", "worker_id": "forged", "model": "chosen:model", "update": None})
    assert context.uid == "owner"
    assert context.worker_id == "worker"
    assert context.model == "chosen:model"
    assert callable(context.update)


@pytest.mark.asyncio
async def test_normalized_persistent_config_drops_subagent_runtime_flags():
    """状态查询与主动压缩的配置归一化不接受运行标记。"""
    from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
    from yuxi.modules.agents.runtime.context import normalize_agent_context_config

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
        context_schema=ChatBotContext,
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
    config = {"tools": "all", "skills": ["first", "hidden"], "mcps": "all"}
    first = await normalize_agent_context_config(config, db=None, user=None, context_schema=ChatBotContext)
    assert first["tools"] == ["first"]
    available.append("second")
    second = await normalize_agent_context_config(config, db=None, user=None, context_schema=ChatBotContext)
    assert second["tools"] == second["mcps"] == ["first", "second"]
    assert second["skills"] == ["first"]
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

    from yuxi.modules.agents.services.configuration import merge_agent_config_json, prepare_agent_config_write

    async def options(names, **kwargs):
        return {name: [{"key": "visible"}] for name in names}

    monkeypatch.setattr(context_module, "resolve_agent_resource_options", options)
    monkeypatch.setattr("yuxi.modules.agents.services.configuration.resolve_agent_resource_options", options)
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


def test_context_update_applies_only_declared_runtime_fields() -> None:
    context = BaseContext()
    context.update(
        {
            "thread_id": "thread-1",
            "uid": "user-1",
            "system_prompt": "runtime prompt",
            "unknown_field": "ignored",
            "update": "must not shadow the method",
        },
    )

    assert context.thread_id == "thread-1"
    assert context.uid == "user-1"
    assert context.system_prompt == "runtime prompt"
    assert not hasattr(context, "unknown_field")
    assert callable(context.update)


@pytest.mark.asyncio
async def test_workspace_prompt_excludes_memory_from_shared_context(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    ensure_user_workspace("user-1")
    agents_dir = tmp_path / "threads" / "shared" / "user-1" / "workspace" / "agents"
    (agents_dir / "AGENTS.md").write_text("行为约束", encoding="utf-8")
    (agents_dir / "USER.md").write_text("用户信息", encoding="utf-8")
    (agents_dir / "MEMORY.md").write_text("长期记忆", encoding="utf-8")

    context = BaseContext(thread_id="thread-1", uid="user-1")
    context.update_config({})
    await context_module._append_workspace_agent_prompt(context)

    assert context.system_prompt == (
        "You are a helpful assistant.\n\n用户工作区 agents/AGENTS.md 内容：\n行为约束\n\n用户工作区 agents/USER.md 内容：\n用户信息"
    )
    assert "长期记忆" not in context.system_prompt


@pytest.mark.parametrize("workspace_text", ["用户工作区策略", ""])
async def test_workspace_prompt_appends_only_available_agent_context(monkeypatch, workspace_text):
    """运行快照保留系统提示词，工作区内容只追加到同一 Context。"""
    monkeypatch.setattr(context_module, "_load_workspace_agent_context", lambda uid: workspace_text)
    context = BaseContext(thread_id="thread-1", uid="user-1")
    context.update_config({"system_prompt": "快照中的系统提示词", "temperature": 0.1})
    await context_module._append_workspace_agent_prompt(context)
    assert context.system_prompt == "快照中的系统提示词" + (f"\n\n{workspace_text}" if workspace_text else "")
    assert context.thread_id == "thread-1" and context.uid == "user-1"


@pytest.mark.asyncio
async def test_refresh_does_not_pair_replaced_bound_skill_with_old_body(monkeypatch):
    """专属 Skill 同名替换后，新身份必须对应新正文。"""
    from yuxi.modules.agents.runtime import context as context_module
    from yuxi.modules.extensions.skills import runtime as skill_runtime

    context = context_module.BaseContext()
    context.skill_runtime_snapshot = {
        "skill_metadata": {"bound": {"source_scope": "agent_bound", "source_identity": "old"}},
        "preloaded_skill_contents": {"bound": "old body"},
    }
    replacement = {
        "skill_metadata": {"bound": {"source_scope": "agent_bound", "source_identity": "new"}},
        "preloaded_skill_contents": {"bound": "new body"},
        "context_skills": ["bound"],
        "context_preload_skills": ["bound"],
        "capability_limited": False,
    }

    async def normalize(selections, **_kwargs):
        return selections

    async def resolve(*_args, **_kwargs):
        return replacement

    monkeypatch.setattr(context_module, "normalize_agent_context_config", normalize)
    monkeypatch.setattr(skill_runtime, "resolve_runtime_skills_for_context", resolve)
    await context_module.refresh_agent_context_resources(context, db=None, user=None)
    assert context.skill_runtime_snapshot["preloaded_skill_contents"]["bound"] == "new body"


async def test_personal_empty_shared_selection_keeps_personal_skills(monkeypatch, tmp_path):
    """助理不选择共享 Skill，个人 Skill 仍经普通解析加入且依赖须激活。"""
    from yuxi.modules.agents.runtime.agent_backends.personal_assistant.graph import PersonalAssistantContext
    from yuxi.modules.agents.runtime.middlewares.authorization import tool_allowed
    from yuxi.modules.extensions.skills import runtime as skills_runtime
    from yuxi.modules.extensions.skills.resolved import ResolvedSkill

    (tmp_path / "SKILL.md").write_text("# 私人工作指南\n")
    item = ResolvedSkill(
        id="personal-guide",
        slug="personal-guide",
        name="私人指南",
        description="测试",
        source_type="local",
        source_scope="personal",
        source_dir=tmp_path,
        enabled=True,
        created_by="person",
        share_config=None,
        tool_dependencies=["present_artifacts", "ask_user_question"],
        mcp_dependencies=["remote"],
        skill_dependencies=[],
    )
    monkeypatch.setattr(context_module, "resolve_agent_resource_options", AsyncMock(return_value={"tools": [{"key": "web_search"}]}))
    monkeypatch.setattr(skills_runtime, "list_personal_skills", AsyncMock(return_value=[item]))
    monkeypatch.setattr(skills_runtime, "lock_accessible_shared_skills_for_runtime", AsyncMock(return_value=[]))
    context = PersonalAssistantContext(uid="person")
    await context_module.refresh_agent_context_resources(context, db=object(), user=types.SimpleNamespace(uid="person"))
    assert context.tools == ["web_search"]
    assert context.mcps == context.knowledges == context.preload_skills == []
    assert context.skills == ["personal-guide"]
    assert context.skill_runtime_snapshot["effective_skills"] == ["personal-guide"]
    assert context.skill_runtime_snapshot["preloaded_skills"] == []
    context.registered_builtin_tool_names = {"present_artifacts", "ask_user_question"}
    context.mcp_tool_servers = {"remote_tool": "remote"}
    policy = {"remote": set()}
    for name in ("present_artifacts", "ask_user_question", "remote_tool"):
        assert not tool_allowed(context, name, {}, policy)
        assert tool_allowed(context, name, {"activated_skills": ["personal-guide"]}, policy)
        assert not tool_allowed(context, name, {"activated_skills": ["unselected"]}, policy)
