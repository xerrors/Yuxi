from copy import deepcopy
import os
import re
from pathlib import Path
import subprocess

import pytest
import yaml


FORBIDDEN_API_WORKER_TARGETS = frozenset({"/app/checkpoints", "/app/models", "/app/saves", "/var/run/docker.sock"})
REQUIRED_STORAGE_TARGETS = {
    "api": frozenset({"/app/user-data", "/app/skill-sources", "/app/skill-projections"}),
    "worker": frozenset({"/app/user-data", "/app/skill-sources", "/app/skill-projections"}),
}
FORBIDDEN_API_WORKER_ENV_KEYS = frozenset({"YUXI_DOCKER_API_BASE"})
EXPECTED_RUNTIME_DIRS = {"api": "/app/runtime/api", "worker": "/app/runtime/worker"}
EXPECTED_REDIS_CONNECTION_CAPACITY = {
    "api": "${API_REDIS_MAX_CONNECTIONS:-256}",
    "worker": "${WORKER_REDIS_MAX_CONNECTIONS:-256}",
}
EXPECTED_AGENT_CAPACITY = {
    "api": {
        "POSTGRES_POOL_SIZE": "${API_POSTGRES_POOL_SIZE:-120}",
        "POSTGRES_MAX_OVERFLOW": "${API_POSTGRES_MAX_OVERFLOW:-40}",
        "LANGGRAPH_POSTGRES_POOL_SIZE": "${API_LANGGRAPH_POSTGRES_POOL_SIZE:-10}",
        "LANGGRAPH_POSTGRES_POOL_TIMEOUT_SECONDS": "${API_LANGGRAPH_POSTGRES_POOL_TIMEOUT_SECONDS:-30}",
    },
    "worker": {
        "ARQ_MAX_JOBS": "${ARQ_MAX_JOBS:-140}",
        "POSTGRES_POOL_SIZE": "${WORKER_POSTGRES_POOL_SIZE:-120}",
        "POSTGRES_MAX_OVERFLOW": "${WORKER_POSTGRES_MAX_OVERFLOW:-40}",
        "LANGGRAPH_POSTGRES_POOL_SIZE": "${WORKER_LANGGRAPH_POSTGRES_POOL_SIZE:-120}",
    },
}
EXPECTED_SANDBOX_STOP_TIMEOUT = "${SANDBOX_CONTAINER_STOP_TIMEOUT_SECONDS:-2}"
FORBIDDEN_DIRECT_DOCKER_ACCESS_MARKERS = frozenset(
    {"--unix-socket", "/var/run/docker.sock", "YUXI_DOCKER_API_SOCKET", "docker.from_env(", "DockerClient("}
)
SANDBOX_CLEANUP_OWNER_PATHS = (
    "backend/test/integration/conftest.py",
    "backend/test/live_api_cleanup.py",
)
WORKSPACE_PERMISSION_OWNER_PATHS = (
    "backend/package/yuxi/utils/paths.py",
    "backend/package/yuxi/workspace/paths.py",
    "backend/package/yuxi/workspace/filesystem.py",
    "backend/package/yuxi/services/workspace_service.py",
    "backend/package/yuxi/storage_migrations/v071_workdirs.py",
    "docker/sandbox_provisioner/app.py",
)
LEGACY_WORKSPACE_PERMISSION_MARKERS = frozenset(
    {"0o777", "0o666", "chmod a+rwx", "_ensure_user_data_writable", "_chmod_writable"}
)


def _project_root() -> Path:
    """定位包含 Compose 文件的仓库根目录。"""
    configured = os.environ.get("YUXI_PROJECT_ROOT")
    if configured:
        return Path(configured)

    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").exists():
            return parent
    pytest.skip("当前测试环境未挂载仓库根目录")


def _load_compose(filename: str) -> dict:
    return yaml.safe_load((_project_root() / filename).read_text())


def _volume_target(volume: object) -> str:
    """读取 Compose 短格式或长格式 volume 的容器目标路径。"""
    if isinstance(volume, dict):
        return str(volume.get("target") or "")
    if not isinstance(volume, str):
        return ""

    # 未插值的环境变量默认值含冒号，不属于挂载分隔符。
    parts = re.sub(r"\$\{[^}]*\}", "_env_", volume).split(":")
    return parts[1] if len(parts) >= 2 else parts[0]


def _volume_is_read_only(volume: object) -> bool:
    """读取 Compose volume 的只读标记。"""
    if isinstance(volume, dict):
        return bool(volume.get("read_only"))
    return isinstance(volume, str) and len(volume.split(":")) >= 3 and volume.split(":")[-1] == "ro"


def _forbidden_api_worker_mounts(compose: dict) -> set[tuple[str, str]]:
    violations: set[tuple[str, str]] = set()
    for service_name in ("api", "worker"):
        volumes = compose["services"][service_name].get("volumes") or []
        for volume in volumes:
            target = _volume_target(volume)
            if target in FORBIDDEN_API_WORKER_TARGETS:
                violations.add((service_name, target))
    return violations


def _forbidden_api_worker_env_keys(compose: dict) -> set[tuple[str, str]]:
    """识别 API/worker 中已失去 consumer 的环境变量。"""
    violations: set[tuple[str, str]] = set()
    for service_name in ("api", "worker"):
        environment = compose["services"][service_name].get("environment") or {}
        for key in FORBIDDEN_API_WORKER_ENV_KEYS & environment.keys():
            violations.add((service_name, key))
    return violations


def _runtime_directory_violations(compose: dict) -> set[tuple[str, str]]:
    """识别共享、持久或未按服务隔离的运行目录。"""
    violations: set[tuple[str, str]] = set()
    for service_name, expected in EXPECTED_RUNTIME_DIRS.items():
        environment = compose["services"][service_name].get("environment") or {}
        actual = str(environment.get("YUXI_RUNTIME_DIR") or "")
        if actual != expected:
            violations.add((service_name, actual))
    return violations


def _forbidden_direct_docker_access(source: str) -> set[str]:
    """识别绕过 provisioner 直接访问 Docker daemon 的测试代码。"""
    return {marker for marker in FORBIDDEN_DIRECT_DOCKER_ACCESS_MARKERS if marker in source}


def _legacy_workspace_permission_markers(source: str) -> set[str]:
    """识别已由统一运行身份取代的权限补丁。"""
    return {marker for marker in LEGACY_WORKSPACE_PERMISSION_MARKERS if marker in source}


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_api_and_worker_do_not_mount_unused_host_dependencies(filename: str):
    """API/worker 不得重新依赖模型目录或 Docker daemon。"""
    assert _forbidden_api_worker_mounts(_load_compose(filename)) == set()


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_api_worker_and_provisioner_use_explicit_storage_domains(filename: str):
    """shipping 服务不得通过广域 saves 重新获得未声明的文件能力。"""
    compose = _load_compose(filename)
    for service_name in ("api", "worker"):
        targets = {_volume_target(volume) for volume in compose["services"][service_name].get("volumes") or []}
        assert REQUIRED_STORAGE_TARGETS[service_name] <= targets
        assert "/app/saves" not in targets
        assert "/app/.env" not in targets
    provisioner_targets = {
        _volume_target(volume) for volume in compose["services"]["sandbox-provisioner"].get("volumes") or []
    }
    assert {"/app/user-data", "/app/skill-projections"} <= provisioner_targets
    assert "/app/projects" not in provisioner_targets
    assert "/app/saves" not in provisioner_targets


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_api_and_worker_use_role_specific_redis_connection_capacity(filename: str) -> None:
    """API 与 worker 独立核算 Redis 客户端连接池。"""
    services = _load_compose(filename)["services"]

    for service_name, expected in EXPECTED_REDIS_CONNECTION_CAPACITY.items():
        assert services[service_name]["environment"]["REDIS_MAX_CONNECTIONS"] == expected


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_default_agent_capacity_supports_one_hundred_concurrent_runs(filename: str) -> None:
    """单 worker 的默认执行槽与相关连接池必须作为一组维护。"""
    services = _load_compose(filename)["services"]

    for service_name, expected_environment in EXPECTED_AGENT_CAPACITY.items():
        environment = services[service_name]["environment"]
        for key, expected in expected_environment.items():
            assert environment[key] == expected

    provisioner_environment = services["sandbox-provisioner"]["environment"]
    assert "SANDBOX_DELETE_CONCURRENCY=${SANDBOX_DELETE_CONCURRENCY:-32}" in provisioner_environment
    assert "DOCKER_ADDRESS_POOL=${SANDBOX_DOCKER_ADDRESS_POOL:-10.253.240.0/20}" in provisioner_environment
    assert "DOCKER_SUBNET_PREFIX=${SANDBOX_DOCKER_SUBNET_PREFIX:-28}" in provisioner_environment
    assert services["postgres"]["command"][-1] == "max_connections=${POSTGRES_MAX_CONNECTIONS:-600}"


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_worker_user_data_mount_is_writable_for_personal_skill_install(filename: str) -> None:
    """worker 需要在主 Agent 工具调用中原子安装个人 Skill。"""
    volumes = _load_compose(filename)["services"]["worker"].get("volumes") or []
    user_data_mount = next(volume for volume in volumes if _volume_target(volume) == "/app/user-data")

    assert _volume_is_read_only(user_data_mount) is False


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_workspace_consumers_use_fixed_runtime_identity_after_root_migration(filename: str) -> None:
    """普通文件 consumer 使用 1000:1000，只有一次性 migrator 保留 root。"""
    services = _load_compose(filename)["services"]

    assert services["api"]["user"] == "1000:1000"
    assert services["worker"]["user"] == "1000:1000"
    assert services["storage-migrator"]["user"] == "0:0"


def test_api_image_applies_owner_only_umask_before_dropping_to_runtime_identity() -> None:
    """镜像身份与 umask 必须由 shipping 入口显式拥有。"""
    root = _project_root()
    dockerfile = (root / "docker/api.Dockerfile").read_text()
    entrypoint = (root / "docker/api-entrypoint.sh").read_text()

    assert "USER 1000:1000" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/yuxi-entrypoint"]' in dockerfile
    assert "umask 077" in entrypoint
    assert 'exec "$@"' in entrypoint


def test_workspace_owners_do_not_reintroduce_cross_uid_permission_patches() -> None:
    """运行时 Owner 不得重新承担部署身份兼容。"""
    source = "\n".join((_project_root() / path).read_text() for path in WORKSPACE_PERMISSION_OWNER_PATHS)

    assert _legacy_workspace_permission_markers(source) == set()


def test_workspace_permission_guard_detects_reintroduced_world_writable_mode() -> None:
    """负控证明旧权限模式会被 guard 拒绝。"""
    assert _legacy_workspace_permission_markers("os.mkdir(name, 0o777)") == {"0o777"}


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_storage_migrator_gates_every_shipping_file_consumer(filename: str) -> None:
    """文件 consumer 只能在一次性迁移成功后启动。"""
    compose = _load_compose(filename)
    for service_name in ("api", "worker", "sandbox-provisioner"):
        dependency = compose["services"][service_name]["depends_on"]["storage-migrator"]
        assert dependency["condition"] == "service_completed_successfully"
    migrator = compose["services"]["storage-migrator"]
    assert "python -m yuxi.storage_migration" in migrator["command"]
    migrator_targets = {_volume_target(volume) for volume in migrator.get("volumes") or []}
    assert "/app/legacy-saves" in migrator_targets
    assert "/app/legacy-projects" not in migrator_targets
    assert "/app/checkpoints" not in migrator_targets
    assert "YUXI_LEGACY_PROJECTS_DIR" not in (migrator.get("environment") or {})
    assert "minio" not in (migrator.get("depends_on") or {})


def test_storage_migration_script_quiesces_runtime_before_issuing_proof() -> None:
    """升级入口必须先停写入者和动态 Sandbox，再运行破坏性迁移。"""
    script = _project_root() / "scripts" / "migrate-storage.sh"
    source = script.read_text()

    assert script.stat().st_mode & 0o100
    assert source.index("stop api worker sandbox-provisioner") < source.index("/api/sandboxes/quiesce")
    provisioner_stop = source.rindex("stop sandbox-provisioner")
    assert source.index("/api/sandboxes/quiesce") < provisioner_stop
    assert provisioner_stop < source.index("YUXI_STORAGE_MIGRATION_QUIESCENCE_TOKEN")
    assert 'compose=(docker compose "$@")' in source
    assert "up -d --no-deps --build --wait sandbox-provisioner" in source
    assert "mktemp" in source
    assert '-v "$proof_file:/app/legacy-saves/.storage-migration-quiesced:ro"' in source


def test_v071_options_migration_is_not_part_of_normal_startup() -> None:
    """一次性配置迁移只能由 storage-migrator 装配。"""
    root = _project_root()
    migration_source = (root / "backend/package/yuxi/storage_migration.py").read_text()
    api_source = (root / "backend/server/utils/lifespan.py").read_text()
    worker_source = (root / "backend/package/yuxi/services/run_worker.py").read_text()

    assert "migrate_system_options" in migration_source
    assert "storage_migrations" not in api_source
    assert "storage_migrations" not in worker_source


def test_runtime_processes_only_validate_schema_version() -> None:
    """API 与 worker 不得重新取得建表、DDL 收敛或 checkpoint setup ownership。"""
    root = _project_root()
    migration_source = (root / "backend/package/yuxi/storage_migration.py").read_text()
    runtime_sources = {
        "api": (root / "backend/server/utils/lifespan.py").read_text(),
        "worker": (root / "backend/package/yuxi/services/run_worker.py").read_text(),
    }
    ddl_markers = (
        "create_business_tables(",
        "create_knowledge_tables(",
        "ensure_business_schema(",
        "ensure_knowledge_schema(",
        "setup_langgraph_checkpointer(",
    )

    assert "require_current_schema(" in migration_source or "record_schema_version(" in migration_source
    for source in runtime_sources.values():
        assert "require_current_schema(" in source
        assert all(marker not in source for marker in ddl_markers)


def test_storage_migration_script_recovers_stopped_production_deployment(
    tmp_path: Path,
) -> None:
    """已 down 的生产部署也必须使用同一 Compose/env 选择器完成受控停机迁移。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "docker.log"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$FAKE_DOCKER_LOG"\n'
        'if [[ " $* " == *" ps --status running --services "* ]]; then exit 0; fi\n'
        "cat >/dev/null || true\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ,
        "FAKE_DOCKER_LOG": str(command_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        [
            "bash",
            str(_project_root() / "scripts" / "migrate-storage.sh"),
            "-f",
            "docker-compose.prod.yml",
            "--env-file",
            ".env.prod",
        ],
        cwd=_project_root(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commands = command_log.read_text(encoding="utf-8").splitlines()
    prefix = "compose -f docker-compose.prod.yml --env-file .env.prod "
    assert commands
    assert all(command.startswith(prefix) for command in commands)
    assert any("up -d --no-deps --build --wait sandbox-provisioner" in command for command in commands)
    assert any("exec -T sandbox-provisioner python -" in command for command in commands)
    assert any(
        "run --rm" in command
        and "-v " in command
        and ":/app/legacy-saves/.storage-migration-quiesced:ro" in command
        and "-e YUXI_STORAGE_MIGRATION_QUIESCENCE_TOKEN=" in command
        for command in commands
    )


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_api_and_worker_do_not_expose_removed_docker_api_configuration(filename: str):
    """API/worker 不得保留已删除 Docker daemon 通道的配置表面。"""
    assert _forbidden_api_worker_env_keys(_load_compose(filename)) == set()


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_api_and_worker_use_distinct_local_runtime_directories(filename: str):
    """日志与无状态缓存必须使用服务独立且位于 saves 外的运行目录。"""
    compose = _load_compose(filename)

    assert _runtime_directory_violations(compose) == set()
    assert len(set(EXPECTED_RUNTIME_DIRS.values())) == len(EXPECTED_RUNTIME_DIRS)
    assert all(not path.startswith("/app/saves/") for path in EXPECTED_RUNTIME_DIRS.values())


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_docker_provisioner_keeps_required_docker_socket(filename: str):
    """Docker provisioner 仍需拥有创建动态 sandbox 的 Docker socket。"""
    compose = _load_compose(filename)
    volumes = compose["services"]["sandbox-provisioner"].get("volumes") or []

    assert "/var/run/docker.sock" in {_volume_target(volume) for volume in volumes}


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_docker_provisioner_exposes_bounded_container_stop_timeout(filename: str):
    """Docker Sandbox 的优雅停止等待必须由 provisioner 部署配置拥有。"""
    compose = _load_compose(filename)
    environment = compose["services"]["sandbox-provisioner"].get("environment") or []

    assert f"SANDBOX_CONTAINER_STOP_TIMEOUT_SECONDS={EXPECTED_SANDBOX_STOP_TIMEOUT}" in environment


def test_integration_cleanup_does_not_bypass_sandbox_provisioner():
    """集成测试清理不得要求 API 容器直接访问 Docker daemon。"""
    source = "\n".join((_project_root() / path).read_text() for path in SANDBOX_CLEANUP_OWNER_PATHS)

    assert _forbidden_direct_docker_access(source) == set()


@pytest.mark.parametrize(
    ("service_name", "mount", "expected_target"),
    [
        ("api", "./docker/volumes/models:/app/models", "/app/models"),
        ("worker", "/var/run/docker.sock:/var/run/docker.sock", "/var/run/docker.sock"),
        ("api", "${YUXI_STATE_DIR:-./docker/volumes}/models:/app/models:ro", "/app/models"),
    ],
)
def test_mount_guard_detects_reintroduced_api_worker_host_dependencies(
    service_name: str,
    mount: str,
    expected_target: str,
):
    """恢复已删除挂载时，边界 guard 必须在正确目标上失败。"""
    compose = deepcopy(_load_compose("docker-compose.yml"))
    compose["services"][service_name].setdefault("volumes", []).append(mount)

    assert _forbidden_api_worker_mounts(compose) == {(service_name, expected_target)}


def test_environment_guard_detects_reintroduced_docker_api_configuration():
    """恢复旧 Docker API 环境变量时，边界 guard 必须报告对应服务。"""
    compose = deepcopy(_load_compose("docker-compose.yml"))
    compose["services"]["api"]["environment"]["YUXI_DOCKER_API_BASE"] = "http://localhost"

    assert _forbidden_api_worker_env_keys(compose) == {("api", "YUXI_DOCKER_API_BASE")}


@pytest.mark.parametrize("service_name", ["api", "worker"])
def test_runtime_directory_guard_detects_shared_save_directory(service_name: str):
    """把运行目录恢复到共享 saves 时，边界 guard 必须报告对应服务。"""
    compose = deepcopy(_load_compose("docker-compose.yml"))
    compose["services"][service_name]["environment"]["YUXI_RUNTIME_DIR"] = "/app/saves/runtime"

    assert _runtime_directory_violations(compose) == {(service_name, "/app/saves/runtime")}


@pytest.mark.parametrize("marker", sorted(FORBIDDEN_DIRECT_DOCKER_ACCESS_MARKERS))
def test_cleanup_guard_detects_reintroduced_direct_docker_access(marker: str):
    """恢复 Docker socket 清理路径时，边界 guard 必须报告对应标记。"""
    assert _forbidden_direct_docker_access(f"cleanup command: {marker}") == {marker}
