"""语义分块：按 Markdown 结构聚合正文元素，超长正文经嵌入聚类细分并保留原文来源。"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.modules.knowledge.chunking.ragflow_like.utils.md_parser_utils import (
    extract_table_block,
    get_title_path,
    split_text_by_length_and_newline,
)
from yuxi.modules.knowledge.chunking.ragflow_like.utils.table_utils import html_table_to_key_value
from yuxi.modules.knowledge.chunking.source_spans import LocatedText, SourceText, join_source_text

# 行首图片标记与“仅图片”整行
_IMAGE_PREFIX_RE = re.compile(r"^(!\[.*?\]\(.*?\))")
_IMAGE_LINE_RE = re.compile(r"^!\[.*?\]\(.*?\)\s*$")
# 图表编号说明开头（Figure/图/Fig./表/Table + 编号）
_CAPTION_RE = re.compile(r"^(?:Figure|图|Fig\.|表|Table)\s*[\d\w\.]+", re.IGNORECASE)
# 落盘块之间的分隔标记，最终按它聚合出 chunk
_CHUNK_SEPARATOR = "-" * 10


def chunk_markdown(markdown_content: str, parser_config: dict[str, Any] | None = None, embed_fn: Any | None = None) -> list[str]:
    """
    语义化切分 Markdown 内容。

    主流程：解析 Markdown token 流，按结构元素（标题、段落、围栏、列表、表格、
    HTML、公式）累积正文并落盘为带分隔标记的块，最后聚合成 chunk。

    Args:
        markdown_content: 待切分的 Markdown 文本
        parser_config: 切分参数，如 chunk_token_num
        embed_fn: 可选。传入用于生成向量的函数。如果不传，将从系统配置中加载模型。
                  通过注入此参数可以避免在单元测试中加载重型资源。
    """
    parser_config = parser_config or {}
    max_length = int(parser_config.get("chunk_token_num", 512))
    logger.info(f"语义切分开始: max_length={max_length}, content_length={len(markdown_content)}")

    # 延迟加载重型资源，仅在没有注入 embed_fn 时触发
    if embed_fn is None:
        from yuxi.modules.models.embed import select_embedding_model

        embed_model_id = parser_config.get("embed_model_id")
        if not embed_model_id:
            raise ValueError("语义切分缺少 embed_model_id")
        embed_fn = select_embedding_model(embed_model_id).encode

    md = MarkdownIt("commonmark").enable("table")
    md.use(dollarmath_plugin, allow_space=True, allow_digits=True)

    tokens: list[Any] = md.parse(markdown_content)
    # NUL 与 MarkdownIt 预处理一致替换为 U+FFFD，保证字符位置与 token map 对齐
    source = SourceText(markdown_content.replace("\x00", "\ufffd"))
    # 保留换行符的原文行用于字符来源映射；剥掉换行的纯文本行用于表格块还原
    breaks = [match.end() for match in re.finditer(r"\r\n|\r|\n", source)]
    source_lines = [source[start:end] for start, end in zip([0, *breaks], [*breaks, len(source)], strict=True)]
    original_lines = [str(line).rstrip("\r\n") for line in source_lines]

    result: list[str] = []
    current_content: list[str] = []
    title_stack: list[str] = [""] * 6  # Markdown h1–h6 六级标题栈，空串表示层级未使用

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open":
            inline_token = tokens[i + 1] if i + 1 < len(tokens) else None
            full_title = inline_token.content.strip() if inline_token and inline_token.type == "inline" else ""
            if not full_title:
                i += 3
                continue

            _flush_content(result, current_content, title_stack, max_length, embed_fn)
            level = int(token.tag[1:]) if token.tag and len(token.tag) > 1 else 1
            title_stack[level - 1] = full_title
            for j in range(level, 6):  # 清空更深层级，避免旧标题泄漏到新分节
                title_stack[j] = ""
            i += 3
            continue
        elif token.type == "table_open":
            _flush_content(result, current_content, title_stack, max_length, embed_fn)
            j, table_content = extract_table_block(tokens, i, original_lines)
            current_content.append(LocatedText(table_content, tuple(token.map)))
            _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element="Table")
            i = j + 1 if j < len(tokens) else len(tokens)
            continue
        elif token.type == "paragraph_open":
            handled, new_i = _handle_image_caption(tokens, i, result, current_content, title_stack, max_length, embed_fn)
            if handled:
                i = new_i
                continue
            inline_token = tokens[i + 1]
            if inline_token.type == "inline":
                part = _map_token_text(inline_token.content.strip(), token.map, source_lines)
                current_content.append(part)
            i += 3
            continue
        elif token.type == "fence":
            current_content.append(_build_fence_text(token, source_lines))
            i += 1
            continue
        elif token.type in ("ordered_list_open", "bullet_list_open"):
            j, list_content = _collect_list_items(tokens, i, ordered=token.type == "ordered_list_open")
            if list_content:
                current_content.extend(list_content)
                _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element=token.type)
            i = j + 1
            continue
        elif token.type == "html_block":
            _flush_content(result, current_content, title_stack, max_length, embed_fn)
            content = token.content.strip()
            parts = _iter_html_table_parts(BeautifulSoup(content, "html.parser")) if "<table" in content.lower() else [content]
            for part in parts:
                kv_list = []
                if isinstance(part, Tag) and part.name == "table":
                    try:
                        kv_list = html_table_to_key_value(str(part))
                    except ValueError as e:
                        logger.warning(f"HTML表格转KV失败，保留原表: {e}")
                if not kv_list:
                    current_content.append(LocatedText(str(part), tuple(token.map)))
                    continue

                _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element=token.type)
                for row in kv_list:
                    current_content.append(LocatedText(f"- {row}", tuple(token.map)))
                    # 一行的类别与条件是完整事实；即使超出长度目标也不能拆散。
                    _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element="Table KV")
            _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element=token.type)
            i += 1
            continue
        elif token.type == "math_block":
            current_content.append(LocatedText(f"$ {token.content} $", tuple(token.map)))
            _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element="Math Block")
            i += 1
            continue
        else:
            i += 1

    _flush_content(result, current_content, title_stack, max_length, embed_fn)

    # 按 "-"*10 分隔标记把落盘片段聚合为最终 chunk
    chunks: list[str] = []
    current_chunk_parts: list[str] = []
    for item in result:
        if item == _CHUNK_SEPARATOR:
            if current_chunk_parts:
                chunks.append(_join_source_parts(current_chunk_parts))
                current_chunk_parts = []
        else:
            current_chunk_parts.append(item)

    if current_chunk_parts:
        chunks.append(_join_source_parts(current_chunk_parts))

    logger.info(f"语义切分完成: chunks={len(chunks)}")
    return chunks


# ---------- chunk 落盘 ----------


def _iter_html_table_parts(node: Tag) -> Iterator[Tag | NavigableString]:
    """只拆开含表格的容器，按原顺序保留所有表格与周围正文。"""
    for child in node.children:
        if isinstance(child, Tag) and child.name != "table" and child.find("table") is not None:
            yield from _iter_html_table_parts(child)
        else:
            yield child


def _flush_content(
    result: list[str],
    current_content: list[str],
    title_stack: list[str],
    max_length: int,
    embed_fn: Callable[[list[str]], Any],
    special_element: str | None = None,
) -> None:
    """把累积正文落盘为 chunk：拼接标题头，超长正文细分并保留来源区间。"""
    if not current_content:
        return

    ranges = [part.source_range for part in current_content if hasattr(part, "source_range")]
    source_range = (min(span[0] for span in ranges), max(span[1] for span in ranges)) if ranges else None
    # 全部为带字符来源的文本时保留字符级映射，否则合并后改挂块级行区间
    content = (
        join_source_text(current_content, "\n").strip()
        if all(isinstance(part, SourceText) for part in current_content)
        else "\n".join(current_content).strip()
    )
    if source_range is not None and not isinstance(content, SourceText):
        content = LocatedText(content, source_range)
    if not content:
        current_content.clear()
        return

    # chunk 头层级取标题栈中最深的非空层级，无标题时退为 1 级
    level = next((depth for depth in range(6, 0, -1) if title_stack[depth - 1]), 1)
    title_path = get_title_path(title_stack)
    base_header = f"{'#' * level} {title_path}" if title_path else "#" * level

    if special_element:
        # 保型元素（表格、图注、公式）不细分，元素名直接并入头
        header = f"{base_header}|{special_element}" if title_path else f"{'#' * level} {special_element}"
        result.extend([header, content, _CHUNK_SEPARATOR])
    elif count_tokens(content) > max_length:
        chunks = split_text_by_length_and_newline(content, max_length, embed_fn=embed_fn, token_count_fn=count_tokens)
        cursor = 0
        for idx, chunk in enumerate(chunks, 1):
            header = f"{base_header}|Part {idx}"
            if isinstance(content, SourceText):
                located, cursor = content.map_fragment(chunk, cursor)
                if not any(position >= 0 for position in located.source_positions):
                    # 未闭合 fence 补出的独立围栏没有原文正文，不单独入库。
                    continue
            else:
                # 重写正文的来源属于原始 token 块，不按生成内容反查位置。
                located = LocatedText(chunk, source_range) if source_range else chunk
            result.extend([header, located, _CHUNK_SEPARATOR])
    else:
        result.append(base_header)
        result.append("")
        result.extend([content, _CHUNK_SEPARATOR])

    current_content.clear()


def _join_source_parts(parts: list[str]) -> str:
    """合并生成标题与正文，并保留正文 token 的原文范围。"""
    ranges = [part.source_range for part in parts if isinstance(part, LocatedText)]
    content = join_source_text(parts, "\n").strip()
    if isinstance(content, SourceText):
        return content
    if not ranges:
        return content
    return LocatedText(
        content,
        (min(span[0] for span in ranges), max(span[1] for span in ranges)),
    )


# ---------- 图片与图注识别 ----------


def _handle_image_caption(
    tokens: list[Any],
    i: int,
    result: list[str],
    current_content: list[str],
    title_stack: list[str],
    max_length: int,
    embed_fn: Callable[[list[str]], Any],
) -> tuple[bool, int]:
    """识别“图片 + 图注”组合段落；命中时组合作为整体元素立即落盘，返回 (是否命中, 下一 token 下标)。"""
    token = tokens[i]
    if token.type != "paragraph_open":
        return False, i

    inline_token = tokens[i + 1]
    if inline_token.type != "inline":
        return False, i

    def emit_special(parts: list[str], special_element: str) -> None:
        """先落盘此前累积的正文，再把图片/图注组合以特判元素身份切出。"""
        _flush_content(result, current_content, title_stack, max_length, embed_fn)
        current_content.extend(parts)
        _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element=special_element)

    content = inline_token.content.strip()

    # 图片与图注同段：行首图片标记后紧跟编号说明，图注标题取说明首行
    image_match = _IMAGE_PREFIX_RE.search(content)
    if image_match:
        caption = content[image_match.end() :].strip()
        if caption and _CAPTION_RE.match(caption):
            emit_special([LocatedText(content, tuple(inline_token.map or token.map))], caption.split("\n")[0].strip())
            return True, i + 3

    # 图片独立成段，图注是紧随其后的完整段落
    if _IMAGE_LINE_RE.match(content):
        next_open = i + 3
        if next_open + 1 < len(tokens) and tokens[next_open].type == "paragraph_open":
            next_inline = tokens[next_open + 1]
            if next_inline.type == "inline":
                next_content = next_inline.content.strip()
                if _CAPTION_RE.match(next_content):
                    emit_special(
                        [
                            LocatedText(content, tuple(inline_token.map or token.map)),
                            LocatedText(next_content, tuple(next_inline.map)),
                        ],
                        next_content,
                    )
                    return True, i + 6

    # 图片段已先被累积：图注段出现时取回图片重新组合，避免图片与图注拆进不同 chunk
    if current_content and _CAPTION_RE.match(content):
        last_item = current_content[-1].strip()
        if _IMAGE_LINE_RE.match(last_item):
            image = current_content.pop()
            emit_special([image, LocatedText(content, tuple(inline_token.map or token.map))], content)
            return True, i + 3

    return False, i


# ---------- 结构元素重建 ----------


def _map_token_text(content: str, source_range: list[int], source_lines: list[SourceText]) -> SourceText:
    """在 token 已知的原文范围中映射正文，避免跨块反查重复文本。"""
    source = join_source_text(source_lines[source_range[0] : source_range[1]])
    mapped, _ = source.map_fragment(content)
    mapped.source_range = tuple(source_range)
    return mapped


def _build_fence_text(token: Any, source_lines: list[SourceText]) -> SourceText:
    """按原文行重建围栏代码块并保留字符来源；未闭合围栏不虚构闭合行。"""
    first, last = token.map
    opener = source_lines[first]
    opening = opener.find(token.markup)
    closer = source_lines[last - 1]
    body_line_count = token.content.count("\n") + int(bool(token.content) and not token.content.endswith("\n"))
    # token.content 只包含正文行；token.map 多出的最后一行由 MarkdownIt 认定为 closing。
    has_closer = last > first + 1 + body_line_count
    closing = closer.find(token.markup) if has_closer else -1
    body_lines = source_lines[first + 1 : last - int(has_closer)]
    body_source = join_source_text(body_lines) if body_lines else SourceText("", [])
    body, _ = body_source.map_fragment(token.content)
    # 围栏标记统一输出 ```，来源位置对齐原文标记出现处；未闭合时闭合行记为生成字符
    fence = join_source_text(
        [
            SourceText("```", opener.source_positions[opening : opening + 3]),
            "\n",
            body,
            "\n",
            SourceText("```", closer.source_positions[closing : closing + 3] if has_closer else [-1] * 3),
        ]
    )
    fence.source_range = tuple(token.map)
    return fence


def _collect_list_items(tokens: list[Any], start: int, ordered: bool) -> tuple[int, list[LocatedText]]:
    """收集列表项文本，返回 (列表闭合 token 下标, 项文本列表)；有序项带“序号. ”前缀，无序项带“- ”前缀。"""
    close_type = "ordered_list_close" if ordered else "bullet_list_close"
    items: list[LocatedText] = []
    counter = 1
    j = start + 1
    while j < len(tokens) and tokens[j].type != close_type:
        if tokens[j].type == "list_item_open":
            k = j + 1
            while k < len(tokens) and tokens[k].type != "list_item_close":
                if tokens[k].type == "paragraph_open" and k + 1 < len(tokens) and tokens[k + 1].type == "inline":
                    text = tokens[k + 1].content.strip()
                    if ordered:
                        items.append(LocatedText(f"{counter}. {text}", tuple(tokens[k].map)))
                        counter += 1
                    else:
                        items.append(LocatedText(f"- {text}", tuple(tokens[k].map)))
                k += 1
        j += 1
    return j, items
