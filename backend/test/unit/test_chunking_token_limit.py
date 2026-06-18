"""测试分块 token 上限保护：general parser 对超长 chunk 的硬切分。

nlp.py / general.py 只依赖 re 和标准库，用 sys.modules 占位绕过 yuxi 包的
重依赖链（langchain / pydantic / .env 配置等），实现纯单元测试。
跑完后清理 sys.modules，避免污染其他测试。
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[2] / "package"

_STUB_NAMES = [
    "yuxi",
    "yuxi.knowledge",
    "yuxi.knowledge.chunking",
    "yuxi.knowledge.chunking.ragflow_like",
    "yuxi.knowledge.chunking.ragflow_like.parsers",
    "yuxi.knowledge.chunking.ragflow_like.nlp",
    "yuxi.knowledge.chunking.ragflow_like.parsers.general",
]

# 由 _isolated_modules fixture 在运行时注入
nlp = None  # type: ignore[assignment]
general = None  # type: ignore[assignment]


@pytest.fixture(autouse=True, scope="module")
def _isolated_modules():
    """在模块级加载 nlp/general，跑完后清理 sys.modules 避免污染其他测试。"""
    saved = {name: sys.modules.get(name) for name in _STUB_NAMES}

    for name in _STUB_NAMES[:5]:
        sys.modules.setdefault(name, types.ModuleType(name))

    def _load(name: str, rel: str):
        spec = importlib.util.spec_from_file_location(name, _PKG / rel)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod

    _nlp = _load(
        "yuxi.knowledge.chunking.ragflow_like.nlp",
        "yuxi/knowledge/chunking/ragflow_like/nlp.py",
    )
    sys.modules["yuxi.knowledge.chunking.ragflow_like"].nlp = _nlp  # type: ignore[attr-defined]

    _general = _load(
        "yuxi.knowledge.chunking.ragflow_like.parsers.general",
        "yuxi/knowledge/chunking/ragflow_like/parsers/general.py",
    )

    # 注入模块级变量供测试用例访问
    global nlp, general  # noqa: PLW0603
    nlp = _nlp
    general = _general

    yield

    # 清理：恢复原始状态
    for name in _STUB_NAMES:
        if saved[name] is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved[name]


# ── nlp.hard_split_by_token_limit ──────────────────────────────────


class TestHardSplitByTokenLimit:
    def test_short_text_unchanged(self):
        text = "这是一段短文本"
        result = nlp.hard_split_by_token_limit(text, 512)
        assert result == [text]

    def test_splits_long_chinese_text(self):
        text = "测试内容" * 300  # ~600 CJK tokens
        result = nlp.hard_split_by_token_limit(text, 512)
        assert len(result) > 1
        for chunk in result:
            assert nlp.count_tokens(chunk) <= 512

    def test_optional_hard_limit_keeps_slightly_oversized_text(self):
        text = "内容" * 300  # 600 CJK tokens
        result = nlp.hard_split_by_token_limit(text, 512, hard_limit_token_num=768)
        assert result == [text]

    def test_optional_hard_limit_merges_short_tail(self):
        text = "内容" * 635  # 1270 CJK tokens -> 512 + 512 + 246
        result = nlp.hard_split_by_token_limit(text, 512, hard_limit_token_num=768)
        assert len(result) == 2
        assert [nlp.count_tokens(chunk) for chunk in result] == [512, 758]

    def test_splits_long_english_text(self):
        text = "hello world " * 1000  # ~2000 word tokens
        result = nlp.hard_split_by_token_limit(text, 512)
        assert len(result) > 1
        for chunk in result:
            assert nlp.count_tokens(chunk) <= 512

    def test_splits_long_continuous_ascii_text(self):
        text = "a" * 20000
        result = nlp.hard_split_by_token_limit(text, 512)
        assert len(result) > 1
        assert "".join(result) == text
        for chunk in result:
            assert nlp.count_tokens(chunk) <= 512

    def test_empty_text_returns_empty(self):
        assert nlp.hard_split_by_token_limit("", 512) == []

    def test_whitespace_only_returns_empty(self):
        assert nlp.hard_split_by_token_limit("   \n\t  ", 512) == []

    def test_zero_limit_floors_to_one(self):
        text = "a b c"  # 3 个独立 token（单词）
        result = nlp.hard_split_by_token_limit(text, 0)
        # max_tokens = max(0, 1) = 1, 每个 token 单独一个 chunk
        assert len(result) == 3

    def test_punctuation_only_text(self):
        text = "，。！？"
        result = nlp.hard_split_by_token_limit(text, 512)
        assert result == ["，。！？"]

    def test_count_tokens_estimates_long_continuous_ascii(self):
        assert nlp.count_tokens("a" * 1600) == 100


# ── general._ensure_chunk_token_limit ──────────────────────────────


class TestEnsureChunkTokenLimit:
    def test_all_chunks_within_limit_pass_through(self):
        chunks = ["短文本一", "短文本二", "短文本三"]
        result = general._ensure_chunk_token_limit(chunks, 512)
        assert result == ["短文本一", "短文本二", "短文本三"]

    def test_slightly_oversized_chunk_passes_through(self):
        long_text = "内容" * 300  # ~600 CJK tokens
        chunks = ["短文本", long_text, "短文本二"]
        result = general._ensure_chunk_token_limit(chunks, 512)
        assert result == ["短文本", long_text, "短文本二"]

    def test_oversized_chunk_gets_split_with_merged_tail(self):
        long_text = "内容" * 635  # 1270 CJK tokens -> 512 + 758
        chunks = ["短文本", long_text, "短文本二"]
        result = general._ensure_chunk_token_limit(chunks, 512)
        assert result[0] == "短文本"
        assert result[-1] == "短文本二"
        middle_chunks = result[1:-1]
        assert len(middle_chunks) == 2
        assert [nlp.count_tokens(chunk) for chunk in middle_chunks] == [512, 758]

    def test_empty_chunks_filtered(self):
        chunks = ["有效文本", "", "   ", "另一段"]
        result = general._ensure_chunk_token_limit(chunks, 512)
        assert result == ["有效文本", "另一段"]

    def test_zero_limit_returns_stripped(self):
        chunks = ["  文本一  ", "文本二"]
        result = general._ensure_chunk_token_limit(chunks, 0)
        assert result == ["文本一", "文本二"]


# ── general.chunk_markdown 集成 ────────────────────────────────────


class TestGeneralChunkMarkdown:
    def test_normal_document_chunks_within_limit(self):
        doc = "# 标题\n\n第一段内容\n\n第二段内容\n\n第三段内容"
        chunks = general.chunk_markdown(doc, {"chunk_token_num": 512})
        assert len(chunks) > 0
        for chunk in chunks:
            assert nlp.count_tokens(chunk) <= 512

    def test_oversized_single_line_gets_split(self):
        long_line = "运维知识" * 800  # ~3200 CJK tokens
        doc = f"# 运维知识库\n\n{long_line}"
        chunks = general.chunk_markdown(doc, {"chunk_token_num": 512})
        assert len(chunks) > 1
        for chunk in chunks:
            assert nlp.count_tokens(chunk) <= 768

    def test_empty_document_returns_empty(self):
        assert general.chunk_markdown("", {"chunk_token_num": 512}) == []

    def test_default_config_uses_512(self):
        doc = "测试\n" * 200
        chunks = general.chunk_markdown(doc)
        for chunk in chunks:
            assert nlp.count_tokens(chunk) <= 512


# ── laws parser 回归 ──────────────────────────────────────────────


class TestLawsParserRegression:
    """验证 nlp.hard_split_by_token_limit 可被 laws parser 正常调用。"""

    def test_hard_split_produces_same_result(self):
        text = "法规内容" * 300
        result = nlp.hard_split_by_token_limit(text, 512)
        assert len(result) > 1
        for chunk in result:
            assert nlp.count_tokens(chunk) <= 512
