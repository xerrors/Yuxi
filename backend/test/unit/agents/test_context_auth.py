from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass, field

import pytest


def _load_context_module():
    return importlib.import_module("yuxi.agents.context")


context_module = _load_context_module()
BaseContext = context_module.BaseContext
filter_config_by_role = context_module.filter_config_by_role
normalize_agent_context_config = context_module.normalize_agent_context_config


@dataclass(kw_only=True)
class ChatBotContext(BaseContext):
    subagents: list[str] | None = field(default=None, metadata={"kind": "subagents"})


@dataclass
class SuperAdminOnlyContext(BaseContext):
    secret_setting: str = field(default="hidden", metadata={"name": "Secret", "auth": "superadmin"})


def test_get_configurable_items_filters_admin_fields_for_user():
    items = BaseContext.get_configurable_items(user_role="user")

    assert "system_prompt" in items
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
async def test_normalize_agent_context_config_expands_null_and_filters_explicit_lists(monkeypatch):
    async def fake_get_databases_by_user(_user):
        return {"databases": [{"kb_id": "kb-a"}, {"kb_id": "kb-b"}]}

    async def fake_get_all_mcp_servers(_db):
        return [
            types.SimpleNamespace(slug="mcp-a", name="MCP A", description="", enabled=True),
            types.SimpleNamespace(slug="mcp-b", name="MCP B", description="", enabled=True),
        ]

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
                {"slug": "tavily_search", "name": "Tavily", "description": ""},
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
        types.SimpleNamespace(get_all_mcp_servers=fake_get_all_mcp_servers),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.agents.skills.service",
        types.SimpleNamespace(list_accessible_skills=fake_list_skills),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.repositories.agent_repository",
        types.SimpleNamespace(AgentRepository=FakeAgentRepository),
    )

    normalized = await normalize_agent_context_config(
        {
            "tools": None,
            "knowledges": ["kb-b", "missing", "kb-b"],
            "mcps": None,
            "skills": [],
            "subagents": ["research-agent", "missing"],
            "summary_threshold": 10,
            "summary_keep_messages": 8,
            "summary_prompt": "custom summary",
            "summary_tool_result_token_limit": 500,
            "max_execution_steps": 50,
        },
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None),
        context_schema=ChatBotContext,
    )

    assert normalized["tools"] == ["ask_user_question", "tavily_search"]
    assert normalized["knowledges"] == ["kb-b"]
    assert normalized["mcps"] == ["mcp-a", "mcp-b"]
    assert normalized["skills"] == []
    assert normalized["subagents"] == ["research-agent"]
    assert "summary_threshold" not in normalized
    assert "summary_keep_messages" not in normalized
    assert "summary_prompt" not in normalized
    assert "summary_tool_result_token_limit" not in normalized
    assert "max_execution_steps" not in normalized

    empty_subagents_normalized = await normalize_agent_context_config(
        {"tools": [], "knowledges": [], "mcps": [], "skills": [], "subagents": []},
        db=object(),
        user=types.SimpleNamespace(role="user", uid="u1", department_id=None),
        context_schema=ChatBotContext,
    )

    assert empty_subagents_normalized["subagents"] == ["research-agent", "critique-agent"]


@pytest.mark.asyncio
async def test_prepare_agent_runtime_context_filters_resources_and_derives_runtime_scope(monkeypatch):
    async def fake_get_databases_by_user(_user):
        return {"databases": [{"kb_id": "kb-a"}, {"kb_id": "kb-b"}]}

    async def fake_get_all_mcp_servers(_db):
        return [types.SimpleNamespace(slug="mcp-a", name="MCP A", description="", enabled=True)]

    async def fake_list_skills(_db, _user):
        return [
            types.SimpleNamespace(slug="skill-a", name="Skill A", description=""),
            types.SimpleNamespace(slug="skill-b", name="Skill B", description=""),
        ]

    async def fake_resolve_visible_knowledge_bases(context):
        assert context.knowledges == ["kb-a"]
        context._visible_knowledge_bases = [{"slug": "kb-a", "name": "Docs A"}]
        return context._visible_knowledge_bases

    async def fake_resolve_runtime_skills_for_context(context, *, db=None, user=None):
        del db
        assert user.uid == "u1"
        assert context.skills == ["skill-a"]
        return {
            "context_skills": ["skill-a"],
            "prompt_skills": ["skill-a", "skill-b"],
            "readable_skills": ["skill-a", "skill-b"],
            "runtime_skill_metadata": {"skill-a": {"name": "Skill A"}},
            "runtime_skill_dependency_map": {"skill-a": {"skills": ["skill-b"]}},
        }

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

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
        "yuxi.agents.middlewares.skills",
        types.SimpleNamespace(resolve_runtime_skills_for_context=fake_resolve_runtime_skills_for_context),
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
        types.SimpleNamespace(get_all_mcp_servers=fake_get_all_mcp_servers),
    )
    monkeypatch.setitem(
        sys.modules,
        "yuxi.agents.skills.service",
        types.SimpleNamespace(list_accessible_skills=fake_list_skills),
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
        mcps=None,
        skills=["skill-a", "missing"],
        subagents=[],
    )

    prepared = await context_module.prepare_agent_runtime_context(context)

    assert prepared.tools == ["ask_user_question"]
    assert prepared.knowledges == ["kb-a"]
    assert prepared.mcps == ["mcp-a"]
    assert prepared.skills == ["skill-a"]
    assert prepared.subagents == ["research-agent"]
    assert prepared._visible_knowledge_bases == [{"slug": "kb-a", "name": "Docs A"}]
    assert prepared._prompt_skills == ["skill-a", "skill-b"]
    assert prepared._readable_skills == ["skill-a", "skill-b"]
    assert prepared._runtime_skill_metadata == {"skill-a": {"name": "Skill A"}}
    assert prepared._runtime_skill_dependency_map == {"skill-a": {"skills": ["skill-b"]}}


@pytest.mark.asyncio
async def test_prepare_agent_runtime_context_clears_resources_for_missing_user(monkeypatch):
    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

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
        "yuxi.agents.middlewares.skills",
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
        subagents=["agent"],
    )

    prepared = await context_module.prepare_agent_runtime_context(context)

    assert prepared.tools == []
    assert prepared.knowledges == []
    assert prepared.mcps == []
    assert prepared.skills == []
    assert prepared.subagents == []
    assert prepared._visible_knowledge_bases == []
    assert prepared._prompt_skills == []
    assert prepared._readable_skills == []
    assert prepared._runtime_skill_metadata == {}
    assert prepared._runtime_skill_dependency_map == {}
