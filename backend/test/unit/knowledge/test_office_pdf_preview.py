from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from test.unit.support.knowledge import make_knowledge_file_record
from yuxi.infrastructure.minio import ObjectSizeLimitError
from yuxi.modules.knowledge import preview
from yuxi.shared.files import MAX_FILE_PREVIEW_BYTES


class FakeMinioClient:
    KB_BUCKETS = {"parsed": "knowledgebases"}

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    async def astat_file(self, bucket_name: str, object_name: str) -> int | None:
        content = self.objects.get((bucket_name, object_name))
        return len(content) if content is not None else None

    async def adownload_file(self, bucket_name: str, object_name: str, *, max_bytes: int | None = None) -> bytes:
        content = self.objects[(bucket_name, object_name)]
        if max_bytes is not None and len(content) > max_bytes:
            raise ObjectSizeLimitError("object exceeds read limit")
        return content

    async def aupload_file(
        self,
        bucket_name: str,
        object_name: str,
        data: bytes,
        content_type: str | None = None,
    ) -> SimpleNamespace:
        assert content_type == "application/pdf"
        self.objects[(bucket_name, object_name)] = data
        return SimpleNamespace(url=f"http://localhost:9000/{bucket_name}/{object_name}")


def make_file_record(**overrides) -> SimpleNamespace:
    values = {
        "file_id": "file1",
        "kb_id": "db1",
        "filename": "demo.docx",
        "path": "minio://knowledgebases/db1/upload/demo.docx",
        "file_size": None,
    }
    values.update(overrides)
    return make_knowledge_file_record(**values)


def stub_file_record(monkeypatch: pytest.MonkeyPatch, record: SimpleNamespace) -> None:
    @asynccontextmanager
    async def projection_lock(*_args, **_kwargs):
        """对象内容单测由集成用例覆盖数据库并发边界。"""
        yield

    monkeypatch.setattr(preview, "knowledge_projection_lock", projection_lock)

    async def get_by_file_id(file_id: str):
        assert file_id == record.file_id
        return record

    monkeypatch.setattr(
        preview,
        "KnowledgeFileRepository",
        lambda: SimpleNamespace(get_by_file_id=get_by_file_id),
    )


@pytest.mark.asyncio
async def test_read_office_source_preview_returns_original_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_file_record(monkeypatch, make_file_record())
    minio_client = FakeMinioClient()
    minio_client.objects[("knowledgebases", "db1/upload/demo.docx")] = b"office"

    async def fail_conversion(_filename: str, _content: bytes) -> bytes:
        raise AssertionError("default office preview must not convert to PDF")

    monkeypatch.setattr(preview, "get_minio_client", lambda: minio_client)
    monkeypatch.setattr(preview, "convert_office_to_pdf", fail_conversion)

    response = await preview.read_knowledge_file_preview("db1", "file1")

    assert response["preview_type"] == "office"
    assert response["supported"] is True
    assert response["binary"] is True
    assert response["content"] == b"office"
    assert response["filename"] == "demo.docx"
    assert response["media_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert ("knowledgebases", "db1/preview/file1.pdf") not in minio_client.objects


@pytest.mark.asyncio
async def test_read_office_pdf_preview_converts_and_caches_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_file_record(monkeypatch, make_file_record())
    minio_client = FakeMinioClient()
    minio_client.objects[("knowledgebases", "db1/upload/demo.docx")] = b"office"
    convert_calls = 0

    async def fake_convert(filename: str, content: bytes) -> bytes:
        nonlocal convert_calls
        convert_calls += 1
        assert filename == "demo.docx"
        assert content == b"office"
        return b"%PDF-1.4\nconverted"

    monkeypatch.setattr(preview, "get_minio_client", lambda: minio_client)
    monkeypatch.setattr(preview, "convert_office_to_pdf", fake_convert)

    response = await preview.read_knowledge_file_preview("db1", "file1", office_render="pdf")
    cached_response = await preview.read_knowledge_file_preview("db1", "file1", office_render="pdf")

    assert response["preview_type"] == "pdf"
    assert response["supported"] is True
    assert response["binary"] is True
    assert response["content"] == b"%PDF-1.4\nconverted"
    assert response["media_type"] == "application/pdf"
    assert cached_response["content"] == b"%PDF-1.4\nconverted"
    assert minio_client.objects[("knowledgebases", "db1/preview/file1.pdf")] == b"%PDF-1.4\nconverted"
    assert convert_calls == 1


@pytest.mark.asyncio
async def test_xlsx_preview_returns_original_bytes_without_pdf_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_file_record(monkeypatch, make_file_record(filename="demo.xlsx"))
    minio_client = FakeMinioClient()
    minio_client.objects[("knowledgebases", "db1/upload/demo.docx")] = b"PK\x03\x04excel"

    async def fail_conversion(_filename: str, _content: bytes) -> bytes:
        raise AssertionError("xlsx must not be converted to PDF")

    monkeypatch.setattr(preview, "get_minio_client", lambda: minio_client)
    monkeypatch.setattr(preview, "convert_office_to_pdf", fail_conversion)

    response = await preview.read_knowledge_file_preview("db1", "file1")
    pdf_request = await preview.read_knowledge_file_preview("db1", "file1", office_render="pdf")

    assert response["preview_type"] == "office"
    assert response["supported"] is True
    assert response["content"] == b"PK\x03\x04excel"
    assert response["filename"] == "demo.xlsx"
    assert response["media_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert pdf_request == response
    assert ("knowledgebases", "db1/preview/file1.pdf") not in minio_client.objects


@pytest.mark.asyncio
async def test_read_binary_preview_uses_complete_renderer_result(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_file_record(monkeypatch, make_file_record(filename="report.bin", file_size=16))
    minio_client = FakeMinioClient()
    content = b"%PDF-1.4\nreport"
    minio_client.objects[("knowledgebases", "db1/upload/demo.docx")] = content
    monkeypatch.setattr(preview, "get_minio_client", lambda: minio_client)

    response = await preview.read_knowledge_file_preview("db1", "file1")

    assert response["content"] == content
    assert response["preview_type"] == "pdf"
    assert response["supported"] is True
    assert response["media_type"] == "application/pdf"
    assert response["binary"] is True
    assert response["filename"] == "report.bin"


@pytest.mark.asyncio
async def test_read_file_preview_rejects_large_original_before_download(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_file_record(
        monkeypatch,
        make_file_record(filename="large.pdf", file_size=MAX_FILE_PREVIEW_BYTES + 1),
    )

    def fail_minio_access():
        raise AssertionError("large preview should not access MinIO")

    monkeypatch.setattr(preview, "get_minio_client", fail_minio_access)

    response = await preview.read_knowledge_file_preview("db1", "file1")

    assert response["preview_type"] == "unsupported"
    assert response["supported"] is False
    assert response["limit"] == MAX_FILE_PREVIEW_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["report.pdf", "report.docx", "report.pptx"])
@pytest.mark.parametrize("file_size", [0, None])
async def test_read_file_preview_rejects_actual_oversize_before_conversion_or_cache(
    monkeypatch: pytest.MonkeyPatch, filename: str, file_size: int | None
) -> None:
    stub_file_record(monkeypatch, make_file_record(filename=filename, file_size=file_size))
    minio_client = FakeMinioClient()
    minio_client.objects[("knowledgebases", "db1/upload/demo.docx")] = b"123456789"

    async def unknown_stat(_bucket, _object):
        return None

    async def fail_conversion(_filename, _content):
        raise AssertionError("oversized source must not be converted")

    monkeypatch.setattr(minio_client, "astat_file", unknown_stat)
    monkeypatch.setattr(preview, "get_minio_client", lambda: minio_client)
    monkeypatch.setattr(preview, "MAX_FILE_PREVIEW_BYTES", 8)
    monkeypatch.setattr(preview, "convert_office_to_pdf", fail_conversion)

    response = await preview.read_knowledge_file_preview("db1", "file1")

    assert response["content"] is None
    assert response["preview_type"] == "unsupported"
    assert response["supported"] is False
    assert response["message"] == "文件过大，当前仅支持 30 MB 以内的文件预览"
    assert ("knowledgebases", "db1/preview/file1.pdf") not in minio_client.objects


@pytest.mark.asyncio
async def test_read_file_preview_rejects_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_file_record(monkeypatch, make_file_record(is_folder=True))

    with pytest.raises(ValueError, match="folder"):
        await preview.read_knowledge_file_preview("db1", "file1")
