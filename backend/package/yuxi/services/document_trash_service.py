"""文档回收站用例：保留期内可恢复，到期后统一清理存储产物。"""

import re
from urllib.parse import unquote

from yuxi.repositories.document_trash_repository import DocumentTrashRepository


def image_object_paths(markdown: str) -> set[str]:
    """先提取编码对象键再解码，避免空格截断和前缀误匹配。"""
    keys = re.findall(r"kb-images/[^\s\)\]<>\"\'?]+", markdown)
    return {unquote(key.split("#", 1)[0]) for key in keys if ".." not in unquote(key) and "\\" not in unquote(key)}


class DocumentTrashService:
    """协调状态仓储、可见性与到期物理清理。"""

    def __init__(self):
        self.repository = DocumentTrashRepository()

    async def trash(self, kb_id: str, file_ids: list[str], *, deleted_by: str) -> int:
        """原子移入回收站，提交后刷新统计。"""
        rows = await self.repository.trash(kb_id, file_ids, deleted_by=deleted_by)
        await self._refresh_stats(kb_id)
        return len(rows)

    async def restore(self, kb_id: str, file_id: str) -> int:
        """恢复文件或同批目录后代，返回恢复项数。"""
        rows = await self.repository.restore(kb_id, file_id)
        await self._refresh_stats(kb_id)
        return len(rows)

    async def _refresh_stats(self, kb_id: str) -> None:
        """刷新派生统计，不将缓存失败误报为文件操作失败。"""
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
        from yuxi.storage.redis import get_async_redis_client
        from yuxi.utils import logger

        try:
            await KnowledgeBaseRepository().refresh_stats(kb_id)
            client = await get_async_redis_client()
            await client.delete(f"yuxi:kb_file_stats:{kb_id}")
        except Exception:
            # The document transaction has committed; do not misreport a successful restore as failed.
            logger.exception("Trash operation committed but stats refresh failed: kb_id=%s", kb_id)

    async def list_items(self, kb_id: str, *, offset: int = 0, limit: int = 10) -> dict:
        """分页投影生命周期，仅向前端返回固定清理错误文案。"""
        rows = await self.repository.list_trashed(kb_id, offset=offset, limit=limit)
        return {
            "items": [
                {
                    "file_id": row.file_id,
                    "filename": row.filename,
                    "is_folder": row.is_folder,
                    "deleted_at": row.deleted_at,
                    "purge_after": row.purge_after,
                    "status": "purge_failed" if row.purge_error else ("purging" if row.purge_started_at else "trashed"),
                    "error_message": "自动清理失败，系统将重试" if row.purge_error else None,
                }
                for row in rows
            ],
            "total": await self.repository.count_trashed(kb_id),
        }

    async def _markdown(self, row, client) -> str:
        """读取已登记的解析文本以确认旧图片归属。"""
        from yuxi.knowledge.utils import is_minio_url, parse_minio_url

        if not row.markdown_file:
            return ""
        if not is_minio_url(row.markdown_file):
            raise ValueError("Unsupported parsed document storage")
        bucket, key = parse_minio_url(row.markdown_file)
        return (await client.adownload_file(bucket, key)).decode("utf-8")

    async def image_is_active(self, kb_id: str, object_path: str) -> bool:
        """只允许活动文件拥有或明确引用的图片。"""
        from yuxi.storage.minio.client import get_minio_client

        rows = await self.repository.list_storage_references(kb_id)
        parts = object_path.split("/")
        if len(parts) >= 3:
            return any(row.file_id == parts[1] and row.deleted_at is None for row in rows)
        # Legacy flat image keys have no owner ID: fail closed unless an active source references them.
        client = get_minio_client()
        for row in rows:
            if row.deleted_at is None and object_path in image_object_paths(await self._markdown(row, client)):
                return True
        return False

    async def _purge_one(self, row) -> None:
        """依次获取运行配置和引用锁，串行完成单文件清理。"""
        import asyncio

        from yuxi.knowledge.runtime import knowledge_base
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        # Resolve cached configuration before the tree lock (whole-KB deletion uses cache -> tree).
        executor = None if row.is_folder else await knowledge_base.get_kb_executor(row.kb_id)
        # Serialize reference checking through metadata removal, including other workers and uploads.
        async with KnowledgeFileRepository().lock_storage_references():
            async with KnowledgeFileRepository().lock_file_tree(row.kb_id):
                # Synchronous storage threads outlive cancellation; retain both locks until they finish.
                cleanup = asyncio.create_task(self._purge_locked(row, executor))
                cancelled = False
                while True:
                    try:
                        await asyncio.shield(cleanup)
                        break
                    except asyncio.CancelledError:
                        if cleanup.cancelled():
                            raise
                        cancelled = True
                if cancelled:
                    raise asyncio.CancelledError

    async def _purge_locked(self, row, executor) -> None:
        """在引用锁内清理无其他引用的产物，最后提交文件移除。"""
        from yuxi.knowledge.utils import is_minio_url, parse_minio_url
        from yuxi.storage.minio.client import MinIOClient, get_minio_client

        client = get_minio_client()
        token = (row.file_id, row.deletion_id, row.purge_token)
        objects = row.purge_objects
        if objects is None:
            objects = []
            if not row.is_folder:
                for path in (row.path, row.minio_url, row.markdown_file):
                    if path and is_minio_url(path):
                        bucket, key = parse_minio_url(path)
                        objects.append({"bucket": bucket, "key": key})
                bucket = MinIOClient.KB_BUCKETS["parsed"]
                objects.extend(
                    [
                        {"bucket": bucket, "key": f"{row.kb_id}/parsed/{row.file_id}.md"},
                        {"bucket": bucket, "key": f"{row.kb_id}/preview/{row.file_id}.pdf"},
                    ]
                )
                for path in image_object_paths(await self._markdown(row, client)):
                    if ".." not in path and "\\" not in path:
                        objects.append({"bucket": MinIOClient.KB_BUCKETS["images"], "key": f"{row.kb_id}/{path}"})
                for item in await client.alist_object_metadata(
                    MinIOClient.KB_BUCKETS["images"], f"{row.kb_id}/kb-images/{row.file_id}/"
                ):
                    objects.append({"bucket": MinIOClient.KB_BUCKETS["images"], "key": item["object_name"]})
        if not await self.repository.set_purge_objects(*token, objects):
            raise RuntimeError("Purge lease lost before artifact cleanup")
        # Preserve shared objects until their last document is purged (including other trash entries).
        references = await self.repository.list_storage_references()
        shared = set()
        legacy_images = [obj for obj in objects if obj["bucket"] == MinIOClient.KB_BUCKETS["images"]]
        for other in references:
            if other.file_id == row.file_id:
                continue
            for path in (other.path, other.minio_url, other.markdown_file):
                if path and is_minio_url(path):
                    shared.add(parse_minio_url(path))
            if other.purge_objects is not None:
                shared.update((obj["bucket"], obj["key"]) for obj in other.purge_objects)
            elif legacy_images and other.kb_id == row.kb_id:
                image_paths = image_object_paths(await self._markdown(other, client))
                for obj in legacy_images:
                    if obj["key"].removeprefix(f"{row.kb_id}/") in image_paths:
                        shared.add((obj["bucket"], obj["key"]))
        if not row.is_folder:
            await executor.purge_file_artifacts(row.kb_id, row.file_id)
        for obj in objects:
            if (obj["bucket"], obj["key"]) not in shared:
                await client.adelete_file(obj["bucket"], obj["key"])
        if not await self.repository.complete_purge(*token):
            raise RuntimeError("Purge lease lost before completion")

    async def purge_due(self, *, limit: int = 50) -> dict:
        """有界领取到期项，记录失败并继续处理其他文件。"""
        import asyncio

        from yuxi.utils import logger

        completed = failed = 0
        for _ in range(limit):
            rows = await self.repository.claim_due(limit=1, lease_seconds=600)
            if not rows:
                break
            row = rows[0]
            try:
                await asyncio.wait_for(self._purge_one(row), timeout=240)
                completed += 1
            except Exception as error:
                logger.exception("Document trash purge failed: file_id=%s", row.file_id)
                await self.repository.fail_purge(row.file_id, row.deletion_id, row.purge_token, type(error).__name__)
                failed += 1
                # The repository defers retries, allowing other expired documents to progress.
                continue
        return {"completed": completed, "failed": failed}


document_trash_service = DocumentTrashService()
