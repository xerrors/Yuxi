from __future__ import annotations

import io

import pytest

from yuxi.services import chat_service as chat_svc
from yuxi.services import attachment_service as svc


class _DummyUpload:
    def __init__(self, *, filename: str, content_type: str | None, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._buffer = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    async def seek(self, offset: int) -> int:
        return self._buffer.seek(offset)


def test_build_state_files_only_parsed_and_with_content():
    attachments = [
        {
            "status": "parsed",
            "file_path": "/attachments/a.md",
            "markdown": "line1\nline2",
            "uploaded_at": "2026-02-20T00:00:00+00:00",
        },
        {
            "status": "pending",
            "file_path": "/attachments/b.md",
            "markdown": "ignored",
        },
        {
            "status": "parsed",
            "file_path": "/attachments/c.md",
            "markdown": "",
        },
    ]

    files = chat_svc._build_state_files(attachments)

    assert list(files.keys()) == ["/attachments/a.md"]
    assert files["/attachments/a.md"]["content"] == ["line1", "line2"]
    assert files["/attachments/a.md"]["created_at"] == "2026-02-20T00:00:00+00:00"


@pytest.mark.asyncio
async def test_convert_upload_to_markdown_returns_conversion_result(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_parse_document(source: str, params=None) -> str:
        return "converted markdown"

    monkeypatch.setattr(svc, "_ensure_workdir", lambda: tmp_path)
    monkeypatch.setattr(svc, "parse_document", _fake_parse_document)

    payload = b"hello attachment"
    upload = _DummyUpload(filename="note.txt", content_type="text/plain", data=payload)

    result = await svc._convert_upload_to_markdown(upload)

    assert result.file_name == "note.txt"
    assert result.file_type == "text/plain"
    assert result.file_size == len(payload)
    assert result.markdown == "converted markdown"
    assert result.truncated is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_convert_upload_to_markdown_truncates_content(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_parse_document(source: str, params=None) -> str:
        return "x" * (svc.MAX_ATTACHMENT_MARKDOWN_CHARS + 200)

    monkeypatch.setattr(svc, "_ensure_workdir", lambda: tmp_path)
    monkeypatch.setattr(svc, "parse_document", _fake_parse_document)

    upload = _DummyUpload(filename="note.md", content_type="text/markdown", data=b"hello")

    result = await svc._convert_upload_to_markdown(upload)

    assert result.truncated is True
    assert f"超出 {svc.MAX_ATTACHMENT_MARKDOWN_CHARS} 字符限制" in result.markdown


@pytest.mark.asyncio
async def test_convert_upload_to_markdown_rejects_unsupported_extension(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(svc, "ATTACHMENT_ALLOWED_EXTENSIONS", (".md",))
    upload = _DummyUpload(filename="note.pdf", content_type="application/pdf", data=b"pdf")

    with pytest.raises(ValueError, match="不支持的文件类型"):
        await svc._convert_upload_to_markdown(upload)


@pytest.mark.parametrize(
    ("default_engine", "expected"),
    [
        ("mineru_ocr", "mineru_ocr"),
        ("deepseek_ocr", "deepseek_ocr"),
        ("disable", "rapid_ocr"),
    ],
)
def test_normalize_parse_method_image_uses_default_engine_or_fallback(default_engine, expected):
    method = svc._normalize_parse_method("scan.png", parse_method=None, default_ocr_engine=default_engine)
    assert method == expected


def test_normalize_parse_method_pdf_defaults_to_disable():
    method = svc._normalize_parse_method("doc.pdf", parse_method=None, default_ocr_engine="mineru_ocr")
    assert method == "disable"


def test_normalize_parse_method_respects_explicit_parse_method():
    method = svc._normalize_parse_method("scan.png", parse_method="deepseek_ocr", default_ocr_engine="rapid_ocr")
    assert method == "deepseek_ocr"
