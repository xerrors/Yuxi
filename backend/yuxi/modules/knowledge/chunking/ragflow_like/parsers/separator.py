"""严格分隔分块：命中分隔符即切分，超长片段带重叠按 token 细分。"""

from __future__ import annotations

from typing import Any

from yuxi.modules.knowledge.chunking.ragflow_like import nlp
from yuxi.modules.knowledge.chunking.ragflow_like.parsers.general import _iter_sections, unescape_delimiter


def chunk_markdown(markdown_content: str, parser_config: dict[str, Any] | None = None) -> list[str]:
    """严格分隔主入口：先按分隔符切段，超长段再带重叠细分。"""
    parser_config = parser_config or {}

    delimiter = unescape_delimiter(str(parser_config.get("delimiter", "\n") or "\n"))
    chunk_token_num = int(parser_config.get("chunk_token_num", 512) or 512)
    overlapped_percent = int(parser_config.get("overlapped_percent", 0) or 0)

    sections = _iter_sections(markdown_content, delimiter)
    chunks: list[str] = []

    for section in sections:
        text = (section or "").strip()
        if not text:
            continue

        if chunk_token_num > 0 and nlp.count_tokens(text) > chunk_token_num:
            chunks.extend(_split_section_with_overlap(text, chunk_token_num, overlapped_percent))
            continue

        chunks.append(text)

    return chunks


def _slice_text_by_tokens(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """逐字符累积到 token 上限切分，并在窗口间保留指定重叠。"""
    if max_tokens <= 0:
        return [text] if text.strip() else []

    units = [part for part in text]
    chunks: list[str] = []
    start = 0

    while start < len(units):
        current = ""
        current_tokens = 0
        end = start

        while end < len(units):
            next_text = current + units[end]
            next_tokens = nlp.count_tokens(next_text)
            if current and next_tokens > max_tokens:
                break
            current = next_text
            current_tokens = next_tokens
            end += 1
            if current_tokens >= max_tokens:
                break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(units):
            break

        if overlap_tokens <= 0:
            start = end
            continue

        backtrack = end
        overlap_text = ""
        while backtrack > start:
            candidate = units[backtrack - 1] + overlap_text
            if nlp.count_tokens(candidate) > overlap_tokens:
                break
            overlap_text = candidate
            backtrack -= 1

        start = backtrack if backtrack < end else end

    return chunks


def _split_section_with_overlap(section: str, chunk_token_num: int, overlapped_percent: int) -> list[str]:
    """按百分比换算重叠 token 数后切分超长段。"""
    overlap_tokens = 0
    if chunk_token_num > 0 and overlapped_percent > 0:
        overlap_tokens = int(chunk_token_num * max(0, min(overlapped_percent, 99)) / 100)
    return _slice_text_by_tokens(section, chunk_token_num, overlap_tokens)
