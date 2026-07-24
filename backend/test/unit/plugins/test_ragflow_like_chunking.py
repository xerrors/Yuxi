from __future__ import annotations

import os
import sys

sys.path.append(os.getcwd())

from yuxi.knowledge.chunking.ragflow_like.dispatcher import chunk_markdown
from yuxi.knowledge.chunking.ragflow_like.nlp import bullets_category, count_tokens
from yuxi.knowledge.chunking.ragflow_like.utils.semantic_utils import split_sentences_chinese
from yuxi.knowledge.chunking.ragflow_like.presets import (
    CHUNK_ENGINE_VERSION,
    CHUNK_PRESET_IDS,
    CHUNK_PRESETS,
    get_chunk_preset_options,
    get_default_chunk_parser_config,
    map_to_internal_parser_id,
    resolve_chunk_processing_params,
)
from yuxi.knowledge.utils.kb_utils import resolve_processing_params, sanitize_processing_params


def test_general_maps_to_naive() -> None:
    assert map_to_internal_parser_id("general") == "naive"


def test_resolve_chunk_processing_params_priority() -> None:
    resolved = resolve_chunk_processing_params(
        kb_additional_params={
            "chunk_preset_id": "book",
            "chunk_parser_config": {"chunk_token_num": 300, "delimiter": "\\n"},
        },
        file_processing_params={
            "chunk_preset_id": "qa",
            "chunk_parser_config": {"delimiter": "###", "overlapped_percent": 5},
        },
        request_params={
            "chunk_preset_id": "laws",
            "chunk_parser_config": {"chunk_token_num": 666},
        },
    )

    assert resolved["chunk_preset_id"] == "laws"
    assert resolved["chunk_engine_version"] == CHUNK_ENGINE_VERSION
    assert resolved["chunk_parser_config"] == {
        "chunk_token_num": 666,
        "delimiter": "###",
        "overlapped_percent": 5,
    }


def test_resolve_chunk_processing_params_returns_only_nested_keys() -> None:
    resolved = resolve_chunk_processing_params(
        kb_additional_params={"chunk_parser_config": {"chunk_token_num": 300}},
        file_processing_params={},
        request_params={},
    )

    assert resolved["chunk_parser_config"] == {"chunk_token_num": 300}
    assert resolved["chunk_preset_id"] == "general"
    assert resolved["chunk_engine_version"] == CHUNK_ENGINE_VERSION
    assert len(resolved) == 3


def test_qa_chunking_from_markdown_headings() -> None:
    content = """
# 问题一
这是答案一。

## 子问题
这是答案二。
""".strip()

    chunks = chunk_markdown(
        markdown_content=content,
        file_id="file_1",
        filename="faq.md",
        processing_params={"chunk_preset_id": "qa", "chunk_parser_config": {}},
    )

    assert len(chunks) >= 1
    assert "问题：" in chunks[0]["content"]
    assert "回答：" in chunks[0]["content"]


def test_chunk_records_include_reserved_position_fields() -> None:
    content = "第一段内容。\n\n第二段内容。"

    chunks = chunk_markdown(
        markdown_content=content,
        file_id="file_pos",
        filename="pos.md",
        processing_params={
            "chunk_preset_id": "separator",
            "chunk_parser_config": {"delimiter": "\\n\\n"},
        },
    )

    assert chunks[0]["start_char_pos"] == 0
    assert chunks[0]["end_char_pos"] == len("第一段内容。")
    assert chunks[0]["start_token_pos"] is None
    assert chunks[0]["end_token_pos"] is None
    assert "start_char_pos" in chunks[1]


def test_book_chunking_hierarchical_merge() -> None:
    content = """
第一章 总则
第一节 适用范围
本规范适用于测试场景。
第二节 基本原则
应当遵循最小改动原则。
""".strip()

    chunks = chunk_markdown(
        markdown_content=content,
        file_id="file_2",
        filename="book.txt",
        processing_params={"chunk_preset_id": "book", "chunk_parser_config": {"chunk_token_num": 256}},
    )

    assert len(chunks) >= 1
    assert any("第一章" in ck["content"] for ck in chunks)


def test_book_chunking_should_apply_overlength_protection() -> None:
    content = "\n".join(
        [
            "第一章 总则",
            "第一节 适用范围",
            "超长正文" * 1200,
            "第二节 基本原则",
            "应当遵循最小改动原则。",
        ]
    )
    max_chunk_tokens = 180

    chunks = chunk_markdown(
        markdown_content=content,
        file_id="file_book_long",
        filename="book.txt",
        processing_params={
            "chunk_preset_id": "book",
            "chunk_parser_config": {"chunk_token_num": max_chunk_tokens, "delimiter": "\\n"},
        },
    )

    assert len(chunks) > 1
    assert max(count_tokens(ck["content"]) for ck in chunks) <= max_chunk_tokens


def test_split_sentences_chinese_should_keep_quote_boundary() -> None:
    text = "他说：“你好。”然后问：“你在吗？”最后结束！"
    sentences = split_sentences_chinese(text)

    assert sentences == ["他说：“你好。”", "然后问：“你在吗？”", "最后结束！"]


def test_markdown_heading_has_higher_weight_in_bullet_category() -> None:
    sections = [
        "# 3.2 个人所得项目及计税、申报方式概括",
        "一、关于季节工、临时工等费用税前扣除问题，以下规定继续执行。",
        "二、根据现行规定，补贴收入应并入工资薪金所得。",
        "（一）从超出国家规定比例支付的补贴，不属于免税福利费。",
    ]

    # 命中 markdown 标题模式（BULLET_PATTERN 下标 4）时，应该优先选中该组。
    assert bullets_category(sections) == 4


def test_mid_sentence_bullet_marker_should_not_be_treated_as_heading() -> None:
    sections = [
        "根据前述规则：一、这里是句中枚举，不是章节标题，不能被当成层级。",
        "延续上文：（二）这里同样是正文中的枚举表达，不是独立标题。",
        "## 3.4 交通补贴的个税处理",
    ]
    assert bullets_category(sections) == 4


def test_chunk_preset_options_include_description() -> None:
    options = get_chunk_preset_options()
    assert [option["value"] for option in options] == list(CHUNK_PRESETS)
    assert {option["value"] for option in options} == CHUNK_PRESET_IDS
    assert all(isinstance(option.get("description"), str) and option["description"] for option in options)


def test_chunk_preset_defaults_only_include_strategy_specific_fields() -> None:
    for preset_id in CHUNK_PRESET_IDS:
        assert get_default_chunk_parser_config(preset_id) == {}


def test_laws_chunking_should_apply_overlength_protection() -> None:
    lines = ["#### 中华人民共和国企业所得税法实施条例", "##### 微信扫一扫：分享"]
    lines.extend(
        [f"第{i}条 企业所得税法实施细则说明，适用于测试场景，确保条文长度足够用于验证分块策略。" for i in range(1, 260)]
    )
    content = "\n".join(lines)

    max_chunk_tokens = 180
    chunks = chunk_markdown(
        markdown_content=content,
        file_id="file_laws_long",
        filename="laws.docx",
        processing_params={
            "chunk_preset_id": "laws",
            "chunk_parser_config": {
                "chunk_token_num": max_chunk_tokens,
                "overlapped_percent": 20,
                "delimiter": "\\n",
            },
        },
    )

    assert len(chunks) > 1
    assert max(count_tokens(ck["content"]) for ck in chunks) <= max_chunk_tokens


def test_laws_chunking_should_prefer_sentence_boundary_split() -> None:
    line = "第一条 企业所得税法实施细则用于测试分块语义边界。"
    content = line * 120

    chunks = chunk_markdown(
        markdown_content=content,
        file_id="file_laws_sentence",
        filename="laws.docx",
        processing_params={
            "chunk_preset_id": "laws",
            "chunk_parser_config": {
                "chunk_token_num": 120,
                "overlapped_percent": 0,
                "delimiter": "\\n",
            },
        },
    )

    assert len(chunks) > 1
    for ck in chunks:
        text = ck["content"].strip()
        assert text
        assert count_tokens(text) <= 120


def test_laws_chunking_should_prefer_article_level_before_item_level() -> None:
    content = """
第六章 特别纳税调整
第一百零六条 企业所得税法第三十八条规定的可以指定扣缴义务人的情形，包括：
(一)在资金、经营、购销等方面存在直接或者间接的控制关系;
(二)可以代表企业实施其他具有约束力的行为。
第一百零七条 税务机关可以依法核定应纳税所得额。
""".strip()

    chunks = chunk_markdown(
        markdown_content=content,
        file_id="file_laws_article",
        filename="laws.docx",
        processing_params={
            "chunk_preset_id": "laws",
            "chunk_parser_config": {
                "chunk_token_num": 1000,
                "overlapped_percent": 0,
                "delimiter": "\\n",
            },
        },
    )

    # 只要条下款项没有被拆成独立碎片，即可满足“条级优先”的目标。
    target_chunks = [ck["content"] for ck in chunks if "第一百零六条" in ck["content"]]
    assert target_chunks
    assert any("(一)" in chunk and "(二)" in chunk for chunk in target_chunks)


def test_laws_markdown_articles_should_not_collapse_into_chapter_chunk() -> None:
    content = """
## 第一章 总则
- **第一条** 为了规范担保活动，保障债权实现，制定本法。
- **第二条** 在借贷活动中，当事人可以依法设定担保。
- **第三条** 担保活动应当遵循平等、自愿、公平和诚实信用原则。
""".strip()

    chunks = chunk_markdown(
        markdown_content=content,
        file_id="file_laws_markdown_article",
        filename="laws.md",
        processing_params={
            "chunk_preset_id": "laws",
            "chunk_parser_config": {
                "chunk_token_num": 120,
                "overlapped_percent": 0,
                "delimiter": "\\n",
            },
        },
    )

    first_article_chunks = [ck["content"] for ck in chunks if "第一条" in ck["content"]]
    assert first_article_chunks
    # 条级切分时，第一条与第二条不应被合并到同一块。
    assert all("第二条" not in chunk for chunk in first_article_chunks)
    assert max(count_tokens(ck["content"]) for ck in chunks) <= 120


def test_sanitize_processing_params_should_drop_non_persistent_fields() -> None:
    sanitized = sanitize_processing_params(
        {
            "chunk_preset_id": "general",
            "chunk_parser_config": {"chunk_token_num": 300},
            "ocr_engine": "mineru_ocr",
            "ocr_engine_config": {},
            "auto_index": True,
            "content_hashes": {"a.md": "hash-a"},
            "enable_ocr": "mineru_ocr",
            "_preprocessed_map": {"a.md": {"path": "/tmp/a.md"}},
        }
    )

    assert sanitized == {
        "chunk_preset_id": "general",
        "chunk_parser_config": {"chunk_token_num": 300},
        "ocr_engine": "mineru_ocr",
    }


def test_resolve_processing_params_keeps_ocr_fields_and_chunk_params() -> None:
    resolved = resolve_processing_params(
        kb_additional_params={
            "chunk_preset_id": "book",
            "chunk_parser_config": {"delimiter": "\n", "chunk_token_num": 300},
        },
        file_processing_params={
            "ocr_engine": "mineru_ocr",
            "ocr_engine_config": {"backend": "pipeline"},
            "chunk_preset_id": "qa",
            "chunk_parser_config": {"overlapped_percent": 10},
            "content_hashes": {"a.md": "hash-a"},
        },
        request_params={
            "auto_index": True,
            "chunk_preset_id": "laws",
            "chunk_parser_config": {"chunk_token_num": 666},
        },
    )

    assert resolved["ocr_engine"] == "mineru_ocr"
    assert "ocr_engine_config" not in resolved
    assert resolved["chunk_preset_id"] == "laws"
    assert resolved["chunk_parser_config"] == {
        "delimiter": "\n",
        "chunk_token_num": 666,
        "overlapped_percent": 10,
    }
    assert "content_hashes" not in resolved
    assert "enable_ocr" not in resolved
    assert "auto_index" not in resolved


def test_resolve_processing_params_does_not_inject_runtime_defaults() -> None:
    resolved = resolve_processing_params(
        kb_additional_params={},
        file_processing_params={"ocr_engine_config": "invalid", "enable_ocr": "mineru_ocr"},
    )

    assert "ocr_engine" not in resolved
    assert "ocr_engine_config" not in resolved
    assert "enable_ocr" not in resolved


def test_resolve_processing_params_drops_file_and_request_ocr_config() -> None:
    resolved = resolve_processing_params(
        kb_additional_params={},
        file_processing_params={
            "ocr_engine": "rapid_ocr",
            "ocr_engine_config": {"det_box_thresh": 0.4, "zoom_x": 3.0},
        },
        request_params={"ocr_engine_config": {"det_box_thresh": 0.6}},
    )

    assert resolved["ocr_engine"] == "rapid_ocr"
    assert "ocr_engine_config" not in resolved
