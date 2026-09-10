from __future__ import annotations

import asyncio
import json
import os
import uuid

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.agents.buildin.chatbot.context import ChatBotContext
from yuxi.agents.context import normalize_agent_context_config
from yuxi.storage.postgres.models_business import User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_admin_settings_are_readable_but_not_writable_by_delegated_user(
    test_client, admin_headers, standard_user
):
    """普通管理用户读取管理员参数，越权保存后 PostgreSQL 仍保留原值。"""
    slug = f"pytest-config-auth-{uuid.uuid4().hex[:10]}"
    uid = str(standard_user["user"]["uid"])
    headers = standard_user["headers"]
    scope = {"access_level": "user", "department_ids": [], "user_uids": [uid]}
    settings = {
        "tool_approval_mode": "always_trust",
        "summary_threshold": 37,
        "summary_keep_messages": 7,
        "summary_prompt": "摘要 {messages}",
        "summary_tool_result_token_limit": 123,
        "max_execution_steps": 42,
        "model_retry_times": 5,
    }
    conn = await asyncpg.connect(os.environ["POSTGRES_URL"].replace("+asyncpg", ""))
    try:
        created = await test_client.post(
            "/api/agent",
            headers=admin_headers,
            json={
                "name": "Pytest config auth",
                "slug": slug,
                "backend_id": "ChatbotAgent",
                "share_config": {"version": 2, "read_scope": scope, "manage_scope": scope},
                "config_json": {"context": {**settings, "max_execution_steps": 60}},
            },
        )
        assert created.status_code == 200, created.text
        updated = await test_client.put(
            f"/api/agent/{slug}", headers=admin_headers, json={"config_json": {"context": settings}}
        )
        assert updated.status_code == 200, updated.text
        read = await test_client.get(f"/api/agent/{slug}", headers=headers)
        assert read.status_code == 200, read.text
        context = read.json()["agent"]["config_json"]["context"]
        assert {key: context[key] for key in settings} == settings

        overwritten = {
            "tool_approval_mode": "default",
            "summary_threshold": 90,
            "summary_keep_messages": 20,
            "summary_prompt": "替换 {messages}",
            "summary_tool_result_token_limit": 900,
            "max_execution_steps": 900,
            "model_retry_times": 9,
        }
        saved = await test_client.put(
            f"/api/agent/{slug}",
            headers=headers,
            json={"config_json": {"context": {**overwritten, "system_prompt": "user edit"}}},
        )
        assert saved.status_code == 200, saved.text
        persisted = (await _read_agent_config(conn, slug))["context"]
        assert {key: persisted[key] for key in settings} == settings
        assert persisted["system_prompt"] == "user edit"
    finally:
        deleted = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        assert deleted.status_code in {200, 404}, deleted.text
        await conn.close()


async def test_delegated_manager_resource_patch_preserves_hidden_config_and_rejects_new_reference(
    test_client,
    admin_headers,
    standard_user,
):
    """真实保存边界保留隐藏选择，并约束运行交集、并发合并与越权新增。"""
    postgres_url = os.environ["POSTGRES_URL"]
    postgres_dsn = postgres_url.replace("+asyncpg", "")
    suffix = uuid.uuid4().hex[:10]
    agent_slug = f"pytest-shared-resources-{suffix}"
    manager_uid = str(standard_user["user"]["uid"])
    manager_headers = standard_user["headers"]
    current_admin = await test_client.get("/api/auth/me", headers=admin_headers)
    assert current_admin.status_code == 200, current_admin.text
    owner_uid = str(current_admin.json()["uid"])

    visible_configured = [f"pytest-{suffix}-visible-{index}" for index in range(5)]
    visible_extra = [f"pytest-{suffix}-extra-{index}" for index in range(3)]
    hidden_configured = [f"pytest-{suffix}-hidden-{index}" for index in range(5)]
    hidden_concurrent = f"pytest-{suffix}-hidden-concurrent"
    hidden_new = f"pytest-{suffix}-hidden-new"
    forbidden_agent_slug = f"pytest-forbidden-resources-{suffix}"
    configured = [item for pair in zip(visible_configured, hidden_configured, strict=True) for item in pair]
    all_skill_slugs = [*configured, *visible_extra, hidden_concurrent, hidden_new]

    conn = await asyncpg.connect(postgres_dsn)
    created_agent = False
    try:
        rows = []
        for slug in [*visible_configured, *visible_extra]:
            rows.append((slug, _user_share_config(manager_uid), manager_uid))
        for slug in [*hidden_configured, hidden_concurrent, hidden_new]:
            rows.append((slug, _user_share_config(owner_uid), owner_uid))
        await conn.executemany(
            """
            INSERT INTO skills
                (slug, name, description, source_type, tool_dependencies, mcp_dependencies,
                 skill_dependencies, dir_path, share_config, enabled, created_by, updated_by)
            VALUES
                ($1, $1, 'pytest resource authorization', 'upload', '[]'::jsonb, '[]'::jsonb,
                 '[]'::jsonb, $1, $2::jsonb, true, $3, $3)
            """,
            rows,
        )

        forbidden_create = await test_client.post(
            "/api/agent",
            json={
                "name": "Pytest forbidden resource agent",
                "slug": forbidden_agent_slug,
                "backend_id": "ChatbotAgent",
                "config_json": {"context": {"skills": [hidden_new]}},
            },
            headers=manager_headers,
        )
        assert forbidden_create.status_code == 422, forbidden_create.text
        assert "无权新增智能体资源 skills" in forbidden_create.json()["detail"]
        assert await conn.fetchval("SELECT count(*) FROM agents WHERE slug = $1", forbidden_agent_slug) == 0

        create_response = await test_client.post(
            "/api/agent",
            json={
                "name": "Pytest shared resource agent",
                "slug": agent_slug,
                "backend_id": "ChatbotAgent",
                "config_json": {"context": {"skills": configured, "system_prompt": "owner prompt"}},
                "share_config": {
                    "version": 2,
                    "read_scope": {
                        "access_level": "user",
                        "department_ids": [],
                        "user_uids": [manager_uid],
                    },
                    "manage_scope": {
                        "access_level": "user",
                        "department_ids": [],
                        "user_uids": [manager_uid],
                    },
                },
            },
            headers=admin_headers,
        )
        assert create_response.status_code == 200, create_response.text
        created_agent = True

        engine = create_async_engine(postgres_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as db:
                manager = await db.scalar(select(User).where(User.uid == manager_uid))
                assert manager is not None
                normalized = await normalize_agent_context_config(
                    {
                        "tools": [],
                        "knowledges": [],
                        "mcps": [],
                        "skills": configured,
                        "subagents": ["missing"],
                    },
                    db=db,
                    user=manager,
                    context_schema=ChatBotContext,
                )
        finally:
            await engine.dispose()
        assert normalized["skills"] == visible_configured
        assert (await _read_agent_config(conn, agent_slug))["context"]["skills"] == configured

        save_response = await test_client.put(
            f"/api/agent/{agent_slug}",
            json={
                "name": "Renamed by delegated manager",
                "config_json": {"context": {"skills": visible_configured}},
            },
            headers=manager_headers,
        )
        assert save_response.status_code == 200, save_response.text
        saved = await _read_agent_config(conn, agent_slug)
        assert saved["context"]["skills"] == configured
        assert saved["context"]["system_prompt"] == "owner prompt"

        observer = await asyncpg.connect(postgres_dsn)
        transaction = conn.transaction()
        await transaction.start()
        blocked_save = None
        lock_observed = False
        try:
            await conn.fetchrow("SELECT id FROM agents WHERE slug = $1 FOR UPDATE", agent_slug)
            concurrent_config = json.loads(json.dumps(saved))
            concurrent_config["context"]["skills"].append(hidden_concurrent)
            await conn.execute(
                "UPDATE agents SET config_json = $2::jsonb WHERE slug = $1",
                agent_slug,
                json.dumps(concurrent_config),
            )
            blocked_save = asyncio.create_task(
                test_client.put(
                    f"/api/agent/{agent_slug}",
                    json={"config_json": {"context": {"skills": visible_configured}}},
                    headers=manager_headers,
                )
            )
            for _ in range(60):
                lock_observed = await observer.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_stat_activity
                        WHERE datname = current_database()
                          AND wait_event_type = 'Lock'
                          AND query LIKE '%agents.config_json%'
                          AND query LIKE '%FOR UPDATE%'
                    )
                    """
                )
                if lock_observed:
                    break
                await asyncio.sleep(0.05)
        finally:
            await transaction.commit()
            await observer.close()

        assert blocked_save is not None
        concurrent_response = await blocked_save
        assert concurrent_response.status_code == 200, concurrent_response.text
        assert lock_observed, "Agent update did not reach the config row lock before the concurrent commit"
        after_concurrent_save = await _read_agent_config(conn, agent_slug)
        assert after_concurrent_save["context"]["skills"] == [*configured, hidden_concurrent]

        edit_response = await test_client.put(
            f"/api/agent/{agent_slug}",
            json={"config_json": {"context": {"skills": [visible_configured[0], visible_extra[0]]}}},
            headers=manager_headers,
        )
        assert edit_response.status_code == 200, edit_response.text
        after_visible_edit = await _read_agent_config(conn, agent_slug)
        assert after_visible_edit["context"]["skills"] == [
            visible_configured[0],
            *hidden_configured,
            hidden_concurrent,
            visible_extra[0],
        ]

        forbidden_response = await test_client.put(
            f"/api/agent/{agent_slug}",
            json={"config_json": {"context": {"skills": [visible_configured[0], hidden_new]}}},
            headers=manager_headers,
        )
        assert forbidden_response.status_code == 422, forbidden_response.text
        assert "无权新增智能体资源 skills" in forbidden_response.json()["detail"]
        assert await _read_agent_config(conn, agent_slug) == after_visible_edit
    finally:
        if created_agent:
            delete_response = await test_client.delete(f"/api/agent/{agent_slug}", headers=admin_headers)
            assert delete_response.status_code in {200, 404}, delete_response.text
        await conn.execute("DELETE FROM skills WHERE slug = ANY($1::text[])", all_skill_slugs)
        await conn.close()


def _user_share_config(*uids: str) -> str:
    """生成测试资源的用户可见范围。"""
    return json.dumps(
        {
            "version": 2,
            "read_scope": {"access_level": "user", "department_ids": [], "user_uids": list(uids)},
            "manage_scope": None,
        }
    )


async def _read_agent_config(conn, slug: str) -> dict:
    """从独立连接回读持久配置，避免使用 HTTP 回包作为唯一事实。"""
    value = await conn.fetchval("SELECT config_json FROM agents WHERE slug = $1", slug)
    assert value is not None
    return json.loads(value) if isinstance(value, str) else value
