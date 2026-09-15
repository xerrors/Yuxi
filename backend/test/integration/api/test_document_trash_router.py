"""Real HTTP/RBAC/database recycle-bin lifecycle (isolated live service required)."""

import os

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile


@pytest.mark.asyncio
async def test_trash_http_lifecycle(test_client, admin_headers, standard_user):
    me = await test_client.get("/api/auth/me", headers=admin_headers)
    assert me.status_code == 200
    uid = me.json()["uid"]
    kb_id, file_id = "trash_" + uuid4().hex, uuid4().hex
    engine = create_async_engine(os.environ["POSTGRES_URL"], poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        session.add(
            KnowledgeBase(
                kb_id=kb_id,
                name="Recycle bin HTTP validation",
                kb_type="milvus",
                created_by=uid,
                additional_params={},
                share_config={"version": 2, "read_scope": None, "manage_scope": None},
            )
        )
        await session.flush()
        session.add(
            KnowledgeFile(
                kb_id=kb_id,
                file_id=file_id,
                filename="合同验收.pdf",
                status="uploaded",
                file_type="file",
                created_by=uid,
            )
        )
    base = f"/api/knowledge/databases/{kb_id}"
    try:
        anonymous = await test_client.get(base + "/trash")
        assert anonymous.status_code in (401, 403)
        forbidden = await test_client.get(base + "/trash", headers=standard_user["headers"])
        assert forbidden.status_code in (403, 404)
        response = await test_client.delete(base + f"/documents/{file_id}", headers=admin_headers)
        assert response.status_code == 200, response.text
        response = await test_client.get(base + "/trash?offset=0&limit=10", headers=admin_headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 1
        item = payload["items"][0]
        assert item["file_id"] == file_id and item["status"] == "trashed"
        assert (datetime.fromisoformat(item["purge_after"]) - datetime.fromisoformat(item["deleted_at"])).days == 30
        response = await test_client.get(base + f"/documents/{file_id}/download", headers=admin_headers)
        assert response.status_code >= 400
        forbidden = await test_client.post(base + f"/trash/{file_id}/restore", headers=standard_user["headers"])
        assert forbidden.status_code in (403, 404)
        response = await test_client.delete(base, headers=admin_headers)
        assert response.status_code == 409
        response = await test_client.post(base + f"/trash/{file_id}/restore", headers=admin_headers)
        assert response.status_code == 200, response.text
        assert response.json()["restored_count"] == 1
        assert (await test_client.get(base + "/trash", headers=admin_headers)).json()["total"] == 0
        async with sessions.begin() as session:
            await session.execute(
                update(KnowledgeFile)
                .where(KnowledgeFile.file_id == file_id)
                .values(status="parsing", processing_task_id="task")
            )
        response = await test_client.delete(base + f"/documents/{file_id}", headers=admin_headers)
        assert response.status_code == 409
        async with sessions.begin() as session:
            await session.execute(
                update(KnowledgeFile)
                .where(KnowledgeFile.file_id == file_id)
                .values(status="uploaded", processing_task_id=None)
            )
        assert (await test_client.delete(base + f"/documents/{file_id}", headers=admin_headers)).status_code == 200
        async with sessions.begin() as session:
            await session.execute(
                update(KnowledgeFile)
                .where(KnowledgeFile.file_id == file_id)
                .values(
                    deleted_at=datetime.now(UTC) - timedelta(days=31), purge_after=datetime.now(UTC) - timedelta(days=1)
                )
            )
        response = await test_client.post(base + f"/trash/{file_id}/restore", headers=admin_headers)
        assert response.status_code == 409
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))

        await engine.dispose()
