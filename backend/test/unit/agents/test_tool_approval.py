import pytest

from yuxi.agents.tool_approval import (
    TOOL_APPROVAL_INTERRUPT_ON,
    create_tool_approval_middleware,
    normalize_tool_approval_mode,
)


def test_default_mode_builds_sensitive_tool_approval_middleware():
    middleware = create_tool_approval_middleware("default")

    assert middleware.interrupt_on == TOOL_APPROVAL_INTERRUPT_ON
    assert all(
        config["allowed_decisions"] == ["approve", "reject"]
        for config in middleware.interrupt_on.values()
    )


def test_always_trust_mode_does_not_build_approval_middleware():
    assert create_tool_approval_middleware("always_trust") is None


def test_unknown_tool_approval_mode_is_rejected():
    with pytest.raises(ValueError, match="不支持的 tool_approval_mode"):
        normalize_tool_approval_mode("unknown")
