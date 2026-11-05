"""公共文件输入、已授权文件结果与媒体类型识别。"""

import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO

# 所有预览入口共用的原文件读取预算；存储边界只执行传入的字节上限。
MAX_FILE_PREVIEW_BYTES = 30 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class FileInput:
    """借用已复位的文件流；调用方负责关闭，消费者不得保存或关闭它。"""

    filename: str
    source: BinaryIO


@dataclass(frozen=True, slots=True)
class PreparedFile:
    """描述已授权、需在响应完成后删除的临时文件。"""

    path: str
    media_type: str
    filename: str | None = None
    explicit_disposition: bool = False


@dataclass(frozen=True, slots=True)
class PreviewResult:
    """与存储和 HTTP 无关的文件预览结果。"""

    content: str | bytes | None
    preview_type: str
    supported: bool
    media_type: str | None = None
    filename: str | None = None
    message: str | None = None
    truncated: bool = False
    limit: int | None = None

    def payload(self) -> dict:
        """返回文本或不支持预览使用的数据。"""
        return {
            "content": self.content if isinstance(self.content, str) else None,
            "preview_type": self.preview_type,
            "supported": self.supported,
            "message": self.message,
            "truncated": self.truncated,
            "limit": self.limit,
        }


def detect_media_type(path: str, raw_content: bytes | None = None) -> str:
    """优先按内容签名识别，无法识别时使用文件名的媒体类型。"""
    detected = sniff_media_type(raw_content or b"")
    if detected:
        return detected
    if PurePosixPath(path).suffix.lower() == ".svg" and (raw_content or b"").lstrip().startswith(b"<?xml"):
        return "image/svg+xml"

    office_media_types = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in office_media_types:
        return office_media_types[suffix]

    fallback_media_types = {
        ".md": "text/markdown",
        ".xls": "application/vnd.ms-excel",
        ".zip": "application/zip",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }
    return mimetypes.guess_type(path)[0] or fallback_media_types.get(suffix, "application/octet-stream")


def sniff_media_type(raw_content: bytes) -> str | None:
    """仅按内容签名识别常见媒体，未知格式返回空值。"""
    head = raw_content[: 16 * 1024]
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
    if stripped_head.startswith(b"<svg") or (stripped_head.startswith(b"<?xml") and b"<svg" in stripped_head[:256]):
        return "image/svg+xml"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"fLaC"):
        return "audio/flac"
    if head.startswith(b"ID3"):
        return "audio/mpeg"
    if len(head) > 1 and head[0] == 0xFF:
        # ADTS 与 MPEG 共用同步前缀；MPEG 的版本和层不能取保留值。
        if head[1] & 0xF6 == 0xF0:
            return "audio/aac"
        if head[1] & 0xE0 == 0xE0 and head[1] & 0x18 != 0x08 and head[1] & 0x06:
            return "audio/mpeg"
    if head.startswith(b"OggS"):
        if b"theora" in head:
            return "video/ogg"
        if b"OpusHead" in head or b"vorbis" in head:
            return "audio/ogg"
    if head[4:8] == b"ftyp":
        if head[8:12] in (b"M4A ", b"M4B "):
            return "audio/mp4"
        if head[8:12] in (b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"dash"):
            return "video/mp4"
    if head.startswith(b"\x1aE\xdf\xa3") and b"webm" in head:
        return "video/webm"
    return None
