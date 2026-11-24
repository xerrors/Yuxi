"""产品内部的附件预解析与图片处理入口。"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, require_product_user
from yuxi.api.uploads import read_upload_with_limit
from yuxi.infrastructure.images import process_uploaded_image
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.attachments import get_draft_file, parse_draft_file
from yuxi.modules.identity.models import User

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(require_product_user)])


class DraftParse(BaseModel):
    """私有解析只选择方式，来源由服务端文件引用决定。"""

    model_config = ConfigDict(extra="forbid")
    parse_method: str | None = None


@router.get("/files/{file_id}")
async def retrieve_draft(file_id: str, user: User = Depends(require_product_user), db: AsyncSession = Depends(get_db)):
    """产品读取 draft 的文件信息和可用解析方式。"""
    return await get_draft_file(file_id=file_id, scope=ActorScope(uid=str(user.uid), app_id=None), db=db, private=True)


@router.post("/files/{file_id}/parse")
async def parse_file(file_id: str, payload: DraftParse, user: User = Depends(require_product_user), db: AsyncSession = Depends(get_db)):
    """预解析仍留在 draft，发送时由文件 ID 带上派生内容。"""
    return await parse_draft_file(
        file_id=file_id,
        parse_method=payload.parse_method,
        scope=ActorScope(uid=str(user.uid), app_id=None),
        db=db,
    )


@router.post("/images")
async def prepare_image(file: UploadFile = File(...)):
    """返回完整 data URL，图片处理不绑定 Session 或 Workdir。"""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只支持图片文件上传")
    try:
        content = await read_upload_with_limit(
            file,
            max_size_bytes=10 * 1024 * 1024,
            too_large_message="图片文件过大，请上传小于10MB的图片",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = process_uploaded_image(content, file.filename)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=f"图片处理失败: {result['error']}")
    return {
        "image_url": f"data:{result['mime_type']};base64,{result['image_content']}",
        "thumbnail_url": f"data:image/jpeg;base64,{result['thumbnail_content']}",
        **{key: result[key] for key in ("width", "height", "mime_type", "size_bytes", "format")},
    }
