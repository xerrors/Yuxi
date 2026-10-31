"""显式加载内置工具并执行注册。"""

from yuxi.modules.extensions.tools.builtin import web_search  # noqa: F401
from yuxi.modules.extensions.tools.builtin.ask_user_question import ask_user_question
from yuxi.modules.extensions.tools.builtin.install_skill import install_skill
from yuxi.modules.extensions.tools.builtin.mark_for_improvement import mark_for_improvement
from yuxi.modules.extensions.tools.builtin.ocr_parse_file import ocr_parse_file
from yuxi.modules.extensions.tools.builtin.present_artifacts import present_artifacts

__all__ = [
    "ask_user_question",
    "install_skill",
    "mark_for_improvement",
    "ocr_parse_file",
    "present_artifacts",
]
