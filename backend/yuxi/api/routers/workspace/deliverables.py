"""个人成果的 Web 列表、预览与下载路由。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.api.responses.files import render_file_result
from yuxi.modules.identity.models import User
from yuxi.modules.workspace.services.deliverables import list_deliverables, read_deliverable

deliverables = APIRouter(prefix="/workspace/deliverables", tags=["workspace"])


async def require_web_deliverables_user(request: Request, user: User = Depends(get_required_user)) -> User:
    """成果目录只属于产品 Web 登录面，不接受带 APP scope 的 API Key。"""
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(status_code=403, detail="我的成果仅支持 Web 登录用户")
    return user


@deliverables.get("")
async def get_deliverables(
    query: str = Query("", max_length=255),
    file_type: Literal["all", "document", "image", "video", "audio", "other"] = "all",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_web_deliverables_user),
    db: AsyncSession = Depends(get_db),
):
    """分页读取当前用户已自动保存的成果。"""
    return await list_deliverables(uid=str(current_user.uid), query=query, file_type=file_type, page=page, page_size=page_size, db=db)


@deliverables.get("/{deliverable_id}/preview")
async def preview_deliverable(
    deliverable_id: str,
    current_user: User = Depends(require_web_deliverables_user),
    db: AsyncSession = Depends(get_db),
):
    """读取成果副本并按现有文件预览协议返回。"""
    return render_file_result(await read_deliverable(deliverable_id=deliverable_id, uid=str(current_user.uid), db=db, preview=True))


@deliverables.get("/{deliverable_id}/download")
async def download_deliverable(
    deliverable_id: str,
    current_user: User = Depends(require_web_deliverables_user),
    db: AsyncSession = Depends(get_db),
):
    """以附件返回当前用户可见成果的完整副本。"""
    return render_file_result(await read_deliverable(deliverable_id=deliverable_id, uid=str(current_user.uid), db=db, download=True))


@deliverables.get("/{deliverable_id}/content")
async def deliverable_content(
    deliverable_id: str,
    current_user: User = Depends(require_web_deliverables_user),
    db: AsyncSession = Depends(get_db),
):
    """返回成果原始字节供图片、音视频播放使用。"""
    return render_file_result(await read_deliverable(deliverable_id=deliverable_id, uid=str(current_user.uid), db=db))
