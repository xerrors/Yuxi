#!/bin/bash

set -euo pipefail

echo "Yuxi 测试运行器"
echo "========================"

PYTEST_CMD=("docker" "compose" "exec" "api" "uv" "run" "--group" "test" "pytest")

check_server() {
    echo "检查测试服务是否就绪..."
    if docker compose exec -T api curl -fsS http://localhost:5050/api/system/ready > /dev/null 2>&1; then
        echo "✓ 测试服务已就绪"
        return 0
    fi

    echo "✗ 测试服务未就绪或无法访问"
    echo "  请先执行 docker compose up -d 并确认 api 服务 readiness"
    return 1
}

run_unit_tests() {
    echo "运行单元测试..."
    "${PYTEST_CMD[@]}" test/unit -m "not slow"
}

run_integration_tests() {
    echo "运行集成测试..."
    check_server
    "${PYTEST_CMD[@]}" test/integration
}

run_e2e_tests() {
    echo "运行确定性 Agent 端到端测试..."
    check_server
    "${PYTEST_CMD[@]}" test/e2e/test_deterministic_agent_path_e2e.py -m e2e
}

run_all_e2e_tests() {
    echo "运行全部端到端测试（包括真实模型与外部服务探针）..."
    check_server
    "${PYTEST_CMD[@]}" test/e2e -m e2e
}

run_all_tests() {
    echo "运行全部测试..."
    check_server
    "${PYTEST_CMD[@]}" test/unit test/integration test/e2e/test_deterministic_agent_path_e2e.py
}

show_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  unit         - 运行单元测试"
    echo "  integration  - 运行集成测试"
    echo "  e2e          - 运行确定性 Agent 端到端测试"
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
