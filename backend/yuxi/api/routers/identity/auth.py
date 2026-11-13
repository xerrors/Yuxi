"""认证、用户管理与 CLI 浏览器登录授权路由。"""

import re
from typing import Literal

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_admin_user, get_db, get_required_user

# OIDC 认证相关导入
from yuxi.api.routers.identity.oidc import (
    get_oidc_config_handler,
    oidc_callback_handler,
    oidc_exchange_code_handler,
    oidc_login_url_handler,
    oidc_logout_handler,
)
from yuxi.api.uploads import read_upload_with_limit
from yuxi.modules.identity.models import User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.identity.services import users as user_service
from yuxi.modules.identity.services.administration import (
    IdentityConflictError,
    SystemAlreadyInitializedError,
    initialize_system_admin,
    list_managed_users_page,
)
from yuxi.modules.identity.services.cli_auth import (
    CLI_AUTH_POLL_INTERVAL_SECONDS,
    CLI_AUTH_SESSION_TTL_SECONDS,
    CLIAuthError,
    approve_cli_auth_session,
    create_cli_auth_session,
    exchange_cli_auth_token,
    get_cli_auth_session_for_user,
)
from yuxi.modules.identity.services.login import login_result
from yuxi.modules.identity.services.login_limits import extract_client_ip
from yuxi.modules.identity.services.password_login import PasswordLoginRejected, login_with_password
from yuxi.modules.identity.services.usernames import is_valid_phone_number, validate_username

auth = APIRouter(prefix="/auth", tags=["authentication"])


class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: int
    username: str
    uid: str  # 用于登录的user_id
    phone_number: str | None = None
    avatar: str | None = None
    role: str
    department_id: int | None = None
    department_name: str | None = None


class UserCreate(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: Literal["user", "admin", "superadmin"] = "user"
    phone_number: str | None = None
    department_id: int | None = None


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "admin", "superadmin"] | None = None
    username: str | None = None
    password: str | None = Field(default=None, min_length=8)
    phone_number: str | None = None
    avatar: str | None = None
    department_id: int | None = None


class UserProfileUpdate(BaseModel):
    username: str | None = None
    phone_number: str | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    uid: str
    phone_number: str | None = None
    avatar: str | None = None
    role: str
    department_id: int | None = None
    department_name: str | None = None  # 部门名称
    created_at: str
    last_login: str | None = None


class ManagedUserResponse(UserResponse):
    """用户管理列表中的身份类型与 APP 来源。"""

    user_kind: Literal["human", "end_user"]
    app_id: str | None = None


class UserPageResponse(BaseModel):
    items: list[ManagedUserResponse]
    total: int
    limit: int
    offset: int


class UserAccessOption(BaseModel):
    uid: str
    username: str
    role: str
    department_id: int | None = None
    department_name: str | None = None


class InitializeAdmin(BaseModel):
    uid: str  # 直接输入用户ID
    password: str = Field(min_length=8)
    phone_number: str | None = None


class UsernameValidation(BaseModel):
    username: str


class UidGeneration(BaseModel):
    username: str
    uid: str
    is_available: bool


class OIDCConfigResponse(BaseModel):
    """OIDC 配置响应"""

    enabled: bool
    login_url: str | None = None
    provider_name: str | None = "OIDC登录"


class CLIAuthSessionCreate(BaseModel):
    key_name: str | None = Field(default=None, max_length=100)


class CLIAuthTokenRequest(BaseModel):
    device_code: str


class CLIAuthSessionCreateResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class CLIAuthSessionResponse(BaseModel):
    user_code: str
    status: str
    key_name: str
    created_at: str
    expires_at: str
    approved_at: str | None = None


class CLIAuthApproveResponse(BaseModel):
    user_code: str
    status: str
    approved_at: str | None = None


class CLIAuthTokenResponse(BaseModel):
    api_key: dict
    secret: str
    user: dict


# --- 密码认证 ---


@auth.post("/token", response_model=Token)
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """适配密码登录表单、拒绝原因与 HTTP 响应。"""
    try:
        result = await login_with_password(db, form_data.username, form_data.password, extract_client_ip(request))
    except PasswordLoginRejected as error:
        headers = {"WWW-Authenticate": "Bearer"}
        if error.reason == "rate_limited":
            code, detail = 429, "登录尝试过于频繁，请稍后再试"
            headers = {"Retry-After": str(error.remaining_seconds)}
        elif error.reason == "deleted":
            code, detail = 403, "该账户已注销"
        elif error.reason in {"locked", "newly_locked"}:
            code = 423
            remaining = error.remaining_seconds
            detail = (
                f"登录被锁定，请等待 {remaining} 秒后再试" if error.reason == "locked" else f"由于多次登录失败，账户已被锁定 {remaining} 秒"
            )
            headers["X-Lock-Remaining"] = str(remaining)
        else:
            code = 401
            detail = "登录标识或密码错误" if error.reason == "unknown_account" else "用户名或密码错误"
        raise HTTPException(status_code=code, detail=detail, headers=headers) from error

    return result


# --- CLI 浏览器登录授权 ---


@auth.post("/cli/sessions", response_model=CLIAuthSessionCreateResponse)
async def create_cli_session(data: CLIAuthSessionCreate, db: AsyncSession = Depends(get_db)):
    """创建 CLI 设备授权会话，返回轮询参数。"""
    session, device_code = await create_cli_auth_session(db, key_name=data.key_name)
    return CLIAuthSessionCreateResponse(
        device_code=device_code,
        user_code=session.user_code,
        verification_uri="/auth/cli/authorize",
        expires_in=CLI_AUTH_SESSION_TTL_SECONDS,
        interval=CLI_AUTH_POLL_INTERVAL_SECONDS,
    )


@auth.get("/cli/sessions/{user_code}", response_model=CLIAuthSessionResponse)
async def get_cli_session(
    user_code: str,
    _current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """读取当前用户创建的 CLI 授权会话状态。"""
    try:
        session = await get_cli_auth_session_for_user(db, user_code)
    except CLIAuthError as exc:
        _raise_cli_auth_error(exc)
    return CLIAuthSessionResponse(**session.to_dict())


@auth.post("/cli/sessions/{user_code}/approve", response_model=CLIAuthApproveResponse)
async def approve_cli_session(
    user_code: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """当前用户批准指定 CLI 设备的授权请求。"""
    try:
        session = await approve_cli_auth_session(db, user_code, current_user)
    except CLIAuthError as exc:
        _raise_cli_auth_error(exc)
    return CLIAuthApproveResponse(**session.to_dict())


@auth.post("/cli/sessions/token", response_model=CLIAuthTokenResponse)
async def exchange_cli_session_token(data: CLIAuthTokenRequest, db: AsyncSession = Depends(get_db)):
    """设备凭 device_code 兑换 API Key。"""
    try:
        return await exchange_cli_auth_token(db, data.device_code)
    except CLIAuthError as exc:
        _raise_cli_auth_error(exc)


@auth.get("/check-first-run")
async def check_first_run():
    """报告系统是否尚未初始化管理员。"""
    return {"first_run": await user_service.is_first_run()}


@auth.post("/initialize", response_model=Token)
async def initialize_admin(admin_data: InitializeAdmin, db: AsyncSession = Depends(get_db)):
    """初始化系统管理员并直接返回登录令牌。"""
    # 验证用户ID格式（只支持字母数字和下划线）
    if not re.fullmatch(r"[a-zA-Z0-9_]+", admin_data.uid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID只能包含字母、数字和下划线",
        )

    if len(admin_data.uid) < 3 or len(admin_data.uid) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID长度必须在3-20个字符之间",
        )

    # 验证手机号格式（如果提供了）
    if admin_data.phone_number and not is_valid_phone_number(admin_data.phone_number):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号格式不正确")

    try:
        created = await initialize_system_admin(
            db,
            uid=admin_data.uid,
            password=admin_data.password,
            phone_number=admin_data.phone_number,
        )
    except SystemAlreadyInitializedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except IdentityConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    new_admin = created.admin

    # 生成访问令牌
    token_data = {"sub": str(new_admin.id)}
    access_token = AuthUtils.create_access_token(token_data)

    return login_result(new_admin, access_token, created.department.name)


# --- 用户信息 ---


@auth.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """获取当前登录用户的个人信息"""
    user_dict = current_user.to_dict()
    if current_user.department_id:
        user_dict["department_name"] = await user_service.get_department_name(db, current_user=current_user)

    return user_dict


@auth.put("/profile", response_model=UserResponse)
async def update_profile(
    profile_data: UserProfileUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """适配用户写入请求与领域结果。"""
    return await user_service.update_profile(db, current_user=current_user, **profile_data.model_dump())


# --- 用户管理 ---


@auth.post("/users", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """适配用户写入请求与领域结果。"""
    return await user_service.create_user(db, current_user=current_user, **user_data.model_dump())


ALLOWED_USER_ROLES = ("superadmin", "admin", "user")


def parse_csv_ints(raw: str | None) -> list[int] | None:
    """把逗号分隔的整数参数解析为去重列表；空值或全部非法时视为未筛选。"""
    if not raw:
        return None
    values = set()
    for part in raw.split(","):
        try:
            values.add(int(part.strip()))
        except ValueError:
            continue
    return sorted(values) or None


def parse_csv_roles(raw: str | None) -> list[str] | None:
    """把逗号分隔的角色参数收敛到合法取值；空值或全部非法时视为未筛选。"""
    if not raw:
        return None
    values = [part.strip() for part in raw.split(",") if part.strip() in ALLOWED_USER_ROLES]
    return list(dict.fromkeys(values)) or None


@auth.get("/users/page", response_model=UserPageResponse)
async def read_users_page(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(None, max_length=100),
    department_id: int | None = Query(None, ge=1),
    department_ids: str | None = Query(None, max_length=200),
    role: Literal["superadmin", "admin", "user"] | None = None,
    roles: str | None = Query(None, max_length=80),
    user_kind: Literal["human", "end_user", "all"] = "human",
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """分页查询当前管理员可管理的有效用户；多选参数接受逗号分隔取值。"""
    return await list_managed_users_page(
        db,
        offset=offset,
        limit=limit,
        is_superadmin=current_user.role == "superadmin",
        visible_department_id=current_user.department_id,
        department_id=department_id,
        department_ids=parse_csv_ints(department_ids),
        role=role,
        roles=parse_csv_roles(roles),
        search=search.strip() if search else None,
        user_kind=None if user_kind == "all" else user_kind,
    )


@auth.get("/users", response_model=list[UserResponse])
async def read_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取所有用户（管理员权限）。"""
    return await user_service.list_users(db, current_user=current_user, skip=skip, limit=limit)


@auth.get("/users/access-options", response_model=list[UserAccessOption])
async def read_user_access_options(
    skip: int = 0,
    limit: int = 1000,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取用户访问选项（管理员权限）。"""
    return await user_service.list_access_options(db, skip=skip, limit=limit)


@auth.get("/users/{user_id}", response_model=UserResponse)
async def read_user(
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取特定用户信息（管理员权限）。"""
    user = await user_service.get_managed_user(db, current_user=current_user, user_id=user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )
    return user.to_dict()


@auth.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """适配用户写入请求与领域结果。"""
    return await user_service.update_user(db, current_user=current_user, user_id=user_id, **user_data.model_dump())


@auth.delete("/users/{user_id}", response_model=dict)
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """适配用户写入请求与领域结果。"""
    return await user_service.delete_user(db, current_user=current_user, user_id=user_id)


@auth.post("/validate-username", response_model=UidGeneration)
async def validate_username_and_generate_uid(
    validation_data: UsernameValidation,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """验证用户名格式并生成可用的user_id"""
    # 验证用户名格式
    is_valid, error_msg = validate_username(validation_data.username)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
        )

    # 检查用户名是否已存在并生成唯一 uid
    uid = await user_service.suggest_username_uid(db, username=validation_data.username)
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在",
        )

    return UidGeneration(username=validation_data.username, uid=uid, is_available=True)


@auth.get("/check-uid/{uid}")
async def check_uid_availability(
    uid: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """检查 uid 是否可用"""
    return {"uid": uid, "is_available": await user_service.is_uid_available(db, uid=uid)}


@auth.post("/upload-avatar")
async def upload_user_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """上传用户头像"""
    try:
        content = await read_upload_with_limit(
            file,
            max_size_bytes=5 * 1024 * 1024,
            too_large_message="文件大小不能超过5MB",
        )
        avatar_url = await user_service.upload_avatar(db, current_user=current_user, content=content)

        return {"success": True, "avatar_url": avatar_url, "message": "头像上传成功"}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"头像上传失败: {str(e)}") from e


# --- OIDC 认证 ---


@auth.get("/oidc/config", response_model=OIDCConfigResponse)
async def get_oidc_config():
    """获取 OIDC 配置（供前端使用）"""
    return await get_oidc_config_handler()


@auth.get("/oidc/login-url")
async def get_oidc_login_url(request: Request, response: Response):
    """获取 OIDC 登录 URL"""
    return await oidc_login_url_handler(request, response)


@auth.get("/oidc/callback", response_class=RedirectResponse)
async def oidc_callback(
    request: Request,
    code: str = Query(default="", max_length=2048),
    state: str = Query(default="", max_length=128),
    error: str = Query(default="", max_length=128),
    db: AsyncSession = Depends(get_db),
):
    """处理 OIDC 回调 - 重定向到前端 Vue 路由"""
    return await oidc_callback_handler(code, state, request, db, error)


@auth.post("/oidc/exchange-code", response_model=Token)
async def oidc_exchange_code(
    request: Request,
    response: Response,
    code: str = Body(..., embed=True, min_length=1, max_length=128),
    db: AsyncSession = Depends(get_db),
):
    """使用一次性 code 交换 OIDC 登录数据"""
    return await oidc_exchange_code_handler(code, request, response, db)


@auth.post("/oidc/logout")
async def oidc_logout(request: Request, response: Response, user=Depends(get_required_user), db=Depends(get_db)):
    """当前用户只撤销 Yuxi 本地凭据，保留认证中心共享会话。"""
    response.headers["Cache-Control"] = "no-store"
    return await oidc_logout_handler(request, db)


@auth.post("/oidc/backchannel-logout")
async def oidc_backchannel_logout(logout_token: str = Form(..., max_length=16384), db=Depends(get_db)):
    """接收认证中心签名的标准登出通知。"""
    from yuxi.modules.identity.services.oidc import OIDCService
    from yuxi.shared.errors import DomainError

    try:
        await OIDCService(db).backchannel_logout(logout_token)
    except DomainError as exc:
        raise HTTPException(400, "OIDC 登出通知无效") from exc
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


def _raise_cli_auth_error(exc: CLIAuthError) -> None:
    """把 CLI 授权失败映射为携带错误码的 HTTP 响应。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.code, "message": exc.message},
    ) from exc
