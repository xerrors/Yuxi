from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SandboxInfo:
    sandbox_id: str
    sandbox_url: str
    container_name: str = ""
    container_id: str = ""
