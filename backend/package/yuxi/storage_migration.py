"""在 API、worker 与 Sandbox 启动前完成一次性存储迁移。"""

from __future__ import annotations

import asyncio
import hmac
import os
from pathlib import Path

from sqlalchemy import text

from yuxi.config import get_legacy_storage_dir
from yuxi.config.options import ensure_options_in_db
from yuxi.config.runtime import lite_mode_enabled
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.storage.postgres.manager import (
    BUSINESS_SCHEMA_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    V071_WORKDIR_CUTOVER_STATEMENTS,
    pg_manager,
)
from yuxi.storage_migrations.v071_options import migrate_system_options
from yuxi.storage_migrations.v071_skills import (
    mark_migrated as mark_v071_skills_migrated,
)
from yuxi.storage_migrations.v071_skills import (
    migrate_shared_skills,
)
from yuxi.storage_migrations.v071_skills import (
    migration_completed as v071_skill_migration_completed,
)
from yuxi.storage_migrations.v071_workdirs import (
    cleanup_v071_thread_sources,
    import_v071_workdirs,
    read_v071_workdir_plan,
    rewrite_v071_workdir_paths,
    verify_workdir_bindings,
)
from yuxi.storage_migrations.v072_runtime_identity import (
    migrate_runtime_storage_identity,
    runtime_storage_requires_quiescence,
)

_QUIESCENCE_TOKEN_ENV = "YUXI_STORAGE_MIGRATION_QUIESCENCE_TOKEN"
_QUIESCENCE_FILE_ENV = "YUXI_STORAGE_MIGRATION_QUIESCENCE_FILE"


def _legacy_skill_roots_exist() -> bool:
    """判断是否仍有会被停机迁移删除的历史共享 Skill 目录。"""
    if v071_skill_migration_completed():
        return False
    shared_skills = get_legacy_storage_dir() / "skills"
    if shared_skills.is_symlink() or (shared_skills.is_dir() and any(shared_skills.iterdir())):
        return True
    return False


def _legacy_system_config_exists() -> bool:
    """判断旧广域目录是否仍保存系统配置。"""
    return (get_legacy_storage_dir() / "config/base.toml").is_file()


def _require_quiescence_proof() -> None:
    """校验由宿主停机脚本创建的一次性证明，禁止普通 up 执行破坏性迁移。"""
    expected = os.getenv(_QUIESCENCE_TOKEN_ENV, "").strip()
    proof_file = Path(
        os.getenv(
            _QUIESCENCE_FILE_ENV,
            str(get_legacy_storage_dir() / ".storage-migration-quiesced"),
        )
    )
    try:
        actual = proof_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("检测到旧文件数据；请先运行 scripts/migrate-storage.sh 停止旧运行环境") from exc
    if not expected or not actual or not hmac.compare_digest(actual, expected):
        raise RuntimeError("存储迁移停机证明无效；请重新运行 scripts/migrate-storage.sh")


async def _converge_database_state(*, fail_nonterminal_runs: bool) -> None:
    """导入历史系统配置；仅在已停机的旧布局切换中收敛非终态 Run。"""
    async with pg_manager.get_async_session_context() as session:
        if fail_nonterminal_runs:
            await AgentRunRepository(session).fail_nonterminal_for_storage_migration()
        await ensure_options_in_db(session)
        await migrate_system_options(
            session,
            legacy_config_file=get_legacy_storage_dir() / "config/base.toml",
        )
        await session.commit()


def _require_supported_version(
    domain: str,
    actual: int | None,
    expected: int,
    *,
    previous: int | None = None,
) -> None:
    """只接受未版本化 baseline、当前版本或显式相邻升级来源。"""
    supported = {None, expected}
    if previous is not None:
        supported.add(previous)
    if actual not in supported:
        raise RuntimeError(f"Unsupported {domain} schema version: {actual}; expected {expected}")


async def main() -> None:
    """独占迁移数据库 Schema，并在停机窗口切换历史文件 Owner。"""
    pg_manager.initialize()
    try:
        async with pg_manager.schema_migration_lock():
            async with pg_manager.get_async_session_context() as session:
                workdir_plan = await read_v071_workdir_plan(session)
            migrates_workdirs = workdir_plan.requires_cutover or bool(workdir_plan.workdirs)
            requires_quiescence = (
                migrates_workdirs
                or _legacy_skill_roots_exist()
                or _legacy_system_config_exists()
                or runtime_storage_requires_quiescence()
            )
            if requires_quiescence:
                _require_quiescence_proof()

            await pg_manager.create_schema_version_table()
            versions = await pg_manager.get_schema_versions()
            business_version = versions.get("business")
            _require_supported_version(
                "business",
                business_version,
                BUSINESS_SCHEMA_VERSION,
                previous=BUSINESS_SCHEMA_VERSION - 1,
            )
            if not lite_mode_enabled():
                _require_supported_version(
                    "knowledge",
                    versions.get("knowledge"),
                    KNOWLEDGE_SCHEMA_VERSION,
                    previous=KNOWLEDGE_SCHEMA_VERSION - 1,
                )

            if business_version is None:
                await pg_manager.create_business_tables()
            if migrates_workdirs:
                await asyncio.to_thread(import_v071_workdirs, workdir_plan.workdirs, workdir_plan.conversations)
                async with pg_manager.get_async_session_context() as session:
                    for statement in V071_WORKDIR_CUTOVER_STATEMENTS:
                        await session.execute(text(statement))
                    await rewrite_v071_workdir_paths(session)
                    await verify_workdir_bindings(session)
                    await session.commit()
            if business_version is None:
                await pg_manager.ensure_business_schema()
                await pg_manager.setup_langgraph_checkpointer()
                await pg_manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION)
            elif business_version == BUSINESS_SCHEMA_VERSION - 1:
                await pg_manager.upgrade_business_schema_v1_to_v2()
                await pg_manager.record_schema_version("business", BUSINESS_SCHEMA_VERSION)

            if not lite_mode_enabled() and versions.get("knowledge") is None:
                await pg_manager.create_knowledge_tables()
                await pg_manager.ensure_knowledge_schema()
                await pg_manager.record_schema_version("knowledge", KNOWLEDGE_SCHEMA_VERSION)
            elif not lite_mode_enabled() and versions.get("knowledge") == KNOWLEDGE_SCHEMA_VERSION - 1:
                await pg_manager.upgrade_knowledge_schema_v1_to_v2()
                await pg_manager.record_schema_version("knowledge", KNOWLEDGE_SCHEMA_VERSION)

            await _converge_database_state(fail_nonterminal_runs=requires_quiescence)
            legacy_config_file = get_legacy_storage_dir() / "config/base.toml"
            if legacy_config_file.is_file() and not legacy_config_file.is_symlink():
                legacy_config_file.unlink()
            if migrates_workdirs:
                await asyncio.to_thread(cleanup_v071_thread_sources, workdir_plan.conversations)
            async with pg_manager.get_async_session_context() as session:
                await migrate_shared_skills(session)
            mark_v071_skills_migrated()
            await asyncio.to_thread(migrate_runtime_storage_identity)
    finally:
        await pg_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
