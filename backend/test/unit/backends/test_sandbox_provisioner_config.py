from __future__ import annotations

import importlib.util
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient


MODULE_NAME = "sandbox_provisioner_app_for_test"


def _find_module_path() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "docker" / "sandbox_provisioner" / "app.py"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("docker/sandbox_provisioner/app.py not found from test path")


MODULE_PATH = _find_module_path()


def _load_module():
    existing = sys.modules.get(MODULE_NAME)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _docker_backend(module, tmp_path, run_container):
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._lock = threading.RLock()
    backend._sandbox_locks = {}
    backend._delete_slots = threading.BoundedSemaphore(16)
    backend._stop_timeout_seconds = 2
    backend._container_limits = module.sandbox_container_limits()
    backend._container_port = 8080
    backend._network_prefix = "yuxi-know-sandbox"
    backend._network_pool = None
    backend._sandbox_image = "sandbox-image"
    backend._container_prefix = "yuxi-sandbox"
    backend._sandbox_env = {}
    backend._health_timeout_seconds = 1
    backend._user_data_host_path = str(tmp_path)
    backend._skill_projections_host_path = str(tmp_path.parent / "skill-projections")
    backend._user_data_container_path = tmp_path
    backend._skill_projections_container_path = tmp_path.parent / "skill-projections"
    backend._user_data_container_path.mkdir(exist_ok=True)
    backend._skill_projections_container_path.mkdir(exist_ok=True)
    backend._client = SimpleNamespace(containers=SimpleNamespace(run=run_container))
    return backend


def test_directory_validation_identifies_missing_mount_prerequisite(tmp_path):
    module = _load_module()
    root = tmp_path / "skill-projections"
    root.mkdir()

    with pytest.raises(ValueError, match="^skill projection must reference"):
        module.LocalContainerProvisionerBackend._validate_directory_without_symlinks(
            root,
            ("fresh-user",),
            label="skill projection",
        )


def _docker_backend_with_running_container(monkeypatch, tmp_path):
    module = _load_module()
    captured = []

    class FakeContainer:
        name = "yuxi-sandbox-sandbox-1"
        status = "running"
        labels = {"thread-id": "thread-1"}
        attrs = {"State": {"Status": "running"}}

        def reload(self):
            return None

    backend = _docker_backend(
        module,
        tmp_path,
        lambda image, **kwargs: captured.append((image, kwargs)) or FakeContainer(),
    )
    (tmp_path / "shared/user-1/workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "skill-projections/user-1").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(backend, "_get_container", lambda _sandbox_id: None)
    monkeypatch.setattr(backend, "_ensure_network", backend._network_name)
    monkeypatch.setattr(module, "wait_for_sandbox_ready", lambda _url, timeout_seconds: True)
    return module, backend, captured


def test_canonical_backend_name(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    assert module.canonical_backend_name("docker") == "docker"
    assert module.canonical_backend_name("kubernetes") == "kubernetes"


def test_merged_sandbox_env_user_values_override_global(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    assert module.merged_sandbox_env(
        {"SHARED": "global", "GLOBAL_ONLY": "value"},
        {"SHARED": "user", "USER_ONLY": "value"},
    ) == {
        "SHARED": "user",
        "GLOBAL_ONLY": "value",
        "USER_ONLY": "value",
    }


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (
            "core",
            {
                "DISABLE_BROWSER": "true",
                "DISABLE_MCP_BROWSER": "true",
                "DISABLE_VNC": "true",
                "DISABLE_JUPYTER": "true",
                "DISABLE_CODE_SERVER": "true",
                "DISABLE_NODEJS_REPL": "true",
            },
        ),
        (
            "browser",
            {
                "DISABLE_BROWSER": "false",
                "DISABLE_MCP_BROWSER": "false",
                "DISABLE_VNC": "false",
                "DISABLE_JUPYTER": "true",
                "DISABLE_CODE_SERVER": "true",
                "DISABLE_NODEJS_REPL": "true",
            },
        ),
        (
            "full",
            {
                "DISABLE_BROWSER": "false",
                "DISABLE_MCP_BROWSER": "false",
                "DISABLE_VNC": "false",
                "DISABLE_JUPYTER": "false",
                "DISABLE_CODE_SERVER": "false",
                "DISABLE_NODEJS_REPL": "false",
            },
        ),
    ],
)
def test_sandbox_runtime_profile_environment(profile, expected):
    module = _load_module()

    assert module.sandbox_runtime_environment(profile) == expected


def test_sandbox_runtime_profile_rejects_unknown_value():
    module = _load_module()

    with pytest.raises(RuntimeError, match="invalid SANDBOX_RUNTIME_PROFILE"):
        module.sandbox_runtime_profile("unknown")

    with pytest.raises(RuntimeError, match="invalid SANDBOX_RUNTIME_PROFILE"):
        module.sandbox_runtime_profile("")


def test_docker_network_pool_reads_dedicated_subnets(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("DOCKER_ADDRESS_POOL", "10.253.240.0/20")
    monkeypatch.setenv("DOCKER_SUBNET_PREFIX", "28")

    address_pool, subnet_prefix = module.docker_network_pool()

    assert str(address_pool) == "10.253.240.0/20"
    assert subnet_prefix == 28


@pytest.mark.parametrize(
    ("address_pool", "subnet_prefix", "error_match"),
    [
        ("10.253.240.1/20", "28", "invalid DOCKER_ADDRESS_POOL"),
        ("fd00::/64", "80", "must be an IPv4 network"),
        ("10.253.240.0/20", "30", "between the address pool prefix and 29"),
        ("10.253.240.0/20", "invalid", "must be an integer"),
    ],
)
def test_docker_network_pool_rejects_invalid_configuration(monkeypatch, address_pool, subnet_prefix, error_match):
    module = _load_module()
    monkeypatch.setenv("DOCKER_ADDRESS_POOL", address_pool)
    monkeypatch.setenv("DOCKER_SUBNET_PREFIX", subnet_prefix)

    with pytest.raises(RuntimeError, match=error_match):
        module.docker_network_pool()


def test_docker_network_pool_rejects_prefix_without_pool(monkeypatch):
    module = _load_module()
    monkeypatch.delenv("DOCKER_ADDRESS_POOL", raising=False)
    monkeypatch.setenv("DOCKER_SUBNET_PREFIX", "28")

    with pytest.raises(RuntimeError, match="requires DOCKER_ADDRESS_POOL"):
        module.docker_network_pool()


def test_sandbox_container_stop_timeout_defaults_to_two_seconds(monkeypatch):
    module = _load_module()
    monkeypatch.delenv("SANDBOX_CONTAINER_STOP_TIMEOUT_SECONDS", raising=False)

    assert module.sandbox_container_stop_timeout_seconds() == 2


def test_sandbox_container_stop_timeout_reads_configured_value():
    module = _load_module()

    assert module.sandbox_container_stop_timeout_seconds("3") == 3


@pytest.mark.parametrize("value", ["", "0", "invalid"])
def test_sandbox_container_stop_timeout_rejects_invalid_value(value):
    module = _load_module()

    with pytest.raises(RuntimeError, match="SANDBOX_CONTAINER_STOP_TIMEOUT_SECONDS"):
        module.sandbox_container_stop_timeout_seconds(value)


def test_sandbox_delete_concurrency_rejects_invalid_configuration():
    module = _load_module()

    assert module.sandbox_delete_concurrency("16") == 16
    with pytest.raises(RuntimeError, match="must be >= 1"):
        module.sandbox_delete_concurrency("0")
    with pytest.raises(RuntimeError, match="must be an integer"):
        module.sandbox_delete_concurrency("invalid")


def test_sandbox_container_limits_defaults_and_environment_overrides(monkeypatch):
    module = _load_module()
    for name in ("SANDBOX_MEM_LIMIT", "SANDBOX_CPUS", "SANDBOX_PIDS_LIMIT"):
        monkeypatch.delenv(name, raising=False)

    assert module.sandbox_container_limits() == {
        "mem_limit": "2g",
        "nano_cpus": 2_000_000_000,
        "pids_limit": 512,
    }

    monkeypatch.setenv("SANDBOX_MEM_LIMIT", "512m")
    monkeypatch.setenv("SANDBOX_CPUS", "1.5")
    monkeypatch.setenv("SANDBOX_PIDS_LIMIT", "256")

    assert module.sandbox_container_limits() == {
        "mem_limit": "512m",
        "nano_cpus": 1_500_000_000,
        "pids_limit": 256,
    }


@pytest.mark.parametrize(
    ("mem_limit", "cpus", "pids_limit", "error_match"),
    [
        ("", "2", "512", "SANDBOX_MEM_LIMIT"),
        ("lots", "2", "512", "SANDBOX_MEM_LIMIT"),
        ("0g", "2", "512", "SANDBOX_MEM_LIMIT"),
        ("2g", "nan", "512", "SANDBOX_CPUS"),
        ("2g", "inf", "512", "SANDBOX_CPUS"),
        ("2g", "1e-10", "512", "SANDBOX_CPUS"),
        ("2g", "2", "0", "SANDBOX_PIDS_LIMIT"),
        ("2g", "2", "many", "SANDBOX_PIDS_LIMIT"),
    ],
)
def test_sandbox_container_limits_reject_invalid_configuration(
    mem_limit, cpus, pids_limit, error_match
):
    """非法上限显式失败，不回退到默认值；每个案例覆盖一条守卫路径。"""
    module = _load_module()

    with pytest.raises(RuntimeError, match=error_match):
        module.sandbox_container_limits(
            mem_limit=mem_limit, cpus=cpus, pids_limit=pids_limit
        )


def test_sandbox_container_limits_reject_invalid_environment_at_startup(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("SANDBOX_CPUS", "two")

    with pytest.raises(RuntimeError, match="SANDBOX_CPUS"):
        module.sandbox_container_limits()


def test_docker_backend_init_fails_on_invalid_container_limits(monkeypatch):
    """非法上限让构造期显式失败，不创建 docker client 也不带病启动。"""
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    monkeypatch.setenv("SANDBOX_CPUS", "nan")
    errors_module = ModuleType("docker.errors")
    errors_module.DockerException = type("DockerException", (Exception,), {})
    docker_module = ModuleType("docker")
    docker_module.errors = errors_module
    docker_module.from_env = lambda: pytest.fail(
        "docker client was created before container limits were validated"
    )
    monkeypatch.setitem(sys.modules, "docker", docker_module)
    monkeypatch.setitem(sys.modules, "docker.errors", errors_module)

    with pytest.raises(RuntimeError, match="SANDBOX_CPUS"):
        module.LocalContainerProvisionerBackend()


def test_docker_create_applies_resource_limits_to_sandbox_container(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    _module, backend, captured = _docker_backend_with_running_container(
        monkeypatch, tmp_path
    )

    backend.create("sandbox-1", "thread-1", "user-1")

    run_kwargs = captured[0][1]
    assert run_kwargs["mem_limit"] == "2g"
    assert run_kwargs["nano_cpus"] == 2_000_000_000
    assert run_kwargs["pids_limit"] == 512


def test_normalize_env_converts_values_to_strings(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    assert module.normalize_env({"A": 1, "B": None, "": "ignored"}) == {"A": "1", "B": ""}


def test_wait_for_sandbox_ready_uses_bounded_fast_backoff(monkeypatch):
    module = _load_module()
    clock = SimpleNamespace(now=0.0, sleeps=[])
    probe_timeouts = []

    def fake_sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds

    class FailingOpener:
        def open(self, _url, *, timeout):
            probe_timeouts.append(timeout)
            raise OSError("not ready")

    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(monotonic=lambda: clock.now, sleep=fake_sleep),
    )
    monkeypatch.setattr(module.request, "build_opener", lambda *_args: FailingOpener())
    monkeypatch.setattr(
        module,
        "random",
        SimpleNamespace(uniform=lambda _lower, _upper: 1.0),
        raising=False,
    )

    assert module.wait_for_sandbox_ready("http://sandbox", timeout_seconds=2.2) is False
    assert clock.sleeps == pytest.approx([0.05, 0.1, 0.2, 0.4, 0.8, 0.65])
    assert all(0 < timeout <= 3 for timeout in probe_timeouts)
    assert clock.now == pytest.approx(2.2)


def test_wait_for_sandbox_ready_returns_immediately_when_first_probe_succeeds(monkeypatch):
    module = _load_module()
    sleeps = []

    class SuccessfulResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class SuccessfulOpener:
        def open(self, _url, *, timeout):
            assert timeout == 3
            return SuccessfulResponse()

    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(monotonic=lambda: 0.0, sleep=sleeps.append),
    )
    monkeypatch.setattr(module.request, "build_opener", lambda *_args: SuccessfulOpener())

    assert module.wait_for_sandbox_ready("http://sandbox", timeout_seconds=30) is True
    assert sleeps == []


def test_local_container_identity_validation_rejects_unsafe_path_segments(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend_cls = module.LocalContainerProvisionerBackend

    assert backend_cls._validate_thread_id("thread-1_2") == "thread-1_2"
    assert backend_cls._validate_uid("user-1_2") == "user-1_2"
    canonical_workdir = "projects/11111111-1111-4111-8111-111111111111"
    assert module.normalize_workdir_path(canonical_workdir) == canonical_workdir
    assert module.normalize_workdir_path("agents/skills") == "agents/skills"

    for value in ["../escape", "thread/name", "thread name", "thread;rm", "thread.name"]:
        with pytest.raises(ValueError):
            backend_cls._validate_thread_id(value)

    for value in ["../user", "user/name", "user name", "user;rm", "user.name"]:
        with pytest.raises(ValueError):
            backend_cls._validate_uid(value)

    for value in [
        "../workdir",
        "/workdir",
        "agents//skills",
        "https://example.com/workdir",
    ]:
        with pytest.raises(ValueError):
            module.normalize_workdir_path(value)


def test_docker_host_paths_require_explicit_storage_mounts(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._user_data_host_path = None
    backend._skill_projections_host_path = None
    backend._client = SimpleNamespace(
        api=SimpleNamespace(
            inspect_container=lambda _container_id: {
                "Mounts": [
                    {"Destination": "/app/user-data", "Source": "/host/user-data"},
                    {"Destination": "/app/skill-projections", "Source": "/host/skill-projections"},
                ]
            }
        )
    )
    monkeypatch.setenv("HOSTNAME", "provisioner")

    backend._resolve_host_paths()

    assert backend._user_data_host_path == "/host/user-data"
    assert backend._skill_projections_host_path == "/host/skill-projections"

    backend._user_data_host_path = None
    backend._skill_projections_host_path = None
    backend._client.api.inspect_container = lambda _container_id: {
        "Mounts": [{"Destination": "/app/saves", "Source": "/host/legacy"}]
    }
    with pytest.raises(RuntimeError, match="explicit UserWorkspace/Skill"):
        backend._resolve_host_paths()


def test_memory_backend_accepts_runtime_thread_id(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = module.MemoryProvisionerBackend()

    record = backend.create(
        "sandbox-1",
        "child-thread",
        "user-1",
    )

    assert record.sandbox_id == "sandbox-1"
    assert backend.discover("sandbox-1") is record


def test_memory_backend_concurrent_get_or_create_returns_one_generation(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = module.MemoryProvisionerBackend()

    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(
            executor.map(
                lambda _index: backend.create(
                    "sandbox-shared",
                    "root-thread",
                    "user-1",
                    workdir_path="projects/11111111-1111-4111-8111-111111111111",
                ),
                range(32),
            )
        )

    assert len({id(record) for record in records}) == 1
    assert len({record.generation for record in records}) == 1
    assert records[0].workdir_path == "projects/11111111-1111-4111-8111-111111111111"

    with pytest.raises(ValueError, match="does not match"):
        backend.create(
            "sandbox-shared", "root-thread", "user-1", workdir_path="projects/22222222-2222-4222-8222-222222222222"
        )

    with pytest.raises(module.SandboxGenerationMismatchError, match="generation"):
        backend.delete("sandbox-shared", expected_generation="stale-generation")
    assert backend.discover("sandbox-shared") is records[0]

    backend.delete("sandbox-shared", expected_generation=records[0].generation)
    assert backend.discover("sandbox-shared") is None


def test_idle_reaper_does_not_delete_or_forget_new_generation(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    class FakeBackend:
        def __init__(self):
            self.generation = "generation-2"
            self.deleted = []

        def delete(self, sandbox_id, *, expected_generation=None):
            self.deleted.append((sandbox_id, expected_generation))
            if expected_generation != self.generation:
                raise module.SandboxGenerationMismatchError("stale generation")

    backend = FakeBackend()
    reaper = module.SandboxIdleReaper(backend)
    reaper.touch("sandbox-1", generation="generation-1")
    reaper._last_activity_at["sandbox-1"] = ("generation-1", 0)
    expired = reaper._collect_expired_sandboxes()
    reaper.touch("sandbox-1", generation="generation-2")

    reaper._delete_expired_sandbox(*expired[0])

    assert backend.deleted == []
    assert reaper._last_activity_at["sandbox-1"][0] == "generation-2"


def test_operation_pins_drain_started_requests_and_block_new_requests_during_delete(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    pins = module.SandboxOperationPins()
    delete_started = threading.Event()
    delete_finished = threading.Event()
    next_request_started = threading.Event()

    pins.acquire("sandbox-1")

    def delete_generation():
        pins.begin_delete("sandbox-1")
        delete_started.set()
        assert not next_request_started.is_set()
        pins.end_delete("sandbox-1")
        delete_finished.set()

    delete_thread = threading.Thread(target=delete_generation)
    delete_thread.start()
    with pins._condition:
        assert pins._condition.wait_for(lambda: "sandbox-1" in pins._deleting, timeout=1)
    assert not delete_started.is_set()

    def next_request():
        pins.acquire("sandbox-1")
        next_request_started.set()
        pins.release("sandbox-1")

    next_thread = threading.Thread(target=next_request)
    next_thread.start()
    assert not next_request_started.wait(timeout=0.05)

    pins.release("sandbox-1")
    assert delete_finished.wait(timeout=1)
    assert next_request_started.wait(timeout=1)
    delete_thread.join(timeout=1)
    next_thread.join(timeout=1)


def test_docker_mount_checks_reject_uploads_and_outputs_mounts(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._user_data_host_path = str(tmp_path)
    backend._skill_projections_host_path = str(tmp_path.parent / "skill-projections")

    workspace = tmp_path / "shared" / "user-1" / "workspace"
    skills = tmp_path.parent / "skill-projections" / "user-1"
    container = SimpleNamespace(
        attrs={
            "Mounts": [
                {"Destination": "/home/gem/user-data", "Source": str(workspace)},
                {"Destination": "/home/gem/skills", "Source": str(skills), "RW": False, "Mode": "ro"},
            ]
        }
    )

    assert backend._has_expected_user_data_mounts(container, "user-1") is True
    assert backend._is_expected_skills_mount(container, "user-1") is True
    assert backend._is_expected_skills_mount(container, "user-2") is False

    container.attrs["Mounts"][1]["RW"] = True
    assert backend._is_expected_skills_mount(container, "user-1") is False
    container.attrs["Mounts"][1]["RW"] = False

    container.attrs["Mounts"].append(
        {"Destination": "/home/gem/user-data/outputs", "Source": str(tmp_path / "legacy-outputs")}
    )
    assert backend._has_expected_user_data_mounts(container, "user-1") is False
    container.attrs["Mounts"].pop()

    container.attrs["Mounts"].append(
        {"Destination": "/home/gem/user-data/uploads", "Source": str(tmp_path / "legacy-uploads")}
    )
    assert backend._has_expected_user_data_mounts(container, "user-1") is False


def test_docker_sandbox_mounts_shared_workspace_without_thread_history_projection(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module, backend, captured = _docker_backend_with_running_container(monkeypatch, tmp_path)

    backend.create("sandbox-1", "thread-1", "user-1")

    volumes = captured[0][1]["volumes"]
    destinations = {mount["bind"] for mount in volumes.values()}
    assert destinations == {
        "/home/gem/user-data",
        "/home/gem/skills",
    }
    assert all("/agents/chats" not in destination for destination in destinations)


def test_docker_project_workdir_contract_mounts_shared_posix_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module, backend, captured = _docker_backend_with_running_container(monkeypatch, tmp_path)

    workdir = tmp_path / "shared" / "user-1" / "workspace" / "projects" / "11111111-1111-4111-8111-111111111111"
    workdir.mkdir(parents=True)
    record = backend.create(
        "sandbox-project", "root-thread", "user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111"
    )

    run_kwargs = captured[0][1]
    destinations = {mount["bind"] for mount in run_kwargs["volumes"].values()}
    assert destinations == {
        "/home/gem/user-data",
        "/home/gem/skills",
    }
    assert run_kwargs["working_dir"] == "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111"
    assert run_kwargs["labels"]["workdir-path"] == "projects/11111111-1111-4111-8111-111111111111"
    assert {key: run_kwargs["environment"][key] for key in ("USER", "USER_UID", "USER_GID")} == {
        "USER": "gem",
        "USER_UID": "1000",
        "USER_GID": "1000",
    }
    assert record.workdir_path is None  # Fake container has no Docker labels; real discover reads the label.


def test_docker_rejects_symlink_in_workspace_identity_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    _module, backend, captured = _docker_backend_with_running_container(monkeypatch, tmp_path)
    shared = backend._user_data_container_path / "shared"
    outside = tmp_path.parent / "outside-user-workspace"
    shared.mkdir(exist_ok=True)
    outside.mkdir()
    (shared / "user-1/workspace").rmdir()
    (shared / "user-1").rmdir()
    (shared / "user-1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="without symlinks"):
        backend.create("sandbox-project", "root-thread", "user-1")

    assert captured == []


def test_docker_rejects_rebinding_existing_runtime_to_another_workdir(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    class FakeContainer:
        status = "running"
        labels = {"thread-id": "root-thread", "workdir-path": "projects/11111111-1111-4111-8111-111111111111"}
        attrs = {"State": {"Status": "running"}}

        def reload(self):
            return None

    backend = _docker_backend(module, tmp_path, lambda *_args, **_kwargs: pytest.fail("sandbox was recreated"))
    monkeypatch.setattr(backend, "_get_container", lambda _sandbox_id: FakeContainer())

    with pytest.raises(ValueError, match="workdir identity"):
        backend.create(
            "sandbox-project", "root-thread", "user-1", workdir_path="projects/22222222-2222-4222-8222-222222222222"
        )


def test_kubernetes_mount_check_rejects_uploads_and_outputs_mounts(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = object.__new__(module.KubernetesProvisionerBackend)
    backend._user_data_pvc = "user-data"
    backend._skill_pvc = "skills"
    pod = SimpleNamespace(
        spec=SimpleNamespace(
            volumes=[
                SimpleNamespace(
                    name="user-data",
                    persistent_volume_claim=SimpleNamespace(claim_name="user-data"),
                ),
                SimpleNamespace(
                    name="skills-data",
                    persistent_volume_claim=SimpleNamespace(claim_name="skills"),
                ),
            ],
            containers=[
                SimpleNamespace(
                    name="sandbox",
                    volume_mounts=[
                        SimpleNamespace(
                            mount_path="/home/gem/user-data",
                            name="user-data",
                            sub_path="shared/user-1/workspace",
                        ),
                        SimpleNamespace(
                            mount_path="/home/gem/skills",
                            name="skills-data",
                            sub_path="skill-projections/user-1",
                            read_only=True,
                        ),
                    ],
                )
            ],
        )
    )

    assert backend._pod_has_expected_mounts(
        pod,
        uid="user-1",
    )
    pod.spec.containers[0].volume_mounts[1].read_only = False
    assert not backend._pod_has_expected_mounts(
        pod,
        uid="user-1",
    )
    pod.spec.containers[0].volume_mounts[1].read_only = True
    pod.spec.containers[0].volume_mounts.append(
        SimpleNamespace(
            mount_path="/home/gem/user-data/outputs",
            sub_path="threads/parent-thread/user-data/outputs",
        )
    )
    assert not backend._pod_has_expected_mounts(
        pod,
        uid="user-1",
    )
    pod.spec.containers[0].volume_mounts.pop()

    pod.spec.containers[0].volume_mounts.append(
        SimpleNamespace(
            mount_path="/home/gem/user-data/uploads",
            sub_path="threads/parent-thread/user-data/uploads",
        )
    )
    assert not backend._pod_has_expected_mounts(pod, uid="user-1")
    pod.spec.containers[0].volume_mounts.pop()

    pod.spec.volumes[0].persistent_volume_claim.claim_name = "old-user-data"
    assert not backend._pod_has_expected_mounts(pod, uid="user-1")


def test_ephemeral_mount_validation_rejects_exact_projects_root(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    container = SimpleNamespace(attrs={"Mounts": [{"Destination": "/home/gem/projects"}]})

    assert not module.LocalContainerProvisionerBackend._has_no_persistent_file_mounts(container)

    backend = object.__new__(module.KubernetesProvisionerBackend)
    pod = SimpleNamespace(
        spec=SimpleNamespace(
            volumes=[],
            containers=[
                SimpleNamespace(
                    name="sandbox",
                    volume_mounts=[SimpleNamespace(mount_path="/home/gem/projects")],
                )
            ],
        )
    )
    assert not backend._pod_has_expected_mounts(
        pod,
        uid="remote-skill-user",
        ephemeral_storage=True,
    )


def test_management_api_requires_bearer_token(monkeypatch):
    token = "test-provisioner-token-that-is-long-enough"
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", token)
    module = _load_module()

    with TestClient(module.app) as client:
        assert client.get("/api/sandboxes").status_code == 401
        assert client.get("/api/sandboxes", headers={"Authorization": "Bearer wrong"}).status_code == 401

        response = client.get(
            "/api/sandboxes",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {"sandboxes": [], "count": 0}


def test_authenticated_management_api_returns_proxied_sandbox_url(monkeypatch):
    token = "test-provisioner-token-that-is-long-enough"
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", token)
    monkeypatch.setenv("PROVISIONER_PUBLIC_URL", "http://sandbox-provisioner:8002")
    module = _load_module()
    headers = {"Authorization": f"Bearer {token}"}
    sandbox_id = "sandbox-auth-test"

    with TestClient(module.app) as client:
        create_response = client.post(
            "/api/sandboxes",
            headers=headers,
            json={
                "sandbox_id": sandbox_id,
                "thread_id": "thread-1",
                "uid": "user-1",
            },
        )
        list_response = client.get("/api/sandboxes", headers=headers)
        stale_delete_response = client.delete(
            f"/api/sandboxes/{sandbox_id}",
            headers=headers,
            params={"expected_generation": "stale-generation"},
        )
        delete_response = client.delete(
            f"/api/sandboxes/{sandbox_id}",
            headers=headers,
            params={"expected_generation": create_response.json()["generation"]},
        )

    expected_url = f"http://sandbox-provisioner:8002/api/sandboxes/{sandbox_id}/proxy"
    assert create_response.status_code == 200
    assert create_response.json()["sandbox_url"] == expected_url
    assert list_response.status_code == 200
    sandboxes = list_response.json()["sandboxes"]
    assert len(sandboxes) == 1
    assert sandboxes[0]["sandbox_id"] == sandbox_id
    assert sandboxes[0]["sandbox_url"] == expected_url
    assert sandboxes[0]["status"] == "Running"
    assert sandboxes[0]["generation"]
    assert sandboxes[0]["workdir_path"] is None
    assert stale_delete_response.status_code == 409
    assert delete_response.status_code == 200


def test_create_sandbox_forwards_environment_policy(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    calls = []

    def create(*_args, **kwargs):
        calls.append(kwargs)
        return module.SandboxRecord(sandbox_id="sandbox-1", sandbox_url="http://sandbox", status="Running")

    monkeypatch.setattr(module, "backend_impl", SimpleNamespace(create=create))
    monkeypatch.setattr(module.idle_reaper, "touch", lambda _sandbox_id, **_kwargs: None)

    module.create_sandbox(
        module.CreateSandboxRequest(
            sandbox_id="sandbox-1",
            thread_id="thread-1",
            uid="user-1",
            inherit_env=False,
        )
    )

    assert calls[0]["inherit_env"] is False


def test_create_sandbox_reports_address_pool_exhaustion_as_unavailable(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", "test-provisioner-token-that-is-long-enough")

    def create(*_args, **_kwargs):
        raise module.SandboxCapacityError("Docker Sandbox address pool 10.253.240.0/20 has no available /28 subnet")

    monkeypatch.setattr(module, "backend_impl", SimpleNamespace(create=create))

    with TestClient(module.app) as client:
        response = client.post(
            "/api/sandboxes",
            headers={"Authorization": "Bearer test-provisioner-token-that-is-long-enough"},
            json={
                "sandbox_id": "sandbox-capacity",
                "thread_id": "thread-1",
                "uid": "user-1",
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"].endswith("has no available /28 subnet")


def test_authenticated_proxy_forwards_request_without_management_token(monkeypatch):
    token = "test-provisioner-token-that-is-long-enough"
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", token)
    module = _load_module()
    headers = {"Authorization": f"Bearer {token}"}
    captured = []

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"X-Ignored": "value"})

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(upstream)

    clients = []

    def create_client(**kwargs):
        client = real_async_client(transport=transport, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        create_client,
    )

    with TestClient(module.app) as client:
        client.post(
            "/api/sandboxes",
            headers=headers,
            json={
                "sandbox_id": "sandbox-proxy-test",
                "thread_id": "thread-1",
                "uid": "user-1",
            },
        )
        response = client.get(
            "/api/sandboxes/sandbox-proxy-test/proxy/v1/sandbox",
            headers=headers,
            params={"detail": "full"},
        )
        second_response = client.get(
            "/api/sandboxes/sandbox-proxy-test/proxy/v1/sandbox",
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert second_response.status_code == 200
    assert len(clients) == 1
    assert clients[0].is_closed
    assert str(captured[0].url) == "http://agent-sandbox:8000/v1/sandbox?detail=full"
    assert str(captured[1].url) == "http://agent-sandbox:8000/v1/sandbox"
    assert "authorization" not in captured[0].headers
    assert "x-ignored" not in response.headers


@pytest.mark.asyncio
async def test_proxy_discovers_sandbox_outside_event_loop_thread(monkeypatch):
    token = "test-provisioner-token-that-is-long-enough"
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", token)
    module = _load_module()
    event_loop_thread = threading.get_ident()
    discover_threads = []

    def discover(sandbox_id):
        discover_threads.append(threading.get_ident())
        return module.SandboxRecord(
            sandbox_id=sandbox_id,
            sandbox_url="http://agent-sandbox:8000",
            status="Running",
        )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True}))
    http_client = real_async_client(transport=transport, timeout=None, follow_redirects=False, trust_env=False)
    module.app.state.http_client = http_client
    monkeypatch.setattr(module, "backend_impl", SimpleNamespace(discover=discover))
    request = module.Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/sandboxes/sandbox-proxy-test/proxy/v1/sandbox",
            "headers": [],
            "query_string": b"",
            "app": module.app,
        },
        receive,
    )

    try:
        response = await module.proxy_sandbox_request("sandbox-proxy-test", request, "v1/sandbox")
        body = b"".join([chunk async for chunk in response.body_iterator])
    finally:
        await http_client.aclose()

    assert body == b'{"ok":true}'
    assert discover_threads and discover_threads[0] != event_loop_thread


def test_docker_backend_uses_private_network_without_published_port(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    _, backend, captured = _docker_backend_with_running_container(monkeypatch, tmp_path)

    record = backend.create("sandbox-1", "thread-1", "user-1")

    assert record.sandbox_url == "http://yuxi-sandbox-sandbox-1:8080"
    assert captured[0][0] == "sandbox-image"
    assert captured[0][1]["network"] == "yuxi-know-sandbox-sandbox-1"
    assert "ports" not in captured[0][1]


def test_docker_runtime_profile_cannot_be_overridden_by_request(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module, backend, captured = _docker_backend_with_running_container(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "runtime_profile_name", "core")
    backend._sandbox_env = {"GLOBAL_VALUE": "global", "DISABLE_BROWSER": "false"}

    backend.create(
        "sandbox-1",
        "thread-1",
        "user-1",
        {"REQUEST_VALUE": "request", "DISABLE_BROWSER": "false"},
    )

    environment = captured[0][1]["environment"]
    assert environment["GLOBAL_VALUE"] == "global"
    assert environment["REQUEST_VALUE"] == "request"
    assert environment["DISABLE_BROWSER"] == "true"


def test_docker_ephemeral_sandbox_has_runtime_profile_and_identity_without_persistent_mounts(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module, backend, captured = _docker_backend_with_running_container(monkeypatch, tmp_path)
    backend._sandbox_env = {"GLOBAL_SECRET": "value"}
    uid = "remote-skill-ephemeral"

    backend.create("sandbox-1", "thread-1", uid, {"USER_SECRET": "value"}, inherit_env=False)

    run_config = captured[0][1]
    assert run_config["environment"] == {
        **module.sandbox_runtime_environment("core"),
        "USER": "gem",
        "USER_UID": "1000",
        "USER_GID": "1000",
    }
    assert run_config["volumes"] == {}
    assert run_config["labels"]["storage-mode"] == "ephemeral"
    assert not (backend._user_data_container_path / "shared" / uid).exists()
    assert not (backend._skill_projections_container_path / uid).exists()


def test_kubernetes_ephemeral_sandbox_uses_profile_and_only_empty_home(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    class FakeKubernetesClient:
        def __getattr__(self, _name):
            return lambda *_args, **kwargs: SimpleNamespace(**kwargs)

    backend = object.__new__(module.KubernetesProvisionerBackend)
    backend._client = FakeKubernetesClient()
    backend._sandbox_image = "sandbox-image"
    backend._container_port = 8080
    backend._user_data_pvc = "threads"
    backend._skill_pvc = "skills"
    backend._sandbox_env = {"GLOBAL_SECRET": "value"}

    pod = backend._build_pod_spec(
        "sandbox-1",
        "thread-1",
        "user-1",
        {"USER_SECRET": "value"},
        inherit_env=False,
    )

    assert pod.spec.automount_service_account_token is False
    assert {item.name: item.value for item in pod.spec.containers[0].env} == {
        **module.sandbox_runtime_environment("core"),
        "USER": "gem",
        "USER_UID": "1000",
        "USER_GID": "1000",
    }
    sandbox_mounts = {mount.mount_path for mount in pod.spec.containers[0].volume_mounts}
    assert sandbox_mounts == {"/home/gem"}
    assert pod.spec.init_containers == []
    assert [volume.name for volume in pod.spec.volumes] == ["home-dir"]
    assert pod.metadata.annotations["storage-mode"] == "ephemeral"
    assert backend._pod_has_expected_mounts(
        pod,
        uid="user-1",
        ephemeral_storage=True,
    )
    pod.spec.containers[0].volume_mounts.append(
        SimpleNamespace(mount_path="/home/gem/skills", name="skills-data", sub_path="skill-projections/user-1")
    )
    assert not backend._pod_has_expected_mounts(
        pod,
        uid="user-1",
        ephemeral_storage=True,
    )


def test_kubernetes_workdir_contract_uses_user_workspace_subpath(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()

    class FakeKubernetesClient:
        def __getattr__(self, _name):
            return lambda *_args, **kwargs: SimpleNamespace(**kwargs)

    backend = object.__new__(module.KubernetesProvisionerBackend)
    backend._client = FakeKubernetesClient()
    backend._sandbox_image = "sandbox-image"
    backend._container_port = 8080
    backend._user_data_pvc = "threads-rwx"
    backend._skill_pvc = "skills-rwx"
    backend._sandbox_env = {}

    pod = backend._build_pod_spec(
        "sandbox-1",
        "root-thread",
        "user-1",
        {},
        inherit_env=False,
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
    )

    sandbox = pod.spec.containers[0]
    mounts = {mount.mount_path: getattr(mount, "sub_path", None) for mount in sandbox.volume_mounts}
    assert sandbox.working_dir == "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111"
    assert mounts["/home/gem/user-data"] == "shared/user-1/workspace"
    assert mounts["/home/gem/skills"] == "skill-projections/user-1"
    skills_mount = next(mount for mount in sandbox.volume_mounts if mount.mount_path == "/home/gem/skills")
    assert skills_mount.read_only is True
    assert skills_mount.name == "skills-data"
    claims = {
        volume.name: volume.persistent_volume_claim.claim_name
        for volume in pod.spec.volumes
        if getattr(volume, "persistent_volume_claim", None) is not None
    }
    assert claims == {"user-data": "threads-rwx", "skills-data": "skills-rwx"}
    assert pod.metadata.annotations["workdir-path"] == "projects/11111111-1111-4111-8111-111111111111"
    assert pod.metadata.labels["managed-by"] == "yuxi-sandbox-provisioner"
    assert pod.spec.security_context.run_as_user == 0
    assert getattr(pod.spec.security_context, "fs_group", None) is None
    sandbox_env = {item.name: item.value for item in sandbox.env}
    assert {key: sandbox_env[key] for key in ("USER", "USER_UID", "USER_GID")} == {
        "USER": "gem",
        "USER_UID": "1000",
        "USER_GID": "1000",
    }
    init = pod.spec.init_containers[0]
    assert init.command == ["python", "-c"]
    assert "os.O_NOFOLLOW" in init.args[0]
    assert "('projects', '11111111-1111-4111-8111-111111111111')" in init.args[0]
    assert "MARKER_DIR = '.v072-runtime-identity'" in init.args[0]
    assert "os.mkdir(part, 0o700" in init.args[0]
    assert "os.fchmod(fd, 0o700)" in init.args[0]
    assert "follow_symlinks=False" in init.args[0]
    assert "'uploads'" not in init.args[0]
    assert "'outputs'" not in init.args[0]
    compile(init.args[0], "<kubernetes-storage-init>", "exec")


def test_kubernetes_storage_init_migrates_only_real_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    user_data = tmp_path / "user-data"
    skills_data = tmp_path / "skills-data"
    user_data.mkdir()
    skills_data.mkdir()
    workspace = user_data / "shared/user-1/workspace"
    workspace.mkdir(parents=True)
    (workspace / "projects/11111111-1111-4111-8111-111111111111").mkdir(parents=True)
    document = workspace / "old.txt"
    document.write_text("old", encoding="utf-8")
    document.chmod(0o666)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (workspace / "linked").symlink_to(outside, target_is_directory=True)

    script = module.kubernetes_storage_init_script("user-1", "projects/11111111-1111-4111-8111-111111111111")
    script = script.replace("/mnt/user-data", str(user_data)).replace("/mnt/skills-data", str(skills_data))
    script = script.replace("UID = 1000", f"UID = {os.getuid()}").replace("GID = 1000", f"GID = {os.getgid()}")

    exec(compile(script, "<kubernetes-storage-init>", "exec"), {})

    assert document.stat().st_mode & 0o777 == 0o600
    assert (workspace / "linked").is_symlink()
    assert outside.stat().st_mode & 0o777 == 0o755
    assert (workspace / "projects/11111111-1111-4111-8111-111111111111").is_dir()
    assert (skills_data / "skill-projections/user-1").is_dir()
    assert (user_data / ".v072-runtime-identity/workspace-user-1").exists()
    assert (skills_data / ".v072-runtime-identity/skills-user-1").exists()


def test_kubernetes_storage_init_rejects_missing_authoritative_workdir(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    user_data = tmp_path / "user-data"
    skills_data = tmp_path / "skills-data"
    user_data.mkdir()
    skills_data.mkdir()
    script = module.kubernetes_storage_init_script("user-1", "projects/missing")
    script = script.replace("/mnt/user-data", str(user_data)).replace("/mnt/skills-data", str(skills_data))
    script = script.replace("UID = 1000", f"UID = {os.getuid()}").replace("GID = 1000", f"GID = {os.getgid()}")

    with pytest.raises(FileNotFoundError):
        exec(compile(script, "<kubernetes-storage-init>", "exec"), {})

    assert not (user_data / "shared/user-1/workspace/projects/missing").exists()


def test_kubernetes_rejects_rebinding_existing_runtime_to_another_workdir(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = object.__new__(module.KubernetesProvisionerBackend)
    backend._lock = threading.Lock()
    backend._core_api = SimpleNamespace()
    kubernetes_module = ModuleType("kubernetes")
    client_module = ModuleType("kubernetes.client")
    rest_module = ModuleType("kubernetes.client.rest")

    class ApiException(Exception):
        pass

    rest_module.ApiException = ApiException
    client_module.rest = rest_module
    kubernetes_module.client = client_module
    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes_module)
    monkeypatch.setitem(sys.modules, "kubernetes.client", client_module)
    monkeypatch.setitem(sys.modules, "kubernetes.client.rest", rest_module)
    monkeypatch.setattr(
        backend,
        "discover",
        lambda _sandbox_id: module.SandboxRecord(
            sandbox_id="sandbox-1",
            sandbox_url="http://sandbox",
            generation="generation-1",
            workdir_path="projects/11111111-1111-4111-8111-111111111111",
        ),
    )
    monkeypatch.setattr(backend, "_discovered_matches_request", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(backend, "delete", lambda *_args, **_kwargs: pytest.fail("sandbox was deleted"))

    with pytest.raises(ValueError, match="identity does not match"):
        backend.create(
            "sandbox-1", "root-thread", "user-1", workdir_path="projects/22222222-2222-4222-8222-222222222222"
        )


def test_kubernetes_pod_conflict_is_revalidated_before_creating_service(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    kubernetes_module = ModuleType("kubernetes")
    client_module = ModuleType("kubernetes.client")
    rest_module = ModuleType("kubernetes.client.rest")

    class ApiException(Exception):
        def __init__(self, status=None):
            self.status = status

    rest_module.ApiException = ApiException
    client_module.rest = rest_module
    kubernetes_module.client = client_module
    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes_module)
    monkeypatch.setitem(sys.modules, "kubernetes.client", client_module)
    monkeypatch.setitem(sys.modules, "kubernetes.client.rest", rest_module)

    class FakeCoreApi:
        def create_namespaced_pod(self, **_kwargs):
            raise ApiException(status=409)

        def create_namespaced_service(self, **_kwargs):
            pytest.fail("service was created before pod identity validation")

    backend = object.__new__(module.KubernetesProvisionerBackend)
    backend._lock = threading.RLock()
    backend._core_api = FakeCoreApi()
    backend._namespace = "yuxi"
    backend._pod_name = lambda _sandbox_id: "pod-1"
    backend._service_name = lambda _sandbox_id: "service-1"
    backend.discover = lambda _sandbox_id: None
    backend._build_pod_spec = lambda *_args, **_kwargs: SimpleNamespace()
    backend._discovered_matches_request = lambda *_args, **_kwargs: False

    with pytest.raises(ValueError, match="identity does not match"):
        backend.create(
            "sandbox-1", "root-thread", "user-1", workdir_path="projects/22222222-2222-4222-8222-222222222222"
        )


def test_kubernetes_delete_uses_uid_generation_precondition(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    kubernetes_module = ModuleType("kubernetes")
    client_module = ModuleType("kubernetes.client")
    rest_module = ModuleType("kubernetes.client.rest")

    class ApiException(Exception):
        def __init__(self, status=None):
            self.status = status

    rest_module.ApiException = ApiException
    client_module.rest = rest_module
    kubernetes_module.client = client_module
    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes_module)
    monkeypatch.setitem(sys.modules, "kubernetes.client", client_module)
    monkeypatch.setitem(sys.modules, "kubernetes.client.rest", rest_module)

    calls = []

    class FakeCoreApi:
        def delete_namespaced_pod(self, **kwargs):
            calls.append(("pod", kwargs))

        def delete_namespaced_service(self, **kwargs):
            calls.append(("service", kwargs))

    class FakeKubernetesClient:
        @staticmethod
        def V1Preconditions(**kwargs):
            return SimpleNamespace(**kwargs)

        @staticmethod
        def V1DeleteOptions(**kwargs):
            return SimpleNamespace(**kwargs)

    backend = object.__new__(module.KubernetesProvisionerBackend)
    backend._core_api = FakeCoreApi()
    backend._client = FakeKubernetesClient()
    backend._lock = threading.RLock()
    backend._namespace = "yuxi"
    backend._pod_name = lambda _sandbox_id: "pod-1"
    backend._service_name = lambda _sandbox_id: "service-1"

    backend.delete("sandbox-1", expected_generation="pod-uid-1")

    assert [kind for kind, _kwargs in calls] == ["pod", "service"]
    assert calls[0][1]["body"].preconditions.uid == "pod-uid-1"


@pytest.mark.parametrize(
    ("start_succeeds", "error_match"),
    [
        (True, "is not ready"),
        (False, "container start failed"),
    ],
)
def test_docker_backend_cleans_up_sandbox_and_network_on_failure(monkeypatch, tmp_path, start_succeeds, error_match):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    created_container = None
    deleted_networks = []

    class FakeContainer:
        name = "yuxi-sandbox-sandbox-1"
        status = "running"
        labels = {"thread-id": "thread-1"}
        attrs = {"State": {"Status": "running"}}
        removed = False

        def reload(self):
            return None

        def stop(self, timeout):
            assert timeout == 2
            self.status = "exited"

        def remove(self, *, v, force):
            assert v is True
            assert force is True
            self.removed = True

    def run_container(_image, **_kwargs):
        nonlocal created_container
        if not start_succeeds:
            raise RuntimeError("container start failed")
        created_container = FakeContainer()
        return created_container

    backend = _docker_backend(module, tmp_path, run_container)
    (tmp_path / "shared/user-1/workspace").mkdir(parents=True)
    (tmp_path.parent / "skill-projections/user-1").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(backend, "_get_container", lambda _sandbox_id: created_container)
    monkeypatch.setattr(backend, "_ensure_network", backend._network_name)
    monkeypatch.setattr(backend, "_delete_network", deleted_networks.append)
    monkeypatch.setattr(module, "wait_for_sandbox_ready", lambda _url, timeout_seconds: False)

    with pytest.raises(RuntimeError, match=error_match):
        backend.create("sandbox-1", "thread-1", "user-1")

    assert deleted_networks == ["sandbox-1"]
    if start_succeeds:
        assert created_container is not None
        assert created_container.removed is True


def test_docker_backend_assigns_each_sandbox_a_distinct_network(monkeypatch):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._lock = threading.RLock()
    backend._network_prefix = "yuxi-know-sandbox"

    first_network = backend._network_name("sandbox-1")
    second_network = backend._network_name("sandbox-2")

    assert first_network == "yuxi-know-sandbox-sandbox-1"
    assert second_network == "yuxi-know-sandbox-sandbox-2"
    assert first_network != second_network
    assert backend._is_on_expected_network(
        SimpleNamespace(attrs={"NetworkSettings": {"Networks": {first_network: {}}}}),
        "sandbox-1",
    )
    assert not backend._is_on_expected_network(
        SimpleNamespace(attrs={"NetworkSettings": {"Networks": {first_network: {}, second_network: {}}}}),
        "sandbox-1",
    )


def test_docker_backend_creates_network_from_dedicated_address_pool(monkeypatch):
    module = _load_module()

    class NotFound(Exception):
        pass

    class IPAMPool:
        def __init__(self, *, subnet):
            self.subnet = subnet

    class IPAMConfig:
        def __init__(self, *, pool_configs):
            self.pool_configs = pool_configs

    class FakeNetwork:
        attrs = {
            "Labels": {
                "managed-by": "yuxi-sandbox-provisioner",
                "sandbox-id": "sandbox-1",
            },
            "Containers": {},
            "IPAM": {"Config": [{"Subnet": "10.253.240.0/28"}]},
        }

        def reload(self):
            return None

        def connect(self, _container, aliases):
            assert aliases == ["sandbox-provisioner"]

    captured = []
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._lock = threading.RLock()
    backend._network_prefix = "yuxi-know-sandbox"
    backend._network_pool = (module.ipaddress.ip_network("10.253.240.0/20"), 28)
    backend._docker = SimpleNamespace(types=SimpleNamespace(IPAMPool=IPAMPool, IPAMConfig=IPAMConfig))
    backend._provisioner_container = SimpleNamespace(id="provisioner-id")

    def create_network(name, **kwargs):
        captured.append((name, kwargs))
        return FakeNetwork()

    backend._client = SimpleNamespace(
        networks=SimpleNamespace(
            get=lambda _name: (_ for _ in ()).throw(NotFound()),
            list=lambda: [],
            create=create_network,
        )
    )
    monkeypatch.setitem(sys.modules, "docker.errors", SimpleNamespace(NotFound=NotFound))

    network_name = backend._ensure_network("sandbox-1")

    assert network_name == "yuxi-know-sandbox-sandbox-1"
    assert captured[0][0] == network_name
    assert captured[0][1]["ipam"].pool_configs[0].subnet == "10.253.240.0/28"


def test_docker_backend_skips_used_subnet_and_reuses_it_after_cleanup():
    module = _load_module()
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._network_pool = (module.ipaddress.ip_network("10.253.240.0/27"), 28)

    occupied = SimpleNamespace(attrs={"IPAM": {"Config": [{"Subnet": "10.253.240.0/28"}]}})
    networks = [occupied]
    backend._client = SimpleNamespace(networks=SimpleNamespace(list=lambda: list(networks)))

    assert str(backend._next_network_subnet()) == "10.253.240.16/28"

    networks.clear()
    assert str(backend._next_network_subnet()) == "10.253.240.0/28"


def test_docker_backend_reports_dedicated_address_pool_exhaustion():
    module = _load_module()
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._network_pool = (module.ipaddress.ip_network("10.253.240.0/29"), 29)
    backend._client = SimpleNamespace(
        networks=SimpleNamespace(
            list=lambda: [SimpleNamespace(attrs={"IPAM": {"Config": [{"Subnet": "10.253.240.0/29"}]}})]
        )
    )

    with pytest.raises(module.SandboxCapacityError, match="has no available /29 subnet"):
        backend._next_network_subnet()


def test_docker_backend_retries_subnet_conflict_from_another_provisioner():
    module = _load_module()

    class IPAMPool:
        def __init__(self, *, subnet):
            self.subnet = subnet

    class IPAMConfig:
        def __init__(self, *, pool_configs):
            self.pool_configs = pool_configs

    created_subnets = []
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._network_pool = (module.ipaddress.ip_network("10.253.240.0/27"), 28)
    backend._docker = SimpleNamespace(types=SimpleNamespace(IPAMPool=IPAMPool, IPAMConfig=IPAMConfig))

    def create_network(_name, **kwargs):
        subnet = kwargs["ipam"].pool_configs[0].subnet
        created_subnets.append(subnet)
        if len(created_subnets) == 1:
            raise RuntimeError("Pool overlaps with other one on this address space")
        return SimpleNamespace(name="created")

    backend._client = SimpleNamespace(networks=SimpleNamespace(list=lambda: [], create=create_network))

    network = backend._create_network("sandbox-network", "sandbox-1")

    assert network.name == "created"
    assert created_subnets == ["10.253.240.0/28", "10.253.240.16/28"]


def test_docker_backend_deletes_distinct_sandboxes_with_bounded_parallelism():
    module = _load_module()
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._lock = threading.RLock()
    backend._sandbox_locks = {}
    backend._delete_slots = threading.BoundedSemaphore(2)
    backend._stop_timeout_seconds = 2

    state_lock = threading.Lock()
    two_stops_entered = threading.Event()
    release_stops = threading.Event()
    active_stops = 0
    peak_stops = 0

    class FakeContainer:
        status = "running"

        def __init__(self, sandbox_id):
            self.id = f"generation-{sandbox_id}"

        def reload(self):
            return None

        def stop(self, timeout):
            nonlocal active_stops, peak_stops
            assert timeout == 2
            with state_lock:
                active_stops += 1
                peak_stops = max(peak_stops, active_stops)
                if active_stops == 2:
                    two_stops_entered.set()
            assert release_stops.wait(timeout=2)
            with state_lock:
                active_stops -= 1

        def remove(self, *, v, force):
            assert v is True
            assert force is True

    containers = {
        sandbox_id: FakeContainer(sandbox_id) for sandbox_id in ("sandbox-1", "sandbox-2", "sandbox-3", "sandbox-4")
    }
    backend._get_container = containers.get
    deleted_networks = []
    backend._delete_network = deleted_networks.append

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(backend.delete, sandbox_id) for sandbox_id in containers]
        assert two_stops_entered.wait(timeout=1)
        release_stops.set()
        for future in futures:
            future.result(timeout=2)

    assert peak_stops == 2
    assert set(deleted_networks) == set(containers)


def test_docker_backend_serializes_same_sandbox_generation():
    module = _load_module()
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._lock = threading.RLock()
    backend._sandbox_locks = {}

    first = backend._sandbox_lock("sandbox-1")

    assert backend._sandbox_lock("sandbox-1") is first
    assert backend._sandbox_lock("sandbox-2") is not first


def test_docker_backend_serializes_create_and_delete_for_same_sandbox(monkeypatch):
    module = _load_module()
    backend = object.__new__(module.LocalContainerProvisionerBackend)
    backend._lock = threading.RLock()
    backend._sandbox_locks = {}
    backend._delete_slots = threading.BoundedSemaphore(1)
    backend._stop_timeout_seconds = 2
    backend._container_port = 8080
    backend._health_timeout_seconds = 1

    create_lookup_entered = threading.Event()
    release_create_lookup = threading.Event()
    delete_lookup_entered = threading.Event()
    caller = threading.local()

    class FakeContainer:
        id = "generation-1"
        name = "yuxi-sandbox-sandbox-1"
        status = "running"
        labels = {
            "thread-id": "thread-1",
            "workdir-path": "",
            "storage-mode": "persistent",
        }
        attrs = {"State": {"Status": "running"}}
        removed = False

        def reload(self):
            return None

        def stop(self, timeout):
            assert timeout == 2

        def remove(self, *, v, force):
            assert v is True
            assert force is True
            self.removed = True

    container = FakeContainer()

    def get_container(_sandbox_id):
        if caller.role == "create":
            create_lookup_entered.set()
            assert release_create_lookup.wait(timeout=2)
        else:
            delete_lookup_entered.set()
        return None if container.removed else container

    def create_sandbox():
        caller.role = "create"
        return backend.create("sandbox-1", "thread-1", "user-1")

    def delete_sandbox():
        caller.role = "delete"
        backend.delete("sandbox-1", expected_generation="generation-1")

    backend._get_container = get_container
    backend._ensure_network = lambda _sandbox_id: "yuxi-know-sandbox-sandbox-1"
    backend._delete_network = lambda _sandbox_id: None
    backend._is_expected_skills_mount = lambda _container, _uid: True
    backend._is_on_expected_network = lambda _container, _sandbox_id: True
    backend._has_expected_user_data_mounts = lambda _container, _uid: True
    monkeypatch.setattr(module, "wait_for_sandbox_ready", lambda _url, timeout_seconds: True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        create_future = executor.submit(create_sandbox)
        assert create_lookup_entered.wait(timeout=1)
        delete_future = executor.submit(delete_sandbox)
        try:
            assert not delete_lookup_entered.wait(timeout=0.1)
        finally:
            release_create_lookup.set()

        record = create_future.result(timeout=2)
        delete_future.result(timeout=2)

    assert record.generation == "generation-1"
    assert delete_lookup_entered.is_set()
    assert container.removed is True


def test_docker_backend_reconnects_provisioner_before_reusing_sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setitem(sys.modules, "docker.errors", SimpleNamespace(NotFound=RuntimeError))
    module = _load_module()
    connected = []

    class FakeNetwork:
        name = "yuxi-know-sandbox-sandbox-1"
        attrs = {
            "Labels": {"managed-by": "yuxi-sandbox-provisioner", "sandbox-id": "sandbox-1"},
            "Containers": {},
        }

        def reload(self):
            return None

        def connect(self, container, aliases):
            connected.append((container.id, aliases))

    class FakeContainer:
        name = "yuxi-sandbox-sandbox-1"
        status = "running"
        labels = {"thread-id": "thread-1"}
        attrs = {"State": {"Status": "running"}}

        def reload(self):
            return None

    backend = _docker_backend(module, tmp_path, lambda *_args, **_kwargs: pytest.fail("sandbox was recreated"))
    backend._client.networks = SimpleNamespace(get=lambda _name: FakeNetwork())
    backend._provisioner_container = SimpleNamespace(id="provisioner-id")
    monkeypatch.setattr(backend, "_get_container", lambda _sandbox_id: FakeContainer())
    monkeypatch.setattr(backend, "_is_expected_skills_mount", lambda _container, _uid: True)
    monkeypatch.setattr(backend, "_is_on_expected_network", lambda _container, _sandbox_id: True)
    monkeypatch.setattr(backend, "_has_expected_user_data_mounts", lambda _container, _uid, _workdir=None: True)
    monkeypatch.setattr(module, "wait_for_sandbox_ready", lambda _url, timeout_seconds: bool(connected))

    record = backend.create("sandbox-1", "thread-1", "user-1")

    assert record.sandbox_url == "http://yuxi-sandbox-sandbox-1:8080"
    assert connected == [("provisioner-id", ["sandbox-provisioner"])]


def test_docker_backend_does_not_remove_unowned_network(monkeypatch, tmp_path):
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setitem(sys.modules, "docker.errors", SimpleNamespace(NotFound=RuntimeError))
    module = _load_module()
    disconnected = []
    removed = []

    class FakeNetwork:
        name = "yuxi-know-sandbox-sandbox-1"
        attrs = {
            "Labels": {"managed-by": "operator", "sandbox-id": "sandbox-1"},
            "Containers": {"provisioner-id": {}},
        }

        def reload(self):
            return None

        def disconnect(self, container, force):
            disconnected.append((container.id, force))

        def remove(self):
            removed.append(True)

    backend = _docker_backend(module, tmp_path, lambda *_args, **_kwargs: None)
    backend._client.networks = SimpleNamespace(get=lambda _name: FakeNetwork())
    backend._provisioner_container = SimpleNamespace(id="provisioner-id")

    backend._delete_network("sandbox-1")

    assert disconnected == []
    assert removed == []


def test_kubernetes_inventory_includes_pod_without_service_and_fails_closed(
    monkeypatch,
):
    """迁移清理必须以 Pod 为权威事实，不能把 Service 丢失或枚举失败当成空集。"""
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    kubernetes_module = ModuleType("kubernetes")
    client_module = ModuleType("kubernetes.client")
    rest_module = ModuleType("kubernetes.client.rest")

    class ApiException(Exception):
        pass

    rest_module.ApiException = ApiException
    client_module.rest = rest_module
    kubernetes_module.client = client_module
    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes_module)
    monkeypatch.setitem(sys.modules, "kubernetes.client", client_module)
    monkeypatch.setitem(sys.modules, "kubernetes.client.rest", rest_module)
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            labels={
                "app": "yuxi-sandbox",
                "sandbox-id": "orphan-1",
            },
            annotations={"workdir-path": "projects/11111111-1111-4111-8111-111111111111"},
            uid="pod-generation-1",
        ),
        status=SimpleNamespace(phase="Terminating"),
    )

    class FakeCoreApi:
        fail = False
        selectors = []

        def list_namespaced_pod(self, **kwargs):
            self.selectors.append(kwargs["label_selector"])
            if self.fail:
                raise ApiException("inventory unavailable")
            return SimpleNamespace(items=[pod])

    backend = object.__new__(module.KubernetesProvisionerBackend)
    backend._core_api = FakeCoreApi()
    backend._namespace = "yuxi"

    assert backend.list() == [
        module.SandboxRecord(
            sandbox_id="orphan-1",
            sandbox_url="",
            status="Terminating",
            generation="pod-generation-1",
            workdir_path="projects/11111111-1111-4111-8111-111111111111",
        )
    ]
    assert backend._core_api.selectors == ["app=yuxi-sandbox"]
    backend._core_api.fail = True
    with pytest.raises(ApiException, match="inventory unavailable"):
        backend.list()


def test_quiesce_waits_for_authoritative_inventory_and_blocks_new_creates(
    monkeypatch,
):
    """删除请求返回后仍须看到权威 inventory 归零，且窗口内不得创建新 runtime。"""
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    module = _load_module()
    record = module.SandboxRecord(
        sandbox_id="sandbox-1",
        sandbox_url="",
        status="Terminating",
        generation="generation-1",
    )

    class FakeBackend:
        inventories = iter(([record], [record], []))

        def __init__(self):
            self.deletes = []

        def list(self):
            return next(self.inventories)

        def delete(self, sandbox_id, *, expected_generation=None):
            self.deletes.append((sandbox_id, expected_generation))

    backend = FakeBackend()
    gate = module.SandboxQuiescenceGate()
    monkeypatch.setattr(module, "backend_impl", backend)
    monkeypatch.setattr(module, "sandbox_quiescence_gate", gate)
    monkeypatch.setattr(module, "sandbox_operation_pins", module.SandboxOperationPins())
    monkeypatch.setattr(module.idle_reaper, "forget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    response = module.quiesce_sandboxes(timeout_seconds=1)

    assert response == module.QuiesceSandboxesResponse(ok=True, deleted=1)
    assert backend.deletes == [
        ("sandbox-1", "generation-1"),
        ("sandbox-1", "generation-1"),
    ]
    with pytest.raises(module.HTTPException) as exc_info:
        module.create_sandbox(
            module.CreateSandboxRequest(
                sandbox_id="new-sandbox",
                thread_id="thread-1",
                uid="user-1",
            )
        )
    assert exc_info.value.status_code == 503


def test_quiesce_deletes_large_inventory_with_configured_parallelism(monkeypatch):
    """全局静默必须并行清理 100 个 runtime，不能串行耗尽迁移 deadline。"""
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setenv("SANDBOX_DELETE_CONCURRENCY", "32")
    module = _load_module()
    records = {
        f"sandbox-{index}": module.SandboxRecord(
            sandbox_id=f"sandbox-{index}",
            sandbox_url="",
            status="running",
            generation=f"generation-{index}",
        )
        for index in range(100)
    }
    state_lock = threading.Lock()
    release_deletes = threading.Event()
    capacity_reached = threading.Event()
    active_deletes = 0
    peak_deletes = 0

    class FakeBackend:
        def list(self):
            with state_lock:
                return list(records.values())

        def delete(self, sandbox_id, *, expected_generation=None):
            nonlocal active_deletes, peak_deletes
            with state_lock:
                assert records[sandbox_id].generation == expected_generation
                active_deletes += 1
                peak_deletes = max(peak_deletes, active_deletes)
                if active_deletes == 32:
                    capacity_reached.set()
            assert release_deletes.wait(timeout=2)
            with state_lock:
                records.pop(sandbox_id)
                active_deletes -= 1

    monkeypatch.setattr(module, "backend_impl", FakeBackend())
    monkeypatch.setattr(module, "sandbox_quiescence_gate", module.SandboxQuiescenceGate())
    monkeypatch.setattr(module, "sandbox_operation_pins", module.SandboxOperationPins())
    monkeypatch.setattr(module.idle_reaper, "forget", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    result = {}

    def quiesce():
        result["response"] = module.quiesce_sandboxes(timeout_seconds=2)

    thread = threading.Thread(target=quiesce)
    thread.start()
    try:
        assert capacity_reached.wait(timeout=1)
        assert peak_deletes == 32
    finally:
        release_deletes.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result["response"] == module.QuiesceSandboxesResponse(ok=True, deleted=100)


def test_quiesce_large_inventory_honors_delete_deadline(monkeypatch):
    """100 个阻塞删除必须按 deadline 返回，不能串行等待全部 stop。"""
    monkeypatch.setenv("PROVISIONER_BACKEND", "memory")
    monkeypatch.setenv("SANDBOX_DELETE_CONCURRENCY", "32")
    module = _load_module()
    records = [
        module.SandboxRecord(
            sandbox_id=f"sandbox-{index}",
            sandbox_url="",
            status="running",
            generation=f"generation-{index}",
        )
        for index in range(100)
    ]
    state_lock = threading.Lock()
    release_deletes = threading.Event()
    delete_started = threading.Event()
    deletes_drained = threading.Event()
    active_deletes = 0

    class FakeBackend:
        def delete(self, _sandbox_id, *, expected_generation=None):
            nonlocal active_deletes
            assert expected_generation is not None
            with state_lock:
                active_deletes += 1
                delete_started.set()
            try:
                assert release_deletes.wait(timeout=2)
            finally:
                with state_lock:
                    active_deletes -= 1
                    if active_deletes == 0:
                        deletes_drained.set()

    monkeypatch.setattr(module, "backend_impl", FakeBackend())
    monkeypatch.setattr(module, "sandbox_operation_pins", module.SandboxOperationPins())
    monkeypatch.setattr(module.idle_reaper, "forget", lambda *_args, **_kwargs: None)

    started_at = module.time.monotonic()
    try:
        with pytest.raises(module.SandboxQuiesceTimeoutError):
            module._delete_sandbox_records_for_quiescence(
                records,
                timeout_seconds=0.05,
            )
    finally:
        release_deletes.set()

    assert delete_started.is_set()
    assert module.time.monotonic() - started_at < 0.5
    assert deletes_drained.wait(timeout=1)


def test_quiescence_gate_waits_for_create_already_in_flight():
    """停机栅栏必须排空先进入的 create，避免判空后又出现新 generation。"""
    module = _load_module()
    gate = module.SandboxQuiescenceGate()
    gate.acquire_create()
    entered = threading.Event()
    finished = threading.Event()

    def begin_quiescence():
        entered.set()
        gate.begin()
        finished.set()

    thread = threading.Thread(target=begin_quiescence)
    thread.start()
    assert entered.wait(timeout=1)
    assert not finished.wait(timeout=0.05)
    gate.release_create()
    assert finished.wait(timeout=1)
    thread.join(timeout=1)

    with pytest.raises(RuntimeError, match="quiescing"):
        gate.acquire_create()
