"""会话连接器选择保留原配置的公开语义。"""

import pytest
from pydantic import ValidationError

from yuxi.api.routers.public_v1.agents.schemas import InputAttachmentOptions, MessageEvent, SessionConnectorOptions, SessionCreate
from yuxi.modules.agents.services.input_config import merge_mcp_selections


@pytest.mark.parametrize(
    ("selections", "expected"),
    [
        ((["context"], ["extra", "context", "extra"]), ["context", "extra"]),
        (([], ["extra"], ["next", "extra"]), ["extra", "next"]),
        (("all", ["extra"]), "all"),
        ((["extra"], "all"), "all"),
        (([], []), []),
    ],
)
def test_mcp_union_keeps_existing_selection_and_dynamic_all(selections, expected):
    """显式追加不覆盖既有选择，也不把 all 固化为当前目录。"""
    assert merge_mcp_selections(*selections) == expected


@pytest.mark.parametrize("create", [False, True])
def test_public_inputs_support_only_explicit_connector_slug_lists(create):
    """创建和后续消息接受同一选择协议，非法类型在 wire 边界拒绝。"""
    model = SessionCreate if create else MessageEvent
    base = (
        {"agent_id": "agent", "input": "hello"}
        if create
        else {
            "type": "agent.session.input.message",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        }
    )
    assert model.model_validate(base).yuxi.mcps == []
    assert model.model_validate({**base, "yuxi": {"mcps": [" extra "]}}).yuxi.mcps == ["extra"]
    for invalid in (None, "all", [1], [""], [" "], ["a" * 101]):
        with pytest.raises(ValidationError):
            model.model_validate({**base, "yuxi": {"mcps": invalid}})


def test_session_connector_and_input_attachment_options_reject_cross_scope_fields():
    """附件选项不能接受会话配置，连接器选项不能接受本次附件。"""
    assert InputAttachmentOptions(attachment_file_ids=["a" * 32]).attachment_file_ids == ["a" * 32]
    assert SessionConnectorOptions(mcps=["extra"]).mcps == ["extra"]
    with pytest.raises(ValidationError) as attachment_error:
        InputAttachmentOptions(mcps=["extra"])
    assert attachment_error.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError) as connector_error:
        SessionConnectorOptions(attachment_file_ids=["a" * 32])
    assert connector_error.value.errors()[0]["type"] == "extra_forbidden"
