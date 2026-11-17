"""知识库元数据的创建、修改、删除和读取，不装配执行器。"""

import secrets
import string
from typing import Any

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.identity.permissions import normalize_permission_config
from yuxi.modules.knowledge.base import KBNameConflictError, KBNotFoundError
from yuxi.modules.knowledge.chunking.presets import ChunkConfigError, deep_merge
from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
from yuxi.modules.knowledge.read_models import KnowledgeBaseDetail, KnowledgeBaseSummary
from yuxi.modules.knowledge.services import statistics
from yuxi.shared.datetime import utc_isoformat


async def create_knowledge_base(
    name: str,
    description: str,
    kb_type: str = "milvus",
    embedding_model_spec: str | None = None,
    llm_model_spec: str | None = None,
    share_config: dict | None = None,
    created_by: str | None = None,
    created_by_department_id: int | str | None = None,
    **kwargs,
) -> KnowledgeBaseDetail:
    """创建知识库：校验类型、名称与共享配置，规范化参数后持久化并返回详情。"""
    if not KnowledgeBaseFactory.is_type_supported(kb_type):
        available_types = list(KnowledgeBaseFactory.get_available_types().keys())
        raise ValueError(f"Unsupported knowledge base type: {kb_type}. Available types: {available_types}")

    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

    kb_repo = KnowledgeBaseRepository()
    if await kb_repo.name_exists(name):
        raise KBNameConflictError(f"知识库名称 '{name}' 已存在，请使用其他名称")

    share_config = _normalize_share_config(
        share_config,
        user_uid=created_by,
        department_id=created_by_department_id,
    )

    kb_class = KnowledgeBaseFactory.get_kb_class(kb_type)
    additional_params = kwargs
    additional_params.setdefault("auto_generate_questions", False)
    if "reranker_config" in additional_params:
        raise ValueError("reranker_config 已移除，请在查询参数中使用 reranker_model spec")
    additional_params = kb_class.normalize_additional_params(additional_params)

    if kb_class.requires_embedding_model:
        if not embedding_model_spec:
            raise ValueError("embedding_model_spec 不能为空")

        from yuxi.modules.models.providers.cache import model_cache

        info = model_cache.get_model_info(embedding_model_spec)
        if not info or info.model_type != "embedding":
            raise ValueError(f"不支持的 embedding 模型: {embedding_model_spec}")
    else:
        embedding_model_spec = None

    alphabet = string.ascii_lowercase + string.digits
    while True:
        kb_id = "kb_" + "".join(secrets.choice(alphabet) for _ in range(10))
        if await kb_repo.get_by_kb_id(kb_id) is None:
            break

    query_params = kb_class.get_default_query_params()
    persisted_additional_params = {**additional_params, "stats": statistics.normalize_stats(None)}
    await kb_repo.create(
        {
            "kb_id": kb_id,
            "name": name,
            "description": description,
            "kb_type": kb_type,
            "embedding_model_spec": embedding_model_spec,
            "llm_model_spec": llm_model_spec,
            "query_params": query_params,
            "additional_params": persisted_additional_params,
            "share_config": share_config,
            "created_by": created_by,
        }
    )

    logger.info(f"Created {kb_type} knowledge_base: {name} ({kb_id})")
    knowledge_base = await get_knowledge_base_info(kb_id)
    if knowledge_base is None:
        raise KBNotFoundError(f"Knowledge base {kb_id} not found after creation")
    return knowledge_base


async def update_knowledge_base(
    kb_id: str,
    name: str,
    description: str,
    llm_model_spec: str | None = None,
    update_llm_model_spec: bool = False,
    additional_params: dict | None = None,
    share_config: dict | None = None,
    operator_uid: str | None = None,
    operator_department_id: int | str | None = None,
) -> KnowledgeBaseDetail:
    """更新知识库名称、描述、参数与共享配置，返回更新后的详情。"""
    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

    kb_repo = KnowledgeBaseRepository()
    kb = await kb_repo.get_by_kb_id(kb_id)
    if kb is None:
        raise ValueError(f"知识库 {kb_id} 不存在")

    kb_type = kb.kb_type or "milvus"
    if not KnowledgeBaseFactory.is_type_supported(kb_type):
        raise ValueError(f"不支持的知识库类型: {kb_type}")
    kb_class = KnowledgeBaseFactory.get_kb_class(kb_type)

    update_data: dict = {
        "name": name,
        "description": description,
    }
    if update_llm_model_spec:
        update_data["llm_model_spec"] = llm_model_spec

    if additional_params is not None:
        current_additional_params = kb.additional_params or {}
        current_graph_config = current_additional_params.get("graph_build_config") or {}
        if current_graph_config.get("locked") and "graph_build_config" in additional_params:
            raise ValueError("图谱抽取配置已锁定，请使用图谱重置接口重新配置")

        merged_additional_params = kb_class.normalize_additional_params(deep_merge(current_additional_params, additional_params))
        update_data["additional_params"] = merged_additional_params

    if share_config is not None:
        update_data["share_config"] = _normalize_share_config(
            share_config,
            user_uid=operator_uid,
            department_id=operator_department_id,
        )

    await kb_repo.update(kb_id, update_data)

    knowledge_base = await get_knowledge_base_info(kb_id)
    if knowledge_base is None:
        raise KBNotFoundError(f"Knowledge base {kb_id} not found after update")
    return knowledge_base


async def delete_knowledge_base(kb_id: str) -> dict:
    """提交知识库不可见事实；外部存储清理由 durable outbox 重放。"""
    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

    await KnowledgeBaseRepository().mark_deleted(kb_id)
    return {"message": "删除成功"}


async def get_knowledge_bases() -> list[KnowledgeBaseSummary]:
    """获取所有知识库摘要。"""
    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

    kb_repo = KnowledgeBaseRepository()
    rows = await kb_repo.get_all()
    all_knowledge_bases: list[KnowledgeBaseSummary] = []
    for row in rows:
        kb_type = row.kb_type or "milvus"
        if not KnowledgeBaseFactory.is_type_supported(kb_type):
            logger.warning(f"Skip unsupported knowledge_base: kb_id={row.kb_id}, kb_type={kb_type}")
            continue

        # 单条记录元数据不合法时只跳过该条，避免一条坏记录隐藏整个列表。
        try:
            knowledge_base = KnowledgeBaseSummary(**_knowledge_base_read_fields(row))
        except Exception as e:
            logger.warning(f"Skip knowledge_base with invalid metadata: kb_id={row.kb_id}, kb_type={kb_type}: {e}")
            continue
        all_knowledge_bases.append(knowledge_base)
    return all_knowledge_bases


async def get_knowledge_base_info(kb_id: str, include_files: bool = False) -> KnowledgeBaseDetail | None:
    """获取知识库详情。"""
    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository
    from yuxi.modules.knowledge.repositories.files import KnowledgeFileRepository

    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        return None

    repo = KnowledgeFileRepository()
    files = None
    files_truncated = False
    files_page_size = None
    if include_files:
        files_page_size = 500
        records, total = await repo.list_documents(kb_id=kb_id, page=1, page_size=files_page_size)
        files = {
            record.file_id: {
                "file_id": record.file_id,
                "filename": record.filename,
                "path": getattr(record, "path", "") or "",
                "markdown_file": getattr(record, "markdown_file", "") or "",
                "type": getattr(record, "file_type", "") or "",
                "status": getattr(record, "status", None) or "uploaded",
                "created_at": utc_isoformat(record.created_at) if getattr(record, "created_at", None) else None,
                "is_folder": bool(getattr(record, "is_folder", False)),
                "parent_id": getattr(record, "parent_id", None),
                "chunk_count": int(getattr(record, "chunk_count", 0) or 0),
                "token_count": int(getattr(record, "token_count", 0) or 0),
            }
            for record in records
        }
        files_truncated = total > len(records)

    file_stats = await repo.get_kb_file_stats(kb_id)
    return KnowledgeBaseDetail(
        **_knowledge_base_read_fields(kb, stats=file_stats),
        sample_questions=tuple(kb.sample_questions or []),
        files=files,
        files_truncated=files_truncated,
        files_page_size=files_page_size,
    )


async def update_kb_query_params(kb_id: str, params: dict[str, Any]) -> None:
    """合并并持久化知识库查询参数。"""
    from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository

    kb = await KnowledgeBaseRepository().merge_query_params_options(kb_id, params)
    if kb is None:
        raise KBNotFoundError(f"Knowledge base {kb_id} not found")


def _normalize_share_config(
    share_config: dict | None,
    *,
    user_uid: str | None = None,
    department_id: int | str | None = None,
) -> dict:
    """规范化共享配置：仅接受 version 2，并把创建/操作者并入读取范围。"""
    if share_config is None:
        return {
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        }

    if not share_config or share_config.get("version") != 2:
        raise ValueError("知识库共享配置必须使用 version 2")

    strict = user_uid is not None or department_id is not None
    normalized = normalize_permission_config(share_config, strict=strict)
    read_scope = normalized["read_scope"]
    if read_scope is None and strict:
        raise ValueError("知识库必须设置读取范围")
    if read_scope and read_scope["access_level"] == "department" and department_id is not None:
        read_scope["department_ids"] = sorted({*read_scope["department_ids"], int(department_id)})
    elif read_scope and read_scope["access_level"] == "user" and user_uid:
        read_scope["user_uids"] = sorted({*read_scope["user_uids"], str(user_uid)})
    return normalized


def _knowledge_base_read_fields(
    row: Any,
    *,
    stats: dict[str, int] | None = None,
) -> dict[str, Any]:
    """将知识库记录转换为 Summary 与 Detail 共用的规范字段。"""
    kb_type = row.kb_type or "milvus"
    kb_class = KnowledgeBaseFactory.get_kb_class(kb_type) if KnowledgeBaseFactory.is_type_supported(kb_type) else None
    try:
        additional_params = kb_class.normalize_additional_params(row.additional_params) if kb_class else dict(row.additional_params or {})
    except ChunkConfigError as error:
        # 管理读取保留原值供授权和修复；执行配置仍由 get_kb_config 严格校验。
        additional_params = dict(row.additional_params or {})
        logger.warning(f"Invalid knowledge chunk config: kb_id={row.kb_id}: {error}")
    persisted_stats = additional_params.pop("stats", None)
    normalized_stats = statistics.normalize_stats(stats if stats is not None else persisted_stats)

    try:
        share_config = normalize_permission_config(row.share_config)
    except (TypeError, ValueError) as error:
        # 保留损坏原值供授权拒绝及超级管理员修复，读取不能赋予默认全局权限。
        share_config = row.share_config
        logger.warning(f"Invalid knowledge share config: kb_id={row.kb_id}: {error}")

    return {
        "kb_id": row.kb_id,
        "name": row.name,
        "description": row.description,
        "kb_type": kb_type,
        "embedding_model_spec": row.embedding_model_spec,
        "llm_model_spec": row.llm_model_spec,
        "query_params": dict(row.query_params or {}),
        "additional_params": additional_params,
        "share_config": share_config,
        "created_by": row.created_by,
        "created_at": row.created_at,
        **normalized_stats,
    }
