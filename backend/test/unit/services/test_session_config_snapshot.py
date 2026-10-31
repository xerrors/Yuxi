"""会话快照保存声明配置与默认值，不保存执行身份或展开权限。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
from yuxi.modules.agents.services.input_config import resolve_agent_context_snapshot
from yuxi.modules.models.providers.cache import model_cache
from yuxi.shared.errors import DomainError, InvalidInputError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_snapshot_keeps_defaults_and_resource_intent_without_runtime_identity(monkeypatch):
    """配置里伪造的身份被过滤，列表不会与定义共享可变对象。"""
    monkeypatch.setattr(
        model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="chat"),
    )
    config = {"model": "test:model", "tools": ["read_file"], "skills": "all", "uid": "forged", "run_id": "forged"}
    snapshot = await resolve_agent_context_snapshot(
        None,
        None,
        SimpleNamespace(backend_id="ChatbotAgent", config_json={"context": config}),
        SimpleNamespace(context_schema=ChatBotContext),
    )
    assert snapshot["max_execution_steps"] == ChatBotContext().max_execution_steps
    assert snapshot["summary_prompt"] == ChatBotContext().summary_prompt
    assert snapshot["model"] == "test:model"
    assert snapshot["skills"] == "all"
    assert not {"uid", "thread_id", "run_id", "worker_id", "workdir_path"} & snapshot.keys()
    config["tools"].append("write_file")
    assert snapshot["tools"] == ["read_file"]


async def test_snapshot_requires_resolvable_chat_model(monkeypatch):
    """空会话也在创建边界拒绝不存在的模型。"""
    monkeypatch.setattr(model_cache, "get_model_info", lambda spec: None)
    with pytest.raises(DomainError) as error:
        await resolve_agent_context_snapshot(
            None,
            None,
            SimpleNamespace(backend_id="ChatbotAgent", config_json={"context": {"model": "missing:model"}}),
            SimpleNamespace(context_schema=ChatBotContext),
        )
    assert isinstance(error.value, InvalidInputError)
    assert error.value.code == "chat_model_not_found"


async def test_explicit_blank_model_cannot_reset_to_configured_or_system_default():
    """显式空白模型是无效请求，不能复用 Agent 留空选择默认的语义。"""
    from yuxi.modules.agents.services.input_config import resolve_agent_run_model_spec

    with pytest.raises(DomainError) as error:
        await resolve_agent_run_model_spec("   ", "valid:model")
    assert isinstance(error.value, InvalidInputError)
    assert error.value.message == "显式模型标识不能为空"


@pytest.mark.parametrize(
    ("requested", "configured", "expected"),
    [(None, None, "personal:default"), (None, "agent:model", "agent:model"), ("input:model", "agent:model", "input:model")],
)
async def test_personal_model_default_uses_shared_snapshot_path(monkeypatch, requested, configured, expected):
    """所有创建入口共享专用默认值，显式输入和 Agent 配置优先，普通默认无效不影响助理。"""
    from unittest.mock import AsyncMock

    from yuxi.modules.agents.runtime.agent_backends.personal_assistant.graph import PersonalAssistantAgent

    monkeypatch.setattr(
        "yuxi.modules.agents.services.input_config.system_options",
        SimpleNamespace(get=AsyncMock(return_value={"personal_assistant_model": "personal:default", "default_model": "missing:model"})),
    )
    monkeypatch.setattr(model_cache, "get_model_info", lambda spec: SimpleNamespace(model_type="chat") if spec == expected else None)
    agent = SimpleNamespace(name="我的助理", backend_id="PersonalAssistantAgent", config_json={"context": {"model": configured}})
    snapshot = await resolve_agent_context_snapshot(requested, "always_trust", agent, PersonalAssistantAgent)
    assert snapshot["model"] == expected
    assert snapshot["tool_approval_mode"] == "always_trust"
    assert snapshot["personal_assistant_name"] == "我的助理"
