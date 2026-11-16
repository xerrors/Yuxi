"""按真实对象引用回收已撤回映射与文件产物，调用方持有知识投影排他锁。"""

from yuxi.infrastructure.minio import get_minio_client
from yuxi.infrastructure.minio.object_urls import is_minio_url, parse_minio_url
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository
from yuxi.modules.knowledge.repositories.revisions import DocumentRevisionRepository
from yuxi.modules.knowledge.revision_identity import source_object_uri
from yuxi.modules.knowledge.services.revisions import require_owned_object


async def cleanup_retired_revision(kb_id: str, revision: str) -> None:
    """先清专属媒体投影，再删除没有其他活动引用的对象；失败保留映射供重试。"""
    async with pg_manager.get_async_session_context() as session:
        record = await DocumentRevisionRepository().withdrawn_revision(session, kb_id, revision)
        if record is None:
            return
        candidates = revision_objects(record)
        file_id, kind = record.file_id, record.kind
    if kind == "media":
        from yuxi.modules.knowledge.media.vectors import cleanup_media_projection

        await cleanup_media_projection(kb_id, file_id=file_id, revision=revision)
    protected = await retained_objects(kb_id, candidates)
    client = get_minio_client()
    for bucket, key in sorted(candidates - protected):
        await client.adelete_file(bucket, key)


async def cleanup_file_derivatives(kb_id: str, file_id: str) -> None:
    """重解析清除未使用的旧解析对象；当前映射引用的对象保持受保护。"""
    candidates = await _prefix_objects(kb_id, file_id, ("parsed", "kb-images", "media"))
    protected = await retained_objects(kb_id, candidates)
    client = get_minio_client()
    for bucket, key in sorted(candidates - protected):
        await client.adelete_file(bucket, key)


async def cleanup_deleted_file_artifacts(kb_id: str, file_id: str) -> None:
    """文件已 tombstone 后清理其全部对象；仍被其他文件引用的对象保留。"""
    from yuxi.modules.knowledge.media.vectors import cleanup_media_projection

    async with pg_manager.get_async_session_context() as session:
        file, records = await DocumentRevisionRepository().deleted_file_artifacts(session, kb_id, file_id)
        candidates = set()
        for record in records:
            candidates.update(revision_objects(record))
        if file is not None:
            for uri in [file.markdown_file, source_object_uri(file.minio_url, file.path, file.file_type)]:
                if uri and is_minio_url(uri):
                    bucket, key = parse_minio_url(uri)
                    require_owned_object(kb_id, {"bucket": bucket, "object_name": key})
                    candidates.add((bucket, key))
    candidates.update(await _prefix_objects(kb_id, file_id, ("original", "parsed", "kb-images", "media")))
    await cleanup_media_projection(kb_id, file_id=file_id)
    protected = await retained_objects(kb_id, candidates)
    client = get_minio_client()
    for bucket, key in sorted(candidates - protected):
        await client.adelete_file(bucket, key)
    async with pg_manager.get_async_session_context() as session:
        await DocumentRevisionRepository().purge_file_artifacts(session, file_id)


async def cleanup_attempt(kb_id: str, file_id: str, attempt_id: str) -> None:
    """只回收未发布 attempt；已提交但返回中断的产物不会误删。"""
    candidates = set()
    client = get_minio_client()
    for area, bucket in [("parsed", client.KB_BUCKETS["parsed"]), ("kb-images", client.KB_BUCKETS["images"])]:
        for item in await client.alist_object_metadata(bucket, f"{kb_id}/{area}/{file_id}/{attempt_id}/"):
            candidates.add((bucket, item["object_name"]))
    protected = await retained_objects(kb_id, candidates)
    for bucket, key in sorted(candidates - protected):
        await client.adelete_file(bucket, key)


async def retained_objects(kb_id: str, candidates: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """回读当前文件与活动对象映射，仅保留本次候选中的共享对象。"""
    if not candidates:
        return set()
    protected = set()
    async with pg_manager.get_async_session_context() as session:
        async for markdown_file, minio_url, path, file_type in KnowledgeFileRepository().current_object_pointers(session, kb_id):
            for uri in [markdown_file, source_object_uri(minio_url, path, file_type)]:
                if uri and is_minio_url(uri):
                    ref = parse_minio_url(uri)
                    if ref in candidates:
                        protected.add(ref)
        async for record in DocumentRevisionRepository().active_revisions(session, kb_id):
            protected.update(revision_objects(record) & candidates)
    return protected


def revision_objects(record) -> set[tuple[str, str]]:
    """当前映射按对象类型统一保护，不把媒体当作图片专例。"""
    references = [record.markdown_object, record.manifest_object]
    if record.original_available:
        references.append(record.original_object)
    references.extend(asset["object"] for asset in record.assets)
    result = set()
    for ref in references:
        if ref is None:
            continue
        require_owned_object(record.kb_id, ref)
        result.add((ref["bucket"], ref["object_name"]))
    return result


async def _prefix_objects(kb_id, file_id, areas):
    """只枚举文件拥有的既有技术前缀，原件与预览的具体键仍由文件事实补齐。"""
    client = get_minio_client()
    candidates = set()
    for area in areas:
        bucket = client.KB_BUCKETS[{"original": "documents", "kb-images": "images"}.get(area, "parsed")]
        for item in await client.alist_object_metadata(bucket, f"{kb_id}/{area}/{file_id}/"):
            candidates.add((bucket, item["object_name"]))
    return candidates
