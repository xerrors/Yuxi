from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath

_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown", ".mdx"})
_PDF_EXTENSIONS = frozenset({".pdf"})
_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".text",
        ".log",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".csv",
        ".tsv",
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".vue",
        ".html",
        ".htm",
        ".css",
        ".less",
        ".scss",
        ".xml",
        ".sql",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".env",
        ".dockerfile",
        ".gitignore",
        ".weather",
    }
)
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"})
_BINARY_SIGNATURES = (
    b"\x7fELF",
    b"MZ",
    b"%PDF-",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"RIFF",
)


def detect_preview_type(path: str, raw_content: bytes) -> tuple[str, bool, str | None]:
    suffix = PurePosixPath(path).suffix.lower()
    mime_type, _encoding = mimetypes.guess_type(path)
    head = raw_content[:1024]

    if suffix in _IMAGE_EXTENSIONS or (mime_type and mime_type.startswith("image/")):
        return "image", True, None

    if suffix in _PDF_EXTENSIONS or mime_type == "application/pdf" or head.startswith(b"%PDF-"):
        return "pdf", True, None

    if suffix in _MARKDOWN_EXTENSIONS:
        return "markdown", True, None

    if suffix in _TEXT_EXTENSIONS:
        return "text", True, None

    if b"\x00" in head:
        return "unsupported", False, "当前文件是二进制文件，暂不支持预览"

    if any(head.startswith(signature) for signature in _BINARY_SIGNATURES):
        if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
            return "image", True, None
        return "unsupported", False, "当前文件格式暂不支持预览"

    if mime_type:
        if mime_type.startswith("text/"):
            return "text", True, None
        if mime_type in {"application/json", "application/xml", "application/javascript"}:
            return "text", True, None
        if mime_type.startswith("application/"):
            return "unsupported", False, "当前文件格式暂不支持预览"

    if not raw_content:
        return "text", True, None

    try:
        raw_content.decode("utf-8")
        return "text", True, None
    except UnicodeDecodeError:
        return "unsupported", False, "当前文件不是可读文本，暂不支持预览"


def detect_media_type(path: str, raw_content: bytes | None = None) -> str:
    """Detect response media type, preferring file signatures over filename suffixes."""
    head = (raw_content or b"")[:512]

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"

    stripped_head = head.lstrip()
    if stripped_head.startswith(b"<svg") or stripped_head.startswith(b"<?xml"):
        suffix = PurePosixPath(path).suffix.lower()
        if suffix == ".svg" or b"<svg" in stripped_head[:256]:
            return "image/svg+xml"

    return mimetypes.guess_type(path)[0] or "application/octet-stream"
