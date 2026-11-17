"""知识库文件聚合统计及文件操作的刷新收尾。"""

import asyncio
from collections import Counter
from collections.abc import Awaitable
from typing import Any

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.base import KBNotFoundError


async def run_with_stats_refresh(kb_id: str, operation: Awaitable[Any]) -> Any:
    """执行文件操作并刷新统计，同时保留原始操作异常。"""
    try:
        result = await operation
    except (Exception, asyncio.CancelledError):
        try:
            await refresh_stats(kb_id)
        except (Exception, asyncio.CancelledError) as refresh_error:
            logger.error(f"Refresh knowledge_base stats after failed operation: kb_id={kb_id}: {refresh_error}")
        raise

    await refresh_stats(kb_id)
    return result


async def refresh_stats(kb_id: str) -> dict[str, Any]:
    """刷新并持久化知识库聚合统计。"""
    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

    kb = await KnowledgeBaseRepository().refresh_stats(kb_id)
    if kb is None:
        raise KBNotFoundError(f"Knowledge base {kb_id} not found")
    return normalize_stats(kb.additional_params["stats"])


def normalize_stats(stats: dict | None) -> dict[str, Any]:
    """规范化知识库聚合统计字段。"""
    normalized = {
        "file_count": 0,
        "folder_count": 0,
        "row_count": 0,
        "total_size": 0,
        "chunk_count": 0,
        "token_count": 0,
        "pending_parse_count": 0,
        "pending_index_count": 0,
        "processing_count": 0,
    }
    if not isinstance(stats, dict):
        return {**normalized, "file_status_counts": {}}

    for key in normalized:
        try:
            normalized[key] = max(int(stats.get(key) or 0), 0)
        except (TypeError, ValueError):
            normalized[key] = 0
    normalized["file_status_counts"] = dict(stats.get("file_status_counts") or {})
    return normalized


async def get_statistics() -> dict:
    """统计知识库总数、类型分布与文件总数。"""
    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    rows = await KnowledgeBaseRepository().get_all()
    return {
        "total_knowledge_bases": len(rows),
        "kb_types": dict(Counter(row.kb_type or "milvus" for row in rows)),
        "total_files": await KnowledgeFileRepository().count_all(),
    }
