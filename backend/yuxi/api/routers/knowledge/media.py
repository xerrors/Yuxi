"""原生媒体上传、索引和已授权字节读取的 Web 管理适配。"""

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from starlette.responses import Response

from yuxi.api.dependencies.knowledge import require_knowledge_base_manage, require_knowledge_base_read
from yuxi.api.uploads import read_upload_with_limit
from yuxi.modules.identity.models import User
from yuxi.modules.knowledge.media import jobs, service, settings
from yuxi.modules.knowledge.media.schemas import (
    DocumentImageAutoIndexSettings,
    IndexMediaInput,
    InspectMediaInput,
    SearchMediaInput,
)
from yuxi.modules.knowledge.services.access import visible_knowledge_bases
from yuxi.modules.models.media import available_media_models
from yuxi.shared.errors import InvalidInputError

router = APIRouter(prefix="/knowledge/knowledge-bases/{kb_id}", tags=["knowledge-media"])


@router.get("/media/models")
async def media_models(kb_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """读取当前已启用的兼容媒体模型目录。"""
    return available_media_models()


@router.get("/media/document-image-auto-index")
async def document_image_auto_index_settings(kb_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """读取文档解析图片的自动索引设置。"""
    value = await settings.get_document_image_auto_index_settings(kb_id)
    return {"settings": value.model_dump()}


@router.put("/media/document-image-auto-index")
async def update_document_image_auto_index_settings(
    kb_id: str,
    payload: DocumentImageAutoIndexSettings,
    current_user: User = Depends(require_knowledge_base_manage),
):
    """保存文档图片自动索引设置。"""
    value = await settings.update_document_image_auto_index_settings(kb_id, current_user.uid, payload)
    return {"settings": value.model_dump()}


@router.post("/media-upload")
async def upload_media(
    kb_id: str,
    file: UploadFile = File(...),
    parent_id: str | None = Form(None),
    current_user: User = Depends(require_knowledge_base_manage),
):
    """原件独立登记为上传文件，格式与解码预算由媒体后台任务最终复核。"""
    if not file.filename:
        raise InvalidInputError("需要媒体文件名")
    data = await read_upload_with_limit(file, max_size_bytes=100 * 1024 * 1024, too_large_message="媒体超过 100 MiB")
    return await jobs.upload_media_source(kb_id, file.filename, data, current_user.uid, parent_id)


@router.post("/documents/{file_id}/media-index")
async def index_media(kb_id: str, file_id: str, payload: IndexMediaInput, current_user: User = Depends(require_knowledge_base_manage)):
    """提交媒体构建，响应引用既有持久后台作业。"""
    return await jobs.submit_media_index(kb_id, file_id, payload, current_user.uid)


@router.get("/media")
async def list_media(
    kb_id: str,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=100),
    file_id: str | None = None,
    current_user: User = Depends(require_knowledge_base_read),
):
    """发现当前可读片段及有界分页位置。"""
    from yuxi.modules.knowledge.media.schemas import ListMediaInput

    payload = ListMediaInput(kb_id=kb_id, offset=offset, limit=limit, file_ids=[file_id] if file_id else None)
    visible = await visible_knowledge_bases(current_user.uid, selection=[kb_id])
    return await service.list_kb_media(**payload.model_dump(), visible_kbs=visible, uid=current_user.uid)


@router.get("/documents/{file_id}/media-status")
async def media_status(kb_id: str, file_id: str, current_user: User = Depends(require_knowledge_base_read)):
    """读取当前发布版本和真实后台任务，失败仍可见。"""
    visible = await visible_knowledge_bases(current_user.uid, selection=[kb_id])
    return await service.get_media_status(kb_id, file_id, visible, uid=current_user.uid)


@router.post("/media/query")
async def query_media(kb_id: str, payload: SearchMediaInput, current_user: User = Depends(require_knowledge_base_read)):
    """管理端原生检索与 MCP 复用同一执行服务。"""
    if payload.kb_id != kb_id:
        raise InvalidInputError("媒体查询知识库身份不一致")
    visible = await visible_knowledge_bases(current_user.uid, selection=[kb_id])
    return await service.query_kb_media(**payload.model_dump(), visible_kbs=visible, uid=current_user.uid)


@router.get("/documents/{file_id}/media/{asset_id}")
async def read_media(kb_id: str, file_id: str, asset_id: str, revision: str, current_user: User = Depends(require_knowledge_base_read)):
    """只读取当前授权的指定修订资产，不公开 MinIO URL。"""
    visible = await visible_knowledge_bases(current_user.uid, selection=[kb_id])
    result = await service.read_kb_media(kb_id, file_id, asset_id, revision, visible, uid=current_user.uid)
    return Response(
        content=result["media_bytes"], media_type=result["mime_type"], headers={"ETag": result["asset_hash"], "Cache-Control": "no-store"}
    )


@router.post("/media/inspect")
async def inspect_media(kb_id: str, payload: InspectMediaInput, current_user: User = Depends(require_knowledge_base_read)):
    """用原生模型核对资产并返回可重读来源，保留观察与原件的区别。"""
    if payload.kb_id != kb_id:
        raise InvalidInputError("媒体核对知识库身份不一致")
    visible = await visible_knowledge_bases(current_user.uid, selection=[kb_id])
    return await service.inspect_kb_media(**payload.model_dump(), visible_kbs=visible, uid=current_user.uid)
