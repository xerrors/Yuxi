"""naive_merge 必须使用调用方传入的分隔符拼接片段。"""

from yuxi.modules.knowledge.chunking.ragflow_like import nlp


def test_merge_uses_configured_delimiter_when_budget_is_unlimited():
    """chunk_token_num <= 0 时同样要按 delimiter 拼接，不能硬编码换行。"""
    merged = nlp.naive_merge(
        [("alpha", ""), ("beta", ""), ("gamma", "")],
        chunk_token_num=0,
        delimiter=" ",
    )

    assert merged == ["alpha beta gamma"]


def test_merge_uses_configured_delimiter_in_budget_path():
    """预算充足时片段仍应以配置的分隔符连接，且不引入前导分隔符。"""
    merged = nlp.naive_merge(
        [("alpha", ""), ("beta", "")],
        chunk_token_num=512,
        delimiter=" ",
    )

    assert merged == ["alpha beta"]


def test_merge_keeps_newline_separator_by_default():
    """未指定分隔符时保持既有换行语义。"""
    merged = nlp.naive_merge(
        [("alpha", ""), ("beta", "")],
        chunk_token_num=512,
        delimiter="\n",
    )

    assert merged == ["alpha\nbeta"]


def test_merge_does_not_rewrite_spaces_in_source():
    """分隔符不是空白时，原文中的空格必须保留。"""
    merged = nlp.naive_merge(
        [("alpha beta", ""), ("gamma delta", "")],
        chunk_token_num=512,
        delimiter="|",
    )

    assert merged == ["alpha beta|gamma delta"]
