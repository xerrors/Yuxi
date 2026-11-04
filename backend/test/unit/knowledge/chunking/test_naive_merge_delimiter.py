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


def test_new_chunk_does_not_start_with_the_separator():
    """片段自己开启新块时不能带前导分隔符，分隔符只用于连接块内片段。"""
    merged = nlp.naive_merge(
        ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"],
        chunk_token_num=2,
        delimiter="|",
    )

    assert merged == ["alpha|beta", "gamma|delta", "epsilon|zeta"]


def test_line_sections_keep_their_newlines_without_a_connector():
    """book/laws 按行取段，行间换行属于原文结构，不能被分隔符配置改写。"""
    merged = nlp.naive_merge(
        [("alpha", ""), ("beta", ""), ("gamma", ""), ("delta", "")],
        chunk_token_num=1000,
    )

    assert merged == ["alpha\nbeta\ngamma\ndelta"]


def test_overlap_tail_is_separated_from_the_appended_fragment():
    """overlap 留下的尾部与当前片段相邻，尾部非空时必须用连接符隔开，否则会粘成新词。"""
    merged = nlp.naive_merge(
        ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"],
        chunk_token_num=2,
        delimiter="|",
        overlapped_percent=25,
    )

    assert merged == ["alpha|beta", "eta|gamma|delta", "elta|epsilon|zeta"]


def test_custom_delimiter_config_still_reaches_line_based_chunkers():
    """反引号声明的自定义切分能力必须保留，不能被「保留行间换行」的修复吞掉。"""
    assert nlp.custom_delimiter_config("`CUT`") == "`CUT`"
    assert nlp.custom_delimiter_config("|") is None
    assert nlp.custom_delimiter_config("") is None


def test_merge_does_not_rewrite_spaces_in_source():
    """分隔符不是空白时，原文中的空格必须保留。"""
    merged = nlp.naive_merge(
        [("alpha beta", ""), ("gamma delta", "")],
        chunk_token_num=512,
        delimiter="|",
    )

    assert merged == ["alpha beta|gamma delta"]
