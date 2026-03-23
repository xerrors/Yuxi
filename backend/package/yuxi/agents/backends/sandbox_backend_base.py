from __future__ import annotations

from abc import ABC, abstractmethod

from .sandbox_info import SandboxInfo


class SandboxBackend(ABC):
    @abstractmethod
    def create(
        self,
        *,
        thread_id: str,
        sandbox_id: str,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> SandboxInfo:
        ...

    @abstractmethod
    def destroy(self, info: SandboxInfo) -> None:
        ...

    @abstractmethod
    def is_alive(self, info: SandboxInfo) -> bool:
        ...

    @abstractmethod
    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        ...
