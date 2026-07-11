from dataclasses import dataclass, field

from yuxi.agents.context import BaseContext


@dataclass(kw_only=True)
class SubAgentContext(BaseContext):
    parent_thread_id: str | None = field(
        default=None,
        metadata={"name": "父线程ID", "configurable": False, "hide": True},
    )
    file_thread_id: str | None = field(
        default=None,
        metadata={"name": "文件线程ID", "configurable": False, "hide": True},
    )
    skills_thread_id: str | None = field(
        default=None,
        metadata={"name": "Skills线程ID", "configurable": False, "hide": True},
    )
    is_subagent_runtime: bool = field(
        default=False,
        metadata={"name": "子智能体运行态", "configurable": False, "hide": True},
    )
    allow_parent_questions: bool = field(
        default=False,
        metadata={"name": "允许向父智能体提问", "configurable": False, "hide": True},
    )
