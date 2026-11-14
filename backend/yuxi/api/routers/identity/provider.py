"""可选 OIDC Provider 的 HTTP 协议适配。"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from yuxi.api.dependencies.auth import get_db, get_required_user
from yuxi.infrastructure.runtime_settings import get_public_url
from yuxi.modules.identity.provider import AuthorizationRequest, ProviderError
from yuxi.modules.identity.services.provider import ProviderService

router = APIRouter(prefix="/auth/provider", tags=["identity-provider"])
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


@router.get("/.well-known/openid-configuration")
async def discovery():
    """公开实际启用的协议能力。"""
    try:
        return ProviderService().metadata()
    except ProviderError as exc:
        return error_response(exc)


@router.get("/jwks")
async def jwks():
    """发行者公钥用于独立验证，不公开任何私钥。"""
    try:
        return ProviderService().jwks()
    except ProviderError as exc:
        return error_response(exc)


@router.get("/authorize")
async def start_authorization(request: Request):
    """确认回调可信后进入携带 Yuxi 登录凭据的站内页面。"""
    try:
        if len(request.query_params.multi_items()) != len(request.query_params):
            raise ProviderError("invalid_request", "授权参数不能重复")
        fields = AuthorizationRequest.model_validate(dict(request.query_params))
        service = ProviderService()
        browser = await service.start_browser(fields)
        response = RedirectResponse(
            get_public_url() + "/auth/provider?" + urlencode(fields.model_dump()), status_code=302, headers=NO_STORE
        )
        response.set_cookie(
            "yuxi_provider_browser",
            browser,
            max_age=300,
            httponly=True,
            secure=get_public_url().startswith("https://"),
            samesite="lax",
            path="/api/auth/provider",
        )
        return response
    except ProviderError as exc:
        return error_response(exc)
    except ValueError:
        return error_response(ProviderError("invalid_request", "授权请求无效"))


@router.post("/authorize")
async def approve_authorization(fields: AuthorizationRequest, request: Request, user=Depends(get_required_user), db=Depends(get_db)):
    """只有真实浏览器登录可授权，API Key 不得转换为用户登录。"""
    if getattr(request.state, "api_key", None):
        return error_response(ProviderError("access_denied", "需要用户登录", 403))
    try:
        service = ProviderService(db)
        redirect_url = await service.authorize(fields, user.uid, request.cookies.get("yuxi_provider_browser", ""))
        return JSONResponse({"redirect_url": redirect_url}, headers=NO_STORE)
    except ProviderError as exc:
        return error_response(exc)


@router.post("/token")
async def exchange_token(request: Request, db=Depends(get_db)):
    """按 OAuth 标准以表单和客户端认证兑换授权码。"""
    try:
        fields = await request.form()
        if len(fields.multi_items()) != len(fields) or any(not isinstance(value, str) for value in fields.values()):
            raise ProviderError("invalid_request", "令牌参数无效或重复")
        form = dict(fields)
        result = await ProviderService(db).exchange(form, request.headers.get("authorization", ""))
        return JSONResponse(result, headers=NO_STORE)
    except ProviderError as exc:
        return error_response(exc)


@router.get("/userinfo")
async def userinfo(request: Request, db=Depends(get_db)):
    """返回访问令牌主体的当前身份声明。"""
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return error_response(ProviderError("invalid_token", "需要 Bearer 访问令牌", 401))
    try:
        return JSONResponse(await ProviderService(db).userinfo(header[7:]), headers=NO_STORE)
    except ProviderError as exc:
        return error_response(exc)


def error_response(exc: ProviderError):
    """OAuth 错误保持标准顶层字段，拒绝缓存凭据响应。"""
    headers = dict(NO_STORE)
    if exc.status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="oidc-provider"' if exc.error == "invalid_client" else "Bearer"
    return JSONResponse({"error": exc.error, "error_description": exc.description}, status_code=exc.status, headers=headers)
