"""知识库可选装配与默认部署契约。"""

import json
import os
import subprocess
import sys
from copy import deepcopy

import pytest

from test.unit.config._shared import load_compose, project_root
from yuxi.bootstrap.capabilities import knowledge_enabled


def test_knowledge_is_enabled_by_default(monkeypatch):
    """默认部署继续交付完整知识库能力。"""
    monkeypatch.delenv("YUXI_KNOWLEDGE_ENABLED", raising=False)
    assert knowledge_enabled() is True


def test_builtin_knowledge_server_uses_generic_delegation_auth():
    """内置 knowledge server 走通用平台委托模式，slug 与签发器、插件声明一致。"""
    from yuxi.modules.extensions.mcp.builtin import BUILTIN_MCP_DEFAULTS, BUILTIN_MCP_MANIFEST, KNOWLEDGE_MCP_SLUG

    entry = BUILTIN_MCP_MANIFEST["mcpServers"][KNOWLEDGE_MCP_SLUG]
    assert "auth_mode" not in entry and "enabled" not in entry
    assert BUILTIN_MCP_DEFAULTS[KNOWLEDGE_MCP_SLUG] == {"auth_mode": "delegation", "enabled": True}

    frontmatter = (project_root() / "backend/yuxi/modules/extensions/plugins/builtin/knowledge/skills/knowledge-base/SKILL.md").read_text(encoding="utf-8")
    from yuxi.modules.extensions.plugins.registry import get_plugin

    assert "mcp_dependencies" not in frontmatter
    assert get_plugin("knowledge-base").mcps == (KNOWLEDGE_MCP_SLUG,)


def test_builtin_manifest_preserves_identity_when_deployment_disabled(tmp_path):
    """关闭部署只影响可用性，内置身份和默认管理员意图保持稳定。"""
    env = os.environ | {
        "YUXI_KNOWLEDGE_ENABLED": "false",
        "PYTHONPATH": str(project_root() / "backend"),
    }
    script = """
from yuxi.modules.extensions.mcp.builtin import BUILTIN_MCP_MANIFEST, KNOWLEDGE_MCP_SLUG, builtin_mcp_available
print(KNOWLEDGE_MCP_SLUG in BUILTIN_MCP_MANIFEST["mcpServers"], builtin_mcp_available(KNOWLEDGE_MCP_SLUG))
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines()[-1] == "True False"


@pytest.mark.parametrize("value, expected", [("true", True), ("false", False), (" FALSE ", False)])
def test_knowledge_switch_has_explicit_boolean_semantics(monkeypatch, value, expected):
    """配置边界只接受可解释的布尔开关。"""
    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", value)
    assert knowledge_enabled() is expected


@pytest.mark.parametrize("value", ["", "off", "typo", "0"])
def test_invalid_knowledge_switch_is_rejected(monkeypatch, value):
    """拼写错误不能静默启用或关闭知识库。"""
    monkeypatch.setenv("YUXI_KNOWLEDGE_ENABLED", value)
    with pytest.raises(ValueError, match="YUXI_KNOWLEDGE_ENABLED"):
        knowledge_enabled()


@pytest.mark.parametrize("filename", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_compose_defaults_to_full_knowledge_and_shared_mcp_identity(filename):
    """API 与 worker 默认共享部署模式和委托签验身份。"""
    services = load_compose(filename)["services"]
    for name in ("api", "worker", "app-init"):
        env = services[name]["environment"]
        assert env["YUXI_KNOWLEDGE_ENABLED"] == "${YUXI_KNOWLEDGE_ENABLED:-true}"
        assert "JWT_SECRET_KEY" in env
        assert "YUXI_INSTANCE_ID" in env
    for name in ("milvus", "etcd", "graph"):
        assert services[name].get("scale", 1) > 0
        assert not services[name].get("profiles")


def _closed_knowledge_violations(compose):
    """报告关闭配置中仍在运行的后端或应用能力。"""
    services = compose["services"]
    return {name for name in ("milvus", "etcd", "graph") if services[name].get("scale", 1) != 0} | {
        name for name in ("api", "worker", "app-init") if services[name].get("environment", {}).get("YUXI_KNOWLEDGE_ENABLED") != "false"
    }


def test_closed_compose_stops_backends_and_application_without_changing_storage():
    """关闭配置不重定义任何持久挂载。"""
    compose = load_compose("docker-compose.no-knowledge.yml")
    assert _closed_knowledge_violations(compose) == set()
    assert all("volumes" not in service for service in compose["services"].values())


@pytest.mark.parametrize("service", ["milvus", "etcd", "graph", "api", "worker", "app-init"])
def test_closed_compose_guard_rejects_partial_disable(service):
    """恢复任意单个能力时，关闭检查定位真实遗漏。"""
    compose = deepcopy(load_compose("docker-compose.no-knowledge.yml"))
    compose["services"][service] = {}
    assert _closed_knowledge_violations(compose) == {service}


def test_disabled_api_and_worker_expose_no_knowledge_runtime(tmp_path):
    """冷启动装配通过真实 ASGI 请求验证路由与协议均关闭。"""
    env = os.environ | {
        "YUXI_KNOWLEDGE_ENABLED": "false",
        "PYTHONPATH": str(project_root() / "backend"),
        "YUXI_RUNTIME_DIR": str(tmp_path / "runtime"),
    }
    script = """
import asyncio
import json
import httpx
from yuxi.api.main import app
from yuxi.workers.settings import WorkerSettings

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        statuses = {path: (await client.get(path)).status_code for path in (
            "/api/mcp", "/api/knowledge/knowledge-bases", "/api/graph/list",
            "/api/evaluation/knowledge-bases/example/datasets", "/api/v1/knowledge/tools/list_kbs"
        )}
        discovery = (await client.get("/api/system/discovery")).json()
        health = (await client.get("/api/system/health")).json()
    print(json.dumps({
        "statuses": statuses,
        "knowledge": discovery["capabilities"]["features"]["knowledge"],
        "health": health["status"],
        "mcp_mounted": hasattr(app.state, "knowledge_mcp"),
        "cron_names": [job.name for job in WorkerSettings.cron_jobs],
        "paths": list(app.openapi()["paths"]),
    }))
asyncio.run(main())
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert set(evidence["statuses"].values()) == {404}, evidence
    assert evidence["knowledge"] is False
    assert evidence["health"] == "ok"
    assert evidence["mcp_mounted"] is False
    assert not any("knowledge_projections" in name for name in evidence["cron_names"])
    assert not any(path.startswith(("/api/knowledge", "/api/graph", "/api/evaluation", "/api/v1/knowledge")) for path in evidence["paths"])
