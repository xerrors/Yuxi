"""不同来源 Skill 的统一只读描述。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict


class RuntimeSkill(TypedDict):
    """模型可用的 Skill 提示信息。"""

    name: str
    description: str
    path: str


class SkillRuntimeMetadata(TypedDict):
    """首次解析与实际刷新对应的来源修订。"""

    source_scope: str
    version: str | None
    content_hash: str | None
    source_identity: str


class SkillRuntimeSnapshot(TypedDict):
    """当前权限派生的 Skill 选择及预加载正文。"""

    capability_limited: bool
    context_skills: list[str]
    context_preload_skills: list[str]
    effective_skills: list[str]
    runtime_skills: dict[str, RuntimeSkill]
    skill_metadata: dict[str, SkillRuntimeMetadata]
    preloaded_skills: list[str]
    preloaded_skill_contents: dict[str, str]
    plugin_entry_contents: dict[str, str]


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
    bound_agent_id: int | None = None
    bound_agent: Any = None
    version: str | None = None
    content_hash: str | None = None
    overrides_shared: bool = False
    shadowed_by_personal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """返回可安全提供给前端的 Skill 元数据。"""
        data = {
            "id": self.id,
            "bound_agent_id": self.bound_agent_id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "source_scope": self.source_scope,
            "enabled": self.enabled,
            "created_by": self.created_by,
            "overrides_shared": self.overrides_shared,
            "shadowed_by_personal": self.shadowed_by_personal,
        }
        if self.share_config is not None:
            data["share_config"] = self.share_config
        return data
