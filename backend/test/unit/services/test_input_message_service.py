"""聊天输入（文本 + 图片列表）的构造、归一与历史投影。"""

from __future__ import annotations

import pytest

import yuxi.services.input_message_service as input_message_service
from yuxi.services.input_message_service import (
    MAX_CHAT_IMAGES,
    build_chat_input_message,
    extract_image_contents,
    normalize_image_contents,
    restore_chat_input_message,
)


def _image_url_parts(message) -> list[dict]:
    raw = message.raw_message()
    return [part for part in raw["content"] if part.get("type") == "image_url"]


def test_归一接受单值字符串与数组并剔空():
    assert normalize_image_contents(None) == []
    assert normalize_image_contents("") == []
    assert normalize_image_contents("  ") == []
    assert normalize_image_contents("abc") == ["abc"]
    assert normalize_image_contents(["a", "", "b"]) == ["a", "b"]


def test_归一拒绝非法类型与超量():
    with pytest.raises(ValueError, match="字符串"):
        normalize_image_contents(123)
    with pytest.raises(ValueError, match="元素必须"):
        normalize_image_contents(["a", 7])
    with pytest.raises(ValueError, match=f"最多携带 {MAX_CHAT_IMAGES} 张"):
        normalize_image_contents([f"img{index}" for index in range(MAX_CHAT_IMAGES + 1)])


def test_归一在总量超限时显式失败(monkeypatch):
    monkeypatch.setattr(input_message_service, "MAX_CHAT_IMAGE_TOTAL_BYTES", 10)
    with pytest.raises(ValueError, match="图片总大小超出限制"):
        normalize_image_contents(["a" * 11])


def test_无图时仍是纯文本消息():
    message = build_chat_input_message("只有文字")

    assert message.message_type == "text"
    assert message.image_content is None
    assert message.raw_message()["content"] == "只有文字"


def test_单图时消息结构逐字节不变():
    """单图是既有契约：前缀、part 顺序、image_content 都不能变。"""
    message = build_chat_input_message("看图", "BASE64DATA")

    assert message.message_type == "multimodal_image"
    assert message.image_content == "BASE64DATA"
    assert message.raw_message()["content"] == [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BASE64DATA"}},
    ]


def test_单值字符串与单元素数组构造结果一致():
    assert build_chat_input_message("看图", "X").raw_message() == build_chat_input_message("看图", ["X"]).raw_message()


def test_十张图全部进入模型输入且顺序保持():
    images = [f"IMG{index}" for index in range(MAX_CHAT_IMAGES)]
    message = build_chat_input_message("十张图", images)

    parts = _image_url_parts(message)
    assert len(parts) == MAX_CHAT_IMAGES
    # 第 10 张必须在、且顺序与请求一致：只进第一张的实现会在这里失败
    assert [part["image_url"]["url"] for part in parts] == [
        f"data:image/jpeg;base64,IMG{index}" for index in range(MAX_CHAT_IMAGES)
    ]
    assert message.image_content == "IMG0"
    assert len(message.raw_message()["content"]) == MAX_CHAT_IMAGES + 1


def test_历史投影只取内联图片并跳过外部链接():
    raw_message = {
        "content": [
            {"type": "text", "text": "x"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBB"}},
        ]
    }

    assert extract_image_contents(raw_message) == ["AAA", "BBB"]
    assert extract_image_contents(None) == []
    assert extract_image_contents({}) == []
    assert extract_image_contents({"content": "纯文本"}) == []


def test_纯文本的_raw_message_投影为空():
    """有 raw_message 但没有 image part 的消息，投影必须为空且仍是文本类型。"""
    raw_message = build_chat_input_message("只有文字").raw_message()

    assert extract_image_contents(raw_message) == []
    restored = restore_chat_input_message(content="只有文字", image_content=None, metadata={"raw_message": raw_message})
    assert restored.message_type == "text"


def test_旧单值历史行仍能还原出一张图():
    """更早的历史行没有 raw_message，只有单值 image_content 列。"""
    restored = restore_chat_input_message(content="看图", image_content="OLDVALUE", metadata={})

    assert restored.message_type == "multimodal_image"
    assert restored.image_content == "OLDVALUE"
    assert _image_url_parts(restored)[0]["image_url"]["url"] == "data:image/jpeg;base64,OLDVALUE"


def test_多图历史行按_raw_message_完整还原():
    built = build_chat_input_message("三张图", ["A", "B", "C"])

    restored = restore_chat_input_message(
        content=built.content,
        image_content=built.image_content,
        metadata={"raw_message": built.raw_message()},
    )

    assert len(_image_url_parts(restored)) == 3
    assert restored.image_content == "A"
