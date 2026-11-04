"""法规类文本分块：法条识别、章/节/条树形合并与 token 上限保护。"""

from __future__ import annotations

import re
from typing import Any

from yuxi.modules.knowledge.chunking.ragflow_like import nlp
from yuxi.modules.knowledge.chunking.ragflow_like.parsers.general import unescape_delimiter
from yuxi.modules.knowledge.chunking.source_spans import SourceText, join_source_text

_ARTICLE_PATTERN = re.compile(r"^(第[零一二三四五六七八九十百千万0-9]+条)[\s　:：]*(.*)$")


def chunk_markdown(filename: str, markdown_content: str, parser_config: dict[str, Any] | None = None) -> list[str]:
    """
    法规分块主流程（简化版）：
    - docx 优先尝试标题树；
    - 其余格式先做法规文本归一化，再按章/节/条树形切分（depth=3）；
    - 最后统一执行超长保护。
    """
    parser_config = parser_config or {}
    markdown_content = SourceText(markdown_content or "")

    # 只有反引号声明的自定义切分配置需要透传；普通值对按行取段的 laws 是切分配置，
    # 不是连接符，透传会把行间换行改写掉。
    delimiter = nlp.custom_delimiter_config(unescape_delimiter(str(parser_config.get("delimiter", "") or "")))
    chunk_token_num = int(parser_config.get("chunk_token_num", 512) or 512)
    overlapped_percent = int(parser_config.get("overlapped_percent", 0) or 0)

    if re.search(r"\.docx$", filename or "", re.IGNORECASE):
        docx_chunks = _docx_heading_tree(markdown_content)
        if len(docx_chunks) > 1:
            return _ensure_chunk_token_limit(docx_chunks, chunk_token_num, delimiter, overlapped_percent)

    sections = _iter_law_sections(markdown_content)
    if not sections:
        return []

    nlp.remove_contents_table(sections, eng=nlp.is_english(sections))
    typed_sections = [(s, "") for s in sections]

    bull = nlp.bullets_category([s for s, _ in typed_sections])
    if bull == nlp.MARKDOWN_BULLET_GROUP_INDEX:
        # 归一化后仍命中 markdown 组时，回退到中文法规层级组，避免退化成“按章大块”。
        bull = 0

    merged = nlp.tree_merge(bull, typed_sections, depth=3)
    if not merged:
        merged = nlp.naive_merge(
            typed_sections,
            chunk_token_num=chunk_token_num,
            delimiter=delimiter,
            overlapped_percent=overlapped_percent,
        )

    return _ensure_chunk_token_limit(merged, chunk_token_num, delimiter, overlapped_percent)


def _iter_lines(markdown_content: str) -> list[str]:
    """去掉空行并裁剪行首尾空白。"""
    return [line.strip() for line in (markdown_content or "").splitlines() if line.strip()]


def _normalize_law_line(line: str) -> str:
    """剥离 markdown 装饰并压缩空白，同时保留字符来源。"""
    # 法规 markdown 常见的 #、-、** 装饰会干扰层级识别，这里先做轻量归一化。
    text = (line or "").strip()
    for pattern in (r"^#{1,6}\s+", r"^[-*+]\s+"):
        match = re.match(pattern, text)
        if match:
            text = text[match.end() :]
    for marker in ("**", "__", "`"):
        text = join_source_text(text.split(marker))
    parts = []
    start = 0
    for match in re.finditer(r"[ \t]+", text):
        parts.extend([text[start : match.start()], " "])
        start = match.end()
    parts.append(text[start:])
    text = join_source_text(parts)
    return text.strip()


def _expand_article_line(line: str) -> list[str]:
    """把“第X条 正文”行拆成条号与正文两个片段。"""
    normalized = _normalize_law_line(line)
    if not normalized:
        return []

    matched = _ARTICLE_PATTERN.match(normalized)
    if not matched:
        return [normalized]

    article = normalized[matched.start(1) : matched.end(1)].strip()
    body = normalized[matched.start(2) : matched.end(2)].strip()
    if not body:
        return [article]
    return [article, body]


def _iter_law_sections(markdown_content: str) -> list[str]:
    """逐行展开法条得到法规片段序列。"""
    sections: list[str] = []
    for line in _iter_lines(markdown_content):
        sections.extend(_expand_article_line(line))
    return [section for section in sections if section]


def _docx_heading_tree(markdown_content: str) -> list[str]:
    """docx 来源优先按标题层级树合并。"""
    lines: list[tuple[int, str]] = []
    level_set: set[int] = set()

    for raw in (markdown_content or "").splitlines():
        text = raw.strip()
        if not text:
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", text)
        if heading_match:
            level = len(heading_match.group(1))
            value = text[heading_match.start(2) : heading_match.end(2)].strip()
        else:
            level = 99
            value = text

        if not value:
            continue

        lines.append((level, value))
        level_set.add(level)

    if not lines:
        return []

    sorted_levels = sorted(level_set)
    h2_level = sorted_levels[1] if len(sorted_levels) > 1 else 1
    h2_level = sorted_levels[-2] if h2_level == sorted_levels[-1] and len(sorted_levels) > 2 else h2_level

    root = nlp.Node(level=0, depth=h2_level, texts=[])
    root.build_tree(lines)
    return [element for element in root.get_tree() if element]


def _ensure_chunk_token_limit(chunks: list[str], chunk_token_num: int, delimiter: str | None, overlapped_percent: int) -> list[str]:
    """
    对输出 chunk 做 token 上限保护：
    1) 先尝试按行用 naive_merge 再切一次；
    2) 仍超长时才硬切，避免 embeddings 413。
    """
    max_tokens = int(chunk_token_num or 0)
    normalized = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
    if max_tokens <= 0:
        return normalized

    protected: list[str] = []
    for chunk in normalized:
        if nlp.count_tokens(chunk) <= max_tokens:
            protected.append(chunk)
            continue

        refined = nlp.naive_merge(
            [(_line, "") for _line in _iter_lines(chunk)],
            chunk_token_num=max_tokens,
            delimiter=delimiter,
            overlapped_percent=overlapped_percent,
        )
        if not refined:
            refined = [chunk]

        for item in refined:
            cleaned = (item or "").strip()
            if not cleaned:
                continue
            if nlp.count_tokens(cleaned) <= max_tokens:
                protected.append(cleaned)
            else:
                ends = [match.end() for match in re.finditer(r"(?<=[。！？；;!?])", cleaned)] + [len(cleaned)]
                sentences = [cleaned[start:end] for start, end in zip([0, *ends], ends)]
                sentence_refined = nlp.naive_merge(
                    [(sentence, "") for sentence in sentences if sentence.strip()],
                    chunk_token_num=max_tokens,
                    delimiter=delimiter,
                    overlapped_percent=overlapped_percent,
                )
                if not sentence_refined:
                    sentence_refined = [cleaned]

                for sentence_chunk in sentence_refined:
                    text = sentence_chunk.strip()
                    if not text:
                        continue
                    if nlp.count_tokens(text) <= max_tokens:
                        protected.append(text)
                    else:
                        protected.extend(nlp.hard_split_by_token_limit(text, max_tokens))

    return [chunk for chunk in protected if chunk.strip()]
