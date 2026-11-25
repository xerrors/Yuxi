"""UserWorkspace 文件字节预览与本地 Office 缓存。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path, PurePosixPath

from yuxi.infrastructure.file_preview import OfficeRender, prepare_file_preview
from yuxi.infrastructure.office_conversion import convert_office_to_pdf, is_office_pdf_convertible
from yuxi.infrastructure.runtime_settings import get_runtime_dir
from yuxi.shared.files import PreviewResult


async def preview_workspace_file(
    path: str,
    raw_content: bytes,
    *,
    office_cache_key: str,
    office_render: OfficeRender = "source",
) -> PreviewResult:
    """把 UserWorkspace 文件字节渲染为预览结果。"""
    if office_render == "pdf" and is_office_pdf_convertible(path):
        pdf_content = await _convert_office_to_pdf_cached(path, raw_content, office_cache_key)
        return PreviewResult(
            content=pdf_content,
            preview_type="pdf",
            supported=True,
            media_type="application/pdf",
            filename=f"{PurePosixPath(path).stem or 'preview'}.pdf",
        )
    return prepare_file_preview(path, raw_content, office_source=True)


async def _convert_office_to_pdf_cached(path: str, content: bytes, cache_key: str) -> bytes:
    namespace = hashlib.sha256(str(cache_key).encode("utf-8")).hexdigest()
    content_digest = hashlib.sha256(content).hexdigest()
    cache_dir = get_runtime_dir() / "cache" / "office-previews"
    cache_path = cache_dir / f"{namespace}-{content_digest}.pdf"
    try:
        return await asyncio.to_thread(cache_path.read_bytes)
    except FileNotFoundError:
        pass

    pdf_content = await convert_office_to_pdf(PurePosixPath(path).name, content)
    await asyncio.to_thread(_store_office_preview_cache, cache_dir, namespace, cache_path, pdf_content)
    return pdf_content


def _store_office_preview_cache(
    cache_dir: Path,
    namespace: str,
    cache_path: Path,
    pdf_content: bytes,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for stale in cache_dir.glob(f"{namespace}-*.pdf"):
        if stale != cache_path:
            stale.unlink(missing_ok=True)
    cache_path.write_bytes(pdf_content)
