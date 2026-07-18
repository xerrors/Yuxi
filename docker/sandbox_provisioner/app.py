from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib import request

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from dotenv import dotenv_values

logger = logging.getLogger(__name__)

SANDBOX_ENV_FILE = Path(__file__).parent / "sandbox.env"
SAFE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
PROXY_RESPONSE_HEADERS = frozenset({"cache-control", "content-disposition", "content-type", "etag", "last-modified"})
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


def canonical_backend_name(backend: str) -> str:
    value = (backend or "").strip().lower()
    return value or "memory"


def normalize_env(env: dict | None) -> dict[str, str]:
    if not isinstance(env, dict):
        return {}
    return {str(key): "" if value is None else str(value) for key, value in env.items() if str(key)}


def load_sandbox_env() -> dict[str, str]:
    return normalize_env(dotenv_values(SANDBOX_ENV_FILE))


def merged_sandbox_env(global_env: dict[str, str], user_env: dict[str, str]) -> dict[str, str]:
    return {**global_env, **normalize_env(user_env)}


def provisioner_token() -> str:
    token = os.getenv("SANDBOX_PROVISIONER_TOKEN", "").strip()
    if len(token) < 32:
        raise RuntimeError("SANDBOX_PROVISIONER_TOKEN must contain at least 32 characters")
    return token


def require_provisioner_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = f"Bearer {provisioner_token()}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid provisioner credentials")


def sandbox_proxy_url(sandbox_id: str) -> str:
    public_url = os.getenv("PROVISIONER_PUBLIC_URL", "http://sandbox-provisioner:8002").strip().rstrip("/")
    if not public_url:
        raise RuntimeError("PROVISIONER_PUBLIC_URL is required")
    return f"{public_url}/api/sandboxes/{sandbox_id}/proxy"


class CreateSandboxRequest(BaseModel):
    sandbox_id: str
    thread_id: str
    file_thread_id: str | None = None
    skills_thread_id: str | None = None
    uid: str
    env: dict[str, str] = Field(default_factory=dict)


class SandboxResponse(BaseModel):
    sandbox_id: str
    sandbox_url: str
    status: str | None = None


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


@dataclass(slots=True)
class SandboxRecord:
    sandbox_id: str
    sandbox_url: str
    status: str | None = None


class MemoryProvisionerBackend:
    def __init__(self):
        self._lock = threading.Lock()
        self._records: dict[str, SandboxRecord] = {}
        self._url_template = os.getenv("MEMORY_SANDBOX_URL_TEMPLATE", "http://agent-sandbox:8000")

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
        file_thread_id: str | None = None,
        skills_thread_id: str | None = None,
    ) -> SandboxRecord:
        _ = thread_id
        _ = file_thread_id
        _ = skills_thread_id
        _ = uid
        _ = env
        with self._lock:
            existing = self._records.get(sandbox_id)
            if existing is not None:
                return existing
            record = SandboxRecord(
                sandbox_id=sandbox_id,
                sandbox_url=self._url_for(sandbox_id),
                status="Running",
            )
            self._records[sandbox_id] = record
            return record

    def discover(self, sandbox_id: str) -> SandboxRecord | None:
        with self._lock:
            return self._records.get(sandbox_id)

    def list(self) -> list[SandboxRecord]:
        with self._lock:
            return list(self._records.values())

    def delete(self, sandbox_id: str) -> None:
        with self._lock:
            self._records.pop(sandbox_id, None)


def wait_for_sandbox_ready(sandbox_url: str, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + timeout_seconds
    opener = request.build_opener(request.ProxyHandler({}))
    while time.time() < deadline:
        try:
            with opener.open(f"{sandbox_url.rstrip('/')}/v1/sandbox", timeout=3) as response:
                status_code = getattr(response, "status", 200)
            if status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


class LocalContainerProvisionerBackend:
    def __init__(self):
        import docker
        from docker.errors import DockerException

        self._docker = docker
        self._lock = threading.Lock()
        self._container_port = int(os.getenv("SANDBOX_CONTAINER_PORT", "8080"))
        self._sandbox_image = os.getenv(
            "SANDBOX_IMAGE",
            "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest",
        )
        self._network_prefix = os.getenv("DOCKER_NETWORK_PREFIX")
        if not self._network_prefix:
            raise RuntimeError("DOCKER_NETWORK_PREFIX is required for the docker backend")
        self._threads_host_path = os.getenv("DOCKER_THREADS_HOST_PATH")
        self._container_prefix = os.getenv("DOCKER_SANDBOX_PREFIX", "yuxi-sandbox")
        self._health_timeout_seconds = int(os.getenv("SANDBOX_HEALTH_TIMEOUT_SECONDS", "300"))
        self._sandbox_env = load_sandbox_env()

        try:
            self._client = docker.from_env()
            self._client.ping()
            self._provisioner_container = self._client.containers.get(os.environ["HOSTNAME"])
        except DockerException as exc:
            raise RuntimeError(f"docker backend unavailable: {exc}") from exc

        self._resolve_host_paths()
        self._threads_host_path = self._normalize_host_bind_path(self._threads_host_path)

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
        return LocalContainerProvisionerBackend._validate_path_segment(thread_id, "thread_id")

    @staticmethod
    def _validate_uid(uid: str) -> str:
        return LocalContainerProvisionerBackend._validate_path_segment(uid, "uid")

    @staticmethod
    def _sanitize_id(value: str) -> str:
        sanitized = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip().lower())
        return sanitized[:48] or "sandbox"

    def _container_name(self, sandbox_id: str) -> str:
        return f"{self._container_prefix}-{self._sanitize_id(sandbox_id)}"

    def _network_name(self, sandbox_id: str) -> str:
        prefix = self._network_prefix.rstrip("-_")
        return f"{prefix}-{self._sanitize_id(sandbox_id)}"

    def _thread_skills_host_path(self, thread_id: str) -> Path:
        threads_root = Path(self._threads_host_path).resolve()
        thread_skills = (threads_root / thread_id / "skills").resolve()
        try:
            thread_skills.relative_to(threads_root)
        except ValueError as exc:
            raise ValueError("thread skills path resolved outside threads host root") from exc
        return thread_skills

    def _shared_workspace_host_path(self, uid: str) -> Path:
        threads_root = Path(self._threads_host_path).resolve()
        workspace = (threads_root / "shared" / uid / "workspace").resolve()
        try:
            workspace.relative_to(threads_root)
        except ValueError as exc:
            raise ValueError("user workspace path resolved outside threads host root") from exc
        return workspace

    def _thread_uploads_host_path(self, thread_id: str) -> Path:
        threads_root = Path(self._threads_host_path).resolve()
        uploads = (threads_root / thread_id / "user-data" / "uploads").resolve()
        try:
            uploads.relative_to(threads_root)
        except ValueError as exc:
            raise ValueError("thread uploads path resolved outside threads host root") from exc
        return uploads

    def _thread_outputs_host_path(self, thread_id: str) -> Path:
        threads_root = Path(self._threads_host_path).resolve()
        outputs = (threads_root / thread_id / "user-data" / "outputs").resolve()
        try:
            outputs.relative_to(threads_root)
        except ValueError as exc:
            raise ValueError("thread outputs path resolved outside threads host root") from exc
        return outputs

    def _is_expected_skills_mount(self, container, skills_thread_id: str) -> bool:
        expected_source = str(self._thread_skills_host_path(skills_thread_id))
        for mount in container.attrs.get("Mounts") or []:
            destination = (mount.get("Destination") or "").rstrip("/")
            if destination != "/home/gem/skills":
                continue
            source = str(mount.get("Source") or "").rstrip("/")
            return source == expected_source
        return False

    def _has_expected_user_data_mounts(self, container, file_thread_id: str, uid: str) -> bool:
        expected_mounts = {
            "/home/gem/user-data/workspace": str(self._shared_workspace_host_path(uid)),
            "/home/gem/user-data/uploads": str(self._thread_uploads_host_path(file_thread_id)),
            "/home/gem/user-data/outputs": str(self._thread_outputs_host_path(file_thread_id)),
        }
        actual_mounts = {
            str((mount.get("Destination") or "").rstrip("/")): str((mount.get("Source") or "").rstrip("/"))
            for mount in container.attrs.get("Mounts") or []
        }
        return all(actual_mounts.get(destination) == source for destination, source in expected_mounts.items())

    def _resolve_host_paths(self) -> None:
        if self._threads_host_path:
            return

        container_id = os.getenv("HOSTNAME", "").strip()
        if not container_id:
            raise RuntimeError("HOSTNAME is required to infer docker backend host paths")

        inspected = self._client.api.inspect_container(container_id)
        mounts = inspected.get("Mounts") or []

        saves_source = None
        for mount in mounts:
            destination = (mount.get("Destination") or "").rstrip("/")
            if destination == "/app/saves":
                saves_source = mount.get("Source")
                break

        if not saves_source:
            raise RuntimeError("cannot infer host path for /app/saves mount")

        base = Path(self._normalize_host_bind_path(saves_source))
        if not self._threads_host_path:
            self._threads_host_path = str(base / "threads")

    def _is_on_expected_network(self, container, sandbox_id: str) -> bool:
        networks = (container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
        return set(networks) == {self._network_name(sandbox_id)}

    @staticmethod
    def _has_expected_network_ownership(network, sandbox_id: str) -> bool:
        labels = network.attrs.get("Labels") or {}
        return labels.get("managed-by") == "yuxi-sandbox-provisioner" and labels.get("sandbox-id") == sandbox_id

    def _ensure_network(self, sandbox_id: str) -> str:
        from docker.errors import NotFound

        network_name = self._network_name(sandbox_id)
        try:
            network = self._client.networks.get(network_name)
        except NotFound:
            network = self._client.networks.create(
                network_name,
                driver="bridge",
                labels={
                    "managed-by": "yuxi-sandbox-provisioner",
                    "sandbox-id": sandbox_id,
                },
            )

        network.reload()
        if not self._has_expected_network_ownership(network, sandbox_id):
            raise RuntimeError(f"sandbox network {network_name} has unexpected ownership")

        containers = network.attrs.get("Containers") or {}
        if self._provisioner_container.id not in containers:
            network.connect(self._provisioner_container, aliases=["sandbox-provisioner"])
        return network_name

    def _delete_network(self, sandbox_id: str) -> None:
        from docker.errors import NotFound

        try:
            network = self._client.networks.get(self._network_name(sandbox_id))
        except NotFound:
            return
        network.reload()
        if not self._has_expected_network_ownership(network, sandbox_id):
            logger.warning("Skipping removal of sandbox network %s with unexpected ownership", network.name)
            return
        containers = network.attrs.get("Containers") or {}
        if self._provisioner_container.id in containers:
            network.disconnect(self._provisioner_container, force=True)
        network.remove()

    def _sandbox_url(self, container) -> str:
        return f"http://{container.name}:{self._container_port}"

    def _to_record(self, container, sandbox_id: str) -> SandboxRecord:
        state = (container.attrs.get("State") or {}).get("Status")
        return SandboxRecord(
            sandbox_id=sandbox_id,
            sandbox_url=self._sandbox_url(container),
            status=state or "unknown",
        )

    @staticmethod
    def _ensure_user_data_writable(container) -> None:
        cmd = (
            "sh -lc "
            '"mkdir -p /home/gem/user-data/workspace /home/gem/user-data/uploads /home/gem/user-data/outputs '
            "&& chmod -R a+rwx /home/gem/user-data/workspace "
            '&& chmod a+rwx /home/gem/user-data /home/gem/user-data/uploads /home/gem/user-data/outputs"'
        )
        result = container.exec_run(cmd, user="0:0")
        if result.exit_code != 0:
            output = (
                result.output.decode("utf-8", errors="ignore")
                if isinstance(result.output, bytes)
                else str(result.output)
            )
            raise RuntimeError(f"failed to ensure writable thread user-data mount: {output}")

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
        file_thread_id: str | None = None,
        skills_thread_id: str | None = None,
    ) -> SandboxRecord:
        with self._lock:
            safe_thread_id = self._validate_thread_id(thread_id)
            safe_file_thread_id = self._validate_thread_id(file_thread_id or safe_thread_id)
            safe_skills_thread_id = self._validate_thread_id(skills_thread_id or safe_thread_id)
            safe_uid = self._validate_uid(uid)
            existing = self._get_container(sandbox_id)
            if existing is not None:
                existing.reload()
                if not self._is_expected_skills_mount(existing, safe_skills_thread_id):
                    logger.info("Recreating sandbox %s because skills mount is stale", sandbox_id)
                    self.delete(sandbox_id)
                    existing = None
                elif not self._is_on_expected_network(existing, sandbox_id):
                    logger.info("Recreating sandbox %s because its network is stale", sandbox_id)
                    self.delete(sandbox_id)
                    existing = None
                elif not self._has_expected_user_data_mounts(existing, safe_file_thread_id, safe_uid):
                    logger.info("Recreating sandbox %s because user-data mounts are stale", sandbox_id)
                    self.delete(sandbox_id)
                    existing = None
            if existing is not None:
                self._ensure_network(sandbox_id)
                if existing.status == "running":
                    try:
                        self._ensure_user_data_writable(existing)
                        record = self._to_record(existing, sandbox_id)
                        if not wait_for_sandbox_ready(record.sandbox_url, timeout_seconds=self._health_timeout_seconds):
                            raise RuntimeError(f"sandbox {sandbox_id} is not ready at {record.sandbox_url}")
                        return record
                    except Exception as exc:
                        logger.warning("Recreating unhealthy sandbox %s: %s", sandbox_id, exc)

                try:
                    self.delete(sandbox_id)
                except Exception as exc:
                    logger.warning("Failed to delete stale sandbox %s before recreate: %s", sandbox_id, exc)

            shared_workspace = self._shared_workspace_host_path(safe_uid)
            shared_workspace.mkdir(parents=True, exist_ok=True)
            thread_uploads = self._thread_uploads_host_path(safe_file_thread_id)
            thread_outputs = self._thread_outputs_host_path(safe_file_thread_id)
            thread_uploads.mkdir(parents=True, exist_ok=True)
            thread_outputs.mkdir(parents=True, exist_ok=True)
            thread_skills = self._thread_skills_host_path(safe_skills_thread_id)
            thread_skills.mkdir(parents=True, exist_ok=True)
            network_name = self._ensure_network(sandbox_id)

            container_name = self._container_name(sandbox_id)
            run_kwargs = {
                "name": container_name,
                "detach": True,
                "labels": {
                    "app": "yuxi-sandbox",
                    "sandbox-id": sandbox_id,
                    "thread-id": safe_thread_id,
                    "file-thread-id": safe_file_thread_id,
                    "skills-thread-id": safe_skills_thread_id,
                    "uid": safe_uid,
                    "managed-by": "yuxi-sandbox-provisioner",
                },
                "volumes": {
                    str(shared_workspace): {"bind": "/home/gem/user-data/workspace", "mode": "rw"},
                    str(thread_uploads): {"bind": "/home/gem/user-data/uploads", "mode": "rw"},
                    str(thread_outputs): {"bind": "/home/gem/user-data/outputs", "mode": "rw"},
                    str(thread_skills): {"bind": "/home/gem/skills", "mode": "ro"},
                },
                "network": network_name,
                "security_opt": ["seccomp=unconfined"],
                # The sandbox image expects /home/gem to be writable during boot.
                # Keep it ephemeral and mount persistent user-data underneath it.
                "tmpfs": {"/home/gem": "rw,exec,mode=777"},
            }
            sandbox_env = merged_sandbox_env(self._sandbox_env, env or {})
            if sandbox_env:
                run_kwargs["environment"] = sandbox_env

            try:
                container = self._client.containers.run(self._sandbox_image, **run_kwargs)
                container.reload()
                self._ensure_user_data_writable(container)
                record = self._to_record(container, sandbox_id)
                if not wait_for_sandbox_ready(record.sandbox_url, timeout_seconds=self._health_timeout_seconds):
                    raise RuntimeError(f"sandbox {sandbox_id} is not ready at {record.sandbox_url}")
                return record
            except Exception:
                try:
                    self.delete(sandbox_id)
                except Exception as cleanup_exc:
                    logger.warning("Failed to clean up sandbox %s after creation failed: %s", sandbox_id, cleanup_exc)
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
        file_thread_id = str(labels.get("file-thread-id") or thread_id).strip()
        skills_thread_id = str(labels.get("skills-thread-id") or thread_id).strip()
        uid = str(labels.get("uid") or "").strip()
        if not uid:
            return None
        safe_file_thread_id = self._validate_thread_id(file_thread_id)
        safe_skills_thread_id = self._validate_thread_id(skills_thread_id)
        safe_uid = self._validate_uid(uid)
        if not self._is_on_expected_network(container, sandbox_id):
            logger.info("Discarding stale sandbox %s on an unexpected network", sandbox_id)
            try:
                self.delete(sandbox_id)
            except Exception as exc:
                logger.warning("Failed to delete stale sandbox %s during discover: %s", sandbox_id, exc)
            return None
        self._ensure_network(sandbox_id)
        if not self._is_expected_skills_mount(container, safe_skills_thread_id):
            logger.info("Discarding stale sandbox %s with unexpected skills mount", sandbox_id)
            try:
                self.delete(sandbox_id)
            except Exception as exc:
                logger.warning("Failed to delete stale sandbox %s during discover: %s", sandbox_id, exc)
            return None
        if not self._has_expected_user_data_mounts(container, safe_file_thread_id, safe_uid):
            logger.info("Discarding stale sandbox %s with unexpected user-data mounts", sandbox_id)
            try:
                self.delete(sandbox_id)
            except Exception as exc:
                logger.warning("Failed to delete stale sandbox %s during discover: %s", sandbox_id, exc)
            return None
        record = self._to_record(container, sandbox_id)
        if not record.sandbox_url:
            return None
        if not wait_for_sandbox_ready(record.sandbox_url, timeout_seconds=5):
            return None
        return record

    def list(self) -> list[SandboxRecord]:
        containers = self._client.containers.list(
            all=True, filters={"label": ["app=yuxi-sandbox", "managed-by=yuxi-sandbox-provisioner"]}
        )
        records: list[SandboxRecord] = []
        for container in containers:
            labels = container.labels or {}
            sandbox_id = labels.get("sandbox-id")
            if sandbox_id:
                container.reload()
                records.append(self._to_record(container, sandbox_id))
        return records

    def delete(self, sandbox_id: str) -> None:
        container = self._get_container(sandbox_id)
        if container is not None:
            if container.status == "running":
                container.stop(timeout=10)
            container.remove(v=True, force=True)
        self._delete_network(sandbox_id)


class KubernetesProvisionerBackend:
    def __init__(self):
        from kubernetes import client, config

        self._lock = threading.Lock()
        self._namespace = os.getenv("K8S_NAMESPACE", "yuxi-know")
        self._sandbox_image = os.getenv(
            "SANDBOX_IMAGE",
            "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest",
        )
        self._skill_pvc = os.getenv("SKILLS_PVC", "yuxi-skills")
        self._thread_pvc = os.getenv("THREAD_PVC", "yuxi-thread")
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
        file_thread_id: str,
        skills_thread_id: str,
    ):
        pod_name = self._pod_name(sandbox_id)
        env_vars = [
            self._client.V1EnvVar(name=key, value=value)
            for key, value in merged_sandbox_env(self._sandbox_env, env).items()
        ]
        return self._client.V1Pod(
            metadata=self._client.V1ObjectMeta(
                name=pod_name,
                labels={"app": "yuxi-sandbox", "sandbox-id": sandbox_id},
                annotations={
                    "thread-id": thread_id,
                    "file-thread-id": file_thread_id,
                    "skills-thread-id": skills_thread_id,
                    "uid": uid,
                },
            ),
            spec=self._client.V1PodSpec(
                restart_policy="Never",
                security_context=self._client.V1PodSecurityContext(
                    fs_group=0,
                    run_as_user=0,
                ),
                init_containers=[
                    self._client.V1Container(
                        name="init-user-data",
                        image=self._sandbox_image,
                        command=["sh", "-c"],
                        args=[
                            "chmod 777 /home/gem "
                            f"&& mkdir -p /mnt/shared-data/threads/shared/{uid}/workspace "
                            f"/mnt/shared-data/threads/{file_thread_id}/user-data/uploads "
                            f"/mnt/shared-data/threads/{file_thread_id}/user-data/outputs "
                            f"/mnt/shared-data/threads/{skills_thread_id}/skills "
                            f"&& chmod -R 777 /mnt/shared-data/threads/shared/{uid}/workspace "
                            f"/mnt/shared-data/threads/{file_thread_id}/user-data ",
                        ],
                        volume_mounts=[
                            self._client.V1VolumeMount(name="home-dir", mount_path="/home/gem"),
                            self._client.V1VolumeMount(name="shared-data", mount_path="/mnt/shared-data"),
                        ],
                    ),
                ],
                containers=[
                    self._client.V1Container(
                        name="sandbox",
                        image=self._sandbox_image,
                        env=env_vars,
                        ports=[self._client.V1ContainerPort(container_port=self._container_port)],
                        volume_mounts=[
                            self._client.V1VolumeMount(name="home-dir", mount_path="/home/gem"),
                            self._client.V1VolumeMount(
                                name="shared-data",
                                mount_path="/home/gem/user-data/workspace",
                                sub_path=f"threads/shared/{uid}/workspace",
                            ),
                            self._client.V1VolumeMount(
                                name="shared-data",
                                mount_path="/home/gem/user-data/uploads",
                                sub_path=f"threads/{file_thread_id}/user-data/uploads",
                            ),
                            self._client.V1VolumeMount(
                                name="shared-data",
                                mount_path="/home/gem/user-data/outputs",
                                sub_path=f"threads/{file_thread_id}/user-data/outputs",
                            ),
                            self._client.V1VolumeMount(
                                name="shared-data",
                                mount_path="/home/gem/skills",
                                sub_path=f"threads/{skills_thread_id}/skills",
                                read_only=True,
                            ),
                        ],
                    )
                ],
                volumes=[
                    self._client.V1Volume(
                        name="shared-data",
                        persistent_volume_claim=self._client.V1PersistentVolumeClaimVolumeSource(
                            claim_name=self._thread_pvc,
                            read_only=False,
                        ),
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
                labels={"app": "yuxi-sandbox", "sandbox-id": sandbox_id},
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

    @staticmethod
    def _pod_has_expected_mounts(pod, *, file_thread_id: str, skills_thread_id: str, uid: str) -> bool:
        expected_mounts = {
            "/home/gem/user-data/workspace": f"threads/shared/{uid}/workspace",
            "/home/gem/user-data/uploads": f"threads/{file_thread_id}/user-data/uploads",
            "/home/gem/user-data/outputs": f"threads/{file_thread_id}/user-data/outputs",
            "/home/gem/skills": f"threads/{skills_thread_id}/skills",
        }
        for container in getattr(pod.spec, "containers", []) or []:
            if getattr(container, "name", None) != "sandbox":
                continue
            actual_mounts = {
                str(getattr(mount, "mount_path", "") or "").rstrip("/"): str(getattr(mount, "sub_path", "") or "")
                for mount in getattr(container, "volume_mounts", []) or []
            }
            return all(actual_mounts.get(path) == sub_path for path, sub_path in expected_mounts.items())
        return False

    def _discovered_matches_request(
        self,
        sandbox_id: str,
        *,
        uid: str,
        file_thread_id: str,
        skills_thread_id: str,
    ) -> bool:
        pod_name = self._pod_name(sandbox_id)
        try:
            pod = self._core_api.read_namespaced_pod(name=pod_name, namespace=self._namespace)
        except Exception:
            return False

        annotations = pod.metadata.annotations or {}
        if str(annotations.get("uid") or "").strip() != uid:
            return False
        if str(annotations.get("file-thread-id") or annotations.get("thread-id") or "").strip() != file_thread_id:
            return False
        if str(annotations.get("skills-thread-id") or annotations.get("thread-id") or "").strip() != skills_thread_id:
            return False
        return self._pod_has_expected_mounts(
            pod,
            file_thread_id=file_thread_id,
            skills_thread_id=skills_thread_id,
            uid=uid,
        )

    def create(
        self,
        sandbox_id: str,
        thread_id: str,
        uid: str,
        env: dict[str, str] | None = None,
        *,
        file_thread_id: str | None = None,
        skills_thread_id: str | None = None,
    ) -> SandboxRecord:
        from kubernetes.client.rest import ApiException

        with self._lock:
            safe_thread_id = LocalContainerProvisionerBackend._validate_thread_id(thread_id)
            safe_file_thread_id = LocalContainerProvisionerBackend._validate_thread_id(file_thread_id or safe_thread_id)
            safe_skills_thread_id = LocalContainerProvisionerBackend._validate_thread_id(
                skills_thread_id or safe_thread_id
            )
            safe_uid = LocalContainerProvisionerBackend._validate_uid(uid)
            discovered = self.discover(sandbox_id)
            if discovered is not None:
                if self._discovered_matches_request(
                    sandbox_id,
                    uid=safe_uid,
                    file_thread_id=safe_file_thread_id,
                    skills_thread_id=safe_skills_thread_id,
                ):
                    return discovered
                logger.info("Deleting sandbox %s with mismatched requested identity", sandbox_id)
                self.delete(sandbox_id)

            self._pod_name(sandbox_id)
            self._service_name(sandbox_id)

            try:
                self._core_api.create_namespaced_pod(
                    namespace=self._namespace,
                    body=self._build_pod_spec(
                        sandbox_id,
                        safe_thread_id,
                        safe_uid,
                        env or {},
                        file_thread_id=safe_file_thread_id,
                        skills_thread_id=safe_skills_thread_id,
                    ),
                )
            except ApiException as exc:
                if exc.status != 409:
                    raise

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
                raise RuntimeError(f"failed to discover sandbox after create: {sandbox_id}")
            if not wait_for_sandbox_ready(record.sandbox_url, timeout_seconds=health_timeout):
                try:
                    self.delete(sandbox_id)
                except Exception:
                    pass
                raise RuntimeError(f"sandbox {sandbox_id} is not ready at {record.sandbox_url}")
            return record

    def discover(self, sandbox_id: str) -> SandboxRecord | None:
        from kubernetes.client.rest import ApiException

        pod_name = self._pod_name(sandbox_id)
        service_name = self._service_name(sandbox_id)
        try:
            pod = self._core_api.read_namespaced_pod(name=pod_name, namespace=self._namespace)
            service = self._core_api.read_namespaced_service(name=service_name, namespace=self._namespace)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

        annotations = pod.metadata.annotations or {}
        thread_id = str(annotations.get("thread-id") or "").strip()
        if not thread_id:
            return None
        file_thread_id = str(annotations.get("file-thread-id") or thread_id).strip()
        skills_thread_id = str(annotations.get("skills-thread-id") or thread_id).strip()
        uid = str(annotations.get("uid") or "").strip()
        if not uid:
            return None
        safe_file_thread_id = LocalContainerProvisionerBackend._validate_thread_id(file_thread_id)
        safe_skills_thread_id = LocalContainerProvisionerBackend._validate_thread_id(skills_thread_id)
        safe_uid = LocalContainerProvisionerBackend._validate_uid(uid)
        if not self._pod_has_expected_mounts(
            pod,
            file_thread_id=safe_file_thread_id,
            skills_thread_id=safe_skills_thread_id,
            uid=safe_uid,
        ):
            logger.info("Discarding stale sandbox %s with unexpected pod mounts", sandbox_id)
            try:
                self.delete(sandbox_id)
            except Exception as exc:
                logger.warning("Failed to delete stale sandbox %s during discover: %s", sandbox_id, exc)
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
        )

    def list(self) -> list[SandboxRecord]:
        from kubernetes.client.rest import ApiException

        try:
            pod_list = self._core_api.list_namespaced_pod(
                namespace=self._namespace,
                label_selector="app=yuxi-sandbox",
            )
        except ApiException:
            return []

        records: list[SandboxRecord] = []
        for pod in pod_list.items:
            sandbox_id = (pod.metadata.labels or {}).get("sandbox-id")
            if not sandbox_id:
                continue
            record = self.discover(sandbox_id)
            if record is not None:
                records.append(record)
        return records

    def delete(self, sandbox_id: str) -> None:
        from kubernetes.client.rest import ApiException

        pod_name = self._pod_name(sandbox_id)
        service_name = self._service_name(sandbox_id)

        for delete_call in (
            lambda: self._core_api.delete_namespaced_service(name=service_name, namespace=self._namespace),
            lambda: self._core_api.delete_namespaced_pod(name=pod_name, namespace=self._namespace),
        ):
            try:
                delete_call()
            except ApiException as exc:
                if exc.status != 404:
                    raise


class SandboxIdleReaper:
    def __init__(self, backend):
        self._backend = backend
        self._lock = threading.Lock()
        self._last_activity_at: dict[str, float] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._exec_timeout_seconds = int(os.getenv("SANDBOX_EXEC_TIMEOUT_SECONDS", "180"))
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
        self._check_interval_seconds = max(1, int(os.getenv("SANDBOX_IDLE_CHECK_INTERVAL_SECONDS", "10")))

    def touch(self, sandbox_id: str) -> None:
        with self._lock:
            self._last_activity_at[sandbox_id] = time.time()

    def forget(self, sandbox_id: str) -> None:
        with self._lock:
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
                self._last_activity_at.setdefault(record.sandbox_id, now)

    def _collect_expired_sandbox_ids(self) -> list[str]:
        if self._idle_timeout_seconds <= 0:
            return []
        cutoff = time.time() - self._idle_timeout_seconds
        with self._lock:
            return [sandbox_id for sandbox_id, last_at in self._last_activity_at.items() if last_at <= cutoff]

    def _run(self) -> None:
        while not self._stop_event.wait(self._check_interval_seconds):
            expired_ids = self._collect_expired_sandbox_ids()
            for sandbox_id in expired_ids:
                try:
                    self._backend.delete(sandbox_id)
                    logger.info(f"Deleted idle sandbox: {sandbox_id}")
                    self.forget(sandbox_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Failed to delete idle sandbox {sandbox_id}: {exc}")

    def start(self) -> None:
        if self._idle_timeout_seconds <= 0:
            logger.info("Idle reaper disabled (SANDBOX_IDLE_TIMEOUT_SECONDS <= 0)")
            return
        self._seed_existing()
        self._thread = threading.Thread(target=self._run, name="sandbox-idle-reaper", daemon=True)
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


backend_impl, backend_name = _build_backend()
idle_reaper = SandboxIdleReaper(backend_impl)


@asynccontextmanager
async def lifespan(app: FastAPI):
    provisioner_token()
    app.state.http_client = httpx.AsyncClient(timeout=None, follow_redirects=False, trust_env=False)
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
    )


@app.get("/health")
def health():
    tracked = len(idle_reaper._last_activity_at)  # noqa: SLF001
    return {
        "status": "ok",
        "backend": backend_name,
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
        # Backend.create() already handles container reuse (discovers existing container first)
        record = backend_impl.create(
            payload.sandbox_id,
            payload.thread_id,
            payload.uid,
            payload.env,
            file_thread_id=payload.file_thread_id,
            skills_thread_id=payload.skills_thread_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    idle_reaper.touch(record.sandbox_id)
    return sandbox_response(record)


@app.get(
    "/api/sandboxes/{sandbox_id}",
    response_model=SandboxResponse,
    dependencies=[Depends(require_provisioner_auth)],
)
def get_sandbox(sandbox_id: str):
    try:
        record = backend_impl.discover(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if record is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    idle_reaper.touch(record.sandbox_id)

    return sandbox_response(record)


@app.post(
    "/api/sandboxes/{sandbox_id}/touch",
    response_model=TouchSandboxResponse,
    dependencies=[Depends(require_provisioner_auth)],
)
def touch_sandbox(sandbox_id: str):
    try:
        record = backend_impl.discover(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    idle_reaper.touch(sandbox_id)
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


@app.delete(
    "/api/sandboxes/{sandbox_id}",
    response_model=DeleteSandboxResponse,
    dependencies=[Depends(require_provisioner_auth)],
)
def delete_sandbox(sandbox_id: str):
    try:
        backend_impl.delete(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    idle_reaper.forget(sandbox_id)

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
    try:
        record = await asyncio.to_thread(backend_impl.discover, sandbox_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="failed to discover sandbox") from exc
    if record is None:
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
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="sandbox request failed") from exc

    async def response_body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_response.aiter_bytes():
                yield chunk
        finally:
            await upstream_response.aclose()

    response_headers = {
        key: value for key, value in upstream_response.headers.items() if key.lower() in PROXY_RESPONSE_HEADERS
    }
    idle_reaper.touch(sandbox_id)
    return StreamingResponse(
        response_body(),
        status_code=upstream_response.status_code,
        headers=response_headers,
    )
