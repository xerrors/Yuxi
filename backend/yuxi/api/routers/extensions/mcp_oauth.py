"""MCP 个人授权 HTTP 与浏览器适配。"""

import re
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.modules.extensions.mcp.oauth_config import redirect_uri
from yuxi.modules.extensions.mcp.oauth_service import MCPOAuthService
from yuxi.shared.errors import DomainError

router = APIRouter()
COOKIE = "yuxi_mcp_browser"
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class CompleteMCPAuthorization(BaseModel):
    """一次性授权结果，不能包含目标用户。"""

    model_config = ConfigDict(extra="forbid")

    result: str = Field(min_length=20, max_length=100)


class SaveMCPCredentials(BaseModel):
    """用户只提交秘密与打开表单时的配置修订。"""

    model_config = ConfigDict(extra="forbid")
    secret: StrictStr = Field(min_length=1, max_length=32768, pattern=r"^[!-~]+$", repr=False)
    config_revision: str = Field(min_length=64, max_length=64)


@router.put("/{slug}/connection")
async def save_credentials(slug: str, request: Request, response: Response, user=Depends(get_required_user), db=Depends(get_db)):
    """保存本人凭证，验证错误不回显原始 secret。"""
    try:
        body = SaveMCPCredentials.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise HTTPException(status_code=422, detail="凭证格式无效，请填写原始 Token 或 API Key 并重新打开过期表单") from None
    status = await MCPOAuthService(db).save_credentials(slug, user.id, body.secret, body.config_revision)
    response.headers.update(NO_STORE)
    return {"success": True, "connection_status": status}


@router.get("/oauth/callback")
async def callback(request: Request, state: str = "", code: str = "", error: str = "", db=Depends(get_db)):
    """浏览器回调固定返回 MCP 页，授权码不交给前端。"""
    try:
        result = await MCPOAuthService(db).callback(code, state, request.cookies.get(COOKIE, ""), denied=bool(error))
        query = {"tab": "mcp", "mcp_oauth_result": result}
    except DomainError:
        await db.rollback()
        query = {"tab": "mcp", "mcp_oauth_error": "authorization_failed"}
    return RedirectResponse("/extensions?" + urlencode(query), status_code=302, headers=NO_STORE)


@router.post("/oauth/complete")
async def complete(
    body: CompleteMCPAuthorization, request: Request, response: Response, user=Depends(get_required_user), db=Depends(get_db)
):
    """当前登录用户确认归属，返回脱敏后的服务标识。"""
    response.headers.update(NO_STORE)
    slug = await MCPOAuthService(db).complete(body.result, request.cookies.get(COOKIE, ""), user.id)
    return {"success": True, "slug": slug}


@router.post("/{slug}/connection")
async def connect(slug: str, request: Request, response: Response, user=Depends(get_required_user), db=Depends(get_db)):
    """只有认证用户能发起个人授权，cookie 仅用于浏览器关联。"""
    browser = request.cookies.get(COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", browser):
        browser = secrets.token_urlsafe(32)
    url = await MCPOAuthService(db).start(slug, user.id, browser)
    response.headers.update(NO_STORE)
    response.set_cookie(
        COOKIE,
        browser,
        max_age=600,
        httponly=True,
        secure=redirect_uri().startswith("https://"),
        samesite="lax",
        path="/api/system/mcp-servers",
    )
    return {"authorization_url": url}


@router.delete("/{slug}/connection")
async def disconnect(slug: str, response: Response, user=Depends(get_required_user), db=Depends(get_db)):
    """撤销只作用于当前用户的 MCP 连接。"""
    await MCPOAuthService(db).disconnect(slug, user.id)
    response.headers.update(NO_STORE)
    return {"success": True}
