from __future__ import annotations

import os
import shutil
import socket
import subprocess
import urllib.parse

from yuxi.utils.logging_config import logger

from .sandbox_backend_base import SandboxBackend
from .sandbox_config import get_sandbox_host
from .sandbox_info import SandboxInfo


class LocalContainerBackend(SandboxBackend):
    def __init__(
        self,
        *,
        image: str,
        base_port: int,
        container_prefix: str,
        environment: dict[str, str] | None = None,
    ):
        self._image = image
        self._base_port = base_port
        self._container_prefix = container_prefix
        self._environment = environment or {}
        self._has_docker_cli = shutil.which("docker") is not None
        self._docker_api_base = os.getenv("YUXI_DOCKER_API_BASE", "http://localhost").rstrip("/")
        self._docker_api_socket = os.getenv("YUXI_DOCKER_API_SOCKET", "/var/run/docker.sock")

    def create(
        self,
        *,
        thread_id: str,
        sandbox_id: str,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> SandboxInfo:
        container_name = f"{self._container_prefix}-{sandbox_id}"
        next_start = self._base_port

        for _attempt in range(10):
            port = self._get_free_port(next_start)
            try:
                container_id = self._start_container(container_name, port, extra_mounts)
                return SandboxInfo(
                    sandbox_id=sandbox_id,
                    sandbox_url=f"http://{get_sandbox_host()}:{port}",
                    container_name=container_name,
                    container_id=container_id,
                )
            except RuntimeError as exc:
                message = str(exc).lower()
                if "address already in use" in message or "port is already allocated" in message:
                    next_start = port + 1
                    continue
                if "container name" in message and "already in use" in message:
                    existing = self.discover(sandbox_id)
                    if existing is not None:
                        return existing
                raise

        raise RuntimeError("Could not allocate a sandbox container port")

    def destroy(self, info: SandboxInfo) -> None:
        container_id = info.container_id or info.container_name
        if container_id:
            self._stop_container(container_id)

    def is_alive(self, info: SandboxInfo) -> bool:
        return bool(info.container_name) and self._is_container_running(info.container_name)

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        container_name = f"{self._container_prefix}-{sandbox_id}"
        if not self._is_container_running(container_name):
            return None

        if not self._has_docker_cli:
            try:
                info = self._docker_api_request("GET", f"/containers/{urllib.parse.quote(container_name)}/json")
                ports = ((info.get("NetworkSettings") or {}).get("Ports") or {}).get("8080/tcp") or []
                if not ports:
                    return None
                return SandboxInfo(
                    sandbox_id=sandbox_id,
                    sandbox_url=f"http://{get_sandbox_host()}:{int(ports[0]['HostPort'])}",
                    container_name=container_name,
                    container_id=str(info.get("Id") or ""),
                )
            except Exception:
                return None

        result = subprocess.run(
            ["docker", "port", container_name, "8080"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            port = int(result.stdout.strip().split(":")[-1])
        except ValueError:
            return None
        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=f"http://{get_sandbox_host()}:{port}",
            container_name=container_name,
        )

    def _get_free_port(self, start_port: int) -> int:
        for port in range(start_port, start_port + 100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind(("0.0.0.0", port))
                    return port
            except OSError:
                continue
        raise RuntimeError("No free port available")

    def _start_container(
        self,
        container_name: str,
        port: int,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> str:
        if not self._has_docker_cli:
            return self._start_container_via_api(container_name, port, extra_mounts)

        cmd = [
            "docker",
            "run",
            "--security-opt",
            "seccomp=unconfined",
            "--rm",
            "-d",
            "-p",
            f"{port}:8080",
            "--name",
            container_name,
        ]
        for key, value in self._environment.items():
            cmd.extend(["-e", f"{key}={value}"])
        for host_path, container_path, read_only in extra_mounts or []:
            mount_spec = f"{host_path}:{container_path}"
            if read_only:
                mount_spec += ":ro"
            cmd.extend(["-v", mount_spec])
        cmd.append(self._image)
        cmd.extend(["sh", "-c", "sleep infinity"])

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error(f"Failed to start sandbox container: {result.stderr}")
            raise RuntimeError(result.stderr.strip() or "Failed to start sandbox container")
        return result.stdout.strip()

    def _docker_api_request(self, method: str, path: str, payload: dict | None = None) -> dict:
        cmd = ["curl", "-sS", "--unix-socket", self._docker_api_socket, "-X", method, f"{self._docker_api_base}{path}"]
        if payload is not None:
            import json

            cmd.extend(["-H", "Content-Type: application/json", "-d", json.dumps(payload, ensure_ascii=False)])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Docker API request failed")
        text = (result.stdout or "").strip()
        if not text:
            return {}
        import json

        data = json.loads(text)
        if isinstance(data, dict) and data.get("message"):
            raise RuntimeError(str(data["message"]))
        return data if isinstance(data, dict) else {}

    def _start_container_via_api(
        self,
        container_name: str,
        port: int,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> str:
        binds: list[str] = []
        for host_path, container_path, read_only in extra_mounts or []:
            bind = f"{host_path}:{container_path}"
            if read_only:
                bind += ":ro"
            binds.append(bind)
        create_payload = {
            "Image": self._image,
            "Cmd": ["sh", "-c", "sleep infinity"],
            "HostConfig": {
                "AutoRemove": True,
                "Binds": binds,
                "PortBindings": {"8080/tcp": [{"HostPort": str(port)}]},
                "SecurityOpt": ["seccomp=unconfined"],
            },
            "ExposedPorts": {"8080/tcp": {}},
            "Env": [f"{key}={value}" for key, value in self._environment.items()],
        }
        created = self._docker_api_request(
            "POST",
            f"/containers/create?name={urllib.parse.quote(container_name)}",
            create_payload,
        )
        container_id = created.get("Id")
        if not container_id:
            raise RuntimeError("Docker API create returned invalid payload")
        self._docker_api_request("POST", f"/containers/{container_id}/start")
        return str(container_id)

    def _stop_container(self, container_id: str) -> None:
        if not self._has_docker_cli:
            try:
                self._docker_api_request("POST", f"/containers/{container_id}/stop?t=10")
            except Exception as exc:
                logger.warning(f"Failed to stop sandbox container {container_id}: {exc}")
            return

        result = subprocess.run(["docker", "stop", container_id], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.warning(f"Failed to stop sandbox container {container_id}: {result.stderr}")

    def _is_container_running(self, container_name: str) -> bool:
        if not self._has_docker_cli:
            try:
                info = self._docker_api_request("GET", f"/containers/{urllib.parse.quote(container_name)}/json")
                return bool((info.get("State") or {}).get("Running"))
            except Exception:
                return False

        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"
