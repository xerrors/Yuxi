import os
import uuid

import pytest

from yuxi.knowledge.utils import parse_minio_url
from yuxi.models.providers.cache import model_cache
from yuxi.storage.minio.client import get_minio_client

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources():
    """本文件仅清理自己创建的资源，关闭父层的共享资源扫描。"""
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    """文档对象测试不创建或清理沙盒。"""
    yield


async def _add_document(client, headers, kb_id, upload):
    """通过真实接口将上传对象登记为文档。"""
    path = upload["file_path"]
    response = await client.post(
        f"/api/knowledge/databases/{kb_id}/documents/add",
        headers=headers,
        json={
            "items": [path],
            "params": {
                "content_hashes": {path: upload["content_hash"]},
                "file_sizes": {path: upload["size"]},
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"], response.text
    return response.json()["items"][0]["file_id"]


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("actor", ["editor", "admin"])
async def test_delete_cross_kb_reference_preserves_foreign_source_and_cleans_owned_objects(
    test_client, admin_headers, standard_user, batch, actor
):
    """真实 HTTP 删除跨库引用后回读原件，且本库原件仍可删除。"""
    models = model_cache.get_all_specs("embedding")
    embedding_spec = os.getenv("TEST_KB_EMBEDDING_MODEL")
    if not embedding_spec:
        if not models:
            pytest.skip("No embedding model is configured; set TEST_KB_EMBEDDING_MODEL.")
        embedding_spec = models[0].spec
    current_user = await test_client.get("/api/auth/me", headers=admin_headers)
    assert current_user.status_code == 200, current_user.text
    owner_scope = {"access_level": "user", "user_uids": [current_user.json()["uid"]]}
    editor_scope = {"access_level": "user", "user_uids": [standard_user["user"]["uid"]]}
    storage = get_minio_client()
    database_ids = []
    fixture_objects = []

    try:
        for scope in (owner_scope, editor_scope):
            response = await test_client.post(
                "/api/knowledge/databases",
                headers=admin_headers,
                json={
                    "database_name": f"pytest_storage_isolation_{uuid.uuid4().hex}",
                    "description": "Scoped object deletion fixture",
                    "embedding_model_spec": embedding_spec,
                    "kb_type": "milvus",
                    "additional_params": {},
                    "share_config": {"version": 2, "read_scope": scope, "manage_scope": scope},
                },
            )
            assert response.status_code == 200, response.text
            database_ids.append(response.json()["kb_id"])
        source_kb, target_kb = database_ids
        editor_headers = standard_user["headers"]
        foreign_content = b"Original owned by knowledge base A; B holds only a reference."
        owned_content = b"Original owned by knowledge base B."
        document_ids = []

        for kb_id, headers, content in (
            (source_kb, admin_headers, foreign_content),
            (target_kb, editor_headers, owned_content),
        ):
            response = await test_client.post(
                "/api/knowledge/files/upload",
                headers=headers,
                params={"kb_id": kb_id},
                files={"file": (f"source-{uuid.uuid4().hex}.txt", content, "text/plain")},
            )
            assert response.status_code == 200, response.text
            upload = response.json()
            fixture_objects.append(parse_minio_url(upload["file_path"]))
            doc_id = await _add_document(test_client, headers, kb_id, upload)
            document_ids.append(doc_id)
            if kb_id == source_kb:
                foreign_upload = upload

        reference_id = await _add_document(test_client, admin_headers, target_kb, foreign_upload)
        reference = await test_client.get(
            f"/api/knowledge/databases/{target_kb}/documents/{reference_id}/basic", headers=editor_headers
        )
        assert reference.status_code == 200, reference.text
        assert reference.json()["meta"]["path"] == foreign_upload["file_path"]
        derived_objects = [
            (storage.KB_BUCKETS["parsed"], f"{target_kb}/parsed/{reference_id}.md"),
            (storage.KB_BUCKETS["parsed"], f"{target_kb}/preview/{reference_id}.pdf"),
        ]
        for bucket_name, object_name in derived_objects:
            fixture_objects.append((bucket_name, object_name))
            storage.upload_file(bucket_name, object_name, b"own derived fixture")
        delete_headers = editor_headers if actor == "editor" else admin_headers

        for doc_id in (reference_id, document_ids[1]):
            if batch:
                deleted = await test_client.request(
                    "DELETE",
                    f"/api/knowledge/databases/{target_kb}/documents/batch",
                    headers=delete_headers,
                    json=[doc_id],
                )
                assert deleted.status_code == 200, deleted.text
                assert deleted.json()["deleted_count"] == 1, deleted.text
            else:
                deleted = await test_client.delete(
                    f"/api/knowledge/databases/{target_kb}/documents/{doc_id}", headers=delete_headers
                )
                assert deleted.status_code == 200, deleted.text

        remaining = await test_client.get(f"/api/knowledge/databases/{target_kb}/documents", headers=editor_headers)
        assert remaining.status_code == 200, remaining.text
        assert remaining.json()["items"] == []
        original = await test_client.get(
            f"/api/knowledge/databases/{source_kb}/documents/{document_ids[0]}/download", headers=admin_headers
        )
        assert original.status_code == 200, original.text
        assert original.content == foreign_content
        assert storage.download_file(*fixture_objects[0]) == foreign_content
        assert not storage.file_exists(*fixture_objects[1])
        assert all(not storage.file_exists(*obj) for obj in derived_objects)
    finally:
        for kb_id in reversed(database_ids):
            response = await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
            assert response.status_code in {200, 404}, response.text
        for bucket_name, object_name in fixture_objects:
            storage.delete_file(bucket_name, object_name)
