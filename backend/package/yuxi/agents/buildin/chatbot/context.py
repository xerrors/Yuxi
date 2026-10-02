from dataclasses import dataclass, field

from yuxi.agents.context import BaseContext, ResourceSelection


@dataclass(kw_only=True)
class ChatBotContext(BaseContext):
    subagents: ResourceSelection = field(
        default_factory=lambda: ["general-purpose"],
        metadata={
            "name": "子智能体",
            "options": [],
            "description": (
                "可选子智能体列表，默认仅启用通用任务，选择全部可启用当前用户可见的全部子智能体，空列表表示不启用。"
            ),
            "type": "list",
            "kind": "subagents",
        },
    )
