"""不同来源 Skill 的统一只读描述。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ResolvedSkill:
    """描述当前用户最终可用的 Skill 及其真实来源。"""

    id: Any
    slug: str
    name: str
    description: str
    source_type: str
    source_scope: str
    source_dir: Path
    enabled: bool
    created_by: str | None
    share_config: dict[str, Any] | None
    tool_dependencies: list[str]
    mcp_dependencies: list[str]
    skill_dependencies: list[str]
    version: str | None = None
    content_hash: str | None = None
    overrides_shared: bool = False
    shadowed_by_personal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """返回可安全提供给前端的 Skill 元数据。"""
        data = {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "source_scope": self.source_scope,
            "enabled": self.enabled,
            "created_by": self.created_by,
            "tool_dependencies": self.tool_dependencies,
            "mcp_dependencies": self.mcp_dependencies,
            "skill_dependencies": self.skill_dependencies,
            "overrides_shared": self.overrides_shared,
            "shadowed_by_personal": self.shadowed_by_personal,
        }
        if self.share_config is not None:
            data["share_config"] = self.share_config
        return data
