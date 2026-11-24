"""认证与授权相关的 FastAPI 依赖。"""

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.identity.models import APIKey, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.identity.services.public_auth import verify_api_key
from yuxi.modules.knowledge.mcp import KNOWLEDGE_MCP_PATH
from yuxi.shared.datetime import utc_now

KNOWLEDGE_TOOL_PATHS = frozenset(
    f"/api/v1/knowledge/tools/{name}"
    for name in (
        "list_kbs",
        "query_kb",
        "open_kb_document",
        "find_kb_document",
        "search_file",
    )
)


async def get_db():
    """提供请求级数据库会话；提交与回滚由会话上下文管理。"""
    async with pg_manager.get_async_session_context() as db:
        yield db


async def get_current_user(
    request: Request,
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """解析 Bearer 凭据：API Key 校验访问面，JWT 校验会话状态；无凭据返回 None。"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if authorization is None or not authorization.startswith("Bearer "):
        return None
    token = authorization.split("Bearer ")[1]
    if not token:
        return None

    # 根据 token 前缀判断认证方式
    if token.startswith("yxkey_"):
        # API Key 认证
        user, api_key_obj = await verify_api_key(token, db)
        if user is not None and api_key_obj is not None:
            request.state.api_key = api_key_obj
            request.state.app_id = api_key_obj.app_id
            if not _api_key_route_permitted(api_key_obj, request.scope.get("path", "")):
                raise HTTPException(status_code=403, detail="该 API Key 无权访问此 API 面")
            api_key_obj.last_used_at = utc_now()
            await db.commit()
        return user

    # JWT Token 认证
    try:
        payload = AuthUtils.verify_access_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    result = await db.execute(select(User).filter(User.id == int(user_id), User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    if user.user_kind == "end_user":
        raise HTTPException(status_code=403, detail="终端用户只能通过 Public API 使用")
    if user.is_login_locked():
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="登录被锁定，请稍后重试",
            headers={"X-Lock-Remaining": str(user.get_remaining_lock_time())},
        )

    if payload.get("oidc_sid"):
        from yuxi.modules.identity.oidc import get_oidc_config
        from yuxi.modules.identity.repositories.oidc import OIDCRepository
        from yuxi.shared.errors import PermissionDeniedError

        try:
            oidc_config = get_oidc_config()
            if not oidc_config.enabled:
                raise PermissionDeniedError("OIDC 未启用")
            await OIDCRepository(db).active_session(payload["oidc_sid"], user.id, oidc_config.issuer_url)
        except PermissionDeniedError as exc:
            raise credentials_exception from exc
    else:
        from yuxi.modules.identity.models import OIDCIdentity
        from yuxi.modules.identity.repositories.login_sessions import LoginSessionRepository

        if await LoginSessionRepository(db).is_revoked(token):
            raise credentials_exception
        # 旧标准账号及同名绑定账号的 JWT 无法证明登录来源，升级后必须重新认证。
        if payload.get("auth_method") != "password":
            bound = await db.scalar(select(OIDCIdentity.id).where(OIDCIdentity.user_id == user.id).limit(1))
            if bound or (user.uid and user.uid.startswith("oidc:")):
                raise credentials_exception
    request.state.jwt_token = token
    request.state.jwt_payload = payload
    return user


async def get_required_user(user: User | None = Depends(get_current_user)):
    """要求已登录用户；未登录 401，未绑定部门 400。"""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请登录后再访问",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.department_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前用户未绑定部门",
        )
    return user


async def require_product_user(request: Request, user: User = Depends(get_required_user)) -> User:
    """只接受产品登录凭据。"""
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(status_code=403, detail="此操作仅向产品登录用户开放")
    return user


async def get_admin_user(current_user: User = Depends(get_required_user)):
    """要求管理员及以上角色。"""
    if current_user.role not in {"admin", "superadmin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user


async def get_superadmin_user(current_user: User = Depends(get_required_user)):
    """要求超级管理员角色。"""
    if current_user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要超级管理员权限",
        )
    return current_user


def _api_key_route_permitted(api_key: APIKey, route_path: str) -> bool:
    """判断 API Key 的访问级别是否覆盖请求路径。"""
    if api_key.access_level == "full":
        return True
    permitted_root = "/api/v1/agents" if api_key.access_level == "agents" else None
    if permitted_root and (route_path == permitted_root or route_path.startswith(f"{permitted_root}/")):
        return True
    if api_key.access_level == "knowledge" and route_path in KNOWLEDGE_TOOL_PATHS | {KNOWLEDGE_MCP_PATH}:
        return True
    return False
