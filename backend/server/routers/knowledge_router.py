import asyncio
import os
import textwrap
import time
import traceback
from urllib.parse import quote, unquote

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.responses import StreamingResponse
from yuxi import config, knowledge_base
from yuxi.knowledge.factory import KnowledgeBaseFactory
from yuxi.knowledge.graphs.milvus_graph_service import GRAPH_TASK_TYPE, MilvusGraphService
from yuxi.knowledge.parser import SUPPORTED_FILE_EXTENSIONS, Parser, is_supported_file_extension
from yuxi.knowledge.utils import calculate_content_hash, is_minio_url, parse_minio_url
from yuxi.knowledge.utils.mindmap_utils import (
    generate_database_mindmap,
    get_database_mindmap_data,
    get_mindmap_database_files,
    get_mindmap_databases_overview,
    get_mindmap_diff,
)
from yuxi.knowledge.utils.sample_question_utils import (
    generate_database_sample_questions,
    get_database_sample_questions,
)
from yuxi.knowledge.utils.url_fetcher import fetch_url_content
from yuxi.models.providers.cache import model_cache
from yuxi.services.task_service import TaskContext, tasker
from yuxi.services.workspace_service import MAX_WORKSPACE_UPLOAD_SIZE_BYTES, resolve_workspace_file_path
from yuxi.storage.minio.client import MinIOClient, StorageError, aupload_file_to_minio, get_minio_client
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger
from yuxi.utils.upload_utils import MAX_UPLOAD_SIZE_BYTES, read_upload_with_limit, write_upload_to_path

from server.utils.auth_middleware import get_admin_user, get_required_user

knowledge = APIRouter(prefix="/knowledge", tags=["knowledge"])

ACTIVE_GRAPH_BUILD_STATUSES = {"pending", "running"}
ACTIVE_DOCUMENT_ACTION_TASK_STATUSES = {"pending", "running"}
DOCUMENT_ACTION_BATCH_SIZE = 500
DOCUMENT_ACTION_RESULT_ITEM_LIMIT = 200
MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS = 1000
PENDING_PARSE_STATUSES = ["uploaded"]
PENDING_INDEX_STATUSES = ["parsed", "error_indexing"]


class UpdateDatabaseRequest(BaseModel):
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


async def _delete_document_storage_objects(kb_id: str, doc_id: str, file_path: str) -> None:
    minio_client = get_minio_client()

    if is_minio_url(file_path):
        try:
            bucket_name, object_name = parse_minio_url(file_path)
            await minio_client.adelete_file(bucket_name, object_name)
        except Exception as minio_error:
            logger.warning(f"从MinIO删除原始文件失败: {minio_error}")

    try:
        await minio_client.adelete_file(minio_client.KB_BUCKETS["parsed"], f"{kb_id}/parsed/{doc_id}.md")
    except Exception as minio_error:
        logger.warning(f"从MinIO删除解析结果失败: {minio_error}")

    try:
        await minio_client.adelete_file(minio_client.KB_BUCKETS["parsed"], f"{kb_id}/preview/{doc_id}.pdf")
    except Exception as minio_error:
        logger.warning(f"从MinIO删除预览 PDF 失败: {minio_error}")


async def _ensure_database_supports_documents(kb_id: str, operation: str) -> None:
    db_info = await knowledge_base.get_database_info(kb_id)
    if not db_info:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")
    kb_type = (db_info.get("kb_type") or "").lower()
    kb_class = KnowledgeBaseFactory.get_kb_class(kb_type)
    if not kb_class.supports_documents:
        raise HTTPException(status_code=400, detail=f"{db_info.get('name') or kb_type} 只支持检索，不支持{operation}")


def _ensure_document_params(params: dict | None) -> dict:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be an object")
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

    preprocessed_map = params.get("_preprocessed_map")
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


def _params_for_uploaded_document_item(item: str, params: dict) -> dict:
    source_paths = params.get("source_paths")
    item_params = dict(params)
    item_params.pop("source_paths", None)
    if isinstance(source_paths, dict) and source_paths.get(item):
        item_params["source_path"] = source_paths[item]
    return item_params


async def _has_running_graph_build_task(kb_id: str) -> bool:
    return (
        await tasker.find_task_by_payload(
            task_type=GRAPH_TASK_TYPE,
            payload_match={"kb_id": kb_id},
            statuses=ACTIVE_GRAPH_BUILD_STATUSES,
        )
        is not None
    )


# =============================================================================
# === 知识库管理分组 ===
# =============================================================================


@knowledge.get("/databases")
async def get_databases(current_user: User = Depends(get_admin_user)):
    """获取所有知识库（根据用户权限过滤）"""
    try:
        return await knowledge_base.get_databases_by_uid(current_user.uid)
    except Exception as e:
        logger.error(f"获取数据库列表失败 {e}, {traceback.format_exc()}")
        return {"message": f"获取数据库列表失败 {e}", "databases": []}


@knowledge.post("/databases")
async def create_database(
    database_name: str = Body(...),
    description: str = Body(...),
    embedding_model_spec: str | None = Body(None),
    kb_type: str = Body("milvus"),
    additional_params: dict | None = Body(None),
    llm_model_spec: str | None = Body(None),
    share_config: dict | None = Body(None),
    current_user: User = Depends(get_admin_user),
):
    """创建知识库"""
    logger.debug(
        f"Create database {database_name} with kb_type {kb_type}, "
        f"additional_params {additional_params}, llm_model_spec {llm_model_spec}, "
        f"embedding_model_spec {embedding_model_spec}, share_config {share_config}"
    )
    try:
        # 先检查名称是否已存在
        if await knowledge_base.database_name_exists(database_name):
            raise HTTPException(
                status_code=409,
                detail=f"知识库名称 '{database_name}' 已存在，请使用其他名称",
            )

        if not KnowledgeBaseFactory.is_type_supported(kb_type):
            raise HTTPException(status_code=400, detail=f"Unsupported knowledge base type: {kb_type}")

        kb_class = KnowledgeBaseFactory.get_kb_class(kb_type)

        additional_params = {**(additional_params or {})}
        additional_params["auto_generate_questions"] = False  # 默认不生成问题

        if "reranker_config" in additional_params:
            raise HTTPException(
                status_code=400,
                detail="reranker_config 已移除，请在查询参数中使用 reranker_model spec",
            )
        additional_params = kb_class.normalize_additional_params(additional_params)

        if kb_class.requires_embedding_model:
            if not embedding_model_spec:
                raise HTTPException(status_code=400, detail="embedding_model_spec 不能为空")

            info = model_cache.get_model_info(embedding_model_spec)
            if not info or info.model_type != "embedding":
                raise HTTPException(status_code=400, detail=f"不支持的 embedding 模型: {embedding_model_spec}")
        else:
            embedding_model_spec = None

        database_info = await knowledge_base.create_database(
            database_name,
            description,
            kb_type=kb_type,
            embedding_model_spec=embedding_model_spec,
            llm_model_spec=llm_model_spec,
            share_config=share_config,
            created_by=current_user.uid,
            created_by_department_id=current_user.department_id,
            **additional_params,
        )

        # 需要重新加载所有智能体，因为工具刷新了
        from yuxi.agents.buildin import agent_manager

        await agent_manager.reload_all()

        return database_info
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建数据库失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"创建数据库失败: {e}")


@knowledge.get("/databases/accessible")
async def get_accessible_databases(current_user: User = Depends(get_required_user)):
    """获取当前用户有权访问的知识库列表（用于智能体配置）"""
    try:
        databases = await knowledge_base.get_databases_by_uid(current_user.uid)

        accessible = [
            {
                "name": db.get("name", ""),
                "kb_id": db.get("kb_id"),
                "description": db.get("description", ""),
                "created_by": db.get("created_by"),
                "kb_type": db.get("kb_type"),
                "supports_documents": KnowledgeBaseFactory.get_kb_class(
                    (db.get("kb_type") or "milvus").lower()
                ).supports_documents,
            }
            for db in databases.get("databases", [])
        ]

        return {"databases": accessible}
    except Exception as e:
        logger.error(f"获取可访问知识库列表失败: {e}, {traceback.format_exc()}")
        return {"message": f"获取可访问知识库列表失败: {str(e)}", "databases": []}


@knowledge.get("/mindmap/databases")
async def get_mindmap_databases(current_user: User = Depends(get_admin_user)):
    """获取所有知识库的概览信息，用于思维导图界面选择。"""
    try:
        return await get_mindmap_databases_overview(current_user.uid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取知识库列表失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取知识库列表失败: {str(e)}")


@knowledge.get("/databases/{kb_id}/mindmap/files")
async def get_database_mindmap_files(kb_id: str, current_user: User = Depends(get_admin_user)):
    """获取指定知识库的所有文件列表。"""
    try:
        return await get_mindmap_database_files(kb_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取文件列表失败: {str(e)}")


@knowledge.post("/databases/{kb_id}/mindmap/generate")
async def generate_mindmap(
    kb_id: str,
    file_ids: list[str] | None = Body(default=None, description="选择的文件ID列表"),
    user_prompt: str = Body(default="", description="用户自定义提示词"),
    incremental: bool = Body(default=False, description="是否增量更新"),
    current_user: User = Depends(get_admin_user),
):
    """使用 AI 分析知识库文件，生成思维导图结构。支持增量更新模式。"""
    try:
        return await generate_database_mindmap(kb_id, file_ids, user_prompt, incremental)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成思维导图失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"生成思维导图失败: {str(e)}")


@knowledge.get("/databases/{kb_id}/mindmap")
async def get_database_mindmap(kb_id: str, current_user: User = Depends(get_admin_user)):
    """获取知识库关联的思维导图。"""
    try:
        return await get_database_mindmap_data(kb_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取知识库思维导图失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取知识库思维导图失败: {str(e)}")


@knowledge.get("/databases/{kb_id}/mindmap/diff")
async def get_mindmap_diff_route(kb_id: str, current_user: User = Depends(get_admin_user)):
    """检测思维导图与知识库文件的变更差异。"""
    try:
        return await get_mindmap_diff(kb_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"检测思维导图变更失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"检测思维导图变更失败: {str(e)}")


@knowledge.get("/databases/{kb_id}")
async def get_database_info(
    kb_id: str,
    include_files: bool = Query(False, description="是否包含全量文件列表，默认关闭以避免大知识库响应过大"),
    current_user: User = Depends(get_admin_user),
):
    """获取知识库详细信息"""
    database = await knowledge_base.get_database_info(kb_id, include_files=include_files)
    if database is None:
        raise HTTPException(status_code=404, detail="Database not found")
    return database


@knowledge.post("/databases/{kb_id}/stats/repair")
async def repair_database_stats(kb_id: str, current_user: User = Depends(get_admin_user)):
    """修复知识库历史文件缺失的 Chunk/Token 统计。"""
    await _ensure_database_supports_documents(kb_id, "统计修复")
    try:
        return await knowledge_base.repair_missing_file_stats(kb_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"修复知识库统计失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"修复知识库统计失败: {e}")


@knowledge.put("/databases/{kb_id}")
async def update_database_info(
    kb_id: str,
    data: UpdateDatabaseRequest,
    current_user: User = Depends(get_admin_user),
):
    """更新知识库信息"""
    logger.debug(
        f"[update_database_info] 接收到的参数: name={data.name}, llm_model_spec={data.llm_model_spec}, "
        f"additional_params={data.additional_params}, share_config={data.share_config}"
    )
    try:
        update_llm_model_spec = "llm_model_spec" in data.model_fields_set

        additional_params = data.additional_params
        if additional_params is not None:
            db_info = await knowledge_base.get_database_info(kb_id)
            if not db_info:
                raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

            kb_type = (db_info.get("kb_type") or "").lower()
            kb_class = KnowledgeBaseFactory.get_kb_class(kb_type)
            merged_params = dict(db_info.get("additional_params") or {})
            merged_params.update(additional_params)
            kb_class.normalize_additional_params(merged_params)
            additional_params = (
                kb_class.normalize_additional_params(additional_params)
                if kb_class.apply_chunk_defaults
                else kb_class.normalize_additional_params(merged_params)
            )

        database = await knowledge_base.update_database(
            kb_id,
            data.name,
            data.description,
            data.llm_model_spec,
            update_llm_model_spec=update_llm_model_spec,
            additional_params=additional_params,
            share_config=data.share_config,
            operator_uid=current_user.uid,
            operator_department_id=current_user.department_id,
        )
        return {"message": "更新成功", "database": database}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新数据库失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"更新数据库失败: {e}")


@knowledge.delete("/databases/{kb_id}")
async def delete_database(kb_id: str, current_user: User = Depends(get_admin_user)):
    """删除知识库"""
    logger.debug(f"Delete database {kb_id}")
    try:
        await knowledge_base.delete_database(kb_id)

        # 需要重新加载所有智能体，因为工具刷新了
        from yuxi.agents.buildin import agent_manager

        await agent_manager.reload_all()

        return {"message": "删除成功"}
    except Exception as e:
        logger.error(f"删除数据库失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"删除数据库失败: {e}")


@knowledge.get("/databases/{kb_id}/graph-build/status")
async def get_graph_build_status(kb_id: str, current_user: User = Depends(get_admin_user)):
    try:
        return await MilvusGraphService().get_status(kb_id, tasker=tasker)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"获取图谱构建状态失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取图谱构建状态失败: {e}")


@knowledge.post("/databases/{kb_id}/graph-build/config")
async def configure_graph_build(
    kb_id: str,
    data: dict = Body(...),
    current_user: User = Depends(get_admin_user),
):
    try:
        config = await MilvusGraphService().configure(
            kb_id,
            extractor_type=data.get("extractor_type"),
            extractor_options=data.get("extractor_options") or {},
            created_by=current_user.uid,
        )
        return {"message": "图谱抽取配置已锁定", "status": "success", "config": config}
    except ValueError as e:
        status_code = 409 if "已锁定" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))
    except Exception as e:
        logger.error(f"配置图谱构建失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"配置图谱构建失败: {e}")


@knowledge.post("/databases/{kb_id}/graph-build/index")
async def index_graph_build(
    kb_id: str,
    data: dict | None = Body(default=None),
    current_user: User = Depends(get_admin_user),
):
    data = data or {}
    try:
        if await _has_running_graph_build_task(kb_id):
            raise HTTPException(status_code=409, detail="该知识库已有正在运行的图谱构建任务")

        database = await knowledge_base.get_database_info(kb_id)
        if not database:
            raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

        batch_size = max(1, min(int(data.get("batch_size") or 20), 200))
        service = MilvusGraphService()
        graph_status = await service.get_status(kb_id)
        if not graph_status.get("locked"):
            raise HTTPException(status_code=400, detail="请先确认并锁定图谱抽取配置")

        async def run_graph_index(context: TaskContext):
            await context.set_message("任务初始化")
            await context.set_progress(5.0, "准备构建图谱")
            result = await service.build_pending_chunks(kb_id, batch_size=batch_size, context=context)
            await context.set_result(result)
            await context.set_progress(100.0, f"图谱构建完成，成功 {result['success']} 个，失败 {result['failed']} 个")
            return result

        task, created = await tasker.enqueue_unique_by_payload(
            name=f"图谱构建 ({database['name']})",
            task_type=GRAPH_TASK_TYPE,
            payload={"kb_id": kb_id, "batch_size": batch_size},
            coroutine=run_graph_index,
            payload_match={"kb_id": kb_id},
            statuses=ACTIVE_GRAPH_BUILD_STATUSES,
        )
        if not created:
            raise HTTPException(status_code=409, detail="该知识库已有正在运行的图谱构建任务")
        return {"message": "图谱构建任务已提交", "status": "queued", "task_id": task.id}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"提交图谱构建任务失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"提交图谱构建任务失败: {e}")


@knowledge.post("/databases/{kb_id}/graph-build/reset")
async def reset_graph_build(
    kb_id: str,
    data: dict | None = Body(default=None),
    current_user: User = Depends(get_admin_user),
):
    data = data or {}
    try:
        if await _has_running_graph_build_task(kb_id):
            raise HTTPException(status_code=409, detail="该知识库存在正在运行的图谱构建任务，无法重置")

        return await MilvusGraphService().reset(
            kb_id,
            clear_extraction_result=bool(data.get("clear_extraction_result", True)),
            clear_config=bool(data.get("clear_config", False)),
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"重置图谱构建状态失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"重置图谱构建状态失败: {e}")


@knowledge.get("/databases/{kb_id}/export")
async def export_database(
    kb_id: str,
    format: str = Query("csv", enum=["csv", "xlsx", "md", "txt"]),
    include_vectors: bool = Query(False, description="是否在导出中包含向量数据"),
    current_user: User = Depends(get_admin_user),
):
    """导出知识库数据"""
    logger.debug(f"Exporting database {kb_id} with format {format}")
    try:
        file_path = await knowledge_base.export_data(kb_id, format=format, include_vectors=include_vectors)

        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Exported file not found.")

        media_type = media_types.get(f".{format}", "application/octet-stream")

        return FileResponse(path=file_path, filename=os.path.basename(file_path), media_type=media_type)
    except HTTPException:
        raise
    except NotImplementedError as e:
        logger.warning(f"A disabled feature was accessed: {e}")
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.error(f"导出数据库失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"导出数据库失败: {e}")


# =============================================================================
# === 知识库文档管理分组 ===
# =============================================================================


@knowledge.get("/databases/{kb_id}/documents")
async def list_documents(
    kb_id: str,
    parent_id: str | None = Query(None, description="父文件夹 ID，空值表示根目录"),
    path_prefix: str | None = Query(None, description="路径型目录前缀，用于懒加载 source_path 形成的虚拟目录"),
    status: str = Query("all", description="文件状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(100, ge=1, le=500, description="每页数量"),
    recursive: bool = Query(False, description="是否跨目录筛选"),
    current_user: User = Depends(get_admin_user),
):
    """分页获取知识库文件列表。"""
    await _ensure_database_supports_documents(kb_id, "文档查看")
    try:
        return await knowledge_base.list_document_files(
            kb_id,
            parent_id=parent_id,
            path_prefix=path_prefix,
            status=status,
            page=page,
            page_size=page_size,
            recursive=recursive,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@knowledge.get("/databases/{kb_id}/documents/exists")
async def document_file_exists(
    kb_id: str,
    filename: str = Query(..., min_length=1, description="知识库文件展示名或相对路径"),
    current_user: User = Depends(get_admin_user),
):
    """检查知识库中是否已存在指定文件名或相对路径的文件。"""
    await _ensure_database_supports_documents(kb_id, "文档存在性检查")
    normalized_filename = filename.strip()
    if not normalized_filename:
        raise HTTPException(status_code=400, detail="filename is required")
    try:
        exists = await knowledge_base.document_file_exists(kb_id, normalized_filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"kb_id": kb_id, "filename": normalized_filename, "exists": exists}


@knowledge.post("/databases/{kb_id}/documents")
async def add_documents(
    kb_id: str, items: list[str] = Body(...), params: dict = Body(...), current_user: User = Depends(get_admin_user)
):
    """添加文档到知识库（上传 -> 解析 -> 可选入库）"""
    logger.debug(f"Add documents for kb_id {kb_id}: {items} {params=}")
    await _ensure_database_supports_documents(kb_id, "文档添加/解析/入库")

    params = _ensure_document_params(params)
    content_type = params.get("content_type", "file")
    # 自动入库参数
    auto_index = params.get("auto_index", False)
    indexing_params = {}
    chunk_preset_id = params.get("chunk_preset_id")
    if chunk_preset_id:
        indexing_params["chunk_preset_id"] = chunk_preset_id

    chunk_parser_config = params.get("chunk_parser_config")
    if isinstance(chunk_parser_config, dict):
        indexing_params["chunk_parser_config"] = chunk_parser_config

    if content_type == "url":
        raise HTTPException(status_code=400, detail="URL 处理方式已变更，请使用 fetch-url 接口先获取内容")
    if content_type != "file":
        raise HTTPException(status_code=400, detail=f"Unsupported content_type: {content_type}")

    _validate_uploaded_document_items(items, params)

    async def run_ingest(context: TaskContext):
        await context.set_message("任务初始化")
        await context.set_progress(5.0, "准备处理文档")

        total = len(items)
        processed_items: list[dict | None] = [None] * total
        added_files: list[dict] = []

        try:
            await context.set_message("第一阶段：添加文件记录")
            for idx, item in enumerate(items, 1):
                await context.raise_if_cancelled()

                progress = 5.0 + (idx / total) * 25.0
                await context.set_progress(progress, f"[1/3] 添加记录 {idx}/{total}")

                try:
                    file_meta = await knowledge_base.add_file_record(
                        kb_id, item, params=params, operator_id=current_user.uid
                    )
                    added_files.append(
                        {
                            "index": idx - 1,
                            "item": item,
                            "file_id": file_meta["file_id"],
                            "file_meta": file_meta,
                        }
                    )
                except Exception as add_error:
                    logger.error(f"添加文件记录失败 {item}: {add_error}")
                    error_type = "timeout" if isinstance(add_error, TimeoutError) else "add_failed"
                    error_msg = "添加超时" if isinstance(add_error, TimeoutError) else "添加记录失败"
                    processed_items[idx - 1] = {
                        "item": item,
                        "status": "failed",
                        "error": f"{error_msg}: {str(add_error)}",
                        "error_type": error_type,
                    }

            await context.set_message("第二阶段：解析文件")
            parse_end = 60.0 if auto_index else 95.0
            parse_total = len(added_files)
            for idx, record in enumerate(added_files, 1):
                await context.raise_if_cancelled()

                progress = 30.0 + (idx / parse_total) * (parse_end - 30.0)
                await context.set_progress(progress, f"[2/3] 解析文件 {idx}/{parse_total}")

                item = record["item"]
                file_id = record["file_id"]
                try:
                    file_meta = await knowledge_base.parse_file(kb_id, file_id, operator_id=current_user.uid)
                    record["file_meta"] = file_meta
                    if not auto_index or file_meta.get("status") != "parsed":
                        processed_items[record["index"]] = file_meta
                except Exception as parse_error:
                    logger.error(f"解析文件失败 {item} (file_id={file_id}): {parse_error}")
                    error_type = "timeout" if isinstance(parse_error, TimeoutError) else "parse_failed"
                    error_msg = "解析超时" if isinstance(parse_error, TimeoutError) else "解析失败"
                    processed_items[record["index"]] = {
                        "item": item,
                        "status": "failed",
                        "error": f"{error_msg}: {str(parse_error)}",
                        "error_type": error_type,
                    }

            if auto_index:
                await context.set_message("第三阶段：自动入库")
                parsed_files = [record for record in added_files if record["file_meta"].get("status") == "parsed"]
                total_parsed = len(parsed_files)

                for idx, record in enumerate(parsed_files, 1):
                    await context.raise_if_cancelled()

                    progress = 60.0 + (idx / total_parsed) * 35.0
                    await context.set_progress(progress, f"[3/3] 入库文件 {idx}/{total_parsed}")

                    item = record["item"]
                    file_id = record["file_id"]
                    try:
                        await knowledge_base.update_file_params(
                            kb_id, file_id, indexing_params, operator_id=current_user.uid
                        )
                        result = await knowledge_base.index_file(
                            kb_id, file_id, operator_id=current_user.uid, params=indexing_params
                        )
                        processed_items[record["index"]] = result
                    except Exception as index_error:
                        logger.error(f"自动入库失败 {item} (file_id={file_id}): {index_error}")
                        processed_items[record["index"]] = {
                            "item": item,
                            "status": "failed",
                            "error": f"入库失败: {str(index_error)}",
                            "error_type": "index_failed",
                        }

        except asyncio.CancelledError:
            await context.set_progress(100.0, "任务已取消")
            raise
        except Exception as task_error:
            logger.exception(f"Task processing failed: {task_error}")
            await context.set_progress(100.0, f"任务处理失败: {str(task_error)}")
            raise

        final_items = [
            item
            if item is not None
            else {
                "item": items[index],
                "status": "failed",
                "error": "文件未处理",
                "error_type": "not_processed",
            }
            for index, item in enumerate(processed_items)
        ]
        failed_count = len([item for item in final_items if "error" in item or item.get("status") == "failed"])

        summary = {
            "kb_id": kb_id,
            "item_type": "文件",
            "submitted": total,
            "failed": failed_count,
        }
        message = f"文件处理完成，失败 {failed_count} 个" if failed_count else "文件处理完成"
        await context.set_result(summary | {"items": final_items})
        await context.set_progress(100.0, message)

        if failed_count:
            raise RuntimeError(message)

        return summary | {"items": final_items}

    try:
        database = await knowledge_base.get_database_info(kb_id)
        task = await tasker.enqueue(
            name=f"知识库文档处理 ({database['name']})",
            task_type="knowledge_ingest",
            payload={
                "kb_id": kb_id,
                "items": items,
                "params": params,
                "content_type": content_type,
            },
            coroutine=run_ingest,
        )
        return {
            "message": "任务已提交，请在任务中心查看进度",
            "status": "queued",
            "task_id": task.id,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to enqueue {content_type}s: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to enqueue task: {e}")


@knowledge.post("/databases/{kb_id}/documents/add")
async def add_uploaded_documents(
    kb_id: str,
    payload: AddUploadedDocumentsRequest,
    current_user: User = Depends(get_admin_user),
):
    """将已上传的 MinIO 文件同步添加为知识库文档记录，不解析、不入库。"""
    logger.debug(f"Add uploaded documents for kb_id {kb_id}: {payload.items} params={payload.params}")
    await _ensure_database_supports_documents(kb_id, "文档添加")

    params = _ensure_document_params(payload.params)
    content_type = params.get("content_type", "file")
    if content_type == "url":
        raise HTTPException(status_code=400, detail="URL 处理方式已变更，请使用 fetch-url 接口先获取内容")
    if content_type != "file":
        raise HTTPException(status_code=400, detail=f"Unsupported content_type: {content_type}")

    _validate_uploaded_document_items(payload.items, params)

    added_items: list[dict] = []
    failed_items: list[dict] = []
    for index, item in enumerate(payload.items):
        try:
            file_meta = await knowledge_base.add_file_record(
                kb_id,
                item,
                params=_params_for_uploaded_document_item(item, params),
                operator_id=current_user.uid,
            )
            added_items.append(
                {
                    "index": index,
                    "item": item,
                    "file_id": file_meta["file_id"],
                    "status": file_meta.get("status"),
                    "file_meta": file_meta,
                }
            )
        except Exception as add_error:  # noqa: BLE001
            logger.error(f"添加文件记录失败 {item}: {add_error}")
            failed_items.append(
                {
                    "index": index,
                    "item": item,
                    "status": "failed",
                    "error": f"添加记录失败: {str(add_error)}",
                    "error_type": "add_failed",
                }
            )

    failed_count = len(failed_items)
    added_count = len(added_items)
    if failed_count == 0:
        status = "success"
        message = f"已添加 {added_count} 个文件"
    elif added_count == 0:
        status = "failed"
        message = f"文件添加失败，失败 {failed_count} 个"
    else:
        status = "partial_failed"
        message = f"已添加 {added_count} 个文件，失败 {failed_count} 个"

    return {
        "message": message,
        "status": status,
        "items": added_items,
        "failed_items": failed_items,
        "added": added_count,
        "failed": failed_count,
    }


def _validate_direct_document_action_file_ids(file_ids: list[str]) -> list[str]:
    normalized_file_ids = [file_id for file_id in file_ids if file_id]
    if not normalized_file_ids:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
    if len(normalized_file_ids) > MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS:
        raise HTTPException(
            status_code=400,
            detail=(f"单次最多支持 {MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS} 个文件，请使用待处理状态入口提交全量后台任务"),
        )
    return normalized_file_ids


def _append_document_action_result_sample(items: list[dict], item: dict) -> None:
    if len(items) < DOCUMENT_ACTION_RESULT_ITEM_LIMIT:
        items.append(item)


async def _run_parse_file_ids(
    *,
    context: TaskContext,
    kb_id: str,
    file_ids: list[str],
    operator_id: str,
) -> dict:
    await context.set_message("任务初始化")
    await context.set_progress(5.0, "准备解析文档")

    total = len(file_ids)
    processed_items = []

    for idx, file_id in enumerate(file_ids, 1):
        await context.raise_if_cancelled()
        progress = 5.0 + (idx / total) * 90.0
        await context.set_progress(progress, f"正在解析第 {idx}/{total} 个文档")

        try:
            result = await knowledge_base.parse_file(kb_id, file_id, operator_id=operator_id)
            processed_items.append(result)
        except Exception as e:
            logger.error(f"Parse failed for {file_id}: {e}")
            processed_items.append({"file_id": file_id, "status": "failed", "error": str(e)})

    failed_count = len([p for p in processed_items if "error" in p])
    message = f"解析完成，失败 {failed_count} 个"
    result_payload = {"items": processed_items, "processed": len(processed_items), "failed": failed_count}
    await context.set_result(result_payload)
    await context.set_progress(100.0, message)
    return result_payload


async def _run_index_file_ids(
    *,
    context: TaskContext,
    kb_id: str,
    file_ids: list[str],
    operator_id: str,
    params: dict,
) -> dict:
    await context.set_message("任务初始化")
    await context.set_progress(5.0, "准备入库文档")

    total = len(file_ids)
    processed_items = []
    param_update_failed = set()

    if params:
        for file_id in file_ids:
            try:
                await knowledge_base.update_file_params(kb_id, file_id, params, operator_id=operator_id)
            except Exception as e:
                logger.error(f"Failed to update params for {file_id}: {e}")
                param_update_failed.add(file_id)
                processed_items.append({"file_id": file_id, "status": "failed", "error": f"参数更新失败: {str(e)}"})

    for idx, file_id in enumerate(file_ids, 1):
        await context.raise_if_cancelled()

        if file_id in param_update_failed:
            logger.debug(f"Skipping {file_id} due to param update failure")
            continue

        progress = 5.0 + (idx / total) * 90.0
        await context.set_progress(progress, f"正在入库第 {idx}/{total} 个文档")

        try:
            result = await knowledge_base.index_file(kb_id, file_id, operator_id=operator_id, params=params)
            processed_items.append(result)
        except Exception as e:
            logger.error(f"Index failed for {file_id}: {e}")
            processed_items.append({"file_id": file_id, "status": "failed", "error": str(e)})

    failed_count = len([p for p in processed_items if "error" in p])
    message = f"入库完成，失败 {failed_count} 个"
    result_payload = {"items": processed_items, "processed": len(processed_items), "failed": failed_count}
    await context.set_result(result_payload)
    await context.set_progress(100.0, message)
    return result_payload


async def _run_parse_pending_statuses(
    *,
    context: TaskContext,
    kb_id: str,
    statuses: list[str],
    initial_total: int,
    operator_id: str,
) -> dict:
    await context.set_message("任务初始化")
    await context.set_progress(5.0, "准备解析待处理文档")

    processed_count = 0
    failed_count = 0
    result_items = []
    after_file_id = None

    while True:
        file_ids = await knowledge_base.list_document_file_ids_by_statuses(
            kb_id,
            statuses=statuses,
            after_file_id=after_file_id,
            limit=DOCUMENT_ACTION_BATCH_SIZE,
        )
        if not file_ids:
            break

        for file_id in file_ids:
            await context.raise_if_cancelled()
            after_file_id = file_id
            processed_count += 1
            progress_total = max(initial_total, processed_count)
            progress = 5.0 + (processed_count / progress_total) * 90.0
            await context.set_progress(progress, f"正在解析第 {processed_count}/{progress_total} 个文档")

            try:
                result = await knowledge_base.parse_file(kb_id, file_id, operator_id=operator_id)
                _append_document_action_result_sample(result_items, result)
            except Exception as e:
                failed_count += 1
                logger.error(f"Parse failed for {file_id}: {e}")
                _append_document_action_result_sample(
                    result_items,
                    {"file_id": file_id, "status": "failed", "error": str(e)},
                )

    message = f"解析完成，失败 {failed_count} 个" if processed_count else "没有待解析文档"
    result_payload = {
        "items": result_items,
        "processed": processed_count,
        "failed": failed_count,
        "result_truncated": processed_count > len(result_items),
    }
    await context.set_result(result_payload)
    await context.set_progress(100.0, message)
    return result_payload


async def _run_index_pending_statuses(
    *,
    context: TaskContext,
    kb_id: str,
    statuses: list[str],
    initial_total: int,
    operator_id: str,
    params: dict,
) -> dict:
    await context.set_message("任务初始化")
    await context.set_progress(5.0, "准备入库待处理文档")

    processed_count = 0
    failed_count = 0
    result_items = []
    after_file_id = None

    while True:
        file_ids = await knowledge_base.list_document_file_ids_by_statuses(
            kb_id,
            statuses=statuses,
            after_file_id=after_file_id,
            limit=DOCUMENT_ACTION_BATCH_SIZE,
        )
        if not file_ids:
            break

        for file_id in file_ids:
            await context.raise_if_cancelled()
            after_file_id = file_id
            processed_count += 1
            progress_total = max(initial_total, processed_count)
            progress = 5.0 + (processed_count / progress_total) * 90.0
            await context.set_progress(progress, f"正在入库第 {processed_count}/{progress_total} 个文档")

            try:
                if params:
                    await knowledge_base.update_file_params(kb_id, file_id, params, operator_id=operator_id)
                result = await knowledge_base.index_file(kb_id, file_id, operator_id=operator_id, params=params)
                _append_document_action_result_sample(result_items, result)
            except Exception as e:
                failed_count += 1
                logger.error(f"Index failed for {file_id}: {e}")
                _append_document_action_result_sample(
                    result_items,
                    {"file_id": file_id, "status": "failed", "error": str(e)},
                )

    message = f"入库完成，失败 {failed_count} 个" if processed_count else "没有待入库文档"
    result_payload = {
        "items": result_items,
        "processed": processed_count,
        "failed": failed_count,
        "result_truncated": processed_count > len(result_items),
    }
    await context.set_result(result_payload)
    await context.set_progress(100.0, message)
    return result_payload


@knowledge.post("/databases/{kb_id}/documents/parse")
async def parse_documents(kb_id: str, file_ids: list[str] = Body(...), current_user: User = Depends(get_admin_user)):
    """手动触发文档解析"""
    file_ids = _validate_direct_document_action_file_ids(file_ids)
    logger.debug(f"Parse documents for kb_id {kb_id}: {file_ids}")
    await _ensure_database_supports_documents(kb_id, "文档解析")

    async def run_parse(context: TaskContext):
        try:
            return await _run_parse_file_ids(
                context=context,
                kb_id=kb_id,
                file_ids=file_ids,
                operator_id=current_user.uid,
            )
        except Exception as e:
            logger.exception(f"Parse task failed: {e}")
            raise

    try:
        database = await knowledge_base.get_database_info(kb_id)
        task = await tasker.enqueue(
            name=f"文档解析 ({database['name']})",
            task_type="knowledge_parse",
            payload={"kb_id": kb_id, "file_ids": file_ids},
            coroutine=run_parse,
        )
        return {"message": "解析任务已提交", "status": "queued", "task_id": task.id}
    except Exception as e:
        return {"message": f"提交失败: {e}", "status": "failed"}


@knowledge.post("/databases/{kb_id}/documents/parse-pending")
async def parse_pending_documents(kb_id: str, current_user: User = Depends(get_admin_user)):
    """按状态手动触发全部待解析文档解析。"""
    logger.debug(f"Parse pending documents for kb_id {kb_id}")
    await _ensure_database_supports_documents(kb_id, "文档解析")

    try:
        database = await knowledge_base.get_database_info(kb_id)
        pending_count = int((database.get("stats") or {}).get("pending_parse_count") or 0)
        if pending_count <= 0:
            return {"message": "没有待解析文档", "status": "success", "queued_count": 0}

        async def run_parse(context: TaskContext):
            try:
                return await _run_parse_pending_statuses(
                    context=context,
                    kb_id=kb_id,
                    statuses=PENDING_PARSE_STATUSES,
                    initial_total=pending_count,
                    operator_id=current_user.uid,
                )
            except Exception as e:
                logger.exception(f"Pending parse task failed: {e}")
                raise

        task, created = await tasker.enqueue_unique_by_payload(
            name=f"待解析文档解析 ({database['name']})",
            task_type="knowledge_parse",
            payload={
                "kb_id": kb_id,
                "scope": "pending",
                "action": "parse",
                "statuses": PENDING_PARSE_STATUSES,
                "count": pending_count,
            },
            payload_match={"kb_id": kb_id, "scope": "pending", "action": "parse"},
            statuses=ACTIVE_DOCUMENT_ACTION_TASK_STATUSES,
            coroutine=run_parse,
        )
        return {
            "message": "解析任务已提交" if created else "已有待解析任务正在执行",
            "status": "queued",
            "task_id": task.id,
            "queued_count": pending_count,
        }
    except Exception as e:
        return {"message": f"提交失败: {e}", "status": "failed"}


@knowledge.post("/databases/{kb_id}/documents/index")
async def index_documents(
    kb_id: str,
    file_ids: list[str] = Body(...),
    params: dict | None = Body(None),
    current_user: User = Depends(get_admin_user),
):
    """手动触发文档入库（Indexing），支持更新参数"""
    file_ids = _validate_direct_document_action_file_ids(file_ids)
    params = params or {}
    logger.debug(f"Index documents for kb_id {kb_id}: {file_ids} {params=}")
    await _ensure_database_supports_documents(kb_id, "文档入库")

    operator_id = current_user.uid

    async def run_index(context: TaskContext):
        try:
            return await _run_index_file_ids(
                context=context,
                kb_id=kb_id,
                file_ids=file_ids,
                operator_id=operator_id,
                params=params,
            )
        except Exception as e:
            logger.exception(f"Index task failed: {e}")
            raise

    try:
        database = await knowledge_base.get_database_info(kb_id)
        task = await tasker.enqueue(
            name=f"文档入库 ({database['name']})",
            task_type="knowledge_index",
            payload={"kb_id": kb_id, "file_ids": file_ids, "params": params},
            coroutine=run_index,
        )
        return {"message": "入库任务已提交", "status": "queued", "task_id": task.id}
    except Exception as e:
        return {"message": f"提交失败: {e}", "status": "failed"}


@knowledge.post("/databases/{kb_id}/documents/index-pending")
async def index_pending_documents(
    kb_id: str,
    payload: PendingIndexDocumentsRequest | None = None,
    current_user: User = Depends(get_admin_user),
):
    """按状态手动触发全部待入库文档入库。"""
    params = payload.params if payload else None
    params = params or {}
    logger.debug(f"Index pending documents for kb_id {kb_id}: {params=}")
    await _ensure_database_supports_documents(kb_id, "文档入库")

    try:
        database = await knowledge_base.get_database_info(kb_id)
        pending_count = int((database.get("stats") or {}).get("pending_index_count") or 0)
        if pending_count <= 0:
            return {"message": "没有待入库文档", "status": "success", "queued_count": 0}

        operator_id = current_user.uid

        async def run_index(context: TaskContext):
            try:
                return await _run_index_pending_statuses(
                    context=context,
                    kb_id=kb_id,
                    statuses=PENDING_INDEX_STATUSES,
                    initial_total=pending_count,
                    operator_id=operator_id,
                    params=params,
                )
            except Exception as e:
                logger.exception(f"Pending index task failed: {e}")
                raise

        task, created = await tasker.enqueue_unique_by_payload(
            name=f"待入库文档入库 ({database['name']})",
            task_type="knowledge_index",
            payload={
                "kb_id": kb_id,
                "scope": "pending",
                "action": "index",
                "statuses": PENDING_INDEX_STATUSES,
                "count": pending_count,
                "params": params,
            },
            payload_match={"kb_id": kb_id, "scope": "pending", "action": "index"},
            statuses=ACTIVE_DOCUMENT_ACTION_TASK_STATUSES,
            coroutine=run_index,
        )
        return {
            "message": "入库任务已提交" if created else "已有待入库任务正在执行",
            "status": "queued",
            "task_id": task.id,
            "queued_count": pending_count,
        }
    except Exception as e:
        return {"message": f"提交失败: {e}", "status": "failed"}


@knowledge.get("/databases/{kb_id}/documents/{doc_id}")
async def get_document_info(kb_id: str, doc_id: str, current_user: User = Depends(get_admin_user)):
    """获取文档详细信息（包含基本信息和内容信息）"""
    logger.debug(f"GET document {doc_id} info in {kb_id}")
    await _ensure_database_supports_documents(kb_id, "文档查看")

    try:
        info = await knowledge_base.get_file_info(kb_id, doc_id)
        return info
    except Exception as e:
        logger.error(f"Failed to get file info, {e}, {kb_id=}, {doc_id=}, {traceback.format_exc()}")
        return {"message": "Failed to get file info", "status": "failed"}


@knowledge.get("/databases/{kb_id}/documents/{doc_id}/basic")
async def get_document_basic_info(kb_id: str, doc_id: str, current_user: User = Depends(get_admin_user)):
    """获取文档基本信息（仅元数据）"""
    logger.debug(f"GET document {doc_id} basic info in {kb_id}")
    await _ensure_database_supports_documents(kb_id, "文档查看")

    try:
        info = await knowledge_base.get_file_basic_info(kb_id, doc_id)
        return info
    except Exception as e:
        logger.error(f"Failed to get file basic info, {e}, {kb_id=}, {doc_id=}, {traceback.format_exc()}")
        return {"message": "Failed to get file basic info", "status": "failed"}


@knowledge.get("/databases/{kb_id}/documents/{doc_id}/content")
async def get_document_content(kb_id: str, doc_id: str, current_user: User = Depends(get_admin_user)):
    """获取文档内容信息（chunks和lines）"""
    logger.debug(f"GET document {doc_id} content in {kb_id}")
    await _ensure_database_supports_documents(kb_id, "文档查看")

    try:
        info = await knowledge_base.get_file_content(kb_id, doc_id)
        return info
    except Exception as e:
        logger.error(f"Failed to get file content, {e}, {kb_id=}, {doc_id=}, {traceback.format_exc()}")
        return {"message": "Failed to get file content", "status": "failed"}


@knowledge.delete("/databases/{kb_id}/documents/batch")
async def batch_delete_documents(
    kb_id: str, file_ids: list[str] = Body(...), current_user: User = Depends(get_admin_user)
):
    """批量删除文档或文件夹"""
    logger.debug(f"BATCH DELETE documents {file_ids} in {kb_id}")
    await _ensure_database_supports_documents(kb_id, "批量文档删除")

    deleted_count = 0
    failed_items = []
    mindmap_removals: list[tuple[str, str]] = []

    for doc_id in file_ids:
        try:
            file_meta_info = await knowledge_base.get_file_basic_info(kb_id, doc_id)

            # Check if it is a folder
            is_folder = file_meta_info.get("meta", {}).get("is_folder", False)
            if is_folder:
                await knowledge_base.delete_folder(kb_id, doc_id)
                deleted_count += 1
                continue

            file_path = file_meta_info.get("meta", {}).get("path", "")

            await _delete_document_storage_objects(kb_id, doc_id, file_path)

            # 收集待清理的文件名（循环结束后统一清理导图）
            removed_filename = file_meta_info.get("meta", {}).get("filename", "")
            if removed_filename:
                mindmap_removals.append((doc_id, removed_filename))

            # 无论MinIO删除是否成功，都继续从知识库删除
            await knowledge_base.delete_file(kb_id, doc_id)
            deleted_count += 1
        except Exception as e:
            logger.error(f"批量删除过程中删除文档 {doc_id} 失败: {e}, {traceback.format_exc()}")
            failed_items.append({"doc_id": doc_id, "error": str(e)})

    if failed_items:
        if deleted_count == 0:
            raise HTTPException(status_code=400, detail=f"批量删除失败: 所有 {len(failed_items)} 个文件均未删除。")
        return {
            "message": f"部分删除成功: 已删除 {deleted_count} 个文件，失败 {len(failed_items)} 个",
            "deleted_count": deleted_count,
            "failed_items": failed_items,
        }

    return {"message": f"批量删除成功: 已删除 {deleted_count} 个文件", "deleted_count": deleted_count}


@knowledge.delete("/databases/{kb_id}/documents/{doc_id}")
async def delete_document(kb_id: str, doc_id: str, current_user: User = Depends(get_admin_user)):
    """删除文档或文件夹"""
    logger.debug(f"DELETE document {doc_id} info in {kb_id}")
    await _ensure_database_supports_documents(kb_id, "文档删除")
    try:
        file_meta_info = await knowledge_base.get_file_basic_info(kb_id, doc_id)

        # Check if it is a folder
        is_folder = file_meta_info.get("meta", {}).get("is_folder", False)
        if is_folder:
            await knowledge_base.delete_folder(kb_id, doc_id)
            return {"message": "文件夹删除成功"}

        file_path = file_meta_info.get("meta", {}).get("path", "")

        await _delete_document_storage_objects(kb_id, doc_id, file_path)

        # 无论MinIO删除是否成功，都继续从知识库删除
        await knowledge_base.delete_file(kb_id, doc_id)
        return {"message": "删除成功"}
    except Exception as e:
        logger.error(f"删除文档失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"删除文档失败: {e}")


@knowledge.get("/databases/{kb_id}/documents/{doc_id}/download")
async def download_document(kb_id: str, doc_id: str, current_user: User = Depends(get_admin_user)):
    """下载原始文件"""
    logger.debug(f"Download document {doc_id} from {kb_id}")
    await _ensure_database_supports_documents(kb_id, "文档下载")
    try:
        file_info = await knowledge_base.get_file_basic_info(kb_id, doc_id)
        file_meta = file_info.get("meta", {})

        # 获取文件类型、路径和文件名
        file_type = file_meta.get("file_type", "file")
        file_path = file_meta.get("path", "")
        filename = file_meta.get("filename", "file")

        # URL 类型文件没有原始文件可下载
        if file_type == "url":
            raise HTTPException(status_code=400, detail="URL 类型文件不支持下载原始文件")
        logger.debug(f"File path from database: {file_path}")
        logger.debug(f"Original filename from database: {filename}")

        # 解码URL编码的文件名（如果有的话）
        try:
            decoded_filename = unquote(filename, encoding="utf-8")
            logger.debug(f"Decoded filename: {decoded_filename}")
        except Exception as e:
            logger.debug(f"Failed to decode filename {filename}: {e}")
            decoded_filename = filename  # 如果解码失败，使用原文件名

        _, ext = os.path.splitext(decoded_filename)
        media_type = media_types.get(ext.lower(), "application/octet-stream")

        if not is_minio_url(file_path):
            raise HTTPException(status_code=400, detail="文件路径必须是 MinIO URL")

        logger.debug(f"Downloading from MinIO: {file_path}")

        try:
            bucket_name, object_name = parse_minio_url(file_path)
            logger.debug(f"Parsed bucket_name: {bucket_name}, object_name: {object_name}")

            minio_client = get_minio_client()

            # 直接使用解析出的完整对象名称下载
            minio_response = await minio_client.adownload_response(
                bucket_name=bucket_name,
                object_name=object_name,
            )
            logger.debug(f"Successfully downloaded object: {object_name}")

        except Exception as e:
            logger.error(f"Failed to download MinIO file: {e}")
            raise StorageError(f"下载文件失败: {e}")

        # 创建流式生成器
        async def minio_stream():
            try:
                while True:
                    chunk = await asyncio.to_thread(minio_response.read, 8192)
                    if not chunk:
                        break
                    yield chunk
            finally:
                minio_response.close()
                minio_response.release_conn()

        response = StreamingResponse(
            minio_stream(),
            media_type=media_type,
        )
        try:
            decoded_filename.encode("ascii")
            response.headers["Content-Disposition"] = f'attachment; filename="{decoded_filename}"'
        except UnicodeEncodeError:
            encoded_filename = quote(decoded_filename.encode("utf-8"))
            response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_filename}"

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载文件失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"下载失败: {e}")


# =============================================================================
# === 知识库查询分组 ===
# =============================================================================


@knowledge.post("/databases/{kb_id}/query")
async def query_knowledge_base(
    kb_id: str, query: str = Body(...), meta: dict = Body(...), current_user: User = Depends(get_admin_user)
):
    """查询知识库"""
    logger.debug(f"Query knowledge base {kb_id}: {query}")
    try:
        result = await knowledge_base.aquery(query, kb_id=kb_id, **meta)
        return {"result": result, "status": "success"}
    except Exception as e:
        logger.error(f"知识库查询失败 {e}, {traceback.format_exc()}")
        return {"message": f"知识库查询失败: {e}", "status": "failed"}


@knowledge.post("/databases/{kb_id}/query-test")
async def query_test(
    kb_id: str, query: str = Body(...), meta: dict = Body(...), current_user: User = Depends(get_admin_user)
):
    """测试查询知识库"""
    logger.debug(f"Query test in {kb_id}: {query}")
    try:
        result = await knowledge_base.aquery(query, kb_id=kb_id, **meta)
        return result
    except Exception as e:
        logger.error(f"测试查询失败 {e}, {traceback.format_exc()}")
        return {"message": f"测试查询失败: {e}", "status": "failed"}


@knowledge.put("/databases/{kb_id}/query-params")
async def update_knowledge_base_query_params(
    kb_id: str, params: dict = Body(...), current_user: User = Depends(get_admin_user)
):
    """更新知识库查询参数配置"""
    try:
        # 获取知识库实例
        kb_instance = await knowledge_base._get_kb_for_database(kb_id)
        if not kb_instance:
            raise HTTPException(status_code=404, detail="Knowledge base not found")

        # 更新实例元数据中的查询参数
        async with knowledge_base._metadata_lock:
            # 确保 kb_id 在实例的 databases_meta 中
            if kb_id not in kb_instance.databases_meta:
                raise HTTPException(status_code=404, detail="Database not found in instance metadata")

            # 确保 query_params 不为 None
            if kb_instance.databases_meta[kb_id].get("query_params") is None:
                kb_instance.databases_meta[kb_id]["query_params"] = {}

            options = kb_instance.databases_meta[kb_id]["query_params"].setdefault("options", {})
            options.update(params)
            updated_query_params = kb_instance.databases_meta[kb_id]["query_params"]

        # 直接通过 Repository 更新单条记录，避免调用 _save_metadata() 遍历所有数据库和文件
        from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

        kb_repo = KnowledgeBaseRepository()
        await kb_repo.update(kb_id, {"query_params": updated_query_params})

        logger.info(f"更新知识库 {kb_id} 查询参数: {params}")

        return {"message": "success", "data": params}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新知识库查询参数失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新查询参数失败: {str(e)}")


@knowledge.get("/databases/{kb_id}/query-params")
async def get_knowledge_base_query_params(kb_id: str, current_user: User = Depends(get_admin_user)):
    """获取知识库类型特定的查询参数"""
    try:
        # 获取知识库实例
        kb_instance = await knowledge_base._get_kb_for_database(kb_id)

        # 调用知识库实例的方法获取配置
        params = kb_instance.get_query_params_config(kb_id=kb_id)

        # 获取用户保存的配置并合并（从实例 metadata 读取）
        saved_options = kb_instance._get_query_params(kb_id)
        if saved_options:
            params = _merge_saved_options(params, saved_options)

        return {"params": params, "message": "success"}

    except Exception as e:
        logger.error(f"获取知识库查询参数失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


def _merge_saved_options(params: dict, saved_options: dict) -> dict:
    """将用户保存的配置合并到默认配置中"""
    for option in params.get("options", []):
        key = option.get("key")
        if key in saved_options:
            option["default"] = saved_options[key]
    return params


# =============================================================================
# === AI生成示例问题 ===
# =============================================================================


@knowledge.post("/databases/{kb_id}/sample-questions")
async def generate_sample_questions(
    kb_id: str,
    request_body: dict = Body(...),
    current_user: User = Depends(get_admin_user),
):
    """AI生成针对知识库的测试问题。"""
    try:
        count = request_body.get("count", 10)
        return await generate_database_sample_questions(kb_id, count=count)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成知识库问题失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"生成问题失败: {str(e)}")


@knowledge.get("/databases/{kb_id}/sample-questions")
async def get_sample_questions(kb_id: str, current_user: User = Depends(get_admin_user)):
    """获取知识库的测试问题。"""
    try:
        return await get_database_sample_questions(kb_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取知识库问题失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取问题失败: {str(e)}")


# =============================================================================
# === 文件管理分组 ===
# =============================================================================


@knowledge.post("/databases/{kb_id}/folders")
async def create_folder(
    kb_id: str,
    folder_name: str = Body(..., embed=True),
    parent_id: str | None = Body(None, embed=True),
    current_user: User = Depends(get_admin_user),
):
    """创建文件夹"""
    try:
        await _ensure_database_supports_documents(kb_id, "文件夹创建")
        return await knowledge_base.create_folder(kb_id, folder_name, parent_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建文件夹失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@knowledge.put("/databases/{kb_id}/documents/{doc_id}/move")
async def move_document(
    kb_id: str,
    doc_id: str,
    new_parent_id: str | None = Body(..., embed=True),
    current_user: User = Depends(get_admin_user),
):
    """移动文件或文件夹"""
    logger.debug(f"Move document {doc_id} to {new_parent_id} in {kb_id}")
    try:
        await _ensure_database_supports_documents(kb_id, "文件移动")
        return await knowledge_base.move_file(kb_id, doc_id, new_parent_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"移动文件失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@knowledge.post("/files/fetch-url")
async def fetch_url(
    url: str = Body(..., embed=True),
    kb_id: str | None = Body(None, embed=True),
    current_user: User = Depends(get_admin_user),
):
    """
    抓取 URL 内容并上传到 MinIO
    """
    logger.debug(f"Fetching URL: {url} for kb_id: {kb_id}")
    try:
        # 1. 下载内容 (包含白名单校验、大小限制、类型检查)
        content_bytes, final_url = await fetch_url_content(url)

        # 2. 计算 Hash
        content_hash = await calculate_content_hash(content_bytes)

        # 检查是否已存在相同内容的文件
        if kb_id:
            file_exists = await knowledge_base.file_existed_in_db(kb_id, content_hash)
            if file_exists:
                raise HTTPException(
                    status_code=409,
                    detail="数据库中已经存在了相同内容文件",
                )

        # 3. 上传到 MinIO
        minio_client = get_minio_client()
        bucket_name = MinIOClient.KB_BUCKETS["documents"]
        await asyncio.to_thread(minio_client.ensure_bucket_exists, bucket_name)

        folder = kb_id if kb_id else "unknown"
        object_name = f"{folder}/upload/{content_hash}.html"

        upload_result = await minio_client.aupload_file(
            bucket_name=bucket_name,
            object_name=object_name,
            data=content_bytes,
            content_type="text/html",
        )

        # 检测同名文件（URL即为文件名）
        same_name_files = []
        has_same_name = False
        if kb_id:
            same_name_files = await knowledge_base.get_same_name_files(kb_id, url)
            has_same_name = len(same_name_files) > 0

        return {
            "status": "success",
            "file_path": upload_result.url,
            "minio_url": upload_result.url,
            "content_hash": content_hash,
            "filename": url,  # 原始 URL 作为文件名
            "final_url": final_url,
            "size": len(content_bytes),
            "has_same_name": has_same_name,
            "same_name_files": same_name_files,
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"URL fetch validation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to fetch URL {url}: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch URL: {str(e)}")


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

    await _ensure_database_supports_documents(kb_id, "文档添加/解析/入库")

    bucket_name = MinIOClient.KB_BUCKETS["documents"]
    results = []
    for workspace_path in paths:
        target = resolve_workspace_file_path(path=workspace_path, current_user=current_user)

        filename = target.name
        ext = os.path.splitext(filename)[1].lower()
        if not is_supported_file_extension(filename):
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

        size = target.stat().st_size
        if size > MAX_WORKSPACE_UPLOAD_SIZE_BYTES:
            raise HTTPException(status_code=400, detail="文件过大，当前仅支持 100 MB 以内的工作区文件")

        file_bytes = await asyncio.to_thread(target.read_bytes)
        content_hash = await calculate_content_hash(file_bytes)

        file_exists = await knowledge_base.file_existed_in_db(kb_id, content_hash)
        if file_exists:
            raise HTTPException(status_code=409, detail=f"数据库中已经存在了相同内容文件: {filename}")

        basename, ext = os.path.splitext(filename)
        timestamp = int(time.time() * 1000)
        minio_filename = f"{basename}_{timestamp}{ext}"
        object_name = f"{kb_id}/upload/{minio_filename}"
        minio_url = await aupload_file_to_minio(bucket_name, object_name, file_bytes)

        normalized_filename = filename.lower()
        same_name_files = await knowledge_base.get_same_name_files(kb_id, normalized_filename)
        results.append(
            {
                "message": "Workspace file successfully imported",
                "file_path": minio_url,
                "minio_path": minio_url,
                "kb_id": kb_id,
                "content_hash": content_hash,
                "filename": normalized_filename,
                "original_filename": basename,
                "size": len(file_bytes),
                "minio_filename": minio_filename,
                "object_name": object_name,
                "bucket_name": bucket_name,
                "workspace_path": workspace_path,
                "same_name_files": same_name_files,
                "has_same_name": len(same_name_files) > 0,
            }
        )

    return {"status": "success", "items": results}


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
        await _ensure_database_supports_documents(kb_id, "文档上传")

    logger.debug(f"Received upload file with filename: {file.filename}")

    ext = os.path.splitext(file.filename)[1].lower()

    if not is_supported_file_extension(file.filename):
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    basename, ext = os.path.splitext(file.filename)
    # 直接使用原始文件名（小写）
    filename = f"{basename}{ext}".lower()

    try:
        file_bytes = await read_upload_with_limit(
            file,
            max_size_bytes=MAX_UPLOAD_SIZE_BYTES,
            too_large_message="文件过大，当前仅支持 100 MB 以内的文件",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    content_hash = await calculate_content_hash(file_bytes)

    file_exists = await knowledge_base.file_existed_in_db(kb_id, content_hash)
    if file_exists:
        raise HTTPException(
            status_code=409,
            detail="数据库中已经存在了相同内容文件，File with the same content already exists in this database",
        )

    # 直接上传到MinIO，添加时间戳区分版本
    timestamp = int(time.time() * 1000)
    minio_filename = f"{basename}_{timestamp}{ext}"

    bucket_name = MinIOClient.KB_BUCKETS["documents"]
    folder = kb_id if kb_id else "unknown"
    object_name = f"{folder}/upload/{minio_filename}"

    # 上传到MinIO
    minio_url = await aupload_file_to_minio(bucket_name, object_name, file_bytes)

    # 检测同名文件（基于原始文件名）
    same_name_files = await knowledge_base.get_same_name_files(kb_id, filename)
    has_same_name = len(same_name_files) > 0

    return {
        "message": "File successfully uploaded",
        "file_path": minio_url,  # MinIO路径作为主要路径
        "minio_path": minio_url,  # MinIO路径
        "kb_id": kb_id,
        "content_hash": content_hash,
        "filename": filename,  # 原始文件名（小写）
        "original_filename": basename,  # 原始文件名（去掉后缀）
        "size": len(file_bytes),
        "minio_filename": minio_filename,  # MinIO中的文件名（带时间戳）
        "object_name": object_name,
        "bucket_name": bucket_name,  # MinIO存储桶名称
        "same_name_files": same_name_files,  # 同名文件列表
        "has_same_name": has_same_name,  # 是否包含同名文件标志
    }


@knowledge.get("/files/supported-types")
async def get_supported_file_types(current_user: User = Depends(get_admin_user)):
    """获取当前支持的文件类型"""
    return {"message": "success", "file_types": sorted(SUPPORTED_FILE_EXTENSIONS)}


@knowledge.post("/files/markdown")
async def mark_it_down(file: UploadFile = File(...), current_user: User = Depends(get_admin_user)):
    """调用统一 Parser 将文件解析为 markdown，需要管理员权限"""
    import tempfile

    if not file.filename:
        return {"message": "文件解析失败: 无法识别文件名", "markdown_content": ""}

    suffix = os.path.splitext(file.filename)[1].lower()
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name

        await write_upload_to_path(
            file,
            temp_path,
            max_size_bytes=MAX_UPLOAD_SIZE_BYTES,
            too_large_message="文件过大，当前仅支持 100 MB 以内的文件",
        )

        markdown_content = await Parser.aparse(temp_path)
        return {"markdown_content": markdown_content, "message": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"文件解析失败 {e}, {traceback.format_exc()}")
        return {"message": f"文件解析失败 {e}", "markdown_content": ""}
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception as cleanup_error:
                logger.warning(f"临时文件清理失败 {temp_path}: {cleanup_error}")


# =============================================================================
# === 知识库类型分组 ===
# =============================================================================


@knowledge.get("/types")
async def get_knowledge_base_types(current_user: User = Depends(get_admin_user)):
    """获取支持的知识库类型"""
    try:
        kb_types = knowledge_base.get_supported_kb_types()
        return {"kb_types": kb_types, "message": "success"}
    except Exception as e:
        logger.error(f"获取知识库类型失败 {e}, {traceback.format_exc()}")
        return {"message": f"获取知识库类型失败 {e}", "kb_types": {}}


@knowledge.get("/stats")
async def get_knowledge_base_statistics(current_user: User = Depends(get_admin_user)):
    """获取知识库统计信息"""
    try:
        stats = await knowledge_base.get_statistics()
        return {"stats": stats, "message": "success"}
    except Exception as e:
        logger.error(f"获取知识库统计失败 {e}, {traceback.format_exc()}")
        return {"message": f"获取知识库统计失败 {e}", "stats": {}}


# =============================================================================
# === 知识库 AI 辅助功能分组 ===
# =============================================================================


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
    from yuxi.models import select_model

    file_list = file_list or []
    logger.debug(f"Generating description for knowledge base: {name}, files: {len(file_list)}")

    # 构建文件列表文本
    if file_list:
        # 限制文件数量，避免 prompt 过长
        display_files = file_list[:50]
        files_str = "\n".join([f"- {f}" for f in display_files])
        more_text = f"\n... (还有 {len(file_list) - 50} 个文件)" if len(file_list) > 50 else ""
        current_description += f"\n\n知识库包含的文件:\n{files_str}{more_text}"

    current_description = current_description or "暂无描述"

    # 构建提示词
    prompt = textwrap.dedent(f"""
        请帮我优化以下知识库的描述。

        知识库名称: {name}
        当前描述: {current_description}

        要求:
        1. 这个描述将作为智能体工具的描述使用
        2. 智能体会根据知识库的标题和描述来选择合适的工具
        3. 所以描述需要清晰、具体，说明该知识库包含什么内容、适合解答什么类型的问题
        4. 描述应该简洁有力，通常 2-4 句话即可
        5. 不要使用 Markdown 格式
        {"6. 请参考提供的文件列表来准确概括知识库内容" if file_list else ""}

        请直接输出优化后的描述，不要有任何前缀说明。
    """).strip()

    try:
        model = select_model(model_spec=config.default_model)
        response = await model.call(prompt)
        description = response.content.strip()
        logger.debug(f"Generated description: {description}")
        return {"description": description, "status": "success"}
    except Exception as e:
        logger.error(f"生成描述失败: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"生成描述失败: {e}")
