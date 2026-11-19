"""用冻结旧 DDL 和合成数据验证 9/4 架构转换，不依赖当前模型生成来源。"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.api.dependencies.auth import get_db
from yuxi.api.routers.public_v1.agents.auth import require_public_context
from yuxi.api.routers.public_v1.agents.threads import router as threads_router
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.legacy_v073 import audit_legacy_schema, repair_legacy_session_configs, upgrade_legacy_schema
from yuxi.migrations.main import initialize_schema
from yuxi.modules.agents.input_messages import deserialize_input_message
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.permissions import user_can_run_agent
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.identity.models import User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]

SEED_SQL = """
INSERT INTO yuxi_schema_migrations (domain, version) VALUES ('business',9),('knowledge',4);
INSERT INTO users (id, username, uid, password_hash, role, login_failed_count, is_deleted)
VALUES (40,'retained','owner','password-sentinel','superadmin',0,0),
       (41,'reader','reader','reader-sentinel','user',0,0),
       (42,'outsider','outsider','outsider-sentinel','user',0,0);
INSERT INTO projects (id,uid,name,selection_status,workdir_path,directory_mode)
VALUES ('project','owner','retained','implicit','/workspaces/project','managed');
INSERT INTO agents (id,slug,backend_id,name,pics,config_json,share_config,is_default,is_subagent,created_by)
VALUES (50,'retained','SubAgentBackend','retained','[]','{"custom":"sentinel"}',
        '{"version":2,"read_scope":{"access_level":"user","user_uids":["reader"]},"manage_scope":null}',false,false,'owner'),
       (51,'private','ChatbotAgent','private','[]','{}',
        '{"version":2,"read_scope":null,"manage_scope":null}',false,false,'owner');
INSERT INTO conversations (id,thread_id,uid,agent_id,title,status,is_pinned,project_id,created_at,updated_at)
VALUES (60,'root','owner','retained','root title','active',true,'project','2026-01-02 03:04:05','2026-01-02 03:04:05'),
       (61,'child','owner','retained','child title','active',false,'project','2026-01-02 03:04:05','2026-01-02 03:04:05');
UPDATE agents SET config_json='{"custom":"sentinel","context":{
    "model":"agent-model","system_prompt":"retained prompt","skills":["retained-skill"],
    "tools":null,"mcps":null,"knowledges":[],"preload_skills":null}}'
WHERE slug='retained';
UPDATE conversations SET extra_metadata='{"model_spec":"session-model","tool_approval_mode":"default"}' WHERE id=60;
INSERT INTO agent_runs (id,conversation_thread_id,runtime_scope_id,agent_slug,uid,status,request_id,
    source,channel,origin_metadata,conversation_id,created_by_run_id,run_type,input_payload,token_usage,created_at,finished_at)
VALUES ('first','root','root','retained','owner','interrupted','original','chat','web','{}',60,null,'chat','{}','{}',
        '2026-01-02 03:04:05','2026-01-02 03:05:00'),
       ('resumed','root','root','retained','owner','completed','resume','chat','web','{}',60,'first','resume','{}','{}',
        '2026-01-02 03:06:00','2026-01-02 03:07:00'),
       ('child-run','child','root','retained','owner','completed','child-request','chat','web','{}',61,'first','subagent','{}','{}',
        '2026-01-02 03:04:06','2026-01-02 03:04:55');
UPDATE agent_runs SET input_payload='{"model_spec":"last-run-model","tool_approval_mode":"always_trust"}' WHERE id='resumed';
INSERT INTO subagent_threads (id,uid,parent_conversation_id,child_conversation_id,child_thread_id,subagent_slug,created_by_run_id)
VALUES (70,'owner',60,61,'child','retained','first');
INSERT INTO messages (id,conversation_id,role,content,message_type,extra_metadata,run_id,delivery_status,created_at)
VALUES (80,60,'user','question','multimodal_image',
        '{"raw_message":{"content":[{"type":"text","text":"question"},{"type":"image_url","image_url":{"url":"https://example.com/image.png"}}]},"attachment_file_ids":["attachment"]}',
        'first','complete','2026-01-02 03:04:05'),
       (81,60,'assistant','final answer','text','{}','resumed','complete','2026-01-02 03:07:00'),
       (82,61,'assistant','child answer','text','{}','child-run','complete','2026-01-02 03:04:55'),
       (83,60,'tool','tool output','tool_audit','{}','first','complete','2026-01-02 03:04:40'),
       (84,60,'user','cancelled question','text','{}',null,'cancelled','2026-01-02 04:00:00');
UPDATE agent_runs SET input_message_id=80 WHERE id='first';
UPDATE agent_runs SET output_message_id=81 WHERE id='resumed';
UPDATE agent_runs SET output_message_id=82 WHERE id='child-run';
INSERT INTO agent_run_requests (id,request_id,uid,agent_slug,conversation_thread_id,source,channel,
    origin_metadata,queue_policy,status,input_message_id,dispatched_run_id,input_payload,created_at,dispatched_at,updated_at)
VALUES (90,'original','owner','retained','root','chat','web','{}','follow_up','dispatched',80,'first',
        '{"model_spec":"model-sentinel"}','2026-01-02 03:04:05','2026-01-02 03:04:05','2026-01-02 03:04:05'),
       (91,'cancelled','owner','retained','root','chat','web','{}','steer','cancelled',84,null,
        '{}','2026-01-02 04:00:00',null,'2026-01-02 04:00:01');
INSERT INTO message_feedbacks (id,message_id,uid,rating,reason,created_at)
VALUES (100,81,'owner','like','reason-sentinel','2026-01-02 04:00:01');
INSERT INTO tasks (id,name,type,status,progress,message,payload,result,cancel_requested,created_at)
VALUES ('job','retained','knowledge','success',100,'done','{}','{"retained":true}',0,'2026-01-02 03:04:05');
INSERT INTO wps_cloud_config (id,enabled,app_id,app_secret_encrypted,version)
VALUES (1,true,'app-sentinel','encrypted-secret-sentinel','version-sentinel');
INSERT INTO wps_connections (uid,access_token_encrypted,refresh_token_encrypted)
VALUES ('owner','encrypted-access-sentinel','encrypted-refresh-sentinel');
INSERT INTO knowledge_bases (id,kb_id,name,kb_type,created_by) VALUES (110,'kb','retained','milvus','owner');
INSERT INTO knowledge_files (id,file_id,kb_id,filename,path,status,processing_task_id)
VALUES (120,'file','kb','retained.md','object-sentinel','done','job');
INSERT INTO knowledge_document_versions (version_id,kb_id,file_id,doc_id,indexing_path,embedding_model_spec,
    embedding_dimension,chunk_preset_id,processing_params,status)
VALUES ('version','kb','file','doc','parent_child','embedding-sentinel',128,'default','{}','active');
INSERT INTO knowledge_parent_chunks (parent_id,version_id,kb_id,file_id,doc_id,parent_index,parent_text,
    start_offset,end_offset,token_count,metadata)
VALUES ('parent','version','kb','file','doc',0,'parent sentinel',0,15,3,'{}');
INSERT INTO knowledge_child_chunks (child_id,parent_id,version_id,kb_id,file_id,doc_id,child_index,child_text,
    start_offset,end_offset,token_count,spans,metadata)
VALUES ('chunk','parent','version','kb','file','doc',0,'child sentinel',0,14,3,'[]','{}');
INSERT INTO checkpoints (thread_id,checkpoint_id,checkpoint,metadata) VALUES ('root','checkpoint','{"retained":true}','{}');
CREATE TABLE unrelated_sentinel (value text);
INSERT INTO unrelated_sentinel VALUES ('untouched');
"""


# 无参数；提供完整冻结旧结构的独立 manager，结束时清理目标与归档 Schema。
@pytest.fixture
async def legacy_database():
    """用不含用户数据的旧库 DDL 与合成哨兵建立真实 PostgreSQL 来源。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_legacy")
    try:
        fixture = Path(__file__).parents[2] / "fixtures" / "migrations" / "legacy_v073_9_4.sql"
        ddl = fixture.read_text(encoding="utf-8")
        async with engine.begin() as conn:
            raw = await conn.get_raw_connection()
            await raw.driver_connection.execute(ddl.replace("public.", f'"{schema}".') + SEED_SQL)
        yield manager
    finally:
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}_v073_archive" CASCADE'))
        await drop_isolated_schema(schema, admin, engine)


# legacy_database 为冻结来源；无返回，逐项验证业务关联、权限、密文与重复执行。
async def test_upgrade_preserves_data_scope_and_can_write_again(legacy_database):
    """新结构回读历史内容和归属，并真实写入验证序列与权限约束。"""
    manager = legacy_database
    report = await audit_legacy_schema(manager)
    assert report["versions"] == {"business": 9, "knowledge": 4}
    assert report["blocking"] == []
    async with manager.async_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('sessions')")) is None
    await upgrade_legacy_schema(manager)
    await upgrade_legacy_schema(manager)
    await initialize_schema(manager)
    assert await get_schema_versions(manager) == {"business": 4, "knowledge": 3}

    async with manager.async_engine.connect() as conn:
        # 每张当前模型都能按完整列读取，防止只写版本标记而遗漏结构。
        for metadata in load_models():
            for table in metadata.tables.values():
                await conn.execute(select(table).limit(1))
        assert (
            await conn.execute(text("SELECT parent_thread_id,tree_root_thread_id,cooperation_path FROM sessions WHERE thread_id='child'"))
        ).one() == ("root", "root", "/root/legacy_70")
        turn = (await conn.execute(text("SELECT id,result_run_id,status,created_at FROM agent_turns WHERE thread_id='root'"))).one()
        assert turn.result_run_id == "resumed" and turn.status == "completed"
        assert turn.created_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        assert (await conn.execute(text("SELECT turn_id,resume_from_run_id FROM agent_runs WHERE id='resumed'"))).one() == (
            turn.id,
            "first",
        )
        assert await conn.scalar(text("SELECT turn_id<>:turn FROM agent_runs WHERE id='child-run'"), {"turn": turn.id})
        assert (await conn.execute(text("SELECT run_id,turn_id,source_input_id,content FROM messages WHERE id=80"))).one() == (
            "first",
            turn.id,
            "original",
            "question",
        )
        assert (await conn.execute(text("SELECT run_id,turn_id,content FROM messages WHERE id=81"))).one() == (
            "resumed",
            turn.id,
            "final answer",
        )
        assert (await conn.execute(text("SELECT content,extra_metadata->>'output' FROM messages WHERE id=83"))).one() == ("", "tool output")
        raw = await conn.scalar(text("SELECT messages FROM agent_inputs WHERE id='original'"))
        message = deserialize_input_message(raw[0])
        assert message.content == "question" and message.message_type == "multimodal_image"
        assert message.raw_message()["content"][1]["image_url"]["url"] == "https://example.com/image.png"
        assert await conn.scalar(text("SELECT attachment_file_ids FROM agent_inputs WHERE id='original'")) == ["attachment"]
        assert await conn.scalar(text("SELECT count(*) FROM agent_input_receipts")) == 2
        assert await conn.scalar(text("SELECT status FROM agent_inputs WHERE id='cancelled'")) == "cancelled"
        assert await conn.scalar(text("SELECT reason FROM agent_feedback_records WHERE run_id='resumed'")) == "reason-sentinel"
        assert await conn.scalar(text("SELECT result FROM background_jobs WHERE id='job'")) == {"retained": True}
        assert await conn.scalar(text("SELECT app_secret_encrypted FROM wps_cloud_config")) == "encrypted-secret-sentinel"
        assert await conn.scalar(text("SELECT refresh_token_encrypted FROM wps_connections")) == "encrypted-refresh-sentinel"
        assert (
            await conn.execute(text("SELECT filename,path,generation,active_generation,processing_job_id FROM knowledge_files"))
        ).one() == ("retained.md", "object-sentinel", 1, 1, "job")
        assert (
            await conn.scalar(text("SELECT child_text FROM knowledge_child_chunks WHERE parent_id='parent' AND version_id='version'"))
            == "child sentinel"
        )
        assert await conn.scalar(text("SELECT generation FROM knowledge_document_versions WHERE version_id='version'")) == 1
        assert await conn.scalar(text("SELECT checkpoint FROM checkpoints")) == {"retained": True}
        assert await conn.scalar(text("SELECT value FROM unrelated_sentinel")) == "untouched"
        schema = await conn.scalar(text("SELECT current_schema()"))
        assert await conn.scalar(text(f'SELECT request_id FROM "{schema}_v073_archive".messages WHERE id=80')) is None
        assert await conn.scalar(text(f'SELECT content FROM "{schema}_v073_archive".messages WHERE id=83')) == "tool output"
        assert await conn.scalar(text(f"SELECT version FROM \"{schema}_v073_archive\".yuxi_schema_migrations WHERE domain='business'")) == 9
        assert await conn.scalar(text("SELECT to_regclass('ix_knowledge_chunks_graph_extraction_status')")) is not None

    async with AsyncSession(manager.async_engine) as db:
        shared = await db.scalar(select(Agent).where(Agent.slug == "retained"))
        private = await db.scalar(select(Agent).where(Agent.slug == "private"))
        reader = await db.scalar(select(User).where(User.uid == "reader"))
        outsider = await db.scalar(select(User).where(User.uid == "outsider"))
        assert shared.backend_id == "ChatbotAgent" and shared.config_json["custom"] == "sentinel"
        assert shared.config_json["context"] == {
            "model": "agent-model",
            "system_prompt": "retained prompt",
            "skills": ["retained-skill"],
            "tools": "all",
            "mcps": "all",
            "knowledges": [],
            "preload_skills": [],
        }
        assert private.config_json == {}
        assert shared.visibility == "shared" and user_can_run_agent(reader, shared)
        assert not user_can_run_agent(outsider, shared) and not user_can_run_agent(reader, private)
    async with manager.async_engine.begin() as conn:
        assert (
            await conn.scalar(
                text(
                    "INSERT INTO messages(session_record_id,role,content,delivery_status) VALUES (60,'user','new','complete') RETURNING id"
                )
            )
            > 84
        )
        assert (
            await conn.scalar(
                text(
                    "INSERT INTO agent_inputs(id,thread_id,uid,agent_slug,kind,status,messages,attachment_file_ids,"
                    "input_payload,source,channel,origin_metadata,created_at) VALUES "
                    "('new','root','owner','retained','follow_up','pending','[]','[]','{}','chat','web','{}',now()) "
                    "RETURNING received_seq"
                )
            )
            > 91
        )
        assert await conn.scalar(text("SELECT nextval('agent_runs_execution_seq')")) > 3


# legacy_database 为隔离旧库；无返回，经真实 HTTP 协议读取迁移后的会话。
async def test_migrated_sessions_support_http_history(legacy_database, monkeypatch, tmp_path):
    """历史读取和新建会话均使用迁移后的配置，并回读持久快照。"""
    from yuxi.modules.agents.services import scheduler
    from yuxi.modules.models.providers.cache import model_cache

    manager = legacy_database
    await upgrade_legacy_schema(manager)
    async with manager.async_engine.begin() as conn:
        await conn.execute(text("UPDATE projects SET workdir_path='projects/20260102/abcdef' WHERE id='project'"))
    manager.AsyncSession = async_sessionmaker(manager.async_engine, expire_on_commit=False)
    monkeypatch.setattr(scheduler, "pg_manager", manager)
    monkeypatch.setattr(model_cache, "get_model_info", lambda spec: SimpleNamespace(model_type="chat") if spec == "agent-model" else None)
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(threads_router, prefix="/api/v1/agents")

    async def database():
        """提供隔离 Schema 的请求级真实数据库会话。"""
        async with AsyncSession(manager.async_engine, expire_on_commit=False) as db:
            yield db
            await db.commit()

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[require_public_context] = lambda: SimpleNamespace(scope=ActorScope(uid="owner", app_id=None), api_key=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/agents/sessions")
        assert response.status_code == 200, response.text
        sessions = {row["id"]: row for row in response.json()["data"]}
        assert sessions["root"]["agent"]["model"] == "last-run-model"
        assert sessions["root"]["yuxi"]["tool_approval_mode"] == "always_trust"
        response = await client.get("/api/v1/agents/sessions/root")
        assert response.status_code == 200, response.text
        assert response.json()["yuxi"]["title"] == "root title"
        created = await client.post(
            "/api/v1/agents/sessions",
            headers={"Idempotency-Key": "migrated-agent-create"},
            json={"agent_id": "retained", "project_id": "project", "agent": {"model": "agent-model"}},
        )
        assert created.status_code == 201, created.text
        created_id = created.json()["id"]
        assert created.json()["agent"]["model"] == "agent-model"
    async with manager.async_engine.connect() as conn:
        snapshot = await conn.scalar(text("SELECT config_snapshot FROM sessions WHERE thread_id='child'"))
        assert snapshot["model"] == "agent-model"
        assert snapshot["system_prompt"] == "retained prompt"
        assert snapshot["skills"] == ["retained-skill"]
        assert snapshot["tools"] == snapshot["mcps"] == "all"
        assert snapshot["knowledges"] == snapshot["preload_skills"] == []
        created_snapshot = await conn.scalar(text("SELECT config_snapshot FROM sessions WHERE thread_id=:id"), {"id": created_id})
        assert created_snapshot["tools"] == created_snapshot["mcps"] == "all"
        assert created_snapshot["knowledges"] == created_snapshot["preload_skills"] == []
        assert created_snapshot["skills"] == ["retained-skill"]
        assert await conn.scalar(text("SELECT count(*) FROM agent_input_receipts WHERE thread_id=:id"), {"id": created_id}) == 1


# legacy_database 为隔离旧库；无返回，验证旧定义修复的审计、来源与幂等边界。
async def test_agent_config_repair_preserves_current_choices_and_requires_archive(legacy_database):
    """只修复归档可证明的旧空值，保留当前选择、非归档定义和历史原文。"""
    from yuxi.migrations.legacy_v073 import repair_legacy_agent_configs

    manager = legacy_database
    with pytest.raises(RuntimeError, match="只支持已迁移"):
        await repair_legacy_agent_configs(manager, apply=True)
    await upgrade_legacy_schema(manager)
    retained = {
        "custom": "updated sentinel",
        "context": {
            "model": "selected-model",
            "system_prompt": "updated prompt",
            "tools": "all",
            "mcps": None,
            "skills": None,
            "knowledges": None,
            "preload_skills": None,
        },
    }
    private = {"context": {"tools": [], "mcps": None, "skills": None, "knowledges": None, "preload_skills": []}}
    new_config = {"context": {"mcps": None, "skills": None}}
    async with manager.async_engine.begin() as conn:
        await conn.execute(
            text("UPDATE agents SET config_json=CAST(:config AS json) WHERE slug=:slug"),
            [{"slug": "retained", "config": json.dumps(retained)}, {"slug": "private", "config": json.dumps(private)}],
        )
        await conn.execute(
            text("""INSERT INTO agents (id,slug,backend_id,name,pics,config_json,share_config,visibility,is_builtin,is_default,created_by)
                VALUES (90,'post-upgrade','ChatbotAgent','new','[]',CAST(:config AS json),'{}','private',false,false,'owner')"""),
            {"config": json.dumps(new_config)},
        )
        schema = await conn.scalar(text("SELECT current_schema()"))
        archived = await conn.scalar(text(f'SELECT config_json FROM "{schema}_v073_archive".agents WHERE id=50'))
    assert await repair_legacy_agent_configs(manager) == {"candidates": 2, "repaired": 0}
    async with manager.async_engine.connect() as conn:
        assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='retained'")) == retained
        assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='private'")) == private
    assert await repair_legacy_agent_configs(manager, apply=True) == {"candidates": 2, "repaired": 2}
    assert await repair_legacy_agent_configs(manager, apply=True) == {"candidates": 0, "repaired": 0}
    expected_retained = {
        "custom": "updated sentinel",
        "context": {
            "model": "selected-model",
            "system_prompt": "updated prompt",
            "tools": "all",
            "mcps": "all",
            "skills": None,
            "knowledges": None,
            "preload_skills": [],
        },
    }
    expected_private = {"context": {"tools": [], "mcps": "all", "skills": "all", "knowledges": "all", "preload_skills": []}}
    async with manager.async_engine.begin() as conn:
        assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='retained'")) == expected_retained
        assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='private'")) == expected_private
        assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='post-upgrade'")) == new_config
        assert await conn.scalar(text(f'SELECT config_json FROM "{schema}_v073_archive".agents WHERE id=50')) == archived
        assert await conn.scalar(text("SELECT count(*) FROM messages")) == 5
        # 相同 ID 但不同 slug 不证明当前定义来自归档中的同一个智能体。
        await conn.execute(
            text("UPDATE agents SET slug='renamed',config_json=CAST(:config AS json) WHERE id=51"),
            {"config": json.dumps(new_config)},
        )
        # 相同 slug 但不同 ID 同样不能被当作历史定义修复。
        await conn.execute(text("UPDATE agents SET slug='private' WHERE id=90"))
    assert await repair_legacy_agent_configs(manager, apply=True) == {"candidates": 0, "repaired": 0}
    async with manager.async_engine.begin() as conn:
        assert await conn.scalar(text("SELECT config_json FROM agents WHERE id=51")) == new_config
        assert await conn.scalar(text("SELECT config_json FROM agents WHERE id=90")) == new_config
        await conn.execute(text(f'DROP SCHEMA "{schema}_v073_archive" CASCADE'))
    with pytest.raises(RuntimeError, match="缺少 v0.7.3 归档"):
        await repair_legacy_agent_configs(manager, apply=True)
    async with manager.async_engine.connect() as conn:
        assert await conn.scalar(text("SELECT config_json FROM agents WHERE slug='retained'")) == expected_retained


# legacy_database 为已迁移隔离库；无返回，验证修复仅填空且可重复运行。
async def test_snapshot_repair_is_explicit_and_preserves_existing_config(legacy_database):
    """审计不写库；补齐缺失快照时保留已有选择、消息和归档。"""
    manager = legacy_database
    await upgrade_legacy_schema(manager)
    async with manager.async_engine.begin() as conn:
        await conn.execute(text("UPDATE sessions SET config_snapshot=NULL WHERE thread_id='root'"))
        await conn.execute(
            text("""UPDATE sessions SET config_snapshot='{"model":"selected","tool_approval_mode":"default"}' WHERE thread_id='child'""")
        )
    assert await repair_legacy_session_configs(manager) == {"candidates": 1, "repaired": 0}
    async with manager.async_engine.connect() as conn:
        assert await conn.scalar(text("SELECT config_snapshot IS NULL FROM sessions WHERE thread_id='root'"))
    assert await repair_legacy_session_configs(manager, apply=True) == {"candidates": 1, "repaired": 1}
    assert await repair_legacy_session_configs(manager, apply=True) == {"candidates": 0, "repaired": 0}
    async with manager.async_engine.connect() as conn:
        snapshot = await conn.scalar(text("SELECT config_snapshot FROM sessions WHERE thread_id='root'"))
        assert snapshot["model"] == "last-run-model" and snapshot["tool_approval_mode"] == "always_trust"
        assert (await conn.scalar(text("SELECT config_snapshot FROM sessions WHERE thread_id='child'")))["model"] == "selected"
        assert await conn.scalar(text("SELECT content FROM messages WHERE id=81")) == "final answer"
        schema = await conn.scalar(text("SELECT current_schema()"))
        assert await conn.scalar(text(f'SELECT count(*) FROM "{schema}_v073_archive".conversations')) == 2


# legacy_database 为隔离旧库，values 为归档系统配置；无返回，验证旧默认值解析。
@pytest.mark.parametrize("values", [{}, {"default_model": "selected-default"}])
async def test_snapshot_restores_archived_system_model_default(legacy_database, values):
    """旧系统 value 未覆盖时仍读取 params 声明的默认模型。"""
    manager = legacy_database
    async with manager.async_engine.begin() as conn:
        await conn.execute(text("UPDATE agents SET config_json='{}'"))
        await conn.execute(
            text(
                "INSERT INTO config_options (key,name,description,value,params) "
                "VALUES ('system_options','system','test',CAST(:value AS json),CAST(:params AS json))"
            ),
            {"value": json.dumps(values), "params": json.dumps({"fields": [{"key": "default_model", "default": "declared-default"}]})},
        )
    await upgrade_legacy_schema(manager)
    async with manager.async_engine.connect() as conn:
        snapshot = await conn.scalar(text("SELECT config_snapshot FROM sessions WHERE thread_id='child'"))
        assert snapshot["model"] == values.get("default_model", "declared-default")
        root = await conn.scalar(text("SELECT config_snapshot FROM sessions WHERE thread_id='root'"))
        assert root["model"] == "last-run-model"


# legacy_database 为隔离旧库；无返回，验证 Agent 审批仅在无会话覆盖时恢复。
async def test_snapshot_restores_archived_agent_approval(legacy_database):
    """缺少 Run 或会话覆盖时保留旧 Agent 的 always_trust。"""
    manager = legacy_database
    async with manager.async_engine.begin() as conn:
        await conn.execute(
            text("""UPDATE agents SET config_json='{"context":{"model":"agent-model","tool_approval_mode":"always_trust"}}'""")
        )
        await conn.execute(text("UPDATE agent_runs SET input_payload='{}'"))
    await upgrade_legacy_schema(manager)
    async with manager.async_engine.connect() as conn:
        child = await conn.scalar(text("SELECT config_snapshot FROM sessions WHERE thread_id='child'"))
        root = await conn.scalar(text("SELECT config_snapshot FROM sessions WHERE thread_id='root'"))
        assert child["tool_approval_mode"] == "always_trust"
        assert root["tool_approval_mode"] == "default"


# legacy_database 为隔离旧库；无返回，验证配置来源不足时整批拒绝且不写一半。
async def test_snapshot_repair_refuses_untraceable_model(legacy_database):
    """缺少旧模型记录不能套用新系统默认值来伪造快照。"""
    manager = legacy_database
    await upgrade_legacy_schema(manager)
    async with manager.async_engine.begin() as conn:
        schema = await conn.scalar(text("SELECT current_schema()"))
        await conn.execute(text("UPDATE sessions SET config_snapshot=NULL"))
        await conn.execute(text(f"UPDATE \"{schema}_v073_archive\".agents SET config_json='{{}}'"))
    with pytest.raises(RuntimeError, match="缺少可追溯的模型"):
        await repair_legacy_session_configs(manager, apply=True)
    async with manager.async_engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM sessions WHERE config_snapshot IS NULL")) == 2
        assert await conn.scalar(text("SELECT count(*) FROM messages")) == 5


# legacy_database 为旧库，mutation 为引入在途工作的 SQL；无返回，验证拒绝不改版本。
@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE agent_runs SET status='running' WHERE id='first'",
        "UPDATE agent_runs SET runtime_cleanup_pending=true WHERE id='first'",
        "UPDATE agent_run_requests SET status='queued' WHERE id=91",
        "UPDATE tasks SET status='running' WHERE id='job'",
        "UPDATE knowledge_document_versions SET status='staging'",
    ],
)
async def test_inflight_work_is_a_read_only_rejection(legacy_database, mutation):
    """运行中工作不能被归档并伪装成已完成迁移。"""
    manager = legacy_database
    async with manager.async_engine.begin() as conn:
        await conn.execute(text(mutation))
    assert (await audit_legacy_schema(manager))["blocking"]
    with pytest.raises(RuntimeError):
        await upgrade_legacy_schema(manager)
    assert await get_schema_versions(manager) == {"business": 9, "knowledge": 4}
    async with manager.async_engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('sessions')")) is None


# legacy_database 为旧库；无返回，验证目标约束失败后整个 DDL 与数据均回滚。
async def test_late_constraint_failure_restores_legacy_database(legacy_database):
    """旧库允许的跨目录文件名触发新约束时，归档和版本写入一起回滚。"""
    manager = legacy_database
    async with manager.async_engine.begin() as conn:
        await conn.execute(text("UPDATE knowledge_files SET filename='../invalid.md'"))
    with pytest.raises(IntegrityError):
        await upgrade_legacy_schema(manager)
    assert await get_schema_versions(manager) == {"business": 9, "knowledge": 4}
    async with manager.async_engine.connect() as conn:
        schema = await conn.scalar(text("SELECT current_schema()"))
        assert await conn.scalar(text("SELECT 1 FROM pg_namespace WHERE nspname=:name"), {"name": schema + "_v073_archive"}) is None
        assert await conn.scalar(text("SELECT count(*) FROM conversations")) == 2
        assert await conn.scalar(text("SELECT to_regclass('sessions')")) is None
        assert await conn.scalar(text("SELECT password_hash FROM users WHERE uid='owner'")) == "password-sentinel"


# legacy_database 为旧库；无返回，验证未知版本与已存在归档均显式拒绝。
async def test_unknown_versions_and_existing_archive_are_rejected(legacy_database):
    """迁移不能覆盖已有归档，也不能对不明版本猜测转换。"""
    manager = legacy_database
    async with manager.async_engine.begin() as conn:
        await conn.execute(text("UPDATE yuxi_schema_migrations SET version=8 WHERE domain='business'"))
    with pytest.raises(RuntimeError, match="仅支持"):
        await upgrade_legacy_schema(manager)
    async with manager.async_engine.begin() as conn:
        await conn.execute(text("UPDATE yuxi_schema_migrations SET version=9 WHERE domain='business'"))
        schema = await conn.scalar(text("SELECT current_schema()"))
        await conn.execute(text(f'CREATE SCHEMA "{schema}_v073_archive"'))
    with pytest.raises(RuntimeError, match="归档 Schema 已存在"):
        await upgrade_legacy_schema(manager)
    assert await get_schema_versions(manager) == {"business": 9, "knowledge": 4}


# legacy_database 为旧库；无返回，验证旧进程在审计后提交工作时迁移拒绝。
async def test_work_committed_while_waiting_for_table_lock_is_rechecked(legacy_database):
    """迁移等待旧写事务结束后必须重新检查，不能使用锁前审计结果。"""
    manager = legacy_database
    async with manager.async_engine.connect() as writer:
        await writer.execute(text("LOCK TABLE agent_runs IN ACCESS SHARE MODE"))
        migration = asyncio.create_task(upgrade_legacy_schema(manager))
        try:
            # 先从 PostgreSQL 证明迁移已通过初次审计并等待表锁，避免定时等待与慢环境竞争。
            async with asyncio.timeout(30):
                while not await writer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE relation='agent_runs'::regclass "
                        "AND mode='AccessExclusiveLock' AND NOT granted)"
                    )
                ):
                    if migration.done():
                        await migration
                        pytest.fail("迁移没有等待 agent_runs 表锁")
                    await asyncio.sleep(0.05)
            await writer.execute(text("UPDATE agent_runs SET runtime_cleanup_pending=true WHERE id='first'"))
            await writer.commit()
            with pytest.raises(RuntimeError, match="锁定后"):
                await migration
        finally:
            await writer.rollback()
            if not migration.done():
                migration.cancel()
                await asyncio.gather(migration, return_exceptions=True)
    assert await get_schema_versions(manager) == {"business": 9, "knowledge": 4}
