"""文件展示类型判断与预览内容准备。"""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath

from yuxi.shared.files import MAX_FILE_PREVIEW_BYTES, PreviewResult, detect_media_type

MAX_TEXT_PREVIEW_CHARS = 250_000

_PREVIEW_HEAD_BYTES = 1024

_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown", ".mdx"})
_PDF_EXTENSIONS = frozenset({".pdf"})
_HTML_EXTENSIONS = frozenset({".html", ".htm"})
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
_OFFICE_SOURCE_EXTENSIONS = frozenset({".docx", ".pptx", ".xlsx", ".xls"})
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


def is_office_source_preview(path: str) -> bool:
    """判断浏览器端 Office 预览是否应直接读取原始字节。"""
    return PurePosixPath(path).suffix.lower() in _OFFICE_SOURCE_EXTENSIONS


def prepare_office_source_preview(path: str, raw_content: bytes) -> PreviewResult:
    """返回保留真实扩展名的 Office 原文件，供浏览器渲染。"""
    filename = PurePosixPath(path).name or "preview"
    return PreviewResult(
        content=raw_content,
        preview_type="office",
        supported=True,
        media_type=detect_media_type(filename, raw_content),
        filename=filename,
    )


def prepare_file_preview(path: str, raw_content: bytes) -> PreviewResult:
    """把文件字节准备为文本或二进制预览结果。"""
    preview_type, supported, message = detect_preview_type(path, raw_content)
    if preview_type in {"image", "pdf"}:
        return PreviewResult(
            content=raw_content,
            preview_type=preview_type,
            supported=True,
            media_type=detect_media_type(path, raw_content),
            filename=PurePosixPath(path).name or "preview",
        )
    if not supported:
        return _unsupported_preview(message)

    try:
        content = raw_content.decode("utf-8")
    except UnicodeDecodeError:
        return _unsupported_preview("当前文件不是 UTF-8 文本，暂不支持预览")

    truncated = len(content) > MAX_TEXT_PREVIEW_CHARS
    if truncated:
        content = content[:MAX_TEXT_PREVIEW_CHARS]
    return PreviewResult(
        content=content,
        preview_type=preview_type,
        supported=True,
        message=message,
        truncated=truncated,
        limit=MAX_TEXT_PREVIEW_CHARS,
    )


def detect_preview_type(path: str, raw_content: bytes) -> tuple[str, bool, str | None]:
    """判断文件可用的展示类型与不支持原因。"""
    suffix = PurePosixPath(path).suffix.lower()
    mime_type, _encoding = mimetypes.guess_type(path)
    head = raw_content[:_PREVIEW_HEAD_BYTES]

    if suffix in _IMAGE_EXTENSIONS or (mime_type and mime_type.startswith("image/")):
        return "image", True, None
    if suffix in _PDF_EXTENSIONS or mime_type == "application/pdf" or head.startswith(b"%PDF-"):
        return "pdf", True, None
    if suffix in _MARKDOWN_EXTENSIONS:
        return "markdown", True, None
    if suffix in _HTML_EXTENSIONS:
        return "html", True, None
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


def preview_too_large() -> PreviewResult:
    """返回超出预览大小限制的结果。"""
    limit_mb = MAX_FILE_PREVIEW_BYTES // (1024 * 1024)
    return _unsupported_preview(
        f"文件过大，当前仅支持 {limit_mb} MB 以内的文件预览",
        limit=MAX_FILE_PREVIEW_BYTES,
    )


def _unsupported_preview(message: str | None, *, limit: int | None = None) -> PreviewResult:
    """构造不支持的预览结果，统一空内容与状态字段。"""
    return PreviewResult(
        content=None,
        preview_type="unsupported",
        supported=False,
        message=message,
        limit=limit,
    )
