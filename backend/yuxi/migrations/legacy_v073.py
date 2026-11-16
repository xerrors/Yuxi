"""维护窗口内显式转换本地 v0.7.3 的 9/4 数据库；默认只读审计。"""

import argparse
import asyncio
import json
from copy import deepcopy
from dataclasses import fields

from sqlalchemy import DateTime, Integer, MetaData, inspect, literal, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateSequence, CreateTable

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.schema import (
    BUSINESS_SCHEMA_STATEMENTS,
    IDENTITY_PERMISSION_STATEMENTS,
    KNOWLEDGE_SCHEMA_STATEMENTS,
    schema_migration_lock,
)
from yuxi.modules.agents.runtime.agent_backends import get_agent_backend
from yuxi.modules.agents.runtime.tool_approval import DEFAULT_TOOL_APPROVAL_MODE, normalize_tool_approval_mode

SOURCE_VERSIONS = {"business": 9, "knowledge": 4}
TARGET_VERSIONS = {"business": 3, "knowledge": 3}
RENAMED_TABLES = {"sessions": "conversations", "background_jobs": "tasks"}
LEGACY_ONLY_TABLES = {
    "conversations",
    "subagent_threads",
    "agent_run_requests",
    "tasks",
    "conversation_stats",
    "message_feedbacks",
    "operation_logs",
    "tool_calls",
    "yuxi_schema_migrations",
}


# manager 为目标数据库管理器，返回不含业务正文或凭据的只读审计摘要。
async def audit_legacy_schema(manager) -> dict:
    """检查结构版本与在途工作，不接受只改版本号的迁移。"""
    versions = await get_schema_versions(manager)
    report = {"versions": versions, "blocking": [], "counts": {}}
    if versions == TARGET_VERSIONS:
        return report
    if versions != SOURCE_VERSIONS:
        report["blocking"].append("仅支持本地 v0.7.3 Schema business=9/knowledge=4")
        return report
    async with manager.async_engine.connect() as conn:
        tables = set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))
        required = {
            "conversations",
            "subagent_threads",
            "agent_runs",
            "agent_run_requests",
            "messages",
            "tasks",
            "users",
            "knowledge_files",
            "knowledge_document_versions",
            "scheduled_agent_runs",
            "message_feedbacks",
        }
        missing = required - tables
        if missing:
            report["blocking"].append(f"缺少旧结构表：{', '.join(sorted(missing))}")
            return report
        for name in sorted(required | {"users", "knowledge_files", "knowledge_document_versions"}):
            report["counts"][name] = await conn.scalar(text(f'SELECT count(*) FROM "{name}"'))
        checks = {
            "存在未结束 Run，必须先由旧版本收敛": (
                "SELECT count(*) FROM agent_runs WHERE status NOT IN ('completed','failed','cancelled','interrupted')"
            ),
            "存在未消费请求，必须先由旧版本处理或取消": "SELECT count(*) FROM agent_run_requests WHERE status = 'queued'",
            "存在未结束后台任务，必须先由旧版本收敛": "SELECT count(*) FROM tasks WHERE status IN ('pending','running')",
            "存在在途定时派发": "SELECT count(*) FROM scheduled_agent_runs WHERE status = 'dispatching'",
            "存在尚未发布的父子版本": "SELECT count(*) FROM knowledge_document_versions WHERE status = 'staging'",
            "存在待清理运行环境": "SELECT count(*) FROM agent_runs WHERE runtime_cleanup_pending",
        }
        for reason, query in checks.items():
            if await conn.scalar(text(query)):
                report["blocking"].append(reason)
    return report


# manager 为数据库管理器；返回迁移前摘要，全部 DDL 与转换在同一事务完成。
async def upgrade_legacy_schema(manager) -> dict:
    """归档旧表后重建当前约束，任一转换失败时恢复整个旧结构。"""
    metadatas = load_models()
    async with schema_migration_lock(manager):
        report = await audit_legacy_schema(manager)
        if report["versions"] == TARGET_VERSIONS:
            return report
        if report["blocking"]:
            raise RuntimeError("；".join(report["blocking"]))
        async with manager.async_engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            await conn.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            schema = await conn.scalar(text("SELECT current_schema()"))
            archive = f"{schema}_v073_archive"
            if len(archive) > 63:
                raise RuntimeError("Schema 名过长，无法生成无歧义的归档名称")
            quote = conn.dialect.identifier_preparer.quote_identifier
            if await conn.scalar(text("SELECT 1 FROM pg_namespace WHERE nspname = :name"), {"name": archive}):
                raise RuntimeError("旧库归档 Schema 已存在，请先核查上次迁移，禁止覆盖")
            legacy = MetaData()
            await conn.run_sync(lambda sync: legacy.reflect(sync))
            current = {table.name: table for metadata in metadatas for table in metadata.tables.values()}
            owned = sorted(set(legacy.tables) & (set(current) | LEGACY_ONLY_TABLES))
            # 排他锁阻止审计后继续写旧表；锁后再次核对在途状态。
            await conn.execute(text("LOCK TABLE " + ", ".join(quote(name) for name in owned) + " IN ACCESS EXCLUSIVE MODE"))
            for table, condition in (
                ("agent_runs", "status NOT IN ('completed','failed','cancelled','interrupted') OR runtime_cleanup_pending"),
                ("agent_run_requests", "status = 'queued'"),
                ("tasks", "status IN ('pending','running')"),
                ("scheduled_agent_runs", "status = 'dispatching'"),
                ("knowledge_document_versions", "status = 'staging'"),
            ):
                if await conn.scalar(text(f"SELECT count(*) FROM {quote(table)} WHERE {condition}")):
                    raise RuntimeError("锁定后发现新的在途工作，迁移已拒绝")
            await conn.execute(text(f"CREATE SCHEMA {quote(archive)}"))
            for name in owned:
                await conn.execute(text(f"ALTER TABLE {quote(name)} SET SCHEMA {quote(archive)}"))
            # 旧表、索引及 SERIAL 序列整体归档；外部对象、向量和 checkpoint 不在此处修改。
            await conn.execute(CreateSequence(current["agent_runs"].c.execution_seq.default))
            for table in current.values():
                await conn.execute(CreateTable(table, include_foreign_key_constraints=[]))
            await conn.execute(
                text(
                    "CREATE TABLE yuxi_schema_migrations (domain varchar(32) PRIMARY KEY, "
                    "version integer NOT NULL CHECK (version > 0), applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            await _create_runtime_maps(conn, archive)
            for name, table in current.items():
                if name == "agent_turns":
                    continue
                source = RENAMED_TABLES.get(name, name)
                if source in legacy.tables:
                    await _copy_table(conn, table, legacy.tables[source], archive)
            await _copy_turns_and_inputs(conn, archive)
            await _restore_session_configs(conn, archive, apply=True)
            await _finish_constraints(conn, current)
            # 所有目标约束通过后才写版本；保留归档中的旧版本事实。
            await conn.execute(text("INSERT INTO yuxi_schema_migrations (domain, version) VALUES ('business', 3), ('knowledge', 3)"))
            for name in owned:
                target = next((new for new, old in RENAMED_TABLES.items() if old == name), name)
                if target in current:
                    old_count = await conn.scalar(text(f"SELECT count(*) FROM {quote(archive)}.{quote(name)}"))
                    new_count = await conn.scalar(text(f"SELECT count(*) FROM {quote(target)}"))
                    if old_count != new_count:
                        raise RuntimeError(f"表 {name} 迁移前后记录数不一致")
        return report


# manager 为当前数据库管理器，apply 控制是否提交；返回候选与修复行数。
async def repair_legacy_session_configs(manager, *, apply: bool = False) -> dict:
    """显式修复已迁移旧会话的缺失快照，不覆盖新会话或已有配置。"""
    if await get_schema_versions(manager) != TARGET_VERSIONS:
        raise RuntimeError("配置修复只支持已迁移的 business=3/knowledge=3")
    async with schema_migration_lock(manager):
        async with manager.async_engine.begin() as conn:
            schema = await conn.scalar(text("SELECT current_schema()"))
            archive = f"{schema}_v073_archive"
            if not await conn.scalar(text("SELECT 1 FROM pg_namespace WHERE nspname=:name"), {"name": archive}):
                raise RuntimeError("缺少 v0.7.3 归档，无法恢复历史配置来源")
            return await _restore_session_configs(conn, archive, apply=apply)


# conn 为迁移事务，archive 为旧配置来源；返回候选与更新行数，不输出配置正文。
async def _restore_session_configs(conn, archive: str, *, apply: bool) -> dict:
    """按旧会话自身的最后一次执行、元数据和 Agent 配置重建完整快照。"""
    source = conn.dialect.identifier_preparer.quote_identifier(archive)
    rows = (
        (
            await conn.execute(
                text(f"""
            SELECT s.id, a.backend_id, a.config_json, c.extra_metadata, r.input_payload
            FROM sessions s
            JOIN {source}.conversations c ON c.id=s.id AND c.thread_id=s.thread_id AND c.uid=s.uid AND c.agent_id=s.agent_id
            LEFT JOIN {source}.agents a ON a.slug=c.agent_id
            LEFT JOIN LATERAL (
                SELECT input_payload FROM {source}.agent_runs
                WHERE conversation_id=c.id ORDER BY created_at DESC, id DESC LIMIT 1
            ) r ON true
            WHERE s.config_snapshot IS NULL OR s.config_snapshot::jsonb='null'::jsonb
            ORDER BY s.id FOR UPDATE OF s
            """)
            )
        )
        .mappings()
        .all()
    )
    # 只使用归档中的系统默认值，避免用升级后的配置冒充旧会话设置。
    option_row = (
        await conn.execute(text(f"SELECT value, params FROM {source}.config_options WHERE key='system_options'"))
    ).mappings().first()
    options = (option_row or {}).get("value") or {}
    option_params = (option_row or {}).get("params") or {}
    default_model = options.get("default_model")
    if not default_model:
        default_model = next(
            (
                field.get("default")
                for field in option_params.get("fields", [])
                if field.get("key") == "default_model" and isinstance(field.get("default"), str)
            ),
            None,
        )
    snapshots = []
    for row in rows:
        backend_id = "ChatbotAgent" if row["backend_id"] == "SubAgentBackend" else row["backend_id"]
        context = get_agent_backend(backend_id).context_schema()
        config = deepcopy((row["config_json"] or {}).get("context") or {})
        # v0.7.3 的 null 表示所有可见资源；空列表仍表示不选择任何资源。
        for name in ("tools", "knowledges", "mcps", "skills"):
            if config.get(name) is None:
                config[name] = "all"
        if config.get("preload_skills") is None:
            config["preload_skills"] = []
        context.update_config(config)
        metadata = row["extra_metadata"] or {}
        payload = row["input_payload"] or {}
        model = next(
            (
                value.strip()
                for value in (payload.get("model_spec"), metadata.get("model_spec"), context.model, default_model)
                if isinstance(value, str) and value.strip()
            ),
            None,
        )
        if model is None:
            raise RuntimeError("历史会话缺少可追溯的模型配置，配置修复已拒绝")
        context.model = model
        snapshot = {item.name: deepcopy(getattr(context, item.name)) for item in fields(context) if item.metadata.get("configurable", True)}
        snapshot["tool_approval_mode"] = normalize_tool_approval_mode(
            payload.get("tool_approval_mode")
            or metadata.get("tool_approval_mode")
            or config.get("tool_approval_mode")
            or DEFAULT_TOOL_APPROVAL_MODE
        )
        snapshots.append({"id": row["id"], "snapshot": json.dumps(snapshot)})
    if apply and snapshots:
        await conn.execute(text("UPDATE sessions SET config_snapshot=CAST(:snapshot AS json) WHERE id=:id"), snapshots)
    return {"candidates": len(snapshots), "repaired": len(snapshots) if apply else 0}


# conn 为迁移事务连接，archive 为已引用转义的旧表 Schema 来源。
async def _create_runtime_maps(conn, archive: str) -> None:
    """按实际子线程与 resume 关系生成会话树、Run 和 Turn 归属。"""
    source = conn.dialect.identifier_preparer.quote_identifier(archive)
    await conn.execute(
        text(f"""
        CREATE TEMP TABLE legacy_sessions ON COMMIT DROP AS
        WITH RECURSIVE tree AS (
            SELECT c.id, c.thread_id, c.thread_id AS root_id, NULL::varchar(64) AS parent_id,
                   'root'::text AS name, '/root'::text AS path, NULL::varchar(64) AS creator
            FROM {source}.conversations c
            WHERE NOT EXISTS (SELECT 1 FROM {source}.subagent_threads s WHERE s.child_conversation_id=c.id)
            UNION ALL
            SELECT c.id, c.thread_id, t.root_id, t.thread_id,
                   'legacy_' || s.id, t.path || '/legacy_' || s.id, s.created_by_run_id
            FROM tree t JOIN {source}.subagent_threads s ON s.parent_conversation_id=t.id
            JOIN {source}.conversations c ON c.id=s.child_conversation_id
        ) SELECT * FROM tree
    """)
    )
    if await conn.scalar(text(f"SELECT count(*) FROM {source}.conversations")) != await conn.scalar(
        text("SELECT count(*) FROM legacy_sessions")
    ):
        raise RuntimeError("旧子线程关系含循环或缺失父会话，不能推测归属")
    await conn.execute(
        text(f"""
        CREATE TEMP TABLE legacy_runs ON COMMIT DROP AS
        WITH RECURSIVE chains AS (
            SELECT r.id, 'turn_' || md5(r.id) AS turn_id
            FROM {source}.agent_runs r WHERE r.run_type <> 'resume'
            UNION ALL
            SELECT r.id, c.turn_id FROM chains c
            JOIN {source}.agent_runs r ON r.created_by_run_id=c.id AND r.run_type='resume'
            JOIN {source}.agent_runs p ON p.id=c.id AND p.conversation_id=r.conversation_id
        )
        SELECT r.id, c.turn_id, row_number() OVER (ORDER BY r.created_at, r.id) AS execution_seq,
               s.thread_id, s.root_id
        FROM chains c JOIN {source}.agent_runs r ON r.id=c.id
        JOIN legacy_sessions s ON s.id=r.conversation_id
    """)
    )
    if await conn.scalar(text(f"SELECT count(*) FROM {source}.agent_runs")) != await conn.scalar(text("SELECT count(*) FROM legacy_runs")):
        raise RuntimeError("旧 Run 缺少会话或 resume 父链，不能猜测 Turn")


# table/source 为新旧 SQLAlchemy 表，返回值为空；所有 SQL 标识符由 dialect 转义。
async def _copy_table(conn, table, source, archive: str) -> None:
    """复制同语义列，显式转换变更列；无法表达的旧字段留在完整归档中。"""
    quote = conn.dialect.identifier_preparer.quote_identifier
    table_name = table.name
    overrides = {
        "sessions": {
            "tree_root_thread_id": "s.root_id",
            "parent_thread_id": "s.parent_id",
            "cooperation_name": "s.name",
            "cooperation_path": "s.path",
            "created_by_run_id": "s.creator",
        },
        "agents": {
            "visibility": (
                "CASE WHEN o.created_by IS NULL OR "
                "COALESCE(o.share_config::jsonb->'read_scope','null'::jsonb) <> 'null'::jsonb OR "
                "COALESCE(o.share_config::jsonb->'manage_scope','null'::jsonb) <> 'null'::jsonb "
                "THEN 'shared' ELSE 'private' END"
            ),
            "is_builtin": "false",
            "backend_id": "CASE WHEN o.backend_id='SubAgentBackend' THEN 'ChatbotAgent' ELSE o.backend_id END",
        },
        "agent_runs": {
            "execution_seq": "r.execution_seq",
            "thread_id": "r.thread_id",
            "runtime_scope_id": "r.root_id",
            "turn_id": "r.turn_id",
            "session_record_id": "o.conversation_id",
            "run_type": "CASE WHEN o.run_type='subagent' THEN 'chat' ELSE o.run_type END",
            "resume_from_run_id": "CASE WHEN o.run_type='resume' THEN o.created_by_run_id END",
        },
        "messages": {
            "session_record_id": "o.conversation_id",
            "turn_id": "r.turn_id",
            "content": "CASE WHEN o.message_type='tool_audit' THEN '' ELSE o.content END",
            "extra_metadata": (
                "CASE WHEN o.message_type='tool_audit' AND NOT "
                "(COALESCE(o.extra_metadata::jsonb,'{}'::jsonb) ? 'output') THEN "
                "COALESCE(o.extra_metadata::jsonb,'{}'::jsonb) || jsonb_build_object('output',o.content) "
                "ELSE o.extra_metadata::jsonb END"
            ),
        },
        "scheduled_agent_runs": {"input_id": "o.request_id", "session_title": "o.conversation_title"},
        "knowledge_files": {"processing_job_id": "o.processing_task_id", "projection_status": "'ready'"},
        "evaluation_run_items": {
            "dataset_id": (
                f"(SELECT e.dataset_id FROM {quote(archive)}.evaluation_runs e WHERE e.run_id=o.run_id AND o.dataset_item_id IS NOT NULL)"
            )
        },
    }.get(table_name, {})
    columns, expressions = [], []
    for column in table.columns:
        expression = overrides.get(column.name)
        if expression is None and column.name in source.c:
            expression = f"o.{quote(column.name)}"
            old_type = source.c[column.name].type
            if isinstance(column.type, DateTime) and column.type.timezone and isinstance(old_type, DateTime) and not old_type.timezone:
                expression += " AT TIME ZONE 'UTC'"
        if expression is None:
            if column.default is not None and column.default.is_scalar:
                expression = str(literal(column.default.arg).compile(dialect=conn.dialect, compile_kwargs={"literal_binds": True}))
            elif column.nullable or column.server_default is not None or column.identity is not None:
                continue
            else:
                raise RuntimeError(f"{table_name}.{column.name} 缺少已审阅的旧字段映射")
        columns.append(quote(column.name))
        expressions.append(expression)
    joins = ""
    if table_name == "sessions":
        joins = " JOIN legacy_sessions s ON s.id=o.id"
    elif table_name == "agent_runs":
        joins = " JOIN legacy_runs r ON r.id=o.id"
    elif table_name == "messages":
        joins = " LEFT JOIN legacy_runs r ON r.id=o.run_id"
    await conn.execute(
        text(
            f"INSERT INTO {quote(table_name)} ({', '.join(columns)}) "
            f"SELECT {', '.join(expressions)} FROM {quote(archive)}.{quote(source.name)} o{joins}"
        )
    )


# conn 为迁移事务，archive 保存未改动的旧请求、输入和执行事实。
async def _copy_turns_and_inputs(conn, archive: str) -> None:
    """从真实 Run 链生成终态 Turn，从旧请求保留独立 Input 与历史回执。"""
    source = conn.dialect.identifier_preparer.quote_identifier(archive)
    await conn.execute(
        text("""
        INSERT INTO agent_turns (id, thread_id, uid, status, current_run_id, result_run_id, created_at, finished_at)
        SELECT DISTINCT ON (r.turn_id) r.turn_id, r.thread_id, r.uid,
            CASE WHEN r.status='interrupted' THEN 'failed' ELSE r.status END,
            r.id, r.id, min(r.created_at) OVER (PARTITION BY r.turn_id), r.finished_at
        FROM agent_runs r ORDER BY r.turn_id, r.execution_seq DESC
    """)
    )
    await conn.execute(
        text(f"""
        INSERT INTO agent_inputs (id, received_seq, thread_id, uid, agent_slug, kind, status, turn_id,
            consumed_run_id, messages, attachment_file_ids, input_payload, source, channel,
            external_id, origin_metadata, created_at, consumed_at, cancelled_at)
        SELECT q.request_id, q.id, q.conversation_thread_id, q.uid, q.agent_slug,
            CASE WHEN q.queue_policy='steer' THEN 'steer' ELSE 'follow_up' END,
            CASE WHEN q.dispatched_run_id IS NOT NULL THEN 'consumed' ELSE 'cancelled' END,
            r.turn_id, q.dispatched_run_id,
            jsonb_build_array(jsonb_build_object('role', m.role, 'content',
                CASE WHEN jsonb_typeof(m.extra_metadata::jsonb->'raw_message'->'content')='array'
                    THEN m.extra_metadata::jsonb->'raw_message'->'content'
                    ELSE jsonb_build_array(jsonb_build_object('type','text','text',m.content)) ||
                        CASE WHEN m.image_content IS NOT NULL THEN jsonb_build_array(jsonb_build_object(
                            'type','image_url','image_url',jsonb_build_object('url','data:image/png;base64,' || m.image_content)))
                        ELSE '[]'::jsonb END END,
                'metadata', COALESCE(m.extra_metadata::jsonb, '{{}}'::jsonb))),
            COALESCE(m.extra_metadata::jsonb->'attachment_file_ids', '[]'::jsonb),
            q.input_payload, q.source, q.channel, q.external_id, q.origin_metadata,
            q.created_at AT TIME ZONE 'UTC',
            CASE WHEN q.dispatched_run_id IS NOT NULL THEN COALESCE(q.dispatched_at,q.updated_at) AT TIME ZONE 'UTC' END,
            CASE WHEN q.dispatched_run_id IS NULL THEN q.updated_at AT TIME ZONE 'UTC' END
        FROM {source}.agent_run_requests q JOIN {source}.messages m ON m.id=q.input_message_id
        LEFT JOIN legacy_runs r ON r.id=q.dispatched_run_id ORDER BY q.id
    """)
    )
    await conn.execute(
        text(f"""
        UPDATE messages m SET source_input_id=q.request_id, input_position=0, received_at=i.created_at,
            run_id=i.consumed_run_id, turn_id=i.turn_id
        FROM {source}.agent_run_requests q JOIN agent_inputs i ON i.id=q.request_id
        WHERE m.id=q.input_message_id AND i.status='consumed' AND m.role='user'
    """)
    )
    # 旧协议的请求哈希不能冒充新协议；保留键占用以拒绝不兼容重放。
    await conn.execute(
        text("""
        INSERT INTO agent_input_receipts (id, idempotency_key, uid, thread_id, event_type, intent_hash, input_id, created_at)
        SELECT id, id, uid, thread_id, 'legacy.v073.input', md5(id)||md5(id), id, created_at FROM agent_inputs
    """)
    )
    await conn.execute(
        text(f"""
        INSERT INTO agent_feedback_records (id, run_id, source, rating, reason, created_at, updated_at)
        SELECT DISTINCT ON (m.run_id) 'feedback_' || f.id, m.run_id, 'user', f.rating, f.reason,
            f.created_at AT TIME ZONE 'UTC', f.created_at AT TIME ZONE 'UTC'
        FROM {source}.message_feedbacks f JOIN messages m ON m.id=f.message_id
        JOIN agent_runs r ON r.id=m.run_id AND r.uid=f.uid
        WHERE m.run_id IS NOT NULL ORDER BY m.run_id, f.created_at DESC, f.id DESC
    """)
    )
    if await conn.scalar(text("SELECT count(*) FROM agent_inputs")) != await conn.scalar(
        text(f"SELECT count(*) FROM {source}.agent_run_requests")
    ):
        raise RuntimeError("旧请求缺少输入消息或执行记录，迁移拒绝丢弃请求")


# conn 为事务连接，tables 为当前模型表映射；完成后数据库可执行当前约束与自增写入。
async def _finish_constraints(conn, tables) -> None:
    """回读复合外键与唯一性，并重置新序列，禁止后续写入复用历史 ID。"""
    quote = conn.dialect.identifier_preparer.quote_identifier
    for table in tables.values():
        for constraint in table.foreign_key_constraints:
            await conn.execute(AddConstraint(constraint, isolate_from_table=False))
        for index in table.indexes:
            if index._ddl_if is None or index._ddl_if.dialect == "postgresql":
                await conn.execute(CreateIndex(index))
        for column in table.columns:
            if column.identity is not None or (
                column.primary_key and isinstance(column.type, Integer) and column.autoincrement in (True, "auto")
            ):
                seq = await conn.scalar(
                    text("SELECT pg_get_serial_sequence(:table, :column)"), {"table": table.name, "column": column.name}
                )
                if seq:
                    await conn.execute(
                        text(f"SELECT setval(:seq, COALESCE(max({quote(column.name)}), 1), count(*) > 0) FROM {quote(table.name)}"),
                        {"seq": seq},
                    )
    await conn.execute(text("SELECT setval('agent_runs_execution_seq', COALESCE(max(execution_seq),1), count(*) > 0) FROM agent_runs"))
    for statement in BUSINESS_SCHEMA_STATEMENTS + KNOWLEDGE_SCHEMA_STATEMENTS + IDENTITY_PERMISSION_STATEMENTS:
        await conn.execute(text(statement))


# 无参数；CLI 默认只输出审计摘要，只有显式 --apply 才写入数据库。
async def main() -> None:
    """从部署配置连接旧库；不启动 API/worker，也不读取用户文件或外部对象。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="已停写并备份整套数据后执行事务迁移")
    parser.add_argument("--repair-session-config", action="store_true", help="从旧归档修复已迁移会话缺失的配置快照")
    args = parser.parse_args()
    pg_manager.initialize()
    try:
        if args.repair_session_config:
            report = await repair_legacy_session_configs(pg_manager, apply=args.apply)
        else:
            report = await upgrade_legacy_schema(pg_manager) if args.apply else await audit_legacy_schema(pg_manager)
        print(json.dumps(report, ensure_ascii=False))
        if report.get("blocking"):
            raise SystemExit(1)
    except SQLAlchemyError as error:
        # 数据库异常的 DETAIL 可能包含整行正文或凭据，不将其输出到部署日志。
        print(f"迁移事务已回滚：{type(error).__name__}，SQLSTATE={getattr(error.orig, 'sqlstate', 'unknown')}")
        raise SystemExit(1) from None
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
