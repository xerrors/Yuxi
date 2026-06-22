from __future__ import annotations

from io import BytesIO

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


def test_detect_preview_type_does_not_treat_docx_as_markdown_preview():
    preview_type, supported, message = detect_preview_type("demo.docx", _build_docx_bytes("Docx preview"))

    assert preview_type == "unsupported"
    assert supported is False
    assert message == "当前文件是二进制文件，暂不支持预览"


def test_render_preview_payload_rejects_docx_without_parsed_markdown():
    payload = render_preview_payload("demo.docx", _build_docx_bytes("Docx preview text"))

    assert payload["preview_type"] == "unsupported"
    assert payload["supported"] is False
    assert payload["content"] is None


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
