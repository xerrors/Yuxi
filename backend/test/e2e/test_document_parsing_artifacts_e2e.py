"""解析资源通过真实 HTTP、worker、数据库及文件/对象边界闭合。"""

from __future__ import annotations

import asyncio
import io
import json
import threading
import zipfile
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import uuid4

import pytest
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import update

from test.live_api_cleanup import make_test_session_title
from test.e2e.e2e_helpers import archive_public_thread, cleanup_uploaded_chat_attachment, wait_for_consumed_input
from yuxi.infrastructure.postgres.models import load_models
from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.infrastructure.redis import close_async_redis_client
from yuxi.modules.agents.models.attachments import AgentAttachment
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.system.models import ConfigOption
from yuxi.modules.system.repositories.options import get_option
from yuxi.modules.system.services.options import invalidate_option_cache
from yuxi.modules.workspace.filesystem import Workspace

load_models()

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest.fixture(autouse=True)
async def parser_test_connections():
    """在当前测试事件循环内创建并关闭直接回读所用连接池。"""
    pg_manager.initialize()
    try:
        yield
    finally:
        await close_async_redis_client()
        await pg_manager.close()


def _result_archive() -> tuple[bytes, bytes]:
    """生成固定正文与可独立回读的真实 PNG。"""
    image = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(image, format="PNG")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("markdown.md", "# Artifact replay\n\n![chart](images/chart.png)")
        output.writestr("images/chart.png", image.getvalue())
    return archive.getvalue(), image.getvalue()


@pytest.fixture
async def mineru_replay():
    """在真实 HTTP 边界回放 4.1 Tasks，供 API 与 worker 共同访问。"""
    archive, _ = _result_archive()
    tasks = {}

    class MinerUReplay(BaseHTTPRequestHandler):
        """固定远端协议独立拥有请求记录与已上传字节。"""

        def respond(self, body, status=200, content_type="application/json"):
            """返回有明确长度的响应，避免连接关闭语义掩盖协议错误。"""
            data = json.dumps(body).encode() if content_type == "application/json" else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            """创建任务只返回身份，不返回同步产物。"""
            assert self.path == "/v1/tasks"
            manifest = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            task_id = uuid4().hex
            tasks[task_id] = {"manifest": manifest, "uploaded": None}
            self.respond({"task_id": task_id, "status": "waiting-file", "files": [{"file_id": task_id}]}, 202)

        def do_PUT(self):
            """保存原始文件内容，供测试与输入源独立比对。"""
            task_id = self.path.rsplit("/", 1)[1]
            tasks[task_id]["uploaded"] = self.rfile.read(int(self.headers["Content-Length"]))
            self.respond({})

        def do_GET(self):
            """查询前后状态与下载端点遵循异步 Tasks 契约。"""
            task_id = self.path.rsplit("/", 1)[1]
            if self.path.startswith("/result/"):
                self.respond(archive, content_type="application/zip")
                return
            task = tasks[task_id]
            base = f"http://{self.headers['Host']}"
            status = "completed" if task["uploaded"] is not None else "waiting-file"
            file = {"file_id": task_id, "status": status}
            if status == "completed":
                file["result_url"] = f"{base}/result/{task_id}"
            else:
                file["upload"] = {"url": f"{base}/upload/{task_id}", "headers": {}}
            self.respond({"task_id": task_id, "status": status, "files": [file]})

        def log_message(self, *args):
            """测试不记录 HTTP 访问日志。"""

    replay = ThreadingHTTPServer(("0.0.0.0", 0), MinerUReplay)
    threading.Thread(target=replay.serve_forever, daemon=True).start()
    try:
        yield replay, tasks
    finally:
        await asyncio.to_thread(replay.shutdown)
        replay.server_close()


@pytest.mark.parametrize("tier", ["standard", "advanced"])
async def test_mineru_options_reach_draft_parser_and_preserve_artifacts(e2e_client, e2e_headers, e2e_agent_context, tier, mineru_replay):
    """真实设置与解析入口证明默认参数到达引擎，正文和图片完整落盘。"""
    _, image = _result_archive()
    replay, requests_received = mineru_replay
    async with pg_manager.get_async_session_context() as db:
        previous = await get_option(db, "mineru_ocr_host_opts")
        previous_state = {"value": deepcopy(previous.value), "updated_by": previous.updated_by, "updated_at": previous.updated_at}
    uploaded_id = None
    try:
        configured = await e2e_client.put(
            "/api/system/config/options/mineru_ocr_host_opts",
            json={"value": {"server_url": f"http://127.0.0.1:{replay.server_port}", "tier": tier}},
            headers=e2e_headers,
        )
        assert configured.status_code == 200, configured.text
        source = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(source)
        uploaded = await e2e_client.post(
            "/api/v1/agents/files", files={"file": ("source.pdf", source.getvalue(), "application/pdf")}, headers=e2e_headers
        )
        assert uploaded.status_code == 201, uploaded.text
        uploaded_id = uploaded.json()["id"]
        parsed = await e2e_client.post(f"/api/agent/files/{uploaded_id}/parse", json={"parse_method": "mineru_ocr"}, headers=e2e_headers)
        assert parsed.status_code == 200, parsed.text
        assert len(requests_received) == 1
        [received] = requests_received.values()
        assert received["manifest"]["tier"] == tier
        assert received["manifest"]["output_formats"] == ["default"]
        assert received["uploaded"] == source.getvalue()
        async with pg_manager.get_async_session_context() as db:
            draft = await db.get(AgentAttachment, uploaded_id)
            assert draft.status == "draft" and draft.parsed_source
            workspace = Workspace(e2e_agent_context["uid"])
            markdown = workspace.read_authorized_file(f"/{draft.parsed_source}", 1024).decode()
            image_path = PurePosixPath(draft.parsed_source).parent / "images/images/chart.png"
            assert "![chart](images/images/chart.png)" in markdown
            assert workspace.read_authorized_file(f"/{image_path}", 1024) == image
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(update(ConfigOption).where(ConfigOption.key == "mineru_ocr_host_opts").values(**previous_state))
        await invalidate_option_cache("mineru_ocr_host_opts")
        if uploaded_id:
            deleted = await e2e_client.delete(f"/api/v1/agents/files/{uploaded_id}", headers=e2e_headers)
            assert deleted.status_code == 200 and deleted.json()["deleted"] is True, deleted.text
            async with pg_manager.get_async_session_context() as db:
                assert await db.get(AgentAttachment, uploaded_id) is None


async def test_private_draft_parse_send_and_delete_preserve_complete_local_directory(
    e2e_client,
    e2e_headers,
    e2e_agent_context,
    mineru_replay,
):
    """真实入口确认完整本地目录；资源不进入 MinIO，删除覆盖全部文件。"""
    _, image = _result_archive()
    replay, _ = mineru_replay
    thread_id = None
    turn_id = None
    uploaded = None
    attachment = None
    uid = e2e_agent_context["uid"]
    client = get_minio_client()
    previous = await e2e_client.get("/api/system/config/options", headers=e2e_headers)
    assert previous.status_code == 200, previous.text
    previous_value = next(item["value"] for item in previous.json()["options"] if item["key"] == "mineru_ocr_host_opts")
    await asyncio.to_thread(client.ensure_bucket_exists, client.KB_BUCKETS["images"])
    image_objects_before = await client.alist_object_metadata(client.KB_BUCKETS["images"], "unknown/")
    try:
        configured = await e2e_client.put(
            "/api/system/config/options/mineru_ocr_host_opts",
            json={"value": {"server_url": f"http://127.0.0.1:{replay.server_port}"}},
            headers=e2e_headers,
        )
        assert configured.status_code == 200, configured.text
        created = await e2e_client.post(
            "/api/v1/agents/sessions",
            json={"agent_id": e2e_agent_context["agent_slug"], "title": make_test_session_title("parser-folder")},
            headers={**e2e_headers, "Idempotency-Key": uuid4().hex},
        )
        assert created.status_code == 201, created.text
        thread_id = created.json()["id"]
        source = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(source)
        uploaded_response = await e2e_client.post(
            "/api/v1/agents/files",
            files={"file": ("source.pdf", source.getvalue(), "application/pdf")},
            headers=e2e_headers,
        )
        assert uploaded_response.status_code == 201, uploaded_response.text
        uploaded = uploaded_response.json()
        parsed = await e2e_client.post(
            f"/api/agent/files/{uploaded['id']}/parse",
            json={"parse_method": "mineru_ocr"},
            headers=e2e_headers,
        )
        assert parsed.status_code == 200, parsed.text
        assert parsed.json()["status"] == "parsed"
        async with pg_manager.get_async_session_context() as db:
            draft = await db.get(AgentAttachment, uploaded["id"])
            assert draft.uid == str(uid) and draft.status == "draft" and draft.input_id is None
            parsed_name = draft.parsed_source
        temporary_directory = PurePosixPath(parsed_name).parent
        workspace = Workspace(uid)
        assert workspace.read_authorized_file(f"/{temporary_directory}/images/images/chart.png", 1024) == image
        assert await client.alist_object_metadata(client.KB_BUCKETS["documents"], parsed_name) == []
        assert await client.alist_object_metadata(client.KB_BUCKETS["images"], "unknown/") == image_objects_before
        accepted = await e2e_client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            json={
                "events": [
                    {
                        "type": "agent.session.input.message",
                        "input": [
                            {
                                "role": "user",
                                "content": [{"type": "input_text", "text": "保留完整文档资源"}],
                            }
                        ],
                        "yuxi": {"mode": "follow_up", "attachment_file_ids": [uploaded["id"]]},
                    }
                ]
            },
            headers={**e2e_headers, "Idempotency-Key": uuid4().hex},
        )
        assert accepted.status_code == 202, accepted.text
        consumed = await wait_for_consumed_input(e2e_client, e2e_headers, accepted.json())
        turn_id = consumed["turn_id"]
        await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
        references = await e2e_client.get(f"/api/v1/agents/sessions/{thread_id}/attachments", headers=e2e_headers)
        assert references.status_code == 200, references.text
        [attachment] = references.json()["attachments"]
        markdown_response = await e2e_client.get(attachment["artifact_url"], headers=e2e_headers)
        assert markdown_response.status_code == 200, markdown_response.text
        assert "![chart](images/images/chart.png)" in markdown_response.text
        image_url = attachment["artifact_url"].rsplit("/", 1)[0] + "/images/images/chart.png"
        image_response = await e2e_client.get(image_url, headers=e2e_headers)
        assert image_response.status_code == 200, image_response.text
        assert image_response.content == image
        anonymous = await e2e_client.get(image_url)
        assert anonymous.status_code == 401
        assert (await e2e_client.get(f"/api/v1/agents/files/{uploaded['id']}", headers=e2e_headers)).status_code == 409
        async with pg_manager.get_async_session_context() as db:
            stored = await db.get(AgentAttachment, uploaded["id"])
            assert stored.status == "ready" and stored.input_id == accepted.json()["input_id"]
            assert stored.message_id is not None
            assert stored.path == attachment["path"]
            assert stored.object_name is None and stored.parsed_source is None
        deleted = await e2e_client.delete(
            f"/api/v1/agents/sessions/{thread_id}/attachments/{attachment['file_id']}",
            headers=e2e_headers,
        )
        assert deleted.status_code == 200, deleted.text
        assert (await e2e_client.get(image_url, headers=e2e_headers)).status_code == 404
        assert (await e2e_client.get(attachment["artifact_url"], headers=e2e_headers)).status_code == 404
        async with pg_manager.get_async_session_context() as db:
            assert await db.get(AgentAttachment, uploaded["id"]) is None
        attachment = None
    finally:
        restored = await e2e_client.put(
            "/api/system/config/options/mineru_ocr_host_opts",
            json={"value": previous_value},
            headers=e2e_headers,
        )
        assert restored.status_code == 200, restored.text
        if thread_id:
            await archive_public_thread(e2e_client, e2e_headers, thread_id, turn_id=turn_id)
        if thread_id and attachment:
            await e2e_client.delete(f"/api/v1/agents/sessions/{thread_id}/attachments/{attachment['file_id']}", headers=e2e_headers)
        if uploaded:
            await cleanup_uploaded_chat_attachment(uid, uploaded["id"])


async def test_knowledge_worker_hosts_document_resources_and_file_delete_reclaims_objects(
    e2e_client,
    e2e_headers,
    monkeypatch,
    mineru_replay,
):
    """真实 worker 发布资源，PG 拥有结果，删除单个文档不影响其他文档。"""
    _, image = _result_archive()
    replay, received = mineru_replay
    source = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(source)
    async with pg_manager.get_async_session_context() as db:
        previous = await get_option(db, "mineru_ocr_host_opts")
        previous_state = {"value": deepcopy(previous.value), "updated_by": previous.updated_by, "updated_at": previous.updated_at}
    created = await e2e_client.post(
        "/api/knowledge/knowledge-bases",
        json={
            "name": f"pytest_parser_{uuid4().hex}",
            "description": "Parser artifact E2E",
            "kb_type": "milvus",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "additional_params": {},
        },
        headers=e2e_headers,
    )
    assert created.status_code == 200, created.text
    kb_id = created.json()["kb_id"]
    client = get_minio_client()
    try:
        configured = await e2e_client.put(
            "/api/system/config/options/mineru_ocr_host_opts",
            json={"value": {"server_url": f"http://api:{replay.server_port}", "tier": "standard"}},
            headers=e2e_headers,
        )
        assert configured.status_code == 200, configured.text
        uploaded = await e2e_client.post(
            f"/api/knowledge/files/upload?kb_id={kb_id}",
            files={"file": ("source.pdf", source.getvalue(), "application/pdf")},
            headers=e2e_headers,
        )
        assert uploaded.status_code == 200, uploaded.text
        info = uploaded.json()
        added = await e2e_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/add",
            json={
                "items": [info["file_path"]],
                "params": {
                    "content_hashes": {info["file_path"]: info["content_hash"]},
                    "file_sizes": {info["file_path"]: info["size"]},
                },
            },
            headers=e2e_headers,
        )
        assert added.status_code == 200, added.text
        file_id = added.json()["items"][0]["file_id"]
        parsing = await e2e_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/parse",
            json={"file_ids": [file_id], "params": {"ocr_engine": "mineru_ocr"}},
            headers=e2e_headers,
        )
        assert parsing.status_code == 200, parsing.text
        job_id = parsing.json()["job_id"]
        for _ in range(100):
            job_response = await e2e_client.get(f"/api/background-jobs/{job_id}", headers=e2e_headers)
            assert job_response.status_code == 200, job_response.text
            job = job_response.json()["job"]
            if job["status"] in {"success", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.2)
        assert job["status"] == "success", job
        record = await KnowledgeFileRepository().get_by_file_id(file_id)
        assert record.status == "parsed"
        [task] = received.values()
        assert task["manifest"]["tier"] == "standard"
        assert task["uploaded"] == source.getvalue()
        from yuxi.infrastructure.minio.object_urls import parse_minio_url

        bucket, markdown_name = parse_minio_url(record.markdown_file)
        assert markdown_name.startswith(f"{kb_id}/parsed/{file_id}/")
        markdown = (await client.adownload_file(bucket, markdown_name)).decode()
        image_objects = await client.alist_object_metadata(client.KB_BUCKETS["images"], f"{kb_id}/kb-images/{file_id}/")
        assert len(image_objects) == 1
        image_name = image_objects[0]["object_name"]
        assert await client.adownload_file(client.KB_BUCKETS["images"], image_name) == image
        relative_image = image_name.split("/", 1)[1]
        assert f"/api/knowledge/knowledge-bases/{kb_id}/images/{quote(relative_image, safe='/')}" in markdown
        # 上传已完成但 owning PG 更新失去 owner 时，只回收本次 attempt。
        from yuxi.modules.documents import service as document_service
        from yuxi.modules.knowledge.implementations.milvus import MilvusKB

        from datetime import timedelta
        from sqlalchemy import update
        from yuxi.modules.background_jobs.models import BackgroundJobRecord
        from yuxi.shared.datetime import utc_now

        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(BackgroundJobRecord)
                .where(BackgroundJobRecord.id == job_id)
                .values(status="running", worker_id="test-owner", lease_expires_at=utc_now() + timedelta(minutes=5))
            )
        repository = KnowledgeFileRepository()
        await repository.update_fields(
            file_id=file_id,
            kb_id=kb_id,
            data={"status": "uploaded", "processing_job_id": job_id, "processing_owner": "test-owner"},
        )
        real_hosted_parse = document_service.parse_to_hosted_markdown

        async def lose_owner_after_upload(*args, **kwargs):
            content = await real_hosted_parse(*args, **kwargs)
            await repository.update_fields(file_id=file_id, kb_id=kb_id, data={"processing_owner": "other-owner"})
            return content

        with monkeypatch.context() as patch:
            patch.setattr(document_service, "parse_to_hosted_markdown", lose_owner_after_upload)
            with pytest.raises(asyncio.CancelledError):
                await MilvusKB.__new__(MilvusKB).parse_file(
                    kb_id, file_id, additional_params={"ocr_engine": "mineru_ocr"}, processing_job_id=job_id, processing_owner="test-owner"
                )
        assert await client.adownload_file(bucket, markdown_name)
        assert await client.adownload_file(client.KB_BUCKETS["images"], image_name) == image
        assert len(await client.alist_object_metadata(client.KB_BUCKETS["images"], f"{kb_id}/kb-images/{file_id}/")) == 1
        assert len(await client.alist_object_metadata(client.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{file_id}/")) == 1
        record = await repository.get_by_file_id(file_id)
        assert record.processing_owner == "other-owner"
        assert record.markdown_file.endswith(markdown_name)

        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(BackgroundJobRecord)
                .where(BackgroundJobRecord.id == job_id)
                .values(status="success", worker_id=None, lease_expires_at=None)
            )
        # 现有重试入口接收 uploaded/error_parsing；新结果提交后回收先前产物。
        await repository.update_fields(
            file_id=file_id,
            kb_id=kb_id,
            data={"status": "uploaded", "processing_owner": None, "processing_job_id": None},
        )
        retry = await e2e_client.post(
            f"/api/knowledge/knowledge-bases/{kb_id}/documents/parse",
            json={"file_ids": [file_id], "params": {"ocr_engine": "mineru_ocr"}},
            headers=e2e_headers,
        )
        assert retry.status_code == 200, retry.text
        for _ in range(100):
            response = await e2e_client.get(f"/api/background-jobs/{retry.json()['job_id']}", headers=e2e_headers)
            retry_job = response.json()["job"]
            if retry_job["status"] in {"success", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.2)
        assert retry_job["status"] == "success", retry_job
        replacement = await repository.get_by_file_id(file_id)
        assert replacement.status == "parsed"
        assert replacement.markdown_file != record.markdown_file
        assert await client.alist_object_metadata(bucket, markdown_name) == []
        assert await client.alist_object_metadata(client.KB_BUCKETS["images"], image_name) == []
        assert len(await client.alist_object_metadata(client.KB_BUCKETS["images"], f"{kb_id}/kb-images/{file_id}/")) == 1
        # 单文件清理的负控：其他文件的对象不能被知识库级前缀误删。
        other_name = f"{kb_id}/kb-images/other-file/attempt/images/chart.png"
        await client.aupload_file(bucket_name=client.KB_BUCKETS["images"], object_name=other_name, data=image)
        deleted = await e2e_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}/documents/{file_id}", headers=e2e_headers)
        assert deleted.status_code == 200, deleted.text
        assert await KnowledgeFileRepository().get_by_file_id(file_id) is None
        # 删除先提交 tombstone；真实 worker 异步回收对象，读取最终产物确认完成。
        for _ in range(200):
            images = await client.alist_object_metadata(client.KB_BUCKETS["images"], f"{kb_id}/kb-images/{file_id}/")
            parsed = await client.alist_object_metadata(client.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{file_id}/")
            if not images and not parsed:
                break
            await asyncio.sleep(0.2)
        assert images == []
        assert parsed == []
        assert await client.adownload_file(client.KB_BUCKETS["images"], other_name) == image
    finally:
        async with pg_manager.get_async_session_context() as db:
            await db.execute(update(ConfigOption).where(ConfigOption.key == "mineru_ocr_host_opts").values(**previous_state))
        await invalidate_option_cache("mineru_ocr_host_opts")
        deleted = await e2e_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=e2e_headers)
        assert deleted.status_code == 200, deleted.text
