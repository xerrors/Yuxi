from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import knowledge_router

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def allow_authorized_upload(monkeypatch):
    async def fake_permission(kb_id, current_user, required):
        assert kb_id == "db_1"
        assert current_user.role == "admin"
        assert required == knowledge_router.ResourcePermission.MANAGE

    monkeypatch.setattr(knowledge_router, "_ensure_database_permission", fake_permission)


async def test_import_workspace_files_uploads_workspace_file_to_minio(monkeypatch):
    async def fake_ensure_database_supports_documents(slug: str, operation: str) -> None:
        assert slug == "db_1"
        assert "文档添加" in operation

    async def fake_file_existed_in_db(slug: str, content_hash: str) -> bool:
        assert slug == "db_1"
        assert content_hash
        return False

    async def fake_get_same_name_files(slug: str, filename: str) -> list:
        assert slug == "db_1"
        assert filename == "note.md"
        return []

    async def fake_upload(bucket_name: str, file_name: str, data: bytes) -> str:
        assert bucket_name == knowledge_router.MinIOClient.KB_BUCKETS["documents"]
        assert file_name.startswith("db_1/upload/note_")
        assert data == b"# workspace note\n"
        return f"http://minio/{bucket_name}/{file_name}"

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    user = SimpleNamespace(uid="user_1", role="admin")

    async def fake_read_workspace_file_bytes(*, path, current_user):
        assert path == "/note.md"
        assert current_user is user
        return "note.md", b"# workspace note\n"

    monkeypatch.setattr(knowledge_router, "read_workspace_file_bytes", fake_read_workspace_file_bytes)
    monkeypatch.setattr(knowledge_router.knowledge_base, "file_existed_in_db", fake_file_existed_in_db)
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_same_name_files", fake_get_same_name_files)
    monkeypatch.setattr(knowledge_router, "aupload_file_to_minio", fake_upload)

    result = await knowledge_router.import_workspace_files(
        knowledge_router.WorkspaceImportRequest(kb_id="db_1", paths=["/note.md"]),
        current_user=user,
    )

    assert result["status"] == "success"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["file_path"].startswith(
        f"http://minio/{knowledge_router.MinIOClient.KB_BUCKETS['documents']}/db_1/upload/note_"
    )
    assert item["content_hash"]
    assert item["filename"] == "note.md"
    assert item["size"] == len(b"# workspace note\n")
    assert item["workspace_path"] == "/note.md"


async def test_import_workspace_files_rejects_directory(monkeypatch):
    async def fake_ensure_database_supports_documents(slug: str, operation: str) -> None:
        return None

    async def fake_read_workspace_file_bytes(**_kwargs):
        raise HTTPException(status_code=400, detail="当前路径不是文件: /folder")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router, "read_workspace_file_bytes", fake_read_workspace_file_bytes)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.import_workspace_files(
            knowledge_router.WorkspaceImportRequest(kb_id="db_1", paths=["/folder"]),
            current_user=SimpleNamespace(uid="user_1", role="admin"),
        )

    assert exc_info.value.status_code == 400
    assert "不是文件" in exc_info.value.detail
