"""远程 MCP 清单的正向与拒绝边界。"""

import pytest

from yuxi.modules.extensions.mcp.builtin import builtin_mcp_servers, supports_direct_mcp_selection
from yuxi.modules.extensions.mcp.config import normalize_mcp_manifest_entry
from yuxi.modules.extensions.mcp import runtime


def test_builtin_manifest_normalizes_deepwiki_connection_and_display():
    config = normalize_mcp_manifest_entry("deepwiki-official", builtin_mcp_servers()["deepwiki-official"].manifest)
    assert config["transport"] == "streamable_http"
    assert config["url"] == "https://mcp.deepwiki.com/mcp"
    assert config["name"] == "DeepWiki"
    assert not {"icon", "tags"} & config.keys()
    assert not {"command", "args", "env", "extra_data"} & config.keys()


@pytest.mark.parametrize(
    "entry,reason",
    [
        ({"type": "stdio", "url": "https://example.com/mcp"}, "transport"),
        ({"type": "http", "command": "sh"}, "连接字段"),
        ({"type": "http", "env": {}}, "连接字段"),
        ({"type": "http", "args": []}, "连接字段"),
        ({"type": "http", "transport": "sse"}, "不一致"),
        ({"type": "http", "url": "file:///tmp/mcp"}, "HTTP URL"),
        ({"type": "http", "url": "https://example.com:bad/mcp"}, "HTTP URL"),
        ({"type": "http", "url": "https://example.com:65536/mcp"}, "HTTP URL"),
        ({"type": "http", "url": "https://exa mple.com/mcp"}, "HTTP URL"),
        ({"type": "http", "url": "https://example.com/\u0000mcp"}, "HTTP URL"),
        ({"type": "http", "url": "https://example.com", "headers": {"X": 1}}, "headers"),
        ({"type": "http", "url": "https://example.com", "timeout": 0}, "timeout"),
        ({"type": "http", "url": "https://example.com", "timeout": True}, "timeout"),
        ({"type": "http", "extra_data": {"command": "sh"}}, "extra_data"),
        ({"type": "http", "extra_data": None}, "extra_data"),
        ({"type": "http", "extra_data": {"icon": "📚"}}, "extra_data"),
        ({"type": "http", "extra_data": {"tags": ["docs"]}}, "extra_data"),
    ],
)
def test_manifest_rejects_invalid_remote_entry(entry, reason):
    with pytest.raises(ValueError, match=reason):
        normalize_mcp_manifest_entry("example", entry)


@pytest.mark.parametrize("field,value", [("command", "sh"), ("args", []), ("env", {})])
async def test_remote_runtime_rejects_process_fields_before_client(monkeypatch, field, value):
    def unexpected_client(*_args, **_kwargs):
        pytest.fail("进程字段到达客户端")

    monkeypatch.setattr(runtime, "MultiServerMCPClient", unexpected_client)
    config = {"transport": "streamable_http", "url": "https://example.com/mcp", field: value}
    with pytest.raises(ValueError, match="进程字段"):
        await runtime.get_mcp_client({"example": config})
    with pytest.raises(ValueError, match="进程字段"):
        await runtime.get_mcp_tools("example", config)


def test_only_shared_mcp_can_be_selected_directly():
    """插件专属身份由注册声明派生，共享与用户定义的连接保留独立选择。"""
    assert not supports_direct_mcp_selection("knowledge")
    assert not supports_direct_mcp_selection("abridge")
    assert supports_direct_mcp_selection("deepwiki-official")
    assert supports_direct_mcp_selection("my-server")


def test_builtin_catalog_rejects_mcp_slug_with_two_owners(monkeypatch):
    """相同连接不能重复发布或把共享连接改成插件专属。"""
    from dataclasses import replace
    from yuxi.modules.extensions.plugins import registry

    class Duplicate(registry.AbridgePlugin):
        """模拟复用了同一标识的代码插件。"""

    monkeypatch.setattr(registry, "REGISTERED_PLUGINS", (registry.AbridgePlugin, Duplicate))
    with pytest.raises(ValueError, match="专属 MCP 标识重复：abridge"):
        builtin_mcp_servers()

    Duplicate.mcps = (replace(Duplicate.mcps[0], slug="deepwiki-official"),)
    with pytest.raises(ValueError, match="内置 MCP 标识重复：deepwiki-official"):
        builtin_mcp_servers()


def test_migrated_plugin_cannot_claim_another_plugins_mcp(tmp_path, monkeypatch):
    """部署 JSON 的共享引用不得贡献其他插件的专属工具。"""
    import json
    from yuxi.modules.extensions.plugins import registry

    monkeypatch.setattr(registry, "MIGRATED_PLUGIN_DIRECTORY", tmp_path)
    declaration = dict(
        plugin_id="example", name="Example", description="测试", entry_skill="example", skills=["example"], mcps=["knowledge"]
    )
    path = tmp_path / "example.json"
    try:
        path.write_text(json.dumps(declaration))
        registry.get_plugins.cache_clear()
        with pytest.raises(ValueError, match="example 不能引用专属 MCP：knowledge"):
            registry.get_plugins()

        declaration["mcps"] = ["deepwiki-official"]
        path.write_text(json.dumps(declaration))
        assert registry.get_plugin("example").mcp_slugs() == ("deepwiki-official",)
    finally:
        registry.get_plugins.cache_clear()


@pytest.mark.parametrize("slug", ["deepwiki-official", "user-server"])
async def test_management_rejects_delegation_for_mcp_without_a_factory(slug):
    """持久连接上的 delegation 字段不会把凭据交给未声明委托的服务。"""
    from yuxi.modules.extensions.mcp.models import MCPServer
    from yuxi.modules.extensions.mcp.service import inspect_mcp_server_tools

    server = MCPServer(
        slug=slug, name=slug, transport="streamable_http", url="https://example.com/mcp", auth_mode="delegation", code_owned=slug == "deepwiki-official"
    )
    with pytest.raises(ValueError, match="未提供此内部 MCP 服务的认证模块"):
        await inspect_mcp_server_tools(server, uid="reader")


@pytest.mark.parametrize("mcps, reason", [(["remote"], "必须是元组"), ((" ",), "无效标识")])
def test_plugin_registry_rejects_invalid_mcp_references(mcps, reason):
    """部署时拒绝错误声明，避免把字符串或空白值当作服务列表。"""
    from yuxi.modules.extensions.plugins.builtin.knowledge.plugin import KnowledgePlugin
    from yuxi.modules.extensions.plugins.registry import validate_plugin

    candidate = type("InvalidPlugin", (KnowledgePlugin,), {"mcps": mcps})
    with pytest.raises(ValueError, match=reason):
        validate_plugin(candidate)


async def test_management_refuses_user_row_claiming_builtin_delegation(monkeypatch):
    """直接传入 ORM 对象也必须证明代码来源，拒绝对不匹配的连接签发委托。"""
    from yuxi.modules.extensions.mcp.models import MCPServer
    from yuxi.modules.extensions.mcp.service import inspect_mcp_server_tools

    monkeypatch.setattr("yuxi.modules.extensions.mcp.service.load_mcp_tools", lambda *args, **kwargs: pytest.fail("来源错误仍尝试连接"))
    row = MCPServer(slug="knowledge", code_owned=False, auth_mode="delegation", transport="streamable_http", url="https://example.com/mcp")
    with pytest.raises(ValueError, match="当前部署不可用"):
        await inspect_mcp_server_tools(row, uid="reader")
