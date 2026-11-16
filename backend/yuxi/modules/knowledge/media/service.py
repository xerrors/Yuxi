"""Knowledge MCP 的原生媒体发现、检索、授权原件读取与独立核对。"""

import asyncio
import hashlib
from collections import defaultdict
from uuid import uuid4

from pydantic import ValidationError

from yuxi.infrastructure.filesystem import await_io
from yuxi.infrastructure.minio import get_minio_client
from yuxi.modules.knowledge.media.processing import MAX_ASSET_BYTES, MAX_SOURCE_BYTES
from yuxi.modules.knowledge.media.repository import current_media_files, current_media_revision, latest_media_build, media_file_snapshot
from yuxi.modules.knowledge.media.schemas import MediaManifest
from yuxi.modules.knowledge.media.vectors import MediaVectorStore
from yuxi.modules.knowledge.services.access import visible_knowledge_bases
from yuxi.modules.knowledge.services.file_scope import intersect_file_ids, require_file_in_scope
from yuxi.modules.knowledge.services.revisions import object_checksum, require_owned_object
from yuxi.modules.knowledge.services.tools import require_visible_kb
from yuxi.modules.models.media import NativeMediaModel, media_content_part, media_data_uri
from yuxi.shared.errors import ContentTooLargeError, InvalidOperationError, ResourceNotFoundError, StateConflictError


async def list_kb_media(
    kb_id: str,
    visible_kbs: list[dict],
    *,
    uid: str,
    file_ids: list[str] | None = None,
    allowed_file_ids: list[str] | None = None,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """列出实际可读资产及媒体覆盖；目录与片段位置不等于内容已读。"""
    await _authorize(kb_id, visible_kbs, uid)
    items, missing = await _catalogue(kb_id, intersect_file_ids(file_ids, allowed_file_ids))
    assets = []
    for source, manifest in items:
        for asset in manifest.assets:
            if asset.kind == "frame":
                continue
            assets.append(
                _asset_source(source, asset.model_dump())
                | {"modality": manifest.modality, "coverage": manifest.coverage, "content_read": False}
            )
    await _authorize(kb_id, visible_kbs, uid)
    return {
        "kb_id": kb_id,
        "items": assets[offset : offset + limit],
        "total_assets": len(assets),
        "next_offset": offset + limit if offset + limit < len(assets) else None,
        "unindexed_files": missing,
        "content_read": False,
    }


async def get_media_status(kb_id: str, file_id: str, visible_kbs: list[dict], *, uid: str) -> dict:
    """管理详情直接读取当前媒体与最近构建任务，不维护第二套状态。"""
    await _authorize(kb_id, visible_kbs, uid)
    async with media_file_snapshot(kb_id, file_id) as (session, file):
        latest = await latest_media_build(session, kb_id, file_id)
        current = None
        try:
            revision = await current_media_revision(session, file)
            manifest = await _manifest(revision)
            current = {
                "revision": revision.revision,
                "modality": manifest.modality,
                "asset_count": len(manifest.assets),
                "duration_ms": manifest.duration_ms,
                "config": manifest.config.model_dump(),
                "coverage": manifest.coverage,
            }
        except ResourceNotFoundError as error:
            if error.code != "media_index_unavailable":
                raise
        return {"kb_id": kb_id, "file_id": file_id, "current": current, "job": latest.to_observation_dict() if latest else None}


async def query_kb_media(
    kb_id: str,
    query: str,
    modality: str,
    visible_kbs: list[dict],
    *,
    uid: str,
    file_ids: list[str] | None = None,
    allowed_file_ids: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
    candidate_limit: int = 12,
) -> dict:
    """图像/帧原生向量召回；音频按有界候选页消费原始音频评分。"""
    await _authorize(kb_id, visible_kbs, uid)
    scope = intersect_file_ids(file_ids, allowed_file_ids)
    entries, missing = await _catalogue(kb_id, scope)
    if modality == "audio":
        candidates = [(source, manifest, asset) for source, manifest in entries for asset in manifest.assets if asset.kind == "audio"]
        page = candidates[offset : offset + candidate_limit]
        ranked = []
        for source, manifest, asset in page:
            media = await read_kb_media(
                kb_id, source["file_id"], asset.asset_id, source["revision"], visible_kbs, uid=uid, allowed_file_ids=scope
            )
            model = NativeMediaModel(manifest.config.understanding_model, "understanding")
            scored = await model.inspect(query, [media_content_part(media["media_bytes"], media["mime_type"])], scoring=True)
            ranked.append(
                {
                    **media["source"],
                    "score": scored["score"],
                    "observation": scored["observation"],
                    "evidence_kind": "model_observation",
                    "scoring_model": scored["model"],
                }
            )
        ranked.sort(key=lambda item: item["score"], reverse=True)
        coverage = {
            "method": "native_audio_scoring",
            "total_candidates": len(candidates),
            "scored_candidates": len(page),
            "offset": offset,
            "next_offset": offset + len(page) if offset + len(page) < len(candidates) else None,
            "complete": offset == 0 and len(page) == len(candidates),
        }
        result = {
            "results": ranked[:limit],
            "coverage": coverage,
            "retrieval_status": {"audio_vector_index": "not_supported", "native_audio_scoring": "completed"},
        }
    else:
        result = await _visual_search(kb_id, query, modality, entries, visible_kbs, uid, scope, limit, offset, candidate_limit)
    # 模型和向量调用可能较长；返回前重新核对 ACL 与所有命中修订。
    await _authorize(kb_id, visible_kbs, uid)
    for item in result["results"]:
        async with media_file_snapshot(kb_id, item["file_id"]) as (session, file):
            row = await current_media_revision(session, file)
            if row.revision != item["revision"]:
                raise StateConflictError("媒体检索期间来源已变化", code="stale_source")
    if missing:
        result["coverage"]["complete"] = False
        result["coverage"]["unindexed_files"] = missing
    return {"kb_id": kb_id, "modality": modality, "unindexed_files": missing, **result}


async def read_kb_media(
    kb_id: str,
    file_id: str,
    asset_id: str,
    expected_revision: str,
    visible_kbs: list[dict],
    *,
    uid: str,
    allowed_file_ids: list[str] | None = None,
) -> dict:
    """每次读取都重新校验 ACL、当前来源及真实字节，不接任意 URL。"""
    await _authorize(kb_id, visible_kbs, uid)
    require_file_in_scope(file_id, allowed_file_ids)
    async with media_file_snapshot(kb_id, file_id) as (session, file):
        row = await current_media_revision(session, file)
        if row.revision != expected_revision:
            raise StateConflictError("媒体修订已变化，请重新检索", code="stale_source")
        manifest = await _manifest(row)
        await _verify_source(file, row, manifest)
        source = _source(file, row)
        if asset_id == "original":
            ref, size, digest = row.original_object, row.original_size, row.original_hash
            if size > MAX_ASSET_BYTES:
                raise ContentTooLargeError("原件超过 MCP 10 MiB 读取预算，请读取具体片段或从管理界面下载", code="media_asset_too_large")
            # 原件的 MIME 来自实际容器；对文档图片路线不把 PDF 等文档冒充图片。
            if manifest.modality == "document_images":
                raise InvalidOperationError("文档原件请使用文档下载；此工具读取索引内图片", code="unsupported_media")
            mime = manifest.source_mime_type
            asset = {
                "asset_id": "original",
                "segment_id": "original",
                "kind": manifest.modality,
                "mime_type": mime,
                "sha256": digest,
                "size": size,
                "start_ms": 0 if manifest.duration_ms else None,
                "end_ms": manifest.duration_ms,
            }
        else:
            selected = next((asset for asset in manifest.assets if asset.asset_id == asset_id), None)
            if selected is None:
                raise ResourceNotFoundError("媒体资产不属于当前文件与修订")
            asset = selected.model_dump()
            ref, size, digest, mime = selected.object, selected.size, selected.sha256, selected.mime_type
            if selected.document_asset_id:
                current_image = await get_minio_client().adownload_file(
                    get_minio_client().KB_BUCKETS["images"], f"{kb_id}/{selected.document_asset_id}", max_bytes=MAX_ASSET_BYTES
                )
                if hashlib.sha256(current_image).hexdigest() != selected.document_asset_sha256:
                    raise StateConflictError("正文原图内容已变化，请重建媒体索引", code="stale_source")
        data = await _asset_bytes(kb_id, ref, size, digest)
        source = _asset_source(source, asset)
        result = {
            **source,
            "source": source,
            "mime_type": mime,
            "media_bytes": data,
            "coverage": {
                "kind": "original" if asset_id == "original" else "bounded_asset",
                "start_ms": asset.get("start_ms"),
                "end_ms": asset.get("end_ms"),
                "full_duration_ms": manifest.duration_ms,
            },
            "understanding_model": manifest.config.understanding_model,
            "representation": "original_bytes"
            if asset_id == "original"
            else {
                "image": "decoded_rgb_png",
                "frame": "sampled_rgb_frame_max_width_1280",
                "audio": "pcm_s16le_16000hz_mono",
                "video": "h264_crf18_original_resolution_and_frame_rate_video_only",
            }[asset["kind"]],
            "audio_asset_id": next(
                (item.asset_id for item in manifest.assets if item.kind == "audio" and item.segment_id == asset["segment_id"]), None
            )
            if asset["kind"] == "video"
            else None,
        }
    await _authorize(kb_id, visible_kbs, uid)
    return result


async def inspect_kb_media(
    kb_id: str,
    file_id: str,
    asset_id: str,
    expected_revision: str,
    question: str,
    visible_kbs: list[dict],
    *,
    uid: str,
    allowed_file_ids: list[str] | None = None,
) -> dict:
    """一次全新原生模型调用核对指定媒体；模型观察与原始证据分开。"""
    media = await read_kb_media(kb_id, file_id, asset_id, expected_revision, visible_kbs, uid=uid, allowed_file_ids=allowed_file_ids)
    if media["mime_type"].startswith("video/") and asset_id == "original":
        raise InvalidOperationError(
            "视频原件仅供下载；请先列出媒体，选择带明确时间范围的片段核对画面与对应音轨", code="media_video_segment_required"
        )
    parts = [media_content_part(media.pop("media_bytes"), media["mime_type"])]
    inspected_sources = [media["source"]]
    # 视频资产显式分离音轨；查明对应 audio 后单独提供，不能假设视频抽帧包含声音。
    if media["mime_type"] == "video/mp4" and asset_id != "original":
        async with media_file_snapshot(kb_id, file_id) as (session, file):
            row = await current_media_revision(session, file)
            manifest = await _manifest(row)
            audio = next((asset for asset in manifest.assets if asset.kind == "audio" and asset.segment_id == media["segment_id"]), None)
        if audio is not None:
            audio_media = await read_kb_media(
                kb_id, file_id, audio.asset_id, expected_revision, visible_kbs, uid=uid, allowed_file_ids=allowed_file_ids
            )
            parts.append(media_content_part(audio_media["media_bytes"], audio_media["mime_type"]))
            inspected_sources.append(audio_media["source"])
    model = NativeMediaModel(media.pop("understanding_model"), "understanding")
    operation_id = uuid4().hex
    observation = await model.inspect(question, parts)
    await _authorize(kb_id, visible_kbs, uid)
    # 回读版本后才发布观察，更新/删除不能留下看似当前的核验结论。
    async with media_file_snapshot(kb_id, file_id) as (session, file):
        if (await current_media_revision(session, file)).revision != expected_revision:
            raise StateConflictError("媒体核对期间来源已变化", code="stale_source")
    return {
        **media,
        **observation,
        "operation_id": operation_id,
        "operation": "native_media_inspection",
        "question": question,
        "provider_id": model.info.provider_id,
        "inspected_sources": inspected_sources,
        "raw_evidence_read": True,
        "independent_call": True,
        "video_sampling": {"fps": 1, "max_frames": 16, "complete_frame_coverage": False} if media["mime_type"] == "video/mp4" else None,
    }


async def _visual_search(kb_id, query, modality, entries, visible, uid, scope, limit, offset, candidate_limit):
    """同模型空间内召回，候选原图视觉重排；禁止把不同模型余弦直接当统一评分。"""
    groups = defaultdict(list)
    for source, manifest in entries:
        if (
            modality == "video"
            and manifest.modality == "video"
            or modality == "image"
            and manifest.modality in {"image", "document_images"}
        ):
            groups[manifest.config.embedding_model].append((source, manifest))
    if not groups:
        return {
            "results": [],
            "coverage": {"method": "native_visual_embedding", "candidate_count": 0},
            "retrieval_status": {"visual_embedding": "no_indexed_media"},
        }
    store = await await_io(asyncio.to_thread(MediaVectorStore))
    ranked, status = [], []
    for spec, group in groups.items():
        model = NativeMediaModel(spec, "embedding")
        if any(manifest.embedding_fingerprint != model.fingerprint for _, manifest in group):
            raise InvalidOperationError("媒体向量模型配置已变化，请重建索引", code="media_embedding_changed")
        vector = (await model.embed([{"text": query}]))[0]
        if any(manifest.embedding_dimension != len(vector) for _, manifest in group):
            raise InvalidOperationError("媒体向量维度已变化，请重建索引", code="media_embedding_changed")
        hits = await store.search(kb_id, spec, [source["revision"] for source, _ in group], vector, min(offset + candidate_limit, 1000))
        hits = hits[offset : offset + candidate_limit]
        manifests = {source["revision"]: manifest for source, manifest in group}
        rerank_groups = defaultdict(list)
        for hit in hits:
            manifest = manifests[hit["revision"]]
            media = await read_kb_media(kb_id, hit["file_id"], hit["asset_id"], hit["revision"], visible, uid=uid, allowed_file_ids=scope)
            rerank_groups[manifest.config.rerank_model].append((hit, manifest, media))
        for rerank_spec, candidates in rerank_groups.items():
            if not rerank_spec:
                # 配置缺失仍展示向量候选，但明确未重排；调用方不能宣称重排已完成。
                rows = [{"index": index, "relevance_score": None} for index in range(len(candidates))]
                state = "not_configured"
            else:
                reranker = NativeMediaModel(rerank_spec, "rerank")
                rows = await reranker.rerank(
                    query, [{"image": media_data_uri(media["media_bytes"], media["mime_type"])} for _, _, media in candidates]
                )
                state = "completed"
            status.append({"embedding_model": spec, "rerank_model": rerank_spec, "rerank": state})
            for rank, row in enumerate(rows, 1):
                hit, manifest, media = candidates[row["index"]]
                source = media["source"]
                if modality == "video":
                    video = next(asset for asset in manifest.assets if asset.kind == "video" and asset.segment_id == source["segment_id"])
                    source = _asset_source(
                        {
                            key: value
                            for key, value in source.items()
                            if key not in {"asset_id", "asset_hash", "kind", "start_ms", "end_ms"}
                        },
                        video.model_dump(),
                    )
                    source["matched_frame_asset_id"] = hit["asset_id"]
                ranked.append(
                    {
                        **source,
                        "score": 1 / (60 + rank),
                        "visual_similarity": hit["score"],
                        "rerank_score": row["relevance_score"],
                        "ranking": "reciprocal_rank",
                        "evidence_kind": "native_media_candidate",
                    }
                )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    unique = {}
    for item in ranked:
        unique.setdefault((item["file_id"], item["revision"], item["asset_id"]), item)
    return {
        "results": list(unique.values())[:limit],
        "coverage": {
            "method": "native_visual_embedding",
            "candidate_count": len(ranked),
            "candidate_limit_per_model": candidate_limit,
            "offset": offset,
            "sampled_video_frames": modality == "video",
            "frame_interval_ms": 2000 if modality == "video" else None,
            "complete": False,
        },
        "retrieval_status": {"models": status},
    }


async def _catalogue(kb_id: str, scope: list[str] | None) -> tuple[list, int]:
    """只从 PG 当前已发布版本构造候选，失效来源计为未索引。"""
    entries, missing = [], 0
    for file_id in await current_media_files(kb_id, scope):
        try:
            async with media_file_snapshot(kb_id, file_id) as (session, file):
                row = await current_media_revision(session, file)
                manifest = await _manifest(row)
                if file.content_hash != manifest.source_sha256:
                    raise StateConflictError("媒体原件指纹已经变化", code="stale_source")
                entries.append((_source(file, row), manifest))
        except ResourceNotFoundError as error:
            if error.code != "media_index_unavailable":
                raise
            missing += 1
    return entries, missing


async def _manifest(row) -> MediaManifest:
    """持久清单也经过对象归属、字节 hash 和结构验证。"""
    ref = row.manifest_object
    data = await _asset_bytes(row.kb_id, ref, row.content_size, row.content_hash, budget=2 * 1024 * 1024)
    try:
        manifest = MediaManifest.model_validate_json(data)
    except ValidationError as error:
        raise InvalidOperationError("媒体清单已损坏，请重新构建", code="media_manifest_invalid") from error
    if (manifest.kb_id, manifest.file_id, manifest.source_revision, manifest.source_sha256, manifest.basis_document_revision) != (
        row.kb_id,
        row.file_id,
        row.source_revision,
        row.original_hash,
        row.basis_document_revision,
    ):
        raise StateConflictError("媒体清单与档案身份不一致", code="media_manifest_invalid")
    prefix = ref["object_name"].rsplit("/", 1)[0] + "/"
    for asset in manifest.assets:
        require_owned_object(row.kb_id, asset.object)
        if not asset.object["object_name"].startswith(prefix):
            raise InvalidOperationError("媒体资产不属于该构建清单", code="media_manifest_invalid")
    return manifest


async def _verify_source(file, row, manifest):
    """原件、正文及引用图片都从实际当前对象核对，不能靠缓存命中放过撤换。"""
    size = await get_minio_client().astat_file(row.original_object["bucket"], row.original_object["object_name"])
    if size is None or size != row.original_size or size > MAX_SOURCE_BYTES:
        raise StateConflictError("媒体原件不可用或已变化", code="stale_source")
    actual_size, digest = await object_checksum(row.original_object)
    if actual_size != row.original_size or digest != row.original_hash or file.content_hash != digest:
        raise StateConflictError("媒体原件内容已变化", code="immutable_source_changed")
    if manifest.basis_document_revision:
        from yuxi.modules.knowledge.services.documents import read_minio_bytes
        from yuxi.modules.knowledge.services.structure import document_images

        content = await read_minio_bytes(file.markdown_file)
        if hashlib.sha256(content).hexdigest() != manifest.basis_document_sha256:
            raise StateConflictError("媒体依据正文内容已变化", code="stale_source")
        current = {
            item["asset_id"]
            for item in document_images(content.decode("utf-8"), kb_id=file.kb_id, file_id=file.file_id)
            if item["readable"]
        }
        if any(asset.document_asset_id not in current for asset in manifest.assets):
            raise StateConflictError("媒体图片已从当前正文移除", code="stale_source")


async def _asset_bytes(kb_id, ref, size, digest, *, budget=MAX_ASSET_BYTES):
    """授权范围内的对象也必须符合实际大小与内容指纹。"""
    require_owned_object(kb_id, ref)
    if not 0 < size <= budget:
        raise ContentTooLargeError("媒体资产超过读取预算", code="media_asset_too_large")
    data = await get_minio_client().adownload_file(ref["bucket"], ref["object_name"], max_bytes=budget)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise StateConflictError("媒体资产内容已变化", code="immutable_source_changed")
    return data


async def _authorize(kb_id, visible, uid):
    """调用范围与实时 ACL 都必须允许；不接受仅由工具参数声明的可见性。"""
    require_visible_kb(kb_id, visible)
    require_visible_kb(kb_id, await visible_knowledge_bases(uid, selection=[kb_id]))


def _source(file, row):
    """可公开的媒体来源身份，不暴露内部对象 URL。"""
    return {
        "kb_id": file.kb_id,
        "file_id": file.file_id,
        "filename": file.filename,
        "revision": row.revision,
        "source_revision": row.source_revision,
        "original_hash": row.original_hash,
        "basis_document_revision": row.basis_document_revision,
    }


def _asset_source(source, asset):
    """把片段时间码绑定到同一来源，图像行号仅表示原文引用位置。"""
    return {
        **source,
        "asset_id": asset["asset_id"],
        "segment_id": asset["segment_id"],
        "asset_hash": asset["sha256"],
        "kind": asset["kind"],
        "start_ms": asset.get("start_ms"),
        "end_ms": asset.get("end_ms"),
        "line": asset.get("line"),
    }
