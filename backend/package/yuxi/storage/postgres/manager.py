"""PostgreSQL 数据库管理器 - 支持知识库和业务数据"""

import json
import os
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from yuxi.storage.postgres.models_business import (
    AGENT_RUN_SHAPE_CONSTRAINT_NAME,
    AGENT_RUN_SHAPE_CONSTRAINT_SQL,
    AGENT_RUN_TERMINAL_STATUSES,
    UNVIEWED_RUN_MARKER,
)
from yuxi.storage.postgres.models_business import Base as BusinessBase
from yuxi.storage.postgres.models_knowledge import Base as KnowledgeBase
from yuxi.utils import logger
from yuxi.utils.singleton import SingletonMeta

AGENT_RUN_TERMINAL_STATUS_SQL = ", ".join(f"'{status}'" for status in AGENT_RUN_TERMINAL_STATUSES)
BUSINESS_SCHEMA_VERSION = 2
KNOWLEDGE_SCHEMA_VERSION = 2
SCHEMA_VERSION_TABLE = "yuxi_schema_migrations"
AGENT_RUN_LEASE_SCHEMA_STATEMENTS = (
    "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS worker_id VARCHAR(128)",
    "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITHOUT TIME ZONE",
    "CREATE INDEX IF NOT EXISTS ix_agent_runs_status_lease_expires ON agent_runs(status, lease_expires_at)",
)
AGENT_RUN_FACT_SCHEMA_STATEMENTS = (
    "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS manifest JSONB",
    "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS manifest_fingerprint VARCHAR(64)",
    "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS manifest_recorded_at TIMESTAMP WITHOUT TIME ZONE",
    """
    CREATE TABLE IF NOT EXISTS agent_run_attempts (
        id SERIAL PRIMARY KEY,
        run_id VARCHAR(64) NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        attempt_no INTEGER NOT NULL,
        worker_id VARCHAR(128) NOT NULL,
        started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        heartbeat_at TIMESTAMP WITHOUT TIME ZONE,
        lease_expires_at TIMESTAMP WITHOUT TIME ZONE,
        finished_at TIMESTAMP WITHOUT TIME ZONE,
        outcome VARCHAR(32),
        error_type VARCHAR(64),
        error_message TEXT,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
    )
    """,
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_run_attempts_run_attempt_no "
        "ON agent_run_attempts(run_id, attempt_no)"
    ),
    "CREATE INDEX IF NOT EXISTS ix_agent_run_attempts_open ON agent_run_attempts(run_id, finished_at)",
)
WORKDIR_PATH_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS projects (
        id VARCHAR(64) PRIMARY KEY,
        uid VARCHAR(64) NOT NULL CONSTRAINT fk_projects_uid_users REFERENCES users(uid) ON DELETE CASCADE,
        name VARCHAR(255),
        selection_status VARCHAR(20) NOT NULL,
        workdir_path VARCHAR(512) NOT NULL,
        directory_mode VARCHAR(20) NOT NULL,
        idempotency_key VARCHAR(128),
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_projects_id_uid UNIQUE (id, uid),
        CONSTRAINT uq_projects_uid_idempotency_key UNIQUE (uid, idempotency_key),
        CONSTRAINT ck_projects_selection_status CHECK (selection_status IN ('implicit', 'selectable')),
        CONSTRAINT ck_projects_directory_mode CHECK (directory_mode IN ('managed', 'linked'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_projects_uid ON projects(uid)",
    "CREATE INDEX IF NOT EXISTS ix_projects_selection_status ON projects(selection_status)",
    "ALTER TABLE IF EXISTS projects DROP CONSTRAINT IF EXISTS uq_projects_uid_workdir_path",
    "ALTER TABLE IF EXISTS projects ALTER COLUMN name DROP NOT NULL",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_projects_uid_users'
              AND conrelid = 'projects'::regclass
        ) THEN
            ALTER TABLE projects
            ADD CONSTRAINT fk_projects_uid_users
            FOREIGN KEY (uid) REFERENCES users(uid) ON DELETE CASCADE;
        END IF;
    END $$
    """,
    "ALTER TABLE IF EXISTS conversations ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)",
    "ALTER TABLE IF EXISTS conversations ADD COLUMN IF NOT EXISTS creation_request_id VARCHAR(64)",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_uid_creation_request_id "
        "ON conversations(uid, creation_request_id) WHERE creation_request_id IS NOT NULL"
    ),
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM conversations
            WHERE project_id IS NULL
        ) THEN
            RAISE EXCEPTION 'Conversation project_id requires storage-migrator cutover';
        END IF;
    END $$
    """,
    "ALTER TABLE IF EXISTS conversations ALTER COLUMN project_id SET NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_conversations_project_id ON conversations(project_id)",
    "ALTER TABLE IF EXISTS conversations DROP CONSTRAINT IF EXISTS ck_conversations_workdir_binding",
    "ALTER TABLE IF EXISTS conversations DROP COLUMN IF EXISTS workdir_path",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_conversations_project_uid'
              AND conrelid = 'conversations'::regclass
        ) THEN
            ALTER TABLE conversations
            ADD CONSTRAINT fk_conversations_project_uid
            FOREIGN KEY (project_id, uid) REFERENCES projects(id, uid);
        END IF;
    END $$
    """,
)
V071_WORKDIR_CUTOVER_STATEMENTS = (
    "ALTER TABLE IF EXISTS conversations ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)",
    """
    WITH bindings AS (
        SELECT
            child.uid,
            COALESCE(parent.thread_id, child.thread_id) AS owner_thread_id
        FROM conversations AS child
        LEFT JOIN subagent_threads AS relation ON relation.child_conversation_id = child.id
        LEFT JOIN conversations AS parent ON parent.id = relation.parent_conversation_id
    )
    INSERT INTO projects (
        id, uid, name, selection_status, workdir_path, directory_mode, idempotency_key, created_at, updated_at
    )
    SELECT DISTINCT
        (md5('project:' || uid || ':' || owner_thread_id)::uuid)::text,
        uid,
        NULL,
        'implicit',
        'projects/' || (md5(uid || ':' || owner_thread_id)::uuid)::text,
        'managed',
        NULL,
        timezone('utc', now()),
        timezone('utc', now())
    FROM bindings
    ON CONFLICT (id) DO NOTHING
    """,
    """
    UPDATE conversations AS child
    SET project_id = (md5(
        'project:' || owner.uid || ':' || owner.owner_thread_id
    )::uuid)::text
    FROM (
        SELECT
            current.id,
            current.uid,
            COALESCE(parent.thread_id, current.thread_id) AS owner_thread_id
        FROM conversations AS current
        LEFT JOIN subagent_threads AS relation ON relation.child_conversation_id = current.id
        LEFT JOIN conversations AS parent ON parent.id = relation.parent_conversation_id
    ) AS owner
    WHERE child.id = owner.id
      AND child.project_id IS NULL
    """,
    "ALTER TABLE IF EXISTS conversations ALTER COLUMN project_id SET NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_conversations_project_id ON conversations(project_id)",
)
KNOWLEDGE_FILE_TASK_OWNER_SCHEMA_STATEMENTS = (
    "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS processing_task_id VARCHAR(64)",
    "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS processing_owner VARCHAR(128)",
    "CREATE INDEX IF NOT EXISTS ix_knowledge_files_processing_task_id ON knowledge_files(processing_task_id)",
    """
    UPDATE knowledge_files
    SET status = CASE WHEN status = 'parsing' THEN 'error_parsing' ELSE 'error_indexing' END,
        error_message = 'service_interrupted: 旧执行实例中断，处理结果未知，请重试',
        processing_task_id = NULL,
        processing_owner = NULL,
        updated_at = timezone('utc', now())
    WHERE status IN ('parsing', 'indexing')
      AND processing_task_id IS NULL
    """,
)
TASK_DURABLE_SCHEMA_STATEMENTS = (
    "ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS recovery_strategy VARCHAR(16)",
    "UPDATE tasks SET recovery_strategy = 'fail' WHERE recovery_strategy IS NULL",
    "ALTER TABLE IF EXISTS tasks ALTER COLUMN recovery_strategy SET DEFAULT 'fail'",
    "ALTER TABLE IF EXISTS tasks ALTER COLUMN recovery_strategy SET NOT NULL",
    "ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS handler_version INTEGER",
    "UPDATE tasks SET handler_version = 0 WHERE handler_version IS NULL",
    "ALTER TABLE IF EXISTS tasks ALTER COLUMN handler_version SET DEFAULT 1",
    "ALTER TABLE IF EXISTS tasks ALTER COLUMN handler_version SET NOT NULL",
    "ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(64)",
    "ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS worker_id VARCHAR(128)",
    "ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITHOUT TIME ZONE",
    "ALTER TABLE IF EXISTS tasks ADD COLUMN IF NOT EXISTS timeout_seconds DOUBLE PRECISION",
    "UPDATE tasks SET timeout_seconds = 21600.0 WHERE timeout_seconds IS NULL",
    "ALTER TABLE IF EXISTS tasks ALTER COLUMN timeout_seconds SET DEFAULT 21600.0",
    "ALTER TABLE IF EXISTS tasks ALTER COLUMN timeout_seconds SET NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_tasks_status_lease_expires ON tasks(status, lease_expires_at)",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'uq_tasks_active_dedupe'
              AND conrelid = 'tasks'::regclass
        ) THEN
            ALTER TABLE tasks
            ADD CONSTRAINT uq_tasks_active_dedupe UNIQUE (type, dedupe_key);
        END IF;
    END $$
    """,
)
RUNTIME_SCOPE_SCHEMA_STATEMENTS = (
    "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS runtime_scope_id VARCHAR(64)",
    (
        "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS "
        "runtime_cleanup_pending BOOLEAN NOT NULL DEFAULT FALSE"
    ),
    """
    UPDATE agent_runs AS run
    SET runtime_scope_id = COALESCE(
        (
            SELECT parent.thread_id
            FROM subagent_threads AS relation
            JOIN conversations AS parent ON parent.id = relation.parent_conversation_id
            WHERE relation.id = run.subagent_thread_relation_id
        ),
        run.conversation_thread_id
    )
    WHERE run.runtime_scope_id IS NULL
    """,
    "ALTER TABLE IF EXISTS agent_runs ALTER COLUMN runtime_scope_id SET NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_agent_runs_runtime_scope_id ON agent_runs(runtime_scope_id)",
    ("CREATE INDEX IF NOT EXISTS ix_agent_runs_runtime_cleanup_pending ON agent_runs(runtime_cleanup_pending)"),
    f"""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = '{AGENT_RUN_SHAPE_CONSTRAINT_NAME}'
              AND conrelid = 'agent_runs'::regclass
        ) THEN
            BEGIN
                ALTER TABLE agent_runs
                ADD CONSTRAINT {AGENT_RUN_SHAPE_CONSTRAINT_NAME}
                CHECK ({AGENT_RUN_SHAPE_CONSTRAINT_SQL}) NOT VALID;
            EXCEPTION WHEN duplicate_object THEN
                NULL;
            END;
        END IF;
    END $$
    """,
)


class PostgresManager(metaclass=SingletonMeta):
    """PostgreSQL 数据库管理器 - 支持知识库和业务数据"""

    # 知识库 PostgreSQL URL 环境变量名
    KB_DATABASE_URL_ENV = "POSTGRES_URL"

    def __init__(self):
        self.async_engine = None
        self.AsyncSession = None
        self.langgraph_pool = None
        self.langgraph_checkpointer = None
        self._langgraph_checkpointer_setup = False
        self._initialized = False

    def initialize(self):
        """初始化数据库连接"""
        if self._initialized:
            return

        db_url = os.getenv(self.KB_DATABASE_URL_ENV)
        if not db_url:
            logger.error(
                f"环境变量 {self.KB_DATABASE_URL_ENV} 未设置，"
                "请在 docker-compose.yml 或 .env 中配置 PostgreSQL 连接字符串"
            )
            return

        try:
            # 创建异步 SQLAlchemy 引擎
            self.async_engine = create_async_engine(
                db_url,
                json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False),
                json_deserializer=json.loads,
                pool_pre_ping=True,
                pool_recycle=1800,
                pool_size=10,
                max_overflow=20,
            )

            # 创建异步会话工厂
            self.AsyncSession = async_sessionmaker(
                bind=self.async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            # ==========================================
            # 2. 为 LangGraph 专门初始化一个原生 psycopg_pool
            # ==========================================
            # ⚠️ 注意：psycopg 不认识 "+asyncpg" 这样的 SQLAlchemy 方言标识。
            # 如果你的 db_url 是 "postgresql+asyncpg://user:pwd@host/db"，
            # 需要把它清洗成标准的 "postgresql://user:pwd@host/db"
            langgraph_db_url = db_url.replace("+asyncpg", "").replace("+psycopg", "")

            # 创建 LangGraph 专属连接池
            self.langgraph_pool = AsyncConnectionPool(
                conninfo=langgraph_db_url,
                max_size=10,  # 根据你的 Agent 并发情况设置，通常 5-10 足够了
                kwargs={"autocommit": True},  # LangGraph Checkpoint 强依赖 autocommit
                check=AsyncConnectionPool.check_connection,
            )

            self._initialized = True
            logger.info(f"PostgreSQL manager initialized for knowledge base: {db_url.split('@')[0]}://***")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL manager: {e}")
            # 不抛出异常，允许应用启动，但在使用时会报错

    def _check_initialized(self):
        """检查是否已初始化"""
        if not self._initialized:
            raise RuntimeError("PostgreSQL manager not initialized. Please check configuration.")

    def get_langgraph_checkpointer(self) -> AsyncPostgresSaver:
        """获取当前进程共享的 PostgreSQL LangGraph checkpointer。"""
        self._check_initialized()
        if self.langgraph_pool is None:
            raise RuntimeError("PostgreSQL LangGraph connection pool is not initialized.")
        if self.langgraph_checkpointer is None:
            self.langgraph_checkpointer = AsyncPostgresSaver(self.langgraph_pool)
        return self.langgraph_checkpointer

    async def setup_langgraph_checkpointer(self) -> AsyncPostgresSaver:
        """跨进程串行创建 LangGraph checkpoint 表并返回共享 checkpointer。"""
        checkpointer = self.get_langgraph_checkpointer()
        if not self._langgraph_checkpointer_setup:
            async with self.langgraph_pool.connection() as connection:
                await connection.execute("SELECT pg_advisory_lock(94721802)")
                try:
                    await checkpointer.setup()
                finally:
                    try:
                        cursor = await connection.execute("SELECT pg_advisory_unlock(94721802)")
                        row = await cursor.fetchone()
                        if not row or row[0] is not True:
                            raise RuntimeError("Failed to release LangGraph checkpoint advisory lock")
                    except BaseException:
                        # Session 级锁不随事务回滚释放，解锁失败时必须销毁物理连接。
                        await connection.close()
                        raise
                self._langgraph_checkpointer_setup = True
                logger.info("LangGraph checkpoint tables verified/created")
        return checkpointer

    @asynccontextmanager
    async def schema_migration_lock(self):
        """用独立 PostgreSQL session 串行化唯一 Schema migrator。"""
        self._check_initialized()
        async with self.async_engine.connect() as conn:
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

    async def create_schema_version_table(self) -> None:
        """创建轻量 Schema 版本表；仅允许迁移器调用。"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
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

    async def get_schema_versions(self) -> dict[str, int]:
        """读取当前数据库已完成的 Yuxi Schema 版本。"""
        self._check_initialized()
        async with self.async_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT to_regclass(:table_name) IS NOT NULL"),
                {"table_name": SCHEMA_VERSION_TABLE},
            )
            if not exists:
                return {}
            rows = await conn.execute(text(f"SELECT domain, version FROM {SCHEMA_VERSION_TABLE}"))
            return {str(row.domain): int(row.version) for row in rows}

    async def record_schema_version(self, domain: str, version: int) -> None:
        """在对应域迁移完整成功后记录当前版本。"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
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

    async def require_current_schema(self, *, include_knowledge: bool) -> None:
        """只读校验运行进程需要的 Schema 域均为精确当前版本。"""
        versions = await self.get_schema_versions()
        required = {"business": BUSINESS_SCHEMA_VERSION}
        if include_knowledge:
            required["knowledge"] = KNOWLEDGE_SCHEMA_VERSION
        mismatches = [
            f"{domain}={versions.get(domain, 'missing')} (required {version})"
            for domain, version in required.items()
            if versions.get(domain) != version
        ]
        if mismatches:
            detail = ", ".join(mismatches)
            raise RuntimeError(f"Database schema migration is incomplete or incompatible: {detail}")

    async def create_knowledge_tables(self):
        """创建完整模式使用的知识与评估表。"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
            await conn.run_sync(KnowledgeBase.metadata.create_all)
        logger.info("PostgreSQL knowledge tables created/checked")

    async def create_business_tables(self):
        """创建所有业务数据表"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
            await conn.run_sync(BusinessBase.metadata.create_all)
        logger.info("PostgreSQL business tables created/checked")

    async def upgrade_business_schema_v1_to_v2(self) -> None:
        """为通用 Task 增加持久执行权、租约和去重字段。"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
            for statement in TASK_DURABLE_SCHEMA_STATEMENTS:
                await conn.execute(text(statement))

    async def upgrade_knowledge_schema_v1_to_v2(self) -> None:
        """为知识文件处理中间态增加 Durable Task attempt owner。"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
            for statement in KNOWLEDGE_FILE_TASK_OWNER_SCHEMA_STATEMENTS:
                await conn.execute(text(statement))

    async def drop_tables(self):
        """删除所有表（慎用！）"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
            await conn.run_sync(BusinessBase.metadata.drop_all)
            await conn.run_sync(KnowledgeBase.metadata.drop_all)
        logger.info("PostgreSQL tables dropped")

    async def ensure_knowledge_schema(self):
        """确保知识库 schema 包含所有必要字段"""
        self._check_initialized()
        stmts = [
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS embedding_model_spec VARCHAR(512)",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS llm_model_spec VARCHAR(512)",
            "ALTER TABLE IF EXISTS knowledge_bases DROP COLUMN IF EXISTS embed_info",
            "ALTER TABLE IF EXISTS knowledge_bases DROP COLUMN IF EXISTS llm_info",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS query_params JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS additional_params JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS share_config JSONB",
            (
                "UPDATE knowledge_bases SET share_config = jsonb_build_object("
                "'version', 2, "
                "'read_scope', COALESCE(NULLIF(share_config, '{}'::jsonb), "
                '\'{"access_level": "global", "department_ids": [], "user_uids": []}\'::jsonb), '
                "'manage_scope', COALESCE(NULLIF(share_config, '{}'::jsonb), "
                '\'{"access_level": "global", "department_ids": [], "user_uids": []}\'::jsonb)) '
                "WHERE share_config IS NULL OR share_config->>'version' IS DISTINCT FROM '2'"
            ),
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS mindmap JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS mindmap_file_ids JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS mindmap_metadata JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS sample_questions JSONB",
            "ALTER TABLE IF EXISTS knowledge_bases ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS parent_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS original_filename VARCHAR(512)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS file_type VARCHAR(64)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS path VARCHAR(1024)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS minio_url VARCHAR(1024)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS markdown_file VARCHAR(1024)",
            "ALTER TABLE IF EXISTS knowledge_files DROP COLUMN IF EXISTS source_preview_file",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS status VARCHAR(32)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS content_hash VARCHAR(128)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS file_size BIGINT",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS chunk_count INTEGER DEFAULT 0",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS token_count BIGINT DEFAULT 0",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS content_type VARCHAR(64)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS processing_params JSONB",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS is_folder BOOLEAN",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS error_message TEXT",
            *KNOWLEDGE_FILE_TASK_OWNER_SCHEMA_STATEMENTS,
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS created_by VARCHAR(64)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS updated_by VARCHAR(64)",
            "ALTER TABLE IF EXISTS knowledge_files ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS evaluation_datasets ADD COLUMN IF NOT EXISTS created_by VARCHAR(64)",
            "ALTER TABLE IF EXISTS evaluation_datasets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS evaluation_datasets ADD COLUMN IF NOT EXISTS build_metadata JSONB",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS name VARCHAR(255)",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS metrics JSONB",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS overall_score DOUBLE PRECISION",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS total_items INTEGER",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS completed_items INTEGER",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ",
            "ALTER TABLE IF EXISTS evaluation_runs ADD COLUMN IF NOT EXISTS created_by VARCHAR(64)",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS gold_chunk_ids JSONB",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS gold_answer TEXT",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS generated_answer TEXT",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS retrieved_chunks JSONB",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS metrics JSONB",
            "ALTER TABLE IF EXISTS evaluation_run_items ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ",
            """
            CREATE TABLE IF NOT EXISTS evaluation_datasets (
                id SERIAL PRIMARY KEY,
                dataset_id VARCHAR(64) NOT NULL UNIQUE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                item_count INTEGER DEFAULT 0,
                has_gold_chunks BOOLEAN DEFAULT FALSE,
                has_gold_answers BOOLEAN DEFAULT FALSE,
                build_metadata JSONB,
                created_by VARCHAR(64),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS evaluation_dataset_items (
                id SERIAL PRIMARY KEY,
                item_id VARCHAR(64) NOT NULL UNIQUE,
                dataset_id VARCHAR(64) NOT NULL REFERENCES evaluation_datasets(dataset_id) ON DELETE CASCADE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                item_index INTEGER NOT NULL,
                query_text TEXT NOT NULL,
                gold_chunk_ids JSONB,
                gold_answer TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_evaluation_dataset_items_dataset_index UNIQUE (dataset_id, item_index)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS evaluation_runs (
                id SERIAL PRIMARY KEY,
                run_id VARCHAR(64) NOT NULL UNIQUE,
                name VARCHAR(255) NOT NULL,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                dataset_id VARCHAR(64) REFERENCES evaluation_datasets(dataset_id) ON DELETE SET NULL,
                status VARCHAR(32) DEFAULT 'running',
                retrieval_config JSONB,
                metrics JSONB,
                overall_score DOUBLE PRECISION,
                total_items INTEGER DEFAULT 0,
                completed_items INTEGER DEFAULT 0,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                created_by VARCHAR(64)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS evaluation_run_items (
                id SERIAL PRIMARY KEY,
                run_id VARCHAR(64) NOT NULL REFERENCES evaluation_runs(run_id) ON DELETE CASCADE,
                dataset_item_id VARCHAR(64) REFERENCES evaluation_dataset_items(item_id) ON DELETE SET NULL,
                item_index INTEGER NOT NULL,
                query_text TEXT NOT NULL,
                gold_chunk_ids JSONB,
                gold_answer TEXT,
                generated_answer TEXT,
                retrieved_chunks JSONB,
                metrics JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_evaluation_run_items_run_index UNIQUE (run_id, item_index)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id SERIAL PRIMARY KEY,
                chunk_id VARCHAR(128) NOT NULL UNIQUE,
                file_id VARCHAR(64) NOT NULL REFERENCES knowledge_files(file_id) ON DELETE CASCADE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                start_char_pos INTEGER,
                end_char_pos INTEGER,
                start_token_pos INTEGER,
                end_token_pos INTEGER,
                graph_structure_indexed BOOLEAN NOT NULL DEFAULT FALSE,
                graph_indexed BOOLEAN DEFAULT FALSE,
                graph_extraction_details JSONB NOT NULL
                    DEFAULT jsonb_build_object('status', 'pending', 'attempt_count', 0),
                ent_ids JSONB,
                tags JSONB,
                extraction_result JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "ALTER TABLE IF EXISTS knowledge_chunks ADD COLUMN IF NOT EXISTS extraction_result JSONB",
            (
                "ALTER TABLE IF EXISTS knowledge_chunks ADD COLUMN IF NOT EXISTS "
                "graph_structure_indexed BOOLEAN NOT NULL DEFAULT FALSE"
            ),
            (
                "ALTER TABLE IF EXISTS knowledge_chunks ADD COLUMN IF NOT EXISTS "
                "graph_extraction_details JSONB NOT NULL "
                "DEFAULT jsonb_build_object('status', 'pending', 'attempt_count', 0)"
            ),
            (
                "ALTER TABLE IF EXISTS knowledge_chunks ALTER COLUMN graph_extraction_details "
                "SET DEFAULT jsonb_build_object('status', 'pending', 'attempt_count', 0)"
            ),
            ("ALTER TABLE IF EXISTS knowledge_chunks ALTER COLUMN graph_structure_indexed SET DEFAULT FALSE"),
            (
                "UPDATE knowledge_chunks SET graph_structure_indexed = TRUE "
                "WHERE graph_indexed IS TRUE AND graph_structure_indexed IS NOT TRUE"
            ),
            (
                "UPDATE knowledge_chunks SET graph_extraction_details = jsonb_build_object('status', 'succeeded') "
                "WHERE (graph_extraction_details IS NULL "
                "OR graph_extraction_details->>'status' = 'pending') "
                "AND (extraction_result IS NOT NULL OR graph_structure_indexed IS TRUE OR graph_indexed IS TRUE)"
            ),
            (
                "UPDATE knowledge_chunks SET graph_extraction_details = "
                "jsonb_build_object('status', 'pending', 'attempt_count', 0) "
                "WHERE graph_extraction_details IS NULL"
            ),
            """
            CREATE TABLE IF NOT EXISTS knowledge_graph_entities (
                id SERIAL PRIMARY KEY,
                entity_id VARCHAR(64) NOT NULL UNIQUE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                normalized_name VARCHAR(512) NOT NULL,
                label VARCHAR(128) NOT NULL,
                name VARCHAR(512) NOT NULL,
                attributes JSONB,
                vector_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                vector_attempt_count INTEGER NOT NULL DEFAULT 0,
                vector_last_error TEXT,
                vector_next_retry_at TIMESTAMPTZ,
                vector_locked_until TIMESTAMPTZ,
                vector_lock_token VARCHAR(32),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_knowledge_graph_entities_identity UNIQUE (kb_id, normalized_name, label)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_graph_entity_mentions (
                id SERIAL PRIMARY KEY,
                entity_id VARCHAR(64) NOT NULL REFERENCES knowledge_graph_entities(entity_id) ON DELETE CASCADE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                file_id VARCHAR(64) NOT NULL REFERENCES knowledge_files(file_id) ON DELETE CASCADE,
                chunk_id VARCHAR(128) NOT NULL REFERENCES knowledge_chunks(chunk_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_knowledge_graph_entity_mentions_entity_chunk UNIQUE (entity_id, chunk_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_graph_triples (
                id SERIAL PRIMARY KEY,
                triple_id VARCHAR(64) NOT NULL UNIQUE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                source_entity_id VARCHAR(64) NOT NULL REFERENCES knowledge_graph_entities(entity_id) ON DELETE CASCADE,
                target_entity_id VARCHAR(64) NOT NULL REFERENCES knowledge_graph_entities(entity_id) ON DELETE CASCADE,
                relation_type VARCHAR(256) NOT NULL,
                content TEXT NOT NULL,
                vector_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                vector_attempt_count INTEGER NOT NULL DEFAULT 0,
                vector_last_error TEXT,
                vector_next_retry_at TIMESTAMPTZ,
                vector_locked_until TIMESTAMPTZ,
                vector_lock_token VARCHAR(32),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS knowledge_graph_triple_mentions (
                id SERIAL PRIMARY KEY,
                triple_id VARCHAR(64) NOT NULL REFERENCES knowledge_graph_triples(triple_id) ON DELETE CASCADE,
                kb_id VARCHAR(80) NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                file_id VARCHAR(64) NOT NULL REFERENCES knowledge_files(file_id) ON DELETE CASCADE,
                chunk_id VARCHAR(128) NOT NULL REFERENCES knowledge_chunks(chunk_id) ON DELETE CASCADE,
                text TEXT,
                extractor_type VARCHAR(128),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_knowledge_graph_triple_mentions_triple_chunk UNIQUE (triple_id, chunk_id)
            )
            """,
            "ALTER TABLE IF EXISTS knowledge_bases ALTER COLUMN kb_id TYPE VARCHAR(80)",
            "ALTER TABLE IF EXISTS knowledge_graph_entities ADD COLUMN IF NOT EXISTS vector_status VARCHAR(16)",
            (
                "ALTER TABLE IF EXISTS knowledge_graph_entities ADD COLUMN IF NOT EXISTS "
                "vector_attempt_count INTEGER NOT NULL DEFAULT 0"
            ),
            "ALTER TABLE IF EXISTS knowledge_graph_entities ADD COLUMN IF NOT EXISTS vector_last_error TEXT",
            (
                "ALTER TABLE IF EXISTS knowledge_graph_entities ADD COLUMN IF NOT EXISTS "
                "vector_next_retry_at TIMESTAMPTZ"
            ),
            ("ALTER TABLE IF EXISTS knowledge_graph_entities ADD COLUMN IF NOT EXISTS vector_locked_until TIMESTAMPTZ"),
            ("ALTER TABLE IF EXISTS knowledge_graph_entities ADD COLUMN IF NOT EXISTS vector_lock_token VARCHAR(32)"),
            (
                "UPDATE knowledge_graph_entities AS entity SET vector_status = CASE WHEN EXISTS ("
                "SELECT 1 FROM knowledge_graph_entity_mentions AS mention "
                "JOIN knowledge_chunks AS chunk ON chunk.chunk_id = mention.chunk_id "
                "WHERE mention.entity_id = entity.entity_id AND chunk.graph_indexed IS NOT TRUE"
                ") THEN 'pending' ELSE 'indexed' END WHERE entity.vector_status IS NULL"
            ),
            "ALTER TABLE IF EXISTS knowledge_graph_entities ALTER COLUMN vector_status SET DEFAULT 'pending'",
            "ALTER TABLE IF EXISTS knowledge_graph_entities ALTER COLUMN vector_status SET NOT NULL",
            ("ALTER TABLE IF EXISTS knowledge_graph_entities ALTER COLUMN vector_attempt_count SET DEFAULT 0"),
            "ALTER TABLE IF EXISTS knowledge_graph_triples ADD COLUMN IF NOT EXISTS vector_status VARCHAR(16)",
            (
                "ALTER TABLE IF EXISTS knowledge_graph_triples ADD COLUMN IF NOT EXISTS "
                "vector_attempt_count INTEGER NOT NULL DEFAULT 0"
            ),
            "ALTER TABLE IF EXISTS knowledge_graph_triples ADD COLUMN IF NOT EXISTS vector_last_error TEXT",
            ("ALTER TABLE IF EXISTS knowledge_graph_triples ADD COLUMN IF NOT EXISTS vector_next_retry_at TIMESTAMPTZ"),
            ("ALTER TABLE IF EXISTS knowledge_graph_triples ADD COLUMN IF NOT EXISTS vector_locked_until TIMESTAMPTZ"),
            "ALTER TABLE IF EXISTS knowledge_graph_triples ADD COLUMN IF NOT EXISTS vector_lock_token VARCHAR(32)",
            (
                "UPDATE knowledge_graph_triples AS triple SET vector_status = CASE WHEN EXISTS ("
                "SELECT 1 FROM knowledge_graph_triple_mentions AS mention "
                "JOIN knowledge_chunks AS chunk ON chunk.chunk_id = mention.chunk_id "
                "WHERE mention.triple_id = triple.triple_id AND chunk.graph_indexed IS NOT TRUE"
                ") THEN 'pending' ELSE 'indexed' END WHERE triple.vector_status IS NULL"
            ),
            "ALTER TABLE IF EXISTS knowledge_graph_triples ALTER COLUMN vector_status SET DEFAULT 'pending'",
            "ALTER TABLE IF EXISTS knowledge_graph_triples ALTER COLUMN vector_status SET NOT NULL",
            ("ALTER TABLE IF EXISTS knowledge_graph_triples ALTER COLUMN vector_attempt_count SET DEFAULT 0"),
            "ALTER TABLE IF EXISTS knowledge_files ALTER COLUMN kb_id TYPE VARCHAR(80)",
            "ALTER TABLE IF EXISTS evaluation_datasets ALTER COLUMN kb_id TYPE VARCHAR(80)",
            "ALTER TABLE IF EXISTS evaluation_dataset_items ALTER COLUMN kb_id TYPE VARCHAR(80)",
            "ALTER TABLE IF EXISTS evaluation_runs ALTER COLUMN kb_id TYPE VARCHAR(80)",
            "CREATE INDEX IF NOT EXISTS idx_kb_type ON knowledge_bases(kb_type)",
            "CREATE INDEX IF NOT EXISTS idx_kb_name ON knowledge_bases(name)",
            "CREATE INDEX IF NOT EXISTS idx_kf_kb_id ON knowledge_files(kb_id)",
            "CREATE INDEX IF NOT EXISTS idx_kf_kb_filename ON knowledge_files(kb_id, filename)",
            "CREATE INDEX IF NOT EXISTS idx_kf_parent ON knowledge_files(parent_id)",
            "CREATE INDEX IF NOT EXISTS idx_kf_status ON knowledge_files(status)",
            "CREATE INDEX IF NOT EXISTS idx_kf_hash ON knowledge_files(content_hash)",
            # 虚拟目录分组索引：按路径首段聚合，避免大知识库全表扫描 + 磁盘排序
            (
                "CREATE INDEX IF NOT EXISTS idx_kf_kb_parent_segment ON knowledge_files "
                "(kb_id, parent_id, split_part(filename, '/', 1)) WHERE strpos(filename, '/') > 0"
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_kf_kb_parent_flat ON knowledge_files (kb_id, parent_id) "
                "WHERE strpos(filename, '/') = 0 AND filename IS NOT NULL AND filename <> ''"
            ),
            "CREATE INDEX IF NOT EXISTS ix_evaluation_datasets_kb_id ON evaluation_datasets(kb_id)",
            (
                "CREATE INDEX IF NOT EXISTS ix_evaluation_dataset_items_dataset_index "
                "ON evaluation_dataset_items(dataset_id, item_index)"
            ),
            "CREATE INDEX IF NOT EXISTS ix_evaluation_dataset_items_kb_id ON evaluation_dataset_items(kb_id)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_runs_kb_id ON evaluation_runs(kb_id)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_runs_status ON evaluation_runs(status)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_runs_started ON evaluation_runs(started_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_evaluation_run_items_run_index ON evaluation_run_items(run_id, item_index)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_chunks_chunk_id ON knowledge_chunks(chunk_id)",
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_file_id ON knowledge_chunks(file_id)",
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_kb_id ON knowledge_chunks(kb_id)",
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_graph_indexed ON knowledge_chunks(graph_indexed)",
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_graph_structure_indexed "
                "ON knowledge_chunks(graph_structure_indexed)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_graph_extraction_status "
                "ON knowledge_chunks(kb_id, ((graph_extraction_details->>'status')))"
            ),
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_graph_entities_entity_id "
                "ON knowledge_graph_entities(entity_id)"
            ),
            "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_entities_kb_id ON knowledge_graph_entities(kb_id)",
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_entities_vector_pending "
                "ON knowledge_graph_entities(kb_id, vector_status, vector_next_retry_at)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_entity_mentions_kb_id "
                "ON knowledge_graph_entity_mentions(kb_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_entity_mentions_file_id "
                "ON knowledge_graph_entity_mentions(file_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_entity_mentions_chunk_id "
                "ON knowledge_graph_entity_mentions(chunk_id)"
            ),
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_graph_triples_triple_id "
                "ON knowledge_graph_triples(triple_id)"
            ),
            "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_triples_kb_id ON knowledge_graph_triples(kb_id)",
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_triples_vector_pending "
                "ON knowledge_graph_triples(kb_id, vector_status, vector_next_retry_at)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_triple_mentions_kb_id "
                "ON knowledge_graph_triple_mentions(kb_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_triple_mentions_file_id "
                "ON knowledge_graph_triple_mentions(file_id)"
            ),
            (
                "CREATE INDEX IF NOT EXISTS ix_knowledge_graph_triple_mentions_chunk_id "
                "ON knowledge_graph_triple_mentions(chunk_id)"
            ),
        ]

        async with self.async_engine.begin() as conn:
            for stmt in stmts:
                await conn.execute(text(stmt))

    async def ensure_business_schema(self):
        """确保业务 schema 包含后续新增字段（运行时 schema 演进）。"""
        self._check_initialized()
        stmts = [
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS tool_dependencies JSONB DEFAULT '[]'::jsonb",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS mcp_dependencies JSONB DEFAULT '[]'::jsonb",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS skill_dependencies JSONB DEFAULT '[]'::jsonb",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS version VARCHAR(64)",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS source_type VARCHAR(32) NOT NULL DEFAULT 'upload'",
            (
                "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS share_config JSONB NOT NULL "
                'DEFAULT \'{"access_level": "user", "department_ids": [], "user_uids": []}\'::jsonb'
            ),
            "ALTER TABLE IF EXISTS skills ALTER COLUMN share_config TYPE JSONB USING share_config::jsonb",
            (
                "UPDATE skills SET share_config = jsonb_build_object("
                "'version', 2, 'read_scope', CASE WHEN share_config = '{}'::jsonb THEN jsonb_build_object("
                "'access_level', 'user', 'department_ids', '[]'::jsonb, "
                "'user_uids', jsonb_build_array(created_by)) ELSE share_config END, "
                "'manage_scope', NULL) "
                "WHERE share_config IS NOT NULL AND share_config->>'version' IS DISTINCT FROM '2'"
            ),
            "ALTER TABLE IF EXISTS skills ALTER COLUMN share_config DROP DEFAULT",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS content_hash VARCHAR(128)",
            "ALTER TABLE IF EXISTS conversations ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE IF EXISTS conversations ADD COLUMN IF NOT EXISTS last_viewed_run_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS mcp_servers ADD COLUMN IF NOT EXISTS env JSONB",
            """
            CREATE TABLE IF NOT EXISTS agent_envs (
                id SERIAL PRIMARY KEY,
                uid VARCHAR NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
                env JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_agent_envs_uid UNIQUE (uid)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_config (
                id SERIAL PRIMARY KEY,
                uid VARCHAR NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
                enable_memory BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_user_config_uid UNIQUE (uid)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS agents (
                id SERIAL PRIMARY KEY,
                slug VARCHAR(80) NOT NULL UNIQUE,
                backend_id VARCHAR(64) NOT NULL,
                name VARCHAR(100) NOT NULL,
                description TEXT,
                icon VARCHAR(255),
                pics JSONB NOT NULL DEFAULT '[]'::jsonb,
                config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                share_config JSONB NOT NULL DEFAULT '{}'::jsonb,
                is_default BOOLEAN NOT NULL DEFAULT FALSE,
                is_subagent BOOLEAN NOT NULL DEFAULT FALSE,
                created_by VARCHAR(64),
                updated_by VARCHAR(64),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS backend_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS share_config JSONB NOT NULL DEFAULT '{}'::jsonb",
            "ALTER TABLE IF EXISTS agents ALTER COLUMN share_config TYPE JSONB USING share_config::jsonb",
            (
                "UPDATE agents SET share_config = jsonb_build_object("
                "'version', 2, 'read_scope', CASE WHEN share_config = '{}'::jsonb THEN "
                '\'{"access_level": "global", "department_ids": [], "user_uids": []}\'::jsonb '
                "ELSE share_config END, 'manage_scope', NULL) "
                "WHERE share_config IS NOT NULL AND share_config->>'version' IS DISTINCT FROM '2'"
            ),
            "ALTER TABLE IF EXISTS agents ALTER COLUMN share_config DROP DEFAULT",
            "ALTER TABLE IF EXISTS agents ADD COLUMN IF NOT EXISTS is_subagent BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE IF EXISTS user_config ADD COLUMN IF NOT EXISTS enable_memory BOOLEAN NOT NULL DEFAULT FALSE",
            """
            UPDATE cli_auth_sessions
            SET api_key_id = NULL
            WHERE api_key_id IN (
                SELECT id FROM api_keys WHERE user_id IS NULL
            )
            """,
            "DELETE FROM api_keys WHERE user_id IS NULL",
            "ALTER TABLE IF EXISTS api_keys ALTER COLUMN user_id SET NOT NULL",
            "ALTER TABLE IF EXISTS api_keys ADD COLUMN IF NOT EXISTS request_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS api_keys ADD COLUMN IF NOT EXISTS intent_hash VARCHAR(64)",
            "ALTER TABLE IF EXISTS api_keys ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMP WITHOUT TIME ZONE",
            """
            UPDATE api_keys AS api_key
            SET is_enabled = FALSE,
                revoked_at = COALESCE(api_key.revoked_at, users.deleted_at, CURRENT_TIMESTAMP)
            FROM users
            WHERE api_key.user_id = users.id
              AND users.is_deleted <> 0
              AND api_key.revoked_at IS NULL
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_api_keys_request_id ON api_keys(request_id)",
            "CREATE INDEX IF NOT EXISTS ix_api_keys_revoked_at ON api_keys(revoked_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_agents_slug ON agents(slug)",
            "CREATE INDEX IF NOT EXISTS ix_agents_backend_id ON agents(backend_id)",
            "CREATE INDEX IF NOT EXISTS ix_agents_is_subagent ON agents(is_subagent)",
            "CREATE INDEX IF NOT EXISTS ix_agents_created_by ON agents(created_by)",
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_default
            ON agents(is_default)
            WHERE is_default IS TRUE
            """,
            """
            CREATE TABLE IF NOT EXISTS config_options (
                id SERIAL PRIMARY KEY,
                key VARCHAR(100) NOT NULL UNIQUE,
                name VARCHAR(100) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                params JSONB NOT NULL DEFAULT '{}'::jsonb,
                value JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_by VARCHAR(100),
                updated_by VARCHAR(100),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_config_options_key ON config_options(key)",
            """
            CREATE TABLE IF NOT EXISTS model_providers (
                id SERIAL PRIMARY KEY,
                provider_id VARCHAR(100) NOT NULL UNIQUE,
                display_name VARCHAR(100) NOT NULL,
                provider_type VARCHAR(32) NOT NULL DEFAULT 'openai',
                default_protocol VARCHAR(64),
                base_url VARCHAR(500) NOT NULL,
                embedding_base_url VARCHAR(500),
                rerank_base_url VARCHAR(500),
                models_endpoint VARCHAR(200),
                embedding_models_endpoint VARCHAR(200),
                rerank_models_endpoint VARCHAR(200),
                api_key_env VARCHAR(128),
                api_key VARCHAR(500),
                capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
                enabled_models JSONB NOT NULL DEFAULT '[]'::jsonb,
                headers_json JSONB,
                extra_json JSONB,
                is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
                created_by VARCHAR(100),
                updated_by VARCHAR(100),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS subagent_threads (
                id SERIAL PRIMARY KEY,
                uid VARCHAR(64) NOT NULL,
                parent_conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                child_conversation_id INTEGER NOT NULL UNIQUE REFERENCES conversations(id) ON DELETE CASCADE,
                child_thread_id VARCHAR(64) NOT NULL UNIQUE,
                subagent_slug VARCHAR(64) NOT NULL,
                created_by_run_id VARCHAR(64) NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            *WORKDIR_PATH_SCHEMA_STATEMENTS,
            "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS agent_slug VARCHAR(64)",
            "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS conversation_thread_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS created_by_run_id VARCHAR(64)",
            "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS subagent_thread_relation_id INTEGER",
            "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS source VARCHAR(32) NOT NULL DEFAULT 'chat'",
            "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS channel VARCHAR(32) NOT NULL DEFAULT 'web'",
            "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS external_id VARCHAR(128)",
            *AGENT_RUN_LEASE_SCHEMA_STATEMENTS,
            *AGENT_RUN_FACT_SCHEMA_STATEMENTS,
            (
                "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS "
                "origin_metadata JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS "
                "token_usage JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            (
                "ALTER TABLE IF EXISTS agent_run_requests ADD COLUMN IF NOT EXISTS "
                "channel VARCHAR(32) NOT NULL DEFAULT 'web'"
            ),
            "ALTER TABLE IF EXISTS agent_run_requests ADD COLUMN IF NOT EXISTS external_id VARCHAR(128)",
            (
                "ALTER TABLE IF EXISTS agent_run_requests ADD COLUMN IF NOT EXISTS "
                "origin_metadata JSONB NOT NULL DEFAULT '{}'::jsonb"
            ),
            "ALTER TABLE IF EXISTS subagent_threads ADD COLUMN IF NOT EXISTS subagent_slug VARCHAR(64)",
            "ALTER TABLE IF EXISTS subagent_threads ADD COLUMN IF NOT EXISTS created_by_run_id VARCHAR(64)",
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'agent_runs'
                      AND column_name = 'agent_id'
                ) THEN
                    EXECUTE '
                        UPDATE agent_runs
                        SET agent_slug = agent_id
                        WHERE agent_slug IS NULL
                          AND agent_id IS NOT NULL
                    ';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'agent_runs'
                      AND column_name = 'thread_id'
                ) THEN
                    EXECUTE '
                        UPDATE agent_runs
                        SET conversation_thread_id = thread_id
                        WHERE conversation_thread_id IS NULL
                          AND thread_id IS NOT NULL
                    ';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'agent_runs'
                      AND column_name = 'parent_agent_run_id'
                ) OR EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'agent_runs'
                      AND column_name = 'parent_run_id'
                ) THEN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'agent_runs'
                          AND column_name = 'parent_agent_run_id'
                    ) AND EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'agent_runs'
                          AND column_name = 'parent_run_id'
                    ) THEN
                        EXECUTE '
                            UPDATE agent_runs
                            SET created_by_run_id = COALESCE(parent_agent_run_id, parent_run_id)
                            WHERE created_by_run_id IS NULL
                              AND COALESCE(parent_agent_run_id, parent_run_id) IS NOT NULL
                        ';
                    ELSIF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'agent_runs'
                          AND column_name = 'parent_agent_run_id'
                    ) THEN
                        EXECUTE '
                            UPDATE agent_runs
                            SET created_by_run_id = parent_agent_run_id
                            WHERE created_by_run_id IS NULL
                              AND parent_agent_run_id IS NOT NULL
                        ';
                    ELSE
                        EXECUTE '
                            UPDATE agent_runs
                            SET created_by_run_id = parent_run_id
                            WHERE created_by_run_id IS NULL
                              AND parent_run_id IS NOT NULL
                        ';
                    END IF;
                END IF;
            END $$;
            """,
            *RUNTIME_SCOPE_SCHEMA_STATEMENTS,
            """
            UPDATE subagent_threads st
            SET subagent_slug = c.agent_id
            FROM conversations c
            WHERE st.subagent_slug IS NULL
              AND c.id = st.child_conversation_id
              AND c.agent_id IS NOT NULL
            """,
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'subagent_threads'
                      AND column_name = 'created_by_parent_run_id'
                ) THEN
                    EXECUTE '
                        UPDATE subagent_threads
                        SET created_by_run_id = created_by_parent_run_id::VARCHAR
                        WHERE created_by_run_id IS NULL
                          AND created_by_parent_run_id IS NOT NULL
                    ';
                END IF;
            END $$;
            """,
            """
            UPDATE subagent_threads st
            SET created_by_run_id = child_run.created_by_run_id
            FROM (
                SELECT DISTINCT ON (subagent_thread_relation_id)
                    subagent_thread_relation_id,
                    created_by_run_id
                FROM agent_runs
                WHERE run_type = 'subagent'
                  AND subagent_thread_relation_id IS NOT NULL
                  AND created_by_run_id IS NOT NULL
                ORDER BY subagent_thread_relation_id, created_at ASC, id ASC
            ) child_run
            WHERE st.created_by_run_id IS NULL
              AND child_run.subagent_thread_relation_id = st.id
            """,
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM subagent_threads WHERE subagent_slug IS NULL) THEN
                    ALTER TABLE subagent_threads ALTER COLUMN subagent_slug SET NOT NULL;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM subagent_threads WHERE created_by_run_id IS NULL) THEN
                    ALTER TABLE subagent_threads ALTER COLUMN created_by_run_id SET NOT NULL;
                END IF;
            END $$;
            """,
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS agent_id",
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS thread_id",
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS parent_run_id",
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS parent_agent_run_id",
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS resumed_from_run_id",
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS invoked_by_run_id",
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS subagent_thread_id",
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS resume_request_id",
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS resume_idempotency_key",
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS checkpoint_thread_id",
            "ALTER TABLE IF EXISTS agent_runs DROP COLUMN IF EXISTS execution_scope_id",
            "ALTER TABLE IF EXISTS subagent_threads DROP COLUMN IF EXISTS subagent_agent_id",
            "ALTER TABLE IF EXISTS subagent_threads DROP COLUMN IF EXISTS created_by_parent_run_id",
            "ALTER TABLE IF EXISTS subagent_threads DROP COLUMN IF EXISTS created_by_tool_call_id",
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_uid_created ON agent_runs(uid, created_at DESC)",
            """
            CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation_thread_created
            ON agent_runs(conversation_thread_id, created_at DESC)
            """,
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_status_updated ON agent_runs(status, updated_at)",
            """
            CREATE INDEX IF NOT EXISTS ix_agent_runs_subagent_thread_relation_id
            ON agent_runs(subagent_thread_relation_id)
            """,
            "CREATE INDEX IF NOT EXISTS ix_subagent_threads_uid ON subagent_threads(uid)",
            """
            CREATE INDEX IF NOT EXISTS ix_subagent_threads_parent_conversation
            ON subagent_threads(parent_conversation_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_subagent_threads_subagent_slug
            ON subagent_threads(subagent_slug)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_subagent_threads_created_by_run_id
            ON subagent_threads(created_by_run_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_agent_runs_created_by_run_created
            ON agent_runs(created_by_run_id, created_at DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_agent_runs_subagent_lookup
            ON agent_runs(uid, conversation_thread_id, run_type, created_at DESC)
            """,
            f"""
            WITH duplicated_active_runs AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY uid, agent_slug, conversation_thread_id
                        ORDER BY created_at DESC NULLS LAST, id DESC
                    ) AS active_rank
                FROM agent_runs
                WHERE status NOT IN ({AGENT_RUN_TERMINAL_STATUS_SQL})
                  AND uid IS NOT NULL
                  AND agent_slug IS NOT NULL
                  AND conversation_thread_id IS NOT NULL
            )
            UPDATE agent_runs ar
            SET status = 'failed',
                error_type = COALESCE(ar.error_type, 'active_run_migration_conflict'),
                error_message = COALESCE(
                    ar.error_message,
                    '旧库存在同一用户、智能体和线程的重复活跃 AgentRun，迁移时已保留最新一条并终结本记录。'
                ),
                finished_at = COALESCE(ar.finished_at, NOW()),
                updated_at = NOW()
            FROM duplicated_active_runs dup
            WHERE ar.id = dup.id
              AND dup.active_rank > 1
            """,
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_one_active_per_thread
            ON agent_runs(uid, agent_slug, conversation_thread_id)
            WHERE status NOT IN ({AGENT_RUN_TERMINAL_STATUS_SQL})
            """,
            "CREATE INDEX IF NOT EXISTS ix_conversations_is_pinned ON conversations(is_pinned)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_model_providers_provider_id ON model_providers(provider_id)",
            "CREATE INDEX IF NOT EXISTS ix_model_providers_is_enabled ON model_providers(is_enabled)",
            """
            CREATE TABLE IF NOT EXISTS agent_run_requests (
                id SERIAL PRIMARY KEY,
                request_id VARCHAR(64) NOT NULL,
                uid VARCHAR(64) NOT NULL,
                agent_slug VARCHAR(64) NOT NULL,
                conversation_thread_id VARCHAR(64) NOT NULL,
                source VARCHAR(32) NOT NULL DEFAULT 'chat',
                queue_policy VARCHAR(16) NOT NULL DEFAULT 'enqueue',
                status VARCHAR(32) NOT NULL DEFAULT 'queued',
                input_message_id INTEGER NOT NULL REFERENCES messages(id),
                dispatched_run_id VARCHAR(64) REFERENCES agent_runs(id),
                input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                dispatched_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_agent_run_requests_request_id ON agent_run_requests(request_id)",
            """
            CREATE INDEX IF NOT EXISTS ix_agent_run_requests_queue
            ON agent_run_requests(uid, agent_slug, conversation_thread_id, status, created_at, id)
            """,
            "CREATE INDEX IF NOT EXISTS ix_agent_run_requests_dispatched_run_id ON agent_run_requests(dispatched_run_id)",  # noqa: E501
            *TASK_DURABLE_SCHEMA_STATEMENTS,
        ]
        async with self.async_engine.begin() as conn:
            # 历史未绑定用户的 API Key 会在下方迁移语句里被静默删除，先计数告警
            # 便于运维凭据失效时回溯；DELETE 之后无法再查询这些 Key。
            try:
                unbound_keys_result = await conn.execute(text("SELECT count(*) FROM api_keys WHERE user_id IS NULL"))
                unbound_keys_count = int(unbound_keys_result.scalar() or 0)
                if unbound_keys_count > 0:
                    logger.warning(
                        f"Schema migration will delete {unbound_keys_count} unbound API key(s) "
                        "(user_id IS NULL). These keys were previously allowed via dept-admin/superadmin "
                        "fallback and will stop authenticating after this migration."
                    )
            except Exception as exc:
                logger.warning(f"Failed to count unbound api_keys before migration: {exc}")

            for stmt in stmts:
                await conn.execute(text(stmt))

            # 一次性回填：历史线程按各自最新顶层 Run 视为已读；新建线程由 repository 写入哨兵值，不受本回填影响。
            # 先用轻量 EXISTS 探测是否还有待回填的行，避免每次启动都对 agent_runs 做全表 DISTINCT ON 聚合；
            # 探测失败时保守地按需要回填处理，行为与探测前一致。
            needs_backfill = True
            try:
                probe = await conn.execute(
                    text("SELECT EXISTS (SELECT 1 FROM conversations WHERE last_viewed_run_id IS NULL)")
                )
                needs_backfill = bool(probe.scalar())
            except Exception as exc:
                logger.warning(f"Failed to probe last_viewed_run_id backfill status, running unconditionally: {exc}")

            if needs_backfill:
                await conn.execute(
                    text(
                        "UPDATE conversations c SET last_viewed_run_id = r.run_id "
                        "FROM ("
                        "  SELECT DISTINCT ON (conversation_thread_id) conversation_thread_id AS thread_id, "
                        "id AS run_id "
                        "  FROM agent_runs "
                        "  WHERE run_type IN ('chat', 'resume') "
                        "  ORDER BY conversation_thread_id, created_at DESC, id DESC"
                        ") r "
                        "WHERE c.thread_id = r.thread_id AND c.last_viewed_run_id IS NULL"
                    )
                )
                # 没有 chat/resume Run 的历史会话（如 agent_call / agent_evaluation 调用、
                # 从未真正对话过的线程）写入未读哨兵，使上面的探测条件在首次回填后自然收敛，
                # 避免每次启动都重复对 agent_runs 做全表聚合。
                await conn.execute(
                    text("UPDATE conversations SET last_viewed_run_id = :marker WHERE last_viewed_run_id IS NULL"),
                    {"marker": UNVIEWED_RUN_MARKER},
                )

    @property
    def is_postgresql(self) -> bool:
        """检查是否是 PostgreSQL 数据库"""
        if not self._initialized:
            return False
        return self.async_engine.dialect.name == "postgresql"

    async def get_async_session(self) -> AsyncSession:
        """获取异步数据库会话"""
        self.initialize()  # 确保已初始化
        return self.AsyncSession()

    @asynccontextmanager
    async def get_async_session_context(self):
        """获取异步数据库会话的上下文管理器"""
        self.initialize()  # 确保已初始化
        session = self.AsyncSession()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"PostgreSQL async operation failed: {e}")
            raise
        finally:
            await session.close()

    async def close(self):
        """关闭引擎"""
        if self.async_engine:
            await self.async_engine.dispose()

        if self.langgraph_pool:
            await self.langgraph_pool.close()

        self.async_engine = None
        self.AsyncSession = None
        self.langgraph_pool = None
        self.langgraph_checkpointer = None
        self._langgraph_checkpointer_setup = False
        self._initialized = False

    async def async_check_first_run(self):
        """检查是否首次运行（异步版本）- 检查用户表是否有数据"""
        from sqlalchemy import func, select

        self._check_initialized()
        async with self.get_async_session_context() as session:
            from yuxi.storage.postgres.models_business import User

            result = await session.execute(select(func.count(User.id)))
            count = result.scalar()
            return count == 0

    async def commit(self):
        """提交当前会话"""
        self._check_initialized()
        async with self.get_async_session_context():
            pass  # commit is automatic in context manager


# 创建全局 PostgreSQL 管理器实例
pg_manager = PostgresManager()
