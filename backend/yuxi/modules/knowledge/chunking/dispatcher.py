"""分块契约入口：按预设策略切分 Markdown 并装配可持久化的来源范围。"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from yuxi.modules.knowledge.chunking.presets import map_to_internal_parser_id, normalize_chunk_preset_id
from yuxi.modules.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.modules.knowledge.chunking.ragflow_like.parsers import book, general, laws, qa, semantic, separator
from yuxi.modules.knowledge.chunking.source_spans import SourceSpanLocator


class IngestionChunk(TypedDict):
    """分块到持久化之间的单一内部形状；来源文件名在检索时从 PG 回读。"""

    chunk_id: str
    file_id: str
    chunk_index: int
    content: str
    start_line: int | None
    end_line: int | None
    start_char_pos: int | None
    end_char_pos: int | None
    start_token_pos: int | None
    end_token_pos: int | None
    extraction_result: dict[str, Any] | None
    generation: NotRequired[int]


def chunk_markdown(markdown_content: str, file_id: str, filename: str, processing_params: dict[str, Any]) -> list[IngestionChunk]:
    """按照已合并配置切分文本，并绑定可持久化来源范围。"""
    params = dict(processing_params or {})
    preset_id = normalize_chunk_preset_id(params.get("chunk_preset_id"))
    parser_config = params.get("chunk_parser_config") if isinstance(params.get("chunk_parser_config"), dict) else {}

    text_chunks = _dispatch_markdown_parser(preset_id, filename, markdown_content, parser_config)
    return _build_chunk_records(text_chunks, file_id, markdown_content)


def calculate_chunk_stats(chunks: list[IngestionChunk]) -> dict[str, int]:
    """汇总分块数量及近似 token 数，与分块预算使用同一计数规则。"""
    return {
        "chunk_count": len(chunks),
        "token_count": sum(count_tokens(chunk["content"]) for chunk in chunks),
    }


def _build_chunk_records(text_chunks: list[str], file_id: str, source_text: str | None = None) -> list[IngestionChunk]:
    """将切分正文及其原文范围装配为可持久化的 Chunk。"""
    records: list[IngestionChunk] = []
    locator = SourceSpanLocator(source_text) if source_text is not None else None

    for idx, chunk_content in enumerate(text_chunks):
        text = (chunk_content or "").strip()
        if not text:
            continue

        start_char_pos = end_char_pos = start_line = end_line = None
        if locator is not None:
            start_char_pos, end_char_pos, start_line, end_line = locator.locate(
                text,
                source_range=getattr(chunk_content, "source_range", None),
            )

        records.append(
            {
                "content": str(text),
                "file_id": file_id,
                "chunk_index": idx,
                "chunk_id": f"{file_id}_chunk_{idx}",
                "start_line": start_line,
                "end_line": end_line,
                "start_char_pos": start_char_pos,
                "end_char_pos": end_char_pos,
                "start_token_pos": None,
                "end_token_pos": None,
                "extraction_result": None,
            }
        )

    return records


def _dispatch_markdown_parser(preset_id: str, filename: str, markdown_content: str, parser_config: dict[str, Any]) -> list[str]:
    """按内部 ID 分派到具体分块器；qa/laws 需要文件名参与识别。"""
    parser_id = map_to_internal_parser_id(preset_id)

    if parser_id == "naive":
        return general.chunk_markdown(markdown_content, parser_config)
    if parser_id == "qa":
        return qa.chunk_markdown(filename, markdown_content, parser_config)
    if parser_id == "book":
        return book.chunk_markdown(markdown_content, parser_config)
    if parser_id == "laws":
        return laws.chunk_markdown(filename, markdown_content, parser_config)
    if parser_id == "semantic":
        return semantic.chunk_markdown(markdown_content, parser_config)
    if parser_id == "separator":
        return separator.chunk_markdown(markdown_content, parser_config)

    return general.chunk_markdown(markdown_content, parser_config)
