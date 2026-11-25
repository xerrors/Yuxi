from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from yuxi.infrastructure.runtime_settings import get_runtime_dir

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _file_snapshot(roots: list[Path]) -> dict[Path, tuple[int, int]]:
    """记录真实文件的大小与修改时间，用于证明请求写入边界。"""
    snapshot: dict[Path, tuple[int, int]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                stat = path.stat()
                snapshot[path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


async def test_office_preview_cache_is_written_only_to_api_runtime(test_client, admin_headers):
    """默认预览返回原文件且不写缓存；PDF 回退只在 API 运行目录生成可重建缓存。"""
    fixture = Path(__file__).resolve().parents[2] / "data" / "测试文档.docx"
    original = fixture.read_bytes()
    filename = f"pytest-runtime-cache-{uuid4().hex}.docx"
    workspace_path = f"/{filename}"
    runtime_cache = get_runtime_dir() / "cache" / "office-previews"
    runtime_before = _file_snapshot([runtime_cache])

    try:
        with fixture.open("rb") as source:
            upload = await test_client.post(
                "/api/workspace/upload",
                data={"parent_path": "/"},
                files={
                    "files": (
                        filename,
                        source,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                headers=admin_headers,
            )
        assert upload.status_code == 200, upload.text

        first = await test_client.get("/api/workspace/file", params={"path": workspace_path}, headers=admin_headers)
        second = await test_client.get("/api/workspace/file", params={"path": workspace_path}, headers=admin_headers)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        assert first.headers["x-yuxi-preview-type"] == "office"
        assert first.headers["content-disposition"].startswith("attachment;")
        assert first.headers["x-content-type-options"] == "nosniff"
        assert first.content == original
        assert second.content == original
        assert _file_snapshot([runtime_cache]) == runtime_before

        pdf_params = {"path": workspace_path, "office_render": "pdf"}
        pdf_first = await test_client.get("/api/workspace/file", params=pdf_params, headers=admin_headers)
        pdf_second = await test_client.get("/api/workspace/file", params=pdf_params, headers=admin_headers)

        assert pdf_first.status_code == 200, pdf_first.text
        assert pdf_second.status_code == 200, pdf_second.text
        assert pdf_first.headers["content-type"].startswith("application/pdf")
        assert pdf_first.headers["x-yuxi-preview-type"] == "pdf"
        assert pdf_first.content.startswith(b"%PDF-")
        assert pdf_second.content == pdf_first.content

        runtime_after = _file_snapshot([runtime_cache])
        assert set(runtime_after) - set(runtime_before)
    finally:
        await test_client.delete("/api/workspace/file", params={"path": workspace_path}, headers=admin_headers)
        for cache_path in set(_file_snapshot([runtime_cache])) - set(runtime_before):
            cache_path.unlink(missing_ok=True)


async def test_office_source_uses_document_media_type(test_client, admin_headers):
    """Office 响应的媒体类型不能被上传内容改成浏览器可内联的类型。"""
    filename = f"pytest-office-media-{uuid4().hex}.xlsx"
    params = {"path": f"/{filename}"}
    content = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    uploaded = await test_client.post(
        "/api/workspace/upload", data={"parent_path": "/"}, files={"files": (filename, content)}, headers=admin_headers
    )
    assert uploaded.status_code == 200, uploaded.text
    try:
        response = await test_client.get("/api/workspace/file", params=params, headers=admin_headers)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.content == content
    finally:
        await test_client.delete("/api/workspace/file", params=params, headers=admin_headers)
