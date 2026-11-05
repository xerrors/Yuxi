"""书籍类文本分块：标题层级识别、层级合并与 token 上限保护。"""

from __future__ import annotations

from typing import Any

from yuxi.modules.knowledge.chunking.ragflow_like import nlp
from yuxi.modules.knowledge.chunking.ragflow_like.parsers.general import unescape_delimiter
from yuxi.modules.knowledge.chunking.source_spans import SourceText, join_source_text


def chunk_markdown(markdown_content: str, parser_config: dict[str, Any] | None = None) -> list[str]:
    """书籍分块主入口：识别层级后优先层级合并，退回 naive 合并兜底。"""
    parser_config = parser_config or {}

    # 只有反引号声明的自定义切分配置需要透传；普通值对按行取段的 book 是切分配置，
    # 不是连接符，透传会把行间换行改写掉。
    delimiter = nlp.custom_delimiter_config(unescape_delimiter(str(parser_config.get("delimiter", "") or "")))
    chunk_token_num = int(parser_config.get("chunk_token_num", 512) or 512)
    overlapped_percent = int(parser_config.get("overlapped_percent", 0) or 0)

    sections = _iter_sections(markdown_content)
    if not sections:
        return []

    section_texts = [text for text, _ in sections]
    nlp.remove_contents_table(sections, eng=nlp.is_english(nlp.random_choices(section_texts, k=200)))

    bull = nlp.bullets_category([t for t in nlp.random_choices([t for t, _ in sections], k=100)])

    if bull >= 0:
        chunks = [join_source_text(ck, "\n") for ck in nlp.hierarchical_merge(bull, sections, depth=5)]
    else:
        chunks = nlp.naive_merge(
            sections,
            chunk_token_num=chunk_token_num,
            delimiter=delimiter,
            overlapped_percent=overlapped_percent,
        )

    if chunks:
        return _ensure_chunk_token_limit(chunks, chunk_token_num)

    return _ensure_chunk_token_limit(
        nlp.naive_merge(
            sections,
            chunk_token_num=chunk_token_num,
            delimiter=delimiter,
            overlapped_percent=overlapped_percent,
        ),
        chunk_token_num,
    )


def _iter_sections(markdown_content: str) -> list[tuple[str, str]]:
    """逐行切段并保留字符来源，空文档返回空列表。"""
    sections: list[tuple[str, str]] = []
    markdown_content = SourceText(markdown_content or "")
    for line in (markdown_content or "").splitlines():
        text = line.strip()
        if not text:
            continue
        sections.append((text, ""))

    if not sections and markdown_content and markdown_content.strip():
        sections.append((markdown_content.strip(), ""))

    return sections


def _ensure_chunk_token_limit(chunks: list[str], chunk_token_num: int) -> list[str]:
    """超上限的 chunk 直接按 token 硬切。"""
    max_tokens = int(chunk_token_num or 0)
    normalized = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
    if max_tokens <= 0:
        return normalized

    protected: list[str] = []
    for chunk in normalized:
        if nlp.count_tokens(chunk) <= max_tokens:
            protected.append(chunk)
        else:
            protected.extend(nlp.hard_split_by_token_limit(chunk, max_tokens))
    return protected
