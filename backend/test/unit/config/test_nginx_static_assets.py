import os
from pathlib import Path
import re

import pytest


def _project_root() -> Path:
    """定位包含 Compose 文件的仓库根目录。"""
    configured = os.environ.get("YUXI_PROJECT_ROOT")
    if configured:
        return Path(configured)

    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").exists():
            return parent
    pytest.skip("当前测试环境未挂载仓库根目录")


def _maps_mjs_to_javascript_mime(source: str) -> bool:
    """检测 nginx 配置是否为 .mjs 声明 JavaScript MIME。"""
    return re.search(r"application/javascript\s+mjs\s*;", source) is not None


def test_nginx_maps_mjs_to_javascript_mime():
    """模块 Worker 脚本必须以 JavaScript MIME 提供，否则浏览器拒绝执行。"""
    source = (_project_root() / "docker" / "nginx" / "nginx.conf").read_text()

    assert _maps_mjs_to_javascript_mime(source)


def test_mime_guard_detects_missing_mjs_mapping():
    """删除 .mjs 映射后 guard 必须失败。"""
    assert _maps_mjs_to_javascript_mime("types { application/javascript js; }") is False
