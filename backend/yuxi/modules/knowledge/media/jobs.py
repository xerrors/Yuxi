"""原生媒体后台构建：解码、外部索引完成后原子发布不可变媒体档案。"""

import asyncio
import hashlib
import re
import tempfile
from pathlib import Path
from uuid import uuid4

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.dispatch import dispatch_job, register_job_in_session
from yuxi.modules.knowledge.media.processing import MAX_SOURCE_BYTES, prepare_image, prepare_media, require_derived_budget
from yuxi.modules.knowledge.media.repository import (
    MEDIA_JOB_TYPE,
    active_media_build,
    enqueue_unpublished_build_cleanup,
    load_build_source,
    register_media_result,
    require_media_manage,
)
from yuxi.modules.knowledge.media.schemas import IndexMediaInput, MediaManifest
from yuxi.modules.knowledge.media.vectors import MediaVectorStore, cleanup_media_projection
from yuxi.modules.knowledge.repositories.directories import DirectoryRepository
from yuxi.modules.knowledge.repositories.document_resets import DocumentResetRepository
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.repositories.projections import acquire_knowledge_projection_lock, knowledge_projection_lock
from yuxi.modules.knowledge.repositories.revisions import DocumentRevisionRepository
from yuxi.modules.knowledge.revision_identity import media_revision
from yuxi.modules.knowledge.services import documents
from yuxi.modules.knowledge.services.documents import file_record_to_meta
from yuxi.modules.knowledge.services.imports import register_import
from yuxi.modules.knowledge.services.revisions import owned_object, prepare_media_revision
from yuxi.modules.knowledge.services.structure import document_images
from yuxi.modules.knowledge.services.uploads import stage_document
from yuxi.modules.models.media import NativeMediaModel, media_data_uri
from yuxi.shared.errors import InvalidOperationError, StateConflictError


async def upload_media_source(kb_id: str, filename: str, data: bytes, operator_id: str, parent_id: str | None = None) -> dict:
    """复用原件暂存和归属登记，媒体上传不隐式运行文档 OCR。"""
    async with pg_manager.get_async_session_context() as session:
        kb = await DirectoryRepository().lock(session, kb_id)
        await require_media_manage(session, kb, operator_id)
    staged = await stage_document(kb_id=kb_id, filename=filename, content=data)
    uri = staged.upload.url
    return await register_import(
        kb_id=kb_id,
        items=[uri],
        params={"content_hashes": {uri: staged.content_hash}, "file_sizes": {uri: len(data)}, "parent_id": parent_id},
        operator_id=operator_id,
    )


async def submit_media_index(
    kb_id: str,
    file_id: str,
    request: IndexMediaInput,
    operator_id: str,
    *,
    processing_job_id: str | None = None,
) -> dict:
    """准入和后台意图同事务登记；提交后才投递 ARQ。"""
    meta, source, basis = await load_build_source(
        kb_id, file_id, document_images=request.document_images, processing_job_id=processing_job_id
    )
    payload = {
        "kb_id": kb_id,
        "file_id": file_id,
        "operator_id": operator_id,
        "source_revision": source,
        "basis_document_revision": basis,
        **request.model_dump(),
    }
    if processing_job_id is not None:
        payload["source_job_id"] = processing_job_id
    async with pg_manager.get_async_session_context() as session:
        kb = await DirectoryRepository().lock(session, kb_id)
        await require_media_manage(session, kb, operator_id)
        await DocumentResetRepository.require_processing(session, kb_id, file_id, job_id=processing_job_id)
        # 完成事务先锁作业再锁知识库；提交持有知识库锁时只读活动作业。
        active = await active_media_build(session, kb_id, file_id, lock=False)
        if active is not None:
            comparable_payload = {key: value for key, value in payload.items() if key != "source_job_id"}
            active_payload = {key: value for key, value in active.payload.items() if key != "source_job_id"}
            if active_payload != comparable_payload:
                raise StateConflictError("同一文件已有不同参数的媒体构建，请等待完成", code="media_build_busy")
            job, created = active, False
        else:
            job, created = await register_job_in_session(
                session, name=f"媒体索引 ({meta['filename']})", job_type=MEDIA_JOB_TYPE, payload=payload, timeout_seconds=1800
            )
    if created:
        await dispatch_job(job.id)
    return {"status": "queued", "job_id": job.id, "created": created}


async def run_media_index(context) -> dict:
    """准备实际外部投影；仅 worker 成功 Hook 可以发布当前媒体修订。"""
    payload = context.payload
    request = IndexMediaInput.model_validate({key: payload[key] for key in ("config", "document_images")})
    kb_id, file_id = payload["kb_id"], payload["file_id"]
    meta, source, basis = await load_build_source(
        kb_id,
        file_id,
        document_images=request.document_images,
        processing_job_id=payload.get("source_job_id"),
    )
    if (source, basis) != (payload["source_revision"], payload.get("basis_document_revision")):
        raise StateConflictError("排队期间媒体来源已变化，请重新提交", code="stale_source")
    async with pg_manager.get_async_session_context() as session:
        kb = await DirectoryRepository().lock(session, kb_id)
        await require_media_manage(session, kb, payload["operator_id"])
    client = get_minio_client()
    original = owned_object(kb_id, documents.original_file_path(meta))
    attempt = uuid4().hex
    prefix = f"{kb_id}/media/{file_id}/{attempt}"
    bucket = client.KB_BUCKETS["documents"]
    manifest_uri = f"minio://{bucket}/{prefix}/manifest.json"
    revision = media_revision(kb_id, file_id, source, manifest_uri)
    build = {"prefix": prefix, "bucket": bucket, "manifest_uri": manifest_uri, "revision": revision}
    await context.set_result({"media_build": build, "outcome": "building"})
    async with knowledge_projection_lock(kb_id, shared=True):
        await context.raise_if_cancelled()
        await context.set_progress(5, "核对媒体原件")
        data = await client.adownload_file(original["bucket"], original["object_name"], max_bytes=MAX_SOURCE_BYTES)
        source_hash = hashlib.sha256(data).hexdigest()
        if meta.get("content_hash") != source_hash:
            raise StateConflictError("媒体原件与文件指纹不一致", code="immutable_source_changed")
        with tempfile.TemporaryDirectory(prefix="yuxi-media-") as directory:
            directory = Path(directory)
            if request.document_images:
                prepared = await _document_images(meta, directory)
            else:
                prepared = await prepare_media(data, directory)
            needs_understanding = prepared["modality"] == "video" or any(asset["kind"] == "audio" for asset in prepared["assets"])
            if needs_understanding:
                if not request.config.understanding_model:
                    modality = "视频" if prepared["modality"] == "video" else "音频"
                    raise InvalidOperationError(f"{modality}索引必须配置核对模型", code="media_model_unavailable")
                NativeMediaModel(request.config.understanding_model, "understanding")
            await context.raise_if_cancelled()
            await context.set_progress(35, "保存原生媒体片段")
            assets, visual_bytes = [], []
            for asset in prepared.pop("assets"):
                path = asset.pop("path")
                content = await await_io(asyncio.to_thread(path.read_bytes))
                object_name = f"{prefix}/{path.name}"
                await client.aupload_file(bucket_name=bucket, object_name=object_name, data=content, content_type=asset["mime_type"])
                assets.append(
                    {
                        **asset,
                        "object": {"bucket": bucket, "object_name": object_name},
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                )
                if asset["kind"] in {"image", "frame"}:
                    visual_bytes.append(content)
            visual_assets = [asset for asset in assets if asset["kind"] in {"image", "frame"}]
            embeddings = []
            if visual_assets:
                model = NativeMediaModel(request.config.embedding_model, "embedding")
                for offset in range(0, len(visual_bytes), 4):
                    await context.raise_if_cancelled()
                    async with pg_manager.get_async_session_context() as session:
                        kb = await DirectoryRepository().lock(session, kb_id)
                        await require_media_manage(session, kb, payload["operator_id"])
                    embeddings.extend(
                        await model.embed(
                            [{"image": media_data_uri(content, "image/png")} for content in visual_bytes[offset : offset + 4]]
                        )
                    )
                    await context.set_progress(40 + 35 * min(offset + 4, len(visual_bytes)) / len(visual_bytes), "生成原始像素视觉索引")
            manifest = MediaManifest(
                kb_id=kb_id,
                file_id=file_id,
                source_revision=source,
                source_sha256=source_hash,
                basis_document_revision=basis,
                config=request.config,
                embedding_dimension=len(embeddings[0]) if embeddings else None,
                embedding_fingerprint=model.fingerprint if embeddings else None,
                assets=assets,
                **prepared,
            )
            manifest_data = manifest.model_dump_json().encode()
            manifest_object = f"{prefix}/manifest.json"
            await client.aupload_file(bucket_name=bucket, object_name=manifest_object, data=manifest_data, content_type="application/json")
            if embeddings:
                store = await await_io(asyncio.to_thread(MediaVectorStore))
                await store.write(kb_id, request.config.embedding_model, revision, file_id, visual_assets, embeddings)
            await context.raise_if_cancelled()
            outcome = {
                "kb_id": kb_id,
                "file_id": file_id,
                "revision": revision,
                "processed": 1,
                "succeeded": 1,
                "failed": 0,
                "items": [{"file_id": file_id, "status": "prepared"}],
                "outcome": "prepared",
                "asset_count": len(assets),
                "media_build": build,
            }
            await context.set_result(outcome)
            await context.set_progress(95, "媒体投影已准备，等待原子发布")
            return outcome


async def finish_media_index(session, job_record, *, result):
    """当前媒体登记与 BackgroundJob success 同事务，失败或失联不能晋升半成品。"""
    kb_id, file_id = job_record.payload["kb_id"], job_record.payload["file_id"]
    build = _build_identity(job_record, result)
    await acquire_knowledge_projection_lock(session, kb_id, shared=True)
    kb = await DirectoryRepository().lock(session, kb_id)
    await require_media_manage(session, kb, job_record.payload["operator_id"])
    file = await KnowledgeFileRepository().lock_current(session, kb_id, file_id)
    if file is None:
        raise StateConflictError("媒体文件已删除", code="stale_source")
    prepared = await prepare_media_revision(file_record_to_meta(file), build["manifest_uri"])
    data = await get_minio_client().adownload_file(build["bucket"], f"{build['prefix']}/manifest.json", max_bytes=2 * 1024 * 1024)
    manifest = MediaManifest.model_validate_json(data)
    visuals = [asset.model_dump() for asset in manifest.assets if asset.kind in {"image", "frame"}]
    if visuals:
        store = await await_io(asyncio.to_thread(MediaVectorStore))
        await store.verify(kb_id, manifest.config.embedding_model, build["revision"], file_id, visuals, manifest.embedding_dimension)
    await register_media_result(
        session,
        file,
        prepared_revision=prepared,
        expected_source=job_record.payload["source_revision"],
        expected_basis=job_record.payload.get("basis_document_revision"),
        job_id=job_record.id,
        owner=job_record.worker_id,
        source_job_id=job_record.payload.get("source_job_id"),
    )
    result["outcome"] = "completed"
    result["items"] = [{"file_id": file_id, "status": "indexed"}]


async def fail_media_index(session, job_record, error: str):
    """失败终态只登记可重试回收意图，外部存储故障不得阻止 Job 终结。"""
    if not isinstance(job_record.result, dict) or "media_build" not in job_record.result:
        return
    build = _build_identity(job_record, job_record.result)
    kb_id, file_id = job_record.payload["kb_id"], job_record.payload["file_id"]
    attempt = build["prefix"].rsplit("/", 1)[1]
    if not await enqueue_unpublished_build_cleanup(session, kb_id, file_id, build["revision"], attempt):
        return
    job_record.result = {
        "kb_id": kb_id,
        "failed": 1,
        "outcome": "failed",
        "items": [{"file_id": file_id, "status": "failed", "error": True}],
        "public_error": "原生媒体构建失败，请检查模型与媒体格式后重试；当前可用索引请查看媒体详情",
    }


async def cleanup_failed_media_build(session, event) -> None:
    """既有 outbox 在排他投影锁内删除指定未发布修订，任务记录删除不丢回收身份。"""
    identity = re.fullmatch(r"media_cleanup:([0-9a-f]{64}):([0-9a-f]{32})", event.event_key)
    if identity is None or event.operation != "media_build_cleanup":
        raise InvalidOperationError("媒体回收事件身份无效", code="media_build_invalid")
    revision = "media2:" + identity[1]
    if await DocumentRevisionRepository().is_registered(session, revision):
        raise StateConflictError("媒体修订已发布，不能按失败构建回收", code="media_build_published")
    await cleanup_media_projection(event.kb_id, event.aggregate_id, revision)
    prefix = f"{event.kb_id}/media/{event.aggregate_id}/{identity[2]}/"
    client = get_minio_client()
    await await_io(client.adelete_objects_by_prefix(client.KB_BUCKETS["documents"], prefix))
    if await client.alist_object_metadata(client.KB_BUCKETS["documents"], prefix):
        raise InvalidOperationError("媒体失败产物尚未完整回收", code="media_cleanup_incomplete")


def _build_identity(job_record, result):
    """恢复用例只可删除该持久 Job 自己的 attempt 前缀。"""
    build = result["media_build"]
    kb_id, file_id = job_record.payload["kb_id"], job_record.payload["file_id"]
    expected = f"{kb_id}/media/{file_id}/"
    prefix = build.get("prefix", "")
    if (
        not prefix.startswith(expected)
        or not re.fullmatch(r"[0-9a-f]{32}", prefix[len(expected) :])
        or build.get("bucket") != get_minio_client().KB_BUCKETS["documents"]
    ):
        raise InvalidOperationError("媒体构建回收身份无效", code="media_build_invalid")
    uri = f"minio://{build['bucket']}/{prefix}/manifest.json"
    if build.get("manifest_uri") != uri or build.get("revision") != media_revision(
        kb_id, file_id, job_record.payload["source_revision"], uri
    ):
        raise InvalidOperationError("媒体构建清单身份无效", code="media_build_invalid")
    return build


async def _document_images(meta: dict, directory: Path) -> dict:
    """复制当前正文实际引用的图片；正文修改后该视觉索引整体失效。"""
    content = await documents.read_minio_bytes(meta["markdown_file"])
    structure = document_images(content.decode("utf-8"), kb_id=meta["kb_id"], file_id=meta["file_id"])
    if any(not item["readable"] for item in structure):
        raise InvalidOperationError("正文包含不可读取图片，不能发布完整图片索引", code="media_image_unavailable")
    assets, seen = [], set()
    for item in structure:
        if item["asset_id"] in seen:
            continue
        seen.add(item["asset_id"])
        if len(seen) > 100:
            raise InvalidOperationError("单文档图片超过一百张，请拆分来源", code="media_decode_budget")
        client = get_minio_client()
        data = await client.adownload_file(client.KB_BUCKETS["images"], f"{meta['kb_id']}/{item['asset_id']}", max_bytes=10 * 1024 * 1024)
        image = await await_io(asyncio.to_thread(prepare_image, data))
        asset_id = "image-" + hashlib.sha256(item["asset_id"].encode()).hexdigest()[:24]
        path = directory / f"{asset_id}.png"
        path.write_bytes(image)
        assets.append(
            {
                "asset_id": asset_id,
                "segment_id": asset_id,
                "kind": "image",
                "mime_type": "image/png",
                "line": item["line"],
                "document_asset_id": item["asset_id"],
                "document_asset_sha256": hashlib.sha256(data).hexdigest(),
                "path": path,
            }
        )
        require_derived_budget(assets)
    if not assets:
        raise InvalidOperationError("正文没有可索引的图片", code="media_image_unavailable")
    return {
        "modality": "document_images",
        "source_mime_type": "application/octet-stream",
        "duration_ms": None,
        "coverage": "complete",
        "basis_document_sha256": hashlib.sha256(content).hexdigest(),
        "assets": assets,
    }
