"""内置定义发现与失败边界。"""

import sys
from importlib import invalidate_caches

import pytest

from yuxi.bootstrap.capabilities import personal_assistant_enabled
from yuxi.modules.agents import presets
from yuxi.modules.agents.runtime import agent_backends
from yuxi.modules.agents.runtime.agent_backends.chatbot.graph import ChatbotAgent
from yuxi.modules.agents.runtime.agent_backends.personal_assistant.graph import PersonalAssistantAgent
from yuxi.modules.extensions.skills import shared as skill_service


@pytest.fixture(autouse=True)
def clean_discovery_test_modules():
    """临时模块不能影响其他发现测试。"""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith(("yuxi.modules.agents.presets.test_", "yuxi.modules.agents.runtime.agent_backends.test_")):
            del sys.modules[name]


def test_preset_discovery_includes_shipping_roles():
    """发布角色完整，知识库问答启用并预加载内置技能。"""
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
    assert knowledge_qa.context["skills"] == ["knowledge-base"]
    assert knowledge_qa.context["preload_skills"] == ["knowledge-base"]


def test_new_preset_file_is_discovered_without_registry(tmp_path, monkeypatch):
    """新增真实模块即可发现，不需要编辑启动入口。"""
    monkeypatch.setattr(presets, "__file__", str(tmp_path / "__init__.py"))
    monkeypatch.setattr(presets, "__path__", [str(tmp_path)])
    (tmp_path / "test_new_role.py").write_text(
        "from yuxi.modules.agents.presets import AgentPreset\n"
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
            'from yuxi.modules.agents.presets import AgentPreset\nPRESET = AgentPreset(slug="duplicate", name="重复", description="测试")\n'
        )
    invalidate_caches()
    with pytest.raises(ValueError, match="重复的预置 Agent slug: duplicate"):
        presets.discover_agent_presets()


def test_explicit_backend_ids_create_independent_instances():
    """后端工厂不共享可变实例状态。"""
    expected = {"ChatbotAgent": ChatbotAgent}
    if personal_assistant_enabled():
        expected["PersonalAssistantAgent"] = PersonalAssistantAgent
    assert agent_backends.AGENT_BACKENDS == expected
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


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [("false", {"ChatbotAgent"}), ("true", {"ChatbotAgent", "PersonalAssistantAgent"})],
)
def test_backend_import_does_not_scan_or_create_instances(monkeypatch, enabled, expected):
    """隔离进程验证两种开关下的导入不扫描目录或创建后端。"""
    import subprocess

    monkeypatch.setenv("YUXI_PERSONAL_ASSISTANT_ENABLED", enabled)
    code = f"""
from importlib import reload
from unittest.mock import patch
import yuxi.modules.agents.runtime.agent_backends as agent_backends
with patch('pathlib.Path.iterdir', side_effect=AssertionError('directory scan')), \
     patch.object(agent_backends.ChatbotAgent, '__init__', side_effect=AssertionError('eager instance')), \
     patch.object(agent_backends.PersonalAssistantAgent, '__init__', side_effect=AssertionError('eager personal instance')):
    reload(agent_backends)
assert set(agent_backends.AGENT_BACKENDS) == {expected!r}
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_each_graph_uses_its_own_run_context(monkeypatch):
    """实际编译并执行两个图，后一个运行不能复用前一个模型或上下文。"""
    from importlib import import_module
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    backend = agent_backends.get_agent_backend("ChatbotAgent")
    module = import_module(type(backend).__module__)
    from yuxi.modules.agents.runtime import graph as runtime_graph

    monkeypatch.setattr(runtime_graph, "sync_agent_context_skills", AsyncMock())
    monkeypatch.setattr(runtime_graph, "resolve_configured_runtime_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(runtime_graph, "create_memory_middleware", AsyncMock(return_value=None))
    monkeypatch.setattr(runtime_graph, "create_agent_composite_backend", lambda _context: object())
    monkeypatch.setattr(runtime_graph, "resolve_chat_model_spec", lambda spec: spec)
    monkeypatch.setattr(module, "build_prompt_with_context", lambda context: context.thread_id)
    monkeypatch.setattr(
        runtime_graph,
        "load_chat_model",
        lambda fully_specified_name, session_id, uid, max_retries: FakeListChatModel(responses=[session_id, uid]),
    )
    monkeypatch.setattr(runtime_graph, "get_langgraph_checkpointer", lambda _manager: None)
    from langchain.agents import create_agent

    monkeypatch.setattr(runtime_graph, "create_summary_middleware_from_context", lambda *args, **kwargs: object())
    monkeypatch.setattr(runtime_graph, "create_agent", lambda **kwargs: create_agent(**(kwargs | {"middleware": [], "tools": []})))
    graphs = []
    for thread_id, uid in [("first-thread", "first-uid"), ("second-thread", "second-uid")]:
        context = SimpleNamespace(
            _runtime_prepared=True,
            workdir_relative_path="projects/test",
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
async def test_reused_prepared_context_refreshes_skills_before_each_graph(monkeypatch, tmp_path):
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

    backend = agent_backends.get_agent_backend("ChatbotAgent")
    module = import_module(type(backend).__module__)
    from yuxi.modules.agents.runtime import graph as runtime_graph

    monkeypatch.setattr(skill_runtime, "refresh_user_skill_projection_async", refresh_projection)
    monkeypatch.setattr(runtime_graph, "resolve_configured_runtime_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(runtime_graph, "create_memory_middleware", AsyncMock(return_value=None))
    monkeypatch.setattr(runtime_graph, "create_agent_composite_backend", lambda _context: object())
    monkeypatch.setattr(runtime_graph, "resolve_chat_model_spec", lambda spec: spec)
    monkeypatch.setattr(module, "build_prompt_with_context", lambda context: "test prompt")
    monkeypatch.setattr(
        runtime_graph,
        "load_chat_model",
        lambda **kwargs: FakeListChatModel(responses=[projection.read_text()]),
    )
    monkeypatch.setattr(runtime_graph, "get_langgraph_checkpointer", lambda _manager: None)
    from langchain.agents import create_agent

    monkeypatch.setattr(runtime_graph, "create_summary_middleware_from_context", lambda *args, **kwargs: object())
    monkeypatch.setattr(runtime_graph, "create_agent", lambda **kwargs: create_agent(**(kwargs | {"middleware": [], "tools": []})))
    context = SimpleNamespace(
        _runtime_prepared=True, workdir_relative_path="projects/test", model="test:model", thread_id="test-thread", uid="test-uid"
    )

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


def test_shipping_skills_keep_required_dependencies():
    """默认知识能力及研究依赖不能因发现重组缺失。"""
    found = {spec["slug"]: spec for spec in skill_service.list_builtin_skill_specs()}
    assert set(found) == {"image-gen", "deep-research", "knowledge-base", "mysql-reporter", "chat-widgets"}
    assert found["knowledge-base"]["tool_dependencies"] == []
    assert found["knowledge-base"]["mcp_dependencies"] == ["knowledge"]
    assert found["deep-research"]["skill_dependencies"] == []
    assert found["mysql-reporter"]["mcp_dependencies"] == []
    assert "html-preview" not in found


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_personal_backend_registration_follows_startup_flag(enabled):
    """部署开关只在新进程装配时决定助理 Backend 是否可运行。"""
    import os
    import subprocess

    source = (
        "from yuxi.modules.agents.runtime.agent_backends import AGENT_BACKENDS; "
        f"assert ('PersonalAssistantAgent' in AGENT_BACKENDS) == {enabled == 'true'}"
    )
    subprocess.run([sys.executable, "-c", source], env={**os.environ, "YUXI_PERSONAL_ASSISTANT_ENABLED": enabled}, check=True)
