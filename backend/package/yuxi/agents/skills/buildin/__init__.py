from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from yuxi.agents.toolkits.utils import get_tool_info
from yuxi.agents.toolkits.mysql import get_mysql_tools


@dataclass(frozen=True)
class BuiltinSkillSpec:
    slug: str
    source_dir: Path
    description: str = ""
    tool_dependencies: tuple[str, ...] = ()
    mcp_dependencies: tuple[str, ...] = ()
    skill_dependencies: tuple[str, ...] = ()


_SKILLS_ROOT = Path(__file__).resolve().parent

BUILTIN_SKILLS: list[BuiltinSkillSpec] = [
    BuiltinSkillSpec(
        slug="reporter",
        source_dir=_SKILLS_ROOT / "reporter",
        description="生成 SQL 查询报表并生成可视化图表。",
        tool_dependencies=[t["name"] for t in get_tool_info(get_mysql_tools())],
        mcp_dependencies=("mcp-server-chart",),
    ),
]

