"""真实 PostgreSQL 与文件系统证明共享初始化可重跑且保护管理员配置。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.bootstrap import app as app_init
from yuxi.infrastructure.postgres import manager as manager_module
from yuxi.infrastructure.postgres.connection import postgres_conninfo
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.modules.extensions.skills.builtin.sync import list_builtin_skill_specs

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest_asyncio.fixture
async def isolated_app(monkeypatch, tmp_path):
    """所有写入限定在独立数据库 Schema 与临时 Skill 目录。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_app_init")
    manager.AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    pool = AsyncConnectionPool(
        conninfo=postgres_conninfo(os.environ["POSTGRES_URL"]),
        kwargs={"autocommit": True, "options": f"-c search_path={schema}"},
        open=False,
    )
    manager.langgraph_pool = pool
    monkeypatch.setattr(manager, "initialize", lambda: None)
    monkeypatch.setattr(manager, "close", AsyncMock())
    monkeypatch.setattr(app_init, "pg_manager", manager)
    monkeypatch.setattr(manager_module, "pg_manager", manager)
    monkeypatch.setenv("YUXI_SKILL_DATA_DIR", str(tmp_path))
    try:
        await pool.open()
        yield manager, engine, tmp_path
    finally:
        await pool.close()
        await drop_isolated_schema(schema, admin, engine)


async def persisted_facts(engine, root):
    """从独立连接回读五类共享记录及实际 Skill 文件字节。"""
    facts = {}
    async with engine.connect() as conn:
        for table, key in [
            ("config_options", "key"),
            ("model_providers", "provider_id"),
            ("agents", "slug"),
            ("mcp_servers", "slug"),
            ("skills", "slug"),
        ]:
            facts[table] = (await conn.execute(text(f'SELECT id, "{key}" FROM {table} ORDER BY id'))).all()
            assert facts[table], table
    facts["files"] = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert any(name.endswith("SKILL.md") for name in facts["files"])
    return facts


async def test_fresh_app_and_rerun_preserve_admin_values_and_shared_files(isolated_app):
    """完整初始化可重复执行，代码拥有的文件收敛且管理员字段保留。"""
    manager, engine, root = isolated_app
    await app_init.main()
    assert await get_schema_versions(manager) == {"business": 1, "knowledge": 2}
    before = await persisted_facts(engine, root)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE config_options SET value='{\"admin\":\"preserve-this-value\"}', updated_by='admin-sentinel'"))
        await conn.execute(
            text("UPDATE model_providers SET base_url='https://fixture.invalid/v1', api_key='admin-token', is_enabled=false")
        )
        await conn.execute(text("UPDATE agents SET name='Admin Agent', config_json='{}'"))
        await conn.execute(text("UPDATE mcp_servers SET enabled=1"))
        await conn.execute(text("UPDATE skills SET enabled=false"))
    # 模拟代码拥有的发布内容需要修复；重跑必须从镜像来源重新同步。
    spec = list_builtin_skill_specs()[0]
    skill_file = root / "shared" / spec["slug"] / "SKILL.md"
    skill_file.write_text("stale builtin release")
    await app_init.main()
    assert await persisted_facts(engine, root) == before
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM config_options WHERE updated_by <> 'admin-sentinel'")) == 0
        assert (
            await conn.scalar(text('SELECT count(*) FROM config_options WHERE value::jsonb <> \'{"admin":"preserve-this-value"}\'::jsonb'))
            == 0
        )
        assert (
            await conn.scalar(
                text(
                    "SELECT count(*) FROM model_providers WHERE base_url <> 'https://fixture.invalid/v1' "
                    "OR api_key <> 'admin-token' OR is_enabled"
                )
            )
            == 0
        )
        assert await conn.scalar(text("SELECT count(*) FROM agents WHERE name <> 'Admin Agent' OR config_json::jsonb <> '{}'::jsonb")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM mcp_servers WHERE enabled <> 1")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM skills WHERE enabled")) == 0


async def test_failed_shared_stage_preserves_schema_and_retry_finishes(isolated_app, monkeypatch):
    """持久内容失败不能触发空库清理，重跑能完成剩余阶段。"""
    manager, engine, root = isolated_app
    original = app_init.init_builtin_skills
    monkeypatch.setattr(app_init, "init_builtin_skills", AsyncMock(side_effect=OSError("fixture storage unavailable")))
    with pytest.raises(OSError, match="fixture storage unavailable"):
        await app_init.main()
    assert await get_schema_versions(manager) == {"business": 1, "knowledge": 2}
    async with engine.begin() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM agents")) > 0
        await conn.execute(text("INSERT INTO knowledge_bases (kb_id,name,kb_type) VALUES ('retained','Retained','milvus')"))
    monkeypatch.setattr(app_init, "init_builtin_skills", original)
    await app_init.main()
    await persisted_facts(engine, root)
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT name FROM knowledge_bases WHERE kb_id='retained'")) == "Retained"


async def test_existing_schema_initializes_in_a_cold_process(isolated_app):
    """独立进程不能依赖 pytest 预先注册的 ORM；移除装配后应复现缺陷。"""
    manager, engine, root = isolated_app
    await app_init.main()
    async with engine.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    script = textwrap.dedent("""
        import asyncio
        import os
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from yuxi.bootstrap import app as initializer
        from yuxi.infrastructure.postgres.manager import pg_manager

        async def run():
            pg_manager.initialize()
            await pg_manager.async_engine.dispose()
            pg_manager.async_engine = create_async_engine(
                os.environ["POSTGRES_URL"],
                connect_args={"server_settings": {"search_path": os.environ["APP_INIT_TEST_SCHEMA"]}},
            )
            pg_manager.AsyncSession = async_sessionmaker(pg_manager.async_engine, expire_on_commit=False)
            if os.environ["APP_INIT_OMIT_MODELS"] == "1":
                initializer.load_models = lambda: None
            await initializer.main()

        asyncio.run(run())
    """)
    for omit_models in ("1", "0"):
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", script],
            env={**os.environ, "APP_INIT_TEST_SCHEMA": schema, "APP_INIT_OMIT_MODELS": omit_models},
            capture_output=True,
            text=True,
            timeout=30,
        )
        if omit_models == "1":
            assert result.returncode != 0
            assert "InvalidRequestError" in result.stderr and "Project" in result.stderr
        else:
            assert result.returncode == 0, result.stderr
    assert await get_schema_versions(manager) == {"business": 1, "knowledge": 2}
    await persisted_facts(engine, root)


async def test_app_lock_covers_shared_writes_until_completion(isolated_app, monkeypatch):
    """第一个部署停在共享写入阶段时，第二个部署不能进入 Schema 阶段。"""
    _, engine, root = isolated_app
    entered = asyncio.Event()
    release = asyncio.Event()
    original_sync = app_init.init_builtin_skills
    original_schema = app_init.initialize_schema
    second_entered = asyncio.Event()
    schema_entries = 0

    async def observe_schema(manager):
        """观察第二个执行者是否进入受保护阶段。"""
        nonlocal schema_entries
        schema_entries += 1
        if schema_entries == 2:
            second_entered.set()
        await original_schema(manager)

    async def pause_shared_stage(session):
        """阻塞第一个发布，锁必须覆盖至持久内容同步完成。"""
        entered.set()
        await release.wait()
        return await original_sync(session)

    monkeypatch.setattr(app_init, "initialize_schema", observe_schema)
    monkeypatch.setattr(app_init, "init_builtin_skills", pause_shared_stage)
    first = asyncio.create_task(app_init.main())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=30)
        second = asyncio.create_task(app_init.main())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(second_entered.wait(), timeout=0.3)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=30)
        assert second_entered.is_set()
        await persisted_facts(engine, root)
    finally:
        release.set()
        for task in (first, second):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)


@pytest.mark.parametrize(
    ("saved", "tier"),
    [
        ({"backend": "hybrid-engine", "effort": "high"}, "advanced"),
        ({"backend": "vlm-engine", "effort": "high"}, "standard"),
        ({"backend": "hybrid-engine", "effort": "medium"}, "standard"),
        ({"backend": "vlm-engine"}, "standard"),
        ({"effort": "high"}, "advanced"),
        ({"backend": "old-unknown", "tier": "basic"}, "basic"),
    ],
)
async def test_mineru_upgrade_persists_tier_without_overwriting_admin_fields(isolated_app, saved, tier):
    """升级通过独立连接验证档位迁移、管理员字段保留及重跑幂等。"""
    import json

    manager, engine, _ = isolated_app
    await app_init.main()
    saved["server_url"] = "https://mineru.example/prefix"
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE config_options SET value=CAST(:value AS json), updated_by='migration-admin' WHERE key='mineru_ocr_host_opts'"),
            {"value": json.dumps(saved)},
        )
    await app_init.main()
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT value, updated_by, updated_at FROM config_options WHERE key='mineru_ocr_host_opts'"))).one()
    assert row.value == {"server_url": saved["server_url"], "tier": tier}
    assert row.updated_by == "migration-admin"
    await app_init.main()
    async with engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT value, updated_by, updated_at FROM config_options WHERE key='mineru_ocr_host_opts'"))
        ).one() == row
    assert await get_schema_versions(manager) == {"business": 1, "knowledge": 2}


async def test_invalid_legacy_mineru_config_fails_without_replacing_value(isolated_app):
    """不能将未知旧档位静默迁移为默认值；失败事务保留原配置。"""
    _, engine, _ = isolated_app
    await app_init.main()
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE config_options SET value='{\"backend\":\"unknown\"}' WHERE key='mineru_ocr_host_opts'"))
    with pytest.raises(ValueError, match="MinerU"):
        await app_init.main()
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT value FROM config_options WHERE key='mineru_ocr_host_opts'")) == {"backend": "unknown"}


async def test_retired_pp_structure_config_is_removed_without_redirecting_to_cloud(isolated_app):
    """删除失效服务配置，保留旧选择为显式错误，云端三模型共用既有凭据。"""
    from yuxi.modules.documents.service import resolve_parse_options

    manager, engine, _ = isolated_app
    await app_init.main()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO config_options (key, name, description, params, value) "
                "VALUES ('pp_structure_v3_ocr_host_opts', 'retired', '', '{}', "
                '\'{"server_url":"https://private.example.test"}\')'
            )
        )
        await conn.execute(
            text("UPDATE config_options SET value='{\"default_ocr_engine\":\"pp_structure_v3_ocr\"}' WHERE key='system_options'")
        )
        await conn.execute(
            text(
                'UPDATE config_options SET value=\'{"api_token":"fixture-token","api_url":"https://cloud.example.test/jobs"}\' '
                "WHERE key='paddleocr_api_opts'"
            )
        )
    await app_init.main()
    await app_init.main()
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM config_options WHERE key='pp_structure_v3_ocr_host_opts'")) == 0
        assert await conn.scalar(text("SELECT value FROM config_options WHERE key='system_options'")) == {
            "default_ocr_engine": "pp_structure_v3_ocr"
        }
    async with manager.get_async_session_context() as session:
        with pytest.raises(ValueError, match="不支持的 OCR 引擎: pp_structure_v3_ocr"):
            await resolve_parse_options(db=session)
        for engine_id in ("paddleocr_vl_1_6", "paddleocr_pp_ocrv6", "paddleocr_pp_structure_v3"):
            resolved = await resolve_parse_options({"ocr_engine": engine_id}, session)
            assert resolved.processor_kwargs == {"api_token": "fixture-token", "api_url": "https://cloud.example.test/jobs"}
