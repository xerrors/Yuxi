from __future__ import annotations

from typing import Any

from yuxi.knowledge.chunking.ragflow_like import nlp
from yuxi.knowledge.chunking.ragflow_like.parsers import book, general, laws, qa, semantic, separator
from yuxi.knowledge.chunking.ragflow_like.presets import map_to_internal_parser_id, normalize_chunk_preset_id

CHUNK_CONTENT_MAX_BYTES = 65535


def _split_text_by_utf8_bytes(text: str, max_bytes: int) -> list[str]:
    parts: list[str] = []
    start = 0
    current_size = 0

    for index, char in enumerate(text):
        char_size = len(char.encode("utf-8"))
        if index > start and current_size + char_size > max_bytes:
            parts.append(text[start:index])
            start = index
            current_size = 0
        current_size += char_size

    tail = text[start:]
    if tail:
        parts.append(tail)
    return parts


def _ensure_chunk_storage_limit(text_chunks: list[str], parser_config: dict[str, Any]) -> list[str]:
    chunk_token_num = int(parser_config.get("chunk_token_num", 512) or 512)
    limited_chunks: list[str] = []

    for chunk in text_chunks:
        text = (chunk or "").strip()
        if not text:
            continue

        token_parts = nlp.hard_split_by_token_limit(text, chunk_token_num)
        for part in token_parts:
            if len(part.encode("utf-8")) <= CHUNK_CONTENT_MAX_BYTES:
                limited_chunks.append(part)
                continue
            limited_chunks.extend(_split_text_by_utf8_bytes(part, CHUNK_CONTENT_MAX_BYTES))

    return limited_chunks


def _build_chunk_records(
    text_chunks: list[str], file_id: str, filename: str, source_text: str | None = None
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    search_from = 0

    for idx, chunk_content in enumerate(text_chunks):
        text = (chunk_content or "").strip()
        if not text:
            continue

        start_char_pos = None
        end_char_pos = None
        if source_text:
            found_at = source_text.find(text, search_from)
            if found_at >= 0:
                start_char_pos = found_at
                end_char_pos = found_at + len(text)
                search_from = end_char_pos

        records.append(
            {
                "id": f"{file_id}_chunk_{idx}",
                "content": text,
                "file_id": file_id,
                "filename": filename,
                "chunk_index": idx,
                "source": filename,
                "chunk_id": f"{file_id}_chunk_{idx}",
                "start_char_pos": start_char_pos,
                "end_char_pos": end_char_pos,
                "start_token_pos": None,
                "end_token_pos": None,
                "extraction_result": None,
            }
        )

    return records


def _dispatch_markdown_parser(
    preset_id: str, filename: str, markdown_content: str, parser_config: dict[str, Any]
) -> list[str]:
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


def chunk_markdown(
    markdown_content: str, file_id: str, filename: str, processing_params: dict[str, Any]
) -> list[dict[str, Any]]:
    params = dict(processing_params or {})
    preset_id = normalize_chunk_preset_id(params.get("chunk_preset_id"))
    parser_config = params.get("chunk_parser_config") if isinstance(params.get("chunk_parser_config"), dict) else {}

    text_chunks = _dispatch_markdown_parser(preset_id, filename, markdown_content, parser_config)
    text_chunks = _ensure_chunk_storage_limit(text_chunks, parser_config)
    return _build_chunk_records(text_chunks, file_id, filename, markdown_content)


def chunk_file(
    file_content: str, file_id: str, filename: str, processing_params: dict[str, Any]
) -> list[dict[str, Any]]:
    # 当前链路中入库前均已转换为 markdown，因此与 chunk_markdown 保持同实现。
    return chunk_markdown(file_content, file_id, filename, processing_params)
