from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document

from yuxi.infrastructure.file_preview import (
    MAX_TEXT_PREVIEW_CHARS,
    detect_preview_type,
    is_office_source_preview,
    prepare_file_preview,
    prepare_office_source_preview,
)


def _build_docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("renderer", "expected_third"),
    [
        (detect_preview_type, "当前文件是二进制文件，暂不支持预览"),
        (prepare_file_preview, None),
    ],
)
def test_docx_is_not_treated_as_markdown_preview(renderer, expected_third):
    result = renderer("demo.docx", _build_docx_bytes("Docx preview"))

    if isinstance(result, tuple):
        preview_type, supported, third = result
    else:
        preview_type, supported, third = result.preview_type, result.supported, result.content

    assert preview_type == "unsupported"
    assert supported is False
    assert third == expected_third


def test_prepare_file_preview_truncates_long_markdown():
    result = prepare_file_preview("note.md", ("x" * (MAX_TEXT_PREVIEW_CHARS + 1)).encode("utf-8"))

    assert result.preview_type == "markdown"
    assert result.supported is True
    assert result.truncated is True
    assert result.limit == MAX_TEXT_PREVIEW_CHARS
    assert len(result.content) == MAX_TEXT_PREVIEW_CHARS


def test_prepare_file_preview_returns_complete_binary_result_from_signature():
    content = b"%PDF-1.4\npreview"

    result = prepare_file_preview("report.bin", content)

    assert result.content == content
    assert result.preview_type == "pdf"
    assert result.supported is True
    assert result.media_type == "application/pdf"
    assert result.filename == "report.bin"


@pytest.mark.parametrize(
    ("filename", "supported"),
    [("demo.docx", True), ("Demo.XLSX", True), ("sheet.xls", True), ("slides.pptx", True), ("notes.csv", False), ("report.pdf", False)],
)
def test_office_source_preview_keeps_original_extension(filename: str, supported: bool) -> None:
    assert is_office_source_preview(filename) is supported
    if not supported:
        return
    result = prepare_office_source_preview(filename, b"PK\x03\x04office")
    assert result.preview_type == "office"
    assert result.supported is True
    assert result.filename == filename
    assert result.content == b"PK\x03\x04office"
    assert result.media_type != "application/pdf"


def test_prepare_file_preview_still_rejects_xlsx_as_generic_binary() -> None:
    result = prepare_file_preview("sheet.xlsx", b"PK\x03\x04excel")

    assert result.preview_type == "unsupported"
    assert result.supported is False
    assert result.content is None


def test_prepare_file_preview_keeps_unsupported_binary_content_hidden():
    result = prepare_file_preview("archive.bin", b"\x00binary")

    assert result.content is None
    assert result.preview_type == "unsupported"
    assert result.supported is False
