"""会话快照保存声明配置与默认值，不保存执行身份或展开权限。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
from yuxi.modules.agents.services.input_config import resolve_agent_context_snapshot
from yuxi.modules.models.providers.cache import model_cache
from yuxi.shared.errors import DomainError, InvalidInputError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("requested_mode, expected_mode", [(None, "default"), ("default", "default"), ("always_trust", "always_trust")])
async def test_snapshot_keeps_defaults_and_resource_intent_without_runtime_identity(monkeypatch, requested_mode, expected_mode):
    """旧智能体审批值与伪造身份被过滤，快照保存会话选择及独立资源列表。"""
    monkeypatch.setattr(
        model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="chat"),
    )
    config = {
        "model": "test:model",
        "tools": ["read_file"],
        "skills": "all",
        "uid": "forged",
        "run_id": "forged",
        "tool_approval_mode": "always_trust",
    }
    snapshot = await resolve_agent_context_snapshot(
        None, requested_mode, SimpleNamespace(config_json={"context": config}), SimpleNamespace(context_schema=ChatBotContext)
    )
    assert snapshot["max_execution_steps"] == ChatBotContext().max_execution_steps
    assert snapshot["summary_prompt"] == ChatBotContext().summary_prompt
    assert snapshot["model"] == "test:model"
    assert snapshot["skills"] == "all"
    assert snapshot["tool_approval_mode"] == expected_mode
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
            SimpleNamespace(config_json={"context": {"model": "missing:model"}}),
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
