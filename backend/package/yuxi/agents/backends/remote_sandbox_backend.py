from __future__ import annotations

from urllib.parse import urlparse

import requests

from yuxi.utils.logging_config import logger

from .sandbox_backend_base import SandboxBackend
from .sandbox_info import SandboxInfo


class RemoteSandboxBackend(SandboxBackend):
    def __init__(self, provisioner_url: str):
        parsed = urlparse(provisioner_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("YUXI_SANDBOX_PROVISIONER_URL must be a valid http(s) URL")
        self._provisioner_url = provisioner_url.rstrip("/")

    def create(
        self,
        *,
        thread_id: str,
        sandbox_id: str,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> SandboxInfo:
        response = requests.post(
            f"{self._provisioner_url}/api/sandboxes",
            json={"sandbox_id": sandbox_id, "thread_id": thread_id},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        return SandboxInfo(sandbox_id=sandbox_id, sandbox_url=payload["sandbox_url"])

    def destroy(self, info: SandboxInfo) -> None:
        try:
            requests.delete(f"{self._provisioner_url}/api/sandboxes/{info.sandbox_id}", timeout=15)
        except requests.RequestException as exc:
            logger.warning(f"Failed to destroy remote sandbox {info.sandbox_id}: {exc}")

    def is_alive(self, info: SandboxInfo) -> bool:
        try:
            response = requests.get(f"{self._provisioner_url}/api/sandboxes/{info.sandbox_id}", timeout=10)
            return response.ok and response.json().get("status") == "Running"
        except requests.RequestException:
            return False

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        try:
            response = requests.get(f"{self._provisioner_url}/api/sandboxes/{sandbox_id}", timeout=10)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
            return SandboxInfo(sandbox_id=sandbox_id, sandbox_url=payload["sandbox_url"])
        except requests.RequestException:
            return None
