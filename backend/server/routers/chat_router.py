import traceback
import uuid
from typing import Any

import aiofiles
from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import User
from server.utils.auth_middleware import get_db, get_required_user
from yuxi import config as conf
from yuxi.models import select_model
from yuxi.services.chat_service import get_agent_state_view
from yuxi.services.conversation_service import (
    confirm_tmp_thread_attachments_view,
    create_thread_view,
    delete_thread_attachment_view,
    delete_thread_view,
    get_thread_history_view,
    list_thread_attachments_view,
    list_threads_view,
    parse_tmp_attachment_view,
    search_threads_view,
    update_thread_view,
    upload_thread_attachment_view,
    upload_tmp_attachment_view,
)
from yuxi.services.file_preview import detect_media_type
from yuxi.services.thread_files_service import (
    list_thread_files_view,
    read_thread_file_content_view,
    resolve_thread_artifact_view,
    save_thread_artifact_to_workspace_view,
)
from yuxi.services.feedback_service import get_message_feedback_view, submit_message_feedback_view
from yuxi.utils.logging_config import logger
from yuxi.utils.image_processor import process_uploaded_image
from yuxi.utils.paths import VIRTUAL_PATH_PREFIX


# TODO：当前文件的功能过于庞杂，路由标签混乱


# 图片上传响应模型
class ImageUploadResponse(BaseModel):
    success: bool
    image_content: str | None = None
    thumbnail_content: str | None = None
    width: int | None = None
    height: int | None = None
    format: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    error: str | None = None


chat = APIRouter(prefix="/chat", tags=["chat"])


@chat.post("/call")
async def call(query: str = Body(...), meta: dict = Body(None), current_user: User = Depends(get_required_user)):
    """调用模型进行简单问答（需要登录）"""
    meta = meta or {}

    # 确保 request_id 存在
    if "request_id" not in meta or not meta.get("request_id"):
        meta["request_id"] = str(uuid.uuid4())

    model = select_model(model_spec=meta.get("model_spec") or meta.get("model") or conf.default_model)

    response = await model.call(query)
    logger.debug({"query": query, "response": response.content})

    return {"response": response.content, "request_id": meta["request_id"]}


@chat.get("/thread/{thread_id}/history")
async def get_thread_history(
    thread_id: str, current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    """获取对话历史消息（需要登录）- 包含用户反馈状态"""
    try:
        return await get_thread_history_view(
            thread_id=thread_id,
            current_uid=str(current_user.uid),
            db=db,
        )

    except Exception as e:
        logger.error(f"获取对话历史消息出错: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取对话历史消息出错: {str(e)}")


@chat.get("/thread/{thread_id}/state")
async def get_thread_state(
    thread_id: str,
    include_messages: bool = Query(False),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取对话当前状态（需要登录）"""
    try:
        return await get_agent_state_view(
            thread_id=thread_id,
            current_user=current_user,
            db=db,
            include_messages=include_messages,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取对话状态出错: {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取对话状态出错: {str(e)}")


# ==================== 线程管理 API ====================


class ThreadCreate(BaseModel):
    title: str | None = None
    agent_id: str
    metadata: dict | None = None


class ThreadResponse(BaseModel):
    id: str
    uid: str
    agent_id: str
    title: str | None = None
    is_pinned: bool = False
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThreadSearchSnippet(BaseModel):
    message_id: int | None = None
    content: str
    created_at: str | None = None


class ThreadSearchItem(ThreadResponse):
    thread_id: str
    matched_count: int
    message_id: int | None = None
    latest_match_at: str | None = None
    snippets: list[ThreadSearchSnippet] = Field(default_factory=list)


class ThreadSearchResponse(BaseModel):
    items: list[ThreadSearchItem]
    has_more: bool
    limit: int
    offset: int


class AttachmentResponse(BaseModel):
    file_id: str
    file_name: str
    file_type: str | None = None
    file_size: int
    status: str
    uploaded_at: str
    path: str
    artifact_url: str | None = None
    original_path: str | None = None
    original_artifact_url: str | None = None
    minio_url: str | None = None
    request_id: str | None = None


class AttachmentLimits(BaseModel):
    allowed_extensions: list[str]
    max_size_bytes: int


class AttachmentListResponse(BaseModel):
    attachments: list[AttachmentResponse]
    limits: AttachmentLimits


class TmpAttachmentResponse(BaseModel):
    tmp_file_id: str
    file_name: str
    file_type: str | None = None
    file_size: int
    bucket_name: str
    object_name: str
    minio_url: str
    uploaded_at: str
    parse_supported: bool = False
    parse_methods: list[str] = Field(default_factory=list)


class TmpAttachmentParseRequest(BaseModel):
    object_name: str
    file_name: str
    parse_method: str | None = None
    bucket_name: str | None = None


class TmpAttachmentParseResponse(BaseModel):
    tmp_file_id: str
    file_name: str
    bucket_name: str
    object_name: str
    parsed_object_name: str
    parsed_minio_url: str
    parse_method: str
    status: str
    truncated: bool = False


class TmpAttachmentConfirmItem(BaseModel):
    file_name: str
    file_type: str | None = None
    bucket_name: str
    object_name: str
    parsed_object_name: str | None = None
    truncated: bool = False


class TmpAttachmentConfirmRequest(BaseModel):
    attachments: list[TmpAttachmentConfirmItem]


class TmpAttachmentConfirmResponse(BaseModel):
    attachments: list[AttachmentResponse]


class ThreadFileEntry(BaseModel):
    path: str
    name: str
    is_dir: bool
    size: int
    modified_at: str | None = None
    artifact_url: str | None = None


class ThreadFileListResponse(BaseModel):
    path: str
    files: list[ThreadFileEntry]


class ThreadFileContentResponse(BaseModel):
    path: str
    content: list[str]
    offset: int
    limit: int
    total_lines: int
    artifact_url: str


class SaveThreadArtifactRequest(BaseModel):
    path: str


class SaveThreadArtifactResponse(BaseModel):
    name: str
    source_path: str
    saved_path: str
    saved_artifact_url: str


# =============================================================================
# > === 会话管理分组 ===
# =============================================================================


@chat.post("/thread", response_model=ThreadResponse)
async def create_thread(
    thread: ThreadCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_required_user)
):
    """创建新对话线程 (使用新存储系统)"""
    return await create_thread_view(
        agent_slug=thread.agent_id,
        title=thread.title,
        metadata=thread.metadata,
        db=db,
        current_uid=str(current_user.uid),
    )


@chat.get("/threads", response_model=list[ThreadResponse])
async def list_threads(
    agent_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """获取用户的所有对话线程 (使用新存储系统)"""
    return await list_threads_view(
        agent_slug=agent_id, db=db, current_uid=str(current_user.uid), limit=limit, offset=offset
    )


@chat.get("/threads/search", response_model=ThreadSearchResponse)
async def search_threads(
    q: str = Query(..., min_length=1, max_length=200),
    agent_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """搜索当前用户的历史对话。"""
    return await search_threads_view(
        query=q,
        agent_id=agent_id,
        db=db,
        current_uid=str(current_user.uid),
        limit=limit,
        offset=offset,
    )


@chat.delete("/thread/{thread_id}")
async def delete_thread(
    thread_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_required_user)
):
    """删除对话线程 (使用新存储系统)"""
    return await delete_thread_view(thread_id=thread_id, db=db, current_uid=str(current_user.uid))


class ThreadUpdate(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None


@chat.put("/thread/{thread_id}", response_model=ThreadResponse)
async def update_thread(
    thread_id: str,
    thread_update: ThreadUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """更新对话线程信息 (使用新存储系统)"""
    return await update_thread_view(
        thread_id=thread_id,
        title=thread_update.title,
        is_pinned=thread_update.is_pinned,
        db=db,
        current_uid=str(current_user.uid),
    )


# ================================
# > === 附件管理分组 ===
# ================================


@chat.post("/attachments/tmp", response_model=TmpAttachmentResponse)
async def upload_tmp_attachment(file: UploadFile = File(...), current_user: User = Depends(get_required_user)):
    """上传附件到 MinIO tmp，暂不关联线程。"""
    return await upload_tmp_attachment_view(file=file, current_uid=str(current_user.uid))


@chat.post("/attachments/tmp/parse", response_model=TmpAttachmentParseResponse)
async def parse_tmp_attachment(
    request: TmpAttachmentParseRequest,
    current_user: User = Depends(get_required_user),
):
    """解析 tmp 附件并返回解析后的 tmp URL。"""
    return await parse_tmp_attachment_view(
        object_name=request.object_name,
        file_name=request.file_name,
        parse_method=request.parse_method,
        bucket_name=request.bucket_name,
        current_uid=str(current_user.uid),
    )


@chat.post("/thread/{thread_id}/attachments/confirm", response_model=TmpAttachmentConfirmResponse)
async def confirm_tmp_thread_attachments(
    thread_id: str,
    request: TmpAttachmentConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """将 tmp 附件正式加入线程附件列表。"""
    return await confirm_tmp_thread_attachments_view(
        thread_id=thread_id,
        attachments=[item.model_dump() for item in request.attachments],
        db=db,
        current_uid=str(current_user.uid),
    )


@chat.post("/thread/{thread_id}/attachments", response_model=AttachmentResponse)
async def upload_thread_attachment(
    thread_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """上传原始附件并关联到指定对话线程。"""
    return await upload_thread_attachment_view(
        thread_id=thread_id,
        file=file,
        db=db,
        current_uid=str(current_user.uid),
    )


@chat.get("/thread/{thread_id}/attachments", response_model=AttachmentListResponse)
async def list_thread_attachments(
    thread_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """列出当前对话线程的所有附件元信息。"""
    return await list_thread_attachments_view(
        thread_id=thread_id,
        db=db,
        current_uid=str(current_user.uid),
    )


@chat.delete("/thread/{thread_id}/attachments/{file_id}")
async def delete_thread_attachment(
    thread_id: str,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """移除指定附件。"""
    return await delete_thread_attachment_view(
        thread_id=thread_id,
        file_id=file_id,
        db=db,
        current_uid=str(current_user.uid),
    )


@chat.get("/thread/{thread_id}/files", response_model=ThreadFileListResponse)
async def list_thread_files(
    thread_id: str,
    path: str = Query(f"{VIRTUAL_PATH_PREFIX}"),
    recursive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """列出线程文件目录。"""
    return await list_thread_files_view(
        thread_id=thread_id,
        current_uid=str(current_user.uid),
        db=db,
        path=path,
        recursive=recursive,
    )


@chat.get("/thread/{thread_id}/files/content", response_model=ThreadFileContentResponse)
async def read_thread_file_content(
    thread_id: str,
    path: str = Query(...),
    offset: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """读取线程文本文件（按行分页）。"""
    return await read_thread_file_content_view(
        thread_id=thread_id,
        current_uid=str(current_user.uid),
        db=db,
        path=path,
        offset=offset,
        limit=limit,
    )


@chat.get("/thread/{thread_id}/artifacts/{path:path}")
async def get_thread_artifact(
    thread_id: str,
    path: str,
    download: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """下载或预览线程文件。"""
    file_path = await resolve_thread_artifact_view(
        thread_id=thread_id,
        current_uid=str(current_user.uid),
        db=db,
        path=path,
    )

    async with aiofiles.open(file_path, "rb") as artifact_file:
        file_head = await artifact_file.read(512)
    media_type = detect_media_type(file_path.name, file_head)
    headers = {"Content-Disposition": f'attachment; filename="{file_path.name}"'} if download else None
    return FileResponse(path=file_path, media_type=media_type, headers=headers)


@chat.post("/thread/{thread_id}/artifacts/save", response_model=SaveThreadArtifactResponse)
async def save_thread_artifact_to_workspace(
    thread_id: str,
    request: SaveThreadArtifactRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """保存交付物到共享 workspace/saved_artifacts 目录。"""
    return await save_thread_artifact_to_workspace_view(
        thread_id=thread_id,
        current_uid=str(current_user.uid),
        db=db,
        path=request.path,
    )


# =============================================================================
# > === 消息反馈分组 ===
# =============================================================================


class MessageFeedbackRequest(BaseModel):
    rating: str  # 'like' or 'dislike'
    reason: str | None = None  # Optional reason for dislike


class MessageFeedbackResponse(BaseModel):
    id: int
    message_id: int
    rating: str
    reason: str | None
    created_at: str


@chat.post("/message/{message_id}/feedback", response_model=MessageFeedbackResponse)
async def submit_message_feedback(
    message_id: int,
    feedback_data: MessageFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """提交消息反馈（需要登录）"""
    result = await submit_message_feedback_view(
        message_id=message_id,
        rating=feedback_data.rating,
        reason=feedback_data.reason,
        db=db,
        current_uid=str(current_user.uid),
    )
    return MessageFeedbackResponse(**result)


@chat.get("/message/{message_id}/feedback")
async def get_message_feedback(
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """获取指定消息的用户反馈（需要登录）"""
    return await get_message_feedback_view(
        message_id=message_id,
        db=db,
        current_uid=str(current_user.uid),
    )


# =============================================================================
# > === 多模态图片支持分组 ===
# =============================================================================


@chat.post("/image/upload", response_model=ImageUploadResponse)
async def upload_image(file: UploadFile = File(...), current_user: User = Depends(get_required_user)):
    """
    上传并处理图片，返回base64编码的图片数据
    """
    try:
        # 验证文件类型
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="只支持图片文件上传")

        # 读取文件内容
        image_data = await file.read()

        # 检查文件大小（10MB限制，超过后会压缩到5MB）
        if len(image_data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="图片文件过大，请上传小于10MB的图片")

        # 处理图片
        result = process_uploaded_image(image_data, file.filename)

        if not result["success"]:
            raise HTTPException(status_code=400, detail=f"图片处理失败: {result['error']}")

        logger.info(
            f"用户 {current_user.id} 成功上传图片: {file.filename}, "
            f"尺寸: {result['width']}x{result['height']}, "
            f"格式: {result['format']}, "
            f"大小: {result['size_bytes']} bytes"
        )

        return ImageUploadResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"图片上传处理失败: {str(e)}, {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"图片处理失败: {str(e)}")
