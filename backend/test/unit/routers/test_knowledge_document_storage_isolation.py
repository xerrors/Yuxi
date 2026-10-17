from types import SimpleNamespace

import pytest

from server.routers import knowledge_router
from yuxi.storage.minio.client import MinIOClient

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize(
    "source",
    [
        "minio://knowledgebases/kb-a/upload/source.txt",
        "http://minio:9000/knowledgebases/kb-a/upload/source.txt",
        "minio://knowledgebases/kb-b-other/upload/source.txt",
        "minio://public/kb-b/upload/source.txt",
        "minio://knowledgebases/kb-b/upload/%2e%2e/%2e%2e/kb-a/upload/source.txt",
    ],
)
async def test_delete_foreign_source_preserves_original_and_removes_current_document(monkeypatch, batch, source):
    """跨库引用不授予原件删除权，本库记录和派生物仍正常清理。"""
    source_object = knowledge_router.parse_minio_url(source)
    parsed = ("knowledgebases", "kb-b/parsed/file-b.md")
    preview = ("knowledgebases", "kb-b/preview/file-b.pdf")
    objects = {source_object: b"foreign original", parsed: b"derived markdown", preview: b"derived preview"}
    documents = {"file-b": {"meta": {"path": source, "filename": "source.txt"}}}

    class ObjectStorage:
        KB_BUCKETS = MinIOClient.KB_BUCKETS

        async def adelete_file(self, bucket_name, object_name):
            objects.pop((bucket_name, object_name), None)

    async def support(kb_id, operation):
        assert kb_id == "kb-b"

    async def basic_info(kb_id, doc_id):
        assert kb_id == "kb-b"
        return documents[doc_id]

    async def delete_file(kb_id, doc_id):
        assert kb_id == "kb-b"
        del documents[doc_id]

    async def remove_mindmap(*args):
        return None

    monkeypatch.setattr(knowledge_router, "get_minio_client", ObjectStorage)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", support)
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_file_basic_info", basic_info)
    monkeypatch.setattr(knowledge_router.knowledge_base, "delete_file", delete_file)
    monkeypatch.setattr(knowledge_router, "remove_file_from_mindmap", remove_mindmap)
    monkeypatch.setattr(knowledge_router, "batch_remove_files_from_mindmap", remove_mindmap)

    editor = SimpleNamespace(uid="editor-b", role="user")
    if batch:
        result = await knowledge_router.batch_delete_documents("kb-b", ["file-b"], editor)
        assert result["deleted_count"] == 1
    else:
        result = await knowledge_router.delete_document("kb-b", "file-b", editor)
        assert result == {"message": "删除成功"}

    assert documents == {}
    assert objects == {source_object: b"foreign original"}


@pytest.mark.parametrize(
    "source",
    [
        "minio://knowledgebases/kb-b/upload/source.txt",
        "http://minio:9000/knowledgebases/kb-b/upload/folder/source%20name.txt",
    ],
)
async def test_delete_owned_source_removes_original_and_derived_objects(monkeypatch, source):
    """本库原件及派生物按原有语义删除。"""
    objects = {
        knowledge_router.parse_minio_url(source): b"original",
        ("knowledgebases", "kb-b/parsed/file-b.md"): b"markdown",
        ("knowledgebases", "kb-b/preview/file-b.pdf"): b"preview",
    }

    class ObjectStorage:
        KB_BUCKETS = MinIOClient.KB_BUCKETS

        async def adelete_file(self, bucket_name, object_name):
            objects.pop((bucket_name, object_name), None)

    monkeypatch.setattr(knowledge_router, "get_minio_client", ObjectStorage)
    await knowledge_router._delete_document_storage_objects("kb-b", "file-b", source)

    assert objects == {}
