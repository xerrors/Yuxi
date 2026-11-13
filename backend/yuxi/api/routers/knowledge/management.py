"""知识库管理、文档、查询与文件上传路由。"""

import asyncio
import os
import traceback
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from yuxi.api.dependencies.auth import get_admin_user, get_required_user
from yuxi.api.dependencies.knowledge import (
    ensure_knowledge_base_permission,
    require_knowledge_base_manage,
    require_knowledge_base_read,
)
from yuxi.api.responses.knowledge import serialize_knowledge_base, serialize_knowledge_base_list, serialize_staged_file
from yuxi.api.uploads import read_upload_with_limit
from yuxi.infrastructure.document_parsing import SUPPORTED_FILE_EXTENSIONS, is_supported_file_extension
from yuxi.infrastructure.minio.client import MinIOClient, StorageError, get_minio_client
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.background_jobs.service import job_tracker
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import ResourcePermission, resolve_knowledge_base_permission
from yuxi.modules.knowledge.base import KBNameConflictError, KBNotFoundError
from yuxi.modules.knowledge.chunking.presets import get_chunk_preset_options, normalize_chunk_preset_id
from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
from yuxi.modules.knowledge.graphs.service import KnowledgeGraphService
from yuxi.modules.knowledge.runtime import knowledge_base as knowledge_base_manager
from yuxi.modules.knowledge.services import administration, documents, graph_build
from yuxi.modules.knowledge.services.document_actions import (
    submit_document_action,
    submit_pending_document_action,
)
from yuxi.modules.knowledge.services.retrieval import query_with_overrides
from yuxi.modules.knowledge.services.uploads import stage_document
from yuxi.modules.knowledge.utils import (
    is_minio_url,
)
from yuxi.modules.knowledge.utils.kb_utils import PREPROCESSED_UPLOADS_KEY
from yuxi.modules.knowledge.utils.sample_question_utils import (
    generate_knowledge_base_sample_questions,
    get_knowledge_base_sample_questions,
)
from yuxi.modules.knowledge.utils.url_fetcher import fetch_url_content
from yuxi.shared.errors import DomainError

knowledge = APIRouter(prefix="/knowledge", tags=["knowledge"])

MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS = 1000
MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024


class UpdateKnowledgeBaseRequest(BaseModel):
    name: str
    description: str
    llm_model_spec: str | None = None
    additional_params: dict | None = None
    share_config: dict | None = None


class WorkspaceImportRequest(BaseModel):
    kb_id: str
    paths: list[str]


class AddUploadedDocumentsRequest(BaseModel):
    items: list[str]
    params: dict | None = None
    import_id: str | None = None


class MoveDocumentRequest(BaseModel):
    new_parent_id: str | None


class ParseDocumentsRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list)
    params: dict | None = None


class PendingParseDocumentsRequest(BaseModel):
    params: dict | None = None


class PendingIndexDocumentsRequest(BaseModel):
    params: dict | None = None


media_types = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".zip": "application/zip",
    ".rar": "application/x-rar-compressed",
    ".7z": "application/x-7z-compressed",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "text/xml",
    ".css": "text/css",
    ".js": "application/javascript",
    ".py": "text/x-python",
    ".java": "text/x-java-source",
    ".cpp": "text/x-c++src",
    ".c": "text/x-csrc",
    ".h": "text/x-chdr",
    ".hpp": "text/x-c++hdr",
}
# 导出与下载响应使用的扩展名到 Content-Type 映射。


# --- 知识库管理 ---


@knowledge.get("/knowledge-bases")
async def get_knowledge_bases(current_user: User = Depends(get_admin_user)):
    """获取所有知识库（根据用户权限过滤）"""
    try:
        return serialize_knowledge_base_list(await knowledge_base_manager.get_knowledge_bases_by_uid(current_user.uid))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取知识库列表失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="获取知识库列表失败") from e


@knowledge.post("/knowledge-bases")
async def create_knowledge_base(
    name: str = Body(...),
    description: str = Body(...),
    embedding_model_spec: str | None = Body(None),
    kb_type: str = Body("milvus"),
    additional_params: dict | None = Body(None),
    llm_model_spec: str | None = Body(None),
    share_config: dict | None = Body(None),
    current_user: User = Depends(get_admin_user),
):
    """创建知识库"""
    logger.debug("Create knowledge_base")
    try:
        knowledge_base_info = await knowledge_base_manager.create_knowledge_base(
            name,
            description,
            kb_type=kb_type,
            embedding_model_spec=embedding_model_spec,
            llm_model_spec=llm_model_spec,
            share_config=share_config,
            created_by=current_user.uid,
            created_by_department_id=current_user.department_id,
            **(additional_params or {}),
        )

        response = serialize_knowledge_base(knowledge_base_info)
        response["files"] = {}
        return response
    except KBNameConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建知识库失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"创建知识库失败: {e}")


@knowledge.get("/knowledge-bases/accessible")
async def get_accessible_knowledge_bases(current_user: User = Depends(get_required_user)):
    """获取当前用户有权访问的知识库列表（用于智能体配置）"""
    try:
        knowledge_bases = await knowledge_base_manager.get_knowledge_bases_by_uid(current_user.uid)

        accessible = [
            {
                "name": kb.name,
                "kb_id": kb.kb_id,
                "description": kb.description or "",
                "created_by": kb.created_by,
                "kb_type": kb.kb_type,
                "supports_documents": knowledge_base_manager.knowledge_base_type_supports_documents(kb.kb_type),
                "supports_document_read": KnowledgeBaseFactory.get_kb_class(kb.kb_type).supports_document_read,
            }
            for kb in knowledge_bases
        ]

        return {"knowledge_bases": accessible}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取可访问知识库列表失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="获取可访问知识库列表失败") from e


@knowledge.get("/knowledge-bases/{kb_id}")
async def get_knowledge_base_info(
    kb_id: str,
    include_files: bool = Query(False, description="是否包含全量文件列表，默认关闭以避免大知识库响应过大"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """获取知识库详细信息"""
    knowledge_base = await knowledge_base_manager.get_knowledge_base_info(kb_id, include_files=include_files)
    if knowledge_base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    permission = resolve_knowledge_base_permission(current_user, knowledge_base)
    return serialize_knowledge_base(
        knowledge_base,
        permission=permission,
        redact_secrets=permission != ResourcePermission.MANAGE,
    )


@knowledge.put("/knowledge-bases/{kb_id}")
async def update_knowledge_base_info(
    kb_id: str,
    data: UpdateKnowledgeBaseRequest,
    current_user: User = Depends(require_knowledge_base_manage),
):
    """更新知识库信息"""
    logger.debug("Update knowledge_base {}", kb_id)
    try:
        update_llm_model_spec = "llm_model_spec" in data.model_fields_set

        knowledge_base = await knowledge_base_manager.update_knowledge_base(
            kb_id,
            data.name,
            data.description,
            data.llm_model_spec,
            update_llm_model_spec=update_llm_model_spec,
            additional_params=data.additional_params,
            share_config=data.share_config,
            operator_uid=current_user.uid,
            operator_department_id=current_user.department_id,
        )
        return {"message": "更新成功", "knowledge_base": serialize_knowledge_base(knowledge_base)}
    except KBNameConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新知识库失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"更新知识库失败: {e}")


@knowledge.delete("/knowledge-bases/{kb_id}")
async def delete_knowledge_base(kb_id: str, current_user: User = Depends(require_knowledge_base_manage)):
    """删除知识库"""
    logger.debug("Delete knowledge_base {}", kb_id)
    try:
        await knowledge_base_manager.delete_knowledge_base(kb_id)

        return {"message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除知识库失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"删除知识库失败: {e}")


@knowledge.get("/knowledge-bases/{kb_id}/graph-build/status")
async def get_graph_build_status(kb_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """读取图谱构建任务状态。"""
    try:
        return await KnowledgeGraphService().get_status(kb_id, job_tracker=job_tracker)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取图谱构建状态失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取图谱构建状态失败: {e}")


@knowledge.get("/knowledge-bases/{kb_id}/graph-build/files/{file_id}/status")
async def get_file_graph_build_status(kb_id: str, file_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """读取单文件待构建图谱的分块数。"""
    return await graph_build.get_file_graph_build_status(kb_id, file_id)


@knowledge.post("/knowledge-bases/{kb_id}/graph-build/config")
async def configure_graph_build(
    kb_id: str,
    data: dict = Body(...),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """保存图谱抽取配置；已锁定的配置返回 409。"""
    try:
        config = await KnowledgeGraphService().configure(
            kb_id,
            extractor_type=data.get("extractor_type"),
            extractor_options=data.get("extractor_options") or {},
            created_by=current_user.uid,
        )
        return {"message": "图谱抽取配置已锁定", "status": "success", "config": config}
    except ValueError as e:
        status_code = 409 if "已锁定" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"配置图谱构建失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"配置图谱构建失败: {e}")


@knowledge.post("/knowledge-bases/{kb_id}/graph-build/index")
async def index_graph_build(
    kb_id: str,
    data: dict | None = Body(default=None),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """提交图谱向量索引构建任务。"""
    try:
        if data is not None and "file_id" in data:
            file_id = data["file_id"]
            if not isinstance(file_id, str) or not file_id.strip():
                raise ValueError("file_id 必须是非空字符串")
            job = await graph_build.start_graph_build(kb_id, file_id=file_id)
        else:
            job = await graph_build.start_graph_build(kb_id)
        return {"message": "图谱构建任务已提交", "status": "queued", "job_id": job.id}
    except (HTTPException, DomainError):
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"提交图谱构建任务失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"提交图谱构建任务失败: {e}")


@knowledge.post("/knowledge-bases/{kb_id}/graph-build/files/{file_id}/clear")
async def clear_file_graph(kb_id: str, file_id: str, current_user: User = Depends(require_knowledge_base_manage)):
    """登记单文件图谱撤销，后台任务拥有外部清理与最终结果。"""
    job = await graph_build.clear_file_graph(kb_id, file_id)
    return {"message": "清除图谱任务已提交", "status": "queued", "job_id": job.id}


@knowledge.get("/knowledge-bases/{kb_id}/graph-build/failed-chunks")
async def get_graph_build_failed_chunks(
    kb_id: str,
    limit: int = 10,
    current_user: User = Depends(require_knowledge_base_read),
):
    """获取图谱抽取失败 Chunk 样例。"""
    try:
        return await KnowledgeGraphService().get_failed_chunk_samples(kb_id, limit=max(1, min(limit, 10)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取图谱抽取失败 Chunk 样例失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取图谱抽取失败 Chunk 样例失败: {e}")


@knowledge.post("/knowledge-bases/{kb_id}/graph-build/reset")
async def reset_graph_build(
    kb_id: str,
    data: dict | None = Body(default=None),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """重置图谱构建状态，可选择性清空抽取结果或配置。"""
    data = data or {}
    try:
        return await graph_build.reset_graph_build(
            kb_id,
            clear_extraction_result=bool(data.get("clear_extraction_result", True)),
            clear_config=bool(data.get("clear_config", False)),
        )
    except (HTTPException, DomainError):
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"重置图谱构建状态失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"重置图谱构建状态失败: {e}")


@knowledge.post("/knowledge-bases/{kb_id}/graph-build/reconcile")
async def reconcile_graph_build(
    kb_id: str,
    data: dict | None = Body(default=None),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """提交失败 Chunk 的向量索引修复任务。"""
    data = data or {}
    mode = data.get("mode") or "failed"
    try:
        job = await graph_build.reconcile_graph_build(kb_id, mode=mode)
        return {
            "message": "图谱向量索引修复任务已提交",
            "status": "queued",
            "job_id": job.id,
            "mode": mode,
        }
    except (HTTPException, DomainError):
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"提交图谱向量索引修复任务失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"提交图谱向量索引修复任务失败: {e}")


@knowledge.get("/knowledge-bases/{kb_id}/export")
async def export_knowledge_base(
    kb_id: str,
    format: str = Query("csv", enum=["csv", "xlsx", "md", "txt"]),
    include_vectors: bool = Query(False, description="是否在导出中包含向量数据"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """导出知识库数据"""
    raise HTTPException(status_code=501, detail="知识库导出暂不支持")


# --- 知识库文档管理分组 ---


@knowledge.get("/knowledge-bases/{kb_id}/documents")
async def list_documents(
    kb_id: str,
    parent_id: str | None = Query(None, description="父文件夹 ID，空值表示根目录"),
    status: str = Query("all", description="文件状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(100, ge=1, le=500, description="每页数量"),
    recursive: bool = Query(False, description="是否跨目录筛选"),
    sort_by: str | None = Query(None, description="排序字段：filename、content_amount、status、created_at"),
    sort_order: str | None = Query(None, description="排序方向：ascend 或 descend"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """分页获取知识库文件列表。排序可选，作用于筛选后的全部记录。"""
    await _ensure_knowledge_base_supports_documents(kb_id, "文档查看")
    try:
        return await knowledge_base_manager.list_document_files(
            kb_id,
            parent_id=parent_id,
            status=status,
            page=page,
            page_size=page_size,
            recursive=recursive,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@knowledge.get("/knowledge-bases/{kb_id}/documents/search")
async def search_documents(
    kb_id: str,
    query: str = Query("", description="文件名关键词，仅匹配文件名不匹配内容"),
    offset: int = Query(0, ge=0, description="偏移量，从 0 开始"),
    limit: int = Query(100, ge=1, le=500, description="每页数量"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """按文件名搜索知识库文件（仅匹配文件名，不搜索文件内容）。"""
    knowledge_base = await knowledge_base_manager.get_knowledge_base_info(kb_id)
    if not knowledge_base:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在或无权访问")
    if not knowledge_base_manager.knowledge_base_type_supports_documents(knowledge_base.kb_type):
        kb_type = knowledge_base.kb_type.lower()
        raise HTTPException(status_code=400, detail=f"{knowledge_base.name or kb_type} 只支持检索，不支持文档搜索")
    normalized_query = (query or "").strip()
    if not normalized_query:
        return {"files": [], "total": 0, "offset": 0, "limit": limit, "has_more": False}
    return await knowledge_base_manager.search_document_files(
        [{"kb_id": knowledge_base.kb_id, "name": knowledge_base.name}],
        query=normalized_query,
        offset=offset,
        limit=limit,
        include_parent_id=True,
    )


@knowledge.get("/knowledge-bases/{kb_id}/documents/exists")
async def document_file_exists(
    kb_id: str,
    filename: str = Query(..., min_length=1, description="知识库文件展示名或相对路径"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """检查知识库中是否已存在指定文件名或相对路径的文件。"""
    await _ensure_knowledge_base_supports_documents(kb_id, "文档存在性检查")
    normalized_filename = filename.strip()
    if not normalized_filename:
        raise HTTPException(status_code=400, detail="filename is required")
    try:
        exists = await knowledge_base_manager.document_file_exists(kb_id, normalized_filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"kb_id": kb_id, "filename": normalized_filename, "exists": exists}


@knowledge.post("/knowledge-bases/{kb_id}/documents")
async def add_documents(
    kb_id: str,
    items: list[str] = Body(...),
    params: dict = Body(...),
    import_id: str | None = Body(None),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """添加文档到知识库（上传 -> 解析 -> 可选入库）"""
    logger.debug("Add documents for kb_id {}", kb_id)
    await _ensure_knowledge_base_supports_documents(kb_id, "文档添加/解析/入库")

    params = _ensure_document_params(params)
    content_type = params.get("content_type", "file")
    if content_type == "url":
        raise HTTPException(status_code=400, detail="URL 处理方式已变更，请使用 fetch-url 接口先获取内容")
    if content_type != "file":
        raise HTTPException(status_code=400, detail=f"Unsupported content_type: {content_type}")

    _validate_uploaded_document_items(items, params)

    from yuxi.modules.knowledge.services.imports import register_import

    config = await knowledge_base_manager.get_kb_config(kb_id)
    return await register_import(
        kb_id=kb_id,
        items=items,
        params=_ensure_document_params(params),
        operator_id=current_user.uid,
        import_id=import_id,
        process=True,
        additional_params=config.additional_params,
    )


@knowledge.post("/knowledge-bases/{kb_id}/documents/add")
async def add_uploaded_documents(
    kb_id: str,
    payload: AddUploadedDocumentsRequest,
    current_user: User = Depends(require_knowledge_base_manage),
):
    """将已上传的 MinIO 文件同步添加为知识库文档记录，不解析、不入库。"""
    logger.debug("Add uploaded documents for kb_id {}", kb_id)
    await _ensure_knowledge_base_supports_documents(kb_id, "文档添加")

    params = _ensure_document_params(payload.params)
    content_type = params.get("content_type", "file")
    if content_type == "url":
        raise HTTPException(status_code=400, detail="URL 处理方式已变更，请使用 fetch-url 接口先获取内容")
    if content_type != "file":
        raise HTTPException(status_code=400, detail=f"Unsupported content_type: {content_type}")

    _validate_uploaded_document_items(payload.items, params)

    from yuxi.modules.knowledge.services.imports import register_import

    config = await knowledge_base_manager.get_kb_config(kb_id)
    return await register_import(
        kb_id=kb_id,
        items=payload.items,
        params=_ensure_document_params(params),
        operator_id=current_user.uid,
        import_id=payload.import_id,
        additional_params=config.additional_params,
    )


@knowledge.post("/knowledge-bases/{kb_id}/documents/parse")
async def parse_documents(
    kb_id: str,
    payload: ParseDocumentsRequest | list[str] = Body(...),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """手动触发文档解析"""
    if isinstance(payload, list):
        file_ids = payload
        params = None
    else:
        file_ids = payload.file_ids
        params = payload.params
    file_ids = _validate_direct_document_action_file_ids(file_ids)
    logger.debug("Parse documents for kb_id {}", kb_id)
    kb_info = await _ensure_knowledge_base_supports_documents(kb_id, "文档解析")
    return await submit_document_action(
        kb_id=kb_id,
        file_ids=file_ids,
        params=_ensure_document_params(params),
        operator_id=current_user.uid,
        kb_info=kb_info,
        action="parse",
    )


@knowledge.post("/knowledge-bases/{kb_id}/documents/parse-pending")
async def parse_pending_documents(
    kb_id: str,
    payload: PendingParseDocumentsRequest | None = None,
    current_user: User = Depends(require_knowledge_base_manage),
):
    """按状态手动触发全部待解析文档解析。"""
    params = (payload.params if payload else None) or {}
    logger.debug("Parse pending documents for kb_id {}", kb_id)
    kb_info = await _ensure_knowledge_base_supports_documents(kb_id, "文档解析")
    return await submit_pending_document_action(
        kb_id=kb_id,
        params=_ensure_document_params(params),
        operator_id=current_user.uid,
        kb_info=kb_info,
        action="parse",
    )


@knowledge.post("/knowledge-bases/{kb_id}/documents/index")
async def index_documents(
    kb_id: str,
    file_ids: list[str] = Body(...),
    params: dict | None = Body(None),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """手动触发文档入库（Indexing），支持更新参数"""
    file_ids = _validate_direct_document_action_file_ids(file_ids)
    params = params or {}
    logger.debug("Index documents for kb_id {}", kb_id)
    kb_info = await _ensure_knowledge_base_supports_documents(kb_id, "文档入库")
    return await submit_document_action(
        kb_id=kb_id,
        file_ids=file_ids,
        params=_ensure_document_params(params),
        operator_id=current_user.uid,
        kb_info=kb_info,
        action="index",
    )


@knowledge.post("/knowledge-bases/{kb_id}/documents/index-pending")
async def index_pending_documents(
    kb_id: str,
    payload: PendingIndexDocumentsRequest | None = None,
    current_user: User = Depends(require_knowledge_base_manage),
):
    """按状态手动触发全部待入库文档入库。"""
    params = (payload.params if payload else None) or {}
    logger.debug("Index pending documents for kb_id {}", kb_id)
    kb_info = await _ensure_knowledge_base_supports_documents(kb_id, "文档入库")
    return await submit_pending_document_action(
        kb_id=kb_id,
        params=_ensure_document_params(params),
        operator_id=current_user.uid,
        kb_info=kb_info,
        action="index",
    )


@knowledge.get("/knowledge-bases/{kb_id}/documents/{doc_id}")
async def get_document_info(kb_id: str, doc_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """获取文档详细信息（包含基本信息和内容信息）"""
    logger.debug("GET document {} info in {}", doc_id, kb_id)
    await _ensure_knowledge_base_supports_documents(kb_id, "文档查看")

    try:
        info = await knowledge_base_manager.get_file_info(kb_id, doc_id)
        return info
    except (HTTPException, DomainError):
        raise
    except ValueError as error:
        raise HTTPException(status_code=404, detail="文档不存在") from error
    except Exception as e:
        logger.error(f"Failed to get file info, {e}, {kb_id=}, {doc_id=}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to get file info") from e


@knowledge.get("/knowledge-bases/{kb_id}/documents/{doc_id}/basic")
async def get_document_basic_info(kb_id: str, doc_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """获取文档基本信息（仅元数据）"""
    logger.debug("GET document {} basic info in {}", doc_id, kb_id)
    await _ensure_knowledge_base_supports_documents(kb_id, "文档查看")

    try:
        info = await knowledge_base_manager.get_file_basic_info(kb_id, doc_id)
        return info
    except (HTTPException, DomainError):
        raise
    except ValueError as error:
        raise HTTPException(status_code=404, detail="文档不存在") from error
    except Exception as e:
        logger.error(f"Failed to get file basic info, {e}, {kb_id=}, {doc_id=}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to get file basic info") from e


@knowledge.post("/knowledge-bases/{kb_id}/documents/{doc_id}/reparse")
async def reparse_document(
    kb_id: str,
    doc_id: str,
    data: dict | None = Body(default=None),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """从保留原件重新解析，后台执行器拥有撤销及新结果发布。"""
    from yuxi.modules.knowledge.services.document_resets import submit_document_reset

    return await submit_document_reset(kb_id, doc_id, action="reparse", operator_id=current_user.uid, params=(data or {}).get("params"))


@knowledge.get("/knowledge-bases/{kb_id}/documents/{doc_id}/markdown")
async def get_document_markdown(kb_id: str, doc_id: str, current_user: User = Depends(require_knowledge_base_manage)):
    """读取编辑用的可靠正文和修订，不以分块或空串替代读取失败。"""
    from yuxi.modules.knowledge.services.document_resets import read_markdown_for_edit

    return await read_markdown_for_edit(kb_id, doc_id)


@knowledge.post("/knowledge-bases/{kb_id}/documents/{doc_id}/markdown")
async def save_document_markdown(
    kb_id: str, doc_id: str, data: dict = Body(...), current_user: User = Depends(require_knowledge_base_manage)
):
    """登记正文保存任务，由执行器撤销下游并条件发布新正文。"""
    from yuxi.modules.knowledge.services.document_resets import submit_document_reset

    return await submit_document_reset(
        kb_id,
        doc_id,
        action="save_markdown",
        operator_id=current_user.uid,
        expected_revision=data.get("expected_revision"),
        content=data.get("content"),
    )


@knowledge.get("/knowledge-bases/{kb_id}/documents/{doc_id}/content")
async def get_document_content(kb_id: str, doc_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """获取文档内容信息（chunks和lines）"""
    logger.debug("GET document {} content in {}", doc_id, kb_id)
    await _ensure_knowledge_base_supports_documents(kb_id, "文档查看")

    try:
        info = await knowledge_base_manager.get_file_content(kb_id, doc_id)
        info["lines"] = _strip_internal_graph_fields(info.get("lines", []))
        return info
    except (HTTPException, DomainError):
        raise
    except ValueError as error:
        raise HTTPException(status_code=404, detail="文档不存在") from error
    except Exception as e:
        logger.error(f"Failed to get file content, {e}, {kb_id=}, {doc_id=}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to get file content") from e


@knowledge.delete("/knowledge-bases/{kb_id}/documents/batch")
async def batch_delete_documents(kb_id: str, file_ids: list[str] = Body(...), current_user: User = Depends(require_knowledge_base_manage)):
    """批量删除文档或文件夹"""
    await _ensure_knowledge_base_supports_documents(kb_id, "批量文档删除")
    return await administration.batch_delete_documents(kb_id, file_ids)


@knowledge.delete("/knowledge-bases/{kb_id}/documents/{doc_id}")
async def delete_document(kb_id: str, doc_id: str, current_user: User = Depends(require_knowledge_base_manage)):
    """删除文档或文件夹"""
    await _ensure_knowledge_base_supports_documents(kb_id, "文档删除")
    try:
        return await administration.delete_document(kb_id, doc_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"删除文档失败: {error}") from error


@knowledge.get("/knowledge-bases/{kb_id}/documents/{doc_id}/download")
async def download_document(kb_id: str, doc_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """下载原始文件"""
    await _ensure_knowledge_base_supports_documents(kb_id, "文档下载")
    try:
        source, filename, media_type = await documents.open_original_file(kb_id, doc_id)
    except (HTTPException, DomainError):
        raise
    except Exception as error:
        logger.exception("下载知识库原件失败")
        raise HTTPException(status_code=500, detail="下载失败") from error
    return _minio_streaming_response(
        source,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"},
    )


@knowledge.get("/knowledge-bases/{kb_id}/images/{object_path:path}")
async def get_kb_image(kb_id: str, object_path: str, current_user: User = Depends(require_knowledge_base_read)):
    """经鉴权代理读取知识库图片（图片存放在私有 bucket，禁止匿名访问）"""
    if not object_path.startswith("kb-images/") or ".." in object_path or "\\" in object_path:
        raise HTTPException(status_code=400, detail="非法的知识库图片路径")

    object_name = f"{kb_id}/{object_path}"
    minio_client = get_minio_client()
    try:
        minio_response = await minio_client.adownload_response(
            bucket_name=MinIOClient.KB_BUCKETS["images"],
            object_name=object_name,
        )
    except StorageError as error:
        if "不存在" in str(error):
            raise HTTPException(status_code=404, detail="图片不存在")
        logger.error(f"读取知识库图片失败 {object_name}: {error}")
        raise HTTPException(status_code=500, detail="读取图片失败")

    content_type = minio_response.getheader("Content-Type") or "application/octet-stream"
    return _minio_streaming_response(
        minio_response,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


# --- 知识库查询分组 ---


@knowledge.post("/knowledge-bases/{kb_id}/query")
async def query_knowledge_base(
    kb_id: str,
    query: str = Body(...),
    meta: dict = Body(default_factory=dict),
    current_user: User = Depends(require_knowledge_base_read),
):
    """查询知识库"""
    logger.debug("Query knowledge base {}", kb_id)
    try:
        result = await query_with_overrides(kb_id, query, meta)
        return {"result": result, "status": "success"}
    except (HTTPException, DomainError):
        raise
    except Exception as e:
        logger.error(f"知识库查询失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="知识库查询失败，请稍后重试") from e


@knowledge.post("/knowledge-bases/{kb_id}/query-test")
async def query_test(
    kb_id: str,
    query: str = Body(...),
    meta: dict = Body(default_factory=dict),
    current_user: User = Depends(require_knowledge_base_read),
):
    """测试查询知识库"""
    logger.debug("Query test in {}", kb_id)
    try:
        result = await query_with_overrides(kb_id, query, meta)
        return result
    except (HTTPException, DomainError):
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"测试查询失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="知识库检索失败") from e


@knowledge.put("/knowledge-bases/{kb_id}/query-params")
async def update_knowledge_base_query_params(
    kb_id: str, params: dict = Body(...), current_user: User = Depends(require_knowledge_base_manage)
):
    """更新知识库查询参数配置"""
    try:
        await knowledge_base_manager.update_kb_query_params(kb_id, params)

        logger.info("更新知识库 {} 查询参数", kb_id)

        return {"message": "success", "data": params}

    except KBNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新知识库查询参数失败: {e}")
        raise HTTPException(status_code=500, detail="更新查询参数失败") from e


@knowledge.get("/knowledge-bases/{kb_id}/query-params")
async def get_knowledge_base_query_params(kb_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """获取知识库类型特定的查询参数"""
    try:
        params = await knowledge_base_manager.get_kb_query_params_config(kb_id)
        return {"params": params, "message": "success"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取知识库查询参数失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="获取查询参数失败") from e


# --- AI生成示例问题 ---


@knowledge.post("/knowledge-bases/{kb_id}/sample-questions")
async def generate_sample_questions(
    kb_id: str,
    request_body: dict = Body(...),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """AI生成针对知识库的测试问题。"""
    try:
        count = request_body.get("count", 10)
        return await generate_knowledge_base_sample_questions(kb_id, count=count)
    except (HTTPException, DomainError):
        raise
    except Exception as e:
        logger.error(f"生成知识库问题失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="生成问题失败") from e


@knowledge.get("/knowledge-bases/{kb_id}/sample-questions")
async def get_sample_questions(kb_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """获取知识库的测试问题。"""
    try:
        return await get_knowledge_base_sample_questions(kb_id)
    except (HTTPException, DomainError):
        raise
    except Exception as e:
        logger.error(f"获取知识库问题失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="获取问题失败") from e


# --- 文件管理分组 ---


@knowledge.post("/knowledge-bases/{kb_id}/folders")
async def create_folder(
    kb_id: str,
    folder_name: str = Body(..., embed=True),
    parent_id: str | None = Body(None, embed=True),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """创建文件夹"""
    try:
        await _ensure_knowledge_base_supports_documents(kb_id, "文件夹创建")
        return await knowledge_base_manager.create_folder(kb_id, folder_name, parent_id, current_user.uid)
    except (HTTPException, DomainError):
        raise
    except Exception as e:
        logger.error(f"创建文件夹失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="创建文件夹失败") from e


@knowledge.put("/knowledge-bases/{kb_id}/folders/{folder_id}/rename")
async def rename_folder(
    kb_id: str,
    folder_id: str,
    folder_name: str = Body(..., embed=True),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """重命名真实文件夹。"""
    try:
        await _ensure_knowledge_base_supports_documents(kb_id, "文件夹重命名")
        return await knowledge_base_manager.rename_folder(kb_id, folder_id, folder_name)
    except (HTTPException, DomainError):
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"重命名文件夹失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="重命名文件夹失败") from e


@knowledge.put("/knowledge-bases/{kb_id}/documents/{doc_id}/move")
async def move_document(
    kb_id: str,
    doc_id: str,
    request: MoveDocumentRequest,
    current_user: User = Depends(require_knowledge_base_manage),
):
    """移动文件或文件夹"""
    logger.debug("Move document {} in {}", doc_id, kb_id)
    try:
        await _ensure_knowledge_base_supports_documents(kb_id, "文件移动")
        return await knowledge_base_manager.move_file(kb_id, doc_id, request.new_parent_id)
    except (HTTPException, DomainError):
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"移动文件失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="移动文件失败") from e


@knowledge.post("/files/fetch-url")
async def fetch_url(
    url: str = Body(..., embed=True),
    kb_id: str | None = Body(None, embed=True),
    current_user: User = Depends(get_admin_user),
):
    """抓取 URL 内容并上传到 MinIO。"""
    logger.debug("Fetching URL for kb_id {}", kb_id)
    try:
        await _require_manage_permission_if_kb_id(kb_id, current_user)
        # 1. 下载内容 (包含白名单校验、大小限制、类型检查)
        content_bytes, final_url = await fetch_url_content(url)

        staged = await stage_document(kb_id=kb_id, filename=url, content=content_bytes, source="url")

        return {
            "status": "success",
            "file_path": staged.upload.url,
            "minio_url": staged.upload.url,
            "content_hash": staged.content_hash,
            "filename": url,  # 原始 URL 作为文件名
            "final_url": final_url,
            "size": len(content_bytes),
        }

    except (HTTPException, DomainError):
        raise
    except ValueError as e:
        logger.warning(f"URL fetch validation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to fetch URL {url}: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="抓取 URL 失败") from e


@knowledge.post("/files/import-workspace")
async def import_workspace_files(
    payload: WorkspaceImportRequest,
    current_user: User = Depends(get_admin_user),
):
    """将当前用户工作区文件导入 MinIO，返回与普通文件上传一致的预处理结果。"""
    kb_id = payload.kb_id.strip()
    paths = [path for path in payload.paths if str(path or "").strip()]
    if not kb_id:
        raise HTTPException(status_code=400, detail="kb_id is required")
    if not paths:
        raise HTTPException(status_code=400, detail="请选择至少一个工作区文件")

    await _require_manage_permission_if_kb_id(kb_id, current_user)
    await _ensure_knowledge_base_supports_documents(kb_id, "文档添加/解析/入库")

    imported = await administration.import_workspace_files(kb_id, paths, current_user)
    return {
        "status": "success",
        "items": [
            {
                "message": "Workspace file successfully imported",
                **serialize_staged_file(item["staged"], kb_id=kb_id, filename=item["filename"], size=item["size"]),
                "workspace_path": item["workspace_path"],
            }
            for item in imported
        ],
    }


@knowledge.post("/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    kb_id: str | None = Query(None),
    current_user: User = Depends(get_admin_user),
):
    """上传文件"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No selected file")

    if kb_id:
        await _require_manage_permission_if_kb_id(kb_id, current_user)
        await _ensure_knowledge_base_supports_documents(kb_id, "文档上传")

    logger.debug("Received upload file")

    ext = os.path.splitext(file.filename)[1].lower()

    if not is_supported_file_extension(file.filename):
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    try:
        file_bytes = await read_upload_with_limit(
            file,
            max_size_bytes=MAX_UPLOAD_SIZE_BYTES,
            too_large_message="文件过大，当前仅支持 100 MB 以内的文件",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    staged = await stage_document(kb_id=kb_id, filename=file.filename, content=file_bytes)
    return {
        "message": "File successfully uploaded",
        **serialize_staged_file(staged, kb_id=kb_id, filename=file.filename, size=len(file_bytes)),
    }


@knowledge.get("/files/supported-types")
async def get_supported_file_types(current_user: User = Depends(get_admin_user)):
    """获取当前支持的文件类型"""
    return {"message": "success", "file_types": sorted(SUPPORTED_FILE_EXTENSIONS)}


# --- 知识库类型分组 ---


@knowledge.get("/types")
async def get_knowledge_base_types(current_user: User = Depends(get_admin_user)):
    """获取支持的知识库类型"""
    try:
        kb_types = knowledge_base_manager.get_supported_kb_types()
        return {"kb_types": kb_types, "message": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取知识库类型失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="获取知识库类型失败") from e


@knowledge.get("/chunk-presets")
async def get_knowledge_chunk_presets(current_user: User = Depends(get_admin_user)):
    """获取支持的知识库分块策略"""
    return {"chunk_presets": get_chunk_preset_options(), "message": "success"}


@knowledge.get("/stats")
async def get_knowledge_base_statistics(current_user: User = Depends(get_admin_user)):
    """获取知识库统计信息"""
    try:
        stats = await knowledge_base_manager.get_statistics()
        return {"stats": stats, "message": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取知识库统计失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="获取知识库统计失败") from e


# --- 知识库 AI 辅助功能分组 ---


@knowledge.post("/generate-description")
async def generate_description(
    name: str = Body(..., description="知识库名称"),
    current_description: str = Body("", description="当前描述（可选，用于优化）"),
    file_list: list[str] | None = Body(None, description="文件列表"),
    current_user: User = Depends(get_admin_user),
):
    """使用 LLM 生成或优化知识库描述

    根据知识库名称和现有描述，使用 LLM 生成适合作为智能体工具描述的内容。
    """
    try:
        description = await administration.generate_description(name, current_description, file_list)
        return {"description": description, "status": "success"}
    except (HTTPException, DomainError):
        raise
    except Exception as error:
        logger.exception("生成知识库描述失败")
        raise HTTPException(status_code=500, detail="生成描述失败") from error


def _minio_streaming_response(minio_response, *, media_type: str, headers: dict | None = None) -> StreamingResponse:
    """把 MinIO 读取响应包装为流式 HTTP 响应，结束时归还连接。"""

    async def stream():
        try:
            while True:
                chunk = await asyncio.to_thread(minio_response.read, 8192)
                if not chunk:
                    break
                yield chunk
        finally:
            minio_response.close()
            minio_response.release_conn()

    return StreamingResponse(stream(), media_type=media_type, headers=headers)


def _strip_internal_graph_fields(lines: list[dict]) -> list[dict]:
    """移除行数据中内嵌的图谱抽取内部字段。"""
    internal_fields = {"ent_id", "ent_ids", "extraction_result"}
    return [{key: value for key, value in line.items() if key not in internal_fields} for line in lines]


async def _require_manage_permission_if_kb_id(kb_id: str | None, current_user: User) -> None:
    """当请求携带 kb_id 时，校验当前用户对该知识库的管理权限。"""
    if kb_id and getattr(current_user, "role", None):
        await ensure_knowledge_base_permission(kb_id, current_user, ResourcePermission.MANAGE)


async def _ensure_knowledge_base_supports_documents(kb_id: str, operation: str) -> dict:
    kb_info, supports_documents = await knowledge_base_manager.get_knowledge_base_document_support(kb_id)
    if not kb_info:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")
    kb_type = kb_info.kb_type.lower()
    if not supports_documents:
        raise HTTPException(status_code=400, detail=f"{kb_info.name or kb_type} 只支持检索，不支持{operation}")
    return kb_info


def _ensure_document_params(params: dict | None) -> dict:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be an object")
    try:
        normalize_chunk_preset_id(params.get("chunk_preset_id"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return params


def _validate_uploaded_document_items(items: list[str], params: dict) -> None:
    if not items:
        raise HTTPException(status_code=400, detail="items must not be empty")

    content_hashes = params.get("content_hashes")
    if content_hashes is not None and not isinstance(content_hashes, dict):
        raise HTTPException(status_code=400, detail="params.content_hashes must be an object")

    file_sizes = params.get("file_sizes")
    if file_sizes is not None and not isinstance(file_sizes, dict):
        raise HTTPException(status_code=400, detail="params.file_sizes must be an object")

    if "source_paths" in params and not isinstance(params["source_paths"], dict):
        raise HTTPException(status_code=400, detail="params.source_paths must be an object")

    preprocessed_map = params.get(PREPROCESSED_UPLOADS_KEY)
    if preprocessed_map is not None and not isinstance(preprocessed_map, dict):
        raise HTTPException(status_code=400, detail="params._preprocessed_map must be an object")

    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise HTTPException(status_code=400, detail="items must only contain non-empty strings")
        if not is_minio_url(item):
            raise HTTPException(status_code=400, detail="File source must be a MinIO URL")

        has_content_hash = isinstance(content_hashes, dict) and bool(content_hashes.get(item))
        preprocessed = preprocessed_map.get(item) if isinstance(preprocessed_map, dict) else None
        has_preprocessed_hash = isinstance(preprocessed, dict) and bool(preprocessed.get("content_hash"))
        if not has_content_hash and not has_preprocessed_hash:
            raise HTTPException(status_code=400, detail=f"Missing content_hash for file: {item}")


def _validate_direct_document_action_file_ids(file_ids: list[str]) -> list[str]:
    """校验直接提交的文件 ID 数量与内容，返回去空后的列表。"""
    normalized_file_ids = [file_id for file_id in file_ids if file_id]
    if not normalized_file_ids:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
    if len(normalized_file_ids) > MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS:
        raise HTTPException(
            status_code=400,
            detail=(f"单次最多支持 {MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS} 个文件，请使用待处理状态入口提交全量后台作业"),
        )
    return normalized_file_ids
