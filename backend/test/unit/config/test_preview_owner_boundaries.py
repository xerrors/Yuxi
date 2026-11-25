"""Preview Owner 的静态依赖方向。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "yuxi"


@pytest.mark.parametrize(
    ("relative_path", "forbidden"),
    [
        (
            "shared/files.py",
            ("yuxi.infrastructure", "yuxi.modules", "yuxi.storage", "fastapi", "starlette", "subprocess", "tempfile"),
        ),
        (
            "infrastructure/file_preview.py",
            (
                "yuxi.modules",
                "yuxi.storage",
                "fastapi",
                "starlette",
                "subprocess",
                "tempfile",
            ),
        ),
    ],
)
def test_file_boundaries_reject_forbidden_imports(relative_path, forbidden) -> None:
    imports = _imports(relative_path)

    _assert_no_forbidden_imports(imports, forbidden)
    for module in forbidden:
        with pytest.raises(AssertionError, match=module):
            _assert_no_forbidden_imports(imports | {module}, forbidden)


def _imports(relative_path: str) -> set[str]:
    """读取模块的静态导入依赖。"""
    tree = ast.parse((PACKAGE_ROOT / relative_path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _assert_no_forbidden_imports(imports: set[str], forbidden: tuple[str, ...]) -> None:
    """拒绝当前职责边界禁止的依赖。"""
    for module in imports:
        assert not module.startswith(forbidden), f"forbidden import: {module}"
