"""内置定义发现与失败边界。"""

from importlib import invalidate_caches
import sys

import pytest

from yuxi.agents import buildin, presets
from yuxi.agents.buildin.chatbot.graph import ChatbotAgent
from yuxi.agents.buildin.subagent.graph import SubAgentBackend
from yuxi.agents.skills import service as skill_service


@pytest.fixture(autouse=True)
def clean_discovery_test_modules():
    """临时模块不能影响其他发现测试。"""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith(("yuxi.agents.presets.test_", "yuxi.agents.buildin.test_")):
            del sys.modules[name]


def test_preset_discovery_includes_shipping_roles():
    """发布内容完整，深度研究保持原有委派关系。"""
    found = {preset.slug: preset for preset in presets.discover_agent_presets()}
    assert set(found) == {
        "default-chatbot",
        "general-purpose",
        "web-search",
        "deep-research",
        "research-explorer",
        "fact-verifier",
    }
    assert found["deep-research"].context["subagents"] == ["research-explorer", "fact-verifier"]
    assert found["general-purpose"].context == {}
    assert found["default-chatbot"].backend_id == "ChatbotAgent"
    assert found["fact-verifier"].backend_id == "SubAgentBackend"


def test_new_preset_file_is_discovered_without_registry(tmp_path, monkeypatch):
    """新增真实模块即可发现，不需要编辑启动入口。"""
    monkeypatch.setattr(presets, "__file__", str(tmp_path / "__init__.py"))
    monkeypatch.setattr(presets, "__path__", [str(tmp_path)])
    (tmp_path / "test_new_role.py").write_text(
        "from yuxi.agents.presets import AgentPreset\n"
        'PRESET = AgentPreset(slug="new-role", name="新增角色", description="测试发现")\n'
    )
    invalidate_caches()
    found = presets.discover_agent_presets()
    assert [(item.slug, item.name) for item in found] == [("new-role", "新增角色")]


@pytest.mark.parametrize(
    ("source", "error", "message"),
    [
        ("PRESET = None\n", TypeError, "必须是 AgentPreset"),
        ('raise RuntimeError("invalid definition")\n', RuntimeError, "invalid definition"),
        ("OTHER = 1\n", AttributeError, "PRESET"),
    ],
)
def test_invalid_preset_is_not_silently_skipped(tmp_path, monkeypatch, source, error, message):
    """无效定义必须阻止发现成功。"""
    monkeypatch.setattr(presets, "__file__", str(tmp_path / "__init__.py"))
    monkeypatch.setattr(presets, "__path__", [str(tmp_path)])
    (tmp_path / "test_invalid_role.py").write_text(source)
    invalidate_caches()
    with pytest.raises(error, match=message):
        presets.discover_agent_presets()


def test_duplicate_preset_slug_is_rejected(tmp_path, monkeypatch):
    """相同 slug 不能由后加载的角色覆盖。"""
    monkeypatch.setattr(presets, "__file__", str(tmp_path / "__init__.py"))
    monkeypatch.setattr(presets, "__path__", [str(tmp_path)])
    for name in ("test_duplicate_a", "test_duplicate_b"):
        (tmp_path / f"{name}.py").write_text(
            "from yuxi.agents.presets import AgentPreset\n"
            'PRESET = AgentPreset(slug="duplicate", name="重复", description="测试")\n'
        )
    invalidate_caches()
    with pytest.raises(ValueError, match="重复的预置 Agent slug: duplicate"):
        presets.discover_agent_presets()


def test_explicit_backend_ids_create_independent_instances():
    """后端工厂不共享可变实例状态。"""
    assert buildin.BUILTIN_BACKENDS == {
        "ChatbotAgent": ChatbotAgent,
        "SubAgentBackend": SubAgentBackend,
    }
    for backend_id, expected_type in buildin.BUILTIN_BACKENDS.items():
        first = buildin.get_agent_backend(backend_id)
        second = buildin.get_agent_backend(backend_id)
        assert type(first) is expected_type and type(second) is expected_type
        assert first is not second
        first.test_state = "first-run"
        assert not hasattr(second, "test_state")


def test_unregistered_backend_is_unavailable():
    """未知后端使用明确领域错误，供调用入口映射响应。"""
    with pytest.raises(buildin.AgentBackendNotFoundError, match="UnregisteredBackend"):
        buildin.get_agent_backend("UnregisteredBackend")


@pytest.mark.asyncio
async def test_backend_info_uses_registry_id_after_class_rename(monkeypatch):
    """类名改变不能影响公开的后端 ID。"""

    class RenamedBackend(ChatbotAgent):
        """使用不同 Python 类名模拟内部重命名。"""

    monkeypatch.setattr(buildin, "BUILTIN_BACKENDS", {"stable-backend-id": RenamedBackend})
    infos = await buildin.list_agent_backend_info()
    assert len(infos) == 1
    assert infos[0]["backend_id"] == "stable-backend-id"
    assert "id" not in infos[0]
    assert infos[0]["name"] == "智能助手"


def test_backend_import_does_not_scan_or_create_instances():
    """隔离进程中验证导入只声明能力，不扫描目录或创建后端。"""
    import subprocess

    code = """
from importlib import reload
from unittest.mock import patch
from yuxi.agents import buildin
with patch('pathlib.Path.iterdir', side_effect=AssertionError('directory scan')), \
     patch.object(buildin.ChatbotAgent, '__init__', side_effect=AssertionError('eager instance')):
    reload(buildin)
assert set(buildin.BUILTIN_BACKENDS) == {'ChatbotAgent', 'SubAgentBackend'}
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_id", ["ChatbotAgent", "SubAgentBackend"])
async def test_each_graph_uses_its_own_run_context(monkeypatch, backend_id):
    """实际编译并执行两个图，后一个运行不能复用前一个模型或上下文。"""
    from importlib import import_module
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    backend = buildin.get_agent_backend(backend_id)
    module = import_module(type(backend).__module__)
    monkeypatch.setattr(module, "sync_agent_context_skills", AsyncMock())
    monkeypatch.setattr(module, "resolve_configured_runtime_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "_build_middlewares", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "create_agent_composite_backend", lambda _context: object())
    monkeypatch.setattr(module, "resolve_chat_model_spec", lambda spec: spec)
    monkeypatch.setattr(module, "build_prompt_with_context", lambda context: context.thread_id)
    monkeypatch.setattr(
        module,
        "load_chat_model",
        lambda fully_specified_name, session_id, uid: FakeListChatModel(responses=[session_id, uid]),
    )
    monkeypatch.setattr(backend, "_get_checkpointer", AsyncMock(return_value=None))
    graphs = []
    for thread_id, uid in [("first-thread", "first-uid"), ("second-thread", "second-uid")]:
        context = SimpleNamespace(
            _runtime_prepared=True,
            model="test:model",
            thread_id=thread_id,
            uid=uid,
        )
        graph = await backend.get_graph(context=context)
        result = await graph.ainvoke({"messages": [("user", "hello")]})
        assert result["messages"][-1].content == thread_id
        graphs.append(graph)
    assert graphs[0] is not graphs[1]


def test_shipping_skills_keep_required_dependencies():
    """默认知识能力及研究依赖不能因发现重组缺失。"""
    found = {spec["slug"]: spec for spec in skill_service.list_builtin_skill_specs()}
    assert set(found) == {"image-gen", "html-preview", "deep-research", "knowledge-base", "mysql-reporter"}
    assert found["knowledge-base"]["tool_dependencies"] == [
        "list_kbs",
        "query_kb",
        "find_kb_document",
        "open_kb_document",
        "get_mindmap",
        "search_file",
        "download_kb_file",
    ]
    assert found["deep-research"]["skill_dependencies"] == []
    assert found["mysql-reporter"]["mcp_dependencies"] == []
    assert found["html-preview"]["version"] == "2026.07.23"
