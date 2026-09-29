from typing import Annotated

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field

from yuxi.agents.backends.paths import VIRTUAL_PERSONAL_SKILLS_PATH
from yuxi.agents.toolkits.registry import tool
from yuxi.utils.logging_config import logger


class InstallSkillInput(BaseModel):
    source: str = Field(
        description="Skill 来源，支持两种格式:\n"
        "1. Sandbox 路径: 当前 Project Workdir 或 /home/gem/user-data/ 下的绝对路径\n"
        "2. Git 仓库: owner/repo 或完整 GitHub URL"
    )
    skill_names: list[str] | None = Field(
        default=None, description="Git 安装时指定要安装的 skill slug 列表（至少一个）。Sandbox 路径安装时忽略此参数。"
    )


@tool(
    category="buildin",
    tags=["skill", "安装"],
    display_name="安装技能",
    args_schema=InstallSkillInput,
)
async def install_skill(
    source: str,
    skill_names: list[str] | None = None,
    runtime: ToolRuntime = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Command:
    """安装新的 Skill 到当前用户私有空间，并返回可直接读取的 Skill 路径。"""
    runtime_context = getattr(runtime, "context", None)
    if getattr(runtime_context, "is_subagent_runtime", False):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="错误：install_skill 只能在主智能体中使用，子智能体无法安装 Skill",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    source = str(source or "").strip()
    uid = getattr(runtime_context, "uid", None)
    thread_id = getattr(runtime_context, "thread_id", None)

    logger.info(f"install_skill called with uid={uid}, thread_id={thread_id}, source={source}")

    if not uid or not thread_id:
        return Command(
            update={"messages": [ToolMessage(content="错误：无法获取当前会话信息", tool_call_id=tool_call_id)]}
        )
    if not source:
        return Command(
            update={"messages": [ToolMessage(content="错误：Skill 来源不能为空", tool_call_id=tool_call_id)]}
        )

    try:
        from yuxi.services.skills.personal import install_personal_skills_from_source

        installed_slugs, failed_items = await install_personal_skills_from_source(
            uid=uid,
            thread_id=thread_id,
            source=source,
            skill_names=skill_names,
            workdir_relative_path=getattr(runtime_context, "workdir_relative_path", None),
            workdir_path=getattr(runtime_context, "workdir_path", None),
        )

        lines = []
        if installed_slugs:
            lines.append(f"已安装 Skill: {', '.join(installed_slugs)}")
            for slug in installed_slugs:
                lines.append(f"Skill 路径: {VIRTUAL_PERSONAL_SKILLS_PATH}/{slug}/SKILL.md")
        if failed_items:
            for item in failed_items:
                lines.append(f"安装失败 ({item['slug']}): {item.get('error', '未知错误')}")
        if not installed_slugs and not failed_items:
            lines.append("未发现需要安装的 Skill")

        return Command(
            update={
                "messages": [ToolMessage(content="\n".join(lines), tool_call_id=tool_call_id)],
            }
        )

    except Exception as e:
        logger.exception("install_skill 异常")
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"安装异常：{str(e)}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
