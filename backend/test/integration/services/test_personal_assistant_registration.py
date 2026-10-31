"""个人助理的 PostgreSQL 登记、并发及离线升级。"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.infrastructure.postgres.schema import require_current_schema
from yuxi.migrations.main import initialize_schema
from yuxi.migrations.personal_assistants import upgrade
from yuxi.migrations.schema import create_business_tables, create_schema_version_table, record_schema_version
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.personal_assistants import PersonalAssistant
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.agents.services.personal_assistants import create_personal_assistant, update_personal_assistant
from yuxi.modules.system.models import ConfigOption
from yuxi.modules.system.options import system_options
from yuxi.modules.workspace.models import Project
from yuxi.shared.errors import ResourceNotFoundError

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.self_managed_resources,
    pytest.mark.usefixtures("enabled_personal_assistant"),
]


async def test_explicit_upgrade_is_atomic_and_idempotent():
    """旧库保留原有数据，升级及重跑后只发布一次新版本。"""
    schema, admin, engine, manager = await create_isolated_manager("personal_upgrade")
    try:
        await create_business_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 1)
        await record_schema_version(manager, "knowledge", 2)
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE personal_assistants"))
            await conn.execute(
                text(
                    "INSERT INTO users(username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('old','old','hash','user',0,0)"
                )
            )
        with pytest.raises(RuntimeError, match="incomplete or incompatible"):
            await require_current_schema(manager)
        with pytest.raises(RuntimeError, match="Unsupported Yuxi schema"):
            await initialize_schema(manager)
        await upgrade(manager)
        await upgrade(manager)
        await require_current_schema(manager)
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT version FROM yuxi_schema_migrations WHERE domain='business'")) == 2
            assert await conn.scalar(text("SELECT count(*) FROM personal_assistants")) == 0
            assert await conn.scalar(text("SELECT password_hash FROM users WHERE uid='old'")) == "hash"
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_concurrent_creation_reuses_binding_and_guards_general_management(monkeypatch, tmp_path):
    """并发创建只产生一组持久绑定，关闭后拒绝修改并保留数据。"""
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    schema, admin, engine, manager = await create_isolated_manager("personal_create")
    try:
        await create_business_tables(manager)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add(
                ConfigOption(
                    key=system_options.key,
                    name=system_options.name,
                    params=system_options.params,
                    value={"personal_assistant_model": "p:assistant", "default_model": "ordinary:unused"},
                )
            )
            await db.execute(
                text(
                    "INSERT INTO users(username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('person','person','hash','user',0,0)"
                )
            )
            await db.commit()

        async def create():
            async with sessions() as db:
                return await create_personal_assistant(db, user=SimpleNamespace(uid="person"), name="助手", avatar="😀")

        results = await asyncio.gather(create(), create())
        assert results[0] == results[1]
        assert results[0]["assistant"]["id"] == "assistant:person"
        assert results[0]["model_available"] is False
        async with sessions() as db:
            row = await db.get(PersonalAssistant, "person")
            assert row is not None
            assert len((await db.scalars(select(PersonalAssistant))).all()) == 1
            agent = await db.get(Agent, row.agent_id)
            project = await db.get(Project, row.project_id)
            assert agent.slug == results[0]["assistant"]["agent_id"]
            assert project.workdir_path == "agents/assistant"
            from yuxi.modules.agents.models.sessions import Session

            main = await db.scalar(select(Session).where(Session.thread_id == row.main_thread_id))
            assert main.config_snapshot["model"] == ""
            assert (tmp_path / "shared" / "person" / "workspace" / "agents" / "assistant").is_dir()
            user = await db.scalar(text("SELECT id FROM users WHERE uid='person'"))
            from yuxi.modules.identity.models import User

            operator = await db.get(User, user)
            assert await AgentRepository(db).list_visible(user=operator) == []
            from yuxi.modules.schedules.service import _validate_agent

            assert (await _validate_agent(agent.slug, operator, db)).id == agent.id
            managed, _ = await AgentRepository(db).lock_for_management(agent_id=agent.id, user=operator)
            assert managed.id == agent.id
            from yuxi.modules.extensions.skills.bound import lock_manageable_agent

            bound_agent, _ = await lock_manageable_agent(db, agent.slug, operator)
            assert bound_agent.id == agent.id
        # 协作创建已持有目标 Agent 键共享锁；编辑不能反向先占据主 Session。
        from yuxi.modules.agents.repositories.sessions import SessionRepository

        editor_started = asyncio.get_running_loop().create_future()

        async def edit():
            async with sessions() as db:
                editor_started.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
                return await update_personal_assistant(db, user=SimpleNamespace(uid="person"), updates={"name": "并发编辑"})

        async with sessions() as db:
            await AgentRepository(db).get_by_slug(agent.slug, for_key_share=True)
            caller_pid = await db.scalar(text("SELECT pg_backend_pid()"))
            editor = asyncio.create_task(edit())
            try:
                editor_pid = await editor_started
                for _ in range(100):
                    blockers = await db.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": editor_pid})
                    if caller_pid in blockers:
                        break
                    await asyncio.sleep(0.01)
                else:
                    pytest.fail("编辑未等待协作方持有的 Agent 锁")
                async with asyncio.timeout(3):
                    await SessionRepository(db).lock_session_by_thread_id(row.main_thread_id)
                await db.commit()
                assert (await editor)["assistant"]["name"] == "并发编辑"
            finally:
                if not editor.done():
                    editor.cancel()
                await asyncio.gather(editor, return_exceptions=True)
        monkeypatch.setenv("YUXI_PERSONAL_ASSISTANT_ENABLED", "false")
        async with sessions() as db:
            with pytest.raises(ResourceNotFoundError, match="未启用"):
                await update_personal_assistant(db, user=SimpleNamespace(uid="person"), updates={"name": "other"})
            assert await db.get(PersonalAssistant, "person") is not None
    finally:
        await drop_isolated_schema(schema, admin, engine)


async def test_web_http_owner_and_disabled_guards(monkeypatch, tmp_path):
    """JWT 用户通过真实 HTTP 只能读取与修改自己的绑定。"""
    from fastapi import FastAPI

    from test.integration.support.module_audit import isolated_audit_store, serve_http
    from yuxi.api.errors import domain_error_handler
    from yuxi.api.routers.agents.management import agent_router
    from yuxi.api.routers.agents.personal import router
    from yuxi.modules.identity.models import Department, User
    from yuxi.modules.identity.security import AuthUtils
    from yuxi.shared.errors import DomainError

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions() as db:
            department = Department(name="个人助理测试部门")
            db.add(department)
            await db.flush()
            owner = User(uid="owner", username="owner", password_hash="fixture", department_id=department.id)
            other = User(uid="other", username="other", password_hash="fixture", department_id=department.id)
            db.add_all(
                [owner, other, ConfigOption(key=system_options.key, name=system_options.name, params=system_options.params, value={})]
            )
            await db.flush()
            headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(owner.id), 'auth_method': 'password'})}"}
            other_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(other.id), 'auth_method': 'password'})}"}
            await db.commit()
        app = FastAPI()
        app.add_exception_handler(DomainError, domain_error_handler)
        app.include_router(router, prefix="/api")
        app.include_router(agent_router, prefix="/api")
        async with serve_http(app) as client:
            url = "/api/agents/personal"
            assert (await client.post(url, json={"name": "小助理", "avatar": ""}, headers=headers)).status_code == 422
            created = await client.post(url, json={"name": "小助理", "avatar": "😀"}, headers=headers)
            assert created.status_code == 200, created.text
            assert created.json()["assistant"]["name"] == "小助理"
            slug = created.json()["assistant"]["agent_id"]
            visible = (await client.get("/api/agent", headers=headers)).json()["agents"]
            assert slug not in {item["slug"] for item in visible}
            detail = await client.get(f"/api/agent/{slug}", headers=headers)
            assert detail.status_code == 200, detail.text
            personal_agent = detail.json()["agent"]
            assert personal_agent["slug"] == slug
            assert personal_agent["is_personal_assistant"] is True
            assert "configurable_items" not in personal_agent
            default_detail = await client.get("/api/agent/default", headers=headers)
            assert default_detail.status_code == 200, default_detail.text
            default_agent = default_detail.json()["agent"]
            assert "is_personal_assistant" not in default_agent
            assert "model" in default_agent["configurable_items"]
            updated = await client.put(f"/api/agent/{slug}", json={"name": "普通编辑"}, headers=headers)
            assert updated.status_code == 200, updated.text
            assert (await client.get(url, headers=headers)).json()["assistant"]["name"] == "普通编辑"
            denied = await client.put(f"/api/agent/{slug}", json={"name": "不能修改"}, headers=other_headers)
            assert denied.status_code in {403, 404}, denied.text
            assert (await client.delete(f"/api/agent/{slug}", headers=headers)).status_code == 409
            assert (await client.get(url, headers=other_headers)).json()["assistant"] is None
            assert (await client.patch(url, json={"icon": "  "}, headers=headers)).status_code == 422
            rejected = await client.patch(
                url, json={"config_json": {"context": {"system_prompt": "test", "model": "forbidden"}}}, headers=headers
            )
            assert rejected.status_code == 422
            async with sessions() as db:
                row = await db.get(PersonalAssistant, "owner")
                agent = await db.get(Agent, row.agent_id)
                agent.config_json = {"context": {"system_prompt": "原有提示词", "summary_keep_messages": 17}}
                await db.commit()
            changed = await client.patch(url, json={"config_json": {"context": {"system_prompt": "简洁回答"}}}, headers=headers)
            assert changed.status_code == 200 and changed.json()["assistant"]["config_json"]["context"]["system_prompt"] == "简洁回答"
            monkeypatch.setenv("YUXI_PERSONAL_ASSISTANT_ENABLED", "false")
            assert (await client.get(url, headers=headers)).status_code == 404
            assert (await client.patch(url, json={"name": "不可修改"}, headers=headers)).status_code == 404
            assert (await client.post(url, json={"name": "不可创建", "avatar": "😀"}, headers=other_headers)).status_code == 404
        async with sessions() as db:
            row = await db.get(PersonalAssistant, "owner")
            agent = await db.get(Agent, row.agent_id)
            assert agent.name == "普通编辑" and agent.config_json["context"]["system_prompt"] == "简洁回答"
            assert agent.config_json["context"]["summary_keep_messages"] == 17
            assert await db.get(PersonalAssistant, "other") is None


async def test_input_snapshot_uses_dedicated_model_and_preserves_bound_project(monkeypatch, tmp_path):
    """缺省模型首次补齐，后续输入保留会话选择，资料编辑不回写已接收输入。"""
    from yuxi.modules.agents.input_messages import build_chat_input_message
    from yuxi.modules.agents.models.inputs import AgentInput
    from yuxi.modules.agents.models.sessions import Session
    from yuxi.modules.agents.repositories.sessions import SessionRepository
    from yuxi.modules.agents.runtime.agent_backends.personal_assistant.graph import PersonalAssistantAgent
    from yuxi.modules.agents.scope import ActorScope
    from yuxi.modules.agents.services.input_config import resolve_agent_context_snapshot
    from yuxi.modules.agents.services.inputs import accept_locked
    from yuxi.modules.agents.services.personal_assistants import personal_assistant_view
    from yuxi.modules.agents.services.threads import archive_thread, update_thread
    from yuxi.modules.models.providers.cache import model_cache
    from yuxi.shared.errors import InvalidInputError, InvalidOperationError

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        model_cache, "get_model_info", lambda spec: SimpleNamespace(model_type="chat") if spec in {"p:first", "p:second"} else None
    )
    schema, admin, engine, manager = await create_isolated_manager("personal_input")
    try:
        await create_business_tables(manager)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add(ConfigOption(key=system_options.key, name=system_options.name, params=system_options.params, value={}))
            await db.execute(
                text(
                    "INSERT INTO users(username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('person','person','hash','user',0,0)"
                )
            )
            await db.commit()
        async with sessions() as db:
            result = await create_personal_assistant(db, user=SimpleNamespace(uid="person"), name="助手", avatar="😀")
            thread_id = result["assistant"]["thread_id"]
            row = await db.get(PersonalAssistant, "person")
            agent = await db.get(Agent, row.agent_id)
            session = await db.scalar(select(Session).where(Session.thread_id == thread_id))
            assert not {"tools", "skills", "knowledges", "mcps"} & session.config_snapshot.keys()
            with pytest.raises(InvalidInputError, match="配置个人助理模型"):
                await resolve_agent_context_snapshot(None, None, agent, PersonalAssistantAgent, db)
            explicit = await resolve_agent_context_snapshot("p:first", "always_trust", agent, PersonalAssistantAgent, db)
            assert explicit["model"] == "p:first" and explicit["tool_approval_mode"] == "always_trust"
            with pytest.raises(InvalidOperationError, match="不能归档"):
                await archive_thread(db=db, scope=ActorScope(uid="person", app_id=None), thread_id=thread_id)

        async def accept(key, scope, source="web", model_spec=None, tool_approval_mode=None):
            async with sessions() as db:
                session = await SessionRepository(db).lock_session_by_thread_id(thread_id)
                receipt = await accept_locked(
                    db=db,
                    scope=scope,
                    agent_session=session,
                    idempotency_key=key,
                    event_type="agent.session.input.message",
                    intent_hash=key,
                    mode="follow_up",
                    messages=[build_chat_input_message(key)],
                    model_spec=model_spec,
                    tool_approval_mode=tool_approval_mode,
                    source=source,
                    channel="web",
                    external_id=None,
                    origin_metadata=None,
                )
                input_id = receipt.input_id
                await db.commit()
                return input_id

        async with sessions() as db:
            option = await db.scalar(select(ConfigOption).where(ConfigOption.key == system_options.key))
            option.value = {"personal_assistant_model": "p:first", "default_model": "ordinary:unused"}
            await db.commit()
            await update_personal_assistant(
                db,
                user=SimpleNamespace(uid="person"),
                updates={"name": "初始名字", "config_json": {"context": {"system_prompt": "第一次"}}},
            )
        first = await accept("first", ActorScope(uid="person", app_id=None))
        async with sessions() as db:
            option = await db.scalar(select(ConfigOption).where(ConfigOption.key == system_options.key))
            option.value = {"personal_assistant_model": "p:second"}
            await db.commit()
            await update_personal_assistant(
                db,
                user=SimpleNamespace(uid="person"),
                updates={"name": "更新名字", "config_json": {"context": {"system_prompt": "第二次"}}},
            )
        second = await accept("second", ActorScope(uid="person", app_id=None), source="cooperation")
        explicit_id = await accept(
            "explicit", ActorScope(uid="person", app_id=None), model_spec="p:first", tool_approval_mode="always_trust"
        )
        async with sessions() as db:
            explicit_input = await db.get(AgentInput, explicit_id)
            assert explicit_input.kind == "follow_up"
            assert explicit_input.input_payload["context_snapshot"]["model"] == "p:first"
            assert explicit_input.input_payload["context_snapshot"]["tool_approval_mode"] == "always_trust"
            first_row = await db.get(AgentInput, first)
            second_row = await db.get(AgentInput, second)
            assert first_row.kind == second_row.kind == "follow_up"
            assert first_row.input_payload["context_snapshot"]["personal_assistant_name"] == "初始名字"
            assert second_row.input_payload["context_snapshot"]["personal_assistant_name"] == "更新名字"
            assert (first_row.input_payload["context_snapshot"]["model"], first_row.input_payload["context_snapshot"]["system_prompt"]) == (
                "p:first",
                "第一次",
            )
            assert (
                second_row.input_payload["context_snapshot"]["model"],
                second_row.input_payload["context_snapshot"]["system_prompt"],
            ) == ("p:first", "第二次")
        async with sessions() as db:
            await update_thread(
                db=db,
                scope=ActorScope(uid="person", app_id=None),
                thread_id=thread_id,
                model_spec="p:second",
                tool_approval_mode="always_trust",
            )
            option = await db.scalar(select(ConfigOption).where(ConfigOption.key == system_options.key))
            option.value = {"personal_assistant_model": "missing:model"}
            await db.commit()
            assert (await personal_assistant_view(db, "person"))["model_available"] is True
        configured_id = await accept("configured", ActorScope(uid="person", app_id=None))
        async with sessions() as db:
            configured = await db.get(AgentInput, configured_id)
            assert configured.input_payload["context_snapshot"]["model"] == "p:second"
            assert configured.input_payload["context_snapshot"]["tool_approval_mode"] == "always_trust"
            current = await db.scalar(select(Session).where(Session.thread_id == thread_id))
            assert current.project_id == row.project_id
            assert current.config_snapshot["model"] == "p:second"
            assert len((await db.scalars(select(AgentInput))).all()) == 4
    finally:
        await drop_isolated_schema(schema, admin, engine)


@pytest.mark.parametrize("access_level", ["full", "agents"])
async def test_personal_session_uses_normal_key_and_user_permissions(monkeypatch, tmp_path, access_level):
    """本人 Key 可以访问已知会话，列表隐藏且其他用户仍受普通权限隔离。"""
    from fastapi import FastAPI

    from test.integration.support.module_audit import isolated_audit_store, serve_http
    from yuxi.api.errors import domain_error_handler
    from yuxi.api.routers.public_v1.agents import public_agents_router
    from yuxi.modules.agents.models.inputs import AgentInput, AgentInputReceipt
    from yuxi.modules.agents.models.sessions import Session
    from yuxi.modules.identity.models import APIKey, Department, User
    from yuxi.modules.identity.security import AuthUtils
    from yuxi.shared.errors import DomainError

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    async with isolated_audit_store(monkeypatch) as (_, sessions):
        async with sessions() as db:
            department = Department(name="个人凭据边界")
            db.add(department)
            await db.flush()
            other = User(uid="boundary-other", username="boundary-other", password_hash="fixture", department_id=department.id)
            db.add(other)
            user = User(uid="boundary-owner", username="boundary-owner", password_hash="fixture", department_id=department.id)
            db.add_all([user, ConfigOption(key=system_options.key, name=system_options.name, params=system_options.params, value={})])
            await db.flush()
            secret, key_hash, key_prefix = AuthUtils.generate_api_key()
            db.add(
                APIKey(
                    user_id=user.id,
                    created_by=user.uid,
                    name="fixture",
                    key_hash=key_hash,
                    key_prefix=key_prefix,
                    access_level=access_level,
                )
            )
            await db.flush()
            other_jwt = AuthUtils.create_access_token({"sub": str(other.id), "auth_method": "password"})
            jwt = AuthUtils.create_access_token({"sub": str(user.id), "auth_method": "password"})
            await db.commit()
            result = await create_personal_assistant(db, user=user, name="受限助理", avatar="/icons/personal-assistant/blue.svg")
            thread_id = result["assistant"]["thread_id"]
            slug = result["assistant"]["agent_id"]
            db.add(
                AgentInputReceipt(
                    id="boundary-receipt",
                    thread_id=thread_id,
                    uid=user.uid,
                    app_id=None,
                    idempotency_key="known-key",
                    event_type="agent.session.input.message",
                    intent_hash="fixture",
                )
            )
            await db.commit()
        app = FastAPI()
        app.add_exception_handler(DomainError, domain_error_handler)
        app.include_router(public_agents_router, prefix="/api")
        path = f"/api/v1/agents/sessions/{thread_id}"
        async with serve_http(app) as client:
            web = {"Authorization": f"Bearer {jwt}"}
            key = {"Authorization": f"Bearer {secret}"}
            assert (await client.get(path, headers=web)).status_code == 200
            assert (await client.get(path + "/receipt", params={"idempotency_key": "known-key"}, headers=web)).status_code == 200
            for suffix in ("", "/queue", "/receipt?idempotency_key=known-key"):
                response = await client.get(path + suffix, headers=key)
                assert response.status_code == 200, response.text
            assert (await client.get(path + "/inputs/missing", headers=key)).status_code == 404
            assert (await client.get(path, headers={"Authorization": f"Bearer {other_jwt}"})).status_code == 404
            assert (await client.get("/api/v1/agents/sessions", params={"agent_id": slug}, headers=key)).json()["data"] == []
            assert (await client.get("/api/v1/agents/sessions/search", params={"q": "受限助理"}, headers=key)).json()["items"] == []
            response = await client.post(path, json={"yuxi": {"title": "允许本人修改标题"}}, headers=key)
            assert response.status_code == 200, response.text
        async with sessions() as db:
            session = await db.scalar(select(Session).where(Session.thread_id == thread_id))
            assert session.title == "允许本人修改标题"
            assert list(await db.scalars(select(AgentInput))) == []
            assert await db.get(PersonalAssistant, user.uid) is not None


@pytest.mark.parametrize("unfinished", ["pending_input", "running_turn", "waiting_turn", "cleanup"])
async def test_existing_backend_upgrade_rejects_unfinished_work(monkeypatch, tmp_path, unfinished):
    """旧图等待中的 Turn 和已结束 Run 的清理责任都必须先处理。"""
    from yuxi.modules.agents.models.inputs import AgentInput
    from yuxi.modules.agents.models.runs import AgentRun
    from yuxi.modules.agents.models.sessions import Session
    from yuxi.modules.agents.models.turns import AgentTurn

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    schema, admin, engine, manager = await create_isolated_manager("personal_backend_upgrade")
    try:
        await create_business_tables(manager)
        await create_schema_version_table(manager)
        await record_schema_version(manager, "business", 2)
        await record_schema_version(manager, "knowledge", 2)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            await db.execute(
                text(
                    "INSERT INTO users(username,uid,password_hash,role,login_failed_count,is_deleted) "
                    "VALUES ('person','person','hash','user',0,0)"
                )
            )
            db.add(ConfigOption(key=system_options.key, name=system_options.name, params=system_options.params, value={}))
            await db.commit()
            result = await create_personal_assistant(db, user=SimpleNamespace(uid="person"), name="保留历史", avatar="😀")
            binding = await db.get(PersonalAssistant, "person")
            agent = await db.get(Agent, binding.agent_id)
            agent.backend_id = "ChatbotAgent"
            thread = await db.scalar(select(Session).where(Session.thread_id == result["assistant"]["thread_id"]))
            turn = AgentTurn(
                id="old-turn",
                thread_id=thread.thread_id,
                uid="person",
                status="waiting" if unfinished == "waiting_turn" else "running" if unfinished == "running_turn" else "completed",
            )
            db.add(turn)
            await db.flush()
            db.add(
                AgentRun(
                    id="old-run",
                    thread_id=thread.thread_id,
                    runtime_scope_id=thread.tree_root_thread_id,
                    agent_slug=agent.slug,
                    uid="person",
                    turn_id=turn.id,
                    session_record_id=thread.id,
                    status="interrupted",
                    runtime_cleanup_pending=unfinished == "cleanup",
                    input_payload={},
                )
            )
            if unfinished == "pending_input":
                db.add(
                    AgentInput(
                        id="old-input", thread_id=thread.thread_id, uid="person", agent_slug=agent.slug, kind="follow_up", messages=[]
                    )
                )
            await db.commit()
        with pytest.raises(RuntimeError, match="待处理输入、未结束轮次或清理责任"):
            await upgrade(manager)
        async with sessions() as db:
            assert (await db.get(Agent, binding.agent_id)).backend_id == "ChatbotAgent"
            await db.execute(text("UPDATE agent_turns SET status='completed'"))
            await db.execute(text("UPDATE agent_inputs SET status='cancelled',cancelled_at=CURRENT_TIMESTAMP"))
            await db.execute(text("UPDATE agent_runs SET runtime_cleanup_pending=false"))
            await db.commit()
        await upgrade(manager)
        async with sessions() as db:
            upgraded = await db.get(Agent, binding.agent_id)
            assert upgraded.backend_id == "PersonalAssistantAgent" and upgraded.slug == result["assistant"]["agent_id"]
            assert (await db.get(PersonalAssistant, "person")).main_thread_id == thread.thread_id
            await db.execute(text("UPDATE agent_turns SET status='waiting'"))
            await db.commit()
        await upgrade(manager)  # 已升级 Agent 不再被当作待迁移的旧图。
    finally:
        await drop_isolated_schema(schema, admin, engine)
