from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from yuxi.knowledge.runtime import knowledge_base
from yuxi.knowledge.base import KBNotFoundError
from yuxi.knowledge.read_models import KnowledgeBaseSummary
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger

from server.utils.auth_middleware import get_required_user

external_kb = APIRouter(prefix="/knowledge", tags=["knowledge"])


class ExternalRetrieveRequest(BaseModel):
    query: str
    file_name: str | None = None
    options: dict | None = None


class ExternalFindRequest(BaseModel):
    patterns: list[str]
    use_regex: bool = False
    case_sensitive: bool = False
    max_windows: int = 5
    window_size: int = 80


@external_kb.get("/databases/external")
async def list_external_databases(current_user: User = Depends(get_required_user)):
    """列出当前登录用户可见的知识库，供 CLI 选择与展示。"""
    databases = await knowledge_base.get_databases_by_uid(current_user.uid)
    items = []
    for db in databases:
        kb_type = db.kb_type.lower()
        items.append(
            {
                "kb_id": db.kb_id,
                "name": db.name,
                "description": db.description or "",
                "kb_type": kb_type,
                "supports_documents": knowledge_base.database_type_supports_documents(kb_type),
            }
        )
    return {"databases": items}


@external_kb.get("/databases/external/{kb_id}/files")
async def list_external_files(
    kb_id: str,
    query: str | None = Query(None, description="按文件名关键词搜索"),
    offset: int = Query(0, ge=0, description="偏移量，从 0 开始"),
    limit: int = Query(100, ge=1, le=500, description="每页数量"),
    status: str = Query("all", description="文件状态筛选"),
    current_user: User = Depends(get_required_user),
):
    """列出或搜索知识库文件，供 CLI 浏览与定位。"""
    database = await knowledge_base.get_accessible_database_info_by_uid(current_user.uid, kb_id)
    if not database:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在或无权访问")
    if not knowledge_base.database_type_supports_documents(database.kb_type):
        raise HTTPException(
            status_code=400,
            detail=f"{database.name or database.kb_type} 只支持检索，不支持文档查看",
        )
    return await knowledge_base.search_document_files(
        [{"kb_id": database.kb_id, "name": database.name}],
        query=query,
        offset=offset,
        limit=limit,
        status=status,
        include_is_folder=True,
        include_parent_id=True,
    )


@external_kb.post("/databases/external/{kb_id}/retrieve")
async def retrieve_external(
    kb_id: str,
    payload: ExternalRetrieveRequest,
    current_user: User = Depends(get_required_user),
):
    """对知识库执行检索查询，返回结构化结果。"""
    if not payload.query:
        raise HTTPException(status_code=400, detail="query is required")
    await _require_accessible_kb(kb_id, current_user.uid)
    options = dict(payload.options or {})
    if payload.file_name:
        options["file_name"] = payload.file_name
    try:
        return await knowledge_base.retrieve(kb_id, payload.query, **options)
    except KBNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"external 知识库查询失败 {e}")
        raise HTTPException(status_code=400, detail=f"知识库查询失败: {e}") from e


@external_kb.get("/databases/external/{kb_id}/files/{file_id}/open")
async def open_external_file(
    kb_id: str,
    file_id: str,
    offset: int = Query(0, ge=0, description="起始行偏移"),
    limit: int = Query(200, ge=1, le=1800, description="返回行数"),
    current_user: User = Depends(get_required_user),
):
    """按行窗口打开文件解析后的 Markdown 内容。"""
    await _require_accessible_kb(kb_id, current_user.uid, require_documents=True, operation="文档查看")
    try:
        return await knowledge_base.open_document(kb_id, file_id, offset=offset, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"external 打开知识库文件失败 {e}")
        raise HTTPException(status_code=400, detail="打开知识库文件失败") from e


@external_kb.post("/databases/external/{kb_id}/files/{file_id}/find")
async def find_external_file(
    kb_id: str,
    file_id: str,
    payload: ExternalFindRequest,
    current_user: User = Depends(get_required_user),
):
    """在指定文件内做关键词或正则定位，返回匹配窗口。"""
    await _require_accessible_kb(kb_id, current_user.uid, require_documents=True, operation="文档查找")
    if not payload.patterns:
        raise HTTPException(status_code=400, detail="patterns 不能为空")
    try:
        return await knowledge_base.find_in_document(
            kb_id,
            file_id,
            payload.patterns,
            use_regex=payload.use_regex,
            case_sensitive=payload.case_sensitive,
            max_windows=payload.max_windows,
            window_size=payload.window_size,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"external 知识库文件内检索失败 {e}")
        raise HTTPException(status_code=400, detail="知识库文件内检索失败") from e


async def _require_accessible_kb(
    kb_id: str,
    uid: str,
    *,
    require_documents: bool = False,
    operation: str = "文档查看",
) -> KnowledgeBaseSummary:
    """校验知识库对 uid 可见，必要时同时校验文档能力。"""
    database = await knowledge_base.get_accessible_database_info_by_uid(uid, str(kb_id or "").strip())
    if not database:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在或无权访问")
    if require_documents and not knowledge_base.database_type_supports_documents(database.kb_type):
        kb_type = database.kb_type.lower()
        raise HTTPException(status_code=400, detail=f"{database.name or kb_type} 只支持检索，不支持{operation}")
    return database
