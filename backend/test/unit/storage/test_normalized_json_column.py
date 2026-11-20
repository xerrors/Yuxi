"""持久化边界的 NUL 归一化：保证裸 json 列落库后 PostgreSQL 仍可读取。"""

import json

from yuxi.infrastructure.postgres.base import NormalizedJSON, normalize_json_nul
from yuxi.modules.agents.models.messages import Message


def test_normalize_json_nul_replaces_nul_in_nested_strings_and_keys():
    """字符串值与键中的 U+0000 均替换为 U+FFFD，非容器类型原样返回。"""
    value = {
        "output": "before\x00after",
        "key\x00name": {"nested": ["a\x00b", 1, None, True]},
        "count": 3,
    }

    normalized = normalize_json_nul(value)

    assert normalized["output"] == "before\ufffdafter"
    assert normalized["key\ufffdname"]["nested"] == ["a\ufffdb", 1, None, True]
    assert normalized["count"] == 3


def test_normalize_json_nul_keeps_both_values_on_key_collision():
    """替换键与既有键重名时不覆盖：无 NUL 原键保留原名原值，替换键追加唯一后缀。"""
    normalized = normalize_json_nul({"a\x00b": 1, "a\ufffdb": 2})

    assert normalized == {"a\ufffdb": 2, "a\ufffdb\ufffd": 1}

    three = normalize_json_nul({"x\x00": 1, "x\x00\x00": 2, "x\ufffd": 3})
    assert three["x\ufffd"] == 3
    assert len(three) == 3
    assert sorted(three.values()) == [1, 2, 3]


def test_normalized_json_bind_param_serializes_without_unreadable_escape():
    """绑定参数经归一化后序列化不再产生 PostgreSQL 无法回读的 \\u0000 转义。"""
    column_type = NormalizedJSON()

    bound = column_type.process_bind_param({"text": "a\x00b"}, None)
    dumped = json.dumps(bound)
    assert column_type.process_bind_param(None, None) is None

    assert "\\u0000" not in dumped
    assert "\\ufffd" in dumped


def test_message_extra_metadata_uses_normalized_json():
    """模型/工具 JSON 全文列必须挂在归一化写边界上，防止 items 分页被污染行拖垮。"""
    column_type = Message.__table__.columns["extra_metadata"].type

    assert isinstance(column_type, NormalizedJSON)
