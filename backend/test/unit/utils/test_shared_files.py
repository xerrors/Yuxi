"""公共文件结果与 MIME 识别契约。"""

import pytest

from yuxi.shared.files import PreviewResult, detect_media_type


@pytest.mark.parametrize(
    ("path", "content", "expected"),
    [
        ("wrong.pdf", b"\x89PNG\r\n\x1a\n", "image/png"),
        ("wrong.png", b"%PDF-1.4", "application/pdf"),
        ("unknown.bin", b"\xff\xd8\xff", "image/jpeg"),
        ("unknown.bin", b"GIF89a", "image/gif"),
        ("unknown.bin", b"RIFF\x00\x00\x00\x00WEBP", "image/webp"),
        ("unknown.bin", b"BM", "image/bmp"),
        ("wrong.png", b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio/wav"),
        ("unknown.bin", b"fLaC", "audio/flac"),
        ("unknown.bin", b"ID3\x04\x00\x00", "audio/mpeg"),
        ("recording.aac", bytes.fromhex("fff1504026fffcde"), "audio/aac"),
        ("wrong.mp3", bytes.fromhex("fff9504026fffcde"), "audio/aac"),
        ("unknown.bin", bytes.fromhex("fffb9044"), "audio/mpeg"),
        ("unknown.bin", bytes.fromhex("fff39044"), "audio/mpeg"),
        ("unknown.bin", bytes.fromhex("ffe39044"), "audio/mpeg"),
        ("unknown.bin", bytes.fromhex("ffeb9044"), "application/octet-stream"),
        ("unknown.bin", bytes.fromhex("ffe09044"), "application/octet-stream"),
        ("wrong.wav", b"\x00\x00\x00\x18ftypmp42", "video/mp4"),
        ("unknown.bin", b"OggS\x00OpusHead", "audio/ogg"),
        ("unknown.bin", b"OggS\x00theora", "video/ogg"),
        ("unknown.bin", b"\x1aE\xdf\xa3webm", "video/webm"),
        ("unknown.bin", b"<svg></svg>", "image/svg+xml"),
        ("unknown.bin", b'<?xml version="1.0"?><svg/>', "image/svg+xml"),
        ("report.DOCX", None, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("slides.pptx", None, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("table.xlsx", None, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("note.txt", None, "text/plain"),
        ("unknown.no-known-extension", None, "application/octet-stream"),
    ],
)
def test_detect_media_type_preserves_signature_and_extension_rules(path, content, expected):
    assert detect_media_type(path, content) == expected


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        ("md", "text/markdown"),
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("xls", "application/vnd.ms-excel"),
        ("zip", "application/zip"),
        ("webp", "image/webp"),
        ("bmp", "image/bmp"),
        ("tif", "image/tiff"),
        ("tiff", "image/tiff"),
    ],
)
def test_media_type_fallback_is_independent_of_system_mapping(monkeypatch, suffix, expected):
    """系统缺少扩展名映射时仍保留存储入口的既有 fallback。"""
    from yuxi.shared import files

    monkeypatch.setattr(files.mimetypes, "guess_type", lambda _path: (None, None))
    assert detect_media_type(f"folder/report.{suffix.upper()}") == expected


def test_media_type_keeps_system_mapping_when_available(monkeypatch):
    """系统已识别的媒体类型优先于后缀 fallback。"""
    from yuxi.shared import files

    monkeypatch.setattr(files.mimetypes, "guess_type", lambda _path: ("text/x-markdown", None))
    assert detect_media_type("report.md") == "text/x-markdown"


@pytest.mark.parametrize("content", ["hello", b"binary", None])
def test_preview_payload_preserves_text_and_omits_binary(content):
    result = PreviewResult(content=content, preview_type="text", supported=True)

    assert result.payload() == {
        "content": content if isinstance(content, str) else None,
        "preview_type": "text",
        "supported": True,
        "message": None,
        "truncated": False,
        "limit": None,
    }
