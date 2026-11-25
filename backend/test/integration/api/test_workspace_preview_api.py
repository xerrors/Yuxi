from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_office_preview_and_download_return_the_original_file(test_client, admin_headers):
    """Office 预览和独立下载均返回上传的原件。"""
    fixture = Path(__file__).resolve().parents[2] / "data" / "测试文档.docx"
    original = fixture.read_bytes()
    filename = f"pytest-office-preview-{uuid4().hex}.docx"
    workspace_path = f"/{filename}"

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

        preview = await test_client.get("/api/workspace/file", params={"path": workspace_path}, headers=admin_headers)
        assert preview.status_code == 200, preview.text
        assert preview.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert preview.headers["x-yuxi-preview-type"] == "office"
        assert preview.headers["content-disposition"].startswith("attachment;")
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert preview.content == original

        download = await test_client.get("/api/workspace/download", params={"path": workspace_path}, headers=admin_headers)
        assert download.status_code == 200, download.text
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.content == original
    finally:
        await test_client.delete("/api/workspace/file", params={"path": workspace_path}, headers=admin_headers)


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
