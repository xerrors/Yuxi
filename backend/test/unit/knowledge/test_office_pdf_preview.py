from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.knowledge.base import KnowledgeBase
from yuxi.services.file_preview import MAX_BINARY_PREVIEW_SIZE_BYTES


class FakeKnowledgeBase(KnowledgeBase):
    @property
    def kb_type(self) -> str:
        return "fake"

    async def _create_kb_instance(self, kb_id: str, config: dict):
        return None

    async def _initialize_kb_instance(self, instance) -> None:
        pass

    async def index_file(self, kb_id: str, file_id: str, operator_id: str | None = None) -> dict:
        return {}

    async def update_content(self, kb_id: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        return []

    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> list[dict]:
        return []

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        return {"options": []}

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        pass

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def _save_metadata(self) -> None:
        pass


class FakeMinioClient:
    KB_BUCKETS = {"parsed": "knowledgebases"}

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    async def astat_file(self, bucket_name: str, object_name: str) -> int | None:
        content = self.objects.get((bucket_name, object_name))
        return len(content) if content is not None else None

    async def adownload_file(self, bucket_name: str, object_name: str) -> bytes:
        return self.objects[(bucket_name, object_name)]

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


def make_kb(tmp_path) -> FakeKnowledgeBase:
    kb = FakeKnowledgeBase(str(tmp_path))
    kb.databases_meta["db1"] = {"metadata": {}}
    kb.files_meta["file1"] = {
        "file_id": "file1",
        "kb_id": "db1",
        "filename": "demo.docx",
        "path": "minio://knowledgebases/db1/upload/demo.docx",
        "markdown_file": "minio://knowledgebases/db1/parsed/file1.md",
        "status": "parsed",
    }
    return kb


def test_office_file_entry_exposes_logical_file_availability(tmp_path) -> None:
    kb = make_kb(tmp_path)

    entry = kb._knowledge_file_entry("db1", "file1", kb.files_meta["file1"])

    assert entry["has_original_file"] is True
    assert entry["has_parsed_markdown"] is True
    assert "preview_modes" not in entry
    assert "default_preview_mode" not in entry


@pytest.mark.asyncio
async def test_read_office_pdf_preview_converts_and_caches_pdf(tmp_path, monkeypatch) -> None:
    kb = make_kb(tmp_path)
    minio_client = FakeMinioClient()
    minio_client.objects[("knowledgebases", "db1/upload/demo.docx")] = b"office"
    minio_client.objects[("knowledgebases", "db1/parsed/file1.md")] = b"# parsed"

    convert_calls = 0

    async def fake_convert(filename: str, content: bytes) -> bytes:
        nonlocal convert_calls
        convert_calls += 1
        assert filename == "demo.docx"
        assert content == b"office"
        return b"%PDF-1.4\nconverted"

    monkeypatch.setattr("yuxi.storage.minio.get_minio_client", lambda: minio_client)
    monkeypatch.setattr("yuxi.knowledge.base.convert_office_to_pdf", fake_convert)

    response = await kb.read_file_preview("db1", "file1")
    cached_response = await kb.read_file_preview("db1", "file1")

    assert response["preview_type"] == "pdf"
    assert response["supported"] is True
    assert response["binary"] is True
    assert response["content"] == b"%PDF-1.4\nconverted"
    assert response["media_type"] == "application/pdf"
    assert cached_response["content"] == b"%PDF-1.4\nconverted"
    assert minio_client.objects[("knowledgebases", "db1/preview/file1.pdf")] == b"%PDF-1.4\nconverted"
    assert convert_calls == 1


@pytest.mark.asyncio
async def test_non_docx_pptx_office_files_do_not_get_pdf_preview(tmp_path, monkeypatch) -> None:
    kb = make_kb(tmp_path)
    kb.files_meta["file1"]["filename"] = "demo.xlsx"
    minio_client = FakeMinioClient()
    minio_client.objects[("knowledgebases", "db1/upload/demo.docx")] = b"PK\x03\x04excel"
    monkeypatch.setattr("yuxi.storage.minio.get_minio_client", lambda: minio_client)

    entry = kb._knowledge_file_entry("db1", "file1", kb.files_meta["file1"])
    response = await kb.read_file_preview("db1", "file1")

    assert entry["has_original_file"] is True
    assert entry["has_parsed_markdown"] is True
    assert response["preview_type"] == "unsupported"
    assert response["supported"] is False


@pytest.mark.asyncio
async def test_read_file_preview_rejects_large_original_before_download(tmp_path, monkeypatch) -> None:
    kb = make_kb(tmp_path)
    kb.files_meta["file1"]["filename"] = "large.pdf"
    kb.files_meta["file1"]["size"] = MAX_BINARY_PREVIEW_SIZE_BYTES + 1

    async def fail_read(_path: str) -> bytes:
        raise AssertionError("large preview should not download file content")

    monkeypatch.setattr(kb, "_read_minio_bytes", fail_read)

    response = await kb.read_file_preview("db1", "file1")

    assert response["preview_type"] == "unsupported"
    assert response["supported"] is False
    assert response["limit"] == MAX_BINARY_PREVIEW_SIZE_BYTES
