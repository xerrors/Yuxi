"""真实 PG/MinIO/Milvus/ffmpeg 的媒体发布、范围、时码与失败回收。"""

import asyncio
import base64
import hashlib
import io
import math
import struct
import subprocess
import sys
import wave
from datetime import timedelta
from functools import partial
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from test.integration.support.postgres_schema import create_isolated_manager, drop_isolated_schema
from yuxi.infrastructure.minio.client import MinIOClient
from yuxi.migrations.schema import create_business_tables, create_knowledge_tables
from yuxi.modules.background_jobs import repository as job_repository
from yuxi.modules.background_jobs.models import BackgroundJobRecord
from yuxi.modules.identity.models import User
from yuxi.modules.knowledge import base as knowledge_base_module
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.media import jobs, repository, service
from yuxi.modules.knowledge.media.processing import prepare_media
from yuxi.modules.knowledge.media.schemas import IndexMediaInput
from yuxi.modules.knowledge.media.vectors import cleanup_media_projection
from yuxi.modules.knowledge.models import KnowledgeBase, KnowledgeDocumentRevision, KnowledgeFile
from yuxi.modules.knowledge.repositories import files as file_repository
from yuxi.modules.knowledge.repositories import projections
from yuxi.modules.knowledge.services import documents, projection_cleanup, revision_cleanup, revisions
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import InvalidInputError, InvalidOperationError, ResourceNotFoundError, StateConflictError
from yuxi.workers.background_job_context import BackgroundJobContext

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


class PixelProvider:
    """确定性回放直接读取像素；真实供应商兼容性另用带凭据探针验证。"""

    def __init__(self, spec, purpose):
        self.info = SimpleNamespace(spec=spec, provider_id="pixel-replay")
        if not spec or spec == "missing":
            raise InvalidOperationError("模型未启用", code="media_model_unavailable")

    @property
    def fingerprint(self):
        """确定性模型的稳定非秘密身份。"""
        return "a" * 64

    async def embed(self, inputs):
        """像素颜色作为独立可手算的视觉向量。"""
        result = []
        for item in inputs:
            if "image" in item:
                data = base64.b64decode(item["image"].split(",", 1)[1])
                with Image.open(io.BytesIO(data)) as image:
                    pixel = image.convert("RGB").getpixel((0, 0))
                result.append([pixel[0] / 255, pixel[2] / 255, 0.1])
            else:
                result.append([1, 0, 0.1] if "red" in item["text"] else [0, 1, 0.1])
        return result

    async def rerank(self, query, images):
        """回放中仍然要求实际图片字节可解码。"""
        vectors = await self.embed(images)
        return sorted(
            [{"index": index, "relevance_score": vector[0] if "red" in query else vector[1]} for index, vector in enumerate(vectors)],
            key=lambda row: row["relevance_score"],
            reverse=True,
        )


@pytest_asyncio.fixture
async def media_store(monkeypatch):
    """每例使用独立 Schema、对象桶与知识库集合，不触及共享产品数据。"""
    schema, admin, engine, manager = await create_isolated_manager("pytest_native_media")
    await create_business_tables(manager)
    await create_knowledge_tables(manager)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    manager.AsyncSession = sessions
    client = MinIOClient()
    bucket = f"pytest-media-{uuid4().hex}"
    client.KB_BUCKETS = {"documents": bucket, "images": bucket, "parsed": bucket}
    await asyncio.to_thread(client.ensure_bucket_exists, bucket)
    kb_id = "media-" + uuid4().hex
    for module in (knowledge_base_module, file_repository, repository, jobs, projections, job_repository, projection_cleanup, revision_cleanup):
        monkeypatch.setattr(module, "pg_manager", manager)
    for module in (knowledge_base_module, jobs, service, revisions, documents, revision_cleanup):
        monkeypatch.setattr(module, "get_minio_client", lambda: client)
    monkeypatch.setattr(jobs, "NativeMediaModel", PixelProvider)
    monkeypatch.setattr(service, "NativeMediaModel", PixelProvider)
    allowed = {"value": True}

    async def visible(uid, *, selection):
        """当前 ACL 可在模型调用后撤销，服务每次复核。"""
        return [{"kb_id": kb_id, "kb_type": "milvus"}] if allowed["value"] and uid == "fixture" else []

    monkeypatch.setattr(service, "visible_knowledge_bases", visible)
    async with sessions() as session, session.begin():
        session.add(KnowledgeBase(kb_id=kb_id, name=kb_id, kb_type="milvus"))
        session.add(User(uid="fixture", username="fixture", role="superadmin", password_hash="not-a-real-password"))
    fixture = SimpleNamespace(
        kb_id=kb_id,
        client=client,
        bucket=bucket,
        sessions=sessions,
        manager=manager,
        allowed=allowed,
        visible=[{"kb_id": kb_id, "kb_type": "milvus"}],
    )
    try:
        yield fixture
    finally:
        await cleanup_media_projection(kb_id)
        await client.adelete_objects_by_prefix(bucket, "")
        await asyncio.to_thread(client.client.remove_bucket, bucket)
        await drop_isolated_schema(schema, admin, engine)


def png(color):
    """视觉预期只由已知原始 RGB 值决定。"""
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(buffer, "PNG")
    return buffer.getvalue()


async def add_file(store, data, name):
    """登记真实上传对象与文件哈希。"""
    file_id = "file-" + uuid4().hex
    uri = f"minio://{store.bucket}/{store.kb_id}/original/{file_id}/{name}"
    await store.client.aupload_file(store.bucket, f"{store.kb_id}/original/{file_id}/{name}", data)
    async with store.sessions() as session, session.begin():
        session.add(
            KnowledgeFile(
                kb_id=store.kb_id,
                file_id=file_id,
                filename=name,
                file_type=name.rsplit(".", 1)[-1],
                path=uri,
                minio_url=uri,
                status="uploaded",
                content_hash=hashlib.sha256(data).hexdigest(),
                file_size=len(data),
            )
        )
    return file_id


async def prepared_job(store, file_id, *, config=None, document_images=False, run=True):
    """使用真实 BackgroundJob lease 准备或运行相同 shipping handler。"""
    from yuxi.modules.background_jobs.models import BackgroundJobRecord

    _meta, source, basis = await repository.load_build_source(store.kb_id, file_id, document_images=document_images)
    job_id, owner = uuid4().hex, uuid4().hex
    payload = {
        "kb_id": store.kb_id,
        "file_id": file_id,
        "source_revision": source,
        "basis_document_revision": basis,
        "operator_id": "fixture",
        "document_images": document_images,
        "config": config or {"embedding_model": "pixel", "rerank_model": "pixel", "understanding_model": "pixel"},
    }
    async with store.sessions() as session, session.begin():
        session.add(
            BackgroundJobRecord(
                id=job_id,
                name="native media fixture",
                type=repository.MEDIA_JOB_TYPE,
                status="running",
                worker_id=owner,
                lease_expires_at=utc_now() + timedelta(minutes=10),
                payload=payload,
            )
        )
    context = BackgroundJobContext(job_id, owner, payload)
    if not run:
        return context, None
    result = await jobs.run_media_index(context)
    return context, result


async def publish_job(context, result):
    """通过实际终态 repository 提交成功与媒体事实，不手写替代发布。"""
    assert await job_repository.BackgroundJobRepository().finish_owned(
        context.job_id,
        worker_id=context.worker_id,
        status="success",
        message="done",
        result=result,
        before_finish=partial(jobs.finish_media_index, result=result),
    )


async def test_pixels_recalled_read_and_published_only_with_job_success(media_store):
    """同名语义两张异色图的原像素进入Milvus，提交前不暴露半索引。"""
    store = media_store
    red = await add_file(store, png("red"), "notice-a.png")
    blue = await add_file(store, png("blue"), "notice-b.png")
    ctx, prepared = await prepared_job(store, red)
    async with store.sessions() as session:
        assert list(await session.scalars(select(KnowledgeDocumentRevision))) == []
    assert (await service.query_kb_media(store.kb_id, "red", "image", store.visible, uid="fixture"))["results"] == []
    await publish_job(ctx, prepared)
    ctx2, prepared2 = await prepared_job(store, blue)
    await publish_job(ctx2, prepared2)
    results = await service.query_kb_media(store.kb_id, "red", "image", store.visible, uid="fixture")
    assert results["results"][0]["file_id"] == red
    assert results["results"][0]["rerank_score"] == 1
    source = results["results"][0]
    actual = await service.read_kb_media(store.kb_id, red, source["asset_id"], source["revision"], store.visible, uid="fixture")
    with Image.open(io.BytesIO(actual["media_bytes"])) as decoded:
        assert decoded.getpixel((0, 0)) == (255, 0, 0)
    assert actual["original_hash"] == hashlib.sha256(png("red")).hexdigest()
    assert (await service.query_kb_media(store.kb_id, "red", "image", store.visible, uid="fixture", allowed_file_ids=[]))["results"] == []
    scoped = await service.query_kb_media(store.kb_id, "red", "image", store.visible, uid="fixture", allowed_file_ids=[blue])
    assert {item["file_id"] for item in scoped["results"]} == {blue}
    with pytest.raises(ResourceNotFoundError):
        await service.read_kb_media(
            store.kb_id, red, source["asset_id"], source["revision"], store.visible, uid="fixture", allowed_file_ids=[blue]
        )
    store.allowed["value"] = False
    with pytest.raises(ValueError, match="可访问"):
        await service.read_kb_media(store.kb_id, red, source["asset_id"], source["revision"], store.visible, uid="fixture")


async def test_incomplete_vector_projection_cannot_publish_or_replace_working_revision(media_store):
    """删除已准备向量恢复目标故障，实际回读必须阻止Job success与新修订。"""
    store = media_store
    file_id = await add_file(store, png("red"), "notice.png")
    old_ctx, old_result = await prepared_job(store, file_id)
    await publish_job(old_ctx, old_result)
    context, result = await prepared_job(store, file_id)
    await cleanup_media_projection(store.kb_id, file_id, result["revision"])
    with pytest.raises(InvalidOperationError, match="不完整"):
        await publish_job(context, result)
    async with store.sessions() as session:
        rows = list(await session.scalars(select(KnowledgeDocumentRevision)))
        assert [row.revision for row in rows] == [old_result["revision"]]
    assert await job_repository.BackgroundJobRepository().finish_owned(
        context.job_id,
        worker_id=context.worker_id,
        status="failed",
        message="failed",
        before_finish=partial(jobs.fail_media_index, error="incomplete vector"),
    )
    await projection_cleanup.process_knowledge_projections()
    assert await store.client.alist_object_metadata(store.bucket, result["media_build"]["prefix"] + "/") == []
    status = await service.get_media_status(store.kb_id, file_id, store.visible, uid="fixture")
    assert status["current"]["revision"] == old_result["revision"] and status["job"]["status"] == "failed"
    assert (await service.query_kb_media(store.kb_id, "red", "image", store.visible, uid="fixture"))["results"][0][
        "revision"
    ] == old_result["revision"]


async def test_raw_audio_segments_preserve_boundaries_and_original_hash(media_store):
    """独立波形oracle证明第二段不混入第一段，非转写文本检索证据。"""
    sample_rate = 16000
    original = io.BytesIO()
    with wave.open(original, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        samples = [
            round(10000 * math.sin(2 * math.pi * (440 if index < 12 * sample_rate else 880) * index / sample_rate))
            for index in range(13 * sample_rate)
        ]
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    store = media_store
    file_id = await add_file(store, original.getvalue(), "recording.wav")
    context, result = await prepared_job(store, file_id)
    await publish_job(context, result)
    listing = await service.list_kb_media(store.kb_id, store.visible, uid="fixture")
    assert [(item["start_ms"], item["end_ms"]) for item in listing["items"]] == [(0, 12000), (12000, 13000)]
    second = listing["items"][1]
    actual = await service.read_kb_media(store.kb_id, file_id, second["asset_id"], result["revision"], store.visible, uid="fixture")
    with wave.open(io.BytesIO(actual["media_bytes"]), "rb") as clip:
        assert clip.getframerate() == sample_rate and clip.getnframes() == sample_rate
        values = struct.unpack(f"<{sample_rate}h", clip.readframes(sample_rate))
    assert values == tuple(samples[12 * sample_rate :])
    assert actual["original_hash"] == hashlib.sha256(original.getvalue()).hexdigest()


async def test_audio_index_without_understanding_model_is_not_published(media_store):
    """缺少音频核对模型时，真实解码后的音频不能发布为可检索档案。"""
    original = io.BytesIO()
    with wave.open(original, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(struct.pack("<16000h", *([1000] * 16000)))
    store = media_store
    file_id = await add_file(store, original.getvalue(), "recording.wav")
    context, _ = await prepared_job(
        store,
        file_id,
        config={"embedding_model": None, "rerank_model": None, "understanding_model": None},
        run=False,
    )

    with pytest.raises(InvalidOperationError, match="音频索引必须配置核对模型"):
        await jobs.run_media_index(context)

    async with store.sessions() as session:
        job = await session.get(BackgroundJobRecord, context.job_id)
        prefix = job.result["media_build"]["prefix"]
    assert await job_repository.BackgroundJobRepository().finish_owned(
        context.job_id,
        worker_id=context.worker_id,
        status="failed",
        message="missing audio understanding model",
        before_finish=partial(jobs.fail_media_index, error="missing audio understanding model"),
    )
    await projection_cleanup.process_knowledge_projections()
    async with store.sessions() as session:
        revisions = list(
            await session.scalars(
                select(KnowledgeDocumentRevision).where(
                    KnowledgeDocumentRevision.kb_id == store.kb_id,
                    KnowledgeDocumentRevision.file_id == file_id,
                )
            )
        )
    assert revisions == []
    assert await store.client.alist_object_metadata(store.bucket, prefix + "/") == []
    status = await service.get_media_status(store.kb_id, file_id, store.visible, uid="fixture")
    assert status["current"] is None and status["job"]["status"] == "failed"


async def test_silent_video_without_understanding_model_is_not_published(media_store, tmp_path):
    """静音视频仍需核对模型，避免发布后无法核验视频片段。"""
    path = tmp_path / "silent.mp4"
    await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:r=2:d=2",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    store = media_store
    file_id = await add_file(store, path.read_bytes(), "silent.mp4")
    context, _ = await prepared_job(
        store,
        file_id,
        config={"embedding_model": "pixel", "rerank_model": None, "understanding_model": None},
        run=False,
    )

    with pytest.raises(InvalidOperationError, match="视频索引必须配置核对模型"):
        await jobs.run_media_index(context)

    async with store.sessions() as session:
        job = await session.get(BackgroundJobRecord, context.job_id)
        prefix = job.result["media_build"]["prefix"]
        revisions = list(
            await session.scalars(
                select(KnowledgeDocumentRevision).where(
                    KnowledgeDocumentRevision.kb_id == store.kb_id,
                    KnowledgeDocumentRevision.file_id == file_id,
                )
            )
        )
    assert revisions == []
    assert await job_repository.BackgroundJobRepository().finish_owned(
        context.job_id,
        worker_id=context.worker_id,
        status="failed",
        message="missing video understanding model",
        before_finish=partial(jobs.fail_media_index, error="missing video understanding model"),
    )
    await projection_cleanup.process_knowledge_projections()
    assert await store.client.alist_object_metadata(store.bucket, prefix + "/") == []
    status = await service.get_media_status(store.kb_id, file_id, store.visible, uid="fixture")
    assert status["current"] is None and status["job"]["status"] == "failed"


async def test_same_pointer_mutation_and_removed_source_reject_reads(media_store):
    """缓存/不可变指针不能掩盖对象被覆盖或文件被删除。"""
    store = media_store
    file_id = await add_file(store, png("red"), "source.png")
    context, result = await prepared_job(store, file_id)
    await publish_job(context, result)
    await store.client.aupload_file(store.bucket, f"{store.kb_id}/original/{file_id}/source.png", png("blue"))
    with pytest.raises(StateConflictError, match="原件"):
        await service.read_kb_media(store.kb_id, file_id, "image", result["revision"], store.visible, uid="fixture")
    async with store.sessions() as session, session.begin():
        file = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
        file.deleted_at = utc_now()
    with pytest.raises(ResourceNotFoundError):
        await service.read_kb_media(store.kb_id, file_id, "image", result["revision"], store.visible, uid="fixture")


async def test_document_images_bind_actual_body_and_referenced_image_bytes(media_store):
    """正文移除图片或原图被覆写后，旧视觉命中不能成为当前证据。"""
    store = media_store
    file_id = await add_file(store, b"original document", "notice.md")
    image_key = f"{store.kb_id}/kb-images/{file_id}/parsed/photo.png"
    body_key = f"{store.kb_id}/parsed/{file_id}/attempt/body.md"
    body = f"# Notice\n![figure](/api/knowledge/knowledge-bases/{store.kb_id}/images/kb-images/{file_id}/parsed/photo.png)"
    await store.client.aupload_file(store.bucket, image_key, png("red"))
    await store.client.aupload_file(store.bucket, body_key, body.encode())
    async with store.sessions() as session, session.begin():
        file = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
        file.markdown_file = f"minio://{store.bucket}/{body_key}"
    context, result = await prepared_job(store, file_id, document_images=True)
    await publish_job(context, result)
    found = (await service.query_kb_media(store.kb_id, "red", "image", store.visible, uid="fixture"))["results"][0]
    assert found["basis_document_revision"].startswith("md2:") and found["line"] == 2
    await store.client.aupload_file(store.bucket, image_key, png("blue"))
    with pytest.raises(StateConflictError, match="原图"):
        await service.read_kb_media(store.kb_id, file_id, found["asset_id"], found["revision"], store.visible, uid="fixture")
    await store.client.aupload_file(store.bucket, image_key, png("red"))
    await store.client.aupload_file(store.bucket, body_key, b"# Image removed")
    with pytest.raises(StateConflictError, match="正文"):
        await service.read_kb_media(store.kb_id, file_id, found["asset_id"], found["revision"], store.visible, uid="fixture")


@pytest.mark.parametrize("mode", ["success", "disabled", "registration_failure", "dispatch_failure"])
async def test_document_parse_automatically_indexes_extracted_images(media_store, monkeypatch, tmp_path, mode):
    """验证解析图片自动索引、关闭、登记失败及投递重试的完整 Job 生命周期。"""
    from yuxi.infrastructure.minio import object_urls
    from yuxi.modules.background_jobs import dispatch as background_job_dispatch
    from yuxi.modules.documents import service as parsing_service
    from yuxi.modules.knowledge.services import document_media
    from yuxi.workers import background_jobs as worker_jobs

    store = media_store
    monkeypatch.setattr(object_urls, "_KNOWN_BUCKETS", object_urls._KNOWN_BUCKETS | {store.bucket})
    file_id = await add_file(store, b"source document", "notice.pdf")
    enabled = mode != "disabled"
    settings = {
        "document_image_auto_index": {
            "enabled": enabled,
            "embedding_model": "pixel" if enabled else None,
        }
    }
    async with store.sessions() as session, session.begin():
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == store.kb_id))
        kb.additional_params = settings

    async def parse_with_image(*, image_bucket, image_prefix, url_builder, **_kwargs):
        """回放解析器生成图片对象和指向该对象的正文引用。"""
        object_name = f"{image_prefix}figure.png"
        await store.client.aupload_file(image_bucket, object_name, png("red"))
        return f"# Notice\n![figure]({url_builder(object_name)})\n"

    dispatched = []

    async def record_dispatch(job_id):
        dispatched.append(job_id)
        if mode == "dispatch_failure":
            raise RuntimeError("queue unavailable")

    if mode == "registration_failure":

        async def fail_registration(*_args, **_kwargs):
            raise RuntimeError("registration failed")

        monkeypatch.setattr(document_media, "register_document_image_index", fail_registration)

    monkeypatch.setattr(parsing_service, "parse_to_hosted_markdown", parse_with_image)
    monkeypatch.setattr(background_job_dispatch, "dispatch_job", record_dispatch)

    executor = object.__new__(MilvusKB)
    executor.work_dir = str(tmp_path)

    parsed = await executor.parse_file(
        store.kb_id,
        file_id,
        "fixture",
        additional_params=settings,
    )

    assert parsed["status"] == "parsed"
    if mode == "disabled":
        assert "document_image_index" not in parsed
        assert dispatched == []
        async with store.sessions() as session:
            jobs_for_file = list(
                await session.scalars(select(BackgroundJobRecord).where(BackgroundJobRecord.type == repository.MEDIA_JOB_TYPE))
            )
            assert jobs_for_file == []
        return

    if mode == "registration_failure":
        assert parsed["document_image_index"]["status"] == "failed"
        assert dispatched == []
        async with store.sessions() as session:
            file = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            revisions_for_file = list(
                await session.scalars(
                    select(KnowledgeDocumentRevision).where(
                        KnowledgeDocumentRevision.file_id == file_id,
                        KnowledgeDocumentRevision.kind == "document",
                    )
                )
            )
            media_jobs = list(
                await session.scalars(select(BackgroundJobRecord).where(BackgroundJobRecord.type == repository.MEDIA_JOB_TYPE))
            )
        assert file.status == "parsed" and file.markdown_file
        assert len(revisions_for_file) == 1 and media_jobs == []
        return

    assert parsed["document_image_index"]["status"] == "queued"
    from yuxi.modules.knowledge.revision_identity import body_revision

    parsed_revision = body_revision(store.kb_id, file_id, parsed["markdown_file"])
    async with store.sessions() as session:
        media_job = await session.scalar(
            select(BackgroundJobRecord).where(BackgroundJobRecord.id == parsed["document_image_index"]["job_id"])
        )
        assert media_job is not None
        assert media_job.status == "pending"
        assert media_job.payload["document_images"] is True
        assert media_job.payload["basis_document_revision"] == parsed_revision
    assert dispatched == [media_job.id]

    if mode == "dispatch_failure":
        assert parsed["document_image_index"]["message"] == "图片索引任务已登记，后台会重试投递。"
        async with store.sessions() as session:
            pending = await session.get(BackgroundJobRecord, media_job.id)
            file = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
        assert pending.status == "pending" and file.status == "parsed" and file.markdown_file

    from unittest.mock import AsyncMock

    monkeypatch.setattr(worker_jobs, "_publish_pending_after_slot_release", AsyncMock())
    await worker_jobs.process_background_job({"worker_id": "document-image-worker"}, media_job.id)

    async with store.sessions() as session:
        media_job = await session.get(BackgroundJobRecord, media_job.id)
        assert media_job.status == "success", media_job.error
        assert media_job.result["items"] == [{"file_id": file_id, "status": "indexed"}]

    found = (await service.query_kb_media(store.kb_id, "red", "image", store.visible, uid="fixture"))["results"]
    assert len(found) == 1 and found[0]["file_id"] == file_id
    assert found[0]["basis_document_revision"] == parsed_revision
    actual = await service.read_kb_media(
        store.kb_id,
        file_id,
        found[0]["asset_id"],
        found[0]["revision"],
        store.visible,
        uid="fixture",
    )
    assert actual["media_bytes"] == png("red")


async def test_document_image_registration_does_not_deadlock_with_media_finish(media_store, monkeypatch):
    """真实 PostgreSQL 覆盖解析持文件行锁时媒体终结持作业锁的竞争。"""
    from datetime import timedelta

    from yuxi.modules.background_jobs.models import BackgroundJobRecord
    from yuxi.modules.knowledge.media.schemas import MediaConfig
    from yuxi.modules.knowledge.services import document_media

    async def skip_projection_cleanup(_kb_id):
        """该回归只登记作业，不创建需要从 Milvus 回收的投影。"""

    monkeypatch.setattr(sys.modules[__name__], "cleanup_media_projection", skip_projection_cleanup)
    store = media_store
    file_id = await add_file(store, b"source document", "notice.pdf")
    body = f"# Notice\n![figure](/api/knowledge/knowledge-bases/{store.kb_id}/images/kb-images/{file_id}/parsed/photo.png)"
    additional_params = {"document_image_auto_index": {"enabled": True, "embedding_model": "pixel"}}
    job_id, owner = uuid4().hex, uuid4().hex
    request = IndexMediaInput(config=MediaConfig(embedding_model="pixel"), document_images=True)
    async with store.sessions() as session, session.begin():
        kb = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.kb_id == store.kb_id))
        kb.additional_params = additional_params
        file = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
        body_key = f"{store.kb_id}/parsed/{file_id}/attempt/body.md"
        file.markdown_file = f"minio://{store.bucket}/{body_key}"
        source_revision, basis_document_revision = repository.current_dependencies(file)
        payload = {
            "kb_id": store.kb_id,
            "file_id": file_id,
            "operator_id": "fixture",
            "source_revision": source_revision,
            "basis_document_revision": basis_document_revision,
            **request.model_dump(),
        }
        session.add(
            BackgroundJobRecord(
                id=job_id,
                name="active media build",
                type=repository.MEDIA_JOB_TYPE,
                status="running",
                worker_id=owner,
                lease_expires_at=utc_now() + timedelta(minutes=10),
                payload=payload,
            )
        )

    media_has_job_lock = asyncio.Event()

    async def finish_media_job():
        """模拟发布钩子在持有 Job 行锁后锁定同一文件。"""

        async def lock_file(session, _record):
            media_has_job_lock.set()
            await session.scalar(
                select(KnowledgeFile).where(KnowledgeFile.kb_id == store.kb_id, KnowledgeFile.file_id == file_id).with_for_update()
            )

        return await job_repository.BackgroundJobRepository().finish_owned(
            job_id,
            worker_id=owner,
            status="success",
            message="done",
            before_finish=lock_file,
        )

    finish_task = None
    try:
        async with store.sessions() as session, session.begin():
            file = await session.scalar(
                select(KnowledgeFile).where(KnowledgeFile.kb_id == store.kb_id, KnowledgeFile.file_id == file_id).with_for_update()
            )
            finish_task = asyncio.create_task(finish_media_job())
            await asyncio.wait_for(media_has_job_lock.wait(), timeout=5)
            result = await document_media.register_document_image_index(
                session,
                kb_id=store.kb_id,
                file=file,
                operator_id="fixture",
                additional_params=additional_params,
                markdown_content=body,
            )
        assert result == {"status": "queued", "job_id": job_id, "created": False}
        assert await finish_task
    finally:
        if finish_task is not None and not finish_task.done():
            finish_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await finish_task


async def test_concurrent_build_intents_reuse_identical_and_reject_different_config(media_store, monkeypatch):
    """同文件的技术构建串行，不用完成时间让旧请求覆盖新配置。"""
    from unittest.mock import AsyncMock

    store = media_store
    file_id = await add_file(store, png("red"), "notice.png")
    monkeypatch.setattr(jobs, "dispatch_job", AsyncMock())
    request = IndexMediaInput(config={"embedding_model": "pixel"})
    first, second = await asyncio.gather(
        jobs.submit_media_index(store.kb_id, file_id, request, "fixture"), jobs.submit_media_index(store.kb_id, file_id, request, "fixture")
    )
    assert first["job_id"] == second["job_id"] and first["created"] != second["created"]
    with pytest.raises(StateConflictError, match="不同参数"):
        await jobs.submit_media_index(store.kb_id, file_id, IndexMediaInput(config={"embedding_model": "other"}), "fixture")


async def test_source_change_and_revoked_operator_prevent_success_publication(media_store):
    """来源CAS与执行器当前权限在真实发布事务中拒绝变化。"""
    from yuxi.shared.errors import PermissionDeniedError

    store = media_store
    file_id = await add_file(store, png("red"), "notice.png")
    context, result = await prepared_job(store, file_id)
    async with store.sessions() as session, session.begin():
        user = await session.scalar(select(User).where(User.uid == "fixture"))
        user.role = "user"
    with pytest.raises(PermissionDeniedError, match="权限"):
        await publish_job(context, result)
    async with store.sessions() as session, session.begin():
        user = await session.scalar(select(User).where(User.uid == "fixture"))
        user.role = "superadmin"
        file = await session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
        file.path = file.minio_url = f"minio://{store.bucket}/{store.kb_id}/original/{file_id}/replacement.png"
    await store.client.aupload_file(store.bucket, f"{store.kb_id}/original/{file_id}/replacement.png", png("red"))
    with pytest.raises(StateConflictError, match="原件|来源"):
        await publish_job(context, result)
    async with store.sessions() as session:
        assert list(await session.scalars(select(KnowledgeDocumentRevision))) == []


async def test_video_frame_recall_resolves_correct_native_interval(media_store, tmp_path):
    """13秒视频末段蓝画面必须返回12至13秒片段，不能猜邻接片段事实。"""
    path = tmp_path / "source.mp4"
    await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:r=2:d=12",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:r=2:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[out]",
            "-map",
            "[out]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    store = media_store
    file_id = await add_file(store, path.read_bytes(), "notice.mp4")
    context, result = await prepared_job(store, file_id)
    await publish_job(context, result)
    found = (await service.query_kb_media(store.kb_id, "blue", "video", store.visible, uid="fixture"))["results"][0]
    with pytest.raises(InvalidOperationError, match="视频原件仅供下载") as rejected:
        await service.inspect_kb_media(
            store.kb_id, file_id, "original", result["revision"], "这段视频说了什么？", store.visible, uid="fixture"
        )
    assert rejected.value.code == "media_video_segment_required"
    original = await service.read_kb_media(store.kb_id, file_id, "original", result["revision"], store.visible, uid="fixture")
    assert original["media_bytes"] == path.read_bytes() and original["mime_type"] == "video/mp4"
    assert (found["start_ms"], found["end_ms"]) == (12000, 13000)
    actual = await service.read_kb_media(store.kb_id, file_id, found["asset_id"], found["revision"], store.visible, uid="fixture")
    clip = tmp_path / "read-back.mp4"
    clip.write_bytes(actual["media_bytes"])
    output = await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-v", "error", "-i", str(clip), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        check=True,
        capture_output=True,
    )
    with Image.open(io.BytesIO(output.stdout)) as image:
        red, green, blue = image.convert("RGB").getpixel((0, 0))
        assert blue > 240 and red < 5 and green < 5


async def test_failed_cleanup_is_observable_terminal_and_retry_deletes_only_own_attempt(media_store, monkeypatch):
    """外部清理中断不阻塞Job终态；删除Job后outbox仍可独立恢复。"""
    from unittest.mock import AsyncMock

    from yuxi.modules.background_jobs.models import BackgroundJobRecord
    from yuxi.modules.knowledge.models import KnowledgeProjectionOutbox

    store = media_store
    file_id = await add_file(store, png("red"), "notice.png")
    old_context, old_result = await prepared_job(store, file_id)
    await publish_job(old_context, old_result)
    context, result = await prepared_job(store, file_id)
    cleanup = jobs.cleanup_media_projection
    monkeypatch.setattr(jobs, "cleanup_media_projection", AsyncMock(side_effect=OSError("injected object/vector outage")))
    assert await job_repository.BackgroundJobRepository().finish_owned(
        context.job_id,
        worker_id=context.worker_id,
        status="failed",
        message="failed",
        before_finish=partial(jobs.fail_media_index, error="injected build failure"),
    )
    async with store.sessions() as session, session.begin():
        record = await session.get(BackgroundJobRecord, context.job_id)
        assert record.status == "failed"
        await session.delete(record)
    assert await projection_cleanup.process_knowledge_projections() == []
    async with store.sessions() as session:
        event = await session.scalar(select(KnowledgeProjectionOutbox).where(KnowledgeProjectionOutbox.operation == "media_build_cleanup"))
        assert event.status == "pending" and "injected" in event.last_error
    assert await store.client.alist_object_metadata(store.bucket, result["media_build"]["prefix"] + "/")
    monkeypatch.setattr(jobs, "cleanup_media_projection", cleanup)
    assert len(await projection_cleanup.process_knowledge_projections()) == 1
    assert await store.client.alist_object_metadata(store.bucket, result["media_build"]["prefix"] + "/") == []
    assert await store.client.alist_object_metadata(store.bucket, old_result["media_build"]["prefix"] + "/")
    actual = await service.query_kb_media(store.kb_id, "red", "image", store.visible, uid="fixture")
    assert actual["results"][0]["revision"] == old_result["revision"]

async def test_short_video_audio_cannot_claim_the_video_interval(tmp_path):
    """短音轨不以整段画面的时间码发布。"""
    source = tmp_path / "short-audio.mp4"
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=red:s=64x64:r=2:d=2",
        "-f", "lavfi", "-i", "sine=f=440:d=1",
        "-c:v", "libx264", "-c:a", "aac", str(source),
    ]
    await asyncio.to_thread(subprocess.run, command, check=True, capture_output=True)
    with pytest.raises(InvalidInputError) as error:
        await prepare_media(source.read_bytes(), tmp_path)
    assert error.value.code == "misaligned_video_audio"
