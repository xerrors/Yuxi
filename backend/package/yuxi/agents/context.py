"""Define the configurable parameters for the agent."""

import uuid
from dataclasses import MISSING, dataclass, field, fields
from typing import Annotated, get_args, get_origin

from yuxi import config as sys_config


@dataclass(kw_only=True)
class BaseContext:
    """
    定义一个基础 Context 供 各类 graph 继承

    配置优先级:
    1. 运行时配置(RunnableConfig)：最高优先级，直接从函数参数传入
    2. 类默认配置：最低优先级，类中定义的默认值
    """

    def update(self, data: dict):
        """更新配置字段"""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    thread_id: str = field(
        default_factory=lambda: str(uuid.uuid4()),
        metadata={"name": "线程ID", "configurable": False, "description": "用来唯一标识一个对话线程"},
    )

    user_id: str = field(
        default_factory=lambda: str(uuid.uuid4()),
        metadata={"name": "用户ID", "configurable": False, "description": "用来唯一标识一个用户"},
    )

    system_prompt: Annotated[str, {"__template_metadata__": {"kind": "prompt"}}] = field(
        default="You are a helpful assistant.",
        metadata={"name": "系统提示词", "description": "用来描述智能体的角色和行为"},
    )

    model: Annotated[str, {"__template_metadata__": {"kind": "llm"}}] = field(
        default=sys_config.default_model,
        metadata={
            "name": "智能体模型",
            "options": [],
            "description": "智能体的驱动模型，建议选择 Agent 能力较强的模型，不建议使用小参数模型。",
        },
    )

    tools: Annotated[list[str], {"__template_metadata__": {"kind": "tools"}}] = field(
        default_factory=lambda: ["ask_user_question", "tavily_search"],
        metadata={
            "name": "工具",
            "description": "内置的工具。",
        },
    )

    knowledges: Annotated[list[str] | None, {"__template_metadata__": {"kind": "knowledges"}}] = field(
        default=None,
        metadata={
            "name": "知识库",
            "description": "知识库列表，可以在左侧知识库页面中创建知识库。默认选择当前用户可访问的全部知识库。",
            "type": "list",  # Explicitly mark as list type for frontend if needed
        },
    )

    mcps: Annotated[list[str], {"__template_metadata__": {"kind": "mcps"}}] = field(
        default_factory=list,
        metadata={
            "name": "MCP服务器",
            "options": [],
            "description": (
                "MCP服务器列表，建议使用支持 SSE 的 MCP 服务器，"
                "如果需要使用 uvx 或 npx 运行的服务器，也请在项目外部启动 MCP 服务器，并在项目中配置 MCP 服务器。"
            ),
        },
    )

    skills: Annotated[list[str], {"__template_metadata__": {"kind": "skills"}}] = field(
        default_factory=list,
        metadata={
            "name": "Skills",
            "options": [],
            "description": "可选技能列表（由超级管理员维护）。运行时仅挂载并只读暴露选中的 "
            "skills。技能依赖的工具和 MCP 服务器也会被自动挂载。",
            "type": "list",
        },
    )

    subagents_model: Annotated[str, {"__template_metadata__": {"kind": "llm"}}] = field(
        default=sys_config.default_model,
        metadata={
            "name": "子智能体的默认模型",
            "description": "为所有子智能体设置默认模型，可在各子智能体配置中单独覆盖。",
        },
    )

    subagents: Annotated[list[str], {"__template_metadata__": {"kind": "subagents"}}] = field(
        default_factory=list,
        metadata={
            "name": "子智能体",
            "options": [],
            "description": "可选子智能体列表。为空表示不启用任何 SubAgent。但依然会启用一个 general-purpose 的子智能体",
            "type": "list",
        },
    )

    summary_threshold: int = field(
        default=100,
        metadata={
            "name": "上下文摘要触发阈值 (KB)",
            "description": "当上下文大小超过该值时，启用摘要功能以优化上下文使用。单位为 KB，默认值为 100KB。",
            "type": "number",
        },
    )

    @classmethod
    def get_configurable_items(cls):
        """实现一个可配置的参数列表，在 UI 上配置时使用"""
        configurable_items = {}
        for f in fields(cls):
            if f.init and not f.metadata.get("hide", False):
                if f.metadata.get("configurable", True):
                    # 处理类型信息
                    field_type = f.type
                    type_name = cls._get_type_name(field_type)

                    # 提取 Annotated 的元数据
                    template_metadata = cls._extract_template_metadata(field_type)

                    options = f.metadata.get("options", [])
                    if callable(options):
                        options = options()

                    configurable_items[f.name] = {
                        "type": f.metadata.get("type", type_name),
                        "name": f.metadata.get("name", f.name),
                        "options": options,
                        "default": f.default
                        if f.default is not MISSING
                        else f.default_factory()
                        if f.default_factory is not MISSING
                        else None,
                        "description": f.metadata.get("description", ""),
                        "template_metadata": template_metadata,  # Annotated 的额外元数据
                    }

        return configurable_items

    @classmethod
    def _get_type_name(cls, field_type) -> str:
        """获取类型名称，处理 Annotated 类型"""
        # 检查是否是 Annotated 类型
        if get_origin(field_type) is not None:
            # 处理泛型类型如 list[str], Annotated[str, {...}]
            origin = get_origin(field_type)
            if hasattr(origin, "__name__"):
                if origin.__name__ == "Annotated":
                    # Annotated 类型，获取真实类型
                    args = get_args(field_type)
                    if args:
                        return cls._get_type_name(args[0])  # 递归处理真实类型
                return origin.__name__
            else:
                return str(origin)
        elif hasattr(field_type, "__name__"):
            return field_type.__name__
        else:
            return str(field_type)

    @classmethod
    def _extract_template_metadata(cls, field_type) -> dict:
        """从 Annotated 类型中提取模板元数据"""
        if get_origin(field_type) is not None:
            origin = get_origin(field_type)
            if hasattr(origin, "__name__") and origin.__name__ == "Annotated":
                args = get_args(field_type)
                if len(args) > 1:
                    # 查找包含 __template_metadata__ 的字典
                    for metadata in args[1:]:
                        if isinstance(metadata, dict) and "__template_metadata__" in metadata:
                            return metadata["__template_metadata__"]
        return {}

    def update_from_dict(self, data: dict):
        """从字典更新配置字段"""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)
