"""Sandbox runner 的部署配置；所有限制在启动时校验。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunnerSettings:
    """保存 runner 的资源、镜像、挂载与认证契约。"""

    token: str
    socket_path: str
    image: str
    image_digest: str
    network: str
    memory_bytes: int
    cpus: float
    pids_limit: int
    home_bytes: int
    tmp_bytes: int
    max_containers: int
    max_execs: int
    max_lifetime_seconds: int
    host_data_dir: str | None
    host_skill_dir: str | None
    data_mount: str
    skill_mount: str
    run_uid: int
    run_gid: int
    environment: str = "development"

    @classmethod
    def from_env(cls) -> RunnerSettings:
        """只读取部署环境，非法值直接阻止 runner 提供执行能力。"""
        token = os.environ.get("SANDBOX_RUNNER_TOKEN", "").strip()
        if len(token) < 16:
            raise RuntimeError("SANDBOX_RUNNER_TOKEN 必须至少 16 个字符")
        socket_path = os.environ.get("SANDBOX_CONTAINER_SOCKET", "/var/run/docker.sock").strip()
        if socket_path.startswith("unix://"):
            socket_path = socket_path[len("unix://") :]
        if not socket_path.startswith("/"):
            raise RuntimeError("SANDBOX_CONTAINER_SOCKET 必须是 Unix socket 绝对路径")
        network = os.environ.get("SANDBOX_CONTAINER_NETWORK", "none").strip().lower()
        if network not in {"none", "bridge"}:
            raise RuntimeError("SANDBOX_CONTAINER_NETWORK 只能是 none 或 bridge")
        image = os.environ.get("SANDBOX_CONTAINER_IMAGE", "yuxi-sandbox:0.8.0.beta7").strip()
        if not image:
            raise RuntimeError("SANDBOX_CONTAINER_IMAGE 不能为空")
        digest = os.environ.get("SANDBOX_CONTAINER_IMAGE_DIGEST", "").strip()
        if digest and not digest.startswith("sha256:"):
            digest = f"sha256:{digest}"
        data_mount = os.environ.get("SANDBOX_CONTAINER_DATA_MOUNT", "/sandbox-data").strip()
        skill_mount = os.environ.get("SANDBOX_CONTAINER_SKILL_MOUNT", "/sandbox-skill-projections").strip()
        host_data_dir = os.environ.get("SANDBOX_CONTAINER_HOST_DATA_DIR", "").strip() or None
        host_skill_dir = os.environ.get("SANDBOX_CONTAINER_HOST_SKILL_DIR", "").strip() or None
        if not data_mount.startswith("/") or not skill_mount.startswith("/"):
            raise RuntimeError("SANDBOX_CONTAINER_*_MOUNT 必须是容器内绝对路径")
        for name, value in (("SANDBOX_CONTAINER_HOST_DATA_DIR", host_data_dir), ("SANDBOX_CONTAINER_HOST_SKILL_DIR", host_skill_dir)):
            if value is not None and not value.startswith("/"):
                raise RuntimeError(f"{name} 必须是绝对路径")
        return cls(
            token=token,
            socket_path=socket_path,
            image=image,
            image_digest=digest,
            network=network,
            memory_bytes=_env_int("SANDBOX_CONTAINER_MEMORY_BYTES", 1024 * 1024 * 1024),
            cpus=_env_float("SANDBOX_CONTAINER_CPUS", 1.0),
            pids_limit=_env_int("SANDBOX_CONTAINER_PIDS_LIMIT", 256, minimum=16),
            home_bytes=_env_int("SANDBOX_CONTAINER_HOME_BYTES", 256 * 1024 * 1024),
            tmp_bytes=_env_int("SANDBOX_CONTAINER_TMP_BYTES", 256 * 1024 * 1024),
            max_containers=_env_int("SANDBOX_CONTAINER_MAX_CONTAINERS", 32),
            max_execs=_env_int("SANDBOX_CONTAINER_MAX_EXECS", 8),
            max_lifetime_seconds=_env_int("SANDBOX_CONTAINER_MAX_LIFETIME_SECONDS", 7200),
            host_data_dir=host_data_dir,
            host_skill_dir=host_skill_dir,
            data_mount=data_mount,
            skill_mount=skill_mount,
            run_uid=_env_int("SANDBOX_CONTAINER_UID", 1000, minimum=1),
            run_gid=_env_int("SANDBOX_CONTAINER_GID", 1000, minimum=1),
            environment=os.environ.get("YUXI_ENV", "development").strip().lower(),
        )


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """读取正整数配置，拒绝零、负数和非法文本。"""
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数") from exc
    if value < minimum:
        raise RuntimeError(f"{name} 必须不小于 {minimum}")
    return value


def _env_float(name: str, default: float) -> float:
    """读取正浮点配置，拒绝零和非法文本。"""
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是数字") from exc
    if value <= 0:
        raise RuntimeError(f"{name} 必须大于 0")
    return value
