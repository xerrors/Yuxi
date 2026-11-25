from __future__ import annotations

import pytest

from yuxi.infrastructure.file_preview import (
    MAX_TEXT_PREVIEW_CHARS,
    detect_preview_type,
    prepare_file_preview,
)


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
    "filename",
    ["demo.docx", "Demo.XLSX", "sheet.xls", "slides.pptx"],
)
def test_office_source_preview_keeps_original_extension(filename: str) -> None:
    content = b"PK\x03\x04office"
    assert detect_preview_type(filename, content) == ("office", True, None)
    result = prepare_file_preview(filename, content)
    assert result.preview_type == "office"
    assert result.supported is True
    assert result.filename == filename
    assert result.content == content
    assert result.media_type != "application/pdf"


def test_prepare_file_preview_keeps_unsupported_binary_content_hidden():
    result = prepare_file_preview("archive.bin", b"\x00binary")

    assert result.content is None
    assert result.preview_type == "unsupported"
    assert result.supported is False
