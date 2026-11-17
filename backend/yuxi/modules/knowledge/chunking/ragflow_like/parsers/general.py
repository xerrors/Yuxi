"""通用（naive）分块：按分隔符切段后做 token 预算内的贪心合并。"""

from __future__ import annotations

from typing import Any

from yuxi.modules.knowledge.chunking.ragflow_like import nlp
from yuxi.modules.knowledge.chunking.source_spans import SourceText

GENERAL_HARD_LIMIT_RATIO = 1.5


def chunk_markdown(markdown_content: str, parser_config: dict[str, Any] | None = None) -> list[str]:
    """通用分块主入口：切段、按 token 预算合并，最后做超长保护。"""
    parser_config = parser_config or {}

    delimiter = unescape_delimiter(str(parser_config.get("delimiter", "\n") or "\n"))
    chunk_token_num = int(parser_config.get("chunk_token_num", 512) or 512)
    overlapped_percent = int(parser_config.get("overlapped_percent", 0) or 0)

    sections = _iter_sections(markdown_content, delimiter)
    chunks = nlp.naive_merge(
        sections,
        chunk_token_num=chunk_token_num,
        delimiter=delimiter,
        overlapped_percent=overlapped_percent,
    )
    return _ensure_chunk_token_limit(chunks, chunk_token_num)


def unescape_delimiter(delimiter: str) -> str:
    """还原配置中被转义的换行、制表与反斜杠分隔符；供各分块器共用。"""
    return delimiter.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t").replace("\\\\", "\\")


def _iter_sections(markdown_content: str, delimiter: str) -> list[str]:
    """按自定义分隔符或逐行切段，空段丢弃并保留字符来源。"""
    sections: list[str] = []
    text = markdown_content if isinstance(markdown_content, SourceText) else SourceText(markdown_content or "")
    if delimiter and delimiter not in {"\n", "\r\n"} and "`" not in delimiter:
        for part in text.split(delimiter):
            block = part.strip()
            if block:
                sections.append(block)
    else:
        for line in text.splitlines():
            block = line.strip()
            if not block:
                continue
            sections.append(block)

    if not sections and text.strip():
        sections.append(text.strip())

    return sections


def _ensure_chunk_token_limit(chunks: list[str], chunk_token_num: int) -> list[str]:
    """对输出 chunk 做 token 上限保护：默认 512 token 时允许到 768 再硬切。"""
    max_tokens = int(chunk_token_num or 0)
    if max_tokens <= 0:
        return [c.strip() for c in chunks if c and c.strip()]

    hard_limit = max(max_tokens, int(max_tokens * GENERAL_HARD_LIMIT_RATIO))
    protected: list[str] = []
    for chunk in chunks:
        cleaned = (chunk or "").strip()
        if not cleaned:
            continue
        if nlp.count_tokens(cleaned) <= max_tokens:
            protected.append(cleaned)
        else:
            protected.extend(nlp.hard_split_by_token_limit(cleaned, max_tokens, hard_limit_token_num=hard_limit))
    return protected
