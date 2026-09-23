"""确定性模型替身的协议拒绝条件。"""

import pytest

from test.support.openai_replay_server import validate_request


@pytest.mark.parametrize(
    ("authorization", "change", "expected_error"),
    [
        (None, {}, "invalid_authorization"),
        ("Bearer ci-replay-key", {"model": "other-model"}, "invalid_model"),
        ("Bearer ci-replay-key", {"stream": False}, "stream_required"),
        ("Bearer ci-replay-key", {"messages": [{"role": "user", "content": "wrong"}]}, "expected_input_missing"),
        (
            "Bearer ci-replay-key",
            {"messages": [{"role": "user", "content": "DETERMINISTIC_AGENT_E2E_OK"}]},
            "preloaded_skill_missing",
        ),
        ("Bearer ci-replay-key", {"tools": []}, "preloaded_tool_missing"),
    ],
)
def test_replay_rejects_invalid_model_contract(authorization, change, expected_error):
    """模型替身必须拒绝未经过预期适配层的请求。"""
    body = {
        "model": "deterministic-chat",
        "stream": True,
        "messages": [
            {"role": "system", "content": "# 图片生成技能"},
            {"role": "user", "content": "DETERMINISTIC_AGENT_E2E_OK"},
        ],
        "tools": [{"type": "function", "function": {"name": "present_artifacts"}}],
    }
    assert validate_request(authorization, {**body, **change}) == expected_error


def test_replay_rejects_unexpected_tool_result():
    """工具结果与 replay 剧本不一致时拒绝继续。"""
    body = {
        "model": "deterministic-chat",
        "stream": True,
        "messages": [
            {"role": "system", "content": "# 图片生成技能"},
            {"role": "user", "content": "DETERMINISTIC_AGENT_E2E_OK"},
            {"role": "tool", "tool_call_id": "call-preloaded-tool", "content": "unexpected result"},
        ],
        "tools": [{"type": "function", "function": {"name": "present_artifacts"}}],
    }
    assert validate_request("Bearer ci-replay-key", body) == "tool_execution_result_missing"
