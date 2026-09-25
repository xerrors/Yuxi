"""预置角色定义；每个模块导出一个 PRESET。"""

from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentPreset:
    """复用既有执行后端的角色配置。"""

    slug: str
    name: str
    description: str
    backend_id: str = "ChatbotAgent"
    context: dict[str, Any] = field(default_factory=dict)


def discover_agent_presets() -> list[AgentPreset]:
    """递归发现角色，拒绝重复标识和无效定义。"""
    presets = {}
    root = Path(__file__).parent
    for path in sorted(root.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = ".".join(path.relative_to(root).with_suffix("").parts)
        module = import_module(f"{__name__}.{module_name}")
        preset = module.PRESET
        if not isinstance(preset, AgentPreset):
            raise TypeError(f"{module.__name__}.PRESET 必须是 AgentPreset")
        if preset.slug in presets:
            raise ValueError(f"重复的预置 Agent slug: {preset.slug}")
        presets[preset.slug] = preset
    return list(presets.values())
