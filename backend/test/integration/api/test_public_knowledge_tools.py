"""Knowledge Public 工具入口的真实 HTTP 权限与调用契约。"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from yuxi.storage.minio import get_minio_client
from yuxi.storage.minio.client import MinIOClient
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def readable_knowledge_tool_data(knowledge_database):
    """在真实 PostgreSQL 与 MinIO 中准备导图和可读文档。"""
    kb_id = knowledge_database["kb_id"]
    file_id = f"pytest_tool_{uuid.uuid4().hex[:12]}"
    object_name = f"{kb_id}/parsed/{file_id}.md"
    minio = get_minio_client()
    uploaded = await minio.aupload_file(
        MinIOClient.KB_BUCKETS["parsed"],
        object_name,
        b"First line\nNeedle in document\nLast line",
        content_type="text/markdown",
    )
    pg_manager.initialize()
    try:
        async with pg_manager.get_async_session_context() as session:
            kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            assert kb is not None
            kb.mindmap = {"content": "Root", "children": [{"content": "Documents", "children": []}]}
            session.add(
                KnowledgeFile(
                    file_id=file_id,
                    kb_id=kb_id,
                    filename="tool-proof.md",
                    file_type="md",
                    markdown_file=uploaded.url,
                    status="parsed",
                    is_folder=False,
                )
            )
        yield file_id
    finally:
        await minio.adelete_file(MinIOClient.KB_BUCKETS["parsed"], object_name)
        await pg_manager.close()


async def test_jwt_can_call_six_read_only_knowledge_tools(test_client, admin_headers, knowledge_database):
    """普通用户 JWT 可调用六个查询工具，缺失资源不会泄露。"""
    kb_id = knowledge_database["kb_id"]
    prefix = "/api/v1/knowledge/tools"
    unauthenticated = await test_client.get(f"{prefix}/list_kbs")
    assert unauthenticated.status_code == 401, unauthenticated.text

    listed = await test_client.get(f"{prefix}/list_kbs", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert any(item["kb_id"] == kb_id for item in listed.json())

    queried = await test_client.post(
        f"{prefix}/query_kb",
        json={"kb_id": kb_id, "query_text": "hello"},
        headers=admin_headers,
    )
    assert queried.status_code == 200, queried.text
    assert queried.json()["kb_id"] == kb_id

    searched = await test_client.post(
        f"{prefix}/search_file",
        json={"query": "missing-needle"},
        headers=admin_headers,
    )
    assert searched.status_code == 200, searched.text
    assert isinstance(searched.json()["files"], list)

    for name, payload, expected in (
        ("get_mindmap", {"kb_name": "missing-kb"}, 404),
        ("open_kb_document", {"kb_id": kb_id, "file_id": "missing-file"}, 400),
        ("find_kb_document", {"kb_id": kb_id, "file_id": "missing-file", "patterns": ["hello"]}, 400),
    ):
        response = await test_client.post(f"{prefix}/{name}", json=payload, headers=admin_headers)
        assert response.status_code == expected, (name, response.text)


async def test_document_tools_return_persisted_results(
    test_client, admin_headers, knowledge_database, readable_knowledge_tool_data
):
    """三项工具经真实 HTTP 回读 PostgreSQL 导图和 MinIO 文档内容。"""
    kb_id = knowledge_database["kb_id"]
    file_id = readable_knowledge_tool_data
    prefix = "/api/v1/knowledge/tools"

    mindmap = await test_client.post(
        f"{prefix}/get_mindmap",
        json={"kb_name": knowledge_database["name"]},
        headers=admin_headers,
    )
    assert mindmap.status_code == 200, mindmap.text
    assert "- Root\n  - Documents" in mindmap.json()

    opened = await test_client.post(
        f"{prefix}/open_kb_document",
        json={"kb_id": kb_id, "file_id": file_id},
        headers=admin_headers,
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["file_id"] == file_id
    assert opened.json()["total_lines"] == 3
    assert "Needle in document" in opened.json()["content"]

    found = await test_client.post(
        f"{prefix}/find_kb_document",
        json={"kb_id": kb_id, "file_id": file_id, "patterns": ["Needle"]},
        headers=admin_headers,
    )
    assert found.status_code == 200, found.text
    assert found.json()["file_id"] == file_id
    assert found.json()["total_matches"] == 1
    assert "Needle in document" in found.json()["windows"][0]["content"]

    invalid_regex = await test_client.post(
        f"{prefix}/find_kb_document",
        json={"kb_id": kb_id, "file_id": file_id, "patterns": ["["], "use_regex": True},
        headers=admin_headers,
    )
    assert invalid_regex.status_code == 400, invalid_regex.text
    assert "无效正则表达式" in invalid_regex.json()["detail"]


async def test_knowledge_key_can_call_tools_but_not_unlisted_operations(test_client, admin_headers, knowledge_database):
    """受限 Key 只进入列明的只读工具，下载与管理保持拒绝。"""
    created = await test_client.post(
        "/api/user/apikey/",
        json={"request_id": str(uuid.uuid4()), "name": "Knowledge tools", "access_level": "knowledge"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    key_id = created.json()["api_key"]["id"]
    key_headers = {"Authorization": f"Bearer {created.json()['secret']}"}
    kb_id = knowledge_database["kb_id"]
    try:
        listed = await test_client.get("/api/v1/knowledge/tools/list_kbs", headers=key_headers)
        assert listed.status_code == 200, listed.text
        assert any(item["kb_id"] == kb_id for item in listed.json())

        queried = await test_client.post(
            "/api/v1/knowledge/tools/query_kb",
            json={"kb_id": kb_id, "query_text": "hello"},
            headers=key_headers,
        )
        assert queried.status_code == 200, queried.text

        for name, payload, expected in (
            ("get_mindmap", {"kb_name": "missing-kb"}, 404),
            ("open_kb_document", {"kb_id": kb_id, "file_id": "missing-file"}, 400),
            ("find_kb_document", {"kb_id": kb_id, "file_id": "missing-file", "patterns": ["hello"]}, 400),
            ("search_file", {"query": "missing-needle"}, 200),
        ):
            response = await test_client.post(f"/api/v1/knowledge/tools/{name}", json=payload, headers=key_headers)
            assert response.status_code == expected, (name, response.text)

        blocked = await test_client.post(
            "/api/v1/knowledge/tools/download_kb_file",
            json={"kb_id": kb_id, "file_id": "missing"},
            headers=key_headers,
        )
        assert blocked.status_code == 404, blocked.text
        blocked = await test_client.get("/api/knowledge/databases", headers=key_headers)
        assert blocked.status_code == 403, blocked.text
    finally:
        await test_client.delete(f"/api/user/apikey/{key_id}", headers=admin_headers)


async def test_tool_query_hides_invisible_knowledge_base(test_client, admin_headers, standard_user):
    """资源 ID 即便已知，另一用户也无法借工具查询其内容。"""
    owner = await test_client.get("/api/auth/me", headers=admin_headers)
    assert owner.status_code == 200, owner.text
    created = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": f"pytest_private_tool_{uuid.uuid4().hex[:8]}",
            "description": "private tool test",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "milvus",
            "additional_params": {},
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "user", "department_ids": [], "user_uids": [owner.json()["uid"]]},
                "manage_scope": None,
            },
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    kb_id = created.json()["kb_id"]
    try:
        response = await test_client.post(
            "/api/v1/knowledge/tools/query_kb",
            json={"kb_id": kb_id, "query_text": "hello"},
            headers=standard_user["headers"],
        )
        assert response.status_code == 404, response.text
    finally:
        deleted = await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
