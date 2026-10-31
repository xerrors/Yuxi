from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker
from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext

from yuxi.modules.agents.presets.deep_research import PRESET as GENERAL_PURPOSE
from yuxi.api.routers.agents.management import delete_agent
from yuxi.modules.agents.services.definitions import serialize_agent_definition, update_agent_definition
from yuxi.modules.agents.repositories.definitions import AgentRepository, DEFAULT_AGENT_DESCRIPTION, DEFAULT_SHARE_CONFIG
from yuxi.modules.agents.services.configuration import merge_agent_config_json
from yuxi.modules.agents.permissions import user_can_access_agent, user_can_manage_agent
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.identity.models import User
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.models.sessions import Session
from yuxi.infrastructure.postgres.base import Base
from test.support.sqlite import create_utc_sqlite_engine


class FakeDb:
    def __init__(self):
        self.added = None
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.flush = AsyncMock()
        self.rollback = AsyncMock()

    def add(self, item):
        self.added = item


@pytest.fixture(autouse=True)
def isolate_bound_definition_commit(monkeypatch):
    """权限单测隔离专属包发布，持久事务由真实 PG/HTTP 验证。"""

    async def commit(db, agent, user, **kwargs):
        await db.commit()

    monkeypatch.setattr("yuxi.modules.agents.services.definitions.commit_agent_definition", commit)


_MANAGER_USER_SCOPE = {
    "version": 2,
    "read_scope": {"access_level": "user", "user_uids": ["manager"]},
    "manage_scope": {"access_level": "user", "user_uids": ["manager"]},
}


def _agent_for_update(*, slug="shared-bot", name="Shared Bot", created_by="owner", share_config=_MANAGER_USER_SCOPE):
    return SimpleNamespace(
        slug=slug,
        backend_id="ChatbotAgent",
        share_config=share_config,
        created_by=created_by,
        updated_by=None,
        updated_at=None,
        name=name,
        description="",
        icon=None,
        pics=[],
        config_json={},
    )


def test_merge_agent_config_json_preserves_omitted_context_fields_and_hidden_skills():
    """省略字段和不可见 Skill 引用均保持原值。"""
    existing = {
        "context": {
            "model": "provider:model-a",
            "skills": [f"skill-{index}" for index in range(10)],
        },
        "metadata": {"source": "owner"},
    }

    merged = merge_agent_config_json(
        existing,
        {
            "context": {
                "temperature": 0.2,
                "skills": [f"skill-{index}" for index in range(5)],
            }
        },
        resource_access={
            "skills": {
                *(f"skill-{index}" for index in range(5)),
                "visible-extra-a",
                "visible-extra-b",
                "visible-extra-c",
            }
        },
    )

    assert merged == {
        "context": {
            "model": "provider:model-a",
            "temperature": 0.2,
            "skills": [f"skill-{index}" for index in range(10)],
        },
        "metadata": {"source": "owner"},
    }
    assert existing["context"]["skills"] == [f"skill-{index}" for index in range(10)]


def test_merge_agent_config_json_applies_visible_edits_and_preserves_hidden_references():
    """可见增删保留交错引用顺序，新选择追加到末尾。"""
    merged = merge_agent_config_json(
        {"context": {"skills": ["visible-a", "hidden-a", "visible-b", "hidden-b"]}},
        {"context": {"skills": ["visible-b", "visible-c", "hidden-a"]}},
        resource_access={"skills": {"visible-a", "visible-b", "visible-c"}},
    )

    assert merged["context"]["skills"] == ["hidden-a", "visible-b", "hidden-b", "visible-c"]


@pytest.mark.parametrize("strategy", ["all", []])
def test_merge_agent_config_json_replaces_resource_list_for_explicit_strategy_switch(strategy):
    """显式空列表或 all 整体切换资源策略。"""
    merged = merge_agent_config_json(
        {"context": {"skills": ["visible", "hidden"], "mcps": ["visible-mcp", "hidden-mcp"]}},
        {"context": {"skills": strategy, "mcps": strategy}},
        resource_access={"skills": {"visible"}, "mcps": {"visible-mcp"}},
        context_schema=ChatBotContext,
    )

    assert merged["context"]["skills"] == strategy
    assert merged["context"]["mcps"] == strategy


def test_merge_agent_config_json_rejects_new_unauthorized_resource_reference():
    """新增无权引用必须拒绝，不能借既有隐藏引用绕过。"""
    with pytest.raises(ValueError, match="无权新增.*skills.*hidden-new"):
        merge_agent_config_json(
            {"context": {"skills": ["visible", "hidden-existing"]}},
            {"context": {"skills": ["visible", "hidden-existing", "hidden-new"]}},
            resource_access={"skills": {"visible"}},
        )


def test_merge_agent_config_json_requires_authorization_result_for_resource_write():
    """缺少权限解析结果的直接资源写入必须拒绝。"""
    with pytest.raises(ValueError, match="skills 未经过权限校验"):
        merge_agent_config_json(
            {"context": {"skills": []}},
            {"context": {"skills": ["new-skill"]}},
            resource_access={},
        )


def test_merge_agent_config_json_validates_preload_skills_independently():
    """预加载选择独立保留不可见引用，不修改 Skill 允许列表。"""
    merged = merge_agent_config_json(
        {
            "context": {
                "skills": ["visible", "hidden"],
                "preload_skills": ["visible", "hidden"],
            }
        },
        {"context": {"preload_skills": ["visible"]}},
        resource_access={"preload_skills": {"visible"}},
    )

    assert merged["context"]["skills"] == ["visible", "hidden"]
    assert merged["context"]["preload_skills"] == ["visible", "hidden"]


@pytest.mark.asyncio
async def test_ensure_default_agent_creates_description(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)

    async def get_by_slug(_slug):
        return None

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent, changed = await repo.ensure_default_agent()

    assert agent.description == DEFAULT_AGENT_DESCRIPTION
    assert agent.config_json == {"context": {"preload_skills": ["chat-widgets", "knowledge-base"]}}
    assert db.added is agent
    assert changed is True
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_default_agent_backfills_missing_description(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)
    agent = SimpleNamespace(
        share_config=DEFAULT_SHARE_CONFIG.copy(),
        is_default=True,
        description=None,
        updated_by=None,
        updated_at=None,
    )

    async def get_by_slug(_slug):
        return agent

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    result, changed = await repo.ensure_default_agent(created_by="admin")

    assert result is agent
    assert agent.description == DEFAULT_AGENT_DESCRIPTION
    assert agent.updated_by == "admin"
    assert changed is True
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_preset_is_idempotent(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)
    existing = SimpleNamespace(slug=GENERAL_PURPOSE.slug, config_json={"context": {"model": "custom:model"}})

    async def get_by_slug(_slug):
        return existing

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    agent, changed = await repo.ensure_preset(GENERAL_PURPOSE)

    assert agent is existing
    assert db.added is None
    db.commit.assert_not_awaited()
    db.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_defaults_to_private_without_grants(monkeypatch):
    db = FakeDb()
    repo = AgentRepository(db)
    monkeypatch.setattr(repo, "_unique_slug", AsyncMock(return_value="personal-bot"))
    user = User(uid="user", role="user", user_kind="human", is_deleted=0)
    agent = await repo.create(name="Personal Bot", backend_id="ChatbotAgent", created_by="user", creator=user)
    assert agent.visibility == "private"
    assert agent.share_config == {"version": 2, "read_scope": None, "manage_scope": None}
    assert db.added is agent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"visibility": "shared"}, "共享"),
        ({"share_config": DEFAULT_SHARE_CONFIG}, "共享授权"),
        ({"created_by": "other"}, "所有者"),
    ],
)
async def test_normal_user_cannot_write_shared_definition(kwargs, reason):
    db = FakeDb()
    user = User(uid="user", role="user", user_kind="human", is_deleted=0)
    args = {"name": "Bot", "backend_id": "ChatbotAgent", "created_by": "user", "creator": user, **kwargs}
    with pytest.raises(ValueError, match=reason):
        await AgentRepository(db).create(**args)
    assert db.added is None
    db.commit.assert_not_awaited()


def test_user_shared_agent_is_read_only_even_when_management_scope_matches():
    user = User(uid="user", role="user", department_id=1)
    agent = Agent(
        slug="shared-bot",
        visibility="shared",
        created_by="other",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "global"},
        },
    )
    assert user_can_access_agent(user, agent)
    assert not user_can_manage_agent(user, agent)


@pytest.mark.asyncio
async def test_delegated_admin_update_preserves_shared_agent_acl():
    db = FakeDb()
    agent = _agent_for_update()
    agent.id = 1
    agent.visibility = "shared"
    db.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: ["manager"]))
    user = User(uid="manager", role="admin", user_kind="human", is_deleted=0)
    db.scalar = AsyncMock(side_effect=[agent, user, None])
    await update_agent_definition(db, agent, share_config=_MANAGER_USER_SCOPE, updater=user)
    assert agent.share_config["manage_scope"]["user_uids"] == ["manager"]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_normal_user_cannot_update_private_agent_grants():
    db = FakeDb()
    agent = _agent_for_update(created_by="manager")
    agent.id = 1
    agent.visibility = "private"
    user = User(uid="manager", role="user", user_kind="human", is_deleted=0)
    db.scalar = AsyncMock(side_effect=[agent, user, None])
    with pytest.raises(ValueError, match="私有智能体不接受共享授权"):
        await update_agent_definition(db, agent, share_config=_MANAGER_USER_SCOPE, updater=user)
    assert agent.share_config == _MANAGER_USER_SCOPE
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role,owner", [("admin", "manager"), ("superadmin", "owner")])
async def test_share_private_agent_updates_same_definition(role, owner):
    """管理员原地共享定义，保留所有者和完整配置。"""
    db = FakeDb()
    agent = _agent_for_update(created_by=owner)
    agent.id, agent.visibility = 1, "private"
    agent.config_json = {"context": {"system_prompt": "original", "skills": ["existing"]}}
    user = User(uid="manager", role=role, user_kind="human", is_deleted=0)
    db.scalar = AsyncMock(side_effect=[agent, user, None])

    updated = await update_agent_definition(db, agent, visibility="shared", share_config=DEFAULT_SHARE_CONFIG, updater=user)

    assert updated is agent
    assert (agent.id, agent.slug, agent.created_by, agent.visibility) == (1, "shared-bot", owner, "shared")
    assert agent.config_json == {"context": {"system_prompt": "original", "skills": ["existing"]}}
    assert agent.share_config == DEFAULT_SHARE_CONFIG


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,grants,error,message",
    [
        ("user", DEFAULT_SHARE_CONFIG, PermissionError, "仅管理员可共享智能体"),
        ("admin", None, ValueError, "必须指定共享权限"),
    ],
)
async def test_share_private_agent_requires_admin_and_explicit_grants(role, grants, error, message):
    """替代仓储调用仍拒绝普通用户发布或无明确授权的转换。"""
    db = FakeDb()
    original = {"version": 2, "read_scope": None, "manage_scope": None}
    agent = _agent_for_update(created_by="manager", share_config=original)
    agent.id, agent.visibility = 1, "private"
    user = User(uid="manager", role=role, user_kind="human", is_deleted=0)
    db.scalar = AsyncMock(side_effect=[agent, user, None])

    with pytest.raises(error, match=message):
        await update_agent_definition(db, agent, visibility="shared", share_config=grants, updater=user)

    assert agent.visibility == "private" and agent.share_config == original
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("field", ["tools", "knowledges", "skills", "mcps", "preload_skills"])
@pytest.mark.parametrize("invalid", [None, "full", ["ok", 1], [""], {"mode": "all"}])
def test_resource_write_rejects_invalid_selection(field, invalid):
    """替代写入路径同样拒绝非法资源配置。"""
    with pytest.raises(ValueError, match=field):
        merge_agent_config_json({}, {"context": {field: invalid}}, resource_access={}, context_schema=ChatBotContext)


def test_all_selection_is_not_a_previous_reference_list():
    """all 不授予新增不可见引用的权限。"""
    with pytest.raises(ValueError, match="无权新增"):
        merge_agent_config_json({"context": {"skills": "all"}}, {"context": {"skills": ["a"]}}, resource_access={"skills": set()})
    merged = merge_agent_config_json(
        {"context": {"skills": "all", "tools": "all"}},
        {"context": {"skills": ["a"]}},
        resource_access={"skills": {"a"}},
    )
    assert merged == {"context": {"skills": ["a"], "tools": "all"}}


@pytest.mark.parametrize("field", ["mcps", "preload_skills"])
def test_opt_in_resource_selection_preserves_all_intent(field):
    """默认关闭的资源同样可显式选择全部，且不展开持久化。"""
    merged = merge_agent_config_json({}, {"context": {field: "all"}}, resource_access={})
    assert merged == {"context": {field: "all"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_id", ["ChatbotAgent", "PersonalAssistantAgent"])
@pytest.mark.parametrize("include_configurable_items", [False, True])
async def test_serialize_agent_preserves_backend_response_contract(monkeypatch, backend_id, include_configurable_items):
    """统一响应保留权限，个人助理始终带身份且不提供通用配置描述。"""
    from yuxi.modules.agents.runtime.agent_backends import AGENT_BACKENDS
    from yuxi.modules.agents.runtime.agent_backends.personal_assistant.graph import PersonalAssistantAgent

    monkeypatch.setitem(AGENT_BACKENDS, "PersonalAssistantAgent", PersonalAssistantAgent)
    agent = Agent(
        slug="test-agent",
        name="测试智能体",
        backend_id=backend_id,
        created_by="owner",
        visibility="private",
        config_json={"context": {}},
        share_config=DEFAULT_SHARE_CONFIG.copy(),
    )
    user = User(uid="owner", username="owner", role="user", password_hash="test")

    result = await serialize_agent_definition(None, agent, user=user, include_configurable_items=include_configurable_items)

    assert result["backend_id"] == backend_id
    assert "is_subagent" not in result
    assert result["name"] == "测试智能体"
    assert result["can_manage"] is True
    assert "metadata" in result
    assert "capabilities" not in result
    if backend_id == "PersonalAssistantAgent":
        assert result["is_personal_assistant"] is True
        assert "configurable_items" not in result
    else:
        assert "is_personal_assistant" not in result
        assert ("configurable_items" in result) == include_configurable_items
        if include_configurable_items:
            assert "model" in result["configurable_items"]


@pytest_asyncio.fixture()
async def session():
    """建立可核对删除结果的持久单测库。"""
    engine = create_utc_sqlite_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed_agent(session):
    """创建待删除 Agent 与历史 Thread。"""
    user = User(username="owner", uid="owner", password_hash="x", role="superadmin")
    agent = Agent(
        visibility="shared",
        slug="custom-agent",
        backend_id="ChatbotAgent",
        name="Custom Agent",
        share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": None},
        created_by=user.uid,
    )
    thread = Session(thread_id="agent-thread", project_id="agent-project", uid=user.uid, agent_id=agent.slug, status="active")
    session.add_all([user, agent, thread])
    await session.flush()
    return user, agent, thread


@pytest.mark.parametrize("status", ["running", "waiting", "cancelling"])
async def test_delete_agent_returns_conflict_for_active_turn(session, status):
    """三种非终态 Turn 均禁止删除其 Agent。"""
    user, agent, _ = await _seed_agent(session)
    session.add(AgentTurn(id="active-turn", thread_id="agent-thread", uid=user.uid, status=status))
    await session.flush()

    with pytest.raises(HTTPException) as exc:
        await delete_agent(agent.slug, current_user=user, db=session)

    assert exc.value.status_code == 409
    assert await session.scalar(select(Agent.id).where(Agent.id == agent.id)) == agent.id


async def test_delete_agent_rejects_pending_input_after_prior_turn(session):
    """上轮已结束但新输入排队时仍保留 Agent。"""
    user, agent, _ = await _seed_agent(session)
    session.add(
        AgentInput(
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "pending"}],
                    "metadata": {},
                    "yuxi": {"attachment_file_ids": []},
                }
            ],
            id="pending-input",
            received_seq=1,
            thread_id="agent-thread",
            uid=user.uid,
            agent_slug=agent.slug,
            kind="follow_up",
            status="pending",
            input_payload={},
        )
    )
    await session.flush()

    with pytest.raises(ValueError, match="待处理输入"):
        await AgentRepository(session).delete(agent=agent, user=user)

    assert await session.scalar(select(Agent.id).where(Agent.id == agent.id)) == agent.id


@pytest.mark.parametrize(
    ("run_status", "cleanup_pending"),
    [("pending", False), ("completed", True)],
)
async def test_delete_agent_rejects_active_run_or_runtime_cleanup(session, run_status, cleanup_pending):
    """Turn 已终态也不能遗失 pending Run 或待清理 runtime。"""
    user, agent, thread = await _seed_agent(session)
    turn = AgentTurn(id="finished-turn", thread_id=thread.thread_id, uid=user.uid, status="completed")
    session.add(turn)
    await session.flush()
    session.add(
        AgentRun(
            id="unfinished-run",
            thread_id=thread.thread_id,
            runtime_scope_id=thread.thread_id,
            agent_slug=agent.slug,
            uid=user.uid,
            status=run_status,
            runtime_cleanup_pending=cleanup_pending,
            turn_id=turn.id,
            session_record_id=thread.id,
            run_type="chat",
            input_payload={},
        )
    )
    await session.flush()

    with pytest.raises(ValueError, match="活跃执行"):
        await AgentRepository(session).delete(agent=agent, user=user)

    assert await session.scalar(select(Agent.id).where(Agent.id == agent.id)) == agent.id


async def test_delete_agent_accepts_terminal_history(session):
    """仅有完成历史时允许删除 Agent，Thread 历史仍可保留。"""
    user, agent, thread = await _seed_agent(session)
    turn = AgentTurn(id="finished-turn", thread_id=thread.thread_id, uid=user.uid, status="completed")
    session.add(turn)
    await session.flush()
    session.add(
        AgentRun(
            id="finished-run",
            thread_id=thread.thread_id,
            runtime_scope_id=thread.thread_id,
            agent_slug=agent.slug,
            uid=user.uid,
            status="completed",
            runtime_cleanup_pending=False,
            turn_id=turn.id,
            session_record_id=thread.id,
            run_type="chat",
            input_payload={},
        )
    )
    await session.flush()

    await AgentRepository(session).delete(agent=agent, user=user)

    assert await session.scalar(select(Agent.id).where(Agent.slug == "custom-agent")) is None
    thread_id = await session.scalar(select(Session.thread_id).where(Session.agent_id == "custom-agent"))
    assert thread_id == "agent-thread"


async def test_repository_delete_rechecks_management_permission(session):
    """仓储直接调用不能绕过删除权限。"""
    _owner, agent, _ = await _seed_agent(session)
    reader = User(username="reader", uid="reader", password_hash="x", role="user")

    with pytest.raises(PermissionError, match="不能删除"):
        await AgentRepository(session).delete(agent=agent, user=reader)

    assert await session.scalar(select(Agent.id).where(Agent.id == agent.id)) == agent.id


async def test_create_lookup_uses_shared_key_lock():
    """Thread 创建查询须与 Agent 删除的独占锁冲突。"""

    class CaptureDb:
        """记录实际仓储查询。"""

        statement = None

        async def execute(self, statement):
            """返回不存在的 Agent 即可验证锁语句。"""
            self.statement = statement
            return self

        def scalar_one_or_none(self):
            """模拟空查询结果。"""
            return None

    db = CaptureDb()
    assert await AgentRepository(db).get_by_slug("custom-agent", for_key_share=True) is None
    sql = str(db.statement.compile(dialect=postgresql.dialect()))
    assert sql.endswith("FOR KEY SHARE")


async def test_shared_key_lookup_refreshes_stale_agent_in_same_session(session):
    """锁读刷新先前加载的 Agent，子执行不使用陈旧配置。"""
    _user, agent, _thread = await _seed_agent(session)
    await session.execute(text("UPDATE agents SET name = '修改名称' WHERE id = :id"), {"id": agent.id})
    await session.commit()
    assert agent.name != "修改名称"

    refreshed = await AgentRepository(session).get_by_slug(agent.slug, for_key_share=True)

    assert refreshed is agent
    assert refreshed.name == "修改名称"
