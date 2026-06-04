from __future__ import annotations

import io
from pathlib import Path

import pytest

from yuxi.services import conversation_service as cs


class _DummyUpload:
    def __init__(self, *, filename: str, content_type: str | None, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._buffer = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    async def seek(self, offset: int) -> int:
        return self._buffer.seek(offset)


def test_build_attachment_storage_path_uses_thread_local_uploads_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cs.app_config, "save_dir", str(tmp_path))

    virtual_path, host_path = cs._build_attachment_storage_path(
        user_id="u-1",
        thread_id="t-1",
        file_name="demo.txt",
    )

    assert virtual_path == "/home/gem/user-data/uploads/attachments/demo.md"
    assert host_path == tmp_path / "threads" / "t-1" / "user-data" / "uploads" / "attachments" / "demo.md"


def test_serialize_attachment_includes_original_file_fields() -> None:
    serialized = cs.serialize_attachment(
        {
            "file_id": "f-1",
            "file_name": "demo.txt",
            "file_type": "text/plain",
            "file_size": 5,
            "status": "parsed",
            "uploaded_at": "2026-03-25T00:00:00+00:00",
            "path": "/home/gem/user-data/uploads/attachments/demo.md",
            "artifact_url": "/api/chat/thread/t-1/artifacts/home/gem/user-data/uploads/attachments/demo.md",
            "original_path": "/home/gem/user-data/uploads/demo.txt",
            "original_artifact_url": "/api/chat/thread/t-1/artifacts/home/gem/user-data/uploads/demo.txt",
            "minio_url": None,
        }
    )

    assert serialized["path"] == "/home/gem/user-data/uploads/attachments/demo.md"
    assert serialized["original_path"] == "/home/gem/user-data/uploads/demo.txt"
    assert serialized["original_artifact_url"] == "/api/chat/thread/t-1/artifacts/home/gem/user-data/uploads/demo.txt"


@pytest.mark.asyncio
async def test_materialize_attachment_files_keeps_original_file_when_markdown_conversion_unsupported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cs.app_config, "save_dir", str(tmp_path))

    upload = _DummyUpload(filename="demo.pdf", content_type="application/pdf", data=b"%PDF-test")

    result = await cs._materialize_attachment_files(
        thread_id="t-1",
        user_id="u-1",
        upload=upload,
        file_name="demo.pdf",
        file_content=b"%PDF-test",
    )

    assert result["status"] == "uploaded"
    assert result["path"] == "/home/gem/user-data/uploads/demo.pdf"
    assert result["original_path"] == "/home/gem/user-data/uploads/demo.pdf"
    assert (
        tmp_path / "threads" / "t-1" / "user-data" / "uploads" / "demo.pdf"
    ).read_bytes() == b"%PDF-test"


@pytest.mark.asyncio
async def test_materialize_attachment_files_writes_markdown_copy_when_conversion_succeeds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cs.app_config, "save_dir", str(tmp_path))

    async def _fake_convert(_upload):
        return cs.ConversionResult(
            file_id="f-1",
            file_name="demo.txt",
            file_type="text/plain",
            file_size=5,
            markdown="hello\nworld",
            truncated=False,
        )

    monkeypatch.setattr(cs, "_convert_upload_to_markdown", _fake_convert)

    upload = _DummyUpload(filename="demo.txt", content_type="text/plain", data=b"hello")

    result = await cs._materialize_attachment_files(
        thread_id="t-1",
        user_id="u-1",
        upload=upload,
        file_name="demo.txt",
        file_content=b"hello",
    )

    assert result["status"] == "parsed"
    assert result["path"] == "/home/gem/user-data/uploads/attachments/demo.md"
    assert result["original_path"] == "/home/gem/user-data/uploads/demo.txt"
    assert result["file_path"] == "/home/gem/user-data/uploads/attachments/demo.md"
    assert result["markdown"] == "hello\nworld"
    assert (tmp_path / "threads" / "t-1" / "user-data" / "uploads" / "demo.txt").read_bytes() == b"hello"
    assert (
        tmp_path / "threads" / "t-1" / "user-data" / "uploads" / "attachments" / "demo.md"
    ).read_text(encoding="utf-8") == "hello\nworld"
