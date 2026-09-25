from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import random
import re
import secrets
import threading
import time
import weakref
from collections.abc import AsyncIterator
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated
from urllib import request

import httpx
from dotenv import dotenv_values
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SANDBOX_ENV_FILE = Path(__file__).parent / "sandbox.env"
DEFAULT_SANDBOX_IMAGE = (
    "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0"
)
SANDBOX_RUNTIME_ENVIRONMENTS = {
    "core": {
        "DISABLE_BROWSER": "true",
        "DISABLE_MCP_BROWSER": "true",
        "DISABLE_VNC": "true",
        "DISABLE_JUPYTER": "true",
        "DISABLE_CODE_SERVER": "true",
        "DISABLE_NODEJS_REPL": "true",
    },
    "browser": {
        "DISABLE_BROWSER": "false",
        "DISABLE_MCP_BROWSER": "false",
        "DISABLE_VNC": "false",
        "DISABLE_JUPYTER": "true",
        "DISABLE_CODE_SERVER": "true",
        "DISABLE_NODEJS_REPL": "true",
    },
    "full": {
        "DISABLE_BROWSER": "false",
        "DISABLE_MCP_BROWSER": "false",
        "DISABLE_VNC": "false",
        "DISABLE_JUPYTER": "false",
        "DISABLE_CODE_SERVER": "false",
        "DISABLE_NODEJS_REPL": "false",
    },
}
DEFAULT_DOCKER_SUBNET_PREFIX = 28
SAFE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
PROXY_RESPONSE_HEADERS = frozenset(
    {"cache-control", "content-disposition", "content-type", "etag", "last-modified"}
)
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
PERSISTENT_SANDBOX_MOUNT_ROOTS = (
    "/home/gem/skills",
    "/home/gem/user-data",
    "/home/gem/projects",
)
SANDBOX_READY_INITIAL_DELAY_SECONDS = 0.05
SANDBOX_READY_MAX_DELAY_SECONDS = 1.0
SANDBOX_READY_BACKOFF_MULTIPLIER = 2.0
SANDBOX_READY_JITTER_RATIO = 0.2
SANDBOX_READY_REQUEST_TIMEOUT_SECONDS = 3.0


def _is_persistent_sandbox_mount_path(path: str) -> bool:
    """判断 Sandbox 路径是否落入任一持久文件挂载根。"""
    return any(
        path == root or path.startswith(f"{root}/")
        for root in PERSISTENT_SANDBOX_MOUNT_ROOTS
    )


def canonical_backend_name(backend: str) -> str:
    value = (backend or "").strip().lower()
    return value or "memory"


def normalize_env(env: dict | None) -> dict[str, str]:
    if not isinstance(env, dict):
        return {}
    return {
        str(key): "" if value is None else str(value)
        for key, value in env.items()
        if str(key)
    }


def normalize_workdir_path(workdir_path: str) -> str:
    """校验 Sandbox wire 中 UserWorkspace 相对 Workdir。"""
    raw = str(workdir_path or "").strip()
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute() or "\\" in raw or "://" in raw:
        raise ValueError("workdir_path must be a relative POSIX path")
    if any(part in {"", ".", ".."} for part in raw.split("/")):
        raise ValueError("workdir_path contains invalid path components")
    return pure.as_posix()


def kubernetes_storage_init_script(uid: str, workdir_path: str | None) -> str:
    """生成 K8s PVC 子树的一次性身份迁移与 no-follow 校验脚本。"""
    workdir_parts = tuple(PurePosixPath(workdir_path).parts) if workdir_path else ()
    return f"""
import os
import stat

FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
UID = 1000
GID = 1000
MARKER_DIR = '.v072-runtime-identity'

def open_path(root, parts, create=False):
    fd = os.open(root, FLAGS)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise

def normalize(fd):
    os.fchown(fd, UID, GID)
    os.fchmod(fd, 0o700)
    for name in os.listdir(fd):
        item = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(item.st_mode):
            os.chown(name, UID, GID, dir_fd=fd, follow_symlinks=False)
        elif stat.S_ISDIR(item.st_mode):
            child = os.open(name, FLAGS, dir_fd=fd)
            try:
                normalize(child)
            finally:
                os.close(child)
        else:
            mode = 0o700 if item.st_mode & 0o111 else 0o600
            os.chown(name, UID, GID, dir_fd=fd, follow_symlinks=False)
            os.chmod(name, mode, dir_fd=fd, follow_symlinks=False)

def migrate(root, parts, marker_name):
    target_fd = open_path(root, parts, create=True)
    marker_fd = open_path(root, (MARKER_DIR,), create=True)
    try:
        try:
            completed = os.open(marker_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=marker_fd)
        except FileNotFoundError:
            normalize(target_fd)
            try:
                completed = os.open(
                    marker_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=marker_fd,
                )
            except FileExistsError:
                completed = os.open(
                    marker_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=marker_fd
                )
        os.close(completed)
    finally:
        os.close(marker_fd)
        os.close(target_fd)

migrate('/mnt/user-data', {("shared", uid, "workspace")!r}, {"workspace-" + uid!r})
migrate('/mnt/skills-data', {("skill-projections", uid)!r}, {"skills-" + uid!r})
workdir_parts = {workdir_parts!r}
if workdir_parts:
    workdir_fd = open_path('/mnt/user-data', {("shared", uid, "workspace")!r} + workdir_parts)
    os.close(workdir_fd)
""".strip()


def load_sandbox_env() -> dict[str, str]:
    return normalize_env(dotenv_values(SANDBOX_ENV_FILE))


def merged_sandbox_env(
    global_env: dict[str, str], user_env: dict[str, str]
) -> dict[str, str]:
    return {**global_env, **normalize_env(user_env)}


def sandbox_runtime_profile(value: str | None = None) -> str:
    """解析部署级 Sandbox 运行规格，并拒绝未知取值。"""
    raw_value = os.getenv("SANDBOX_RUNTIME_PROFILE", "core") if value is None else value
    profile = raw_value.strip().lower()
    if profile not in SANDBOX_RUNTIME_ENVIRONMENTS:
        allowed = ", ".join(SANDBOX_RUNTIME_ENVIRONMENTS)
        raise RuntimeError(
            f"invalid SANDBOX_RUNTIME_PROFILE={profile!r}; expected one of: {allowed}"
        )
    return profile


def sandbox_runtime_environment(profile: str) -> dict[str, str]:
    """返回由运行规格拥有且请求不能覆盖的容器环境变量。"""
    return dict(SANDBOX_RUNTIME_ENVIRONMENTS[sandbox_runtime_profile(profile)])


def docker_network_pool() -> tuple[ipaddress.IPv4Network, int] | None:
    """读取 Docker Sandbox 专用 IPv4 地址池。"""
    raw_address_pool = os.getenv("DOCKER_ADDRESS_POOL", "").strip()
    raw_subnet_prefix = os.getenv("DOCKER_SUBNET_PREFIX", "").strip()
    if not raw_address_pool:
        if raw_subnet_prefix:
            raise RuntimeError("DOCKER_SUBNET_PREFIX requires DOCKER_ADDRESS_POOL")
        return None

    try:
        address_pool = ipaddress.ip_network(raw_address_pool, strict=True)
    except ValueError as exc:
        raise RuntimeError(f"invalid DOCKER_ADDRESS_POOL={raw_address_pool!r}") from exc
    if not isinstance(address_pool, ipaddress.IPv4Network):
        raise RuntimeError("DOCKER_ADDRESS_POOL must be an IPv4 network")

    prefix_value = raw_subnet_prefix or str(DEFAULT_DOCKER_SUBNET_PREFIX)
    try:
        subnet_prefix = int(prefix_value)
    except ValueError as exc:
        raise RuntimeError(
            f"DOCKER_SUBNET_PREFIX must be an integer, got {prefix_value!r}"
        ) from exc
    if subnet_prefix < address_pool.prefixlen or subnet_prefix > 29:
        raise RuntimeError(
            "DOCKER_SUBNET_PREFIX must be between the address pool prefix and 29"
        )
    return address_pool, subnet_prefix


def sandbox_delete_concurrency(value: str | None = None) -> int:
    """读取 Docker Sandbox 有界并行销毁数。"""
    raw_value = (
        os.getenv("SANDBOX_DELETE_CONCURRENCY", "16") if value is None else value
    ).strip()
    try:
        concurrency = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            f"SANDBOX_DELETE_CONCURRENCY must be an integer, got {raw_value!r}"
        ) from exc
    if concurrency < 1:
        raise RuntimeError("SANDBOX_DELETE_CONCURRENCY must be >= 1")
    return concurrency


def sandbox_container_stop_timeout_seconds(value: str | None = None) -> int:
    """读取 Docker Sandbox 收到 SIGTERM 后的最大等待秒数。"""
    raw_value = (
        os.getenv("SANDBOX_CONTAINER_STOP_TIMEOUT_SECONDS", "2")
        if value is None
        else value
    ).strip()
    try:
        timeout_seconds = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            "SANDBOX_CONTAINER_STOP_TIMEOUT_SECONDS must be an integer, "
            f"got {raw_value!r}"
        ) from exc
    if timeout_seconds < 1:
        raise RuntimeError("SANDBOX_CONTAINER_STOP_TIMEOUT_SECONDS must be >= 1")
    return timeout_seconds


def sandbox_container_limits(
    mem_limit: str | None = None,
    cpus: str | None = None,
    pids_limit: str | None = None,
) -> dict:
    """解析单沙盒容器的 mem/cpu/pids 上限，非法配置显式失败而不回退默认。"""
    raw_mem = (
        os.getenv("SANDBOX_MEM_LIMIT", "2g") if mem_limit is None else mem_limit
    ).strip()
    # 本配置只接受纯字节数或 k/m/g 单位后缀（窄于 Docker 完整格式），
    # 提前拒绝，避免运行期才暴露。
    if not re.fullmatch(r"[1-9][0-9]*([kKmMgG][bB]?)?", raw_mem):
        raise RuntimeError(
            f"SANDBOX_MEM_LIMIT must be bytes or a k/m/g value, got {raw_mem!r}"
        )

    raw_cpus = (
        os.getenv("SANDBOX_CPUS", "2") if cpus is None else cpus
    ).strip()
    # nan/inf/溢出在 int() 转换处失败；小于 1e-9 核会截断为 0 并被
    # docker-py 的 `if nano_cpus` 静默丢弃，两者都必须在启动期拒绝。
    try:
        nano_cpus = int(float(raw_cpus) * 1_000_000_000)
    except (ValueError, OverflowError) as exc:
        raise RuntimeError(
            f"SANDBOX_CPUS must be a finite number, got {raw_cpus!r}"
        ) from exc
    if nano_cpus < 1:
        raise RuntimeError(
            f"SANDBOX_CPUS must be > 0 with at least 1 nano-cpu, got {raw_cpus!r}"
        )

    raw_pids = (
        os.getenv("SANDBOX_PIDS_LIMIT", "512")
        if pids_limit is None
        else pids_limit
    ).strip()
    try:
        pids = int(raw_pids)
    except ValueError as exc:
        raise RuntimeError(
            f"SANDBOX_PIDS_LIMIT must be an integer, got {raw_pids!r}"
        ) from exc
    if pids < 1:
        raise RuntimeError(f"SANDBOX_PIDS_LIMIT must be >= 1, got {raw_pids!r}")

    return {
        "mem_limit": raw_mem,
        "nano_cpus": nano_cpus,
        "pids_limit": pids,
    }


def provisioner_token() -> str:
    token = os.getenv("SANDBOX_PROVISIONER_TOKEN", "").strip()
    if len(token) < 32:
        raise RuntimeError(
            "SANDBOX_PROVISIONER_TOKEN must contain at least 32 characters"
        )
    return token


def require_provisioner_auth(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = f"Bearer {provisioner_token()}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid provisioner credentials")


def sandbox_proxy_url(sandbox_id: str) -> str:
    public_url = (
        os.getenv("PROVISIONER_PUBLIC_URL", "http://sandbox-provisioner:8002")
        .strip()
        .rstrip("/")
    )
    if not public_url:
        raise RuntimeError("PROVISIONER_PUBLIC_URL is required")
    return f"{public_url}/api/sandboxes/{sandbox_id}/proxy"


class CreateSandboxRequest(BaseModel):
    sandbox_id: str
    thread_id: str
    workdir_path: str | None = None
    uid: str
    env: dict[str, str] = Field(default_factory=dict)
    inherit_env: bool = True


class SandboxResponse(BaseModel):
    sandbox_id: str
    sandbox_url: str
    status: str | None = None
    generation: str | None = None
    workdir_path: str | None = None


class DeleteSandboxResponse(BaseModel):
    ok: bool
    sandbox_id: str


class TouchSandboxResponse(BaseModel):
    ok: bool
    sandbox_id: str
    status: str | None = None


class ListSandboxesResponse(BaseModel):
    sandboxes: list[SandboxResponse]
    count: int


class QuiesceSandboxesResponse(BaseModel):
    ok: bool
    deleted: int


@dataclass(slots=True)
class SandboxRecord:
    sandbox_id: str
    sandbox_url: str
    status: str | None = None
    generation: str | None = None
    workdir_path: str | None = None


class SandboxGenerationMismatchError(RuntimeError):
    """删除请求引用的 Sandbox generation 已经过期。"""


class SandboxCapacityError(RuntimeError):
    """Sandbox 基础设施容量已耗尽。"""


class SandboxQuiesceTimeoutError(RuntimeError):
    """Sandbox 全局静默未在调用方 deadline 内完成。"""


class SandboxOperationPins:
    """让删除等待已经开始的 proxy 请求排空，并阻止新的请求穿过删除。"""

    def __init__(self):
        self._condition = threading.Condition()
        self._active: dict[str, int] = {}
        self._deleting: set[str] = set()

    def acquire(self, sandbox_id: str) -> None:
        with self._condition:
            while sandbox_id in self._deleting:
                self._condition.wait()
            self._active[sandbox_id] = self._active.get(sandbox_id, 0) + 1

    def release(self, sandbox_id: str) -> None:
        with self._condition:
            count = self._active.get(sandbox_id, 0)
            if count <= 1:
                self._active.pop(sandbox_id, None)
                self._condition.notify_all()
            else:
                self._active[sandbox_id] = count - 1

    def begin_delete(self, sandbox_id: str) -> None:
        with self._condition:
            self._deleting.add(sandbox_id)
            while self._active.get(sandbox_id, 0):
                self._condition.wait()

    def end_delete(self, sandbox_id: str) -> None:
        with self._condition:
            self._deleting.discard(sandbox_id)
            self._condition.notify_all()


class SandboxQuiescenceGate:
    """迁移停机后拒绝创建新的 Sandbox generation。"""

    def __init__(self):
        self._condition = threading.Condition()
        self._started = False
        self._active_creates = 0

    def begin(self) -> None:
        with self._condition:
            self._started = True
            while self._active_creates:
                self._condition.wait()

    def acquire_create(self) -> None:
        with self._condition:
            if self._started:
                raise RuntimeError(
                    "sandbox provisioner is quiescing for storage migration"
                )
            self._active_creates += 1

    def release_create(self) -> None:
        with self._condition:
            self._active_creates -= 1
            if not self._active_creates:
                self._condition.notify_all()


class MemoryProvisionerBackend:
    def __init__(self):
        self._lock = threading.Lock()
        self._records: dict[str, SandboxRecord] = {}
        self._url_template = os.getenv(
            "MEMORY_SANDBOX_URL_TEMPLATE", "http://agent-sandbox:8000"
        )

    def _url_for(self, sandbox_id: str) -> str:
        template = self._url_template
        if "{sandbox_id}" in template:
            return template.format(sandbox_id=sandbox_id)
        return template

    def create(
        self,
        sandbox_id: str,
        thread_id: str,
        uid: str,
        env: dict[str, str] | None = None,
        *,
        workdir_path: str | None = None,
        inherit_env: bool = True,
    ) -> SandboxRecord:
        _ = thread_id
        _ = uid
        _ = env
        _ = inherit_env
        normalized_workdir_path = (
            normalize_workdir_path(workdir_path) if workdir_path else None
        )
        with self._lock:
            existing = self._records.get(sandbox_id)
            if existing is not None:
                if existing.workdir_path != normalized_workdir_path:
                    raise ValueError(
                        "sandbox workdir identity does not match existing generation"
                    )
                return existing
            record = SandboxRecord(
                sandbox_id=sandbox_id,
                sandbox_url=self._url_for(sandbox_id),
                status="Running",
                generation=secrets.token_hex(16),
                workdir_path=normalized_workdir_path,
            )
            self._records[sandbox_id] = record
            return record

    def discover(self, sandbox_id: str) -> SandboxRecord | None:
        with self._lock:
            return self._records.get(sandbox_id)

    def list(self) -> list[SandboxRecord]:
        with self._lock:
            return list(self._records.values())

    def delete(
        self, sandbox_id: str, *, expected_generation: str | None = None
    ) -> None:
        with self._lock:
            record = self._records.get(sandbox_id)
            if (
                record is not None
                and expected_generation
                and record.generation != expected_generation
            ):
                raise SandboxGenerationMismatchError(
                    "sandbox generation does not match delete request"
                )
            self._records.pop(sandbox_id, None)


def wait_for_sandbox_ready(sandbox_url: str, timeout_seconds: float = 30) -> bool:
    deadline = time.monotonic() + timeout_seconds
    opener = request.build_opener(request.ProxyHandler({}))
    delay_seconds = SANDBOX_READY_INITIAL_DELAY_SECONDS
    while True:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return False
        try:
            with opener.open(
                f"{sandbox_url.rstrip('/')}/v1/sandbox",
                timeout=min(SANDBOX_READY_REQUEST_TIMEOUT_SECONDS, remaining_seconds),
            ) as response:
                status_code = getattr(response, "status", 200)
            if status_code == 200:
                return True
        except Exception:
            pass
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return False
        jitter_factor = random.uniform(
            1 - SANDBOX_READY_JITTER_RATIO,
            1 + SANDBOX_READY_JITTER_RATIO,
        )
        time.sleep(min(remaining_seconds, delay_seconds * jitter_factor))
        delay_seconds = min(
            SANDBOX_READY_MAX_DELAY_SECONDS,
            delay_seconds * SANDBOX_READY_BACKOFF_MULTIPLIER,
        )


class LocalContainerProvisionerBackend:
    def __init__(self):
        from docker.errors import DockerException

        import docker

        self._docker = docker
        self._lock = threading.RLock()
        self._sandbox_locks = weakref.WeakValueDictionary()
        self._delete_slots = threading.BoundedSemaphore(sandbox_delete_concurrency())
        self._stop_timeout_seconds = sandbox_container_stop_timeout_seconds()
        self._container_limits = sandbox_container_limits()
        self._container_port = int(os.getenv("SANDBOX_CONTAINER_PORT", "8080"))
        self._sandbox_image = os.getenv(
            "SANDBOX_IMAGE",
            DEFAULT_SANDBOX_IMAGE,
        )
        self._network_prefix = os.getenv("DOCKER_NETWORK_PREFIX")
        if not self._network_prefix:
            raise RuntimeError(
                "DOCKER_NETWORK_PREFIX is required for the docker backend"
            )
        self._network_pool = docker_network_pool()
        self._user_data_host_path = os.getenv("DOCKER_USER_DATA_HOST_PATH")
        self._skill_projections_host_path = os.getenv(
            "DOCKER_SKILL_PROJECTIONS_HOST_PATH"
        )
        self._user_data_container_path = Path("/app/user-data")
        self._skill_projections_container_path = Path("/app/skill-projections")
        self._container_prefix = os.getenv("DOCKER_SANDBOX_PREFIX", "yuxi-sandbox")
        self._health_timeout_seconds = int(
            os.getenv("SANDBOX_HEALTH_TIMEOUT_SECONDS", "300")
        )
        self._sandbox_env = load_sandbox_env()

        try:
            self._client = docker.from_env()
            self._client.ping()
            self._provisioner_container = self._client.containers.get(
                os.environ["HOSTNAME"]
            )
        except DockerException as exc:
            raise RuntimeError(f"docker backend unavailable: {exc}") from exc

        self._resolve_host_paths()
        self._user_data_host_path = self._normalize_host_bind_path(
            self._user_data_host_path
        )
        self._skill_projections_host_path = self._normalize_host_bind_path(
            self._skill_projections_host_path
        )

    @staticmethod
    def _normalize_host_bind_path(path_value: str | None) -> str:
        value = str(path_value or "").strip()
        if not value:
            raise RuntimeError("docker host bind path is required")

        # Docker Desktop on Windows can report bind sources as D:\\... while
        # this provisioner runs in a Linux container. Convert that daemon-
        # reported path into the Linux path exposed inside Docker Desktop.
        normalized = value.replace("\\", "/")
        match = re.match(r"^([A-Za-z]):/(.+)$", normalized)
        if match:
            drive = match.group(1).lower()
            rest = match.group(2).lstrip("/")
            return f"/run/desktop/mnt/host/{drive}/{rest}"

        return normalized

    @staticmethod
    def _validate_path_segment(value: str, label: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError(f"{label} is required")
        if not SAFE_PATH_SEGMENT_RE.fullmatch(candidate):
            raise ValueError(f"{label} must contain only letters, numbers, '-' or '_'")
        return candidate

    @staticmethod
    def _validate_thread_id(thread_id: str) -> str:
        return LocalContainerProvisionerBackend._validate_path_segment(
            thread_id, "thread_id"
        )

    @staticmethod
    def _validate_uid(uid: str) -> str:
        return LocalContainerProvisionerBackend._validate_path_segment(uid, "uid")

    @staticmethod
    def _sanitize_id(value: str) -> str:
        sanitized = "".join(
            ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip().lower()
        )
        return sanitized[:48] or "sandbox"

    def _container_name(self, sandbox_id: str) -> str:
        return f"{self._container_prefix}-{self._sanitize_id(sandbox_id)}"

    def _network_name(self, sandbox_id: str) -> str:
        prefix = self._network_prefix.rstrip("-_")
        return f"{prefix}-{self._sanitize_id(sandbox_id)}"

    def _sandbox_lock(self, sandbox_id: str) -> threading.RLock:
        """返回只串行化同一 Sandbox generation 的进程内锁。"""
        with self._lock:
            lock = self._sandbox_locks.get(sandbox_id)
            if lock is None:
                lock = threading.RLock()
                self._sandbox_locks[sandbox_id] = lock
            return lock

    def _user_skills_host_path(self, uid: str) -> Path:
        return Path(self._skill_projections_host_path) / uid

    def _shared_workspace_host_path(self, uid: str) -> Path:
        return Path(self._user_data_host_path) / "shared" / uid / "workspace"

    @staticmethod
    def _validate_directory_without_symlinks(
        root: Path, parts: tuple[str, ...], *, label: str
    ) -> None:
        """从已挂载根逐层打开既有目录，并拒绝任意 symlink 组件。"""
        directory_fd = None
        try:
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for part in parts:
                child_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                os.close(directory_fd)
                directory_fd = child_fd
        except OSError as exc:
            raise ValueError(
                f"{label} must reference an existing directory without symlinks"
            ) from exc
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

    def _is_expected_skills_mount(self, container, uid: str) -> bool:
        expected_source = str(self._user_skills_host_path(uid))
        for mount in container.attrs.get("Mounts") or []:
            destination = (mount.get("Destination") or "").rstrip("/")
            if destination != "/home/gem/skills":
                continue
            source = str(mount.get("Source") or "").rstrip("/")
            return source == expected_source and mount.get("RW") is False
        return False

    def _has_expected_user_data_mounts(
        self,
        container,
        uid: str,
    ) -> bool:
        expected_mounts = {
            "/home/gem/user-data": str(self._shared_workspace_host_path(uid)),
        }
        actual_mounts = {
            str((mount.get("Destination") or "").rstrip("/")): str(
                (mount.get("Source") or "").rstrip("/")
            )
            for mount in container.attrs.get("Mounts") or []
        }
        expected = all(
            actual_mounts.get(destination) == source
            for destination, source in expected_mounts.items()
        )
        allowed_persistent_mounts = {"/home/gem/user-data", "/home/gem/skills"}
        return expected and all(
            not _is_persistent_sandbox_mount_path(destination)
            or destination in allowed_persistent_mounts
            for destination in actual_mounts
        )

    @staticmethod
    def _has_no_persistent_file_mounts(container) -> bool:
        """一次性 Sandbox 不得获得 User Data、Project 或 Skill 持久卷。"""
        for mount in container.attrs.get("Mounts") or []:
            destination = str((mount.get("Destination") or "").rstrip("/"))
            if _is_persistent_sandbox_mount_path(destination):
                return False
        return True

    def _resolve_host_paths(self) -> None:
        if self._user_data_host_path and self._skill_projections_host_path:
            return

        container_id = os.getenv("HOSTNAME", "").strip()
        if not container_id:
            raise RuntimeError(
                "HOSTNAME is required to infer docker backend host paths"
            )

        inspected = self._client.api.inspect_container(container_id)
        mounts = inspected.get("Mounts") or []

        sources: dict[str, str] = {}
        for mount in mounts:
            destination = (mount.get("Destination") or "").rstrip("/")
            source = str(mount.get("Source") or "").strip()
            if (
                destination
                in {
                    "/app/user-data",
                    "/app/skill-projections",
                }
                and source
            ):
                sources[destination] = source

        if not self._user_data_host_path:
            self._user_data_host_path = sources.get("/app/user-data")
        if not self._skill_projections_host_path:
            self._skill_projections_host_path = sources.get("/app/skill-projections")
        if not all(
            (
                self._user_data_host_path,
                self._skill_projections_host_path,
            )
        ):
            raise RuntimeError("cannot infer explicit UserWorkspace/Skill host paths")

    def _is_on_expected_network(self, container, sandbox_id: str) -> bool:
        networks = (container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
        return set(networks) == {self._network_name(sandbox_id)}

    @staticmethod
    def _has_expected_network_ownership(network, sandbox_id: str) -> bool:
        labels = network.attrs.get("Labels") or {}
        return (
            labels.get("managed-by") == "yuxi-sandbox-provisioner"
            and labels.get("sandbox-id") == sandbox_id
        )

    @staticmethod
    def _network_ipv4_subnets(network) -> list[ipaddress.IPv4Network]:
        """读取 Docker 网络当前占用的 IPv4 子网。"""
        subnets = []
        ipam_configs = (network.attrs.get("IPAM") or {}).get("Config") or []
        for config in ipam_configs:
            raw_subnet = str((config or {}).get("Subnet") or "").strip()
            if not raw_subnet:
                continue
            try:
                subnet = ipaddress.ip_network(raw_subnet, strict=False)
            except ValueError:
                continue
            if isinstance(subnet, ipaddress.IPv4Network):
                subnets.append(subnet)
        return subnets

    def _next_network_subnet(
        self, excluded: set[ipaddress.IPv4Network] | None = None
    ) -> ipaddress.IPv4Network:
        """从专用地址池选择不与现有 Docker 网络重叠的子网。"""
        if self._network_pool is None:
            raise RuntimeError("Docker Sandbox address pool is not configured")
        address_pool, subnet_prefix = self._network_pool
        unavailable = set(excluded or ())
        for network in self._client.networks.list():
            unavailable.update(self._network_ipv4_subnets(network))

        for candidate in address_pool.subnets(new_prefix=subnet_prefix):
            if candidate in unavailable:
                continue
            if any(candidate.overlaps(subnet) for subnet in unavailable):
                continue
            return candidate
        raise SandboxCapacityError(
            f"Docker Sandbox address pool {address_pool} has no available "
            f"/{subnet_prefix} subnet"
        )

    @staticmethod
    def _is_subnet_allocation_conflict(exc: Exception) -> bool:
        message = str(exc).lower()
        return "overlap" in message or "already allocated" in message

    def _create_network(self, network_name: str, sandbox_id: str):
        """创建 Sandbox 网络，并在跨进程子网竞争时重新选择。"""
        labels = {
            "managed-by": "yuxi-sandbox-provisioner",
            "sandbox-id": sandbox_id,
        }
        if self._network_pool is None:
            return self._client.networks.create(
                network_name,
                driver="bridge",
                labels=labels,
            )

        attempted: set[ipaddress.IPv4Network] = set()
        while True:
            subnet = self._next_network_subnet(attempted)
            ipam = self._docker.types.IPAMConfig(
                pool_configs=[self._docker.types.IPAMPool(subnet=str(subnet))]
            )
            try:
                return self._client.networks.create(
                    network_name,
                    driver="bridge",
                    labels=labels,
                    ipam=ipam,
                )
            except Exception as exc:
                if not self._is_subnet_allocation_conflict(exc):
                    raise
                attempted.add(subnet)

    def _ensure_network(self, sandbox_id: str) -> str:
        from docker.errors import NotFound

        network_name = self._network_name(sandbox_id)
        try:
            network = self._client.networks.get(network_name)
        except NotFound:
            # 子网扫描与 network create 必须是同一进程内的原子区间；
            # Docker 的重叠拒绝继续处理跨 provisioner 竞争。
            with self._lock:
                try:
                    network = self._client.networks.get(network_name)
                except NotFound:
                    network = self._create_network(network_name, sandbox_id)

        network.reload()
        if not self._has_expected_network_ownership(network, sandbox_id):
            raise RuntimeError(
                f"sandbox network {network_name} has unexpected ownership"
            )

        containers = network.attrs.get("Containers") or {}
        if self._provisioner_container.id not in containers:
            network.connect(
                self._provisioner_container, aliases=["sandbox-provisioner"]
            )
        return network_name

    def _delete_network(self, sandbox_id: str) -> None:
        from docker.errors import NotFound

        try:
            network = self._client.networks.get(self._network_name(sandbox_id))
        except NotFound:
            return
        network.reload()
        if not self._has_expected_network_ownership(network, sandbox_id):
            logger.warning(
                "Skipping removal of sandbox network %s with unexpected ownership",
                network.name,
            )
            return
        containers = network.attrs.get("Containers") or {}
        if self._provisioner_container.id in containers:
            network.disconnect(self._provisioner_container, force=True)
        network.remove()

    def _sandbox_url(self, container) -> str:
        return f"http://{container.name}:{self._container_port}"

    def _to_record(self, container, sandbox_id: str) -> SandboxRecord:
        state = (container.attrs.get("State") or {}).get("Status")
        labels = getattr(container, "labels", None) or {}
        return SandboxRecord(
            sandbox_id=sandbox_id,
            sandbox_url=self._sandbox_url(container),
            status=state or "unknown",
            generation=str(getattr(container, "id", "") or "") or None,
            workdir_path=str(labels.get("workdir-path") or "").strip() or None,
        )

    def _get_container(self, sandbox_id: str):
        from docker.errors import NotFound

        name = self._container_name(sandbox_id)
        try:
            return self._client.containers.get(name)
        except NotFound:
            return None

    def create(
        self,
        sandbox_id: str,
        thread_id: str,
        uid: str,
        env: dict[str, str] | None = None,
        *,
        workdir_path: str | None = None,
        inherit_env: bool = True,
    ) -> SandboxRecord:
        with self._sandbox_lock(sandbox_id):
            safe_thread_id = self._validate_thread_id(thread_id)
            safe_uid = self._validate_uid(uid)
            safe_workdir_path = (
                normalize_workdir_path(workdir_path) if workdir_path else None
            )
            ephemeral_storage = not inherit_env and safe_workdir_path is None
            existing = self._get_container(sandbox_id)
            if existing is not None:
                existing.reload()
                labels = getattr(existing, "labels", None) or {}
                if str(labels.get("thread-id") or "").strip() != safe_thread_id:
                    raise ValueError(
                        "sandbox runtime identity does not match existing generation"
                    )
                existing_workdir_path = (
                    str(labels.get("workdir-path") or "").strip() or None
                )
                if existing_workdir_path != safe_workdir_path:
                    raise ValueError(
                        "sandbox workdir identity does not match existing generation"
                    )
                existing_ephemeral = labels.get("storage-mode") == "ephemeral"
                if existing_ephemeral != ephemeral_storage:
                    raise ValueError(
                        "sandbox storage identity does not match existing generation"
                    )
                if ephemeral_storage and not self._has_no_persistent_file_mounts(
                    existing
                ):
                    logger.info(
                        "Recreating sandbox %s because ephemeral mounts are stale",
                        sandbox_id,
                    )
                    self.delete(sandbox_id)
                    existing = None
                elif not ephemeral_storage and not self._is_expected_skills_mount(
                    existing, safe_uid
                ):
                    logger.info(
                        "Recreating sandbox %s because skills mount is stale",
                        sandbox_id,
                    )
                    self.delete(sandbox_id)
                    existing = None
                elif not self._is_on_expected_network(existing, sandbox_id):
                    logger.info(
                        "Recreating sandbox %s because its network is stale", sandbox_id
                    )
                    self.delete(sandbox_id)
                    existing = None
                elif not ephemeral_storage and not (
                    self._has_expected_user_data_mounts(existing, safe_uid)
                ):
                    logger.info(
                        "Recreating sandbox %s because user-data mounts are stale",
                        sandbox_id,
                    )
                    self.delete(sandbox_id)
                    existing = None
            if existing is not None:
                self._ensure_network(sandbox_id)
                if existing.status == "running":
                    try:
                        record = self._to_record(existing, sandbox_id)
                        if not wait_for_sandbox_ready(
                            record.sandbox_url,
                            timeout_seconds=self._health_timeout_seconds,
                        ):
                            raise RuntimeError(
                                f"sandbox {sandbox_id} is not ready at {record.sandbox_url}"
                            )
                        return record
                    except Exception as exc:
                        logger.warning(
                            "Recreating unhealthy sandbox %s: %s", sandbox_id, exc
                        )

                try:
                    self.delete(sandbox_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to delete stale sandbox %s before recreate: %s",
                        sandbox_id,
                        exc,
                    )

            shared_workspace = None
            user_skills = None
            if not ephemeral_storage:
                self._validate_directory_without_symlinks(
                    self._user_data_container_path,
                    ("shared", safe_uid, "workspace"),
                    label="user workspace",
                )
                self._validate_directory_without_symlinks(
                    self._skill_projections_container_path,
                    (safe_uid,),
                    label="skill projection",
                )
                shared_workspace = self._shared_workspace_host_path(safe_uid)
                user_skills = self._user_skills_host_path(safe_uid)
            if safe_workdir_path and shared_workspace is not None:
                self._validate_directory_without_symlinks(
                    self._user_data_container_path / "shared" / safe_uid / "workspace",
                    PurePosixPath(safe_workdir_path).parts,
                    label="workdir_path",
                )
            network_name = self._ensure_network(sandbox_id)

            container_name = self._container_name(sandbox_id)
            run_kwargs = {
                "name": container_name,
                "detach": True,
                "labels": {
                    "app": "yuxi-sandbox",
                    "sandbox-id": sandbox_id,
                    "thread-id": safe_thread_id,
                    "uid": safe_uid,
                    "workdir-path": safe_workdir_path or "",
                    "storage-mode": "ephemeral" if ephemeral_storage else "persistent",
                    "managed-by": "yuxi-sandbox-provisioner",
                },
                "volumes": {},
                "network": network_name,
                "security_opt": ["seccomp=unconfined"],
                # The sandbox image expects /home/gem to be writable during boot.
                # Keep it ephemeral and mount persistent user-data underneath it.
                "tmpfs": {"/home/gem": "rw,exec,mode=777"},
                # 单次执行的资源上界，防止一次命令吃满宿主机。
                **self._container_limits,
            }
            if not ephemeral_storage and user_skills is not None:
                run_kwargs["volumes"][str(user_skills)] = {
                    "bind": "/home/gem/skills",
                    "mode": "ro",
                }
            if not ephemeral_storage and shared_workspace is not None:
                run_kwargs["volumes"][str(shared_workspace)] = {
                    "bind": "/home/gem/user-data",
                    "mode": "rw",
                }
                if safe_workdir_path:
                    run_kwargs["working_dir"] = (
                        f"/home/gem/user-data/{safe_workdir_path}"
                    )
            sandbox_env = (
                merged_sandbox_env(self._sandbox_env, env or {}) if inherit_env else {}
            )
            sandbox_env.update(sandbox_runtime_environment(runtime_profile_name))
            sandbox_env.update({"USER": "gem", "USER_UID": "1000", "USER_GID": "1000"})
            run_kwargs["environment"] = sandbox_env

            try:
                container = self._client.containers.run(
                    self._sandbox_image, **run_kwargs
                )
                container.reload()
                record = self._to_record(container, sandbox_id)
                if not wait_for_sandbox_ready(
                    record.sandbox_url, timeout_seconds=self._health_timeout_seconds
                ):
                    raise RuntimeError(
                        f"sandbox {sandbox_id} is not ready at {record.sandbox_url}"
                    )
                return record
            except Exception:
                try:
                    self.delete(sandbox_id)
                except Exception as cleanup_exc:
                    logger.warning(
                        "Failed to clean up sandbox %s after creation failed: %s",
                        sandbox_id,
                        cleanup_exc,
                    )
                raise

    def discover(self, sandbox_id: str) -> SandboxRecord | None:
        container = self._get_container(sandbox_id)
        if container is None:
            return None
        container.reload()
        labels = container.labels or {}
        thread_id = str(labels.get("thread-id") or "").strip()
        if not thread_id:
            return None
        uid = str(labels.get("uid") or "").strip()
        workdir_path = str(labels.get("workdir-path") or "").strip() or None
        ephemeral_storage = labels.get("storage-mode") == "ephemeral"
        if not uid:
            return None
        self._validate_thread_id(thread_id)
        safe_uid = self._validate_uid(uid)
        safe_workdir_path = (
            normalize_workdir_path(workdir_path) if workdir_path else None
        )
        if not self._is_on_expected_network(container, sandbox_id):
            logger.info(
                "Discarding stale sandbox %s on an unexpected network", sandbox_id
            )
            try:
                self.delete(sandbox_id)
            except Exception as exc:
                logger.warning(
                    "Failed to delete stale sandbox %s during discover: %s",
                    sandbox_id,
                    exc,
                )
            return None
        self._ensure_network(sandbox_id)
        if ephemeral_storage and not self._has_no_persistent_file_mounts(container):
            logger.info(
                "Discarding stale ephemeral sandbox %s with persistent mounts",
                sandbox_id,
            )
            try:
                self.delete(sandbox_id)
            except Exception as exc:
                logger.warning(
                    "Failed to delete stale sandbox %s during discover: %s",
                    sandbox_id,
                    exc,
                )
            return None
        if not ephemeral_storage and not self._is_expected_skills_mount(
            container, safe_uid
        ):
            logger.info(
                "Discarding stale sandbox %s with unexpected skills mount", sandbox_id
            )
            try:
                self.delete(sandbox_id)
            except Exception as exc:
                logger.warning(
                    "Failed to delete stale sandbox %s during discover: %s",
                    sandbox_id,
                    exc,
                )
            return None
        if not ephemeral_storage and not self._has_expected_user_data_mounts(
            container, safe_uid
        ):
            logger.info(
                "Discarding stale sandbox %s with unexpected user-data mounts",
                sandbox_id,
            )
            try:
                self.delete(sandbox_id)
            except Exception as exc:
                logger.warning(
                    "Failed to delete stale sandbox %s during discover: %s",
                    sandbox_id,
                    exc,
                )
            return None
        record = self._to_record(container, sandbox_id)
        if record.workdir_path != safe_workdir_path:
            return None
        if not record.sandbox_url:
            return None
        if not wait_for_sandbox_ready(record.sandbox_url, timeout_seconds=5):
            return None
        return record

    def list(self) -> list[SandboxRecord]:
        containers = self._client.containers.list(
            all=True,
            filters={
                "label": ["app=yuxi-sandbox", "managed-by=yuxi-sandbox-provisioner"]
            },
        )
        records: list[SandboxRecord] = []
        for container in containers:
            labels = container.labels or {}
            sandbox_id = labels.get("sandbox-id")
            if sandbox_id:
                container.reload()
                records.append(self._to_record(container, sandbox_id))
        return records

    def delete(
        self, sandbox_id: str, *, expected_generation: str | None = None
    ) -> None:
        with self._sandbox_lock(sandbox_id), self._delete_slots:
            container = self._get_container(sandbox_id)
            if container is not None:
                container.reload()
                current_generation = str(getattr(container, "id", "") or "") or None
                if expected_generation and current_generation != expected_generation:
                    raise SandboxGenerationMismatchError(
                        "sandbox generation does not match delete request"
                    )
                if container.status == "running":
                    container.stop(timeout=self._stop_timeout_seconds)
                container.remove(v=True, force=True)
            self._delete_network(sandbox_id)


class KubernetesProvisionerBackend:
    def __init__(self):
        from kubernetes import client, config

        self._lock = threading.RLock()
        self._namespace = os.getenv("K8S_NAMESPACE", "yuxi-know")
        self._sandbox_image = os.getenv(
            "SANDBOX_IMAGE",
            DEFAULT_SANDBOX_IMAGE,
        )
        self._skill_pvc = os.getenv("SKILLS_PVC", "yuxi-skills")
        self._user_data_pvc = os.getenv("USER_DATA_PVC", "yuxi-user-data")
        self._node_host = os.getenv("NODE_HOST", "host.docker.internal")
        self._container_port = int(os.getenv("SANDBOX_CONTAINER_PORT", "8080"))
        self._sandbox_env = load_sandbox_env()

        kubeconfig_path = os.getenv("KUBECONFIG_PATH")
        if kubeconfig_path:
            config.load_kube_config(config_file=kubeconfig_path)
        else:
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()

        self._core_api = client.CoreV1Api()
        self._client = client

    @staticmethod
    def _pod_name(sandbox_id: str) -> str:
        return f"sandbox-{sandbox_id}"

    @staticmethod
    def _service_name(sandbox_id: str) -> str:
        return f"sandbox-{sandbox_id}"

    def _build_pod_spec(
        self,
        sandbox_id: str,
        thread_id: str,
        uid: str,
        env: dict[str, str],
        *,
        inherit_env: bool,
        workdir_path: str | None = None,
    ):
        pod_name = self._pod_name(sandbox_id)
        sandbox_env = merged_sandbox_env(self._sandbox_env, env) if inherit_env else {}
        sandbox_env.update(sandbox_runtime_environment(runtime_profile_name))
        sandbox_env.update({"USER": "gem", "USER_UID": "1000", "USER_GID": "1000"})
        env_vars = [
            self._client.V1EnvVar(name=key, value=value)
            for key, value in sandbox_env.items()
        ]
        sandbox_workdir = (
            f"/home/gem/user-data/{workdir_path}" if workdir_path else None
        )
        ephemeral_storage = not inherit_env and workdir_path is None
        if ephemeral_storage:
            init_command = None
            data_mounts = []
        else:
            workspace_subpath = f"shared/{uid}/workspace"
            init_command = kubernetes_storage_init_script(uid, workdir_path)
            data_mounts = [
                self._client.V1VolumeMount(
                    name="user-data",
                    mount_path="/home/gem/user-data",
                    sub_path=workspace_subpath,
                )
            ]
        return self._client.V1Pod(
            metadata=self._client.V1ObjectMeta(
                name=pod_name,
                labels={
                    "app": "yuxi-sandbox",
                    "managed-by": "yuxi-sandbox-provisioner",
                    "sandbox-id": sandbox_id,
                },
                annotations={
                    "thread-id": thread_id,
                    "uid": uid,
                    "workdir-path": workdir_path or "",
                    "storage-mode": "ephemeral" if ephemeral_storage else "persistent",
                },
            ),
            spec=self._client.V1PodSpec(
                automount_service_account_token=False,
                restart_policy="Never",
                security_context=self._client.V1PodSecurityContext(
                    run_as_user=0,
                ),
                init_containers=[]
                if init_command is None
                else [
                    self._client.V1Container(
                        name="init-user-data",
                        image=self._sandbox_image,
                        command=["python", "-c"],
                        args=[init_command],
                        volume_mounts=[
                            self._client.V1VolumeMount(
                                name="home-dir", mount_path="/home/gem"
                            ),
                            self._client.V1VolumeMount(
                                name="user-data",
                                mount_path="/mnt/user-data",
                            ),
                            self._client.V1VolumeMount(
                                name="skills-data",
                                mount_path="/mnt/skills-data",
                            ),
                        ],
                    ),
                ],
                containers=[
                    self._client.V1Container(
                        name="sandbox",
                        image=self._sandbox_image,
                        env=env_vars,
                        working_dir=sandbox_workdir,
                        ports=[
                            self._client.V1ContainerPort(
                                container_port=self._container_port
                            )
                        ],
                        volume_mounts=[
                            self._client.V1VolumeMount(
                                name="home-dir", mount_path="/home/gem"
                            ),
                            *data_mounts,
                            *(
                                [
                                    self._client.V1VolumeMount(
                                        name="skills-data",
                                        mount_path="/home/gem/skills",
                                        sub_path=f"skill-projections/{uid}",
                                        read_only=True,
                                    )
                                ]
                                if not ephemeral_storage
                                else []
                            ),
                        ],
                    )
                ],
                volumes=[
                    *(
                        [
                            self._client.V1Volume(
                                name="user-data",
                                persistent_volume_claim=self._client.V1PersistentVolumeClaimVolumeSource(
                                    claim_name=self._user_data_pvc,
                                    read_only=False,
                                ),
                            ),
                            self._client.V1Volume(
                                name="skills-data",
                                persistent_volume_claim=self._client.V1PersistentVolumeClaimVolumeSource(
                                    claim_name=self._skill_pvc,
                                    read_only=False,
                                ),
                            ),
                        ]
                        if not ephemeral_storage
                        else []
                    ),
                    self._client.V1Volume(
                        name="home-dir",
                        empty_dir=self._client.V1EmptyDirVolumeSource(),
                    ),
                ],
            ),
        )

    def _build_service_spec(self, sandbox_id: str):
        service_name = self._service_name(sandbox_id)
        return self._client.V1Service(
            metadata=self._client.V1ObjectMeta(
                name=service_name,
                labels={
                    "app": "yuxi-sandbox",
                    "managed-by": "yuxi-sandbox-provisioner",
                    "sandbox-id": sandbox_id,
                },
            ),
            spec=self._client.V1ServiceSpec(
                type="NodePort",
                selector={"sandbox-id": sandbox_id},
                ports=[
                    self._client.V1ServicePort(
                        name="http",
                        port=self._container_port,
                        target_port=self._container_port,
                        protocol="TCP",
                    )
                ],
            ),
        )

    def _pod_has_expected_mounts(
        self,
        pod,
        *,
        uid: str,
        ephemeral_storage: bool = False,
    ) -> bool:
        if ephemeral_storage:
            for container in getattr(pod.spec, "containers", []) or []:
                if getattr(container, "name", None) != "sandbox":
                    continue
                destinations = {
                    str(getattr(mount, "mount_path", "") or "").rstrip("/")
                    for mount in getattr(container, "volume_mounts", []) or []
                }
                return not any(
                    _is_persistent_sandbox_mount_path(path) for path in destinations
                )
            return False
        actual_claims = {
            str(getattr(volume, "name", "") or ""): str(
                getattr(
                    getattr(volume, "persistent_volume_claim", None),
                    "claim_name",
                    "",
                )
                or ""
            )
            for volume in getattr(pod.spec, "volumes", []) or []
        }
        if actual_claims.get("user-data") != self._user_data_pvc:
            return False
        if actual_claims.get("skills-data") != self._skill_pvc:
            return False
        expected_mounts = {
            "/home/gem/user-data": ("user-data", f"shared/{uid}/workspace"),
            "/home/gem/skills": ("skills-data", f"skill-projections/{uid}"),
        }
        for container in getattr(pod.spec, "containers", []) or []:
            if getattr(container, "name", None) != "sandbox":
                continue
            actual_mounts = {
                str(getattr(mount, "mount_path", "") or "").rstrip("/"): (
                    str(getattr(mount, "name", "") or ""),
                    str(getattr(mount, "sub_path", "") or ""),
                    getattr(mount, "read_only", None) is True,
                )
                for mount in getattr(container, "volume_mounts", []) or []
            }
            return (
                all(
                    actual_mounts.get(path, (None, None, False))[:2] == expected
                    for path, expected in expected_mounts.items()
                )
                and actual_mounts.get("/home/gem/skills", (None, None, False))[2]
                and all(
                    not _is_persistent_sandbox_mount_path(path)
                    or path in expected_mounts
                    for path in actual_mounts
                )
            )
        return False

    def _discovered_matches_request(
        self,
        sandbox_id: str,
        *,
        thread_id: str,
        uid: str,
        workdir_path: str | None,
        ephemeral_storage: bool,
    ) -> bool:
        pod_name = self._pod_name(sandbox_id)
        try:
            pod = self._core_api.read_namespaced_pod(
                name=pod_name, namespace=self._namespace
            )
        except Exception:
            return False

        annotations = pod.metadata.annotations or {}
        if str(annotations.get("thread-id") or "").strip() != thread_id:
            return False
        if str(annotations.get("uid") or "").strip() != uid:
            return False
        if (str(annotations.get("workdir-path") or "").strip() or None) != workdir_path:
            return False
        if (annotations.get("storage-mode") == "ephemeral") != ephemeral_storage:
            return False
        return self._pod_has_expected_mounts(
            pod,
            uid=uid,
            ephemeral_storage=ephemeral_storage,
        )

    def create(
        self,
        sandbox_id: str,
        thread_id: str,
        uid: str,
        env: dict[str, str] | None = None,
        *,
        workdir_path: str | None = None,
        inherit_env: bool = True,
    ) -> SandboxRecord:
        from kubernetes.client.rest import ApiException

        with self._lock:
            safe_thread_id = LocalContainerProvisionerBackend._validate_thread_id(
                thread_id
            )
            safe_uid = LocalContainerProvisionerBackend._validate_uid(uid)
            safe_workdir_path = (
                normalize_workdir_path(workdir_path) if workdir_path else None
            )
            ephemeral_storage = not inherit_env and safe_workdir_path is None
            discovered = self.discover(sandbox_id)
            if discovered is not None:
                if self._discovered_matches_request(
                    sandbox_id,
                    thread_id=safe_thread_id,
                    uid=safe_uid,
                    workdir_path=safe_workdir_path,
                    ephemeral_storage=ephemeral_storage,
                ):
                    return discovered
                raise ValueError("sandbox identity does not match existing generation")

            try:
                self._core_api.create_namespaced_pod(
                    namespace=self._namespace,
                    body=self._build_pod_spec(
                        sandbox_id,
                        safe_thread_id,
                        safe_uid,
                        env or {},
                        inherit_env=inherit_env,
                        workdir_path=safe_workdir_path,
                    ),
                )
            except ApiException as exc:
                if exc.status != 409:
                    raise
                if not self._discovered_matches_request(
                    sandbox_id,
                    thread_id=safe_thread_id,
                    uid=safe_uid,
                    workdir_path=safe_workdir_path,
                    ephemeral_storage=ephemeral_storage,
                ):
                    raise ValueError(
                        "sandbox identity does not match existing generation"
                    ) from exc

            try:
                self._core_api.create_namespaced_service(
                    namespace=self._namespace,
                    body=self._build_service_spec(sandbox_id),
                )
            except ApiException as exc:
                if exc.status != 409:
                    raise

            health_timeout = int(os.getenv("SANDBOX_HEALTH_TIMEOUT_SECONDS", "60"))
            record = self.discover(sandbox_id)
            if record is None:
                raise RuntimeError(
                    f"failed to discover sandbox after create: {sandbox_id}"
                )
            if not self._discovered_matches_request(
                sandbox_id,
                thread_id=safe_thread_id,
                uid=safe_uid,
                workdir_path=safe_workdir_path,
                ephemeral_storage=ephemeral_storage,
            ):
                raise ValueError("sandbox identity does not match created generation")
            if not wait_for_sandbox_ready(
                record.sandbox_url, timeout_seconds=health_timeout
            ):
                try:
                    self.delete(sandbox_id)
                except Exception:
                    pass
                raise RuntimeError(
                    f"sandbox {sandbox_id} is not ready at {record.sandbox_url}"
                )
            return record

    def discover(self, sandbox_id: str) -> SandboxRecord | None:
        from kubernetes.client.rest import ApiException

        pod_name = self._pod_name(sandbox_id)
        service_name = self._service_name(sandbox_id)
        try:
            pod = self._core_api.read_namespaced_pod(
                name=pod_name, namespace=self._namespace
            )
            service = self._core_api.read_namespaced_service(
                name=service_name, namespace=self._namespace
            )
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

        annotations = pod.metadata.annotations or {}
        thread_id = str(annotations.get("thread-id") or "").strip()
        if not thread_id:
            return None
        uid = str(annotations.get("uid") or "").strip()
        workdir_path = str(annotations.get("workdir-path") or "").strip() or None
        ephemeral_storage = annotations.get("storage-mode") == "ephemeral"
        if not uid:
            return None
        LocalContainerProvisionerBackend._validate_thread_id(thread_id)
        safe_uid = LocalContainerProvisionerBackend._validate_uid(uid)
        safe_workdir_path = (
            normalize_workdir_path(workdir_path) if workdir_path else None
        )
        if not self._pod_has_expected_mounts(
            pod,
            uid=safe_uid,
            ephemeral_storage=ephemeral_storage,
        ):
            if safe_workdir_path:
                raise ValueError(
                    "sandbox mounts do not match recorded Workdir identity"
                )
            logger.info(
                "Discarding stale sandbox %s with unexpected pod mounts", sandbox_id
            )
            try:
                self.delete(sandbox_id)
            except Exception as exc:
                logger.warning(
                    "Failed to delete stale sandbox %s during discover: %s",
                    sandbox_id,
                    exc,
                )
            return None

        node_port = None
        if service.spec and service.spec.ports:
            node_port = service.spec.ports[0].node_port
        if not node_port:
            sandbox_url = ""
        else:
            sandbox_url = f"http://{self._node_host}:{node_port}"

        return SandboxRecord(
            sandbox_id=sandbox_id,
            sandbox_url=sandbox_url,
            status=(pod.status.phase if pod and pod.status else "Unknown"),
            generation=str(getattr(pod.metadata, "uid", "") or "") or None,
            workdir_path=safe_workdir_path,
        )

    def list(self) -> list[SandboxRecord]:
        pod_list = self._core_api.list_namespaced_pod(
            namespace=self._namespace,
            # 升级窗口内旧 Pod 尚无 managed-by 标签；inventory 必须仍能枚举并清理它们。
            label_selector="app=yuxi-sandbox",
        )

        records: list[SandboxRecord] = []
        for pod in pod_list.items:
            sandbox_id = (pod.metadata.labels or {}).get("sandbox-id")
            if not sandbox_id:
                continue
            annotations = pod.metadata.annotations or {}
            workdir_path = str(annotations.get("workdir-path") or "").strip() or None
            records.append(
                SandboxRecord(
                    sandbox_id=sandbox_id,
                    sandbox_url="",
                    status=(pod.status.phase if pod.status else "Unknown"),
                    generation=str(getattr(pod.metadata, "uid", "") or "") or None,
                    workdir_path=workdir_path,
                )
            )
        return records

    def delete(
        self, sandbox_id: str, *, expected_generation: str | None = None
    ) -> None:
        from kubernetes.client.rest import ApiException

        with self._lock:
            pod_name = self._pod_name(sandbox_id)
            service_name = self._service_name(sandbox_id)

            delete_options = None
            if expected_generation:
                delete_options = self._client.V1DeleteOptions(
                    preconditions=self._client.V1Preconditions(uid=expected_generation)
                )
            try:
                self._core_api.delete_namespaced_pod(
                    name=pod_name,
                    namespace=self._namespace,
                    body=delete_options,
                )
            except ApiException as exc:
                if exc.status == 409 and expected_generation:
                    raise SandboxGenerationMismatchError(
                        "sandbox generation does not match delete request"
                    ) from exc
                if exc.status != 404:
                    raise
            try:
                self._core_api.delete_namespaced_service(
                    name=service_name, namespace=self._namespace
                )
            except ApiException as exc:
                if exc.status != 404:
                    raise


class SandboxIdleReaper:
    def __init__(self, backend, operation_pins: SandboxOperationPins | None = None):
        self._backend = backend
        self._operation_pins = operation_pins or SandboxOperationPins()
        self._lock = threading.Lock()
        self._last_activity_at: dict[str, tuple[str | None, float]] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._exec_timeout_seconds = int(
            os.getenv("SANDBOX_EXEC_TIMEOUT_SECONDS", "180")
        )
        configured_idle_timeout = int(os.getenv("SANDBOX_IDLE_TIMEOUT_SECONDS", "600"))
        if 0 < configured_idle_timeout <= self._exec_timeout_seconds:
            logger.warning(
                "SANDBOX_IDLE_TIMEOUT_SECONDS=%s is <= SANDBOX_EXEC_TIMEOUT_SECONDS=%s; "
                "adjusting idle timeout to %s seconds to avoid reaping running commands",
                configured_idle_timeout,
                self._exec_timeout_seconds,
                self._exec_timeout_seconds + 30,
            )
            configured_idle_timeout = self._exec_timeout_seconds + 30
        self._idle_timeout_seconds = configured_idle_timeout
        self._check_interval_seconds = max(
            1, int(os.getenv("SANDBOX_IDLE_CHECK_INTERVAL_SECONDS", "10"))
        )

    def touch(self, sandbox_id: str, *, generation: str | None = None) -> None:
        with self._lock:
            current = self._last_activity_at.get(sandbox_id)
            observed_generation = (
                generation
                if generation is not None
                else (current[0] if current else None)
            )
            self._last_activity_at[sandbox_id] = (observed_generation, time.time())

    def forget(
        self, sandbox_id: str, *, expected_generation: str | None = None
    ) -> None:
        with self._lock:
            current = self._last_activity_at.get(sandbox_id)
            if current is None:
                return
            if expected_generation is not None and current[0] != expected_generation:
                return
            self._last_activity_at.pop(sandbox_id, None)

    def _seed_existing(self) -> None:
        try:
            records = self._backend.list()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to seed sandbox activity for idle reaper: {exc}")
            return

        now = time.time()
        with self._lock:
            for record in records:
                self._last_activity_at.setdefault(
                    record.sandbox_id, (record.generation, now)
                )

    def _collect_expired_sandboxes(self) -> list[tuple[str, str | None]]:
        if self._idle_timeout_seconds <= 0:
            return []
        cutoff = time.time() - self._idle_timeout_seconds
        with self._lock:
            return [
                (sandbox_id, generation)
                for sandbox_id, (generation, last_at) in self._last_activity_at.items()
                if last_at <= cutoff
            ]

    def _delete_expired_sandbox(self, sandbox_id: str, generation: str | None) -> None:
        self._operation_pins.begin_delete(sandbox_id)
        try:
            cutoff = time.time() - self._idle_timeout_seconds
            with self._lock:
                current = self._last_activity_at.get(sandbox_id)
                if current is None or current[0] != generation or current[1] > cutoff:
                    return
            self._backend.delete(sandbox_id, expected_generation=generation)
            logger.info(f"Deleted idle sandbox: {sandbox_id}")
            self.forget(sandbox_id, expected_generation=generation)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to delete idle sandbox {sandbox_id}: {exc}")
        finally:
            self._operation_pins.end_delete(sandbox_id)

    def _run(self) -> None:
        while not self._stop_event.wait(self._check_interval_seconds):
            expired_sandboxes = self._collect_expired_sandboxes()
            for sandbox_id, generation in expired_sandboxes:
                self._delete_expired_sandbox(sandbox_id, generation)

    def start(self) -> None:
        if self._idle_timeout_seconds <= 0:
            logger.info("Idle reaper disabled (SANDBOX_IDLE_TIMEOUT_SECONDS <= 0)")
            return
        self._seed_existing()
        self._thread = threading.Thread(
            target=self._run, name="sandbox-idle-reaper", daemon=True
        )
        self._thread.start()
        logger.info(
            "Started sandbox idle reaper with timeout=%ss interval=%ss",
            self._idle_timeout_seconds,
            self._check_interval_seconds,
        )

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3)


def _build_backend():
    backend = canonical_backend_name(os.getenv("PROVISIONER_BACKEND", "memory"))
    if backend == "docker":
        return LocalContainerProvisionerBackend(), backend
    if backend == "kubernetes":
        return KubernetesProvisionerBackend(), backend
    return MemoryProvisionerBackend(), backend


runtime_profile_name = sandbox_runtime_profile()
backend_impl, backend_name = _build_backend()
sandbox_operation_pins = SandboxOperationPins()
sandbox_quiescence_gate = SandboxQuiescenceGate()
idle_reaper = SandboxIdleReaper(backend_impl, sandbox_operation_pins)


@asynccontextmanager
async def lifespan(app: FastAPI):
    provisioner_token()
    app.state.http_client = httpx.AsyncClient(
        timeout=None, follow_redirects=False, trust_env=False
    )
    try:
        idle_reaper.start()
        yield
    finally:
        try:
            idle_reaper.shutdown()
        finally:
            await app.state.http_client.aclose()


app = FastAPI(title="Yuxi Sandbox Provisioner", lifespan=lifespan)


def sandbox_response(record: SandboxRecord) -> SandboxResponse:
    return SandboxResponse(
        sandbox_id=record.sandbox_id,
        sandbox_url=sandbox_proxy_url(record.sandbox_id),
        status=record.status,
        generation=record.generation,
        workdir_path=record.workdir_path,
    )


@app.get("/health")
def health():
    tracked = len(idle_reaper._last_activity_at)  # noqa: SLF001
    return {
        "status": "ok",
        "backend": backend_name,
        "runtime_profile": runtime_profile_name,
        "idle_timeout_seconds": idle_reaper._idle_timeout_seconds,  # noqa: SLF001
        "idle_check_interval_seconds": idle_reaper._check_interval_seconds,  # noqa: SLF001
        "tracked_sandboxes": tracked,
    }


@app.post(
    "/api/sandboxes",
    response_model=SandboxResponse,
    dependencies=[Depends(require_provisioner_auth)],
)
def create_sandbox(payload: CreateSandboxRequest):
    try:
        sandbox_quiescence_gate.acquire_create()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        sandbox_operation_pins.acquire(payload.sandbox_id)
        try:
            try:
                # Backend.create() already handles container reuse (discovers existing container first)
                record = backend_impl.create(
                    payload.sandbox_id,
                    payload.thread_id,
                    payload.uid,
                    payload.env,
                    workdir_path=payload.workdir_path,
                    inherit_env=payload.inherit_env,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except SandboxCapacityError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            sandbox_operation_pins.release(payload.sandbox_id)
    finally:
        sandbox_quiescence_gate.release_create()
    idle_reaper.touch(record.sandbox_id, generation=record.generation)
    return sandbox_response(record)


@app.get(
    "/api/sandboxes/{sandbox_id}",
    response_model=SandboxResponse,
    dependencies=[Depends(require_provisioner_auth)],
)
def get_sandbox(sandbox_id: str):
    sandbox_operation_pins.acquire(sandbox_id)
    try:
        try:
            record = backend_impl.discover(sandbox_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        if record is None:
            raise HTTPException(status_code=404, detail="sandbox not found")
        idle_reaper.touch(record.sandbox_id, generation=record.generation)
    finally:
        sandbox_operation_pins.release(sandbox_id)

    return sandbox_response(record)


@app.post(
    "/api/sandboxes/{sandbox_id}/touch",
    response_model=TouchSandboxResponse,
    dependencies=[Depends(require_provisioner_auth)],
)
def touch_sandbox(sandbox_id: str):
    sandbox_operation_pins.acquire(sandbox_id)
    try:
        try:
            record = backend_impl.discover(sandbox_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="sandbox not found")
        idle_reaper.touch(sandbox_id, generation=record.generation)
    finally:
        sandbox_operation_pins.release(sandbox_id)
    return TouchSandboxResponse(ok=True, sandbox_id=sandbox_id, status=record.status)


@app.get(
    "/api/sandboxes",
    response_model=ListSandboxesResponse,
    dependencies=[Depends(require_provisioner_auth)],
)
def list_sandboxes():
    try:
        records = backend_impl.list()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    sandboxes = [sandbox_response(record) for record in records]
    return ListSandboxesResponse(sandboxes=sandboxes, count=len(sandboxes))


@app.post(
    "/api/sandboxes/quiesce",
    response_model=QuiesceSandboxesResponse,
    dependencies=[Depends(require_provisioner_auth)],
)
def quiesce_sandboxes(timeout_seconds: int = 180):
    """禁止新建运行时，删除全部 Sandbox 并等待权威枚举归零。"""
    if timeout_seconds < 1 or timeout_seconds > 900:
        raise HTTPException(status_code=400, detail="invalid quiesce timeout")
    sandbox_quiescence_gate.begin()
    deadline = time.monotonic() + timeout_seconds
    deleted_ids: set[str] = set()
    while True:
        try:
            records = backend_impl.list()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if not records:
            return QuiesceSandboxesResponse(ok=True, deleted=len(deleted_ids))
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise HTTPException(
                status_code=504,
                detail="timed out waiting for sandbox runtimes to terminate",
            )
        try:
            deleted_ids.update(
                _delete_sandbox_records_for_quiescence(
                    records,
                    timeout_seconds=remaining_seconds,
                )
            )
        except SandboxQuiesceTimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail="timed out waiting for sandbox runtimes to terminate",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def _delete_sandbox_records_for_quiescence(
    records: list[SandboxRecord],
    *,
    timeout_seconds: float,
) -> set[str]:
    """在同一 deadline 内有界并行删除一次权威 Sandbox inventory。"""

    if not records:
        return set()
    executor = ThreadPoolExecutor(
        max_workers=min(sandbox_delete_concurrency(), len(records)),
        thread_name_prefix="sandbox-quiesce",
    )
    futures = [
        executor.submit(_delete_sandbox_record_for_quiescence, record)
        for record in records
    ]
    try:
        done, pending = wait(
            futures,
            timeout=max(0.0, timeout_seconds),
            return_when=FIRST_EXCEPTION,
        )
        failed = next(
            (future for future in done if future.exception() is not None),
            None,
        )
        if failed is not None:
            failed.result()
        if pending:
            raise SandboxQuiesceTimeoutError
        return {
            sandbox_id
            for future in done
            if (sandbox_id := future.result()) is not None
        }
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _delete_sandbox_record_for_quiescence(record: SandboxRecord) -> str | None:
    """带 operation pin 和 generation fence 删除一条 Sandbox inventory。"""

    sandbox_operation_pins.begin_delete(record.sandbox_id)
    try:
        backend_impl.delete(
            record.sandbox_id,
            expected_generation=record.generation,
        )
    except SandboxGenerationMismatchError:
        return None
    finally:
        sandbox_operation_pins.end_delete(record.sandbox_id)
    idle_reaper.forget(
        record.sandbox_id,
        expected_generation=record.generation,
    )
    return record.sandbox_id


@app.delete(
    "/api/sandboxes/{sandbox_id}",
    response_model=DeleteSandboxResponse,
    dependencies=[Depends(require_provisioner_auth)],
)
def delete_sandbox(sandbox_id: str, expected_generation: str | None = None):
    sandbox_operation_pins.begin_delete(sandbox_id)
    try:
        backend_impl.delete(sandbox_id, expected_generation=expected_generation)
    except SandboxGenerationMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        sandbox_operation_pins.end_delete(sandbox_id)
    idle_reaper.forget(sandbox_id, expected_generation=expected_generation)

    return DeleteSandboxResponse(ok=True, sandbox_id=sandbox_id)


@app.api_route(
    "/api/sandboxes/{sandbox_id}/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    dependencies=[Depends(require_provisioner_auth)],
)
@app.api_route(
    "/api/sandboxes/{sandbox_id}/proxy",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    dependencies=[Depends(require_provisioner_auth)],
)
async def proxy_sandbox_request(sandbox_id: str, request: Request, path: str = ""):
    await asyncio.to_thread(sandbox_operation_pins.acquire, sandbox_id)
    try:
        record = await asyncio.to_thread(backend_impl.discover, sandbox_id)
    except asyncio.CancelledError:
        sandbox_operation_pins.release(sandbox_id)
        raise
    except Exception as exc:  # noqa: BLE001
        sandbox_operation_pins.release(sandbox_id)
        raise HTTPException(
            status_code=502, detail="failed to discover sandbox"
        ) from exc
    if record is None:
        sandbox_operation_pins.release(sandbox_id)
        raise HTTPException(status_code=404, detail="sandbox not found")

    target_url = f"{record.sandbox_url.rstrip('/')}/{path.lstrip('/')}"
    request_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() != "authorization" and key.lower() not in HOP_BY_HOP_HEADERS
    }
    client: httpx.AsyncClient = request.app.state.http_client
    try:
        upstream_request = client.build_request(
            request.method,
            target_url,
            params=request.query_params,
            headers=request_headers,
            content=request.stream(),
        )
        upstream_response = await client.send(upstream_request, stream=True)
    except asyncio.CancelledError:
        sandbox_operation_pins.release(sandbox_id)
        raise
    except Exception as exc:  # noqa: BLE001
        sandbox_operation_pins.release(sandbox_id)
        raise HTTPException(status_code=502, detail="sandbox request failed") from exc

    async def response_body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_response.aiter_bytes():
                yield chunk
        finally:
            try:
                await upstream_response.aclose()
            finally:
                sandbox_operation_pins.release(sandbox_id)

    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() in PROXY_RESPONSE_HEADERS
    }
    idle_reaper.touch(sandbox_id, generation=record.generation)
    return StreamingResponse(
        response_body(),
        status_code=upstream_response.status_code,
        headers=response_headers,
    )
