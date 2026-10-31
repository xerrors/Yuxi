"""显式加载内置工具并执行注册。"""

from yuxi.modules.extensions.tools.builtin import web_search  # noqa: F401
from yuxi.modules.extensions.tools.builtin.ask_user_question import ask_user_question
from yuxi.modules.extensions.tools.builtin.mark_for_improvement import mark_for_improvement
from yuxi.modules.extensions.tools.builtin.ocr_parse_file import ocr_parse_file
from yuxi.modules.extensions.tools.builtin.present_artifacts import present_artifacts

# 所有 Agent 的基础工具；注册、授权和 Skill 门控共享此集合。
BASE_TOOL_NAMES = ("ask_user_question", "mark_for_improvement")

__all__ = [
    "BASE_TOOL_NAMES",
    "ask_user_question",
    "mark_for_improvement",
    "ocr_parse_file",
    "present_artifacts",
]
