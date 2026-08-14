from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document

from yuxi.services.file_preview import (
    MAX_TEXT_PREVIEW_CHARS,
    detect_preview_type,
    is_office_pdf_preview_file,
    render_preview_payload,
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
        (render_preview_payload, None),
    ],
)
def test_docx_is_not_treated_as_markdown_preview(renderer, expected_third):
    result = renderer("demo.docx", _build_docx_bytes("Docx preview"))

    if isinstance(result, tuple):
        preview_type, supported, third = result
    else:
        preview_type, supported, third = result["preview_type"], result["supported"], result["content"]

    assert preview_type == "unsupported"
    assert supported is False
    assert third == expected_third


def test_render_preview_payload_truncates_long_markdown():
    payload = render_preview_payload("note.md", ("x" * (MAX_TEXT_PREVIEW_CHARS + 1)).encode("utf-8"))

    assert payload["preview_type"] == "markdown"
    assert payload["supported"] is True
    assert payload["truncated"] is True
    assert payload["limit"] == MAX_TEXT_PREVIEW_CHARS
    assert len(payload["content"]) == MAX_TEXT_PREVIEW_CHARS


def test_office_pdf_preview_scope_only_includes_docx_and_pptx():
    assert is_office_pdf_preview_file("demo.docx") is True
    assert is_office_pdf_preview_file("demo.pptx") is True
    assert is_office_pdf_preview_file("demo.xlsx") is False
    assert is_office_pdf_preview_file("demo.doc") is False
    assert is_office_pdf_preview_file("demo.ppt") is False
