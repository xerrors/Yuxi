"""设备产品入口；身份与实例管理权限由后端依赖确定。"""

from fastapi import APIRouter, Depends

from yuxi.api.dependencies.auth import get_required_user
from yuxi.modules.extensions.abridge.service import personal_devices

devices = APIRouter(prefix="/devices", tags=["devices"])


@devices.get("")
async def list_devices(user=Depends(get_required_user)):
    """返回当前用户获得平台授权的设备。"""
    return await personal_devices(user.uid)
