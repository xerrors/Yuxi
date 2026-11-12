"""知识库导入：原件校验、真实目录、持久回执和提交后投递。"""

import hashlib
import json
from pathlib import PurePosixPath
from urllib.parse import unquote

from yuxi.infrastructure.minio.client import MinIOClient, get_minio_client
from yuxi.infrastructure.minio.object_urls import parse_minio_url
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.background_jobs.dispatch import dispatch_job, register_job_in_session
from yuxi.modules.knowledge.file_status import PARSE_CLAIM_STATUSES, FileStatus
from yuxi.modules.knowledge.models import KnowledgeFile, KnowledgeImport
from yuxi.modules.knowledge.names import MAX_IMPORT_ITEMS, normalize_name, source_segments
from yuxi.modules.knowledge.repositories.directories import DirectoryRepository
from yuxi.modules.knowledge.repositories.document_resets import DocumentResetRepository
from yuxi.modules.knowledge.services.documents import file_meta_to_record_data, file_record_to_meta
from yuxi.modules.knowledge.utils import params_for_uploaded_document, prepare_item_metadata, resolve_processing_params
from yuxi.modules.knowledge.utils.kb_utils import PREPROCESSED_UPLOADS_KEY, content_parser_id
from yuxi.shared.errors import DomainError, InvalidInputError, StateConflictError


async def register_import(
    *,
    kb_id: str,
    items: list[str],
    params: dict,
    operator_id: str,
    import_id: str | None = None,
    process: bool = False,
    additional_params: dict | None = None,
) -> dict:
    """发布完整文件归属及作业；相同意图只登记一次。"""
    if not items or len(items) > MAX_IMPORT_ITEMS or len(set(items)) != len(items):
        raise InvalidInputError(f"每次导入需要 1 至 {MAX_IMPORT_ITEMS} 个不同的暂存对象")
    identity = import_id if import_id is not None else hashlib.sha256(json.dumps(items, ensure_ascii=False).encode()).hexdigest()
    if not identity or len(identity) > 128:
        raise InvalidInputError("import_id 必须为 1 至 128 个字符")
    request = {"kb_id": kb_id, "items": items, "params": params, "process": process}
    fingerprint = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    repo = DirectoryRepository()
    async with pg_manager.get_async_session_context() as session:
        receipt = await repo.receipt(session, operator_id, identity)
        if receipt is not None:
            if receipt.fingerprint != fingerprint:
                raise StateConflictError("导入标识已用于不同请求", code="import_conflict")
            return receipt.response

    # 对象读取在目录事务之外；重放回执不依赖仍然存在的原件。
    source_map = params.get("source_paths", {})
    if not isinstance(source_map, dict) or set(source_map) - set(items):
        raise InvalidInputError("来源清单只能引用本次导入对象")
    prepared = []
    owned_objects = []
    try:
        # 逐项校验暂存原件归属与指纹，转存 original 并推导目录段。
        for item in items:
            metadata = await prepare_item_metadata(item, "file", kb_id, params=params_for_uploaded_document(item, params))
            bucket, object_name = parse_minio_url(metadata["path"])
            if bucket != MinIOClient.KB_BUCKETS["documents"] or not object_name.startswith((f"{kb_id}/upload/", "unknown/upload/")):
                raise InvalidInputError("原件不属于目标知识库的暂存区")
            stored_metadata = await get_minio_client().object_metadata(bucket, object_name)
            original_name = stored_metadata.get("content-disposition")
            if original_name is not None and item not in (params.get(PREPROCESSED_UPLOADS_KEY) or {}):
                metadata["filename"] = normalize_name(unquote(original_name.removeprefix("attachment; filename*=UTF-8''")))
            source = source_map.get(item, params.get("source_path"))
            has_source = item in source_map or "source_path" in params
            segments = source_segments(source) if has_source else [normalize_name(metadata["filename"])]
            content = await get_minio_client().adownload_file(bucket, object_name)
            digest = hashlib.sha256(content).hexdigest()
            if metadata.get("content_hash") != digest:
                raise InvalidInputError("原件内容与上传指纹不一致")
            original = await get_minio_client().aupload_file(
                bucket_name=bucket,
                object_name=f"{kb_id}/original/{metadata['file_id']}/source.{metadata['file_type']}",
                data=content,
                content_type=metadata.get("content_type"),
            )
            owned_objects.append((original.bucket_name, original.object_name))
            metadata["path"] = original.url
            metadata.update(filename=segments[-1], original_filename=metadata["filename"], size=len(content), content_hash=digest)
            metadata["processing_params"] = resolve_processing_params(
                kb_additional_params=additional_params or {},
                file_processing_params=metadata.get("processing_params"),
            )
            if has_source:
                metadata["processing_params"]["original_source_path"] = source
            prepared.append((item, metadata, segments, has_source))
        response = await _register_prepared(
            kb_id=kb_id,
            prepared=prepared,
            params=params,
            operator_id=operator_id,
            identity=identity,
            fingerprint=fingerprint,
            process=process,
        )
    except DomainError:
        # 领域拒绝已完成回滚，可以清理本次独占对象；未知提交结果必须保留原件。
        for bucket, key in owned_objects:
            await get_minio_client().adelete_file(bucket, key)
        raise
    retained = {entry["file_meta"]["path"] for entry in response["items"]}
    for bucket, key in owned_objects:
        if not any(parse_minio_url(path) == (bucket, key) for path in retained):
            await get_minio_client().adelete_file(bucket, key)
    if response.get("job_id"):
        await dispatch_job(response["job_id"])
    return response


async def _register_prepared(*, kb_id, prepared, params, operator_id, identity, fingerprint, process) -> dict:
    """仅在数据库事务中登记目录、文件、回执及作业。"""
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    repo = DirectoryRepository()

    async with pg_manager.get_async_session_context() as session:
        # 回执先于目录树加锁，跨知识库复用同一标识也不能同时登记。
        await repo.lock_import(session, operator_id, identity)
        receipt = await repo.receipt(session, operator_id, identity)
        if receipt is not None:
            if receipt.fingerprint != fingerprint:
                raise StateConflictError("导入标识已用于不同请求", code="import_conflict")
            return receipt.response
        kb = await repo.lock(session, kb_id)
        if process:
            await DocumentResetRepository.require_submission(session, kb_id)
        parent_id = params.get("parent_id")
        await repo.parent(session, kb_id, parent_id)

        # 使用数据库自身的名称表达式检查整个 manifest，写入前拒绝重复及类型矛盾。
        source_paths = ["/".join(segments) for _item, _metadata, segments, has_source in prepared if has_source]
        keys = await repo.manifest_keys(session, source_paths)
        paths, directory_paths = set(), set()
        for key in keys:
            if key in paths:
                raise InvalidInputError("来源清单包含等价的重复路径")
            paths.add(key)
            directory_paths.update(str(parent) for parent in PurePosixPath(key).parents if str(parent) != ".")
        if paths & directory_paths:
            raise InvalidInputError("来源清单中的文件同时被用作目录")

        added, failed, ingest_ids = [], [], []
        for index, (item, metadata, segments, _has_source) in enumerate(prepared):
            existing = await _existing_fingerprint(session, kb_id, metadata)
            if existing is not None:
                meta = file_record_to_meta(existing)
                added.append({"index": index, "item": item, "file_id": existing.file_id, "status": existing.status, "file_meta": meta})
                if process and existing.status in PARSE_CLAIM_STATUSES:
                    _queue_ingest(ingest_ids, existing.file_id)
                continue
            try:
                async with session.begin_nested():
                    target = parent_id
                    for segment in segments[:-1]:
                        folder = await repo.folder(session, kb_id, target, segment, operator_id, reuse=True)
                        target = folder.file_id
                    name = await repo.available_name(session, kb_id, target, segments[-1])
                    data = file_meta_to_record_data(metadata)
                    data.update(filename=name, parent_id=target, status=FileStatus.UPLOADED, created_by=operator_id)
                    record = KnowledgeFile(file_id=metadata["file_id"], **data)
                    session.add(record)
                    await repo.flush(session)
                    meta = file_record_to_meta(record)
                added.append({"index": index, "item": item, "file_id": record.file_id, "status": record.status, "file_meta": meta})
                if process:
                    _queue_ingest(ingest_ids, record.file_id)
            except DomainError as exc:
                failed.append({"index": index, "item": item, "status": "failed", "error": exc.message, "code": exc.code})

        response = {
            "import_id": identity,
            "status": "success" if not failed else ("partial_failed" if added else "failed"),
            "message": f"已添加 {len(added)} 个文件，失败 {len(failed)} 个",
            "items": added,
            "failed_items": failed,
            "added": len(added),
            "failed": len(failed),
        }
        if ingest_ids:
            job, _ = await register_job_in_session(
                session,
                name="知识库文档处理",
                job_type="knowledge_ingest",
                payload={
                    "kb_id": kb_id,
                    "file_ids": ingest_ids,
                    "params": {key: params[key] for key in ("auto_index", "chunk_preset_id", "chunk_parser_config") if key in params},
                    "operator_id": operator_id,
                },
            )
            response.update(job_id=job.id, status="queued")
        stats = await KnowledgeFileRepository().query_kb_file_stats(kb_id, session=session)
        kb.additional_params = {**(kb.additional_params or {}), "stats": stats}
        session.add(
            KnowledgeImport(
                operator_id=operator_id,
                import_id=identity,
                kb_id=kb_id,
                fingerprint=fingerprint,
                response=response,
            )
        )
    return response


async def _existing_fingerprint(session, kb_id: str, metadata: dict):
    """相同知识库、内容哈希、大小和解析器的未删除文件直接复用。"""

    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    content_hash = metadata.get("content_hash")
    file_size = metadata.get("size")
    if not content_hash or file_size is None:
        return None
    return await KnowledgeFileRepository().find_live_file_by_fingerprint(
        session,
        kb_id=kb_id,
        content_hash=str(content_hash),
        file_size=int(file_size),
        parser_id=content_parser_id(metadata.get("processing_params")),
    )


def _queue_ingest(file_ids: list[str], file_id: str) -> None:
    if file_id not in file_ids:
        file_ids.append(file_id)
