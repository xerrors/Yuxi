import re

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import (
    get_authorization_context,
    get_db,
    get_required_user,
    require_permission,
)
from yuxi.permissions.authorization import AuthorizationContext, build_authorization_context
from yuxi.repositories.department_repository import DepartmentRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.auth_service import (
    CLI_AUTH_POLL_INTERVAL_SECONDS,
    CLI_AUTH_SESSION_TTL_SECONDS,
    CLIAuthError,
    approve_cli_auth_session,
    create_cli_auth_session,
    exchange_cli_auth_token,
    get_cli_auth_session_for_user,
)
from yuxi.services.identity_admin_service import (
    IdentityConflictError,
    SystemAlreadyInitializedError,
    initialize_system_admin,
)
from yuxi.services.login_rate_limit_service import (
    check_login_rate_limit,
    clear_login_failures,
    extract_client_ip,
    record_login_failure,
)
from yuxi.services.oa_sso_service import (
    MAX_OA_ACCOUNT_LENGTH,
    MAX_OA_TOKEN_LENGTH,
    exchange_oa_account_handler,
    exchange_oa_token_handler,
)

# OIDC 认证相关导入
from yuxi.services.oidc_service import (
    get_oidc_config_handler,
    oidc_callback_handler,
    oidc_exchange_code_handler,
    oidc_login_url_handler,
)
from yuxi.services.operation_log_service import log_operation
from yuxi.services.user_identity_service import generate_unique_uid, is_valid_phone_number, validate_username
from yuxi.services.user_management_service import (
    department_is_accessible,
    get_authorized_user,
    list_authorized_users,
)
from yuxi.services.user_role_service import (
    UserRoleAuthorizationError,
    UserRoleConflictError,
    has_active_role,
    replace_user_role_assignments,
    serialize_user,
)
from yuxi.storage.minio import upload_image_to_minio
from yuxi.storage.postgres.models_business import ROOT_DEPARTMENT_ID, User
from yuxi.utils import logger
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import utc_now_naive

# 创建路由器
auth = APIRouter(prefix="/auth", tags=["authentication"])


# 请求和响应模型
class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: int
    username: str
    uid: str  # 用于登录的user_id
    phone_number: str | None = None
    avatar: str | None = None
    department_id: int | None = None
    department_name: str | None = None
    roles: list["UserRoleResponse"] = Field(default_factory=list)
    effective_permissions: list[str] = Field(default_factory=list)


class UserRoleAssignmentRequest(BaseModel):
    """一条用户角色分配及其可选收窄范围。"""

    role_id: int
    scope_mode: str = Field(default="inherit", pattern=r"^(inherit|override)$")
    override_scope_type: str | None = Field(default=None, max_length=64)
    override_department_ids: list[int] = Field(default_factory=list)


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str = Field(min_length=8)
    phone_number: str | None = None
    department_id: int | None = None
    role_assignments: list[UserRoleAssignmentRequest] | None = None
    reason: str | None = Field(default=None, max_length=500)


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = None
    password: str | None = Field(default=None, min_length=8)
    phone_number: str | None = None
    avatar: str | None = None
    department_id: int | None = None
    role_assignments: list[UserRoleAssignmentRequest] | None = None
    reason: str | None = Field(default=None, max_length=500)


class UserProfileUpdate(BaseModel):
    username: str | None = None
    phone_number: str | None = None


class UserRoleResponse(BaseModel):
    """用户响应中的完整角色分配。"""

    assignment_id: int | None = None
    id: int
    code: str
    name: str
    is_builtin: bool
    is_active: bool
    scope_mode: str
    default_scope_type: str
    default_department_ids: list[int]
    override_scope_type: str | None = None
    override_department_ids: list[int]
    effective_scope_type: str
    effective_department_ids: list[int]


class UserResponse(BaseModel):
    id: int
    username: str
    uid: str
    phone_number: str | None = None
    avatar: str | None = None
    department_id: int | None = None
    department_name: str | None = None  # 部门名称
    created_at: str
    last_login: str | None = None
    roles: list[UserRoleResponse] = Field(default_factory=list)


class CurrentUserResponse(UserResponse):
    """当前登录用户及其请求级有效功能权限。"""

    effective_permissions: list[str] = Field(default_factory=list)


class UserAccessOption(BaseModel):
    uid: str
    username: str
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


class OIDCLoginResponse(BaseModel):
    """OIDC 登录响应"""

    access_token: str
    token_type: str
    user_id: int
    username: str
    uid: str
    phone_number: str | None = None
    avatar: str | None = None
    department_id: int | None = None
    department_name: str | None = None
    roles: list[UserRoleResponse] = Field(default_factory=list)
    effective_permissions: list[str] = Field(default_factory=list)


class OASSOTokenRequest(BaseModel):
    """OA 页面下发的现有登录凭证。"""

    token: str = Field(min_length=1, max_length=MAX_OA_TOKEN_LENGTH)


class OAAccountLoginRequest(BaseModel):
    """父项目内嵌时下发的 OA 账号。"""

    account: str = Field(min_length=1, max_length=MAX_OA_ACCOUNT_LENGTH)


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


# =============================================================================
# === 工具函数 ===
# =============================================================================


def _raise_cli_auth_error(exc: CLIAuthError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.code, "message": exc.message},
    ) from exc


def _raise_user_role_error(error: ValueError) -> None:
    """将角色分配校验错误映射为稳定的用户管理状态码。"""

    if isinstance(error, UserRoleAuthorizationError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    if isinstance(error, UserRoleConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


async def _get_authorized_user(
    db: AsyncSession,
    authorization: AuthorizationContext,
    permission_key: str,
    user_id: int,
) -> User:
    """读取管理域内用户，缺失或越权统一返回 404。"""

    user = await get_authorized_user(db, authorization, permission_key, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return user


async def _ensure_department_access(
    db: AsyncSession,
    authorization: AuthorizationContext,
    permission_key: str,
    department_id: int,
) -> None:
    """确认目标组织节点存在且位于当前权限的数据范围内。"""

    if not await department_is_accessible(
        authorization,
        permission_key,
        department_id,
        db=db,
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="组织节点不存在")


def _serialize_login_response(
    user: User,
    access_token: str,
    department_name: str | None = None,
) -> dict:
    """序列化登录令牌、多角色和有效权限。"""

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": user.id,
        **serialize_user(user, department_name),
        "effective_permissions": list(build_authorization_context(user).effective_permissions),
    }


# 路由：登录获取令牌
# =============================================================================
# === 认证分组 ===
# =============================================================================


@auth.post("/token", response_model=Token)
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    # 查找用户 - 支持user_id和phone_number登录
    login_identifier = form_data.username  # OAuth2表单中的username字段作为登录标识符
    client_ip = extract_client_ip(request)

    # IP+账号 与 IP 全局滑动窗口失败限速，与账号级锁定叠加
    allowed, retry_after = await check_login_rate_limit(client_ip, login_identifier)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试过于频繁，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )

    user_repository = UserRepository(db)
    user = await user_repository.get_by_login_identifier(login_identifier)

    # 如果用户不存在，为防止用户名枚举攻击，返回通用错误信息
    if not user:
        await record_login_failure(client_ip, login_identifier)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录标识或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查用户是否已被删除
    if user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该账户已注销",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查用户是否处于登录锁定状态
    if user.is_login_locked():
        remaining_time = user.get_remaining_lock_time()
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"登录被锁定，请等待 {remaining_time} 秒后再试",
            headers={"WWW-Authenticate": "Bearer", "X-Lock-Remaining": str(remaining_time)},
        )

    # 锁定已过期：清零失败计数，避免解锁后首次失败又立即再次锁定
    if user.login_locked_until is not None:
        user.reset_failed_login()
        await user_repository.save(user)

    # 验证密码
    if not AuthUtils.verify_password(user.password_hash, form_data.password):
        # 密码错误，记录 IP 维度失败并增加账号失败次数
        await record_login_failure(client_ip, login_identifier)
        user.increment_failed_login()
        await user_repository.save(user)

        # 记录失败操作
        await log_operation(db, user.id if user else None, "登录失败", f"密码错误，失败次数: {user.login_failed_count}")
        await db.commit()

        # 检查是否需要锁定
        if user.is_login_locked():
            remaining_time = user.get_remaining_lock_time()
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"由于多次登录失败，账户已被锁定 {remaining_time} 秒",
                headers={"WWW-Authenticate": "Bearer", "X-Lock-Remaining": str(remaining_time)},
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 登录成功，重置失败计数器并清除 IP+账号维度的失败记录
    user.reset_failed_login()
    user.last_login = utc_now_naive()
    await user_repository.save(user)
    await clear_login_failures(client_ip, login_identifier)

    # 生成访问令牌
    token_data = {"sub": str(user.id)}
    access_token = AuthUtils.create_access_token(token_data)

    # 记录登录操作
    await log_operation(db, user.id, "登录")

    # 获取部门名称
    department_name = None
    if user.department_id:
        department_name = await DepartmentRepository(db).get_name_by_id(user.department_id)
    await db.commit()

    user = await UserRepository().get_by_id_with_db(db, user.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="登录用户读取失败")
    return _serialize_login_response(user, access_token, department_name)


# =============================================================================
# === CLI 浏览器登录授权分组 ===
# =============================================================================


@auth.post("/cli/sessions", response_model=CLIAuthSessionCreateResponse)
async def create_cli_session(data: CLIAuthSessionCreate, db: AsyncSession = Depends(get_db)):
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
    try:
        session = await approve_cli_auth_session(db, user_code, current_user)
    except CLIAuthError as exc:
        _raise_cli_auth_error(exc)
    return CLIAuthApproveResponse(**session.to_dict())


@auth.post("/cli/sessions/token", response_model=CLIAuthTokenResponse)
async def exchange_cli_session_token(data: CLIAuthTokenRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await exchange_cli_auth_token(db, data.device_code)
    except CLIAuthError as exc:
        _raise_cli_auth_error(exc)


# 路由：校验是否需要初始化管理员
@auth.get("/check-first-run")
async def check_first_run():
    is_first_run = await UserRepository().is_first_run()
    return {"first_run": is_first_run}


# 路由：初始化管理员账户
@auth.post("/initialize", response_model=Token)
async def initialize_admin(admin_data: InitializeAdmin, db: AsyncSession = Depends(get_db)):
    # 验证用户ID格式（只支持字母数字和下划线）
    if not re.match(r"^[a-zA-Z0-9_]+$", admin_data.uid):
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

    new_admin = await UserRepository(db).get_by_id_with_db(db, new_admin.id)
    if new_admin is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="初始管理员读取失败")
    return _serialize_login_response(new_admin, access_token, created.department.name)


# 路由：获取当前用户信息
# =============================================================================
# === 用户信息分组 ===
# =============================================================================


@auth.get("/me", response_model=CurrentUserResponse)
async def read_users_me(
    authorization: AuthorizationContext = Depends(get_authorization_context),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录用户的个人信息"""
    current_user = authorization.user
    department_name = None

    if current_user.department_id:
        department_name = await DepartmentRepository(db).get_name_by_id(current_user.department_id)

    return {
        **serialize_user(current_user, department_name),
        "effective_permissions": list(authorization.effective_permissions),
    }


# 路由：更新个人资料
@auth.put("/profile", response_model=UserResponse)
async def update_profile(
    profile_data: UserProfileUpdate,
    request: Request,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """更新当前用户的个人资料"""
    update_details = []
    user_repository = UserRepository(db)

    # 更新用户名（仅允许修改显示名，不修改 user_id）
    if profile_data.username is not None:
        # 验证用户名格式
        is_valid, error_msg = validate_username(profile_data.username)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg,
            )

        # 检查用户名是否已被其他用户使用
        existing_user = await user_repository.get_by_username(profile_data.username, exclude_user_id=current_user.id)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="用户名已存在",
            )

        current_user.username = profile_data.username
        update_details.append(f"用户名: {profile_data.username}")

    # 更新手机号
    if profile_data.phone_number is not None:
        # 如果手机号不为空，验证格式
        if profile_data.phone_number and not is_valid_phone_number(profile_data.phone_number):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号格式不正确")

        # 检查手机号是否已被其他用户使用
        if profile_data.phone_number:
            existing_phone = await user_repository.get_by_phone_excluding(profile_data.phone_number, current_user.id)
            if existing_phone:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号已被其他用户使用")

        current_user.phone_number = profile_data.phone_number
        update_details.append(f"手机号: {profile_data.phone_number or '已清空'}")

    await user_repository.save(current_user)

    # 记录操作
    if update_details:
        await log_operation(db, current_user.id, "更新个人资料", f"更新个人资料: {', '.join(update_details)}", request)
    await db.commit()

    return serialize_user(current_user)


# 路由：创建新用户（管理员权限）
# =============================================================================
# === 用户管理分组 ===
# =============================================================================


@auth.post("/users", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    request: Request,
    authorization: AuthorizationContext = Depends(require_permission("user:create")),
    db: AsyncSession = Depends(get_db),
):
    """在当前管理域内创建新用户。"""

    current_user = authorization.user
    user_repo = UserRepository(db)

    # 验证用户名
    is_valid, error_msg = validate_username(user_data.username)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
        )

    # 检查用户名是否已存在
    users = await user_repo.list_users()
    if any(u.username == user_data.username for u in users):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在",
        )

    # 检查手机号是否已存在（如果提供了）
    if user_data.phone_number:
        if await user_repo.exists_by_phone(user_data.phone_number):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="手机号已存在",
            )

    # 生成唯一的 uid
    existing_uids = await user_repo.get_all_uids()
    uid = generate_unique_uid(user_data.username, existing_uids)

    # 创建新用户
    hashed_password = AuthUtils.hash_password(user_data.password)

    requested_assignments = user_data.role_assignments
    if requested_assignments is not None and not authorization.has_permission("user:role_assign"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="缺少功能权限: user:role_assign",
        )

    department_id = user_data.department_id
    if department_id is None:
        department_id = current_user.department_id or ROOT_DEPARTMENT_ID
    await _ensure_department_access(db, authorization, "user:create", department_id)

    new_user = await user_repo.create_with_db(
        db,
        {
            "username": user_data.username,
            "uid": uid,
            "phone_number": user_data.phone_number,
            "password_hash": hashed_password,
            "department_id": department_id,
        },
    )
    if requested_assignments is not None:
        try:
            await replace_user_role_assignments(
                db,
                authorization=authorization,
                target=new_user,
                assignments=[item.model_dump() for item in requested_assignments],
                reason=user_data.reason,
                check_existing=False,
            )
        except ValueError as error:
            _raise_user_role_error(error)

    # 记录操作
    await log_operation(
        db,
        current_user.id,
        "创建用户",
        f"创建用户: {user_data.username}, 角色: {', '.join(item.role.name for item in new_user.role_assignments)}",
        request,
    )
    await db.commit()

    new_user = await user_repo.get_by_id_with_db(db, new_user.id)
    if new_user is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="用户创建后读取失败")
    return serialize_user(new_user)


# 路由：获取所有用户（管理员权限）
@auth.get("/users", response_model=list[UserResponse])
async def read_users(
    skip: int = 0,
    limit: int = 100,
    department_id: int | None = None,
    direct: bool = False,
    authorization: AuthorizationContext = Depends(require_permission("user:read")),
):
    """返回管理域内用户；组织筛选默认包含全部后代。"""

    # ponytail: 授权后分页会扫描当前用户目录；规模影响延迟时再把有效范围编译为 SQL 条件。
    visible_rows = await list_authorized_users(
        authorization,
        "user:read",
        department_id=department_id,
        direct=direct,
    )
    if visible_rows is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="组织节点不存在")
    return [serialize_user(user, department_name) for user, department_name in visible_rows[skip : skip + limit]]


@auth.get("/users/access-options", response_model=list[UserAccessOption])
async def read_user_access_options(
    skip: int = 0,
    limit: int = 1000,
    department_id: int | None = None,
    direct: bool = False,
    authorization: AuthorizationContext = Depends(require_permission("user:read")),
):
    """返回与用户主列表相同管理域的候选用户。"""

    visible_rows = await list_authorized_users(
        authorization,
        "user:read",
        department_id=department_id,
        direct=direct,
    )
    if visible_rows is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="组织节点不存在")
    visible_rows = visible_rows[skip : skip + limit]
    return [
        {
            "uid": user.uid,
            "username": user.username,
            "department_id": user.department_id,
            "department_name": dept_name,
        }
        for user, dept_name in visible_rows
    ]


# 路由：获取特定用户信息（管理员权限）
@auth.get("/users/{user_id}", response_model=UserResponse)
async def read_user(
    user_id: int,
    authorization: AuthorizationContext = Depends(require_permission("user:read")),
    db: AsyncSession = Depends(get_db),
):
    """读取管理域内单个用户。"""

    user = await _get_authorized_user(db, authorization, "user:read", user_id)
    return serialize_user(user)


# 路由：更新用户信息（管理员权限）
@auth.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    request: Request,
    authorization: AuthorizationContext = Depends(get_authorization_context),
    db: AsyncSession = Depends(get_db),
):
    """修改管理域内用户，并限制新的组织归属。"""

    user_repository = UserRepository(db)
    required_permissions = []
    if user_data.role_assignments is not None:
        required_permissions.append("user:role_assign")
    if (
        any(
            value is not None
            for value in (
                user_data.username,
                user_data.password,
                user_data.phone_number,
                user_data.avatar,
                user_data.department_id,
            )
        )
        or not required_permissions
    ):
        required_permissions.append("user:update")

    for permission_key in required_permissions:
        if not authorization.has_permission(permission_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少功能权限: {permission_key}",
            )
    user = await _get_authorized_user(db, authorization, required_permissions[0], user_id)
    for permission_key in required_permissions[1:]:
        user = await _get_authorized_user(db, authorization, permission_key, user_id)

    current_user = authorization.user

    # 更新信息
    update_details = []

    if user_data.username is not None:
        # 检查用户名是否已被其他用户使用
        existing_user = await user_repository.get_by_username(user_data.username, exclude_user_id=user_id)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="用户名已存在",
            )
        user.username = user_data.username
        update_details.append(f"用户名: {user_data.username}")

    if user_data.password is not None:
        user.password_hash = AuthUtils.hash_password(user_data.password)
        update_details.append("密码已更新")

    if user_data.phone_number is not None:
        user.phone_number = user_data.phone_number
        update_details.append(f"手机号: {user_data.phone_number or '已清空'}")

    if user_data.avatar is not None:
        user.avatar = user_data.avatar
        update_details.append(f"头像: {user_data.avatar or '已清空'}")

    # 新旧归属都必须位于同一 user:update 管理域。
    if user_data.department_id is not None and user_data.department_id != user.department_id:
        await _ensure_department_access(
            db,
            authorization,
            "user:update",
            user_data.department_id,
        )

        user.department_id = user_data.department_id
        update_details.append(f"部门ID: {user_data.department_id}")

    if user_data.role_assignments is not None:
        try:
            await replace_user_role_assignments(
                db,
                authorization=authorization,
                target=user,
                assignments=[item.model_dump() for item in user_data.role_assignments],
                reason=user_data.reason,
            )
        except ValueError as error:
            _raise_user_role_error(error)
        update_details.append("角色分配")

    await user_repository.save(user)

    # 记录操作
    await log_operation(db, current_user.id, "更新用户", f"更新用户ID {user_id}: {', '.join(update_details)}", request)
    await db.commit()

    return serialize_user(user)


# 路由：删除用户（管理员权限）
@auth.delete("/users/{user_id}", response_model=dict)
async def delete_user(
    user_id: int,
    request: Request,
    authorization: AuthorizationContext = Depends(require_permission("user:delete")),
    db: AsyncSession = Depends(get_db),
):
    """软删除管理域内用户。"""

    user_repository = UserRepository(db)
    current_user = authorization.user
    user = await _get_authorized_user(db, authorization, "user:delete", user_id)

    # 不能删除超级管理员账户
    if has_active_role(user, "superadmin"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除超级管理员账户",
        )

    # 不能删除自己的账户
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除自己的账户",
        )

    # 检查是否已经被删除
    if user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该用户已经被删除",
        )

    deletion_detail = (
        f"删除用户: {user.username}, ID: {user.id}, 角色: {', '.join(item.role.name for item in user.role_assignments)}"
    )

    await user_repository.delete_for_admin(user)

    # 记录操作
    await log_operation(db, current_user.id, "删除用户", deletion_detail, request)
    await db.commit()

    return {"success": True, "message": "用户已删除"}


# 路由：验证用户名并生成user_id
@auth.post("/validate-username", response_model=UidGeneration)
async def validate_username_and_generate_uid(
    validation_data: UsernameValidation,
    _authorization: AuthorizationContext = Depends(require_permission("user:create")),
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

    # 检查用户名是否已存在
    user_repository = UserRepository(db)
    existing_user = await user_repository.get_by_username(validation_data.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在",
        )

    # 生成唯一的 uid
    existing_uids = await user_repository.get_all_uids()
    uid = generate_unique_uid(validation_data.username, existing_uids)

    return UidGeneration(username=validation_data.username, uid=uid, is_available=True)


# 路由：检查 uid 是否可用
@auth.get("/check-uid/{uid}")
async def check_uid_availability(
    uid: str,
    _authorization: AuthorizationContext = Depends(require_permission("user:create")),
    db: AsyncSession = Depends(get_db),
):
    """检查 uid 是否可用"""
    return {"uid": uid, "is_available": not await UserRepository(db).exists_by_uid(uid)}


# 路由：上传用户头像
@auth.post("/upload-avatar")
async def upload_user_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """上传用户头像"""
    try:
        avatar_url = await upload_image_to_minio(
            file,
            object_prefix=f"avatar/{current_user.id}",
            max_size_bytes=5 * 1024 * 1024,
            too_large_message="文件大小不能超过5MB",
        )

        current_user.avatar = avatar_url
        await UserRepository(db).save(current_user)
        await log_operation(db, current_user.id, "上传头像", f"更新头像: {avatar_url}")
        await db.commit()

        return {"success": True, "avatar_url": avatar_url, "message": "头像上传成功"}

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"头像上传失败: {str(e)}")


# 路由：模拟用户登录
@auth.post("/impersonate/{user_id}", response_model=Token)
async def impersonate_user(
    user_id: int,
    request: Request,
    authorization: AuthorizationContext = Depends(require_permission("user:impersonate")),
    db: AsyncSession = Depends(get_db),
):
    """在当前管理域内模拟其他用户登录。"""

    current_user = authorization.user
    target_user = await _get_authorized_user(db, authorization, "user:impersonate", user_id)

    # 不能模拟超级管理员
    if has_active_role(target_user, "superadmin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不能模拟超级管理员账户",
        )

    # 生成访问令牌
    token_data = {"sub": str(target_user.id)}
    access_token = AuthUtils.create_access_token(token_data)

    # 获取部门名称
    department_name = None
    if target_user.department_id:
        department_name = await DepartmentRepository(db).get_name_by_id(target_user.department_id)

    target_user = await UserRepository().get_by_id_with_db(db, target_user.id)
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    # 记录操作（危险操作标记）
    await log_operation(db, current_user.id, "⚠️ 危险操作-模拟用户", f"模拟用户: {target_user.username}", request)
    await db.commit()

    # 控制台警告日志
    logger.warning(f"⚠️ [危险操作] 超级管理员 {current_user.username} 模拟登录用户: {target_user.username}")

    return _serialize_login_response(target_user, access_token, department_name)


# =============================================================================
# === OA 自定义 SSO 分组 ===
# =============================================================================


@auth.post("/oa/exchange-token", response_model=Token)
async def exchange_oa_token(data: OASSOTokenRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """验证 OA 现有 token 并签发 Yuxi 登录凭证。"""
    return await exchange_oa_token_handler(data.token, db, request)


@auth.post("/oa/exchange-account", response_model=Token)
async def exchange_oa_account(data: OAAccountLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """使用父项目账号换取仅限内网试用的 Yuxi 登录凭证。"""
    return await exchange_oa_account_handler(data.account, db, request)


# =============================================================================
# === OIDC 认证分组 ===
# =============================================================================


@auth.get("/oidc/config", response_model=OIDCConfigResponse)
async def get_oidc_config():
    """获取 OIDC 配置（供前端使用）"""
    return await get_oidc_config_handler()


@auth.get("/oidc/login-url")
async def get_oidc_login_url(redirect_path: str = "/"):
    """获取 OIDC 登录 URL"""
    return await oidc_login_url_handler(redirect_path)


@auth.get("/oidc/callback", response_class=RedirectResponse)
async def oidc_callback(request: Request, code: str, state: str, db: AsyncSession = Depends(get_db)):
    """处理 OIDC 回调 - 重定向到前端 Vue 路由"""
    return await oidc_callback_handler(code, state, db, request)


@auth.post("/oidc/exchange-code", response_model=OIDCLoginResponse)
async def oidc_exchange_code(code: str = Body(..., embed=True)):
    """使用一次性 code 交换 OIDC 登录数据"""
    return await oidc_exchange_code_handler(code)
