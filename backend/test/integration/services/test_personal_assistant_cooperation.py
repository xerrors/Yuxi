"""个人助理和普通会话的协作范围隔离。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.migrations.schema import create_business_tables
from yuxi.modules.agents.models.inputs import AgentInput
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.agents.repositories.cooperation import CooperationRepository
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.agents.repositories.sessions import SessionRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.cooperation import SessionCooperationService
from yuxi.modules.agents.services.personal_assistants import create_personal_assistant
from yuxi.modules.agents.services.threads import list_session_page, search_threads
from yuxi.modules.identity.models import User
from yuxi.modules.system.models import ConfigOption
from yuxi.modules.system.options import system_options
from yuxi.modules.workspace.models import Project
from yuxi.modules.workspace.repositories.projects import ProjectRepository

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.self_managed_resources,
    pytest.mark.usefixtures("enabled_personal_assistant"),
]


async def test_cooperation_uses_normal_scope_and_hides_personal_from_lists(monkeypatch, tmp_path):
    """助理沿用普通用户与 APP 范围，已知会话可双向通讯，列表隐藏助理。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    schema, admin, engine, manager = await create_isolated_manager("personal_cooperation")
    try:
        await create_business_tables(manager)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add(ConfigOption(key=system_options.key, name=system_options.name, params=system_options.params, value={}))
            db.add(User(uid="person", username="person", password_hash="fixture"))
            await db.commit()
            result = await create_personal_assistant(db, user=SimpleNamespace(uid="person"), name="助手", avatar="😀")
            assistant = await SessionRepository(db).get_session_by_thread_id(result["assistant"]["thread_id"])
            ordinary = await SessionRepository(db).add_session(
                uid="person", agent_id="ordinary", thread_id="regular", project_id=assistant.project_id, config_snapshot={}
            )
            repo = CooperationRepository(db)
            assert await repo.resolve(ordinary, assistant.thread_id) is assistant
            assert await repo.resolve(assistant, ordinary.thread_id) is ordinary
            for caller in (assistant, ordinary):
                assert [row[0].thread_id for row in await repo.summary(caller, all_sessions=True)] == [ordinary.thread_id]
            outsider = Session(thread_id="outsider", uid="another-user", app_id=None)
            other_app = Session(thread_id="other-app", uid="person", app_id="other-app")
            assert await repo.resolve(outsider, assistant.thread_id) is None
            assert await repo.resolve(other_app, assistant.thread_id) is None
            await repo.append(sender=assistant, recipient=ordinary, key="to-task", kind="message", content="任务资料")
            await repo.append(sender=ordinary, recipient=assistant, key="to-assistant", kind="message", content="任务进展")
            assert [item.content for item in await repo.mailbox(ordinary)] == ["任务资料"]
            assert [item.content for item in await repo.mailbox(assistant)] == ["任务进展"]
            with pytest.raises(ValueError, match="同一用户与 APP"):
                await repo.append(sender=outsider, recipient=assistant, key="bad", kind="message", content="无权投递")
            scope = ActorScope(uid="person", app_id=None)
            page, _ = await list_session_page(db=db, scope=scope, agent_slug=assistant.agent_id, after=None, limit=20, order="desc")
            assert page == []
            assert (await search_threads(db=db, scope=scope, query="助手", agent_slug=assistant.agent_id))["items"] == []
            candidates = await ProjectRepository(db).list_history_candidates("person")
            assert assistant.thread_id not in {row.thread_id for row, _ in candidates}
    finally:
        await drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("select_agent", [False, True])
async def test_personal_delegation_creates_root_with_unselected_project_and_target_configuration(monkeypatch, tmp_path, select_agent):
    """助理委派到普通独立根，目录不继承，结果与来源仍可精确查询。"""
    from datetime import timedelta

    from yuxi.modules.agents.models.runs import AgentRun
    from yuxi.modules.agents.services.cooperation import get_cooperation_summary
    from yuxi.shared.datetime import utc_now

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("yuxi.modules.agents.services.cooperation.dispatch_next_input", AsyncMock())
    monkeypatch.setattr(
        "yuxi.modules.agents.services.input_config.model_cache.get_model_info",
        lambda spec: SimpleNamespace(model_type="chat") if spec == "test:chat" else None,
    )
    schema, admin, engine, manager = await create_isolated_manager("personal_child")
    try:
        await create_business_tables(manager)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add(
                ConfigOption(
                    key=system_options.key,
                    name=system_options.name,
                    params=system_options.params,
                    value={"personal_assistant_model": "test:chat"},
                )
            )
            await db.execute(
                text(
                    "INSERT INTO users(username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('person','person','hash','user',0,0)"
                )
            )
            await db.commit()
            result = await create_personal_assistant(db, user=SimpleNamespace(uid="person"), name="助手", avatar="😀")
            main_id = result["assistant"]["thread_id"]
            root = await db.scalar(select(Session).where(Session.thread_id == main_id))
            assert root.tree_root_thread_id == root.thread_id and root.parent_thread_id is None
            root.config_snapshot = {**root.config_snapshot, "system_prompt": "ASSISTANT_PRIVATE_CONFIG"}
            if select_agent:
                user = await db.scalar(select(User).where(User.uid == "person"))
                target_agent = await AgentRepository(db).create(
                    name="执行任务", backend_id="ChatbotAgent", slug="task-agent", created_by="person", creator=user
                )
            else:
                target_agent, _ = await AgentRepository(db).ensure_default_agent()
            target_agent.config_json = {"context": {"system_prompt": "TARGET_AGENT_CONFIG", "model": "test:chat"}}
            target_agent_id = target_agent.slug
            selected_agent_id = target_agent_id if select_agent else None
            turn = AgentTurn(id="parent-turn", thread_id=main_id, uid="person", status="running")
            db.add(turn)
            await db.flush()
            run = AgentRun(
                id="parent-run",
                execution_seq=1,
                thread_id=main_id,
                runtime_scope_id=main_id,
                agent_slug=root.agent_id,
                uid="person",
                turn_id=turn.id,
                session_record_id=root.id,
                input_payload={"context_snapshot": root.config_snapshot},
                status="running",
                run_type="chat",
                worker_id="probe-worker",
                lease_expires_at=utc_now() + timedelta(minutes=5),
            )
            db.add(run)
            await db.flush()
            turn.current_run_id = run.id
            await db.commit()
        async with sessions() as db:
            service = SessionCooperationService(db, run_id="parent-run", uid="person", worker_id="probe-worker")
            good = await service.create_task(name="good", description="成功子任务", call_id="good", agent_id=selected_agent_id)
            bad = await service.create_task(name="bad", description="失败子任务", call_id="failed", agent_id=selected_agent_id)
            for target, status, description in ((good, "completed", "成功子任务"), (bad, "failed", "失败子任务")):
                child = await db.scalar(select(Session).where(Session.thread_id == target["session_id"]))
                assert child.parent_thread_id is None and child.tree_root_thread_id == child.thread_id
                assert child.agent_id == target_agent_id and child.agent_id != root.agent_id
                assert child.project_id != root.project_id
                child_project = await db.get(Project, child.project_id)
                assert child_project.selection_status == "implicit" and child_project.directory_mode == "managed"
                assert child_project.workdir_path != "agents/assistant"
                assert child.config_snapshot["system_prompt"] == "TARGET_AGENT_CONFIG"
                task_input = await db.get(AgentInput, target["input_id"])
                assert task_input.messages == [{"role": "user", "content": [{"type": "text", "text": description}], "metadata": {}}]
                assert task_input.input_payload["context_snapshot"]["system_prompt"] == "TARGET_AGENT_CONFIG"
                child.title = "共同关键字"
                child.is_pinned = True
                db.add(AgentTurn(id=f"turn-{status}", thread_id=child.thread_id, uid="person", status=status))
            await db.commit()
            summary = await get_cooperation_summary(db=db, scope=ActorScope(uid="person", app_id=None), thread_id=main_id)
            projection = {item["name"]: item["turn_status"] for item in summary["sessions"]}
            assert projection == {"root": "running"}
            from yuxi.modules.agents.services.personal_assistants import personal_task_summary

            tasks = await personal_task_summary(db, uid="person")
            assert {item["name"]: item["turn_status"] for item in tasks["sessions"]} == {"good": "completed", "bad": "failed"}
            replay = await service.create_task(name="good", description="成功子任务", call_id="good", agent_id=selected_agent_id)
            assert replay["input_id"] == good["input_id"]
            scope = ActorScope(uid="person", app_id=None)
            page, _ = await list_session_page(db=db, scope=scope, agent_slug=None, after=None, limit=20, order="desc", is_pinned=True)
            assert {item["id"] for item in page} == {good["session_id"], bad["session_id"]}
            assert len((await search_threads(db=db, scope=scope, query="共同关键字"))["items"]) == 2

            # 普通 Agent 仍可创建独立顶层会话。
            user = await db.scalar(select(User).where(User.uid == "person"))
            agent = await AgentRepository(db).create(
                name="普通", backend_id="ChatbotAgent", slug="regular", created_by="person", creator=user
            )
            db.add(
                Project(id="ordinary-project", uid="person", selection_status="implicit", workdir_path="regular", directory_mode="linked")
            )
            await db.flush()
            ordinary = await SessionRepository(db).add_session(
                uid="person",
                agent_id=agent.slug,
                thread_id="ordinary-main",
                project_id="ordinary-project",
                config_snapshot={**root.config_snapshot, "model": "test:chat", "tool_approval_mode": "always_trust"},
            )
            ordinary_turn = AgentTurn(id="ordinary-turn", thread_id=ordinary.thread_id, uid="person", status="running")
            db.add(ordinary_turn)
            await db.flush()
            ordinary_run = AgentRun(
                id="ordinary-run",
                execution_seq=2,
                thread_id=ordinary.thread_id,
                runtime_scope_id=ordinary.thread_id,
                agent_slug=ordinary.agent_id,
                uid="person",
                turn_id=ordinary_turn.id,
                session_record_id=ordinary.id,
                input_payload={"context_snapshot": ordinary.config_snapshot},
                status="running",
                run_type="chat",
                worker_id="probe-worker",
                lease_expires_at=utc_now() + timedelta(minutes=5),
            )
            db.add(ordinary_run)
            await db.flush()
            ordinary_turn.current_run_id = ordinary_run.id
            await db.commit()
            ordinary_service = SessionCooperationService(db, run_id="ordinary-run", uid="person", worker_id="probe-worker")
            independent = await ordinary_service.create_session(
                name="independent", description="普通独立任务", call_id="normal", mode="root"
            )
            independent_session = await db.scalar(select(Session).where(Session.thread_id == independent["session_id"]))
            assert independent_session.parent_thread_id is None
            assert independent_session.tree_root_thread_id == independent_session.thread_id

            # 已授权目标按普通契约创建，Backend 类型不构成额外禁令。
            personal = await ordinary_service.create_task(
                name="personal-target", description="显式目标任务", call_id="personal-target", agent_id=root.agent_id
            )
            target = await db.scalar(select(Session).where(Session.thread_id == personal["session_id"]))
            task_input = await db.get(AgentInput, personal["input_id"])
            assert target.agent_id == root.agent_id and target.parent_thread_id is None
            assert target.project_id != ordinary.project_id
            assert task_input.kind == "follow_up"
            assert task_input.input_payload["context_snapshot"]["model"] == "test:chat"
            assert task_input.input_payload["context_snapshot"]["tool_approval_mode"] == "always_trust"
    finally:
        await drop_isolated_schema(schema, admin, engine)
