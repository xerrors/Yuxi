from io import BytesIO
from inspect import signature
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from server.routers import knowledge_router

pytestmark = pytest.mark.asyncio


class FakeTaskContext:
    def __init__(self):
        self.result = None

    async def set_message(self, message: str) -> None:
        return None

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def set_result(self, result: dict) -> None:
        self.result = result

    async def raise_if_cancelled(self) -> None:
        return None


async def test_upload_file_does_not_expose_legacy_allow_jsonl_query():
    assert "allow_jsonl" not in signature(knowledge_router.upload_file).parameters


async def test_upload_file_rejects_jsonl_uploads():
    upload = UploadFile(filename="dataset.jsonl", file=BytesIO(b'{"query":"hello"}\n'))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id=None, current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unsupported file type: .jsonl"


async def test_upload_file_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(knowledge_router, "MAX_UPLOAD_SIZE_BYTES", 5)
    upload = UploadFile(filename="demo.txt", file=BytesIO(b"123456"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id="kb_1", current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == 400
    assert "100 MB" in exc_info.value.detail


async def test_markdown_endpoint_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(knowledge_router, "MAX_UPLOAD_SIZE_BYTES", 5)
    upload = UploadFile(filename="demo.txt", file=BytesIO(b"123456"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.mark_it_down(upload, current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == 400
    assert "100 MB" in exc_info.value.detail


async def test_index_documents_uses_uid_for_operator(monkeypatch):
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        captured["operator_id"] = operator_id
        return {"file_id": file_id, "status": "indexed"}

    async def fake_enqueue(name: str, task_type: str, payload: dict, coroutine):
        await coroutine(FakeTaskContext())
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.index_documents(
        "kb_1",
        ["file_1"],
        params={},
        current_user=SimpleNamespace(id="numeric-id", uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert captured["operator_id"] == "uid-user"


async def test_add_documents_auto_index_returns_one_final_result_per_item(monkeypatch):
    context = FakeTaskContext()
    item = "minio://knowledgebases/kb_1/upload/demo.txt"

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_add_file_record(kb_id: str, item_path: str, params: dict, operator_id: str | None = None):
        return {"file_id": "file_1", "status": "indexing"}

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        return {"file_id": file_id, "status": "parsed"}

    async def fake_update_file_params(kb_id: str, file_id: str, params: dict, operator_id: str | None = None):
        return None

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        return {"file_id": file_id, "status": "indexed"}

    async def fake_enqueue(name: str, task_type: str, payload: dict, coroutine):
        await coroutine(context)
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(knowledge_router.knowledge_base, "add_file_record", fake_add_file_record)
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.knowledge_base, "update_file_params", fake_update_file_params)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.add_documents(
        "kb_1",
        [item],
        params={"content_type": "file", "auto_index": True},
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert context.result["submitted"] == 1
    assert context.result["failed"] == 0
    assert context.result["items"] == [{"file_id": "file_1", "status": "indexed"}]
