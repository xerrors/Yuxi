from __future__ import annotations

from types import SimpleNamespace

import pytest

from test.unit.support.knowledge import make_knowledge_file_record
from yuxi.infrastructure.minio import ObjectSizeLimitError
from yuxi.modules.knowledge import preview
from yuxi.shared.files import MAX_FILE_PREVIEW_BYTES


class FakeMinioClient:
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
    async def get_by_file_id(file_id: str):
        assert file_id == record.file_id
        return record

    monkeypatch.setattr(
        preview,
        "KnowledgeFileRepository",
        lambda: SimpleNamespace(get_by_file_id=get_by_file_id),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "mime"),
    [
        ("demo.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("demo.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
)
async def test_read_office_preview_returns_original_bytes(monkeypatch: pytest.MonkeyPatch, filename: str, mime: str) -> None:
    stub_file_record(monkeypatch, make_file_record(filename=filename))
    minio_client = FakeMinioClient()
    minio_client.objects[("knowledgebases", "db1/upload/demo.docx")] = b"office"
    monkeypatch.setattr(preview, "get_minio_client", lambda: minio_client)

    response = await preview.read_knowledge_file_preview("db1", "file1")

    assert response.preview_type == "office"
    assert response.supported is True
    assert response.content == b"office"
    assert response.filename == filename
    assert response.media_type == mime


@pytest.mark.asyncio
async def test_read_binary_preview_uses_complete_renderer_result(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_file_record(monkeypatch, make_file_record(filename="report.bin", file_size=16))
    minio_client = FakeMinioClient()
    content = b"%PDF-1.4\nreport"
    minio_client.objects[("knowledgebases", "db1/upload/demo.docx")] = content
    monkeypatch.setattr(preview, "get_minio_client", lambda: minio_client)

    response = await preview.read_knowledge_file_preview("db1", "file1")

    assert response.content == content
    assert response.preview_type == "pdf"
    assert response.supported is True
    assert response.media_type == "application/pdf"
    assert response.filename == "report.bin"


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
async def test_read_file_preview_rejects_oversized_object_with_incomplete_metadata(
    monkeypatch: pytest.MonkeyPatch, filename: str, file_size: int | None
) -> None:
    stub_file_record(monkeypatch, make_file_record(filename=filename, file_size=file_size))
    minio_client = FakeMinioClient()
    minio_client.objects[("knowledgebases", "db1/upload/demo.docx")] = b"123456789"

    async def unknown_stat(_bucket, _object):
        return None

    monkeypatch.setattr(minio_client, "astat_file", unknown_stat)
    monkeypatch.setattr(preview, "get_minio_client", lambda: minio_client)
    monkeypatch.setattr(preview, "MAX_FILE_PREVIEW_BYTES", 8)

    response = await preview.read_knowledge_file_preview("db1", "file1")

    assert response["content"] is None
    assert response["preview_type"] == "unsupported"
    assert response["supported"] is False
    assert response["message"] == "文件过大，当前仅支持 30 MB 以内的文件预览"


@pytest.mark.asyncio
async def test_read_file_preview_rejects_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_file_record(monkeypatch, make_file_record(is_folder=True))

    with pytest.raises(ValueError, match="folder"):
        await preview.read_knowledge_file_preview("db1", "file1")
