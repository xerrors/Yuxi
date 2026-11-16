"""App-init 使用的 PostgreSQL fresh baseline 与结构约束。"""

from contextlib import asynccontextmanager

from sqlalchemy import text

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.base import BusinessBase, KnowledgeBase
from yuxi.infrastructure.postgres.schema import SCHEMA_VERSION_TABLE
from yuxi.modules.agents.models.runs import AGENT_RUN_TERMINAL_STATUSES
from yuxi.modules.identity.models import IDENTITY_CHANGE_LOCK_KEY

AGENT_RUN_TERMINAL_STATUS_SQL = ", ".join(f"'{status}'" for status in AGENT_RUN_TERMINAL_STATUSES)

IDENTITY_PERMISSION_STATEMENTS = (
    f"""
    CREATE FUNCTION yuxi_guard_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'UPDATE' AND
            array_position(ARRAY['user','admin','superadmin'], NEW.role) <
            array_position(ARRAY['user','admin','superadmin'], OLD.role) THEN
            RAISE EXCEPTION '角色只允许提升，禁止降级' USING ERRCODE = '23514';
        END IF;
        IF OLD.role = 'superadmin' AND OLD.is_deleted = 0 THEN
            IF TG_OP = 'DELETE' OR NEW.is_deleted <> 0 THEN
                PERFORM pg_advisory_xact_lock({IDENTITY_CHANGE_LOCK_KEY});
                IF NOT EXISTS (SELECT 1 FROM users WHERE role = 'superadmin' AND is_deleted = 0 AND id <> OLD.id) THEN
                    RAISE EXCEPTION '必须至少保留一个有效系统管理员' USING ERRCODE = '23514';
                END IF;
            END IF;
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END $$
    """,
    "CREATE TRIGGER yuxi_identity_guard BEFORE UPDATE OR DELETE ON users FOR EACH ROW EXECUTE FUNCTION yuxi_guard_identity()",
)


KNOWLEDGE_SCHEMA_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_graph_extraction_status "
    "ON knowledge_chunks(kb_id, ((graph_extraction_details->>'status')))",
    "CREATE INDEX IF NOT EXISTS ix_knowledge_files_kb_status_file ON knowledge_files(kb_id, status, file_id)",
    "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_entities_vector_pending "
    "ON knowledge_graph_entities(kb_id, vector_status, vector_next_retry_at)",
    "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_triples_vector_pending "
    "ON knowledge_graph_triples(kb_id, vector_status, vector_next_retry_at)",
)

BUSINESS_SCHEMA_STATEMENTS = (
    "CREATE SEQUENCE IF NOT EXISTS agent_runs_execution_seq",
    "ALTER TABLE agent_runs ALTER COLUMN execution_seq SET DEFAULT nextval('agent_runs_execution_seq')",
    "CREATE INDEX IF NOT EXISTS ix_agent_inputs_pending_queue ON agent_inputs(thread_id, received_seq) WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS ix_messages_session_created_id ON messages(session_record_id, created_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_agent_runs_thread_execution_seq ON agent_runs(thread_id, execution_seq)",
    "CREATE INDEX IF NOT EXISTS ix_agent_runs_status_lease_expires ON agent_runs(status, lease_expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_background_jobs_status_lease_expires ON background_jobs(status, lease_expires_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_one_active_per_thread "
    "ON agent_runs(uid, agent_slug, thread_id) "
    f"WHERE status NOT IN ({AGENT_RUN_TERMINAL_STATUS_SQL})",
)


@asynccontextmanager
async def schema_migration_lock(manager):
    """用独立 PostgreSQL session 串行化 Schema 初始化进程。"""
    manager.require_initialized()
    async with manager.async_engine.connect() as conn:
        params = {"lock_scope": "yuxi:schema-migration"}
        await conn.execute(text("SELECT pg_advisory_lock(hashtextextended(:lock_scope, 0))"), params)
        await conn.commit()
        try:
            yield
        finally:
            unlocked = await conn.scalar(
                text("SELECT pg_advisory_unlock(hashtextextended(:lock_scope, 0))"),
                params,
            )
            await conn.commit()
            if unlocked is not True:
                await conn.close()
                raise RuntimeError("Failed to release Yuxi schema migration advisory lock")


async def create_schema_version_table(manager) -> None:
    """创建轻量 Schema 版本表；仅允许 Schema 初始化进程调用。"""
    manager.require_initialized()
    async with manager.async_engine.begin() as conn:
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
                    domain VARCHAR(32) PRIMARY KEY,
                    version INTEGER NOT NULL CHECK (version > 0),
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )


async def record_schema_version(manager, domain: str, version: int) -> None:
    """在当前 fresh baseline 完整成功后记录域版本。"""
    manager.require_initialized()
    async with manager.async_engine.begin() as conn:
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA_VERSION_TABLE} (domain, version, applied_at)
                VALUES (:domain, :version, CURRENT_TIMESTAMP)
                ON CONFLICT (domain) DO UPDATE
                SET version = EXCLUDED.version, applied_at = EXCLUDED.applied_at
                """
            ),
            {"domain": domain, "version": version},
        )


async def create_knowledge_tables(manager):
    """使用当前 ORM metadata 建立知识域 fresh baseline。"""
    from yuxi.infrastructure.postgres.models import load_models

    load_models()
    manager.require_initialized()
    async with manager.async_engine.begin() as conn:
        await conn.run_sync(KnowledgeBase.metadata.create_all)
    logger.info("PostgreSQL knowledge tables created")


async def create_business_tables(manager):
    """使用当前 ORM metadata 建立业务域 fresh baseline。"""
    from yuxi.infrastructure.postgres.models import load_models

    load_models()
    manager.require_initialized()
    async with manager.async_engine.begin() as conn:
        await conn.run_sync(BusinessBase.metadata.create_all)
        await conn.execute(text("INSERT INTO wps_cloud_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))
    logger.info("PostgreSQL business tables created")


async def ensure_knowledge_schema(manager):
    """建立不适合由 ORM 表达的知识域访问路径索引。"""
    manager.require_initialized()

    async with manager.async_engine.begin() as conn:
        for statement in KNOWLEDGE_SCHEMA_STATEMENTS:
            await conn.execute(text(statement))


async def ensure_business_schema(manager):
    """建立不适合由 ORM 表达的业务热路径索引。"""
    manager.require_initialized()

    async with manager.async_engine.begin() as conn:
        for statement in BUSINESS_SCHEMA_STATEMENTS:
            await conn.execute(text(statement))
        for statement in IDENTITY_PERMISSION_STATEMENTS:
            await conn.execute(text(statement))


async def drop_fresh_schema(manager) -> None:
    """删除未完成的 fresh baseline，使 app-init 可以从零重试。"""
    from yuxi.infrastructure.postgres.models import load_models

    load_models()
    manager.require_initialized()
    async with manager.async_engine.begin() as conn:
        # PostgreSQL 在删除循环 FK 时要求明确 CASCADE；仅删除已确认属于本次 fresh 初始化的表。
        preparer = conn.dialect.identifier_preparer
        for metadata in (BusinessBase.metadata, KnowledgeBase.metadata):
            for table in metadata.tables.values():
                await conn.execute(text(f"DROP TABLE IF EXISTS {preparer.quote(table.name)} CASCADE"))
        await conn.execute(text(f"DROP TABLE IF EXISTS {SCHEMA_VERSION_TABLE}"))
        for table_name in ("checkpoint_writes", "checkpoint_blobs", "checkpoints", "checkpoint_migrations"):
            await conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
        await conn.execute(text("DROP SEQUENCE IF EXISTS agent_runs_execution_seq"))
        await conn.execute(text("DROP FUNCTION IF EXISTS yuxi_guard_identity()"))
