#!/usr/bin/env bash
# 在一次性的 PostgreSQL/Redis 测试环境执行独立集成测试。
set -euo pipefail
: "${POSTGRES_URL:?需要隔离测试数据库}"
: "${REDIS_URL:?需要隔离测试 Redis}"
: "${YUXI_SKILL_DATA_DIR:?需要临时 Skill 目录}"
cd "$(dirname "$0")/../../backend"
# app-init 会用本地 .env 覆盖进程环境，禁止借用已有部署配置。
if [[ -e .env ]]; then
    echo '独立集成测试拒绝加载 backend/.env，请使用干净 checkout 与隔离资源' >&2
    exit 1
fi
# 所有执行节点与共享 API 不可达，避免把现成运行环境误当成独立性证据。
export TEST_BASE_URL=http://127.0.0.1:1
export SANDBOX_BACKEND=container
export SANDBOX_RUNNER_URL=http://127.0.0.1:1
export SANDBOX_RUNNER_TOKEN=ci-service-integration-unused-token
export CUBE_API_URL=http://127.0.0.1:1
export CUBE_PROXY_NODE_IP=127.0.0.1
export CUBE_PROXY_PORT_HTTP=1
export TEST_USERNAME= TEST_PASSWORD=
uv run --locked --group test python - <<'PY'
import asyncio

from yuxi.bootstrap.app import main
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.identity.services.administration import initialize_system_admin


async def prepare():
    """一次性环境保留基线管理员，测试账号清理仍经过最后管理员约束。"""
    await main()
    pg_manager.initialize()
    try:
        async with pg_manager.get_async_session_context() as session:
            await initialize_system_admin(session, uid="ci_service_admin", password="ci-service-admin-password", phone_number=None)
    finally:
        await pg_manager.close()


asyncio.run(prepare())
PY
report_args=()
if [[ -n "${CI_TEST_REPORT_DIR:-}" ]]; then
    report_args=("--junitxml=${CI_TEST_REPORT_DIR}/service-integration.xml" --durations=20 --durations-min=1.0)
fi
uv run --locked --group test pytest \
    test/integration/api/test_error_information_boundary.py \
    test/integration/api/test_knowledge_url_import_options.py \
    test/integration/api/test_dashboard_usage.py \
    test/integration/api/test_scheduled_agent_history.py \
    test/integration/api/test_sandbox_runner_health.py \
    test/integration/services/test_app_initialization.py \
    test/integration/services/test_schema_migration_version.py \
    test/integration/services/test_agent_input_concurrency.py \
    test/integration/services/test_thread_priority_inputs.py \
    test/integration/services/test_agent_dispatch_receipts.py \
    test/integration/services/test_agent_stop_recovery.py \
    test/integration/services/test_session_title_generation.py \
    test/integration/services/test_session_mentions.py \
    test/integration/services/test_agent_run_lease.py \
    test/integration/services/test_agent_run_manifest_and_attempts.py \
    test/integration/services/test_run_admission.py \
    test/integration/services/test_agent_feedback.py \
    test/integration/services/test_session_cooperation.py \
    test/integration/services/test_oidc_login_transaction.py \
    test/integration/services/test_password_login_boundary.py \
    test/integration/services/test_knowledge_projection_integrity.py \
    test/integration/services/test_knowledge_metadata_boundary.py \
    test/integration/services/test_knowledge_evaluation_reads.py \
    test/integration/services/test_knowledge_directory_migration.py \
    test/integration/services/test_database_relation_integrity.py \
    test/integration/services/test_database_access_paths.py \
    test/integration/services/test_mcp_oauth.py \
    test/integration/services/test_mcp_delegation_lifecycle.py \
    test/integration/services/test_arq_worker_dispatch.py \
    test/integration/services/test_background_job_repository.py \
    test/integration/services/test_chunk_config_boundary.py \
    test/integration/services/test_knowledge_stats_refresh.py \
    -q --tb=short -ra "${report_args[@]}"
