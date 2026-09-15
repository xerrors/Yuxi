"""个人文件与正式会话附件的统一回收站HTTP边界。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.repositories.personal_trash_repository import PersonalTrashRepository
from yuxi.services.personal_trash_service import process_personal_entry, restore_personal_entry
from yuxi.storage.postgres.models_business import User

personal_trash = APIRouter(prefix="/personal-trash", tags=["workspace"])


@personal_trash.get("")
async def list_personal_trash(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """只列出当前用户的回收站，不随管理角色扩大范围。"""
    return await PersonalTrashRepository(db).list(str(current_user.uid), page, page_size)


@personal_trash.post("/{entry_id}/restore")
async def restore_personal_trash(
    entry_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """恢复本人文件，已有目标始终保持原样。"""
    return await restore_personal_entry(db=db, uid=str(current_user.uid), entry_id=entry_id)


@personal_trash.post("/{entry_id}/retry")
async def retry_personal_trash(
    entry_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """重新执行已持久化的未完成操作，不允许提前清理。"""
    return await process_personal_entry(db=db, uid=str(current_user.uid), entry_id=entry_id)
