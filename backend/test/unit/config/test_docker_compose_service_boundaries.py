from copy import deepcopy
import re
import shlex

import pytest
from test.unit.config._shared import load_compose, project_root


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
FORBIDDEN_DIRECT_DOCKER_ACCESS_MARKERS = frozenset(
    {"--unix-socket", "/var/run/docker.sock", "YUXI_DOCKER_API_SOCKET", "docker.from_env(", "DockerClient("}
)
SANDBOX_CLEANUP_OWNER_PATHS = (
    "backend/test/integration/conftest.py",
    "backend/test/live_api_cleanup.py",
)
WORKSPACE_PERMISSION_OWNER_PATHS = (
    "backend/yuxi/infrastructure/filesystem.py",
    "backend/yuxi/modules/workspace/paths.py",
    "backend/yuxi/modules/workspace/filesystem.py",
    "backend/yuxi/modules/workspace/services/files.py",
)
LEGACY_WORKSPACE_PERMISSION_MARKERS = frozenset({"0o777", "0o666", "chmod a+rwx", "_ensure_user_data_writable", "_chmod_writable"})


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


def _backend_reload_services(compose: dict) -> set[str]:
    """识别会因源码编辑而重启进程的后端启动命令。"""
    return {name for name in ("api", "worker") if {"--reload", "watchfiles"} & set(shlex.split(compose["services"][name]["command"]))}


def test_development_backends_do_not_reload_on_source_changes() -> None:
    """源码编辑必须由显式重启决定生效时点。"""
    assert _backend_reload_services(load_compose()) == set()


@pytest.mark.parametrize("consumer", ["api", "worker", "app-init"])
def test_minio_consumers_share_server_credential_interpolation(consumer):
    """客户端与 MinIO 服务使用相同的凭据插值及空值默认规则。"""
    services = load_compose()["services"]
    client = services[consumer]["environment"]
    server = services["minio"]["environment"]
    assert client["MINIO_ACCESS_KEY"] == server["MINIO_ACCESS_KEY"]
    assert client["MINIO_SECRET_KEY"] == server["MINIO_SECRET_KEY"]


@pytest.mark.parametrize(
    ("service_name", "command"),
    [
        ("api", "uvicorn yuxi.api.main:app --reload --reload-dir /app/yuxi"),
        ("worker", 'watchfiles --filter python "python -m yuxi.workers.main" /app/yuxi'),
    ],
)
def test_reload_guard_detects_reintroduced_backend_watchers(service_name: str, command: str) -> None:
    """恢复旧启动命令时，guard 必须报告被重载的服务。"""
    compose = deepcopy(load_compose())
    for name in ("api", "worker"):
        compose["services"][name]["command"] = "python -m yuxi.workers.main" if name == "worker" else "uvicorn yuxi.api.main:app"
    compose["services"][service_name]["command"] = command

    assert _backend_reload_services(compose) == {service_name}


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_api_and_worker_do_not_mount_unused_host_dependencies(filename: str):
    """API/worker 不得重新依赖模型目录或 Docker daemon。"""
    assert _forbidden_api_worker_mounts(load_compose(filename)) == set()


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_api_worker_use_explicit_storage_domains(filename: str):
    """shipping 服务不得通过广域 saves 重新获得未声明的文件能力。"""
    compose = load_compose(filename)
    for service_name in ("api", "worker"):
        targets = {_volume_target(volume) for volume in compose["services"][service_name].get("volumes") or []}
        assert REQUIRED_STORAGE_TARGETS[service_name] <= targets
        assert "/app/saves" not in targets
        assert "/app/.env" not in targets


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_api_and_worker_use_role_specific_redis_connection_capacity(filename: str) -> None:
    """API 与 worker 独立核算 Redis 客户端连接池。"""
    services = load_compose(filename)["services"]

    for service_name, expected in EXPECTED_REDIS_CONNECTION_CAPACITY.items():
        assert services[service_name]["environment"]["REDIS_MAX_CONNECTIONS"] == expected


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_default_agent_capacity_supports_one_hundred_concurrent_runs(filename: str) -> None:
    """单 worker 的默认执行槽与相关连接池必须作为一组维护。"""
    services = load_compose(filename)["services"]

    for service_name, expected_environment in EXPECTED_AGENT_CAPACITY.items():
        environment = services[service_name]["environment"]
        for key, expected in expected_environment.items():
            assert environment[key] == expected

    assert services["postgres"]["command"][-1] == "max_connections=${POSTGRES_MAX_CONNECTIONS:-600}"


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_milvus_suite_has_log_rotation_and_cpus_bound(filename: str) -> None:
    """milvus/etcd 日志轮转封顶；milvus 只保留 cpus 上界；不保留无效日志级别配置。"""
    services = load_compose(filename)["services"]

    for service_name in ("milvus", "etcd"):
        logging_config = services[service_name]["logging"]
        assert logging_config["driver"] == "json-file"
        assert logging_config["options"] == {"max-size": "50m", "max-file": "3"}

    milvus = services["milvus"]
    assert milvus["cpus"] == "${YUXI_MILVUS_CPUS:-2}"
    assert "mem_limit" not in milvus
    assert "MILVUS_LOG_LEVEL" not in milvus.get("environment", {})


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_worker_user_data_mount_is_writable_for_personal_skill_install(filename: str) -> None:
    """worker 需要在主 Agent 工具调用中原子安装个人 Skill。"""
    volumes = load_compose(filename)["services"]["worker"].get("volumes") or []
    user_data_mount = next(volume for volume in volumes if _volume_target(volume) == "/app/user-data")

    assert _volume_is_read_only(user_data_mount) is False


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_workspace_consumers_use_fixed_runtime_identity_after_root_migration(filename: str) -> None:
    """文件 consumer 和 Schema 初始化进程均使用镜像的非 root 身份。"""
    services = load_compose(filename)["services"]

    assert services["api"]["user"] == "1000:1000"
    assert services["worker"]["user"] == "1000:1000"
    assert "user" not in services["app-init"]


def test_api_image_applies_owner_only_umask_before_dropping_to_runtime_identity() -> None:
    """镜像身份与 umask 必须由 shipping 入口显式拥有。"""
    root = project_root()
    dockerfile = (root / "docker/api.Dockerfile").read_text()
    entrypoint = (root / "docker/api-entrypoint.sh").read_text()

    assert "USER 1000:1000" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/yuxi-entrypoint"]' in dockerfile
    assert "umask 077" in entrypoint
    assert 'exec "$@"' in entrypoint


def test_workspace_owners_do_not_reintroduce_cross_uid_permission_patches() -> None:
    """运行时 Owner 不得重新承担部署身份兼容。"""
    source = "\n".join((project_root() / path).read_text() for path in WORKSPACE_PERMISSION_OWNER_PATHS)

    assert _legacy_workspace_permission_markers(source) == set()


def test_workspace_permission_guard_detects_reintroduced_world_writable_mode() -> None:
    """负控证明旧权限模式会被 guard 拒绝。"""
    assert _legacy_workspace_permission_markers("os.mkdir(name, 0o777)") == {"0o777"}


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_app_initializer_gates_every_shipping_file_consumer(filename: str) -> None:
    """文件 consumer 只能在共享应用初始化成功后启动。"""
    compose = load_compose(filename)
    assert _app_dependency_violations(compose) == set()
    initializer = compose["services"]["app-init"]
    assert "python -m yuxi.bootstrap.app" in initializer["command"]
    initializer_targets = {_volume_target(volume) for volume in initializer.get("volumes") or []}
    assert "/app/legacy-saves" not in initializer_targets
    assert not any(target.startswith("/app/legacy-") for target in initializer_targets)
    assert "/app/user-data" not in initializer_targets
    assert "/app/skill-sources" in initializer_targets
    assert initializer["depends_on"]["storage-init"]["condition"] == "service_completed_successfully"
    assert "schema-init" not in compose["services"]
    assert "/app/skill-projections" not in initializer_targets
    assert "YUXI_LEGACY_STORAGE_DIR" not in (initializer.get("environment") or {})
    assert "/app/legacy-projects" not in initializer_targets
    assert "/app/checkpoints" not in initializer_targets
    assert "YUXI_LEGACY_PROJECTS_DIR" not in (initializer.get("environment") or {})
    assert "minio" not in (initializer.get("depends_on") or {})


def _app_dependency_violations(compose):
    """识别未等待共享初始化成功的运行进程。"""
    return {
        name
        for name in ("api", "worker")
        if compose["services"][name].get("depends_on", {}).get("app-init", {}).get("condition") != "service_completed_successfully"
    }


@pytest.mark.parametrize("service_name", ["api", "worker"])
@pytest.mark.parametrize("dependency", [{}, {"condition": "service_started"}])
def test_app_gate_rejects_missing_or_nonblocking_dependency(service_name, dependency):
    """恢复绕过共享初始化的依赖时，部署检查必须拒绝。"""
    compose = deepcopy(load_compose("docker-compose.yml"))
    compose["services"][service_name]["depends_on"]["app-init"] = dependency
    assert _app_dependency_violations(compose) == {service_name}


def _shared_initialization_calls(source):
    """检测运行进程重新取得共享定义同步职责。"""
    return {
        name
        for name in (
            "ensure_options_in_db",
            "ensure_builtin_mcp_servers_in_db",
            "init_builtin_skills",
            "initialize_agent_presets",
            "ensure_builtin_model_providers_in_db",
        )
        if f"{name}(" in source
    }


@pytest.mark.parametrize(
    "function_name",
    [
        "ensure_options_in_db",
        "ensure_builtin_mcp_servers_in_db",
        "init_builtin_skills",
        "initialize_agent_presets",
        "ensure_builtin_model_providers_in_db",
    ],
)
def test_runtime_guard_rejects_reintroduced_shared_initialization(function_name):
    """逐项恢复共享写入调用时，职责检查报告真实调用名。"""
    assert _shared_initialization_calls(f"await {function_name}(session)") == {function_name}


def test_runtime_processes_only_validate_schema_version() -> None:
    """API 与 worker 不得重新取得建表、DDL 收敛或 checkpoint setup ownership。"""
    root = project_root()
    migration_source = (root / "backend/yuxi/migrations/main.py").read_text()
    runtime_sources = {
        "api": (root / "backend/yuxi/bootstrap/api.py").read_text(),
        "worker": (root / "backend/yuxi/bootstrap/worker.py").read_text(),
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
        assert _shared_initialization_calls(source) == set()


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_api_and_worker_do_not_expose_removed_docker_api_configuration(filename: str):
    """API/worker 不得保留已删除 Docker daemon 通道的配置表面。"""
    assert _forbidden_api_worker_env_keys(load_compose(filename)) == set()


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_api_and_worker_use_distinct_local_runtime_directories(filename: str):
    """日志与无状态缓存必须使用服务独立且位于 saves 外的运行目录。"""
    compose = load_compose(filename)

    assert _runtime_directory_violations(compose) == set()
    assert len(set(EXPECTED_RUNTIME_DIRS.values())) == len(EXPECTED_RUNTIME_DIRS)
    assert all(not path.startswith("/app/saves/") for path in EXPECTED_RUNTIME_DIRS.values())


def test_integration_cleanup_does_not_access_docker_daemon():
    """集成测试清理不得要求 API 容器直接访问 Docker daemon。"""
    source = "\n".join((project_root() / path).read_text() for path in SANDBOX_CLEANUP_OWNER_PATHS)

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
    compose = deepcopy(load_compose("docker-compose.yml"))
    compose["services"][service_name].setdefault("volumes", []).append(mount)

    assert _forbidden_api_worker_mounts(compose) == {(service_name, expected_target)}


def test_environment_guard_detects_reintroduced_docker_api_configuration():
    """恢复旧 Docker API 环境变量时，边界 guard 必须报告对应服务。"""
    compose = deepcopy(load_compose("docker-compose.yml"))
    compose["services"]["api"]["environment"]["YUXI_DOCKER_API_BASE"] = "http://localhost"

    assert _forbidden_api_worker_env_keys(compose) == {("api", "YUXI_DOCKER_API_BASE")}


@pytest.mark.parametrize("service_name", ["api", "worker"])
def test_runtime_directory_guard_detects_shared_save_directory(service_name: str):
    """把运行目录恢复到共享 saves 时，边界 guard 必须报告对应服务。"""
    compose = deepcopy(load_compose("docker-compose.yml"))
    compose["services"][service_name]["environment"]["YUXI_RUNTIME_DIR"] = "/app/saves/runtime"

    assert _runtime_directory_violations(compose) == {(service_name, "/app/saves/runtime")}


@pytest.mark.parametrize("marker", sorted(FORBIDDEN_DIRECT_DOCKER_ACCESS_MARKERS))
def test_cleanup_guard_detects_reintroduced_direct_docker_access(marker: str):
    """恢复 Docker socket 清理路径时，边界 guard 必须报告对应标记。"""
    assert _forbidden_direct_docker_access(f"cleanup command: {marker}") == {marker}


def _maps_mjs_to_javascript_mime(source: str) -> bool:
    """检测 nginx 配置是否为 .mjs 声明 JavaScript MIME。"""
    return re.search(r"application/javascript\s+mjs\s*;", source) is not None


def test_nginx_maps_mjs_to_javascript_mime():
    """模块 Worker 脚本必须以 JavaScript MIME 提供。"""
    source = (project_root() / "docker" / "nginx" / "nginx.conf").read_text()

    assert _maps_mjs_to_javascript_mime(source)


def test_mime_guard_detects_missing_mjs_mapping():
    """删除 .mjs 映射后部署契约 guard 必须失败。"""
    assert _maps_mjs_to_javascript_mime("types { application/javascript js; }") is False


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_shipping_selects_cube_or_container_with_explicit_runner_boundary(filename):
    """API/worker 只保存配置；Docker socket 只属于 container profile 的 runner。"""
    compose = load_compose(filename)
    services = compose["services"]
    assert "sandbox-provisioner" not in services
    for name in ("api", "worker"):
        env = services[name]["environment"]
        assert env["SANDBOX_BACKEND"] == "${SANDBOX_BACKEND:-container}"
        assert env["SANDBOX_RUNNER_URL"] == "${SANDBOX_RUNNER_URL:-http://sandbox-runner:8090}"
        assert env["CUBE_API_URL"] == "${CUBE_API_URL:-}"
        assert env["CUBE_NODE_USER_DATA_DIR"] == "${CUBE_NODE_USER_DATA_DIR:-/data/shared/yuxi/threads}"
        assert env["CUBE_NODE_SKILL_PROJECTION_DIR"] == "${CUBE_NODE_SKILL_PROJECTION_DIR:-/data/shared/yuxi/skill-projections}"
        assert not any(key.startswith("SANDBOX_CONTAINER_") for key in env)
        assert env["SANDBOX_IDLE_SECONDS"] == "${SANDBOX_IDLE_SECONDS:-300}"
        assert not any(key.startswith("SANDBOX_PROVISIONER") for key in env)
        targets = {_volume_target(volume) for volume in services[name].get("volumes") or []}
        assert "/var/run/docker.sock" not in targets
    runner = services["sandbox-runner"]
    assert runner["profiles"] == ["container"]
    targets = {_volume_target(volume) for volume in runner.get("volumes") or []}
    assert {"/var/run/docker.sock", "/sandbox-data", "/sandbox-skill-projections"} <= targets
    assert services["sandbox-image"]["profiles"] == ["container"]


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_filesystem_initializer_does_not_allocate_an_implicit_compose_network(filename):
    """只修改目录属主的初始化器不能额外占用宿主子网。"""
    compose = load_compose(filename)
    initializer = compose["services"]["storage-init"]
    assert initializer.get("network_mode") == "none"


_ENV_DIR_PATTERN = re.compile(
    r"""RAPIDOCR_MODEL_DIR=\s*("[^"]*"|'[^']*'|[^\s]+?)(?:\s+\w+=.*|)\s*$""",
    re.MULTILINE,
)


def _rapidocr_compose_dirs() -> dict[str, str]:
    dirs = {}
    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        env = load_compose(name).get("x-api-worker-env") or {}
        dirs[name] = str(env.get("RAPIDOCR_MODEL_DIR", ""))
    return dirs


def _rapidocr_model_preinstall_defects(dockerfile: str, compose_dirs: dict[str, str]) -> list[str]:
    """检查镜像是否把 rapidocr 包内模型预置到 RAPIDOCR_MODEL_DIR 指向的目录，并与 Compose 注入值一致。"""
    env_match = _ENV_DIR_PATTERN.search(dockerfile)
    if env_match is None:
        return ["缺少 RAPIDOCR_MODEL_DIR 配置"]
    model_dir = env_match.group(1).strip("\"'").rstrip("/")

    copy_match = re.search(r"\*\.onnx\s+(\S+?)/?\s*(?:\\\n|\n)", dockerfile)
    if copy_match is None:
        return [f"未把 rapidocr 包内 *.onnx 拷贝到 {model_dir}"]

    copy_target = copy_match.group(1).rstrip("/")
    if copy_target != model_dir:
        return [f"模型拷贝目标 {copy_target} 与 RAPIDOCR_MODEL_DIR {model_dir} 不一致"]
    for name, injected in compose_dirs.items():
        if injected.rstrip("/") != model_dir:
            return [f"{name} 注入的 RAPIDOCR_MODEL_DIR {injected} 与镜像预置目录 {model_dir} 不一致"]
    return []


def test_api_image_preinstalls_bundled_rapidocr_models() -> None:
    """运行时联网下载 OCR 模型会拖慢首次解析且离线部署不可行，api 镜像必须预置包内模型。"""
    dockerfile = (project_root() / "docker/api.Dockerfile").read_text(encoding="utf-8")
    assert _rapidocr_model_preinstall_defects(dockerfile, _rapidocr_compose_dirs()) == []


def test_rapidocr_preinstall_guard_detects_missing_or_mismatched_copy() -> None:
    """负控：删除拷贝步骤、拷错目录或 Compose 注入值漂移时 guard 必须报出对应缺陷。"""
    dockerfile = (project_root() / "docker/api.Dockerfile").read_text(encoding="utf-8")
    compose_dirs = _rapidocr_compose_dirs()

    without_copy = "\n".join(line for line in dockerfile.splitlines() if "*.onnx" not in line)
    assert "未把 rapidocr 包内" in _rapidocr_model_preinstall_defects(without_copy, compose_dirs)[0]

    mismatched = dockerfile.replace("*.onnx /home/yuxi/.cache/rapidocr/models/", "*.onnx /opt/rapidocr/models/")
    assert "模型拷贝目标" in _rapidocr_model_preinstall_defects(mismatched, compose_dirs)[0]

    drifted = {**compose_dirs, "docker-compose.yml": "/srv/other/models"}
    assert "docker-compose.yml 注入" in _rapidocr_model_preinstall_defects(dockerfile, drifted)[0]
