"""将知识库内部读取模型转换为现有 HTTP 响应。"""

from __future__ import annotations

from typing import Any

from yuxi.knowledge.read_models import KnowledgeBaseDetail, KnowledgeBaseSummary
from yuxi.knowledge.utils.security import redact_sensitive_params
from yuxi.permissions import ResourcePermission
from yuxi.utils.datetime_utils import utc_isoformat


def _knowledge_base_stats(database: KnowledgeBaseSummary) -> dict[str, int]:
    """组装兼容现有接口的嵌套统计字段。"""
    return {
        "file_count": database.file_count,
        "folder_count": database.folder_count,
        "row_count": database.row_count,
        "total_size": database.total_size,
        "chunk_count": database.chunk_count,
        "token_count": database.token_count,
        "pending_parse_count": database.pending_parse_count,
        "pending_index_count": database.pending_index_count,
        "processing_count": database.processing_count,
    }


def serialize_knowledge_base(
    database: KnowledgeBaseSummary,
    *,
    permission: ResourcePermission | None = None,
    can_manage: bool | None = None,
    redact_secrets: bool = False,
    row_count_fallback: bool = False,
) -> dict[str, Any]:
    """转换单个知识库读取模型，并保留现有 HTTP 字段兼容性。"""
    stats = _knowledge_base_stats(database)
    additional_params = dict(database.additional_params)
    if redact_secrets:
        additional_params = redact_sensitive_params(additional_params)
    additional_params["stats"] = stats

    response = {
        "kb_id": database.kb_id,
        "name": database.name,
        "description": database.description,
        "kb_type": database.kb_type,
        "embedding_model_spec": database.embedding_model_spec,
        "llm_model_spec": database.llm_model_spec,
        "query_params": dict(database.query_params),
        "metadata": dict(additional_params),
        "created_by": database.created_by,
        "created_at": utc_isoformat(database.created_at) if database.created_at else None,
        "status": "已连接",
        "stats": stats,
        "row_count": (database.row_count or database.file_count) if row_count_fallback else database.row_count,
        "share_config": database.share_config,
        "additional_params": additional_params,
    }

    effective_permission = permission or database.effective_permission
    if effective_permission is not None:
        response["effective_permission"] = effective_permission.value
        response["can_manage"] = database.can_manage if can_manage is None else can_manage
        response["can_edit_documents"] = effective_permission == ResourcePermission.MANAGE

    if isinstance(database, KnowledgeBaseDetail):
        response["mindmap"] = database.mindmap
        response["sample_questions"] = list(database.sample_questions)
        if database.files is not None:
            response["files"] = database.files
            response["files_truncated"] = database.files_truncated
            response["files_page_size"] = database.files_page_size

    return response


def serialize_knowledge_base_list(databases: list[KnowledgeBaseSummary]) -> dict[str, list[dict[str, Any]]]:
    """转换知识库摘要列表为现有列表接口响应。"""
    return {"databases": [serialize_knowledge_base(database, row_count_fallback=True) for database in databases]}
