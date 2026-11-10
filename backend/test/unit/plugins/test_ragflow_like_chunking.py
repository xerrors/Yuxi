from __future__ import annotations

import random

import pytest
from yuxi.modules.knowledge.chunking.ragflow_like import nlp
from yuxi.modules.knowledge.chunking.dispatcher import chunk_markdown
from yuxi.modules.knowledge.chunking.ragflow_like.nlp import bullets_category, count_tokens
from yuxi.modules.knowledge.chunking.presets import (
    CHUNK_ENGINE_VERSION,
    CHUNK_PRESET_IDS,
    CHUNK_PRESETS,
    ensure_chunk_defaults_in_additional_params,
    get_chunk_preset_options,
    map_to_internal_parser_id,
    normalize_chunk_preset_id,
    resolve_chunk_processing_params,
)
from yuxi.modules.knowledge.chunking.ragflow_like.utils.semantic_utils import (
    split_mixed_sentences,
    split_sentences_chinese,
)


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


@pytest.mark.parametrize("value", ["qa_typo", 123, False, 0, [], {}])
@pytest.mark.parametrize("source", ["knowledge_base", "file", "request"])
def test_invalid_preset_rejected_in_every_source(value, source):
    """错误配置不能因优先级或 falsy 值变成成功默认。"""
    sources = [{}, {}, {}]
    sources[(["knowledge_base", "file", "request"]).index(source)] = {"chunk_preset_id": value}
    with pytest.raises(ValueError, match="chunk_preset_id"):
        resolve_chunk_processing_params(*sources)


@pytest.mark.parametrize("value", [None, "", "general", "naive", " NAIVE "])
def test_default_and_internal_alias_remain_general(value):
    """缺省和已有内部别名继续映射通用策略。"""
    assert normalize_chunk_preset_id(value) == "general"


def test_optional_file_and_request_values_inherit_knowledge_base():
    """空选择继承知识库，None 配置不覆盖有效对象。"""
    result = resolve_chunk_processing_params(
        {"chunk_preset_id": "book", "chunk_parser_config": {"chunk_token_num": 256}},
        {"chunk_preset_id": "", "chunk_parser_config": None},
        {"chunk_preset_id": None},
    )
    assert result["chunk_preset_id"] == "book"
    assert result["chunk_parser_config"] == {"chunk_token_num": 256}
    assert ensure_chunk_defaults_in_additional_params({"chunk_parser_config": None})["chunk_parser_config"] == {}


def test_request_preset_overrides_invalid_file_preset():
    """请求级有效选择可覆盖历史文件坏值。"""
    result = resolve_chunk_processing_params({}, {"chunk_preset_id": "typo"}, {"chunk_preset_id": "qa"})
    assert result["chunk_preset_id"] == "qa"


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

    assert [chunk["content"] for chunk in chunks] == [
        "问题：问题一\t回答：这是答案一。",
        "问题：问题一\n子问题\t回答：这是答案二。",
    ]


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


@pytest.mark.parametrize("seed", [0, 94])
def test_book_chunking_hierarchical_merge(monkeypatch, seed) -> None:
    """抽样不得遗漏短文档标题，导致同一文档随机丢失分节。"""
    monkeypatch.setattr(nlp, "random", random.Random(seed))
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

    assert [chunk["content"] for chunk in chunks] == [
        "第一章 总则\n第一节 适用范围\n本规范适用于测试场景。",
        "第一章 总则\n第二节 基本原则\n应当遵循最小改动原则。",
    ]


@pytest.mark.parametrize(
    ("preset_id", "file_id", "filename", "content", "parser_config"),
    [
        (
            "book",
            "file_book_long",
            "book.txt",
            "\n".join(
                [
                    "第一章 总则",
                    "第一节 适用范围",
                    "超长正文" * 1200,
                    "第二节 基本原则",
                    "应当遵循最小改动原则。",
                ]
            ),
            {"chunk_token_num": 180, "delimiter": "\\n"},
        ),
        (
            "laws",
            "file_laws_long",
            "laws.docx",
            "\n".join(
                ["#### 中华人民共和国企业所得税法实施条例", "##### 微信扫一扫：分享"]
                + [f"第{i}条 企业所得税法实施细则说明，适用于测试场景，确保条文长度足够用于验证分块策略。" for i in range(1, 260)]
            ),
            {"chunk_token_num": 180, "overlapped_percent": 20, "delimiter": "\\n"},
        ),
    ],
)
def test_chunking_should_apply_overlength_protection(preset_id, file_id, filename, content, parser_config) -> None:
    max_chunk_tokens = 180

    chunks = chunk_markdown(
        markdown_content=content,
        file_id=file_id,
        filename=filename,
        processing_params={"chunk_preset_id": preset_id, "chunk_parser_config": parser_config},
    )

    assert len(chunks) > 1
    assert max(count_tokens(ck["content"]) for ck in chunks) <= max_chunk_tokens


def test_split_sentences_chinese_should_keep_quote_boundary() -> None:
    text = "他说：“你好。”然后问：“你在吗？”最后结束！"
    sentences = split_sentences_chinese(text)

    assert sentences == ["他说：“你好。”", "然后问：“你在吗？”", "最后结束！"]


def test_split_mixed_sentences_handles_english_abbreviations_without_external_tokenizer() -> None:
    text = "Dr. Smith arrived at 3.14 p.m. He left."

    assert split_mixed_sentences(text) == ["Dr. Smith arrived at 3.14 p.m.", "He left."]
    assert split_mixed_sentences("The U.S. Government acted. Next sentence.") == [
        "The U.S. Government acted.",
        "Next sentence.",
    ]
    assert split_mixed_sentences("I live in the U.S. Next sentence.") == [
        "I live in the U.S.",
        "Next sentence.",
    ]
    assert split_mixed_sentences("He listed items, etc. Next sentence.") == [
        "He listed items, etc.",
        "Next sentence.",
    ]


@pytest.mark.parametrize(
    "sections",
    [
        [
            "# 3.2 个人所得项目及计税、申报方式概括",
            "一、关于季节工、临时工等费用税前扣除问题，以下规定继续执行。",
            "二、根据现行规定，补贴收入应并入工资薪金所得。",
            "（一）从超出国家规定比例支付的补贴，不属于免税福利费。",
        ],
        [
            "根据前述规则：一、这里是句中枚举，不是章节标题，不能被当成层级。",
            "延续上文：（二）这里同样是正文中的枚举表达，不是独立标题。",
            "## 3.4 交通补贴的个税处理",
        ],
    ],
)
def test_bullet_category_prefers_markdown_heading(sections) -> None:
    assert bullets_category(sections) == 4


def test_decimal_outline_starting_at_zero_is_numeric_scheme() -> None:
    """行首 0. 与 0.1 是十进制标题，不能被孤立 0 的伪编号规则整行否决。"""
    assert bullets_category(["0. 概述", "0.1 范围", "0.2 术语", "本规范适用于测试场景。"]) == 1


def test_quantity_and_dot_leader_lines_are_not_a_numbering_scheme() -> None:
    assert bullets_category(["0", "3 个样本", "12......34", "普通说明文字"]) == -1


def test_book_chunking_keeps_at_sign_lines_and_drops_position_tags(monkeypatch) -> None:
    """层级合并不能因行内 @ 丢弃正文；单独的 PDF 位置标签仍然不成段。"""
    monkeypatch.setattr(nlp, "random", random.Random(0))
    content = "\n".join(
        [
            "第一章 总则",
            "第一节 适用范围",
            "@所有人 今晚发布",
            "a@example.com 请查收本周说明",
            "2024@发布说明详见附件",
            "@@1\t2.0##",
            "请查收@@3\t4.0##",
            "本规范适用于测试场景。",
            "第二节 基本原则",
            "应当遵循最小改动原则。",
        ]
    )

    chunks = chunk_markdown(
        markdown_content=content,
        file_id="file_book_at",
        filename="book.txt",
        processing_params={"chunk_preset_id": "book", "chunk_parser_config": {"chunk_token_num": 256}},
    )
    joined = "\n".join(chunk["content"] for chunk in chunks)

    assert "@所有人 今晚发布" in joined
    assert "a@example.com 请查收本周说明" in joined
    assert "2024@发布说明详见附件" in joined
    assert "请查收@@3\t4.0##" in joined
    assert "@@1\t2.0##" not in joined


def test_chunk_preset_options_include_description() -> None:
    options = get_chunk_preset_options()
    assert [option["value"] for option in options] == list(CHUNK_PRESETS)
    assert {option["value"] for option in options} == CHUNK_PRESET_IDS
    assert all(isinstance(option.get("description"), str) and option["description"] for option in options)


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
