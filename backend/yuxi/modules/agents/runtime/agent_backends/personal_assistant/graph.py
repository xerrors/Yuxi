"""个人助理复用通用执行图，独立拥有提示词与工具装配。"""

from dataclasses import dataclass, field

from yuxi.modules.agents.runtime.agent_backends.personal_assistant.middleware import PersonalAssistantMiddleware
from yuxi.modules.agents.runtime.agent_backends.personal_assistant.prompt import build_personal_prompt
from yuxi.modules.agents.runtime.base import BaseAgent
from yuxi.modules.agents.runtime.context import BaseContext, ResourceSelection
from yuxi.modules.agents.runtime.graph import build_agent_graph
from yuxi.modules.agents.runtime.middlewares.cooperation import create_cooperation_middleware
from yuxi.modules.agents.runtime.state import BaseState

PERSONAL_WEB_TOOLS = ("web_search", "web_fetch")
PERSONAL_FILE_TOOLS = ("ls", "read_file", "write_file", "edit_file")


@dataclass(kw_only=True)
class PersonalAssistantContext(BaseContext):
    """助理的默认能力选择，资源仍由通用授权流程解析。"""

    tools: ResourceSelection = field(default_factory=lambda: list(PERSONAL_WEB_TOOLS), metadata={"hide": True})
    skills: ResourceSelection = field(default_factory=list, metadata={"hide": True})
    preload_skills: ResourceSelection = field(default_factory=list, metadata={"hide": True, "kind": "skills"})
    mcps: ResourceSelection = field(default_factory=list, metadata={"hide": True})
    knowledges: ResourceSelection = field(default_factory=list, metadata={"hide": True})
    personal_assistant_name: str = field(default="语析", metadata={"hide": True})


class PersonalAssistantAgent(BaseAgent):
    """长期主对话使用专用沟通和独立根会话委派能力。"""

    name = "个人助理"
    description = "用户的个人对话与任务协调助理。"
    context_schema = PersonalAssistantContext

    async def get_graph(self, *, context, checkpoint_only: bool = False, **kwargs):
        """声明助理的文件范围、只读记忆及独立会话委派能力。"""
        return await build_agent_graph(
            context,
            prompt_builder=build_personal_prompt,
            file_tools=PERSONAL_FILE_TOOLS,
            memory_read_only=True,
            middleware=(
                PersonalAssistantMiddleware() if context.run_id and context.uid else None,
                create_cooperation_middleware(context, independent_tasks=True),
            ),
            state_schema=BaseState,
            checkpoint_only=checkpoint_only,
        )
