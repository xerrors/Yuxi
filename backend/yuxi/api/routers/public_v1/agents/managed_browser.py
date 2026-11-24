"""APP 终端用户的托管浏览器入口。"""

from fastapi import APIRouter, Depends, Response

from yuxi.api.routers.public_v1.agents.auth import PublicAgentContext, require_public_context
from yuxi.api.routers.public_v1.agents.responses import PUBLIC_ERRORS
from yuxi.api.routers.public_v1.agents.schemas import PublicError
from yuxi.modules.extensions.plugins.abridge.managed_browser import ManagedBrowserStatus, provision_managed_browser

router = APIRouter(responses=PUBLIC_ERRORS)


@router.post(
    "/managed-browser",
    responses={
        202: {"model": ManagedBrowserStatus, "description": "浏览器正在连接"},
        502: {"model": PublicError, "description": "托管浏览器服务或 Cube 返回异常"},
        504: {"model": PublicError, "description": "等待 aBridge 响应超时"},
    },
)
async def post_managed_browser(response: Response, context: PublicAgentContext = Depends(require_public_context)) -> ManagedBrowserStatus:
    """登记、启动或查询当前有效用户的托管浏览器。"""
    result = await provision_managed_browser(context.scope)
    if result.status == "connecting":
        response.status_code = 202
    return result
