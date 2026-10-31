from copy import deepcopy
import os
import shlex
import subprocess

import pytest

from test.unit.config._shared import load_compose, project_root


STATE_ROOT = "${YUXI_STATE_DIR:-./docker/volumes}"
IMAGE_PREFIX = "${COMPOSE_PROJECT_NAME:-yuxi}"
PORT_MARKERS = {
    "api": {"${YUXI_API_PORT:-5050}:5050"},
    "frontend": {"${YUXI_FRONTEND_PORT:-5173}:5173"},
    "graph": {
        "127.0.0.1:${YUXI_NEO4J_HTTP_PORT:-7474}:7474",
        "127.0.0.1:${YUXI_NEO4J_BOLT_PORT:-7687}:7687",
    },
    "minio": {
        "127.0.0.1:${YUXI_MINIO_API_PORT:-9000}:9000",
        "127.0.0.1:${YUXI_MINIO_CONSOLE_PORT:-9001}:9001",
    },
    "milvus": {
        "127.0.0.1:${YUXI_MILVUS_PORT:-19530}:19530",
        "127.0.0.1:${YUXI_MILVUS_HEALTH_PORT:-9091}:9091",
    },
    "postgres": {"127.0.0.1:${YUXI_POSTGRES_PORT:-5432}:5432"},
    "redis": {"127.0.0.1:${YUXI_REDIS_PORT:-6379}:6379"},
    "mineru-api": {"127.0.0.1:${YUXI_MINERU_PORT:-30001}:30001"},
}
LOCAL_IMAGE_SUFFIXES = {
    "storage-init": "-storage-init:",
    "api": "-api:",
    "worker": "-api:",
    "app-init": "-api:",
    "frontend": "-frontend:",
    "mineru-api": "-mineru:",
}


def _slot_isolation_violations(compose: dict) -> set[str]:
    """报告会让两个开发槽位争用宿主资源的 Compose 配置。"""
    violations: set[str] = set()
    services = compose["services"]

    for service_name, service in services.items():
        if "container_name" in service:
            violations.add(f"container:{service_name}")
        for volume in service.get("volumes") or []:
            if isinstance(volume, str):
                source = volume.split(":", 1)[0]
            else:
                source = str(volume.get("source") or "")
            if source.startswith("./docker/volumes"):
                violations.add(f"state:{service_name}:{source}")

    if "name" in compose["networks"]["app-network"]:
        violations.add("network:app-network")

    for service_name, expected_ports in PORT_MARKERS.items():
        actual_ports = set(services[service_name].get("ports") or [])
        if actual_ports != expected_ports:
            violations.add(f"ports:{service_name}")

    for service_name, suffix in LOCAL_IMAGE_SUFFIXES.items():
        image = str(services[service_name].get("image") or "")
        if not image.startswith(f"{IMAGE_PREFIX}{suffix}"):
            violations.add(f"image:{service_name}")

    return violations


def test_development_compose_is_parameterized_for_parallel_worktree_slots() -> None:
    """开发 Compose 的宿主资源必须由项目名、状态根和端口共同隔离。"""
    compose = load_compose()

    assert _slot_isolation_violations(compose) == set()

    state_sources = {
        str(volume.get("source"))
        for service in compose["services"].values()
        for volume in service.get("volumes") or []
        if isinstance(volume, dict) and str(volume.get("source") or "").startswith(STATE_ROOT)
    }
    assert f"{STATE_ROOT}/postgresql" in state_sources
    assert f"{STATE_ROOT}/redis" in state_sources
    assert f"{STATE_ROOT}/milvus/milvus" in state_sources
    assert f"{STATE_ROOT}/neo4j/data" in state_sources
    assert f"{STATE_ROOT}/yuxi/threads" in state_sources


def test_slot_isolation_guard_rejects_fixed_host_resources() -> None:
    """负控证明固定容器、网络、端口、镜像或状态路径会被拒绝。"""
    compose = deepcopy(load_compose())
    compose["services"]["api"]["container_name"] = "api-dev"
    compose["services"]["api"]["ports"] = ["5050:5050"]
    compose["services"]["api"]["image"] = "yuxi-api:latest"
    compose["services"]["postgres"]["volumes"][0]["source"] = "./docker/volumes/postgresql"
    compose["networks"]["app-network"]["name"] = "yuxi-app-network"

    assert {
        "container:api",
        "ports:api",
        "image:api",
        "state:postgres:./docker/volumes/postgresql",
        "network:app-network",
    } <= _slot_isolation_violations(compose)


def test_production_compose_scopes_images_by_project_with_legacy_default() -> None:
    """生产镜像按项目隔离，未配置项目名时仍使用 yuxi 前缀。"""
    compose = load_compose("docker-compose.prod.yml")

    assert compose["services"]["api"]["image"].startswith("${COMPOSE_PROJECT_NAME:-yuxi}-api:${YUXI_VERSION:-")
    assert compose["services"]["frontend"]["image"].startswith("${COMPOSE_PROJECT_NAME:-yuxi}-frontend:${YUXI_VERSION:-")


@pytest.mark.parametrize("base_url,ready", [(None, True), ("http://slot-api:25050", True), ("http://slot-api:25050", False)])
def test_host_test_runner_probes_current_compose_slot(tmp_path, monkeypatch, base_url, ready) -> None:
    """真实 shell 入口探测目标槽位，未就绪不能执行任何测试批次。"""
    commands_file = tmp_path / "commands"
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_ARGS_LOG"\n'
        'if [ "$5" = curl ]; then exit "$PROBE_EXIT_CODE"; fi\n'
    )
    docker.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_ARGS_LOG", str(commands_file))
    monkeypatch.setenv("PROBE_EXIT_CODE", "0" if ready else "7")
    if base_url:
        monkeypatch.setenv("TEST_BASE_URL", base_url)
    else:
        monkeypatch.delenv("TEST_BASE_URL", raising=False)

    result = subprocess.run(["bash", str(project_root() / "backend/test/run_tests.sh"), "integration"], capture_output=True, text=True)

    commands = [shlex.split(line) for line in commands_file.read_text().splitlines()]
    assert commands[0] == ["compose", "exec", "-T", "api", "curl", "-fsS", f"{base_url or 'http://localhost:5050'}/api/system/ready"]
    assert len([command for command in commands if "pytest" in command]) == (2 if ready else 0)
    for command in commands:
        if "pytest" in command:
            assert command[command.index("uv"):command.index("pytest") + 1] == ["uv", "run", "--no-sync", "--no-dev", "pytest"]
    assert result.returncode == (0 if ready else 1)


@pytest.mark.parametrize("pytest_exit", [0, 23])
def test_cube_worker_runner_uses_installed_dependencies_and_restores_worker(tmp_path, monkeypatch, pytest_exit):
    """真实 shell 入口不重装只读项目，失败仍恢复被暂停的 worker。"""
    commands_file = tmp_path / "commands"
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_ARGS_LOG"\n'
        'case " $* " in *" pytest "*) exit "$PYTEST_EXIT_CODE";; esac\n'
    )
    docker.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_ARGS_LOG", str(commands_file))
    monkeypatch.setenv("PYTEST_EXIT_CODE", str(pytest_exit))
    monkeypatch.setenv("CUBE_TEST_ISOLATED_TOPOLOGY", "1")
    result = subprocess.run(
        ["bash", str(project_root() / "scripts/ci/test_cube_worker_recovery.sh")], capture_output=True, text=True,
    )
    commands = [shlex.split(line) for line in commands_file.read_text().splitlines()]
    [command] = [command for command in commands if "pytest" in command]
    assert command[command.index("uv"):command.index("pytest") + 1] == ["uv", "run", "--no-sync", "--no-dev", "pytest"]
    assert commands[-1] == ["compose", "unpause", "worker"]
    assert result.returncode == pytest_exit


def test_production_api_only_publishes_on_loopback() -> None:
    """生产 API 经 Web 代理接入，宿主默认只允许回环直连。"""
    compose = load_compose("docker-compose.prod.yml")
    assert compose["services"]["api"]["ports"] == ["127.0.0.1:${YUXI_API_PORT:-6050}:5050"]
