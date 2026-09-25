from copy import deepcopy
import os
from pathlib import Path

import pytest
import yaml


STATE_ROOT = "${YUXI_STATE_DIR:-./docker/volumes}"
IMAGE_PREFIX = "${COMPOSE_PROJECT_NAME:-yuxi}"
PORT_MARKERS = {
    "api": {"${YUXI_API_PORT:-5050}:5050"},
    "web": {"${YUXI_WEB_PORT:-5173}:5173"},
    "sandbox-provisioner": {"127.0.0.1:${YUXI_SANDBOX_PORT:-8002}:8002"},
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
    "paddlex": {"127.0.0.1:${YUXI_PADDLEX_PORT:-8080}:8080"},
}
LOCAL_IMAGE_SUFFIXES = {
    "api": "-api:",
    "worker": "-api:",
    "storage-migrator": "-api:",
    "sandbox-provisioner": "-sandbox-provisioner:",
    "web": "-web:",
    "mineru-api": "-mineru:",
    "paddlex": "-paddlex:",
}


def _project_root() -> Path:
    """定位包含 Compose 文件的仓库根目录。"""
    configured = os.environ.get("YUXI_PROJECT_ROOT")
    if configured:
        return Path(configured)

    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").exists():
            return parent
    pytest.skip("当前测试环境未挂载仓库根目录")


def _load_compose(filename: str = "docker-compose.yml") -> dict:
    """读取未插值的 Compose 契约。"""
    return yaml.safe_load((_project_root() / filename).read_text())


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

    provisioner_env = set(services["sandbox-provisioner"]["environment"])
    expected_prefix = "${SANDBOX_DOCKER_NETWORK_PREFIX:-${COMPOSE_PROJECT_NAME:-yuxi}-sandbox}"
    if f"DOCKER_NETWORK_PREFIX={expected_prefix}" not in provisioner_env:
        violations.add("sandbox:network-prefix")
    expected_container = "${SANDBOX_DOCKER_SANDBOX_PREFIX:-${COMPOSE_PROJECT_NAME:-yuxi}-sandbox}"
    if f"DOCKER_SANDBOX_PREFIX={expected_container}" not in provisioner_env:
        violations.add("sandbox:container-prefix")

    return violations


def test_development_compose_is_parameterized_for_parallel_worktree_slots() -> None:
    """开发 Compose 的宿主资源必须由项目名、状态根和端口共同隔离。"""
    compose = _load_compose()

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
    compose = deepcopy(_load_compose())
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
    compose = _load_compose("docker-compose.prod.yml")

    assert compose["services"]["api"]["image"].startswith("${COMPOSE_PROJECT_NAME:-yuxi}-api:${YUXI_VERSION:-")
    assert compose["services"]["web"]["image"].startswith("${COMPOSE_PROJECT_NAME:-yuxi}-web:${YUXI_VERSION:-")


def test_host_test_runner_probes_current_compose_slot() -> None:
    """测试运行器必须通过 Compose service 探测当前槽位的 readiness。"""
    source = (_project_root() / "backend/test/run_tests.sh").read_text()

    assert "docker compose exec -T api curl -fsS http://localhost:5050/api/system/ready" in source
    assert "docker compose exec -T api curl -fsS http://localhost:5050/api/system/health" not in source
