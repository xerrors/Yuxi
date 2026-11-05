"""真实 PostgreSQL 验证统一 Session 的队列、树范围和并发领取。"""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from agent_run_test_helpers import cleanup_agent_run_threads, create_agent_run
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.modules.agents.services.run_lifecycle import complete_run_runtime_cleanup, settle_run
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.models.cooperation import CooperationEvent
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.cooperation import CooperationRepository
from yuxi.modules.agents.repositories.definitions import DEFAULT_SHARE_CONFIG
from yuxi.modules.agents.repositories.runs import AgentRunRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.cooperation import SessionCooperationService, recover_cooperation_waits, tree_snapshot
from yuxi.modules.agents.services.scheduler import deliver
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.models import Project
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import DomainError, StateConflictError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture()
async def tree(monkeypatch):
    """每个用例建立独立用户和树，沙盒清理为替身，输出与范围从数据库回读。"""
    load_models()
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction():
        async with factory() as db:
            async with db.begin():
                yield db

    monkeypatch.setattr(pg_manager, "get_async_session_context", transaction)
    monkeypatch.setattr(
        "yuxi.modules.agents.services.run_lifecycle.get_sandbox_provider",
        lambda: SimpleNamespace(cleanup_run=lambda *_: None),
    )

    monkeypatch.setattr(
        "yuxi.modules.agents.services.input_config.model_cache.get_model_info",
        lambda spec: SimpleNamespace(model_type="chat"),
    )
    monkeypatch.setattr(
        "yuxi.modules.agents.services.input_config.system_options",
        SimpleNamespace(get=AsyncMock(return_value={"default_model": "test:mock"})),
    )
    monkeypatch.setattr("yuxi.modules.agents.services.cooperation.deliver", AsyncMock())
    monkeypatch.setattr("yuxi.modules.agents.services.scheduler.deliver", AsyncMock())
    run_id, thread_id, _message_id = await create_agent_run(
        factory,
        prefix="cooperation",
        message_content="PRIVATE_ROOT_HISTORY",
        input_payload={
            "context_snapshot": {
                "model": "test:mock",
                "tool_approval_mode": "always_trust",
                "tools": [],
                "system_prompt": "CONFIG",
            },
        },
        status="pending",
    )
    async with transaction() as db:
        root = await db.scalar(select(Session).where(Session.thread_id == thread_id))
        run = await db.get(AgentRun, run_id)
        root.config_snapshot = dict(run.input_payload["context_snapshot"])
        agent_slug = f"cooperation-{thread_id[-12:]}"
        root.agent_id = run.agent_slug = agent_slug
        db.add(
            Agent(
                slug=agent_slug,
                name="统一测试配置",
                backend_id="ChatbotAgent",
                visibility="private",
                created_by=root.uid,
                share_config=DEFAULT_SHARE_CONFIG,
                config_json={"context": {"tools": []}},
            )
        )
        _, acquired = await AgentRunRepository(db).mark_running(run_id, worker_id="root-worker", lease_seconds=120)
        assert acquired
        uid = root.uid
    try:
        yield factory, run_id, thread_id, uid
    finally:
        async with factory() as db:
            thread_ids = list((await db.scalars(select(Session.thread_id).where(Session.uid == uid))).all())
        try:
            await cleanup_agent_run_threads(factory, thread_ids)
            async with factory() as db:
                await db.execute(delete(Agent).where(Agent.slug.like(f"{agent_slug}-%")))
                await db.commit()
        finally:
            await engine.dispose()


async def child(tree, name="reviewer", call_id="create-reviewer"):
    """通过真实输入主链路创建成员。"""
    factory, run_id, _, uid = tree
    async with factory() as db:
        return await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").create_session(
            name=name, description=f"EXPLICIT_{name}", call_id=call_id
        )


async def test_creation_is_idempotent_and_context_is_independent(tree):
    factory, _, root_id, _ = tree
    created = await child(tree)
    replay = await child(tree)
    assert created == replay
    async with factory() as db:
        member = await db.scalar(select(Session).where(Session.thread_id == created["session_id"]))
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        assert member.tree_root_thread_id == root_id and member.parent_thread_id == root_id
        assert member.project_id == root.project_id and member.agent_id == root.agent_id
        run = await db.get(AgentRun, created["run_id"])
        assert run.run_type == "chat" and run.runtime_scope_id == root_id
        assert run.input_payload["context_snapshot"]["model"] == "test:mock"
        messages = list((await db.scalars(select(Message).where(Message.session_record_id == member.id))).all())
        assert [message.content for message in messages] == ["EXPLICIT_reviewer"]
        assert "PRIVATE_ROOT_HISTORY" not in str(run.input_payload)
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == member.thread_id)) == 1
    with pytest.raises(ValueError, match="名称"):
        await child(tree, name="other", call_id="create-reviewer")


async def test_agent_directory_returns_only_callable_role_metadata(tree):
    """目录和直接创建使用同一运行范围，隐藏其他私有及无授权共享角色。"""
    factory, run_id, root_id, uid = tree
    allowed = await _define_target_agent(tree)
    hidden_private = await _define_target_agent(tree, suffix="private", owner="another-user")
    hidden_shared = await _define_target_agent(tree, suffix="shared", visibility="shared", shared_uid="another-user")
    async with factory() as db:
        result = await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").list_agents()
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        roles = {agent["id"]: agent for agent in result["agents"]}
        assert allowed in roles and root.agent_id in roles
        assert hidden_private not in roles and hidden_shared not in roles
        assert roles[allowed] == {"id": allowed, "name": "独立核验角色", "description": "核验并交付证据"}
        assert all(set(role) == {"id", "name", "description"} for role in roles.values())


async def test_session_directory_defaults_to_full_project_and_pages_compact_history(tree):
    """项目目录完整、历史 SQL 分页，工具精简不改变完整树摘要。"""
    from sqlalchemy import event

    factory, run_id, root_id, uid = tree
    member = await child(tree, name="directory", call_id="directory-child")
    project_session_ids = [str(uuid.uuid4()) for _ in range(10)]
    history_session_ids = [str(uuid.uuid4()) for _ in range(13)]
    other_project_id = str(uuid.uuid4())
    timestamp = utc_now() - timedelta(days=1)
    recent_updates = {
        history_session_ids[0]: timestamp + timedelta(hours=2),
        project_session_ids[0]: timestamp + timedelta(hours=1),
    }
    async with factory() as db:
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        root.title = None
        db.add(
            Project(
                id=other_project_id,
                uid=uid,
                selection_status="implicit",
                workdir_path=f"projects/{other_project_id}",
                directory_mode="managed",
            )
        )
        await db.flush()
        for thread_id in project_session_ids + history_session_ids:
            db.add(
                Session(
                    thread_id=thread_id,
                    uid=uid,
                    app_id=root.app_id,
                    project_id=root.project_id if thread_id in project_session_ids else other_project_id,
                    agent_id=root.agent_id,
                    title=f"目录 {thread_id}",
                    status="archived" if thread_id == history_session_ids[0] else "active",
                    updated_at=recent_updates.get(thread_id, timestamp),
                )
            )
        for status, app_id in [("deleted", root.app_id), ("active", "another-app")]:
            db.add(
                Session(
                    thread_id=str(uuid.uuid4()),
                    uid=uid,
                    app_id=app_id,
                    project_id=root.project_id,
                    agent_id=root.agent_id,
                    status=status,
                    updated_at=timestamp,
                )
            )
        await db.flush()
        root.updated_at = timestamp
        (await db.scalar(select(Session).where(Session.thread_id == member["session_id"]))).updated_at = timestamp
        member_turn = await db.get(AgentTurn, member["turn_id"])
        member_turn.status = "waiting"
        member_turn.waitpoint = {"kind": "cooperation"}
        (await db.get(AgentRun, member["run_id"])).status = "interrupted"
        previous_turn_id, latest_turn_id = f"directory-a-{uuid.uuid4()}", f"directory-z-{uuid.uuid4()}"
        db.add_all(
            [
                AgentTurn(id=previous_turn_id, thread_id=project_session_ids[0], uid=uid, status="completed", created_at=timestamp),
                AgentTurn(id=latest_turn_id, thread_id=project_session_ids[0], uid=uid, status="failed", created_at=timestamp),
            ]
        )
        await db.commit()

    expected_project = {root_id, member["session_id"], *project_session_ids}
    expected_history = [history_session_ids[0], project_session_ids[0]]
    expected_history += sorted((expected_project | set(history_session_ids)) - set(expected_history), reverse=True)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        project = await service.list_sessions()
        assert set(project) == {"scope", "sessions"} and project["scope"] == "project"
        assert {item["session_id"] for item in project["sessions"]} == expected_project
        assert len(project["sessions"]) == 12
        assert await service.list_sessions(offset=10) == project
        fields = {"session_id", "title", "path", "tree_root_session_id", "project_id", "turn_id", "turn_status", "waiting_for"}
        assert all(set(item) == fields for item in project["sessions"])
        by_id = {item["session_id"]: item for item in project["sessions"]}
        assert by_id[root_id]["title"] == "root"
        assert by_id[member["session_id"]]["path"] == "/root/directory"
        assert by_id[member["session_id"]]["tree_root_session_id"] == root_id
        assert by_id[member["session_id"]]["turn_id"] == member["turn_id"]
        assert by_id[member["session_id"]]["turn_status"] == "waiting"
        assert by_id[member["session_id"]]["waiting_for"] == "cooperation"
        assert by_id[project_session_ids[0]]["turn_id"] == latest_turn_id
        assert by_id[project_session_ids[0]]["turn_status"] == "failed"
        assert by_id[project_session_ids[1]]["turn_id"] is None
        assert by_id[project_session_ids[1]]["turn_status"] is None
        assert by_id[project_session_ids[1]]["waiting_for"] is None

        queries = []

        def record(_conn, _cursor, statement, parameters, _context, _many):
            """记录真实目录 SQL，防止全量读取后在内存中截页。"""
            if "FROM sessions" in statement:
                queries.append((statement, parameters))

        engine = factory.kw["bind"].sync_engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            pages = [await service.list_sessions(scope="all", offset=offset) for offset in (0, 10, 20, 30)]
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert [len(page["sessions"]) for page in pages] == [10, 10, 5, 0]
        assert all(page["scope"] == "all" for page in pages)
        assert [item["session_id"] for page in pages for item in page["sessions"]] == expected_history
        assert all(set(item) == fields for page in pages for item in page["sessions"])
        assert {item["project_id"] for page in pages for item in page["sessions"]} == {root.project_id, other_project_id}
        directory_queries = [(statement, parameters) for statement, parameters in queries if "ORDER BY sessions.updated_at" in statement]
        assert len(directory_queries) == 4
        assert all("LIMIT" in statement and "OFFSET" in statement for statement, _ in directory_queries)
        assert [parameters[-2:] for _, parameters in directory_queries] == [(10, 0), (10, 10), (10, 20), (10, 30)]

        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        snapshot = await tree_snapshot(db, root)
        assert set(snapshot) == {"tree_root_session_id", "sessions"}
        assert {item["session_id"] for item in snapshot["sessions"]} == {root_id, member["session_id"]}
        assert all(
            {"queue_paused", "has_pending_input", "run_status", "current_run_id", "result_run_id"} <= set(item)
            for item in snapshot["sessions"]
        )


@pytest.mark.parametrize("arguments,error", [({"scope": "other"}, "范围"), ({"offset": -1}, "offset")])
async def test_session_directory_rejects_invalid_scope_or_negative_offset(tree, arguments, error):
    """服务入口也显式拒绝无效目录范围及负偏移。"""
    factory, run_id, _, uid = tree
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        with pytest.raises(ValueError, match=error):
            await service.list_sessions(**arguments)


async def test_agent_directory_does_not_turn_private_governance_into_run_permission(tree):
    """超级管理员可管理别人的私有配置，但协作目录不能宣称可运行。"""
    factory, run_id, _, uid = tree
    hidden = await _define_target_agent(tree, owner="another-user")
    async with factory() as db:
        user = await db.scalar(select(User).where(User.uid == uid))
        user.role = "superadmin"
        await db.commit()
        result = await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").list_agents()
        assert hidden not in {role["id"] for role in result["agents"]}


async def test_agent_directory_uses_app_key_owner_visibility_for_end_user(tree):
    """终端用户读取 Key 所有者授权的共享角色，不能使用所有者私有配置。"""
    factory, run_id, root_id, uid = tree
    owner_uid = f"owner-{uuid.uuid4().hex}"
    shared = await _define_target_agent(tree, suffix="app-shared", visibility="shared", shared_uid=owner_uid)
    private = await _define_target_agent(tree, suffix="app-private", owner=owner_uid)
    try:
        async with factory() as db:
            owner = User(uid=owner_uid, username=owner_uid, password_hash="test", role="admin")
            db.add(owner)
            await db.flush()
            user = await db.scalar(select(User).where(User.uid == uid))
            user.user_kind = "end_user"
            user.owner_user_id = owner.id
            user.app_id = "directory-app"
            user.end_user_id = "customer"
            root = await db.scalar(select(Session).where(Session.thread_id == root_id))
            root.app_id = "directory-app"
            run = await db.get(AgentRun, run_id)
            run.app_id = "directory-app"
            turn = await db.get(AgentTurn, run.turn_id)
            turn.app_id = "directory-app"
            root_agent = await db.scalar(select(Agent).where(Agent.slug == root.agent_id))
            root_agent.visibility = "shared"
            root_agent.share_config = DEFAULT_SHARE_CONFIG
            await db.commit()
            service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
            result = await service.list_agents()
            assert shared in {role["id"] for role in result["agents"]}
            assert private not in {role["id"] for role in result["agents"]}
            created = await service.create_session(name="app-worker", description="共享核验", call_id="app", agent_id=shared)
        async with factory() as db:
            child = await db.scalar(select(Session).where(Session.thread_id == created["session_id"]))
            assert child.uid == uid and child.app_id == "directory-app"
            assert child.agent_id == shared and child.tree_root_thread_id == root_id
    finally:
        async with factory() as db:
            user = await db.scalar(select(User).where(User.uid == uid))
            user.user_kind = "human"
            user.owner_user_id = user.app_id = user.end_user_id = None
            await db.flush()
            await db.execute(delete(User).where(User.uid == owner_uid))
            await db.commit()


@pytest.mark.parametrize("operation", ["list_agents", "create_session"])
async def test_agent_selection_rejects_deleted_execution_user(tree, operation):
    """失效账号直接调用服务也不能发现或派发 Agent。"""
    factory, run_id, root_id, uid = tree
    async with factory() as db:
        user = await db.scalar(select(User).where(User.uid == uid))
        user.is_deleted = 1
        await db.commit()
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        with pytest.raises(ValueError, match="账号已失效"):
            if operation == "list_agents":
                await service.list_agents()
            else:
                await service.create_session(name="invalid", description="拒绝", call_id="invalid")
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Session).where(Session.tree_root_thread_id == root_id)) == 1


async def test_selected_agent_freezes_target_config_model_and_parent_approval(tree):
    """回读持久会话与输入，证明角色配置、模型与审批各自来源。"""
    factory, run_id, root_id, uid = tree
    slug = await _define_target_agent(tree)
    async with factory() as db:
        service = SessionCooperationService(
            db, run_id=run_id, uid=uid, worker_id="root-worker", config_snapshot={"system_prompt": "PARENT_OVERRIDE"}
        )
        created = await service.create_session(name="reviewer", description="独立核验", call_id="choose", agent_id=slug)
        replay = await service.create_session(name="reviewer", description="独立核验", call_id="choose", agent_id=slug)
        assert created == replay
    async with factory() as db:
        member = await db.scalar(select(Session).where(Session.thread_id == created["session_id"]))
        run = await db.get(AgentRun, created["run_id"])
        assert member.agent_id == run.agent_slug == slug
        assert member.parent_thread_id == member.tree_root_thread_id == root_id
        assert member.config_snapshot["system_prompt"] == "TARGET_CONFIG"
        assert member.config_snapshot["tools"] == ["ask_user_question"]
        assert run.input_payload["context_snapshot"] == member.config_snapshot
        assert run.input_payload["context_snapshot"]["model"] == member.config_snapshot["model"] == "test:target"
        assert run.input_payload["context_snapshot"]["tool_approval_mode"] == "always_trust"
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == member.thread_id)) == 1


@pytest.mark.parametrize("initial_context", [None, {}])
async def test_empty_selected_config_stays_frozen_when_target_changes(tree, monkeypatch, initial_context):
    """创建后修改目标配置，执行准备仍读取已持久化的空快照。"""
    from types import SimpleNamespace

    from yuxi.modules.agents.services.preparation import prepare_run_execution

    factory, run_id, _, uid = tree
    slug = await _define_target_agent(tree)
    async with factory() as db:
        target = await db.scalar(select(Agent).where(Agent.slug == slug))
        target.config_json = {"context": initial_context}
        await db.commit()
        created = await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").create_session(
            name="empty", description="使用空配置", call_id="empty", agent_id=slug
        )
    async with factory() as db:
        target = await db.scalar(select(Agent).where(Agent.slug == slug))
        target.config_json = {"context": {"system_prompt": "CHANGED_AFTER_CREATION"}}
        await db.commit()

    async def prepare(context):
        """隔离工作区和 Skill 解析，保留真实执行配置装配。"""
        context._runtime_prepared = True
        context.skill_runtime_snapshot = {"preloaded_skills": [], "preloaded_skill_contents": {}, "skill_metadata": {}}
        return context

    monkeypatch.setattr("yuxi.modules.agents.services.preparation.prepare_agent_runtime_context", prepare)
    async with factory() as db:
        run = await db.get(AgentRun, created["run_id"])
        member = await db.scalar(select(Session).where(Session.thread_id == created["session_id"]))
        assert member.config_snapshot == run.input_payload["context_snapshot"]
        assert member.config_snapshot["system_prompt"] == "You are a helpful assistant."
        result = await prepare_run_execution(
            run=run,
            user=await db.scalar(select(User).where(User.uid == uid)),
            db=db,
            workdir_binding=SimpleNamespace(workdir_path="projects/fixture"),
            worker_id="snapshot-worker",
        )
        assert result.context.system_prompt == "You are a helpful assistant."
        assert result.context._cooperation_config_snapshot["system_prompt"] == "You are a helpful assistant."


@pytest.mark.parametrize("target_kind", ["missing", "private", "shared"])
async def test_selected_agent_rejects_inaccessible_target_before_persisting(tree, target_kind):
    """直接服务调用不能用已知 slug 绕过目录可见性并产生成员或输入。"""
    factory, run_id, root_id, uid = tree
    slug = "missing-agent"
    if target_kind != "missing":
        slug = await _define_target_agent(tree, visibility=target_kind, owner="another-user", shared_uid="another-user")
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        with pytest.raises(ValueError, match="无权限运行"):
            await service.create_session(name="forbidden", description="拒绝", call_id="forbidden", agent_id=slug)
    async with factory() as db:
        members = list((await db.scalars(select(Session).where(Session.tree_root_thread_id == root_id))).all())
        assert [member.thread_id for member in members] == [root_id]
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == root_id)) == 1


async def test_creation_replay_cannot_change_selected_agent(tree):
    """相同工具调用不能换 Agent 形成新的会话或重复输入。"""
    factory, run_id, root_id, uid = tree
    first = await _define_target_agent(tree, suffix="first")
    second = await _define_target_agent(tree, suffix="second")
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        created = await service.create_session(name="reviewer", description="核验", call_id="same", agent_id=first)
        with pytest.raises(ValueError, match="Agent 已变化"):
            await service.create_session(name="reviewer", description="核验", call_id="same", agent_id=second)
    async with factory() as db:
        members = list((await db.scalars(select(Session).where(Session.tree_root_thread_id == root_id))).all())
        assert len(members) == 2
        member = next(member for member in members if member.thread_id == created["session_id"])
        assert member.agent_id == first
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == member.thread_id)) == 1


@pytest.mark.parametrize("name", ["/root/aaa/bbb", "aaa/bbb", "/root/name", "../bbb", r"aaa\bbb", "aaa%2Fbbb"])
async def test_creation_rejects_paths_without_persisting_children(tree, name):
    """直接调用服务也不能用路径名称生成子会话或输入。"""
    factory, _, root_id, _ = tree
    async with factory() as db:
        before_inputs = await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == root_id))
    with pytest.raises(ValueError, match="name"):
        await child(tree, name=name)
    async with factory() as db:
        members = list((await db.scalars(select(Session).where(Session.tree_root_thread_id == root_id))).all())
        assert [member.thread_id for member in members] == [root_id]
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == root_id)) == before_inputs


async def test_creation_derives_direct_parent_and_path_from_calling_session(tree):
    """真实派发 Run 决定父节点，同名兄弟与后代互不混淆。"""
    factory, root_run_id, root_id, uid = tree
    parent = await child(tree, name="aaa", call_id="create-aaa")
    sibling = await child(tree, name="bbb", call_id="create-bbb")
    async with factory() as db:
        _, acquired = await AgentRunRepository(db).mark_running(parent["run_id"], worker_id="aaa-worker", lease_seconds=120)
        assert acquired
        await db.commit()
        nested = await SessionCooperationService(db, run_id=parent["run_id"], uid=uid, worker_id="aaa-worker").create_session(
            name="bbb", description="仅由 aaa 创建的直属工作", call_id="create-bbb"
        )
    async with factory() as db:
        members = list((await db.scalars(select(Session).where(Session.tree_root_thread_id == root_id))).all())
        assert {member.cooperation_path for member in members} == {"/root", "/root/aaa", "/root/bbb", "/root/aaa/bbb"}
        by_id = {member.thread_id: member for member in members}
        assert by_id[parent["session_id"]].parent_thread_id == root_id
        assert by_id[sibling["session_id"]].parent_thread_id == root_id
        assert by_id[sibling["session_id"]].created_by_run_id == root_run_id
        descendant = by_id[nested["session_id"]]
        assert descendant.parent_thread_id == parent["session_id"]
        assert descendant.cooperation_name == "bbb" and descendant.cooperation_path == "/root/aaa/bbb"
        assert descendant.created_by_run_id == parent["run_id"]


async def test_tree_summary_orders_by_parent_not_path_characters(tree):
    """路径字符序（`-` 先于 `/`）不得决定层级；子会话必须紧跟真实父节点。"""
    factory, _, root_id, uid = tree
    member_a = await child(tree, name="a", call_id="create-a")
    member_dash = await child(tree, name="a-b", call_id="create-a-b")
    async with factory() as db:
        _, acquired = await AgentRunRepository(db).mark_running(member_a["run_id"], worker_id="a-worker", lease_seconds=120)
        assert acquired
        await db.commit()
        nested = await SessionCooperationService(db, run_id=member_a["run_id"], uid=uid, worker_id="a-worker").create_session(
            name="child", description="仅由 a 创建的子会话", call_id="create-child"
        )
        await db.commit()
    async with factory() as db:
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        snapshot = await tree_snapshot(db, root)
    ids = [item["session_id"] for item in snapshot["sessions"]]
    assert ids == [root_id, member_a["session_id"], nested["session_id"], member_dash["session_id"]]


async def test_tree_summary_fails_closed_when_members_unreachable_from_root(tree):
    """父成员不可见而后代仍可见时，摘要显式失败而不是悄悄丢弃成员。"""
    factory, _, root_id, uid = tree
    parent = await child(tree, name="cut", call_id="create-cut")
    async with factory() as db:
        _, acquired = await AgentRunRepository(db).mark_running(parent["run_id"], worker_id="cut-worker", lease_seconds=120)
        assert acquired
        await db.commit()
        await SessionCooperationService(db, run_id=parent["run_id"], uid=uid, worker_id="cut-worker").create_session(
            name="orphan", description="父成员不可见后的后代", call_id="create-orphan"
        )
        await db.commit()
        # 当前业务没有软删除路径；直写制造父成员不可见、后代仍可见的损坏前提。
        hidden = await db.scalar(select(Session).where(Session.thread_id == parent["session_id"]))
        hidden.status = "deleted"
        await db.commit()
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        with pytest.raises(ValueError, match="无法从根会话完整遍历"):
            await tree_snapshot(db, root)


async def test_whole_tree_shares_four_slots_and_recursive_creation(tree, monkeypatch):
    """名额共享语义固定在上限 4，不随共享库中保存的其他配置值漂移。"""
    monkeypatch.setattr(
        "yuxi.modules.agents.repositories.cooperation.system_options",
        SimpleNamespace(get=AsyncMock(return_value={"max_cooperation_concurrency": 4})),
    )
    factory, _, _, uid = tree
    members = [await child(tree, name=f"worker{i}", call_id=f"create{i}") for i in range(4)]

    async def claim(member):
        async with factory() as db:
            async with db.begin():
                return (
                    await AgentRunRepository(db).mark_running(
                        member["run_id"], worker_id=f"worker-{member['session_id']}", lease_seconds=120
                    )
                )[1]

    acquired = await asyncio.gather(*(claim(member) for member in members))
    assert sum(acquired) == 3
    running = members[acquired.index(True)]
    async with factory() as db:
        grandchild = await SessionCooperationService(
            db, run_id=running["run_id"], uid=uid, worker_id=f"worker-{running['session_id']}"
        ).create_session(name="nested", description="EXPLICIT_NESTED", call_id="nested")
    assert grandchild["path"].endswith("/nested")
    assert not await claim(grandchild)
    async with factory() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(AgentRun).where(AgentRun.runtime_scope_id == tree[2], AgentRun.status == "running")
            )
            == 4
        )


async def test_execution_slot_limit_reads_system_option(tree, monkeypatch):
    """执行名额上限来自系统配置，降到 1 后成员执行被暂缓认领。"""
    factory, _, _, uid = tree
    member = await child(tree)
    monkeypatch.setattr(
        "yuxi.modules.agents.repositories.cooperation.system_options",
        SimpleNamespace(get=AsyncMock(return_value={"max_cooperation_concurrency": 1})),
    )
    async with factory() as db:
        _, acquired = await AgentRunRepository(db).mark_running(member["run_id"], worker_id="limit-worker", lease_seconds=120)
        assert not acquired
        assert (
            await db.scalar(
                select(func.count()).select_from(AgentRun).where(AgentRun.runtime_scope_id == tree[2], AgentRun.status == "running")
            )
            == 1
        )


async def test_message_is_durable_idempotent_and_does_not_create_work(tree):
    factory, run_id, _, uid = tree
    created = await child(tree)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        first = await service.send_message(target=created["session_id"], content="information", call_id="message")
        await db.commit()
        replay = await service.send_message(target=created["path"], content="information", call_id="message")
        assert first == replay
        await db.commit()
    async with factory() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(CooperationEvent).where(CooperationEvent.recipient_thread_id == created["session_id"])
            )
            == 1
        )
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == created["session_id"])) == 1


async def test_cross_tree_target_is_allowed_but_cross_app_is_rejected(tree):
    factory, run_id, root_id, uid = tree
    async with factory() as db:
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        stranger = Session(thread_id=f"{root_id}-x", uid=uid, project_id=root.project_id, agent_id=root.agent_id, status="active")
        other_app = Session(
            thread_id=f"{root_id}-a",
            tree_root_thread_id=root_id,
            parent_thread_id=root_id,
            cooperation_path="/root/other-app",
            cooperation_name="other-app",
            uid=uid,
            app_id="another-app",
            project_id=root.project_id,
            agent_id=root.agent_id,
            status="active",
        )
        db.add_all([stranger, other_app])
        await db.commit()
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        await service.send_message(target=stranger.thread_id, content="cross-tree", call_id="cross-tree")
        with pytest.raises(ValueError, match="授权范围"):
            await service.send_message(target=other_app.thread_id, content="forbidden", call_id="other-app")
        assert (
            await db.scalar(
                select(func.count())
                .select_from(CooperationEvent)
                .where(CooperationEvent.recipient_thread_id == other_app.thread_id)
            )
            == 0
        )


async def test_busy_input_queues_but_human_wait_rejects(tree):
    factory, run_id, _, uid = tree
    member = await child(tree)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        queued = await service.submit_input(target=member["session_id"], description="next", call_id="next")
        assert queued["status"] == "queued" and queued["turn_id"] is None
        turn = await db.get(AgentTurn, member["turn_id"])
        turn.status = "waiting"
        turn.waitpoint = {"kind": "approval", "run_id": member["run_id"]}
        await db.commit()
        with pytest.raises(DomainError) as error:
            await service.submit_input(target=member["session_id"], description="bypass", call_id="bypass")
        assert isinstance(error.value, StateConflictError)


async def test_dispatch_failure_after_persisted_accept_keeps_stable_receipt(tree, monkeypatch):
    """真实领取提交后发布失败，回执及补投保持同一 Input、Turn 和 Run。"""
    from yuxi.modules.agents.services.scheduler import recover_pending_dispatches

    factory, run_id, _, uid = tree
    member = await child(tree)
    await complete_member(factory, member, "DONE")

    async def broken_enqueue(_run_id):
        """在已提交 Run 的真正发布点制造故障。"""
        raise ConnectionError("dispatch backend unavailable")

    monkeypatch.setattr("yuxi.modules.agents.services.scheduler.deliver", deliver)
    monkeypatch.setattr("yuxi.modules.agents.services.scheduler.ensure_bound_user_workdir", lambda *_args: None)
    monkeypatch.setattr("yuxi.modules.agents.services.transport.enqueue_agent_run", broken_enqueue)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        accepted = await service.submit_input(target=member["session_id"], description="FOLLOWUP", call_id="dispatch-fail")
        assert accepted["status"] == "started" and accepted["input_id"]
        assert await service.submit_input(target=member["session_id"], description="FOLLOWUP", call_id="dispatch-fail") == accepted
    async with factory() as db:
        item = await db.get(AgentInput, accepted["input_id"])
        run = await db.get(AgentRun, item.consumed_run_id)
        turn = await db.get(AgentTurn, item.turn_id)
        assert item.status == "consumed" and run.status == "pending"
        assert turn.current_run_id == run.id == accepted["run_id"] and turn.id == accepted["turn_id"]
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == member["session_id"])) == 2
    enqueued = AsyncMock()
    monkeypatch.setattr("yuxi.modules.agents.services.transport.enqueue_agent_run", enqueued)
    await recover_pending_dispatches()
    enqueued.assert_awaited_once_with(accepted["run_id"])
    async with factory() as db:
        item = await db.get(AgentInput, accepted["input_id"])
        assert item.turn_id == accepted["turn_id"] and item.consumed_run_id == accepted["run_id"]
        assert await db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.thread_id == member["session_id"])) == 2


async def test_accept_does_not_hide_dispatch_claim_failure(tree, monkeypatch):
    """领取事务失败不能伪装成投递延迟；持久 Input 仍能用原键取回。"""
    factory, run_id, _, uid = tree
    member = await child(tree)
    await complete_member(factory, member, "DONE")

    async def broken_claim(**_kwargs):
        """制造领取事务中的业务错误，而非发布错误。"""
        raise ValueError("invalid claim configuration")

    monkeypatch.setattr("yuxi.modules.agents.services.scheduler.claim_next_input", broken_claim)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        with pytest.raises(ValueError, match="invalid claim configuration"):
            await service.submit_input(target=member["session_id"], description="FOLLOWUP", call_id="claim-fail")
        replay = await service.submit_input(target=member["session_id"], description="FOLLOWUP", call_id="claim-fail")
    async with factory() as db:
        item = await db.get(AgentInput, replay["input_id"])
        assert item.status == "pending" and item.turn_id is None
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == member["session_id"])) == 2


async def test_cooperation_wait_releases_slot_and_recovers_same_turn(tree):
    factory, run_id, root_id, uid = tree
    member = await child(tree)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        wait = await service.wait_updates(targets=[member["session_id"]], after_cursor=0, timeout_seconds=300)
        assert wait["kind"] == "cooperation"
        run = await db.get(AgentRun, run_id)
        await settle_run(
            db=db,
            run=run,
            worker_id="root-worker",
            status="interrupted",
            token_usage=None,
            waitpoint={"id": "wait-root", "run_id": run_id, "kind": "cooperation", "waits": [{**wait, "interrupt_id": "wait-root"}]},
        )
        await db.commit()
        _, acquired = await AgentRunRepository(db).mark_running(member["run_id"], worker_id="child-worker", lease_seconds=120)
        assert acquired
        await db.commit()
        sender = await db.scalar(select(Session).where(Session.thread_id == member["session_id"]))
        recipient = await db.scalar(select(Session).where(Session.thread_id == root_id))
        await CooperationRepository(db).append(sender=sender, recipient=recipient, key="wake", kind="message", content="ready")
        await db.commit()
    await recover_cooperation_waits()
    await recover_cooperation_waits()
    async with factory() as db:
        old = await db.get(AgentRun, run_id)
        turn = await db.get(AgentTurn, old.turn_id)
        resumed = await db.get(AgentRun, turn.current_run_id)
        assert old.status == "interrupted" and old.worker_id is None
        assert resumed.id != old.id and resumed.run_type == "resume"
        assert resumed.turn_id == old.turn_id and resumed.resume_from_run_id == old.id
        assert resumed.runtime_scope_id == root_id and turn.status == "running"
        assert await db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.turn_id == old.turn_id)) == 2


async def test_tool_side_effects_replay_across_resume_runs(tree):
    """同 Turn 的恢复 Run 不重复副作用，冲突重放仍拒绝且保留首次来源。"""
    factory, run_id, root_id, uid = tree
    member = await child(tree)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        message = await service.send_message(target=member["session_id"], content="information", call_id="message")
        await db.commit()
        submitted = await service.submit_input(target=member["session_id"], description="FOLLOWUP", call_id="followup")
        wait = await service.wait_updates(targets=[member["session_id"]], after_cursor=0, timeout_seconds=300)
        await settle_run(
            db=db,
            run=await db.get(AgentRun, run_id),
            worker_id="root-worker",
            status="interrupted",
            token_usage=None,
            waitpoint={"id": "replay-wait", "run_id": run_id, "kind": "cooperation", "waits": [{**wait, "interrupt_id": "replay-wait"}]},
        )
        await db.commit()
        await CooperationRepository(db).append(
            sender=await db.scalar(select(Session).where(Session.thread_id == member["session_id"])),
            recipient=await db.scalar(select(Session).where(Session.thread_id == root_id)),
            key="replay-wake",
            kind="message",
            content="ready",
        )
        await db.commit()
    await recover_cooperation_waits()
    async with factory() as db:
        old = await db.get(AgentRun, run_id)
        turn = await db.get(AgentTurn, old.turn_id)
        resumed_id = turn.current_run_id
        assert resumed_id != run_id
        await complete_run_runtime_cleanup(old)
        _, acquired = await AgentRunRepository(db).mark_running(resumed_id, worker_id="resume-worker", lease_seconds=120)
        assert acquired
        await db.commit()
        service = SessionCooperationService(db, run_id=resumed_id, uid=uid, worker_id="resume-worker")
        assert await service.create_session(name="reviewer", description="EXPLICIT_reviewer", call_id="create-reviewer") == member
        assert await service.send_message(target=member["session_id"], content="information", call_id="message") == message
        await db.commit()
        assert await service.submit_input(target=member["session_id"], description="FOLLOWUP", call_id="followup") == submitted
        for operation, match in (
            (
                lambda: service.create_session(name="changed", description="EXPLICIT_reviewer", call_id="create-reviewer"),
                "名称",
            ),
            (
                lambda: service.submit_input(target=member["session_id"], description="CHANGED", call_id="followup"),
                "Idempotency-Key",
            ),
            (lambda: service.send_message(target=member["session_id"], content="CHANGED", call_id="message"), "幂等"),
        ):
            with pytest.raises((ValueError, DomainError), match=match):
                await operation()
            await db.rollback()
    async with factory() as db:
        created = await db.scalar(select(Session).where(Session.thread_id == member["session_id"]))
        assert created.created_by_run_id == run_id
        event = await db.get(CooperationEvent, message["message_id"])
        assert event.source_run_id == run_id
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == created.thread_id)) == 2
        assert (
            await db.scalar(
                select(func.count()).select_from(CooperationEvent).where(CooperationEvent.recipient_thread_id == created.thread_id)
            )
            == 1
        )


async def test_same_call_id_in_new_turn_creates_new_work(tree):
    """幂等只跨同 Turn 恢复，新轮次可以复用模型工具调用 ID。"""
    from yuxi.modules.agents.repositories.turn import AgentTurnRepository

    factory, run_id, root_id, uid = tree
    member = await child(tree)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        first = await service.submit_input(target=member["session_id"], description="FOLLOWUP", call_id="same-call")
        old = await db.get(AgentRun, run_id)
        _, changed = await AgentRunRepository(db).set_terminal_status(run_id, status="failed", worker_id="root-worker")
        assert changed
        await AgentTurnRepository(db).set_terminal(await db.get(AgentTurn, old.turn_id), status="failed")
        await db.flush()
        turn = AgentTurn(id=str(uuid.uuid4()), thread_id=root_id, uid=uid, app_id=None, status="running")
        db.add(turn)
        await db.flush()
        new_run = await AgentRunRepository(db).create_run(
            run_id=str(uuid.uuid4()),
            thread_id=root_id,
            agent_slug=old.agent_slug,
            uid=uid,
            turn_id=turn.id,
            input_payload=old.input_payload,
            session_record_id=old.session_record_id,
        )
        turn.current_run_id = new_run.id
        await db.commit()
        await complete_run_runtime_cleanup(old)
        _, acquired = await AgentRunRepository(db).mark_running(new_run.id, worker_id="new-turn-worker", lease_seconds=120)
        assert acquired
        await db.commit()
        second = await SessionCooperationService(db, run_id=new_run.id, uid=uid, worker_id="new-turn-worker").submit_input(
            target=member["session_id"], description="FOLLOWUP", call_id="same-call"
        )
        assert second["input_id"] != first["input_id"]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == member["session_id"])) == 3


async def test_cancel_parent_preserves_descendants(tree, monkeypatch):
    """取消父轮次只取消指定 Run，后代的独立队列继续保留。"""
    from yuxi.modules.agents.scope import ActorScope
    from yuxi.modules.agents.services.turns import cancel_turn

    monkeypatch.setattr("yuxi.modules.agents.services.turns.publish_cancel_signals", AsyncMock())
    factory, run_id, root_id, uid = tree
    member = await child(tree)
    async with factory() as db:
        root_run = await db.get(AgentRun, run_id)
        await cancel_turn(
            db=db,
            scope=ActorScope(uid=uid, app_id=None),
            thread_id=root_id,
            turn_id=root_run.turn_id,
            idempotency_key="cancel-root",
        )
    async with factory() as db:
        assert (await db.get(AgentRun, run_id)).status == "cancel_requested"
        assert (await db.get(AgentRun, member["run_id"])).status == "pending"
        assert (await db.get(AgentTurn, member["turn_id"])).status == "running"
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        descendant = await db.scalar(select(Session).where(Session.thread_id == member["session_id"]))
        assert not root.queue_paused and not descendant.queue_paused


async def test_user_message_after_tree_stop_resumes_only_target_queue(tree, monkeypatch):
    """整树收尾后，新消息优先恢复当前会话，旧子队列保持暂停。"""
    from yuxi.modules.agents.input_messages import build_chat_input_message
    from yuxi.modules.agents.models.cooperation import CooperationRuntime
    from yuxi.modules.agents.services.cooperation import control_tree
    from yuxi.modules.agents.services.inputs import accept_message
    from yuxi.modules.agents.services.leases import complete_run_runtime_cleanup

    monkeypatch.setattr("yuxi.modules.agents.services.turns.publish_cancel_signals", AsyncMock())
    monkeypatch.setattr(
        "yuxi.modules.agents.services.run_lifecycle.get_sandbox_provider",
        lambda: type("Provider", (), {"cleanup_run": staticmethod(lambda *_: None)})(),
    )
    factory, run_id, root_id, uid = tree
    member = await child(tree)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        old_root = await service.submit_input(target=root_id, description="old root", call_id="old-root")
        old_child = await service.submit_input(target=member["session_id"], description="old child", call_id="old-child")
        await control_tree(db=db, scope=ActorScope(uid=uid, app_id=None), thread_id=root_id, idempotency_key="stop-all", stopped=True)
    arguments = dict(
        scope=ActorScope(uid=uid, app_id=None),
        thread_id=root_id,
        idempotency_key="new-work",
        mode=None,
        messages=[build_chat_input_message("new root work")],
    )
    async with factory() as db:
        with pytest.raises(StateConflictError, match="停止全部"):
            await accept_message(db=db, **arguments)
    async with factory() as db:
        run = await db.get(AgentRun, run_id)
        await settle_run(db=db, run=run, worker_id="root-worker", status="cancelled", token_usage=None)
        await db.commit()
    await complete_run_runtime_cleanup(run)
    async with factory() as db:
        accepted = await accept_message(db=db, **arguments)
    async with factory() as db:
        assert not (await db.get(CooperationRuntime, root_id)).stopped
        fresh = await db.get(AgentInput, accepted["input_id"])
        assert fresh.status == "consumed"
        assert (await db.get(AgentInput, old_root["input_id"])).status == "pending"
        assert (await db.get(AgentInput, old_child["input_id"])).status == "pending"
        descendant = await db.scalar(select(Session).where(Session.thread_id == member["session_id"]))
        assert descendant.queue_paused


@pytest.mark.parametrize("resume_entry", ["continue", "user_message"])
@pytest.mark.parametrize("cleanup_owner", ["root", "child"])
async def test_tree_resume_requires_all_runtime_cleanup_and_replays_without_changing_later_stop(
    tree, monkeypatch, resume_entry, cleanup_owner
):
    """两种恢复均等待整树清理，拒绝不改事实，成功与重放保留各自队列语义。"""
    from yuxi.modules.agents.input_messages import build_chat_input_message
    from yuxi.modules.agents.models.cooperation import CooperationRuntime
    from yuxi.modules.agents.models.inputs import AgentInputReceipt
    from yuxi.modules.agents.services.cooperation import control_tree
    from yuxi.modules.agents.services.inputs import accept_message

    monkeypatch.setattr("yuxi.modules.agents.services.turns.publish_cancel_signals", AsyncMock())
    factory, run_id, root_id, uid = tree
    member = await child(tree)
    scope = ActorScope(uid=uid, app_id=None)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        root_input = await service.submit_input(target=root_id, description="old root work", call_id="cleanup-old-root")
        child_input = await service.submit_input(target=member["session_id"], description="old child work", call_id="cleanup-old-child")
        child_run, acquired = await AgentRunRepository(db).mark_running(member["run_id"], worker_id="child-worker", lease_seconds=120)
        assert acquired
        root_run = await db.get(AgentRun, run_id)
        for run, worker_id in [(root_run, "root-worker"), (child_run, "child-worker")]:
            settlement = await settle_run(db=db, run=run, worker_id=worker_id, status="failed", token_usage=None)
            assert settlement.changed and settlement.status == "failed"
        await db.commit()
    pending_cleanup = root_run if cleanup_owner == "root" else child_run
    await complete_run_runtime_cleanup(child_run if cleanup_owner == "root" else root_run)
    async with factory() as db:
        await control_tree(db=db, scope=scope, thread_id=root_id, idempotency_key="stop-before-cleanup", stopped=True)

    async def snapshot():
        """独立连接读取恢复操作不得提前改变的业务事实。"""
        async with factory() as db:
            return {
                "stopped": (await db.get(CooperationRuntime, root_id)).stopped,
                "queues": list(
                    await db.execute(select(Session.thread_id, Session.queue_paused).where(Session.uid == uid).order_by(Session.thread_id))
                ),
                "inputs": list(
                    await db.execute(
                        select(AgentInput.id, AgentInput.status, AgentInput.turn_id, AgentInput.consumed_run_id)
                        .where(AgentInput.uid == uid)
                        .order_by(AgentInput.id)
                    )
                ),
                "receipts": list(
                    await db.scalars(select(AgentInputReceipt.id).where(AgentInputReceipt.uid == uid).order_by(AgentInputReceipt.id))
                ),
                "turns": list(
                    await db.execute(
                        select(AgentTurn.id, AgentTurn.status, AgentTurn.current_run_id).where(AgentTurn.uid == uid).order_by(AgentTurn.id)
                    )
                ),
                "runs": list(
                    await db.execute(
                        select(AgentRun.id, AgentRun.status, AgentRun.runtime_cleanup_pending)
                        .where(AgentRun.uid == uid)
                        .order_by(AgentRun.id)
                    )
                ),
            }

    async def resume():
        """从真实用户入口请求同一恢复意图。"""
        async with factory() as db:
            if resume_entry == "continue":
                return await control_tree(db=db, scope=scope, thread_id=root_id, idempotency_key="resume-after-cleanup", stopped=False)
            return await accept_message(
                db=db,
                scope=scope,
                thread_id=root_id,
                idempotency_key="resume-after-cleanup",
                mode=None,
                messages=[build_chat_input_message("fresh user work")],
            )

    before = await snapshot()
    assert before["stopped"] and all(paused for _, paused in before["queues"])
    with pytest.raises(StateConflictError, match="停止全部任务.*清理"):
        await resume()
    assert await snapshot() == before

    await complete_run_runtime_cleanup(pending_cleanup)
    accepted = await resume()
    async with factory() as db:
        assert not (await db.get(CooperationRuntime, root_id)).stopped
        root_item, child_item = await db.get(AgentInput, root_input["input_id"]), await db.get(AgentInput, child_input["input_id"])
        if resume_entry == "continue":
            assert root_item.status == child_item.status == "consumed"
            for item in [root_item, child_item]:
                run, acquired = await AgentRunRepository(db).mark_running(
                    item.consumed_run_id, worker_id="after-cleanup", lease_seconds=120
                )
                assert acquired and run.status == "running"
            await db.commit()
        else:
            assert root_item.status == child_item.status == "pending"
            assert (await db.get(AgentInput, accepted["input_id"])).status == "consumed"
            assert (await db.scalar(select(Session).where(Session.thread_id == member["session_id"]))).queue_paused

    async with factory() as db:
        await control_tree(db=db, scope=scope, thread_id=root_id, idempotency_key="stop-new-work", stopped=True)
    stopped_again = await snapshot()
    assert stopped_again["stopped"]
    assert await resume() == accepted
    assert await snapshot() == stopped_again


async def test_tree_stop_pauses_idle_queues_before_user_can_resume(tree, monkeypatch):
    """停止扫描尚未遍历时，新用户恢复也不能唤醒其他成员的旧待办。"""
    from yuxi.modules.agents.input_messages import build_chat_input_message
    from yuxi.modules.agents.repositories.turn import AgentTurnRepository
    from yuxi.modules.agents.services import cooperation
    from yuxi.modules.agents.services.inputs import accept_message
    from yuxi.modules.agents.services.scheduler import dispatch_next_input

    factory, run_id, root_id, uid = tree
    member = await child(tree)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        await service.submit_input(target=root_id, description="old root", call_id="idle-root")
        old_child = await service.submit_input(target=member["session_id"], description="old child", call_id="idle-child")
        root_run = await db.get(AgentRun, run_id)
        await settle_run(db=db, run=root_run, worker_id="root-worker", status="failed", token_usage=None)
        child_run, changed = await AgentRunRepository(db).set_terminal_status(member["run_id"], status="failed")
        assert changed
        await AgentTurnRepository(db).set_terminal(await db.get(AgentTurn, member["turn_id"]), status="failed")
        root_run.runtime_cleanup_pending = child_run.runtime_cleanup_pending = False
        (await db.scalar(select(Session).where(Session.thread_id == root_id))).queue_paused = False
        await db.commit()

    scan_started, allow_scan = asyncio.Event(), asyncio.Event()
    original_scan = cooperation.reconcile_stopped_trees

    async def delayed_scan(**kwargs):
        """在停止已提交、逐成员取消尚未开始的真实边界让出执行。"""
        scan_started.set()
        await allow_scan.wait()
        await original_scan(**kwargs)

    monkeypatch.setattr(cooperation, "reconcile_stopped_trees", delayed_scan)

    async def stop_tree():
        """用独立事务提交整树停止。"""
        async with factory() as db:
            await cooperation.control_tree(
                db=db, scope=ActorScope(uid=uid, app_id=None), thread_id=root_id, idempotency_key="stop-idle-tree", stopped=True
            )

    stopping = asyncio.create_task(stop_tree())
    try:
        await asyncio.wait_for(scan_started.wait(), 5)
        async with factory() as db:
            await accept_message(
                db=db,
                scope=ActorScope(uid=uid, app_id=None),
                thread_id=root_id,
                idempotency_key="resume-before-scan",
                mode=None,
                messages=[build_chat_input_message("new root work")],
            )
        assert await dispatch_next_input(uid=uid, agent_slug=child_run.agent_slug, thread_id=member["session_id"]) is True
        async with factory() as db:
            assert (await db.get(AgentInput, old_child["input_id"])).status == "pending"
            assert (await db.scalar(select(Session).where(Session.thread_id == member["session_id"]))).queue_paused
    finally:
        allow_scan.set()
        await asyncio.wait_for(stopping, 10)


async def test_old_stop_scan_cannot_repause_continued_tree(tree, monkeypatch):
    """让旧停止扫描晚于继续事务取得成员锁，验证不会重新暂停队列。"""
    from yuxi.modules.agents.models.cooperation import CooperationRuntime
    from yuxi.modules.agents.repositories.sessions import SessionRepository
    from yuxi.modules.agents.scope import ActorScope
    from yuxi.modules.agents.services.cooperation import control_tree, reconcile_stopped_trees

    factory, run_id, root_id, uid = tree
    async with factory() as db:
        run = await db.get(AgentRun, run_id)
        run.status = "completed"
        run.worker_id = None
        turn = await db.get(AgentTurn, run.turn_id)
        turn.status = "completed"
        turn.result_run_id = run.id
        runtime = await db.get(CooperationRuntime, root_id)
        runtime.stopped = True
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        root.queue_paused = True
        await db.commit()
    scanned, continue_committed = asyncio.Event(), asyncio.Event()
    original_lock = SessionRepository.lock_session_by_thread_id

    async def delayed_lock(repo, thread_id):
        scanned.set()
        await continue_committed.wait()
        return await original_lock(repo, thread_id)

    monkeypatch.setattr(SessionRepository, "lock_session_by_thread_id", delayed_lock)
    monkeypatch.setattr("yuxi.modules.agents.services.scheduler.dispatch_next_input", AsyncMock())
    old_stop = asyncio.create_task(reconcile_stopped_trees())
    await asyncio.wait_for(scanned.wait(), 5)
    try:
        async with factory() as db:
            await control_tree(
                db=db,
                scope=ActorScope(uid=uid, app_id=None),
                thread_id=root_id,
                idempotency_key="continue-tree",
                stopped=False,
            )
    finally:
        continue_committed.set()
    # 扫描同时处理槽位中已有的停止树，截止时间覆盖整批事务而非单个成员。
    await asyncio.wait_for(old_stop, 30)
    async with factory() as db:
        assert not (await db.get(CooperationRuntime, root_id)).stopped
        assert not (await db.scalar(select(Session).where(Session.thread_id == root_id))).queue_paused


async def test_lost_worker_publishes_failure_to_waiting_member(tree, monkeypatch):
    """失联子 Run 的终态必须进入持久 mailbox，使协作等待可恢复。"""
    from datetime import timedelta

    from yuxi.modules.agents.services.leases import reconcile_expired_run_leases

    factory, run_id, root_id, uid = tree
    member = await child(tree)
    monkeypatch.setattr("yuxi.modules.agents.services.leases.reconcile_pending_runtime_cleanups", AsyncMock())
    async with factory() as db:
        _, acquired = await AgentRunRepository(db).mark_running(member["run_id"], worker_id="lost-worker", lease_seconds=120)
        assert acquired
        run = await db.get(AgentRun, member["run_id"])
        run.lease_expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
    assert member["run_id"] in await reconcile_expired_run_leases()
    async with factory() as db:
        updates = await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").wait_updates(
            targets=[member["session_id"]], after_cursor=0, timeout_seconds=300
        )
        assert any(update.get("status") == "failed" for update in updates["updates"]), updates
        assert (await db.get(AgentTurn, member["turn_id"])).status == "failed"


async def test_durable_tree_and_run_binding_reject_wrong_owner(tree):
    """绕过服务直接写入错误树归属和 runtime，数据库必须拒绝。"""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    factory, run_id, root_id, _ = tree
    member = await child(tree)
    async with factory() as db:
        with pytest.raises(IntegrityError, match="fk_agent_runs_session_runtime_scope"):
            async with db.begin_nested():
                await db.execute(
                    text("UPDATE agent_runs SET runtime_scope_id=:child WHERE id=:run"),
                    {"child": member["session_id"], "run": run_id},
                )
        with pytest.raises(IntegrityError, match="fk_sessions_tree_owner|fk_sessions_project_uid"):
            async with db.begin_nested():
                await db.execute(
                    text("UPDATE sessions SET uid='other-owner' WHERE thread_id=:thread"),
                    {"thread": member["session_id"]},
                )
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        assert root.thread_id == root.tree_root_thread_id


async def test_wait_rejects_empty_and_self_targets(tree):
    """自身 waiting 通知不能制造无工作自唤醒循环。"""
    factory, run_id, root_id, uid = tree
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        for targets in [[], [root_id], ["/root"]]:
            with pytest.raises(ValueError, match="目标不能为空|自身"):
                await service.wait_updates(targets=targets, after_cursor=0, timeout_seconds=1)
        assert (
            await db.scalar(select(func.count()).select_from(CooperationEvent).where(CooperationEvent.tree_root_thread_id == root_id)) == 0
        )


async def test_stopped_member_at_model_boundary_requests_cancellation(tree, monkeypatch):
    """停止意图与逐成员取消之间的执行边界不能落为普通失败。"""
    from yuxi.modules.agents.models.cooperation import CooperationRuntime

    factory, run_id, root_id, uid = tree
    monkeypatch.setattr("yuxi.modules.agents.services.turns.publish_cancel_signals", AsyncMock())
    async with factory() as db:
        (await db.get(CooperationRuntime, root_id)).stopped = True
        await db.commit()
        with pytest.raises(asyncio.CancelledError, match="协作树已停止"):
            await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").list_sessions()
    async with factory() as db:
        run = await db.get(AgentRun, run_id)
        turn = await db.get(AgentTurn, run.turn_id)
        member = await db.scalar(select(Session).where(Session.thread_id == root_id))
        assert run.status == "cancel_requested" and run.worker_id == "root-worker"
        assert turn.status == "cancelling" and not member.queue_paused


async def test_tree_stop_cancels_released_retry_without_lease_failure(tree, monkeypatch):
    """合法释放的重试 Run 应按取消收敛，并且继续队列不能复活它。"""
    from yuxi.modules.agents.services.cooperation import control_tree
    from yuxi.modules.agents.services.leases import reconcile_pending_runtime_cleanups
    from yuxi.modules.agents.services.scheduler import Dispatch

    monkeypatch.setattr("yuxi.modules.agents.services.turns.publish_cancel_signals", AsyncMock())
    delivery = AsyncMock()
    monkeypatch.setattr("yuxi.modules.agents.services.scheduler.deliver", delivery)
    factory, run_id, root_id, uid = tree
    async with factory() as db:
        queued = await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").submit_input(
            target=root_id, description="queued after retry", call_id="queue-before-retry"
        )
        old_turn_id = (await db.get(AgentRun, run_id)).turn_id
        assert queued["status"] == "queued"
        assert await AgentRunRepository(db).release_lease_for_retry(run_id, worker_id="root-worker")
        await db.commit()
    async with factory() as db:
        await control_tree(
            db=db,
            scope=ActorScope(uid=uid, app_id=None),
            thread_id=root_id,
            idempotency_key="stop-released-retry",
            stopped=True,
        )
    await reconcile_pending_runtime_cleanups()
    async with factory() as db:
        cancelled = await db.get(AgentRun, run_id)
        assert cancelled.status == "cancelled"
        assert cancelled.error_type == "cancelled"
        assert cancelled.worker_id is None and cancelled.lease_expires_at is None
        assert not cancelled.runtime_cleanup_pending
        assert (await db.get(AgentTurn, old_turn_id)).status == "cancelled"
        assert (await db.get(AgentInput, queued["input_id"])).status == "pending"
        await control_tree(
            db=db,
            scope=ActorScope(uid=uid, app_id=None),
            thread_id=root_id,
            idempotency_key="continue-released-retry",
            stopped=False,
        )
    async with factory() as db:
        assert (await db.get(AgentRun, run_id)).status == "cancelled"
        assert (await db.get(AgentTurn, old_turn_id)).status == "cancelled"
        following = await db.get(AgentInput, queued["input_id"])
        assert following.status == "consumed"
        await db.refresh(following)
        next_run = await db.get(AgentRun, following.consumed_run_id)
        assert next_run is not None and next_run.turn_id != old_turn_id
        delivery.assert_awaited_once()
        assert isinstance(delivery.await_args.args[0], Dispatch)


@pytest.mark.parametrize("operation", ["message", "input", "cancel"])
async def test_resume_call_cannot_change_target_session(tree, monkeypatch, operation):
    """恢复 Run 重放同一工具调用时，换目标不能产生第二份消息、输入或取消。"""
    from datetime import timedelta

    from yuxi.shared.datetime import format_utc_datetime

    monkeypatch.setattr("yuxi.modules.agents.services.turns.publish_cancel_signals", AsyncMock())
    factory, run_id, root_id, uid = tree
    first = await child(tree)
    second = await child(tree, name="second", call_id="create-second")

    async def invoke(service, target):
        """各操作使用同一来源调用身份及相同正文。"""
        if operation == "message":
            return await service.send_message(target=target["session_id"], content="same", call_id="replayed-call")
        if operation == "input":
            return await service.submit_input(target=target["session_id"], description="same", call_id="replayed-call")
        return await service.cancel_turn(target=target["session_id"], turn_id=target["turn_id"], call_id="replayed-call")

    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        original = await invoke(service, first)
        await db.commit()
        await settle_run(
            db=db,
            run=await db.get(AgentRun, run_id),
            worker_id="root-worker",
            status="interrupted",
            token_usage=None,
            waitpoint={
                "id": "replay-target-wait",
                "run_id": run_id,
                "kind": "cooperation",
                "waits": [
                    {
                        "interrupt_id": "replay-target-wait",
                        "target_sessions": [first["session_id"]],
                        "after_cursor": 0,
                        "deadline": format_utc_datetime(utc_now() - timedelta(seconds=1)),
                    }
                ],
            },
        )
        old_turn_id = (await db.get(AgentRun, run_id)).turn_id
        await db.commit()
    await recover_cooperation_waits()
    async with factory() as db:
        turn = await db.get(AgentTurn, old_turn_id)
        assert turn.current_run_id != run_id
        resumed_id = turn.current_run_id
        await complete_run_runtime_cleanup(await db.get(AgentRun, run_id))
        _, acquired = await AgentRunRepository(db).mark_running(resumed_id, worker_id="resume-worker", lease_seconds=120)
        assert acquired
        await db.commit()
        service = SessionCooperationService(db, run_id=resumed_id, uid=uid, worker_id="resume-worker")
        assert await invoke(service, first) == original
        await db.commit()
        inputs_before = await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.uid == uid))
        events_before = await db.scalar(
            select(func.count()).select_from(CooperationEvent).where(CooperationEvent.tree_root_thread_id == root_id)
        )
        with pytest.raises(ValueError, match="目标|幂等"):
            await invoke(service, second)
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.uid == uid)) == inputs_before
        assert (
            await db.scalar(select(func.count()).select_from(CooperationEvent).where(CooperationEvent.tree_root_thread_id == root_id))
            == events_before
        )
        assert (await db.get(AgentTurn, second["turn_id"])).status == "running"
        assert (await db.get(AgentRun, second["run_id"])).status == "pending"


async def test_child_status_notification_preserves_self_and_parent_recipients(tree):
    """同一次子轮次状态分别进入自身和父收件箱，重复通知仍各只有一条。"""
    from yuxi.modules.agents.services.cooperation_notifications import notify_turn_state

    factory, _, root_id, _ = tree
    member = await child(tree)
    async with factory() as db:
        run = await db.get(AgentRun, member["run_id"])
        turn = await db.get(AgentTurn, member["turn_id"])
        turn.status = "waiting"
        turn.waitpoint = {"kind": "approval"}
        await db.flush()
        await notify_turn_state(db, run)
        await notify_turn_state(db, run)
        await db.commit()
    async with factory() as db:
        events = list(
            (
                await db.scalars(
                    select(CooperationEvent).where(
                        CooperationEvent.source_run_id == member["run_id"], CooperationEvent.kind == "turn_status"
                    )
                )
            ).all()
        )
        assert len(events) == 2
        assert {event.recipient_thread_id for event in events} == {root_id, member["session_id"]}
        assert all(event.payload["waiting_for"] == "approval" and event.turn_id == member["turn_id"] for event in events)


async def test_cancelling_last_pending_run_keeps_tree_stop_state(tree):
    """取消 pending Run 持久化终态，不登记协作树沙盒回收计时。"""
    from yuxi.modules.agents.models.cooperation import CooperationRuntime
    from yuxi.modules.agents.services.leases import complete_run_runtime_cleanup
    from yuxi.modules.agents.services.turns import cancel_turn

    factory, run_id, root_id, uid = tree
    member = await child(tree)
    async with factory() as db:
        root_run = await db.get(AgentRun, run_id)
        await settle_run(db=db, run=root_run, worker_id="root-worker", status="failed", token_usage=None)
        await db.commit()
    await complete_run_runtime_cleanup(root_run)
    async with factory() as db:
        await cancel_turn(
            db=db,
            scope=ActorScope(uid=uid, app_id=None),
            thread_id=member["session_id"],
            turn_id=member["turn_id"],
            idempotency_key="cancel-last-pending",
        )
    async with factory() as db:
        assert (await db.get(AgentRun, member["run_id"])).status == "cancelled"
        assert (await db.get(AgentTurn, member["turn_id"])).status == "cancelled"
        assert (await db.get(CooperationRuntime, root_id)).stopped is False


async def complete_member(factory, member, content):
    """持久化精确输出，供跨轮任务观察断言使用。"""
    from yuxi.modules.agents.repositories.turn import AgentTurnRepository

    async with factory() as db:
        repo = AgentRunRepository(db)
        run, acquired = await repo.mark_running(member["run_id"], worker_id="result-worker", lease_seconds=120)
        assert acquired
        output = Message(
            session_record_id=run.session_record_id,
            role="assistant",
            content=content,
            message_type="text",
            turn_id=run.turn_id,
            run_id=run.id,
        )
        db.add(output)
        await db.flush()
        run.output_message_id = output.id
        await repo.set_terminal_status(run.id, status="completed", worker_id="result-worker")
        run.runtime_cleanup_pending = False
        await AgentTurnRepository(db).set_terminal(await db.get(AgentTurn, run.turn_id), status="completed", result_run_id=run.id)
        await db.commit()


async def test_exact_result_survives_next_turn_and_queued_input_keeps_identity(tree):
    """A 完成且 B 已派发时，按 A 身份仍能读取 A 的输出。"""
    from yuxi.modules.agents.services.scheduler import claim_next_input

    factory, run_id, _, uid = tree
    member = await child(tree)
    async with factory() as db:
        queued = await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").submit_input(
            target=member["session_id"], description="B", call_id="submit-B"
        )
        assert queued["turn_id"] is None
    await complete_member(factory, member, "EXACT_A")
    async with factory() as db:
        session = await SessionRepository(db).lock_session_by_thread_id(member["session_id"])
        dispatch = await claim_next_input(db=db, agent_session=session)
        await db.commit()
        assert dispatch is not None
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        queries = []

        def record(_conn, _cursor, statement, _params, _context, _many):
            """只记录精确任务查询实际选择的列。"""
            if "FROM sessions" in statement and "JOIN agent_inputs" in statement:
                queries.append(statement.partition("FROM")[0])

        connection = (await db.connection()).sync_connection
        event.listen(connection, "before_cursor_execute", record)
        try:
            a = await service.get_result(input_id=member["input_id"])
            assert a == await service.get_result(turn_id=member["turn_id"])
        finally:
            event.remove(connection, "before_cursor_execute", record)
        assert len(queries) == 2
        assert all(
            field not in query
            for query in queries
            for field in ("agent_inputs.messages", "agent_inputs.input_payload", "agent_inputs.config_snapshot", "agent_runs.input_payload")
        )
        assert a["output"] == "EXACT_A" and a["terminal"] and a["result_run_id"] == member["run_id"]
        b = await service.get_result(input_id=queued["input_id"])
        assert b["turn_id"] != a["turn_id"] and not b["terminal"] and b["output"] is None
        wait = await service.wait_inputs(input_ids=[member["input_id"]], timeout_seconds=30)
        assert wait["results"] == [a] and not wait["wait_timed_out"]


async def test_input_wait_recovers_after_queued_input_cancelled(tree):
    """无 Turn 的排队 Input 取消后也能唤醒原有持久等待。"""
    from yuxi.modules.agents.services import execution

    factory, run_id, _, uid = tree
    member = await child(tree)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        queued = await service.submit_input(target=member["session_id"], description="queued", call_id="queued")
        wait = await service.wait_inputs(input_ids=[queued["input_id"]], timeout_seconds=300)
        payload = execution.build_pending_interrupt_payload([SimpleNamespace(id="wait-recover", value=wait)], "thread")
        run = await db.get(AgentRun, run_id)
        turn_id = run.turn_id
        await settle_run(
            db=db,
            run=run,
            worker_id="root-worker",
            status="interrupted",
            token_usage=None,
            waitpoint=execution._waitpoint_from_interrupt(payload, run_id),
        )
        await db.commit()
    await recover_cooperation_waits()
    async with factory() as db:
        assert (await db.get(AgentTurn, turn_id)).current_run_id == run_id
        queued_input = await db.get(AgentInput, queued["input_id"])
        queued_input.status = "cancelled"
        queued_input.cancelled_at = utc_now()
        await db.commit()
    await recover_cooperation_waits()
    async with factory() as db:
        resumed = await db.get(AgentRun, (await db.get(AgentTurn, turn_id)).current_run_id)
        assert resumed.id != run_id and resumed.resume_from_run_id == run_id
        message = await db.get(Message, resumed.input_message_id)
        result = message.extra_metadata["resume"]["wait-recover"]["results"][0]
        assert result["input_id"] == queued["input_id"] and result["terminal"]
        assert result["status"] == "cancelled" and result["turn_id"] is None


async def test_task_lookup_and_wait_reject_unknown_scope_and_self(tree):
    """模型不能通过任务 ID 绕过用户与 APP 边界或等待自身。"""
    factory, run_id, _, uid = tree
    member = await child(tree)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        for arguments in ({}, {"input_id": member["input_id"], "turn_id": member["turn_id"]}):
            with pytest.raises(ValueError, match="只能"):
                await service.get_result(**arguments)
        with pytest.raises(ValueError, match="授权范围"):
            await service.wait_inputs(input_ids=[member["input_id"], "missing"], timeout_seconds=30)
        _, acquired = await AgentRunRepository(db).mark_running(member["run_id"], worker_id="self", lease_seconds=120)
        assert acquired
        with pytest.raises(ValueError, match="自身"):
            await SessionCooperationService(db, run_id=member["run_id"], uid=uid, worker_id="self").wait_inputs(
                input_ids=[member["input_id"]], timeout_seconds=30
            )
        target = await db.scalar(select(Session).where(Session.thread_id == member["session_id"]))
        target.app_id = "another-app"
        await db.flush()
        with pytest.raises(ValueError, match="授权范围"):
            await service.get_result(input_id=member["input_id"])
        await db.rollback()


async def test_summary_reads_whole_tree_with_constant_queries_and_omits_results(tree):
    """超过原分页上限的成员一次完整返回，批量查询不包含正文。"""
    from sqlalchemy import event

    factory, _, root_id, _ = tree
    members = [await child(tree, name=f"summary{i}", call_id=f"summary{i}") for i in range(6)]
    await complete_member(factory, members[0], "PRIVATE_LONG_RESULT")
    async with factory() as db:
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        queries = []

        def record(_conn, _cursor, statement, _parameters, _context, _many):
            """记录真实 SQL 往返，独立于 repository 实现。"""
            queries.append(statement)

        engine = factory.kw["bind"].sync_engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            first = await tree_snapshot(db, root)
            assert len(first["sessions"]) == 7 and len(queries) == 4
            db.add_all(
                Session(
                    thread_id=str(uuid.uuid4()),
                    tree_root_thread_id=root_id,
                    parent_thread_id=root_id,
                    uid=root.uid,
                    app_id=root.app_id,
                    project_id=root.project_id,
                    agent_id=root.agent_id,
                    cooperation_name=f"extra{i}",
                    cooperation_path=f"/root/extra{i}",
                )
                for i in range(100)
            )
            await db.commit()
            expected = list(
                (
                    await db.scalars(
                        select(Session.thread_id).where(Session.tree_root_thread_id == root_id).order_by(Session.cooperation_path)
                    )
                ).all()
            )
            queries.clear()
            whole = await tree_snapshot(db, root)
            assert len(queries) == 4
            ids = [item["session_id"] for item in whole["sessions"]]
            assert ids == expected
            assert len(ids) == len(set(ids)) == 107
            assert "next_cursor" not in whole
            assert all("output" not in item for item in whole["sessions"])
        finally:
            event.remove(engine, "before_cursor_execute", record)


@pytest.mark.parametrize("lock_owner", ["dispatch", "configuration", "continue", "run_owner", "run_terminal", "turn"])
async def test_message_foreign_keys_do_not_deadlock_session_writers(tree, monkeypatch, lock_owner):
    """树通知与会话写入并发时，外键键共享锁不得形成反向等待。"""
    from sqlalchemy import text

    from yuxi.modules.agents.repositories.turn import AgentTurnRepository
    from yuxi.modules.agents.services.cooperation import control_tree

    factory, run_id, root_id, uid = tree
    async with factory() as db:
        run = await db.get(AgentRun, run_id)
        await AgentRunRepository(db).set_terminal_status(run_id, status="failed", worker_id="root-worker")
        await AgentTurnRepository(db).set_terminal(await db.get(AgentTurn, run.turn_id), status="failed")
        await db.commit()
    await complete_run_runtime_cleanup(run)
    monkeypatch.setattr("yuxi.modules.agents.services.scheduler.dispatch_next_input", AsyncMock())
    locked = asyncio.Event()
    tree_lock = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
    key = {"key": f"cooperation:{root_id}"}

    async def writer():
        """在真实行锁已领取后申请树锁，固定竞争顺序。"""
        async with factory() as db:
            repo = SessionRepository(db)
            if lock_owner == "continue":
                original = db.scalars

                async def scalars(statement, *args, **kwargs):
                    """只观察整树控制实际完成的行锁查询。"""
                    result = await original(statement, *args, **kwargs)
                    if statement._for_update_arg is not None:
                        locked.set()
                    return result

                monkeypatch.setattr(db, "scalars", scalars)
                await control_tree(
                    db=db,
                    scope=ActorScope(uid=uid, app_id=None),
                    thread_id=root_id,
                    idempotency_key=str(uuid.uuid4()),
                    stopped=False,
                )
            else:
                if lock_owner == "dispatch":
                    await repo.lock_session_by_thread_id(root_id)
                elif lock_owner == "configuration":
                    root = await repo.lock_session_by_thread_id(root_id)
                    root.config_snapshot = {**root.config_snapshot, "system_prompt": "UPDATED"}
                    await db.flush()
                elif lock_owner == "run_owner":
                    await AgentRunRepository(db).lock_run_for_user(run_id, uid)
                elif lock_owner == "run_terminal":
                    await AgentRunRepository(db)._lock_run(run_id)
                else:
                    run = await db.get(AgentRun, run_id)
                    await AgentTurnRepository(db).get_for_scope(
                        turn_id=run.turn_id, thread_id=root_id, uid=uid, app_id=None, for_update=True
                    )
                locked.set()
                await db.execute(tree_lock, key)
                await db.commit()

    async with factory() as db:
        root = await db.scalar(select(Session).where(Session.thread_id == root_id))
        await db.execute(tree_lock, key)
        task = asyncio.create_task(writer())
        try:
            await asyncio.wait_for(locked.wait(), 3)
            await CooperationRepository(db).append(
                sender=root,
                recipient=root,
                key=f"lock-{lock_owner}",
                kind="message",
                content="available",
                run_id=run_id,
                turn_id=run.turn_id,
            )
            await db.commit()
            await asyncio.wait_for(task, 3)
        finally:
            await db.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with factory() as db:
        assert await db.scalar(select(CooperationEvent.id).where(CooperationEvent.idempotency_key == f"lock-{lock_owner}"))


async def test_bad_wait_does_not_block_other_wait_recovery(tree):
    """首个等待点损坏时，后续等待仍提交下一 Run，整轮仍报告失败。"""
    factory, root_run_id, _, _ = tree
    member = await child(tree)
    async with factory() as db:
        await AgentRunRepository(db).mark_running(member["run_id"], worker_id="child-wait", lease_seconds=120)
        runs = [await db.get(AgentRun, root_run_id), await db.get(AgentRun, member["run_id"])]
        runs.sort(key=lambda run: run.turn_id)
        for index, run in enumerate(runs):
            await settle_run(
                db=db,
                run=run,
                worker_id="root-worker" if run.id == root_run_id else "child-wait",
                status="interrupted",
                token_usage=None,
                waitpoint={
                    "id": f"wait-{index}",
                    "run_id": run.id,
                    "kind": "cooperation",
                    "waits": [
                        {
                            "interrupt_id": f"wait-{index}",
                            "target_sessions": [runs[1 - index].thread_id],
                            "after_cursor": 9223372036854775807,
                            "deadline": "invalid" if index == 0 else "2000-01-01T00:00:00+00:00",
                        }
                    ],
                },
            )
        bad_turn, good_turn = runs[0].turn_id, runs[1].turn_id
        bad_run, good_run = runs[0].id, runs[1].id
        await db.commit()
    with pytest.raises(RuntimeError, match="恢复失败：1"):
        await recover_cooperation_waits()
    async with factory() as db:
        assert (await db.get(AgentTurn, bad_turn)).current_run_id == bad_run
        recovered = await db.get(AgentTurn, good_turn)
        assert recovered.current_run_id != good_run and recovered.status == "running"
        new_run = await db.get(AgentRun, recovered.current_run_id)
        message = await db.get(Message, new_run.input_message_id)
        assert message.extra_metadata["resume"]["wait-1"]["wait_timed_out"] is True


async def test_expired_lease_failure_does_not_block_other_runs(tree, monkeypatch):
    """单条失联收敛失败后，另一条 Run 的失败终态仍持久化。"""
    from datetime import timedelta

    from yuxi.modules.agents.services.leases import reconcile_expired_run_leases

    factory, _, _, _ = tree
    bad = await child(tree, name="bad-lease", call_id="bad-lease")
    good = await child(tree, name="good-lease", call_id="good-lease")
    async with factory() as db:
        for member in (bad, good):
            run, acquired = await AgentRunRepository(db).mark_running(member["run_id"], worker_id="expired", lease_seconds=120)
            assert acquired
            run.lease_expires_at = utc_now() - timedelta(seconds=2)
        await db.commit()
    original = AgentRunRepository.reconcile_expired_lease

    async def reconcile(repo, run_id, **kwargs):
        """只注入一个记录的恢复故障。"""
        if run_id == bad["run_id"]:
            raise RuntimeError("single record failure")
        return await original(repo, run_id, **kwargs)

    monkeypatch.setattr(AgentRunRepository, "reconcile_expired_lease", reconcile)
    with pytest.raises(RuntimeError, match="失联执行恢复失败：1"):
        await reconcile_expired_run_leases()
    async with factory() as db:
        assert (await db.get(AgentRun, bad["run_id"])).status == "running"
        assert (await db.get(AgentRun, good["run_id"])).status == "failed"
        assert (await db.get(AgentTurn, good["turn_id"])).status == "failed"


async def test_stopped_member_failure_does_not_block_other_members(tree, monkeypatch):
    """停止某个成员失败时，其他成员仍按已持久停止意图取消。"""
    from yuxi.modules.agents.models.cooperation import CooperationRuntime
    from yuxi.modules.agents.services.cooperation import reconcile_stopped_trees

    factory, _, root_id, _ = tree
    member = await child(tree)
    async with factory() as db:
        (await db.get(CooperationRuntime, root_id)).stopped = True
        await db.commit()
    original = SessionRepository.lock_session_by_thread_id

    async def lock(repo, thread_id):
        """注入根成员的锁定故障。"""
        if thread_id == root_id:
            raise RuntimeError("root unavailable")
        return await original(repo, thread_id)

    monkeypatch.setattr(SessionRepository, "lock_session_by_thread_id", lock)
    with pytest.raises(RuntimeError, match="停止会话恢复失败：1"):
        await reconcile_stopped_trees()
    async with factory() as db:
        assert (await db.get(AgentTurn, member["turn_id"])).status == "cancelled"
        assert (await db.get(AgentRun, member["run_id"])).status == "cancelled"


async def _define_target_agent(tree, *, suffix="target", visibility="private", owner=None, shared_uid=None):
    """建立提示词、工具与模型均不同的独立配置，由树 fixture 清理。"""
    factory, run_id, _, uid = tree
    async with factory() as db:
        run = await db.get(AgentRun, run_id)
        slug = f"{run.agent_slug}-{suffix}"
        db.add(
            Agent(
                slug=slug,
                name="独立核验角色",
                description="核验并交付证据",
                backend_id="ChatbotAgent",
                visibility=visibility,
                created_by=owner or uid,
                share_config={
                    "version": 2,
                    "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [shared_uid or uid]},
                    "manage_scope": None,
                },
                config_json={
                    "context": {
                        "system_prompt": "TARGET_CONFIG",
                        "tools": ["ask_user_question"],
                        "model": "test:target",
                    }
                },
            )
        )
        await db.commit()
    return slug


@pytest.mark.parametrize("maximum", [0, 1, 2])
async def test_depth_limit_rejects_creation_without_session_or_input_side_effects(tree, monkeypatch, maximum):
    """根深度为零，允许边界内派发且超限不能留下会话或输入。"""
    monkeypatch.setattr(
        "yuxi.modules.agents.services.cooperation.system_options",
        SimpleNamespace(get=AsyncMock(return_value={"max_cooperation_depth": maximum})),
        raising=False,
    )
    factory, run_id, root_id, uid = tree
    worker_id = "root-worker"
    for level in range(maximum):
        async with factory() as db:
            created = await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id=worker_id).create_session(
                name=f"level-{level}", description="有界协作", call_id=f"create-{level}"
            )
            run_id = created["run_id"]
            worker_id = f"depth-{level}"
            _, acquired = await AgentRunRepository(db).mark_running(run_id, worker_id=worker_id, lease_seconds=120)
            assert acquired
            await db.commit()
    async with factory() as db:
        before = await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.uid == uid))
    with pytest.raises(DomainError, match="协作深度") as rejected:
        async with factory() as db:
            await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id=worker_id).create_session(
                name="too-deep", description="应被拒绝", call_id="too-deep"
            )
    assert rejected.value.code == "cooperation_depth_exceeded"
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Session).where(Session.tree_root_thread_id == root_id)) == maximum + 1
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.uid == uid)) == before


async def test_existing_creation_replays_after_depth_limit_is_lowered(tree, monkeypatch):
    """降低深度只限制新副作用，不破坏已经提交的幂等回执。"""
    created = await child(tree)
    monkeypatch.setattr(
        "yuxi.modules.agents.services.cooperation.system_options",
        SimpleNamespace(get=AsyncMock(return_value={"max_cooperation_depth": 0})),
        raising=False,
    )
    assert await child(tree) == created


async def test_accept_rejects_late_submission_committed_during_stop(tree, monkeypatch):
    """入口检查与持久写入之间提交的整树停止，在写入边界拒绝迟到的提交。"""
    from yuxi.modules.agents.services.cooperation import control_tree

    factory, run_id, root_id, uid = tree
    member = await child(tree)
    real_lock = SessionRepository.lock_session_by_thread_id

    async def stop_tree_then_lock(self, thread_id):
        """模拟停止扫描在接收入口检查后、行锁领取前提交。"""
        if thread_id == member["session_id"]:
            monkeypatch.setattr(SessionRepository, "lock_session_by_thread_id", real_lock)
            async with factory() as db:
                await control_tree(
                    db=db,
                    scope=ActorScope(uid=uid, app_id=None),
                    thread_id=root_id,
                    idempotency_key="stop-during-submit",
                    stopped=True,
                )
        return await real_lock(self, thread_id)

    monkeypatch.setattr(SessionRepository, "lock_session_by_thread_id", stop_tree_then_lock)
    async with factory() as db:
        with pytest.raises(StateConflictError, match="协作树已停止") as error:
            await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").submit_input(
                target=member["session_id"], description="LATE", call_id="late"
            )
        assert isinstance(error.value, StateConflictError)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == member["session_id"])) == 1
        from yuxi.modules.agents.models.cooperation import CooperationRuntime

        assert (await db.get(CooperationRuntime, root_id)).stopped is True


async def test_accept_rejects_old_run_after_stop_cleanup_and_user_restart(tree, monkeypatch):
    """停止已结束且新用户工作恢复整树时，旧工具调用仍不能写入。"""
    from yuxi.modules.agents.input_messages import build_chat_input_message
    from yuxi.modules.agents.models.cooperation import CooperationRuntime
    from yuxi.modules.agents.services.cooperation import control_tree
    from yuxi.modules.agents.services.inputs import accept_message

    factory, run_id, root_id, uid = tree
    member = await child(tree)
    real_lock = SessionRepository.lock_session_by_thread_id
    monkeypatch.setattr("yuxi.modules.agents.services.turns.publish_cancel_signals", AsyncMock())

    async def restart_tree_then_lock(self, thread_id):
        """在旧工具入口检查后，完成停止清理和下一次用户接收。"""
        if thread_id == member["session_id"]:
            monkeypatch.setattr(SessionRepository, "lock_session_by_thread_id", real_lock)
            async with factory() as db:
                await control_tree(
                    db=db,
                    scope=ActorScope(uid=uid, app_id=None),
                    thread_id=root_id,
                    idempotency_key="stop-before-restart",
                    stopped=True,
                )
                old_run = await db.get(AgentRun, run_id)
                await settle_run(db=db, run=old_run, worker_id="root-worker", status="cancelled", token_usage=None)
                await db.commit()
                await complete_run_runtime_cleanup(old_run)
            async with factory() as db:
                await accept_message(
                    db=db,
                    scope=ActorScope(uid=uid, app_id=None),
                    thread_id=root_id,
                    idempotency_key="restart-root",
                    mode=None,
                    messages=[build_chat_input_message("NEW_ROOT_WORK")],
                )
        return await real_lock(self, thread_id)

    monkeypatch.setattr(SessionRepository, "lock_session_by_thread_id", restart_tree_then_lock)
    async with factory() as db:
        with pytest.raises(StateConflictError, match="来源 Run 已失效"):
            await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").submit_input(
                target=member["session_id"], description="STALE_TOOL_WORK", call_id="late-after-restart"
            )
    async with factory() as db:
        assert (await db.get(CooperationRuntime, root_id)).stopped is False
        assert (await db.get(AgentRun, run_id)).status == "cancelled"
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == member["session_id"])) == 1


async def test_stop_waits_for_inflight_child_creation_and_pauses_new_member(tree, monkeypatch):
    """新成员未提交时停止等待父行锁，提交后扫描仍覆盖该新成员。"""
    from sqlalchemy import text

    from yuxi.modules.agents.models.cooperation import CooperationRuntime
    from yuxi.modules.agents.services.cooperation import control_tree, dispatch_next_input

    factory, run_id, root_id, uid = tree
    accepting, release_accept, stop_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    stop_pid = asyncio.get_running_loop().create_future()
    original_accept = SessionCooperationService._accept
    monkeypatch.setattr("yuxi.modules.agents.services.turns.publish_cancel_signals", AsyncMock())

    async def pause_before_accept(self, **kwargs):
        """在父锁已领取且子成员已 flush 的边界让出执行。"""
        accepting.set()
        await release_accept.wait()
        return await original_accept(self, **kwargs)

    async def dispatch_after_stop(**kwargs):
        """让接收先提交，停止先完成，再验证调度门禁。"""
        await stop_finished.wait()
        return await dispatch_next_input(**kwargs)

    async def stop():
        """在独立数据库连接发起真实整树控制。"""
        async with factory() as db:
            stop_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
            try:
                return await control_tree(
                    db=db,
                    scope=ActorScope(uid=uid, app_id=None),
                    thread_id=root_id,
                    idempotency_key="stop-racing-create",
                    stopped=True,
                )
            finally:
                stop_finished.set()

    monkeypatch.setattr(SessionCooperationService, "_accept", pause_before_accept)
    monkeypatch.setattr("yuxi.modules.agents.services.cooperation.dispatch_next_input", dispatch_after_stop)
    creating = asyncio.create_task(child(tree, name="racing", call_id="racing-create"))
    stopping = None
    try:
        await asyncio.wait_for(accepting.wait(), 3)
        stopping = asyncio.create_task(stop())
        pid = await asyncio.wait_for(stop_pid, 3)
        async with asyncio.timeout(3):
            async with factory() as db:
                while not await db.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}):
                    await asyncio.sleep(0.01)
        release_accept.set()
        created, _ = await asyncio.wait_for(asyncio.gather(creating, stopping), 5)
    finally:
        release_accept.set()
        for task in (creating, stopping):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (creating, stopping) if task is not None), return_exceptions=True)
    async with factory() as db:
        member = await db.scalar(select(Session).where(Session.thread_id == created["session_id"]))
        item = await db.get(AgentInput, created["input_id"])
        assert (await db.get(CooperationRuntime, root_id)).stopped is True
        assert member.queue_paused and item.status == "pending" and item.turn_id is None
        assert await db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.thread_id == member.thread_id)) == 0


async def test_parallel_cooperation_waits_resume_with_interrupt_id_mapping(tree):
    """同轮多个协作等待全部就绪后，按中断 ID 一次恢复并送达各自结果。"""
    from datetime import timedelta

    from yuxi.shared.datetime import format_utc_datetime

    factory, run_id, _, uid = tree
    member = await child(tree)
    await complete_member(factory, member, "PARALLEL_DONE")
    async with factory() as db:
        sender = await db.scalar(select(Session).where(Session.thread_id == member["session_id"]))
        recipient = await db.scalar(select(Session).where(Session.thread_id == tree[2]))
        await CooperationRepository(db).append(sender=sender, recipient=recipient, key="parallel-wake", kind="message", content="ready")
        deadline = format_utc_datetime(utc_now() + timedelta(seconds=300))
        await settle_run(
            db=db,
            run=await db.get(AgentRun, run_id),
            worker_id="root-worker",
            status="interrupted",
            token_usage=None,
            waitpoint={
                "id": "wait-parallel",
                "run_id": run_id,
                "kind": "cooperation",
                "waits": [
                    {"interrupt_id": "interrupt-inputs", "target_inputs": [member["input_id"]], "deadline": deadline},
                    {
                        "interrupt_id": "interrupt-sessions",
                        "target_sessions": [member["session_id"]],
                        "after_cursor": 0,
                        "deadline": deadline,
                    },
                ],
            },
        )
        await db.commit()
    await recover_cooperation_waits()
    async with factory() as db:
        turn = await db.get(AgentTurn, (await db.get(AgentRun, run_id)).turn_id)
        resumed = await db.get(AgentRun, turn.current_run_id)
        assert resumed.run_type == "resume" and resumed.resume_from_run_id == run_id
        message = await db.get(Message, resumed.input_message_id)
        resume = message.extra_metadata["resume"]
        assert set(resume) == {"interrupt-inputs", "interrupt-sessions"}
        inputs_wait = resume["interrupt-inputs"]
        assert inputs_wait["wait_timed_out"] is False
        assert [item["input_id"] for item in inputs_wait["results"]] == [member["input_id"]]
        assert all(item["terminal"] for item in inputs_wait["results"])
        assert resume["interrupt-sessions"]["updates"]


@pytest.mark.parametrize("completed", [True, False], ids=["ready", "timed-out"])
async def test_parallel_wait_resumes_settled_entry_without_delaying_its_deadline(tree, completed):
    """已完成或超时的等待立即按 ID 返回，其余挂起点交由 checkpoint 保留。"""
    from datetime import timedelta

    from yuxi.shared.datetime import format_utc_datetime

    factory, run_id, _, _uid = tree
    member = await child(tree)
    if completed:
        await complete_member(factory, member, "ONE_DONE")
    async with factory() as db:
        deadline = format_utc_datetime(utc_now() + timedelta(seconds=300))
        input_deadline = deadline if completed else format_utc_datetime(utc_now() - timedelta(seconds=1))
        await settle_run(
            db=db,
            run=await db.get(AgentRun, run_id),
            worker_id="root-worker",
            status="interrupted",
            token_usage=None,
            waitpoint={
                "id": "wait-hold",
                "run_id": run_id,
                "kind": "cooperation",
                "waits": [
                    {"interrupt_id": "interrupt-inputs", "target_inputs": [member["input_id"]], "deadline": input_deadline},
                    {
                        "interrupt_id": "interrupt-sessions",
                        "target_sessions": [member["session_id"]],
                        "after_cursor": 0,
                        "deadline": deadline,
                    },
                ],
            },
        )
        await db.commit()
    await recover_cooperation_waits()
    async with factory() as db:
        turn = await db.get(AgentTurn, (await db.get(AgentRun, run_id)).turn_id)
        assert turn.status == "running" and turn.current_run_id != run_id
        resumed = await db.get(AgentRun, turn.current_run_id)
        response = (await db.get(Message, resumed.input_message_id)).extra_metadata["resume"]
        assert set(response) == {"interrupt-inputs"}
        assert response["interrupt-inputs"]["wait_timed_out"] is not completed
        assert response["interrupt-inputs"]["results"][0]["terminal"] is completed


async def _retry_root_with_new_owner(tree):
    """通过真实 retry 释放、运行时清理和重新领取切换执行身份。"""
    factory, run_id, _, _ = tree
    async with factory() as db:
        run = await db.get(AgentRun, run_id)
        assert await AgentRunRepository(db).release_lease_for_retry(run_id, worker_id="root-worker")
        await db.commit()
        await complete_run_runtime_cleanup(run)
    async with factory() as db:
        _, acquired = await AgentRunRepository(db).mark_running(run_id, worker_id="retry-worker", lease_seconds=120)
        assert acquired
        await db.commit()


@pytest.mark.parametrize("operation", ["create_session", "submit_input", "send_message"])
async def test_replaced_worker_cannot_create_cooperation_side_effects(tree, operation):
    """相同 Run 的新租约属于新 attempt，旧工具不能借它创建工作或消息。"""
    factory, run_id, root_id, uid = tree
    member = await child(tree)
    await _retry_root_with_new_owner(tree)
    async with factory() as db:
        sessions_before = await db.scalar(select(func.count()).select_from(Session).where(Session.uid == uid))
        inputs_before = await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.uid == uid))
        events_before = await db.scalar(
            select(func.count()).select_from(CooperationEvent).where(CooperationEvent.tree_root_thread_id == root_id)
        )
        stale = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        arguments = {
            "create_session": {"name": "late", "description": "STALE_TASK"},
            "submit_input": {"target": member["session_id"], "description": "STALE_TASK"},
            "send_message": {"target": member["session_id"], "content": "STALE_MESSAGE"},
        }[operation]
        with pytest.raises(ValueError, match="当前 Worker"):
            await getattr(stale, operation)(**arguments, call_id="replaced-attempt")
    async with factory() as db:
        run = await db.get(AgentRun, run_id)
        assert run.status == "running" and run.worker_id == "retry-worker" and run.lease_expires_at > utc_now()
        assert await db.scalar(select(func.count()).select_from(Session).where(Session.uid == uid)) == sessions_before
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.uid == uid)) == inputs_before
        assert (
            await db.scalar(select(func.count()).select_from(CooperationEvent).where(CooperationEvent.tree_root_thread_id == root_id))
            == events_before
        )
        current = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="retry-worker")
        accepted = await getattr(current, operation)(**arguments, call_id="current-attempt")
        await db.commit()
    async with factory() as db:
        if operation == "send_message":
            assert (await db.get(CooperationEvent, accepted["message_id"])).content == "STALE_MESSAGE"
        else:
            assert (await db.get(AgentInput, accepted["input_id"])).status == ("consumed" if operation == "create_session" else "pending")
            if operation == "create_session":
                assert await db.scalar(select(Session.thread_id).where(Session.thread_id == accepted["session_id"]))


@pytest.mark.parametrize("operation", ["create_session", "submit_input"])
async def test_worker_replaced_while_waiting_for_session_lock_cannot_accept_work(tree, monkeypatch, operation):
    """入口校验后换 owner，目标行锁后的写入边界仍拒绝旧 attempt。"""
    factory, run_id, root_id, uid = tree
    member = await child(tree)
    target_id = root_id if operation == "create_session" else member["session_id"]
    lock_session = SessionRepository.lock_session_by_thread_id

    async def replace_owner_then_lock(repository, thread_id):
        """在工具已过入口校验、即将等待 Session 行锁时切换执行者。"""
        if thread_id == target_id:
            monkeypatch.setattr(SessionRepository, "lock_session_by_thread_id", lock_session)
            await _retry_root_with_new_owner(tree)
        return await lock_session(repository, thread_id)

    monkeypatch.setattr(SessionRepository, "lock_session_by_thread_id", replace_owner_then_lock)
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        arguments = (
            {"name": "late", "description": "LATE_TASK"}
            if operation == "create_session"
            else {"target": member["session_id"], "description": "LATE_TASK"}
        )
        with pytest.raises((ValueError, StateConflictError), match="当前 Worker|来源 Run 已失效"):
            await getattr(service, operation)(**arguments, call_id="owner-changed-under-lock")
    async with factory() as db:
        assert (await db.get(AgentRun, run_id)).worker_id == "retry-worker"
        assert await db.scalar(select(func.count()).select_from(Session).where(Session.uid == uid)) == 2
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.uid == uid)) == 2


@pytest.mark.parametrize("transition", ["retry", "cancel"])
async def test_input_accept_holds_source_owner_until_receipt_commit(tree, monkeypatch, transition):
    """来源检查通过后，retry 或取消必须等 Input/Receipt 同事务提交。"""
    from sqlalchemy import text

    from yuxi.modules.agents.models.inputs import AgentInputReceipt

    factory, run_id, _, uid = tree
    member = await child(tree)
    guarded, release = asyncio.Event(), asyncio.Event()
    accepting_pid = asyncio.get_running_loop().create_future()
    changing_pid = asyncio.get_running_loop().create_future()
    original_receipt = CooperationRepository.lock_tool_receipt
    call_id = "accept-before-owner-transition"

    async def hold_guarded_accept(repository, run, target, key):
        """只暂停已锁来源、尚未建立回执的真实接收事务。"""
        if key.endswith(f":{call_id}"):
            accepting_pid.set_result(await repository.db.scalar(text("SELECT pg_backend_pid()")))
            guarded.set()
            await release.wait()
        return await original_receipt(repository, run, target, key)

    async def accept():
        """在独立连接执行提交，保留生产提交边界。"""
        async with factory() as db:
            return await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").submit_input(
                target=member["session_id"], description="COMMITTED_BEFORE_OWNER_CHANGE", call_id=call_id
            )

    async def change_owner():
        """真实转换取锁后，用第三条连接确认接收事实已经可见。"""
        async with factory() as db:
            changing_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
            repo = AgentRunRepository(db)
            if transition == "retry":
                assert await repo.release_lease_for_retry(run_id, worker_id="root-worker")
            else:
                run, _ = await repo.request_cancel_run(run_id=run_id, uid=uid)
                assert run.status == "cancel_requested"
            async with factory() as observing:
                receipt = await observing.scalar(
                    select(AgentInputReceipt).where(AgentInputReceipt.uid == uid, AgentInputReceipt.idempotency_key.endswith(f":{call_id}"))
                )
                assert receipt is not None
                item = await observing.get(AgentInput, receipt.input_id)
                assert item.thread_id == member["session_id"] and item.status == "pending"
                assert item.origin_metadata["created_by_run_id"] == run_id
            await db.commit()
        if transition == "retry":
            await complete_run_runtime_cleanup(await _read_run(factory, run_id))
            async with factory() as db:
                _, acquired = await AgentRunRepository(db).mark_running(run_id, worker_id="retry-worker", lease_seconds=120)
                assert acquired
                await db.commit()

    monkeypatch.setattr(CooperationRepository, "lock_tool_receipt", hold_guarded_accept)
    accepting = asyncio.create_task(accept())
    changing = None
    try:
        await asyncio.wait_for(guarded.wait(), 3)
        changing = asyncio.create_task(change_owner())
        await _wait_for_database_blocker(factory, await asyncio.wait_for(changing_pid, 3), await accepting_pid)
        release.set()
        accepted, _ = await asyncio.wait_for(asyncio.gather(accepting, changing), 5)
    finally:
        release.set()
        await _cancel_pending_tasks(accepting, changing)
    async with factory() as db:
        item = await db.get(AgentInput, accepted["input_id"])
        run = await db.get(AgentRun, run_id)
        assert item.status == "pending" and item.thread_id == member["session_id"]
        assert run.status == ("running" if transition == "retry" else "cancel_requested")
        assert run.worker_id == ("retry-worker" if transition == "retry" else "root-worker")


async def test_message_waiting_for_event_lock_holds_source_owner_until_event_commit(tree, monkeypatch):
    """消息等待事件锁时仍拥有来源锁，retry 不能越过消息事务提交。"""
    from sqlalchemy import text

    factory, run_id, root_id, uid = tree
    member = await child(tree)
    reached_append = asyncio.Event()
    sending_pid = asyncio.get_running_loop().create_future()
    retrying_pid = asyncio.get_running_loop().create_future()
    original_append = CooperationRepository.append

    async def observe_append(repository, **kwargs):
        """保留真实事件锁获取，仅报告消息到达边界。"""
        if kwargs["key"].endswith(":message-before-retry"):
            sending_pid.set_result(await repository.db.scalar(text("SELECT pg_backend_pid()")))
            reached_append.set()
        return await original_append(repository, **kwargs)

    async def send():
        """与生产工具事务一样，在消息落库后提交。"""
        async with factory() as db:
            result = await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").send_message(
                target=member["session_id"], content="BEFORE_RETRY", call_id="message-before-retry"
            )
            await db.commit()
            return result

    async def retry():
        """真实释放和重新领取来源，并独立确认消息先于释放提交。"""
        async with factory() as db:
            retrying_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
            assert await AgentRunRepository(db).release_lease_for_retry(run_id, worker_id="root-worker")
            async with factory() as observing:
                event = await observing.scalar(
                    select(CooperationEvent).where(
                        CooperationEvent.tree_root_thread_id == root_id, CooperationEvent.content == "BEFORE_RETRY"
                    )
                )
                assert event is not None and event.source_run_id == run_id
            await db.commit()
        await complete_run_runtime_cleanup(await _read_run(factory, run_id))
        async with factory() as db:
            _, acquired = await AgentRunRepository(db).mark_running(run_id, worker_id="retry-worker", lease_seconds=120)
            assert acquired
            await db.commit()

    monkeypatch.setattr(CooperationRepository, "append", observe_append)
    sending = retrying = None
    async with factory() as blocker:
        blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        await blocker.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"cooperation-events:{uid}:"})
        try:
            sending = asyncio.create_task(send())
            await asyncio.wait_for(reached_append.wait(), 3)
            message_pid = await sending_pid
            await _wait_for_database_blocker(factory, message_pid, blocker_pid)
            retrying = asyncio.create_task(retry())
            await _wait_for_database_blocker(factory, await asyncio.wait_for(retrying_pid, 3), message_pid)
            await blocker.commit()
            result, _ = await asyncio.wait_for(asyncio.gather(sending, retrying), 5)
        finally:
            await blocker.rollback()
            await _cancel_pending_tasks(sending, retrying)
    async with factory() as db:
        event = await db.get(CooperationEvent, result["message_id"])
        assert event.content == "BEFORE_RETRY" and event.recipient_thread_id == member["session_id"]
        assert (await db.get(AgentRun, run_id)).worker_id == "retry-worker"


async def test_message_rejects_owner_replaced_before_source_lock(tree, monkeypatch):
    """目标解析后、来源锁前换 owner，旧消息不能落库。"""
    factory, run_id, root_id, uid = tree
    member = await child(tree)
    original_lock = AgentRunRepository.lock_run_for_user

    async def replace_before_lock(repository, target_run_id, target_uid):
        """只插入真实 retry 生命周期，不替换来源锁本身。"""
        if target_run_id == run_id:
            monkeypatch.setattr(AgentRunRepository, "lock_run_for_user", original_lock)
            await _retry_root_with_new_owner(tree)
        return await original_lock(repository, target_run_id, target_uid)

    monkeypatch.setattr(AgentRunRepository, "lock_run_for_user", replace_before_lock)
    async with factory() as db:
        with pytest.raises((ValueError, StateConflictError), match="当前 Worker|来源 Run 已失效"):
            await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").send_message(
                target=member["session_id"], content="MUST_NOT_EXIST", call_id="message-after-owner-change"
            )
    async with factory() as db:
        assert (await db.get(AgentRun, run_id)).worker_id == "retry-worker"
        assert not await db.scalar(
            select(CooperationEvent.id).where(CooperationEvent.tree_root_thread_id == root_id, CooperationEvent.content == "MUST_NOT_EXIST")
        )


async def test_cancel_rechecks_source_owner_after_waiting_for_target_turn(tree, monkeypatch):
    """等待目标 Turn 锁期间来源被 retry 接管，旧调用不能取消目标。"""
    from sqlalchemy import text

    from yuxi.modules.agents.models.inputs import AgentInputReceipt

    factory, run_id, _, uid = tree
    member = await child(tree)
    cancelling_pid = asyncio.get_running_loop().create_future()
    monkeypatch.setattr("yuxi.modules.agents.services.turns.publish_cancel_signals", AsyncMock())

    async def cancel():
        """独立连接等待真实目标 Turn 锁。"""
        async with factory() as db:
            cancelling_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
            return await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").cancel_turn(
                target=member["session_id"], turn_id=member["turn_id"], call_id="cancel-after-owner-change"
            )

    cancelling = None
    async with factory() as blocker:
        blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        await blocker.scalar(select(AgentTurn).where(AgentTurn.id == member["turn_id"]).with_for_update(key_share=True))
        try:
            cancelling = asyncio.create_task(cancel())
            await _wait_for_database_blocker(factory, await asyncio.wait_for(cancelling_pid, 3), blocker_pid)
            await asyncio.wait_for(_retry_root_with_new_owner(tree), 5)
            await blocker.commit()
            with pytest.raises((ValueError, StateConflictError), match="当前 Worker|来源 Run 已失效"):
                await asyncio.wait_for(cancelling, 5)
        finally:
            await blocker.rollback()
            await _cancel_pending_tasks(cancelling)
    async with factory() as db:
        assert (await db.get(AgentRun, run_id)).worker_id == "retry-worker"
        assert (await db.get(AgentRun, member["run_id"])).status == "pending"
        assert (await db.get(AgentTurn, member["turn_id"])).status == "running"
        assert not (await db.scalar(select(Session).where(Session.thread_id == member["session_id"]))).queue_paused
        assert not await db.scalar(
            select(AgentInputReceipt.id).where(
                AgentInputReceipt.uid == uid, AgentInputReceipt.idempotency_key.endswith(":cancel-after-owner-change")
            )
        )


async def test_members_cancelling_each_other_use_same_run_lock_order(tree, monkeypatch):
    """双方均已锁目标 Turn 时互相取消，Run 锁序仍收敛且只接受有效来源。"""
    from yuxi.modules.agents.models.inputs import AgentInputReceipt
    from yuxi.modules.agents.repositories.turn import AgentTurnRepository

    factory, run_id, root_id, uid = tree
    member = await child(tree)
    async with factory() as db:
        root_turn_id = (await db.get(AgentRun, run_id)).turn_id
        _, acquired = await AgentRunRepository(db).mark_running(member["run_id"], worker_id="child-worker", lease_seconds=120)
        assert acquired
        await db.commit()
    locked_turn_ids = set()
    both_target_turns_locked = asyncio.Event()
    original_get_turn = AgentTurnRepository.get_for_scope

    async def meet_after_target_turn_lock(repository, **kwargs):
        """确保双方同时持有相反的目标 Turn 锁，再进入真实 Run 锁阶段。"""
        turn = await original_get_turn(repository, **kwargs)
        if kwargs.get("for_update") and turn is not None:
            locked_turn_ids.add(turn.id)
            if len(locked_turn_ids) == 2:
                both_target_turns_locked.set()
            await both_target_turns_locked.wait()
        return turn

    async def cancel(source_run_id, worker_id, target_id, target_turn_id, call_id):
        """各成员通过独立数据库事务执行真实取消。"""
        async with factory() as db:
            return await SessionCooperationService(db, run_id=source_run_id, uid=uid, worker_id=worker_id).cancel_turn(
                target=target_id, turn_id=target_turn_id, call_id=call_id
            )

    monkeypatch.setattr(AgentTurnRepository, "get_for_scope", meet_after_target_turn_lock)
    monkeypatch.setattr("yuxi.modules.agents.services.turns.publish_cancel_signals", AsyncMock())
    cancelling_root = asyncio.create_task(cancel(member["run_id"], "child-worker", root_id, root_turn_id, "child-cancel-root"))
    cancelling_child = asyncio.create_task(cancel(run_id, "root-worker", member["session_id"], member["turn_id"], "root-cancel-child"))
    try:
        results = await asyncio.wait_for(asyncio.gather(cancelling_root, cancelling_child, return_exceptions=True), 6)
    finally:
        both_target_turns_locked.set()
        await _cancel_pending_tasks(cancelling_root, cancelling_child)
    accepted = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, Exception)]
    assert len(accepted) == len(rejected) == 1, results
    assert isinstance(rejected[0], (ValueError, StateConflictError)), rejected
    assert "来源 Run 已失效" in str(rejected[0]) or "当前 Worker" in str(rejected[0])
    async with factory() as db:
        runs = [await db.get(AgentRun, key) for key in (run_id, member["run_id"])]
        turns = [await db.get(AgentTurn, key) for key in (root_turn_id, member["turn_id"])]
        assert sorted(run.status for run in runs) == ["cancel_requested", "running"]
        assert sorted(turn.status for turn in turns) == ["cancelling", "running"]
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AgentInputReceipt)
                .where(
                    AgentInputReceipt.uid == uid,
                    AgentInputReceipt.idempotency_key.endswith(":child-cancel-root")
                    | AgentInputReceipt.idempotency_key.endswith(":root-cancel-child"),
                )
            )
            == 1
        )
        assert accepted[0]["run_id"] == next(run.id for run in runs if run.status == "cancel_requested")


async def _wait_for_database_blocker(factory, waiting_pid, blocking_pid):
    """从独立连接确认 PostgreSQL 的真实阻塞边，而非凭协程时序推断。"""
    from sqlalchemy import text

    try:
        async with asyncio.timeout(3):
            async with factory() as db:
                while not await db.scalar(
                    text("SELECT :blocking_pid = ANY(pg_blocking_pids(:waiting_pid))"),
                    {"waiting_pid": waiting_pid, "blocking_pid": blocking_pid},
                ):
                    await asyncio.sleep(0.01)
    except TimeoutError as error:
        raise AssertionError(f"PostgreSQL 连接 {waiting_pid} 未被持锁连接 {blocking_pid} 阻塞") from error


async def _read_run(factory, run_id):
    """清理入口使用独立回读的已提交 Run。"""
    async with factory() as db:
        return await db.get(AgentRun, run_id)


async def _cancel_pending_tasks(*tasks):
    """失败时释放本例后台协程，避免遗留事务阻塞后续清理。"""
    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
    await asyncio.gather(*(task for task in tasks if task is not None), return_exceptions=True)


async def test_root_creation_inherits_project_and_replays_without_duplicate_work(tree):
    """独立根身份不继承历史，来源与树归属分别保存。"""
    factory, run_id, root_id, uid = tree
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        args = dict(name="independent", description="NEW_ROOT_TASK", call_id="create-new-root", mode="root")
        created = await service.create_session(**args)
        assert await service.create_session(**args) == created
        with pytest.raises(ValueError, match="模式"):
            await service.create_session(**{**args, "mode": "child"})
    async with factory() as db:
        origin = await db.scalar(select(Session).where(Session.thread_id == root_id))
        target = await db.scalar(select(Session).where(Session.thread_id == created["session_id"]))
        assert target.parent_thread_id is None and target.tree_root_thread_id == target.thread_id
        assert target.cooperation_path == "/root" and target.project_id == origin.project_id
        assert target.created_by_run_id == run_id and target.config_snapshot == origin.config_snapshot
        messages = list(await db.scalars(select(Message).where(Message.session_record_id == target.id)))
        assert [message.content for message in messages] == ["NEW_ROOT_TASK"]
        assert await db.scalar(select(func.count()).select_from(AgentInput).where(AgentInput.thread_id == target.thread_id)) == 1
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        listing = await service.list_sessions()
        assert {item["session_id"] for item in listing["sessions"]} == {root_id, target.thread_id}
        assert (await service.require_target(origin, "/root")).thread_id == root_id
        assert (await service.get_result(input_id=created["input_id"]))["session_id"] == target.thread_id
        await service.send_message(target=target.thread_id, content="CROSS_TREE_NOTE", call_id="new-root-note")
        await db.commit()
        events = await CooperationRepository(db).mailbox(target)
        assert any(event.content == "CROSS_TREE_NOTE" and event.sender_thread_id == root_id for event in events)
        extra = await service.submit_input(target=target.thread_id, description="NEXT_WORK", call_id="new-root-next")
        assert extra["input_id"] != created["input_id"] and extra["status"] == "queued"
        await service.cancel_turn(target=target.thread_id, turn_id=created["turn_id"], call_id="cancel-new-root")
    async with factory() as db:
        assert (await db.get(AgentTurn, created["turn_id"])).status == "cancelled"


async def test_cross_tree_input_wait_recovers_exact_result(tree):
    """跨树任务结束后，原等待轮次创建恢复 Run 并绑定精确结果。"""
    factory, run_id, root_id, uid = tree
    async with factory() as db:
        service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
        target = await service.create_session(name="other", description="TASK", call_id="root-wait", mode="root")
        wait = await service.wait_inputs(input_ids=[target["input_id"]], timeout_seconds=30)
        run = await db.get(AgentRun, run_id)
        turn = await db.get(AgentTurn, run.turn_id)
        turn.status = "waiting"
        turn.waitpoint = {"kind": "cooperation", "waits": [{**wait, "interrupt_id": "cross-tree-wait"}]}
        run.status = "interrupted"
        run.worker_id = None
        run.lease_expires_at = None
        await db.commit()
    await complete_member(factory, target, "OTHER_TREE_RESULT")
    await recover_cooperation_waits()
    async with factory() as db:
        turn = await db.get(AgentTurn, turn.id)
        resumed = await db.get(AgentRun, turn.current_run_id)
        assert resumed.id != run_id and resumed.thread_id == root_id
        message = await db.get(Message, resumed.input_message_id)
        result = message.extra_metadata["resume"]["cross-tree-wait"]["results"][0]
        assert result["input_id"] == target["input_id"] and result["output"] == "OTHER_TREE_RESULT"


async def test_nonhuman_origins_use_persisted_input_and_filter_source_scope(tree):
    """来源只由 Input 决定；无权来源不泄露名称或链接。"""
    from yuxi.modules.agents.services.message_origins import get_input_origins, get_session_navigation

    factory, run_id, root_id, uid = tree
    target = await child(tree)
    async with factory() as db:
        scope = ActorScope(uid=uid, app_id=None)
        source = await get_input_origins(db=db, scope=scope, thread_id=target["session_id"], input_ids=[target["input_id"]])
        origin = source["origins"][target["input_id"]]
        assert origin["target"]["session_id"] == root_id and origin["target"]["run_id"] == run_id
        assert origin["target"]["parent_session_id"] is None
        navigation = await get_session_navigation(db=db, scope=scope, thread_id=target["session_id"])
        assert navigation["tree_root_session_id"] == root_id and navigation["parent_session_id"] == root_id
        input_item = await db.get(AgentInput, target["input_id"])
        input_item.source = "public_api"
        source = await get_input_origins(db=db, scope=scope, thread_id=target["session_id"], input_ids=[target["input_id"]])
        assert source["origins"] == {}
        input_item.source = "scheduled_agent"
        source = await get_input_origins(db=db, scope=scope, thread_id=target["session_id"], input_ids=[target["input_id"]])
        assert source["origins"][target["input_id"]] == {"kind": "schedule", "label": "定时任务", "target": None}
        input_item.source = "cooperation"
        parent = await db.scalar(select(Session).where(Session.thread_id == root_id))
        parent.app_id = "hidden-app"
        source = await get_input_origins(db=db, scope=scope, thread_id=target["session_id"], input_ids=[target["input_id"]])
        assert source["origins"][target["input_id"]] == {"kind": "session", "label": "协作会话", "target": None}
        await db.rollback()


async def test_cross_tree_events_publish_in_commit_order(tree):
    """不同树共用可观察游标，后分配事件必须等待先前事务提交。"""
    from sqlalchemy import text
    from yuxi.modules.agents.services.cooperation import read_updates

    factory, run_id, root_id, uid = tree
    async with factory() as db:
        target = await SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker").create_session(
            name="events", description="EVENT_TASK", call_id="root-events", mode="root"
        )
    async with factory() as first:
        root = await first.scalar(select(Session).where(Session.thread_id == root_id))
        other = await first.scalar(select(Session).where(Session.thread_id == target["session_id"]))
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        event = await CooperationRepository(first).append(sender=other, recipient=other, key="first-event", kind="turn_status")
        second_pid = asyncio.get_running_loop().create_future()

        async def publish_second():
            """第二棵树提交自状态事件，实际锁等待由 PostgreSQL 观察。"""
            async with factory() as db:
                second_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
                value = await CooperationRepository(db).append(sender=root, recipient=root, key="second-event", kind="turn_status")
                await db.commit()
                return value.id

        task = asyncio.create_task(publish_second())
        try:
            await _wait_for_database_blocker(factory, await second_pid, first_pid)
            async with factory() as observing:
                before = await read_updates(observing, root, targets=[root_id, other.thread_id], after=0)
                assert before["updates"] == []
            await first.commit()
            second_id = await asyncio.wait_for(task, 5)
        finally:
            await first.rollback()
            await _cancel_pending_tasks(task)
    async with factory() as db:
        result = await read_updates(db, root, targets=[root_id, other.thread_id], after=0)
        assert [item["event_id"] for item in result["updates"]] == [event.id, second_id]
        assert result["cursor"] == second_id


async def test_user_scope_cooperation_rejects_other_users_for_all_target_operations(tree):
    """扩大协作范围不允许通过会话或任务身份越过用户边界。"""
    factory, run_id, _, uid = tree
    foreign_run, foreign_id, _ = await create_agent_run(factory, prefix="scope-outsider", message_content="PRIVATE", input_payload={})
    try:
        async with factory() as db:
            service = SessionCooperationService(db, run_id=run_id, uid=uid, worker_id="root-worker")
            outsider = await db.get(AgentRun, foreign_run)
            for operation, kwargs in [
                ("send_message", {"target": foreign_id, "content": "note", "call_id": "foreign-message"}),
                ("submit_input", {"target": foreign_id, "description": "task", "call_id": "foreign-input"}),
                ("cancel_turn", {"target": foreign_id, "turn_id": outsider.turn_id, "call_id": "foreign-cancel"}),
                ("wait_updates", {"targets": [foreign_id], "after_cursor": 0, "timeout_seconds": 30}),
                ("get_result", {"turn_id": outsider.turn_id}),
            ]:
                with pytest.raises(ValueError, match="授权范围"):
                    await getattr(service, operation)(**kwargs)
            for scope in ("project", "all"):
                assert foreign_id not in {item["session_id"] for item in (await service.list_sessions(scope=scope))["sessions"]}
            assert (
                await db.scalar(
                    select(func.count()).select_from(CooperationEvent).where(CooperationEvent.recipient_thread_id == foreign_id)
                )
                == 0
            )
    finally:
        await cleanup_agent_run_threads(factory, [foreign_id])
