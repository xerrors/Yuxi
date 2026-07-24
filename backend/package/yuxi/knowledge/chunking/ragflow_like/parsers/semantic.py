from __future__ import annotations

import re
from typing import Any

from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin

from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.utils.logging_config import logger

from ..utils.md_parser_utils import (
    extract_table_block,
    get_title_path,
    split_text_by_length_and_newline,
)
from ..utils.table_utils import html_table_to_key_value


def _flush_content(
    result: list,
    current_content: list,
    title_stack: list,
    max_length: int,
    embed_fn: Any,
    special_element: str = None,
    allow_split: bool = False,
) -> None:
    if not current_content:
        return

    content = "\n".join(current_content).strip()
    if not content:
        current_content.clear()
        return

    level = next((i + 1 for i in range(5, -1, -1) if title_stack[i]), 1)
    title_path = get_title_path(title_stack)

    if special_element and not allow_split:
        header = f"{'#' * level} {title_path}|{special_element}" if title_path else f"{'#' * level} {special_element}"
        result.extend([header, content, "-" * 10])
    else:
        if count_tokens(content) > max_length:
            chunks = split_text_by_length_and_newline(
                content, max_length, embed_fn=embed_fn, token_count_fn=count_tokens
            )
            for idx, chunk in enumerate(chunks, 1):
                base_header = f"{'#' * level} {title_path}" if title_path else f"{'#' * level}"
                if special_element:
                    header = f"{base_header}|{special_element}|Part {idx}"
                else:
                    header = f"{base_header}|Part {idx}"
                result.extend([header, chunk, "-" * 10])
        else:
            base_header = f"{'#' * level} {title_path}" if title_path else f"{'#' * level}"
            if special_element:
                header = f"{base_header}|{special_element}"
            else:
                header = base_header

            if header:
                result.append(header)
                result.append("")
            result.extend([content, "-" * 10])

    current_content.clear()


def _handle_image_caption(tokens, i, result, current_content, title_stack, max_length, embed_fn):
    token = tokens[i]
    if token.type != "paragraph_open":
        return False, i

    inline_token = tokens[i + 1]
    if inline_token.type != "inline":
        return False, i

    content = inline_token.content.strip()
    image_pattern = r"^!\[.*?\]\(.*?\)\s*$"
    caption_pattern = r"^(?:Figure|图|Fig\.|表|Table)\s*[\d\w\.]+"

    img_match = re.search(r"^(!\[.*?\]\(.*?\))", content)
    if img_match:
        rest = content[img_match.end() :].strip()
        if rest and re.match(caption_pattern, rest, re.IGNORECASE):
            _flush_content(result, current_content, title_stack, max_length, embed_fn)
            current_content.append(content)
            caption_title = rest.split("\n")[0].strip()
            _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element=caption_title)
            return True, i + 3

    if re.match(image_pattern, content):
        next_p_idx = i + 3
        if next_p_idx + 1 < len(tokens) and tokens[next_p_idx].type == "paragraph_open":
            next_inline = tokens[next_p_idx + 1]
            if next_inline.type == "inline":
                next_content = next_inline.content.strip()
                if re.match(caption_pattern, next_content, re.IGNORECASE):
                    _flush_content(result, current_content, title_stack, max_length, embed_fn)
                    current_content.append(content)
                    current_content.append(next_content)
                    _flush_content(
                        result, current_content, title_stack, max_length, embed_fn, special_element=next_content
                    )
                    return True, i + 6

    if current_content and re.match(caption_pattern, content, re.IGNORECASE):
        last_item = current_content[-1].strip()
        if re.match(image_pattern, last_item):
            image_tag = current_content.pop()
            _flush_content(result, current_content, title_stack, max_length, embed_fn)
            current_content.append(image_tag)
            current_content.append(content)
            _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element=content)
            return True, i + 3

    return False, i


def chunk_markdown(
    markdown_content: str, parser_config: dict[str, Any] | None = None, embed_fn: Any | None = None
) -> list[str]:
    """
    语义化切分 Markdown 内容。

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
        try:
            from yuxi.config.app import config
            from yuxi.models.embed import select_embedding_model

            embed_model_id = parser_config.get("embed_model_id") or config.embed_model
            logger.info(f"语义切分加载Embedding模型: {embed_model_id}")
            embed_model = select_embedding_model(embed_model_id)
            embed_fn = embed_model.encode
        except Exception as e:
            logger.error(f"加载 Embedding 模型失败: {e}。将退化为简单切分。")
            embed_fn = None

    md = MarkdownIt("commonmark").enable("table")
    md.use(dollarmath_plugin, allow_space=True, allow_digits=True)

    tokens: list = md.parse(markdown_content)
    original_lines: list = markdown_content.split("\n")

    result: list = []
    current_content: list = []
    title_stack: list = [""] * 6

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
            for j in range(level, 6):
                title_stack[j] = ""
            i += 3
            continue
        elif token.type == "table_open":
            _flush_content(result, current_content, title_stack, max_length, embed_fn)
            j, table_content = extract_table_block(tokens, i, original_lines)
            current_content.append(table_content)
            _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element="Table")
            i = j + 1 if j < len(tokens) else len(tokens)
            continue
        elif token.type == "paragraph_open":
            handled, new_i = _handle_image_caption(
                tokens, i, result, current_content, title_stack, max_length, embed_fn
            )
            if handled:
                i = new_i
                continue
            inline_token = tokens[i + 1]
            if inline_token.type == "inline":
                current_content.append(inline_token.content.strip())
            i += 3
            continue
        elif token.type == "fence":
            current_content.append(f"```\n{token.content}\n```")
            i += 1
            continue
        elif token.type == "ordered_list_open":
            # _flush_content(result, current_content, title_stack, max_length, embed_fn)
            list_content = []
            j = i + 1
            list_item_counter = 1
            while j < len(tokens) and tokens[j].type != "ordered_list_close":
                if tokens[j].type == "list_item_open":
                    k = j + 1
                    while k < len(tokens) and tokens[k].type != "list_item_close":
                        if (
                            tokens[k].type == "paragraph_open"
                            and k + 1 < len(tokens)
                            and tokens[k + 1].type == "inline"
                        ):
                            list_content.append(f"{list_item_counter}. {tokens[k + 1].content.strip()}")
                            list_item_counter += 1
                        k += 1
                j += 1
            if list_content:
                current_content.extend(list_content)
                _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element=token.type)
            i = j + 1
            continue
        elif token.type == "bullet_list_open":
            # _flush_content(result, current_content, title_stack, max_length, embed_fn)
            list_content = []
            j = i + 1
            while j < len(tokens) and tokens[j].type != "bullet_list_close":
                if tokens[j].type == "list_item_open":
                    k = j + 1
                    while k < len(tokens) and tokens[k].type != "list_item_close":
                        if (
                            tokens[k].type == "paragraph_open"
                            and k + 1 < len(tokens)
                            and tokens[k + 1].type == "inline"
                        ):
                            list_content.append(f"- {tokens[k + 1].content.strip()}")
                        k += 1
                j += 1
            if list_content:
                current_content.extend(list_content)
                _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element=token.type)
            i = j + 1
            continue
        elif token.type == "html_block":
            _flush_content(result, current_content, title_stack, max_length, embed_fn)
            content = token.content.strip()
            is_converted_table = False
            if "<table" in content.lower():
                try:
                    kv_list = html_table_to_key_value(content)
                    if kv_list:
                        content = "\n".join([f"- {item}" for item in kv_list])
                        is_converted_table = True
                except Exception as e:
                    logger.warning(f"HTML表格转KV失败: {e}")

            current_content.append(content)
            if is_converted_table:
                _flush_content(
                    result,
                    current_content,
                    title_stack,
                    max_length,
                    embed_fn,
                    special_element="Table KV",
                    allow_split=True,
                )
            else:
                _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element=token.type)
            i += 1
            continue
        elif token.type in ["list_item_close", "ordered_list_close", "bullet_list_close", "list_item_open"]:
            i += 1
            continue
        elif token.type == "math_block":
            # _flush_content(result, current_content, title_stack, max_length, embed_fn)
            current_content.append(f"$ {token.content} $")
            _flush_content(result, current_content, title_stack, max_length, embed_fn, special_element="Math Block")
            i += 1
            continue
        else:
            i += 1

    _flush_content(result, current_content, title_stack, max_length, embed_fn)

    chunks = []
    current_chunk_parts = []
    for item in result:
        if item == "-" * 10:
            if current_chunk_parts:
                chunks.append("\n".join(current_chunk_parts).strip())
                current_chunk_parts = []
        else:
            current_chunk_parts.append(item)

    if current_chunk_parts:
        chunks.append("\n".join(current_chunk_parts).strip())

    logger.info(f"语义切分完成: chunks={len(chunks)}")
    return chunks
