"""
Integration tests for the job management router.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from test.support.knowledge import default_embedding_model_spec

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.self_managed_resources
async def test_missing_embedding_failure_through_http_worker_and_postgres(test_client, admin_headers, postgres_sessions):
    """模拟历史缺失配置，经真实 HTTP 和 worker 后独立回读任务与文件事实。"""
    from sqlalchemy import delete, select, update

    from yuxi.modules.background_jobs.models import BackgroundJobRecord
    from yuxi.modules.knowledge.cache import delete_cached_kb_config
    from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeChunk, KnowledgeFile

    response = await test_client.post(
        "/api/knowledge/knowledge-bases",
        headers=admin_headers,
        json={
            "name": f"pytest_embedding_warning_{uuid.uuid4().hex[:8]}",
            "kb_type": "milvus",
            "description": "Missing embedding regression",
            "embedding_model_spec": default_embedding_model_spec(),
        },
    )
    assert response.status_code == 200, response.text
    kb_id = response.json()["kb_id"]
    file_id = f"file_{uuid.uuid4().hex}"
    job_id = None
    try:
        async with postgres_sessions() as session:
            await session.execute(update(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id).values(embedding_model_spec=None))
            session.add(KnowledgeFile(file_id=file_id, kb_id=kb_id, filename="embedding-check.txt", status="parsed", is_folder=False))
            await session.commit()
        await delete_cached_kb_config(kb_id)

        response = await test_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/index",
            headers=admin_headers,
            json={"file_ids": [file_id], "params": {}},
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]
        for _ in range(100):
            response = await test_client.get(f"/api/background-jobs/{job_id}", headers=admin_headers)
            assert response.status_code == 200, response.text
            job = response.json()["job"]
            if job["status"] in {"failed", "success", "cancelled"}:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Missing embedding job did not reach a terminal state")

        assert job["status"] == "failed", job
        assert "未配置 Embedding 模型" in job["error"]
        assert "未配置 Embedding 模型" in job["result"]["items"][0]["error"]
        async with postgres_sessions() as observer:
            persisted = await observer.get(BackgroundJobRecord, job_id)
            assert persisted.status == "failed" and persisted.completed_at is not None
            assert "未配置 Embedding 模型" in persisted.result["public_error"]
            file = await observer.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            assert file.status == "parsed" and file.chunk_count == 0
            assert await observer.scalar(select(KnowledgeChunk.id).where(KnowledgeChunk.file_id == file_id)) is None
    finally:
        response = await test_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=admin_headers)
        assert response.status_code == 200, response.text
        if job_id:
            async with postgres_sessions() as session:
                await session.execute(delete(BackgroundJobRecord).where(BackgroundJobRecord.id == job_id))
                await session.commit()


async def test_job_routes_require_admin(test_client, standard_user):
    """Non-admin users should be blocked from accessing job APIs."""
    headers = standard_user["headers"]

    list_response = await test_client.get("/api/background-jobs", headers=headers)
    assert list_response.status_code == 403

    detail_response = await test_client.get("/api/background-jobs/some-job", headers=headers)
    assert detail_response.status_code == 403

    cancel_response = await test_client.post("/api/background-jobs/some-job/cancel", headers=headers)
    assert cancel_response.status_code == 403


async def test_admin_can_list_jobs(test_client, admin_headers):
    """Admin should receive a well-formed job list payload."""
    response = await test_client.get("/api/background-jobs", headers=admin_headers)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert "jobs" in payload
    assert isinstance(payload["jobs"], list)
    assert "summary" in payload
    assert isinstance(payload["summary"], dict)


async def test_cancel_unknown_job_returns_client_error(test_client, admin_headers):
    """Cancelling a non-existent job should surface a 400 response."""
    response = await test_client.post("/api/background-jobs/not-real/cancel", headers=admin_headers)
    assert response.status_code == 400, response.text


async def test_enqueue_document_creates_job(
    test_client,
    admin_headers,
):
    """Trigger knowledge ingestion to ensure a job record is materialised."""
    create_response = await test_client.post(
        "/api/knowledge/knowledge-bases",
        json={
            "name": f"pytest_job_router_{uuid.uuid4().hex[:8]}",
            "description": "Job router integration test",
            "embedding_model_spec": default_embedding_model_spec(),
            "kb_type": "milvus",
            "additional_params": {},
        },
        headers=admin_headers,
    )
    assert create_response.status_code == 200, create_response.text
    kb_id = create_response.json()["kb_id"]

    try:
        upload_response = await test_client.post(
            "/api/knowledge/files/upload",
            params={"kb_id": kb_id},
            files={
                "file": (
                    f"pytest_job_{uuid.uuid4().hex[:8]}.txt",
                    b"job router integration test",
                    "text/plain",
                )
            },
            headers=admin_headers,
        )
        assert upload_response.status_code == 200, upload_response.text
        upload_payload = upload_response.json()
        file_path = upload_payload["file_path"]

        enqueue_response = await test_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents",
            json={
                "items": [file_path],
                "params": {
                    "content_type": "file",
                    "content_hashes": {file_path: upload_payload["content_hash"]},
                    "file_sizes": {file_path: upload_payload["size"]},
                },
            },
            headers=admin_headers,
        )
        assert enqueue_response.status_code == 200, enqueue_response.text

        enqueue_payload = enqueue_response.json()
        assert enqueue_payload.get("status") == "queued"
        job_id = enqueue_payload.get("job_id")
        assert job_id, "Knowledge ingestion did not return a job_id"

        # The job should be queryable immediately after enqueueing.
        detail_response = await test_client.get(f"/api/background-jobs/{job_id}", headers=admin_headers)
        assert detail_response.status_code == 200, detail_response.text
        detail_payload = detail_response.json().get("job", {})
        assert detail_payload.get("id") == job_id
        assert detail_payload.get("status") in {"queued", "pending", "running", "failed", "success", "cancelled"}

        # Ensure the job surfaces in the list endpoint within a short window.
        for _ in range(10):
            list_response = await test_client.get("/api/background-jobs", headers=admin_headers)
            assert list_response.status_code == 200, list_response.text
            all_jobs = list_response.json().get("jobs", [])
            if any(entry.get("id") == job_id for entry in all_jobs):
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail("Job did not appear in list endpoint within timeout window")

        # Poll for successful worker completion, then independently read the persisted file result.
        detail_payload = {}
        for _ in range(120):
            detail_response = await test_client.get(f"/api/background-jobs/{job_id}", headers=admin_headers)
            assert detail_response.status_code == 200, detail_response.text
            detail_payload = detail_response.json().get("job", {})
            job_status = detail_payload.get("status")
            if job_status in {"success", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail("Job did not reach a terminal status within timeout window")

        assert job_status == "success", detail_payload
        result = detail_payload.get("result") or {}
        assert result.get("failed") == 0, result
        assert len(result.get("items") or []) == 1, result
        file_id = result["items"][0].get("file_id")
        assert file_id, result

        file_response = await test_client.get(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/{file_id}/basic",
            headers=admin_headers,
        )
        assert file_response.status_code == 200, file_response.text
        file_payload = file_response.json()
        file_meta = file_payload.get("meta") or file_payload
        assert file_meta.get("file_id") == file_id
        assert file_meta.get("status") == "parsed", file_payload
        assert file_meta.get("markdown_file"), file_payload
    finally:
        await test_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=admin_headers)


async def test_cancel_http_records_intent_before_execution_confirmation(test_client, admin_headers):
    """取消回执只登记请求，执行方确认之后详情才显示取消终态。"""
    from datetime import timedelta

    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.modules.background_jobs.models import BackgroundJobRecord
    from yuxi.modules.background_jobs.repository import BackgroundJobRepository
    from yuxi.shared.datetime import utc_now

    job_id = uuid.uuid4().hex
    owner = "pytest-http-cancel-owner"
    now = utc_now()
    async with pg_manager.get_async_session_context() as session:
        session.add(
            BackgroundJobRecord(
                id=job_id,
                name="pytest cooperative cancel",
                type="knowledge_parse",
                status="running",
                payload={"secret": "private-input"},
                result={
                    "dataset_id": "dataset-ref",
                    "build_metadata": {"params": {"secret": "private-input"}},
                    "items": [
                        {
                            "file_id": "file-ref",
                            "status": "failed",
                            "error": "private-input",
                            "minio_url": "private-input",
                            "markdown_content": "private-input",
                        }
                    ]
                    * 250,
                },
                worker_id=owner,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=120),
                started_at=now,
            )
        )
    repo = BackgroundJobRepository()
    try:
        response = await test_client.post(f"/api/background-jobs/{job_id}/cancel", headers=admin_headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"job_id": job_id, "status": "running", "cancel_requested": True}
        persisted = await repo.get_by_id(job_id)
        assert persisted.status == "running" and persisted.cancel_requested
        assert persisted.completed_at is None
        detail = (await test_client.get(f"/api/background-jobs/{job_id}", headers=admin_headers)).json()["job"]
        assert "payload" not in detail and "worker_id" not in detail
        assert detail["status"] == "running"
        assert "private-input" not in str(detail)
        assert detail["result"]["dataset_id"] == "dataset-ref"
        assert len(detail["result"]["items"]) == 200
        assert detail["result"]["result_truncated"] is True
        assert (await repo.get_by_id(job_id)).result["build_metadata"]["params"]["secret"] == "private-input"
        assert await repo.finish_owned(job_id, worker_id=owner, status="cancelled", message="任务已取消")
        detail = (await test_client.get(f"/api/background-jobs/{job_id}", headers=admin_headers)).json()["job"]
        assert detail["status"] == "cancelled" and detail["completed_at"]
    finally:
        if not await repo.delete_terminal(job_id):
            await repo.release_interrupted_owner(job_id, worker_id=owner, error="pytest cleanup")
            await repo.delete_terminal(job_id)
        await pg_manager.close()


async def test_job_api_has_no_public_status_reporting(test_client, admin_headers):
    """管理端也不能通过 HTTP 伪造任务进度或业务终态。"""
    for path in ("/api/background-jobs", "/api/background-jobs/job-id/progress", "/api/background-jobs/job-id/finish"):
        response = await test_client.post(path, headers=admin_headers, json={"status": "success", "progress": 100})
        assert response.status_code in {404, 405}, response.text
