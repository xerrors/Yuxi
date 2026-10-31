#!/bin/bash

set -euo pipefail

echo "Yuxi 测试运行器"
echo "========================"

PYTEST_CMD=(
    docker compose exec -T
    -e TEST_BASE_URL -e TEST_USERNAME -e TEST_PASSWORD
    -e E2E_USERNAME -e E2E_PASSWORD
    -e OPENAI_REPLAY_URL -e COOPERATION_REPLAY_URL
    api uv run --no-sync --no-dev pytest
)
# 会话协作 E2E 单文件耗时约占整个批次的三分之二，CI 将它与其余生命周期文件分拓扑并行；
# e2e 始终等于两个子集之和，保持本地一次性运行的入口不变。
LIFECYCLE_COOPERATION_E2E_TESTS=(
    test/e2e/test_session_cooperation_e2e.py
)
LIFECYCLE_CORE_E2E_TESTS=(
    test/e2e/test_agent_lifecycle_e2e.py
    test/e2e/test_input_projection_recovery_e2e.py
    test/e2e/test_draft_attachments_e2e.py
    test/e2e/test_agent_lifecycle_extended_e2e.py
    test/e2e/test_agent_lifecycle_key_scope_e2e.py
    test/e2e/test_openai_events_e2e.py
    test/e2e/test_agent_feedback_e2e.py
    test/e2e/test_session_mentions_e2e.py
    test/e2e/test_malformed_tool_recovery_e2e.py
)

# 只在显式收集 CI 证据时追加报告参数，保留 pytest 退出状态。
run_pytest() {
    local suite="$1"
    shift
    local report_args=()
    if [[ -n "${CI_TEST_REPORT_DIR:-}" ]]; then
        report_args=("--junitxml=${CI_TEST_REPORT_DIR}/${suite}.xml" --durations=20 --durations-min=1.0)
    fi
    "${PYTEST_CMD[@]}" "$@" "${report_args[@]}"
}

check_server() {
    echo "检查测试服务是否就绪..."
    if docker compose exec -T api curl -fsS "${TEST_BASE_URL:-http://localhost:5050}/api/system/ready" > /dev/null 2>&1; then
        echo "✓ 测试服务已就绪"
        return 0
    fi

    echo "✗ 测试服务未就绪或无法访问"
    echo "  请先执行 docker compose up -d 并确认 api 服务 readiness"
    return 1
}

run_unit_tests() {
    echo "运行单元测试..."
    run_pytest unit test/unit -m "not slow"
}

run_integration_tests() {
    echo "运行集成测试..."
    check_server
    run_pytest integration-api test/integration/api
    run_pytest integration-other test/integration --ignore=test/integration/api \
        --ignore=test/integration/services/test_background_job_worker_path.py
}

run_e2e_tests() {
    echo "运行确定性 Agent 端到端测试..."
    check_server
    run_pytest e2e "${LIFECYCLE_COOPERATION_E2E_TESTS[@]}" "${LIFECYCLE_CORE_E2E_TESTS[@]}" -m e2e
}

run_cooperation_e2e_tests() {
    echo "运行会话协作端到端测试..."
    check_server
    run_pytest e2e-cooperation "${LIFECYCLE_COOPERATION_E2E_TESTS[@]}" -m e2e
}

run_core_e2e_tests() {
    echo "运行生命周期核心端到端测试..."
    check_server
    run_pytest e2e-core "${LIFECYCLE_CORE_E2E_TESTS[@]}" -m e2e
}

run_all_e2e_tests() {
    echo "运行全部端到端测试（包括真实模型与外部服务探针）..."
    check_server
    run_pytest e2e-all test/e2e -m e2e
}

run_all_tests() {
    echo "运行全部测试..."
    run_unit_tests
    run_integration_tests
    run_e2e_tests
}

show_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  unit         - 运行单元测试"
    echo "  integration  - 运行集成测试"
    echo "  e2e          - 运行确定性 Agent 端到端测试"
    echo "  e2e-cooperation - 只运行会话协作端到端子集"
    echo "  e2e-core     - 只运行除会话协作外的生命周期端到端子集"
    echo "  e2e-all      - 运行全部端到端测试，包括外部探针"
    echo "  all          - 运行 unit、integration 与确定性 Agent E2E"
    echo "  check        - 检查测试服务"
    echo "  help         - 显示此帮助"
    echo ""
    echo "示例:"
    echo "  $0 unit"
    echo "  $0 integration"
    echo "  $0 e2e"
    echo "  $0 all"
}

case "${1:-all}" in
    "unit")
        run_unit_tests
        ;;
    "integration")
        run_integration_tests
        ;;
    "e2e")
        run_e2e_tests
        ;;
    "e2e-cooperation")
        run_cooperation_e2e_tests
        ;;
    "e2e-core")
        run_core_e2e_tests
        ;;
    "e2e-all")
        run_all_e2e_tests
        ;;
    "all")
        run_all_tests
        ;;
    "check")
        check_server
        ;;
    "help"|"-h"|"--help")
        show_help
        ;;
    *)
        echo "未知选项: $1"
        show_help
        exit 1
        ;;
esac
