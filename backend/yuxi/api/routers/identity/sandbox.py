"""用户环境 Web 设置入口，不能由请求提供另一个 uid。"""

from fastapi import APIRouter, Depends, Query

from yuxi.api.dependencies.auth import get_required_user
from yuxi.modules.agents.services.sandbox import get_user_sandbox, get_user_sandbox_disk_usage, rebuild_user_sandbox
from yuxi.modules.identity.models import User

router = APIRouter(prefix="/user/sandbox", tags=["user"])


@router.get("")
async def get_sandbox(current_user: User = Depends(get_required_user)):
    """只返回当前认证用户的环境与占用。"""
    return await get_user_sandbox(str(current_user.uid))


@router.post("/rebuild")
async def rebuild_sandbox(current_user: User = Depends(get_required_user), failed_generation: int | None = Query(default=None, ge=1)):
    """忙碌判断由持久 executor 在用户锁内执行。"""
    return await rebuild_user_sandbox(str(current_user.uid), failed_generation=failed_generation)


@router.post("/disk-usage")
async def get_disk_usage(current_user: User = Depends(get_required_user)):
    """只查询当前用户的环境，显式执行允许恢复休眠实例。"""
    return await get_user_sandbox_disk_usage(str(current_user.uid))
