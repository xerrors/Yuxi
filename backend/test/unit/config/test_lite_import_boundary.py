"""LITE 进程的重依赖导入边界。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_lite_shipping_surface_excludes_knowledge_runtime_routes_skills_and_tools() -> None:
    """Fresh process 的导入图和能力面都不能宣告知识运行时。"""

    backend_root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment["LITE_MODE"] = "true"
    environment["PYTHONPATH"] = os.pathsep.join([str(backend_root), str(backend_root / "package")])
    script = """
import sys
import server.main
from yuxi.agents.skills.buildin import BUILTIN_SKILLS
from yuxi.agents.toolkits.service import get_tool_metadata

forbidden = (
    "yuxi.knowledge.runtime",
    "yuxi.knowledge.parser.unified",
    "yuxi.knowledge.chunking",
    "yuxi.knowledge.implementations.milvus",
    "yuxi.knowledge.eval",
    "yuxi.repositories.knowledge_base_repository",
    "yuxi.repositories.knowledge_file_repository",
    "yuxi.services.knowledge_dashboard_service",
    "yuxi.services.knowledge_task_service",
    "yuxi.storage.neo4j",
    "server.routers.knowledge_dashboard_router",
)
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
if loaded:
    raise SystemExit("LITE imported forbidden modules: " + ", ".join(loaded))

blocked_route_prefixes = (
    "/api/knowledge",
    "/api/evaluation",
    "/api/graph",
    "/api/dashboard/stats/knowledge",
    "/api/workspace/knowledge",
)
knowledge_routes = sorted(
    path
    for path in server.main.app.openapi()["paths"]
    if any(path.startswith(prefix) for prefix in blocked_route_prefixes)
)
if knowledge_routes:
    raise SystemExit("LITE advertised knowledge routes: " + ", ".join(knowledge_routes))

builtin_slugs = {spec.slug for spec in BUILTIN_SKILLS}
if "knowledge-base" in builtin_slugs:
    raise SystemExit("LITE advertised builtin knowledge-base Skill")

knowledge_tools = sorted(
    item["slug"] for item in get_tool_metadata() if item.get("category") == "knowledge"
)
if knowledge_tools:
    raise SystemExit("LITE advertised knowledge tools: " + ", ".join(knowledge_tools))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_full_shipping_surface_registers_knowledge_dashboard() -> None:
    """完整模式仍必须保留知识域 Dashboard 契约。"""

    backend_root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment.pop("LITE_MODE", None)
    environment["PYTHONPATH"] = os.pathsep.join([str(backend_root), str(backend_root / "package")])
    script = """
import server.main

paths = server.main.app.openapi()["paths"]
if "/api/dashboard/stats/knowledge" not in paths:
    raise SystemExit("full mode omitted /api/dashboard/stats/knowledge")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
