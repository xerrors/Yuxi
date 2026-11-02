"""静态校验 loguru 调用不使用标准 logging 的占位符与异常约定。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

LOGURU_LOGGER_MODULES = frozenset({"yuxi.infrastructure.observability.logging", "loguru"})
LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "exception", "critical"})
PERCENT_PLACEHOLDER = re.compile(r"%[sdifr]")
YUXI_ROOT = Path(__file__).resolve().parents[3] / "yuxi"


def _imports_loguru_logger(tree: ast.Module) -> bool:
    """判断模块是否导入了 loguru 的 logger。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        names = {alias.name for alias in node.names}
        if "logger" in names and (node.module or "") in LOGURU_LOGGER_MODULES:
            return True
    return False


def _log_level(node: ast.Call) -> str | None:
    """返回 logger.<level>() 或 logger.opt(...).<level>() 的级别名。"""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if isinstance(func.value, ast.Name) and func.value.id == "logger":
        return func.attr
    if isinstance(func.value, ast.Call):
        inner = func.value.func
        if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name) and inner.value.id == "logger":
            return func.attr
    return None


def find_loguru_violations(source: str) -> list[tuple[int, str]]:
    """返回 loguru 调用中误用 % 占位符或 exc_info 的位置与原因。"""
    tree = ast.parse(source)
    if not _imports_loguru_logger(tree):
        return []

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        level = _log_level(node)
        if level not in LOG_LEVELS:
            continue
        message = node.args[0] if node.args else None
        if isinstance(message, ast.Constant) and isinstance(message.value, str) and PERCENT_PLACEHOLDER.search(message.value):
            violations.append((node.lineno, f"logger.{level} 使用 % 占位符"))
        if any(keyword.arg == "exc_info" for keyword in node.keywords):
            violations.append((node.lineno, f"logger.{level} 使用 exc_info 关键字"))
    return violations


def test_backend_loguru_calls_follow_loguru_formatting() -> None:
    """生产代码中的 loguru 调用必须使用 {} 占位符和 opt(exception=True)。"""
    violations = []
    for path in sorted(YUXI_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for line, reason in find_loguru_violations(source):
            violations.append(f"{path.relative_to(YUXI_ROOT)}:{line}: {reason}")

    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('from loguru import logger\nlogger.warning("run=%s", 1)\n', 1),
        ('from loguru import logger\nlogger.error("failed", exc_info=True)\n', 1),
        ('from yuxi.infrastructure.observability.logging import logger\nlogger.opt(exception=True).error("run={}", 1)\n', 0),
        ('import logging\nlogger = logging.getLogger(__name__)\nlogger.warning("run=%s", 1)\n', 0),
    ],
)
def test_loguru_violation_detector_distinguishes_logging_conventions(source: str, expected: int) -> None:
    """负向样例证明检测器能发现旧写法，正向样例证明不误报标准 logging。"""
    assert len(find_loguru_violations(source)) == expected
