"""内置定义发现与失败边界。"""

from importlib import invalidate_caches
import sys

import pytest

from yuxi.modules.agents.runtime import agent_backends
from yuxi.modules.agents import presets
from yuxi.modules.agents.runtime.agent_backends.chatbot.graph import ChatbotAgent
from yuxi.modules.extensions.skills.builtin import sync as skill_service


@pytest.fixture(autouse=True)
def clean_discovery_test_modules():
    """临时模块不能影响其他发现测试。"""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith(("yuxi.modules.agents.presets.test_", "yuxi.modules.agents.runtime.agent_backends.test_")):
            del sys.modules[name]


def test_preset_discovery_includes_shipping_roles():
    """发布角色完整，知识库使用插件，研究使用普通 Skill。"""
    found = {preset.slug: preset for preset in presets.discover_agent_presets()}
    assert list(found) == [
        "default-chatbot",
        "deep-research",
        "knowledge-base-qa",
    ]
    assert found["default-chatbot"].backend_id == "ChatbotAgent"
    knowledge_qa = found["knowledge-base-qa"]
    assert knowledge_qa.name == "知识库问答"
    assert knowledge_qa.backend_id == "ChatbotAgent"
    assert knowledge_qa.context["skills"] == []
    assert knowledge_qa.context["plugins"] == ["knowledge-base"]
    research = found["deep-research"]
    assert research.context["skills"] == ["deep-research"]
    assert "plugins" not in research.context
    assert "load_plugin" not in research.context["system_prompt"]


def test_new_preset_file_is_discovered_without_registry(tmp_path, monkeypatch):
    """新增真实模块即可发现，不需要编辑启动入口。"""
    monkeypatch.setattr(presets, "__file__", str(tmp_path / "__init__.py"))
    monkeypatch.setattr(presets, "__path__", [str(tmp_path)])
    (tmp_path / "test_new_role.py").write_text(
        'from yuxi.modules.agents.presets import AgentPreset\nPRESET = AgentPreset(slug="new-role", name="新增角色", description="测试发现")\n'
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
            'from yuxi.modules.agents.presets import AgentPreset\nPRESET = AgentPreset(slug="duplicate", name="重复", description="测试")\n'
        )
    invalidate_caches()
    with pytest.raises(ValueError, match="重复的预置 Agent slug: duplicate"):
        presets.discover_agent_presets()


def test_explicit_backend_ids_create_independent_instances():
    """后端工厂不共享可变实例状态。"""
    assert agent_backends.AGENT_BACKENDS == {
        "ChatbotAgent": ChatbotAgent,
    }
    for backend_id, expected_type in agent_backends.AGENT_BACKENDS.items():
        first = agent_backends.get_agent_backend(backend_id)
        second = agent_backends.get_agent_backend(backend_id)
        assert type(first) is expected_type and type(second) is expected_type
        assert first is not second
        first.test_state = "first-run"
        assert not hasattr(second, "test_state")


def test_unregistered_backend_is_unavailable():
    """未知后端使用明确领域错误，供调用入口映射响应。"""
    with pytest.raises(agent_backends.AgentBackendNotFoundError, match="UnregisteredBackend"):
        agent_backends.get_agent_backend("UnregisteredBackend")


@pytest.mark.asyncio
async def test_backend_info_uses_registry_id_after_class_rename(monkeypatch):
    """类名改变不能影响公开的后端 ID。"""

    class RenamedBackend(ChatbotAgent):
        """使用不同 Python 类名模拟内部重命名。"""

    monkeypatch.setattr(agent_backends, "AGENT_BACKENDS", {"stable-backend-id": RenamedBackend})
    infos = await agent_backends.list_agent_backend_info()
    assert len(infos) == 1
    assert infos[0]["backend_id"] == "stable-backend-id"
    assert "id" not in infos[0]
    assert infos[0]["name"] == "智能助手"
    assert "capabilities" not in infos[0]


def test_backend_import_does_not_scan_or_create_instances():
    """隔离进程中验证导入只声明能力，不扫描目录或创建后端。"""
    import subprocess

    code = """
from importlib import reload
from unittest.mock import patch
import yuxi.modules.agents.runtime.agent_backends as agent_backends
with patch('pathlib.Path.iterdir', side_effect=AssertionError('directory scan')), \
     patch.object(agent_backends.ChatbotAgent, '__init__', side_effect=AssertionError('eager instance')):
    reload(agent_backends)
assert set(agent_backends.AGENT_BACKENDS) == {'ChatbotAgent'}
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_id", ["ChatbotAgent"])
async def test_each_graph_uses_its_own_run_context(monkeypatch, backend_id):
    """实际编译并执行两个图，后一个运行不能复用前一个模型或上下文。"""
    from importlib import import_module
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    backend = agent_backends.get_agent_backend(backend_id)
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
        lambda fully_specified_name, session_id, uid, max_retries: FakeListChatModel(responses=[session_id, uid]),
    )
    monkeypatch.setattr(module, "get_langgraph_checkpointer", lambda _manager: None)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_id", ["ChatbotAgent"])
async def test_reused_prepared_context_refreshes_skills_before_each_graph(monkeypatch, tmp_path, backend_id):
    """准备后的 Context 复用时，每次构图仍发布最新 Skill 投影。"""
    from importlib import import_module
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from yuxi.modules.extensions.skills import runtime as skill_runtime

    source = tmp_path / "source.md"
    projection = tmp_path / "projection.md"

    async def refresh_projection(uid):
        """用真实文件模拟已有 Skill 发布入口。"""
        assert uid == "test-uid"
        projection.write_text(source.read_text())

    backend = agent_backends.get_agent_backend(backend_id)
    module = import_module(type(backend).__module__)
    monkeypatch.setattr(skill_runtime, "refresh_user_skill_projection_async", refresh_projection)
    monkeypatch.setattr(module, "resolve_configured_runtime_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "_build_middlewares", AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "create_agent_composite_backend", lambda _context: object())
    monkeypatch.setattr(module, "resolve_chat_model_spec", lambda spec: spec)
    monkeypatch.setattr(module, "build_prompt_with_context", lambda context: "test prompt")
    monkeypatch.setattr(
        module,
        "load_chat_model",
        lambda **kwargs: FakeListChatModel(responses=[projection.read_text()]),
    )
    monkeypatch.setattr(module, "get_langgraph_checkpointer", lambda _manager: None)
    context = SimpleNamespace(_runtime_prepared=True, model="test:model", thread_id="test-thread", uid="test-uid")

    for content in ["first skill revision", "second skill revision"]:
        source.write_text(content)
        graph = await backend.get_graph(context=context)
        result = await graph.ainvoke({"messages": [("user", "hello")]})
        assert projection.read_text() == content
        assert result["messages"][-1].content == content

    async def fail_projection(uid):
        """模拟投影发布失败。"""
        raise RuntimeError("skill projection failed")

    monkeypatch.setattr(skill_runtime, "refresh_user_skill_projection_async", fail_projection)
    with pytest.raises(RuntimeError, match="skill projection failed"):
        await backend.get_graph(context=context)


def test_shipping_plugins_own_runtime_dependencies():
    """内置能力由插件声明，Skill 只保留说明。"""
    from yuxi.modules.extensions.plugins.registry import get_plugin, get_plugins

    found = {spec["slug"]: spec for spec in skill_service.list_builtin_skill_specs()}
    assert set(found) == {"image-gen", "deep-research", "knowledge-base", "mysql-reporter", "chat-widgets", "abridge"}
    for spec in found.values():
        assert not {"tool_dependencies", "mcp_dependencies", "skill_dependencies"}.intersection(spec)
    assert get_plugin("knowledge-base").mcp_slugs() == ("knowledge",)
    assert "deep-research" not in {plugin.plugin_id for plugin in get_plugins()}
    assert "image-gen" not in {plugin.plugin_id for plugin in get_plugins()}
    assert get_plugin("abridge").skills == ("abridge", "chat-widgets")
    assert get_plugin("abridge").mcp_slugs() == ("abridge",)


def test_plugin_skills_are_discovered_without_runtime_assembly(monkeypatch):
    """源码资源来自插件目录，发现过程不装配运行时或迁移插件。"""
    from pathlib import Path
    from yuxi.modules.extensions.plugins import registry
    from yuxi.modules.extensions.skills.builtin import BUILTIN_SKILLS_DIR

    def reject_runtime_assembly(*args):
        """资源发现不应依赖运行工具和中间件。"""
        raise AssertionError("runtime assembly")

    monkeypatch.setattr(registry, "get_plugins", reject_runtime_assembly)
    monkeypatch.setattr(registry, "validate_plugin", reject_runtime_assembly)
    found = {spec["slug"]: spec for spec in skill_service.list_builtin_skill_specs()}
    root = Path(registry.__file__).parent
    assert found["knowledge-base"]["source_dir"] == root / "knowledge/skills/knowledge-base"
    assert found["image-gen"]["source_dir"] == BUILTIN_SKILLS_DIR / "image-gen"
    assert found["abridge"]["source_dir"] == root / "abridge/skills/abridge"
    assert found["chat-widgets"]["source_dir"] == BUILTIN_SKILLS_DIR / "chat-widgets"


@pytest.mark.asyncio
async def test_selected_plugins_assemble_independent_middlewares_in_registry_order(monkeypatch):
    """用户选择顺序不改变部署顺序，每次构图创建各插件独立实例。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from yuxi.modules.agents.runtime.agent_backends.chatbot import graph
    from yuxi.modules.agents.runtime.middlewares.plugins import PluginLoaderMiddleware, PluginMiddleware
    from yuxi.modules.extensions.plugins.abridge.plugin import AbridgePlugin
    from yuxi.modules.extensions.plugins.knowledge.plugin import KnowledgePlugin

    monkeypatch.setattr(graph, "create_agent_filesystem_middleware", lambda *args, **kwargs: object())
    monkeypatch.setattr(graph, "create_summary_middleware_from_context", lambda *args, **kwargs: object())
    monkeypatch.setattr(graph, "create_memory_middleware", AsyncMock(return_value=None))
    monkeypatch.setattr(graph, "create_cooperation_middleware", lambda context: None)
    context = SimpleNamespace(plugins=["abridge", "knowledge-base"], workdir_relative_path="projects/test")
    first = await graph._build_middlewares(context, object())
    second = await graph._build_middlewares(context, object())
    first_plugins = [item for item in first if isinstance(item, PluginMiddleware)]
    second_plugins = [item for item in second if isinstance(item, PluginMiddleware)]
    assert [type(item.plugin) for item in first_plugins] == [KnowledgePlugin, AbridgePlugin]
    assert all(left is not right and left.plugin is not right.plugin for left, right in zip(first_plugins, second_plugins, strict=True))
    assert sum(isinstance(item, PluginLoaderMiddleware) for item in first) == 1
    context.plugins = ["abridge"]
    selected = await graph._build_middlewares(context, object())
    assert [type(item.plugin) for item in selected if isinstance(item, PluginMiddleware)] == [AbridgePlugin]


def test_plugin_resource_discovery_rejects_sibling_skill(tmp_path, monkeypatch):
    """即使插件声明了第二个顶层 Skill 也不能独立发布。"""
    from yuxi.modules.extensions.plugins import registry

    (tmp_path / "skills" / "private-helper").mkdir(parents=True)
    monkeypatch.setattr(registry.KnowledgePlugin, "skills", ("knowledge-base", "private-helper"))
    monkeypatch.setattr(registry, "REGISTERED_PLUGINS", (registry.KnowledgePlugin,))
    monkeypatch.setattr(registry.inspect, "getfile", lambda plugin: str(tmp_path / "plugin.py"))
    with pytest.raises(ValueError, match="私有 Skill private-helper 须放在入口包 knowledge-base 内"):
        registry.get_plugin_skill_directories()


def test_plugin_cannot_use_another_entry_as_shared_skill(monkeypatch, tmp_path):
    """共享引用不能披露另一个业务插件的私有入口。"""
    from yuxi.modules.extensions.plugins import registry

    monkeypatch.setattr(registry, "MIGRATED_PLUGIN_DIRECTORY", tmp_path)
    monkeypatch.setattr(registry.KnowledgePlugin, "skills", ("knowledge-base", "abridge"))
    registry.get_plugins.cache_clear()
    try:
        with pytest.raises(ValueError, match="knowledge-base 不能引用其他代码插件的入口 Skill：abridge"):
            registry.get_plugins()
    finally:
        registry.get_plugins.cache_clear()
