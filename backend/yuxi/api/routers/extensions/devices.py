"""设备产品入口；身份与实例管理权限由后端依赖确定。"""

from fastapi import APIRouter, Depends, Response

from yuxi.api.dependencies.auth import get_required_user, require_product_user
from yuxi.modules.extensions.plugins.abridge.managed_browser import (
    PersonalBrowserStatus,
    personal_browser_status,
    provision_personal_browser,
)
from yuxi.modules.extensions.plugins.abridge.service import personal_devices
from yuxi.modules.identity.models import User

devices = APIRouter(prefix="/devices", tags=["devices"])


@devices.get("")
async def list_devices(user=Depends(get_required_user)):
    """返回当前用户获得平台授权的设备。"""
    return await personal_devices(user.uid)


@devices.get("/sandbox-browser", response_model=PersonalBrowserStatus)
async def get_sandbox_browser(user: User = Depends(require_product_user)) -> PersonalBrowserStatus:
    """读取本人沙盒浏览器的连接状态。"""
    return await personal_browser_status(str(user.uid))


@devices.post("/sandbox-browser", response_model=PersonalBrowserStatus)
async def connect_sandbox_browser(response: Response, user: User = Depends(require_product_user)) -> PersonalBrowserStatus:
    """首次连接可创建本人 Cube，之后复用原实例。"""
    result = await provision_personal_browser(str(user.uid))
    if result.status == "connecting":
        response.status_code = 202
    return result
