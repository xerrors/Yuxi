"""真实对象与 HTTP 下载链路的 MIME、文件内容回归。"""

from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import update
from test.live_api_cleanup import make_test_session_title

from yuxi.infrastructure.minio import ObjectSizeLimitError, get_minio_client
from yuxi.modules.knowledge.models import KnowledgeFile
from yuxi.shared.files import MAX_FILE_PREVIEW_BYTES

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize("explicit_type", [None, "text/custom"])
async def test_minio_upload_persists_media_type_and_bytes(explicit_type):
    """重新 stat 和下载真实对象，验证 MIME 与显式类型优先级。"""
    storage = get_minio_client()
    bucket = storage.KB_BUCKETS["documents"]
    name = f"pytest-mime/{uuid4().hex}/report.MD"
    content = b"# stored bytes\n"
    try:
        await storage.aupload_file(bucket, name, content, content_type=explicit_type)
        metadata = storage.client.stat_object(bucket, name)
        assert metadata.content_type == (explicit_type or "text/markdown")
        assert metadata.size == len(content)
        assert await storage.adownload_file(bucket, name) == content
    finally:
        await storage.adelete_file(bucket, name)


async def test_viewer_and_artifact_download_preserve_mime_and_multichunk_bytes(test_client, standard_user):
    """真实上传到 Workspace 后，Viewer 与 Artifact 的 fd 复制输出完整一致。"""
    headers = standard_user["headers"]
    default = await test_client.get("/api/agent/default", headers=headers)
    assert default.status_code == 200, default.text
    agent = default.json()["agent"]
    agent_id = agent.get("slug") or agent.get("id")
    assert agent_id
    created = await test_client.post(
        "/api/v1/agents/sessions",
        json={"agent_id": agent_id, "title": make_test_session_title("mime-fd-copy")},
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 201, created.text
    thread_id = created.json()["id"]
    content = b"# fd copy\n" + b"x" * (1024 * 1024 + 7)
    uploaded = await test_client.post(
        "/api/viewer/filesystem/upload",
        data={"thread_id": thread_id, "parent_path": "/"},
        files={"files": ("report.MD", content, "text/markdown")},
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    [entry] = uploaded.json()["entries"]
    try:
        viewer = await test_client.get(
            "/api/viewer/filesystem/download",
            params={"thread_id": thread_id, "path": entry["path"]},
            headers=headers,
        )
        artifact = await test_client.get(entry["artifact_url"], params={"download": True}, headers=headers)
        for response in (viewer, artifact):
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].split(";", 1)[0] == "text/markdown"
            assert response.content == content
    finally:
        removed = await test_client.delete(
            "/api/viewer/filesystem/file", params={"thread_id": thread_id, "path": entry["path"]}, headers=headers
        )
        assert removed.status_code == 200, removed.text


async def test_knowledge_original_download_preserves_mime_and_source_bytes(test_client, admin_headers, knowledge_base_resource):
    """通过知识库原文件 HTTP 下载核对类型与真实源对象。"""
    storage = get_minio_client()
    bucket = storage.KB_BUCKETS["documents"]
    kb_id = knowledge_base_resource["kb_id"]
    name = f"{kb_id}/upload/pytest-mime-{uuid4().hex}.MD"
    source = f"minio://{bucket}/{name}"
    content = b"# original knowledge bytes\n"
    file_id = None
    try:
        await storage.aupload_file(bucket, name, content)
        added = await test_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/add",
            json={
                "items": [source],
                "params": {
                    "content_hashes": {source: sha256(content).hexdigest()},
                    "file_sizes": {source: len(content)},
                },
            },
            headers=admin_headers,
        )
        assert added.status_code == 200, added.text
        payload = added.json()
        assert payload["status"] == "success", payload
        file_id = payload["items"][0]["file_id"]
        downloaded = await test_client.get(
            "/api/workspace/knowledge/download", params={"kb_id": kb_id, "file_id": file_id}, headers=admin_headers
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.headers["content-type"].split(";", 1)[0] == "text/markdown"
        assert downloaded.content == content
        assert await storage.adownload_file(bucket, name) == content
    finally:
        if file_id:
            removed = await test_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{file_id}", headers=admin_headers)
            assert removed.status_code in {200, 404}, removed.text
        await storage.adelete_file(bucket, name)


async def test_minio_download_enforces_actual_bytes_without_changing_object():
    """真实 MinIO 对象在上限内完整返回，超限拒绝且保留源字节。"""
    storage = get_minio_client()
    bucket = storage.KB_BUCKETS["documents"]
    object_name = f"pytest-preview-limits/{uuid4().hex}/source.bin"
    content = b"123456789"
    try:
        await storage.aupload_file(bucket, object_name, content)
        with pytest.raises(ObjectSizeLimitError):
            await storage.adownload_file(bucket, object_name, max_bytes=8)
        assert await storage.adownload_file(bucket, object_name, max_bytes=9) == content
        assert await storage.adownload_file(bucket, object_name) == content
    finally:
        await storage.adelete_file(bucket, object_name)


@pytest.mark.parametrize("suffix", [".pdf", ".docx", ".pptx"])
async def test_knowledge_preview_rejects_large_object_despite_understated_metadata(
    test_client, admin_headers, knowledge_base_resource, postgres_sessions, suffix
):
    """持久元数据低估大小时，HTTP 仍拒绝实际超限源文件且不生成缓存。"""
    storage = get_minio_client()
    bucket = storage.KB_BUCKETS["documents"]
    kb_id = knowledge_base_resource["kb_id"]
    object_name = f"{kb_id}/upload/pytest-preview-{uuid4().hex}{suffix}"
    source = f"minio://{bucket}/{object_name}"
    content = b"x" * (MAX_FILE_PREVIEW_BYTES + 1)
    file_id = None
    try:
        await storage.aupload_file(bucket, object_name, content)
        added = await test_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/add",
            json={
                "items": [source],
                "params": {"content_hashes": {source: sha256(content).hexdigest()}},
            },
            headers=admin_headers,
        )
        assert added.status_code == 200, added.text
        added_payload = added.json()
        assert added_payload["status"] == "success", added_payload
        file_id = added_payload["items"][0]["file_id"]

        # 正常导入会读取实际大小，在数据库中构造旧版记录的低估值。
        async with postgres_sessions() as session:
            await session.execute(
                update(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id == file_id).values(file_size=0)
            )
            await session.commit()
        basic = await test_client.get(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{file_id}/basic", headers=admin_headers)
        assert basic.status_code == 200, basic.text
        assert basic.json()["meta"]["size"] == 0
        assert await storage.astat_file(bucket, object_name) == len(content)

        preview = await test_client.get("/api/workspace/knowledge/file", params={"kb_id": kb_id, "file_id": file_id}, headers=admin_headers)
        assert preview.status_code == 200, preview.text
        payload = preview.json()
        assert payload["content"] is None
        assert payload["preview_type"] == "unsupported"
        assert payload["supported"] is False
        assert payload["limit"] == MAX_FILE_PREVIEW_BYTES
        assert payload["message"] == "文件过大，当前仅支持 30 MB 以内的文件预览"
        assert await storage.astat_file(storage.KB_BUCKETS["parsed"], f"{kb_id}/preview/{file_id}.pdf") is None
        assert await storage.adownload_file(bucket, object_name) == content
    finally:
        if file_id:
            removed = await test_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{file_id}", headers=admin_headers)
            assert removed.status_code in {200, 404}, removed.text
        await storage.adelete_file(bucket, object_name)
