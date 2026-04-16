"""OIDC 服务模块。

统一封装 OIDC 配置、工具能力和认证业务处理逻辑
"""

import hashlib
import os
import secrets
import time
import urllib.parse
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from yuxi.repositories.user_repository import UserRepository
from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

from server.utils.auth_utils import AuthUtils
from server.utils.common_utils import log_operation

# 前端 OIDC 回调路由路径（与 web/src/router/index.js 中的路由保持一致）
FRONTEND_CALLBACK_PATH = "/auth/oidc/callback"
# 登录页路径
FRONTEND_LOGIN_PATH = "/login"


class OIDCConfig(BaseModel):
    """OIDC 配置模型"""

    enabled: bool = Field(default=False, description="是否启用 OIDC 认证")
    issuer_url: str = Field(default="", description="OIDC Provider 的 issuer URL")
    client_id: str = Field(default="", description="OIDC Client ID")
    client_secret: str = Field(default="", description="OIDC Client Secret")
    redirect_uri: str = Field(default="", description="OIDC 回调 URL")
    authorization_endpoint: str = Field(default="", description="授权端点 URL")
    token_endpoint: str = Field(default="", description="Token 端点 URL")
    userinfo_endpoint: str = Field(default="", description="UserInfo 端点 URL")
    end_session_endpoint: str = Field(default="", description="登出端点 URL")
    provider_name: str = Field(default="OIDC登录", description="认证源名称，显示在登录按钮上的文字")
    scopes: str = Field(default="openid profile email", description="请求的 scope")
    auto_create_user: bool = Field(default=True, description="是否自动创建用户")
    default_role: str = Field(default="user", description="OIDC 用户的默认角色")
    default_department: str = Field(default="OIDC用户", description="OIDC 用户的默认部门")
    username_claim: str = Field(default="preferred_username", description="用户名映射字段")
    email_claim: str = Field(default="email", description="邮箱映射字段")
    name_claim: str = Field(default="name", description="姓名映射字段")
    use_raw_username: bool = Field(default=False, description="是否使用原始用户名（不带oidc前缀）")
    fetch_department_info: bool = Field(default=False, description="是否从OIDC中获取部门信息")
    department_claim: str = Field(default="department", description="部门信息映射字段")
    force_prompt_login: bool = Field(default=False, description="是否强制用户重新登录（添加prompt=login参数）")

    @classmethod
    def from_env(cls) -> "OIDCConfig":
        """从环境变量加载配置"""

        def _env(name: str, default: str = "") -> str:
            return os.environ.get(name, default).strip()

        enabled = os.environ.get("OIDC_ENABLED", "false").lower() == "true"

        if not enabled:
            return cls(enabled=False)

        return cls(
            enabled=enabled,
            provider_name=_env("OIDC_PROVIDER_NAME", "OIDC登录"),
            issuer_url=_env("OIDC_ISSUER_URL"),
            client_id=_env("OIDC_CLIENT_ID"),
            client_secret=_env("OIDC_CLIENT_SECRET"),
            redirect_uri=_env("OIDC_REDIRECT_URI"),
            authorization_endpoint=_env("OIDC_AUTHORIZATION_ENDPOINT"),
            token_endpoint=_env("OIDC_TOKEN_ENDPOINT"),
            userinfo_endpoint=_env("OIDC_USERINFO_ENDPOINT"),
            end_session_endpoint=_env("OIDC_END_SESSION_ENDPOINT"),
            scopes=_env("OIDC_SCOPES", "openid profile email"),
            auto_create_user=os.environ.get("OIDC_AUTO_CREATE_USER", "true").lower() == "true",
            default_role=_env("OIDC_DEFAULT_ROLE", "user"),
            default_department=_env("OIDC_DEFAULT_DEPARTMENT", "OIDC用户"),
            username_claim=_env("OIDC_USERNAME_CLAIM", "preferred_username"),
            email_claim=_env("OIDC_EMAIL_CLAIM", "email"),
            name_claim=_env("OIDC_NAME_CLAIM", "name"),
            use_raw_username=os.environ.get("OIDC_USE_RAW_USERNAME", "false").lower() == "true",
            fetch_department_info=os.environ.get("OIDC_FETCH_DEPARTMENT_INFO", "false").lower() == "true",
            department_claim=_env("OIDC_DEPARTMENT_CLAIM", "department"),
            force_prompt_login=os.environ.get("OIDC_FORCE_PROMPT_LOGIN", "true").lower() == "true",
        )

    def is_configured(self) -> bool:
        """检查登录链接生成所需配置是否完整"""
        if not self.enabled:
            return False
        # 生成登录链接只要求 client_id + (issuer_url 或 authorization_endpoint)
        return bool(self.client_id and (self.issuer_url or self.authorization_endpoint))

    def is_token_exchange_configured(self) -> bool:
        """检查授权码换 token 所需配置是否完整"""
        if not self.enabled:
            return False
        # 回调换 token 需要 client_id + client_secret + (issuer_url 或 token_endpoint)
        return bool(self.client_id and self.client_secret and (self.issuer_url or self.token_endpoint))


oidc_config = OIDCConfig.from_env()


class OIDCProviderMetadata:
    """OIDC Provider 元数据"""

    def __init__(self):
        self.authorization_endpoint: str | None = None
        self.token_endpoint: str | None = None
        self.userinfo_endpoint: str | None = None
        self.end_session_endpoint: str | None = None
        self.last_error: str | None = None
        self._loaded = False

    async def load(self, issuer_url: str) -> bool:
        """从 discovery 端点加载元数据"""
        if self._loaded:
            return True

        discovery_url = f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(discovery_url, timeout=30.0)
                response.raise_for_status()
                metadata = response.json()

            self.authorization_endpoint = metadata.get("authorization_endpoint")
            self.token_endpoint = metadata.get("token_endpoint")
            self.userinfo_endpoint = metadata.get("userinfo_endpoint")
            self.end_session_endpoint = metadata.get("end_session_endpoint")

            # 登录 URL 生成至少需要 authorization_endpoint。
            if not self.authorization_endpoint:
                self.last_error = "discovery 响应缺少 authorization_endpoint"
                logger.error(f"Failed to load OIDC discovery: {self.last_error}, url={discovery_url}")
                return False

            self._loaded = True
            self.last_error = None
            logger.info(f"OIDC discovery loaded from {discovery_url}")
            return True

        except Exception as e:
            self.last_error = f"{type(e).__name__}: {repr(e)}"
            logger.error(f"Failed to load OIDC discovery: {self.last_error}, url={discovery_url}")
            return False


class OIDCUtils:
    """OIDC 工具类"""

    _metadata: OIDCProviderMetadata | None = None
    _state_store: dict[str, dict[str, Any]] = {}
    _login_code_store: dict[str, dict[str, Any]] = {}
    _state_ttl_seconds = 300
    _login_code_ttl_seconds = 60
    _last_metadata_error: str | None = None

    @classmethod
    def _cleanup_expired_state(cls) -> None:
        now = time.time()
        expired = [k for k, v in cls._state_store.items() if v["expires_at"] <= now]
        for key in expired:
            cls._state_store.pop(key, None)

    @classmethod
    def _cleanup_expired_login_code(cls) -> None:
        now = time.time()
        expired = [k for k, v in cls._login_code_store.items() if v["expires_at"] <= now]
        for key in expired:
            cls._login_code_store.pop(key, None)

    @classmethod
    async def get_metadata(cls) -> OIDCProviderMetadata | None:
        """获取 OIDC Provider 元数据"""
        if not oidc_config.enabled or not oidc_config.is_configured():
            cls._last_metadata_error = "OIDC 未启用或基础配置不完整"
            return None

        if cls._metadata is None:
            cls._metadata = OIDCProviderMetadata()

            if oidc_config.authorization_endpoint:
                cls._metadata.authorization_endpoint = oidc_config.authorization_endpoint
                cls._metadata.token_endpoint = oidc_config.token_endpoint
                cls._metadata.userinfo_endpoint = oidc_config.userinfo_endpoint
                cls._metadata.end_session_endpoint = oidc_config.end_session_endpoint
                cls._metadata._loaded = True
                cls._last_metadata_error = None
            else:
                success = await cls._metadata.load(oidc_config.issuer_url)
                if not success:
                    cls._last_metadata_error = cls._metadata.last_error or "OIDC discovery 加载失败"
                    return None

        if not cls._metadata.authorization_endpoint:
            cls._last_metadata_error = "OIDC 授权端点不可用"
            return None

        cls._last_metadata_error = None

        return cls._metadata

    @classmethod
    def get_last_metadata_error(cls) -> str | None:
        """获取最近一次 OIDC 元数据加载错误"""
        return cls._last_metadata_error

    @classmethod
    def generate_state(cls, redirect_path: str = "/") -> str:
        """生成 state 参数并存储"""
        cls._cleanup_expired_state()
        state = secrets.token_urlsafe(32)
        cls._state_store[state] = {
            "redirect_path": redirect_path,
            "expires_at": time.time() + cls._state_ttl_seconds,
        }
        return state

    @classmethod
    def verify_state(cls, state: str) -> dict[str, Any] | None:
        """验证 state 参数"""
        state_data = cls._state_store.pop(state, None)
        if not state_data:
            return None
        if state_data["expires_at"] <= time.time():
            return None
        return {"redirect_path": state_data["redirect_path"]}

    @classmethod
    def generate_login_code(cls, payload: dict[str, Any]) -> str:
        """生成一次性短期登录 code"""
        cls._cleanup_expired_login_code()
        code = secrets.token_urlsafe(32)
        cls._login_code_store[code] = {
            "payload": payload,
            "expires_at": time.time() + cls._login_code_ttl_seconds,
        }
        return code

    @classmethod
    def consume_login_code(cls, code: str) -> dict[str, Any] | None:
        """消费一次性短期登录 code"""
        data = cls._login_code_store.pop(code, None)
        if not data:
            return None
        if data["expires_at"] <= time.time():
            return None
        return data["payload"]

    @classmethod
    def generate_nonce(cls) -> str:
        """生成 nonce 参数"""
        return secrets.token_urlsafe(32)

    @classmethod
    async def build_authorization_url(cls, redirect_path: str = "/") -> str | None:
        """构建授权 URL"""
        metadata = await cls.get_metadata()
        if not metadata or not metadata.authorization_endpoint:
            return None

        state = cls.generate_state(redirect_path)
        nonce = cls.generate_nonce()

        redirect_uri = oidc_config.redirect_uri
        if not redirect_uri:
            redirect_uri = "/api/auth/oidc/callback"

        params = {
            "client_id": oidc_config.client_id,
            "response_type": "code",
            "scope": oidc_config.scopes,
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
        }

        # 如果配置强制登录，添加 prompt=login 参数
        if oidc_config.force_prompt_login:
            params["prompt"] = "login"

        query_string = urllib.parse.urlencode(params)
        return f"{metadata.authorization_endpoint}?{query_string}"

    @classmethod
    async def exchange_code_for_token(cls, code: str) -> dict[str, Any] | None:
        """用授权码交换令牌"""
        metadata = await cls.get_metadata()
        if not metadata or not metadata.token_endpoint:
            return None

        redirect_uri = oidc_config.redirect_uri or "/api/auth/oidc/callback"

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": oidc_config.client_id,
            "client_secret": oidc_config.client_secret,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    metadata.token_endpoint,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()

        except Exception as e:
            logger.error(f"Failed to exchange code for token: {e}")
            return None

    @classmethod
    async def get_userinfo(cls, access_token: str) -> dict[str, Any] | None:
        """获取用户信息"""
        metadata = await cls.get_metadata()
        if not metadata or not metadata.userinfo_endpoint:
            return None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    metadata.userinfo_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()

        except Exception as e:
            logger.error(f"Failed to get userinfo: {e}")
            return None

    @classmethod
    async def build_logout_url(cls, id_token: str | None = None) -> str | None:
        """构建登出 URL"""
        metadata = await cls.get_metadata()
        if not metadata or not metadata.end_session_endpoint:
            return None

        params = {"client_id": oidc_config.client_id}

        if id_token:
            params["id_token_hint"] = id_token

        if oidc_config.redirect_uri:
            params["post_logout_redirect_uri"] = oidc_config.redirect_uri

        query_string = urllib.parse.urlencode(params)
        return f"{metadata.end_session_endpoint}?{query_string}"

    @classmethod
    def extract_user_info(cls, userinfo: dict[str, Any]) -> dict[str, Any]:
        """从 userinfo 中提取用户信息"""
        sub = userinfo.get("sub", "")

        username = userinfo.get(oidc_config.username_claim, "")
        if not username:
            username = userinfo.get("preferred_username", "")
        if not username:
            username = userinfo.get("email", "").split("@")[0]
        if not username:
            username = sub[:20]

        email = userinfo.get(oidc_config.email_claim, "")
        if not email:
            email = userinfo.get("email", "")

        name = userinfo.get(oidc_config.name_claim, "")
        if not name:
            name = userinfo.get("name", "")
        if not name:
            name = username

        department_name = None
        department_description = None
        if oidc_config.fetch_department_info:
            department_name = userinfo.get(oidc_config.department_claim)
            if not department_name:
                department_name = userinfo.get("department")

            # 获取部门描述
            department_description = userinfo.get("department_description")
            if not department_description:
                department_description = userinfo.get("department_desc")

        return {
            "sub": sub,
            "username": username,
            "email": email,
            "name": name,
            "department_name": department_name,
            "department_description": department_description,
            "raw": userinfo,
        }


async def get_or_create_oidc_department(
    db,
    dept_name_from_oidc: str | None = None,
    dept_desc_from_oidc: str | None = None,
) -> Department | None:
    """获取或创建 OIDC 用户的部门"""
    # 清理并验证从 OIDC 获取的部门名称
    processed_dept_name = None
    processed_dept_desc = None

    if dept_name_from_oidc:
        # 去除首尾空格
        processed_dept_name = dept_name_from_oidc.strip()
        # 截断到 50 字符（匹配数据库限制）
        if len(processed_dept_name) > 50:
            processed_dept_name = processed_dept_name[:50]
        # 如果处理后为空，放弃使用
        if not processed_dept_name:
            processed_dept_name = None

    # 清理并验证从 OIDC 获取的部门描述
    if dept_desc_from_oidc:
        processed_dept_desc = dept_desc_from_oidc.strip()
        # 截断到 255 字符（匹配数据库限制）
        if len(processed_dept_desc) > 255:
            processed_dept_desc = processed_dept_desc[:255]
        if not processed_dept_desc:
            processed_dept_desc = None

    # 最终确定部门名称：优先使用处理后的OIDC部门名称，否则使用默认部门名称
    final_dept_name = processed_dept_name or oidc_config.default_department
    # 最终确定部门描述：优先使用处理后的OIDC部门描述，否则使用默认描述
    final_dept_desc = processed_dept_desc or f"{final_dept_name}部门"

    result = await db.execute(select(Department).filter(Department.name == final_dept_name))
    dept = result.scalar_one_or_none()

    if dept:
        # 部门已存在，直接返回
        logger.info(f"Using existing department: {final_dept_name}")
        return dept

    # 部门不存在，创建新部门
    dept = Department(
        name=final_dept_name,
        description=final_dept_desc,
    )
    db.add(dept)
    try:
        await db.commit()
        await db.refresh(dept)
        logger.info(f"Created OIDC department: {final_dept_name}")
    except IntegrityError:
        # 并发创建时部门可能已存在，再次查询
        await db.rollback()
        result = await db.execute(select(Department).filter(Department.name == final_dept_name))
        dept = result.scalar_one_or_none()

    return dept


async def find_user_by_oidc_sub(db, sub: str) -> User | None:
    """通过 OIDC sub 查找用户"""
    # 方法1: 检查是否有用户的 user_id 直接等于 "oidc:{sub}"（标准 OIDC 用户）
    standard_oidc_user_id = f"oidc:{sub}"
    # 占位绑定记录会被标记为 is_deleted=1，但我们仍需要查询它们来获取绑定关系
    result = await db.execute(select(User).filter(
        User.user_id == standard_oidc_user_id,
        User.is_deleted == 0
    ))
    user = result.scalar_one_or_none()
    if user:
        return user

    # 方法2: 检查是否有绑定占位用户格式: "oidc:{sub}:{target_user_id}"（use_raw_username 绑定记录）
    # 绑定占位用户被标记为 is_deleted=1，需要包括deleted来查询
    legacy_result = await db.execute(
        select(User).filter(
            User.user_id.like(f"{standard_oidc_user_id}:%"),
            User.is_deleted.in_([0, 1])
        ).order_by(User.id.asc())
    )
    legacy_users = list(legacy_result.scalars().all())
    if legacy_users:
        # 对于绑定占位用户，user_id 格式为 oidc:{sub}:{target_user_id}，解析出 target_user_id 并返回真实用户
        for placeholder in legacy_users:
            if placeholder.is_deleted != 1:
                # 非deleted占位，直接返回
                return placeholder
            target_user_id = _extract_oidc_placeholder_target_user_id(placeholder.user_id)
            if target_user_id is None:
                continue
            result = await db.execute(select(User).filter(User.id == target_user_id, User.is_deleted == 0))
            target_user = result.scalar_one_or_none()
            if target_user:
                logger.debug(f"Resolved OIDC binding placeholder {placeholder.user_id} to user {target_user_id}")
                return target_user
        # 如果没有解析出有效的目标用户，返回第一个非deleted legacy用户（向后兼容）
        for candidate in legacy_users:
            if candidate.is_deleted == 0:
                return candidate
        if len(legacy_users) > 1:
            logger.warning(f"Multiple legacy OIDC users matched for sub={sub}, use earliest id={legacy_users[0].id}")
        return legacy_users[0]

    return None


async def find_deleted_oidc_user_by_sub(db, sub: str) -> User | None:
    """查找已注销的 OIDC 账户（标准与历史后缀）"""
    oidc_user_id = f"oidc:{sub}"

    result = await db.execute(select(User).filter(User.user_id == oidc_user_id, User.is_deleted == 1))
    deleted_user = result.scalar_one_or_none()
    if deleted_user:
        return deleted_user

    # 检查绑定占位格式 oidc:{sub}:{target_user_id}（占位本身是deleted，需要查询目标用户）
    legacy_result = await db.execute(
        select(User).filter(
            User.user_id.like(f"{oidc_user_id}:%"),
            User.is_deleted == 1
        ).order_by(User.id.asc())
    )
    legacy_users = list(legacy_result.scalars().all())
    if legacy_users:
        for placeholder in legacy_users:
            target_user_id = _extract_oidc_placeholder_target_user_id(placeholder.user_id)
            if target_user_id is None:
                continue
            result = await db.execute(select(User).filter(User.id == target_user_id, User.is_deleted == 1))
            target_user = result.scalar_one_or_none()
            if target_user:
                return target_user
        return legacy_users[0]
    return None


def _extract_oidc_placeholder_target_user_id(user_id: str) -> int | None:
    """从占位用户ID中解析真实用户ID，允许 sub 中包含冒号。"""
    value = str(user_id or "").strip()
    if not value.startswith("oidc:"):
        return None

    # 占位格式始终以 `:{target_user_id}` 结尾，因此从右侧拆分即可避免 sub 中的冒号干扰。
    try:
        _prefix, target_user_id = value.rsplit(":", 1)
        return int(target_user_id)
    except ValueError:
        return None


async def _create_oidc_binding_placeholder(db, sub: str, target_user: User) -> None:
    """创建 OIDC sub 绑定占位用户（仅用于记录绑定关系，不用于登录）

    在 use_raw_username 模式下，我们创建一个占位用户格式: oidc:{sub}:{target_user_id},
    占位用户标记为 is_deleted=1（不参与实际登录），仅用于存储绑定关系，
    find_user_by_oidc_sub 查询时会读取该占位记录并解析出绑定的真实用户，
    这样就能在不修改User表结构的前提下，保持绑定关系可验证，防止账号冒用。

    使用传入的同一个 db session，避免跨session一致性问题。
    """
    # 占位用户格式: oidc:{sub}:{target_user_id}，这样find_user_by_oidc_sub可以解析出目标用户ID
    oidc_placeholder_id = f"oidc:{sub}:{target_user.id}"
    # 占位用户标记为 deleted，查询时需要特别包括deleted才能找到
    result = await db.execute(select(User).filter(User.user_id == oidc_placeholder_id, User.is_deleted.in_([0, 1])))
    if result.scalar_one_or_none():
        # 占位用户已存在，无需重复创建
        return

    # 创建占位用户：使用随机密码，标记为deleted，不用于实际登录，仅存储绑定关系
    random_password = secrets.token_urlsafe(32)
    password_hash = AuthUtils.hash_password(random_password)

    # username 使用 oidc-binding-{sub_hash} 避免冲突，sub_hash 基于完整 sub 生成
    import hashlib
    sub_hash = hashlib.sha256(sub.encode()).hexdigest()[:8]
    username = f"oidc-binding-{sub_hash}"

    placeholder_user = User(
        username=username,
        user_id=oidc_placeholder_id,
        phone_number=None,
        avatar=None,
        password_hash=password_hash,
        role=target_user.role,
        department_id=target_user.department_id,
        is_deleted=1,  # 标记为deleted，不参与实际登录
        last_login=utc_now_naive(),
    )

    try:
        db.add(placeholder_user)
        await db.commit()
        logger.info(
            f"Created OIDC binding placeholder (deleted) for sub {sub} -> "
            f"user {target_user.id} ({target_user.user_id})"
        )
    except IntegrityError:
        # 并发创建冲突，回滚后忽略
        await db.rollback()
        logger.info(f"OIDC binding placeholder already exists for sub {sub}")


async def build_unique_oidc_username(db, preferred_username: str, sub: str) -> str:
    """为 OIDC 用户生成不冲突的用户名"""
    base_username = preferred_username.strip() if preferred_username else ""
    if not base_username:
        base_username = f"oidc_{sub[:8]}"

    result = await db.execute(select(User.id).filter(User.username == base_username))
    if result.scalar_one_or_none() is None:
        return base_username

    hash_suffix = hashlib.sha256(sub.encode()).hexdigest()[:6]
    candidate = f"{base_username}-{hash_suffix}"
    result = await db.execute(select(User.id).filter(User.username == candidate))
    if result.scalar_one_or_none() is None:
        return candidate

    for i in range(2, 100):
        indexed_candidate = f"{candidate}-{i}"
        result = await db.execute(select(User.id).filter(User.username == indexed_candidate))
        if result.scalar_one_or_none() is None:
            return indexed_candidate

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="无法生成可用用户名，请联系管理员",
    )


async def create_oidc_user(db, user_info: dict, department_id: int | None = None) -> User:
    """创建 OIDC 用户"""
    user_repo = UserRepository()

    sub = user_info["sub"]
    preferred_username = user_info["name"] or user_info["username"]

    # 根据配置决定用户ID是否带oidc前缀
    if oidc_config.use_raw_username:
        user_id = user_info["username"]
        # 检查用户名是否已存在
        result = await db.execute(select(User).filter(User.user_id == user_id, User.is_deleted == 0))
        existing_user = result.scalar_one_or_none()
        if existing_user:
            # 用户已存在，必须验证当前sub是否已经绑定到这个用户
            # 如果sub未绑定该用户，不能直接复用，存在账号冒用风险
            user_by_sub = await find_user_by_oidc_sub(db, sub)
            if user_by_sub and user_by_sub.id == existing_user.id:
                # sub 已经正确绑定到该用户，允许返回
                logger.info(
                    f"User with raw username {user_id} already exists and "
                    f"bound to sub {sub}, returning existing user"
                )
                return existing_user
            elif user_by_sub is None:
                # sub 尚未绑定任何用户，可以将sub绑定到这个现有用户
                logger.info(f"Binding new OIDC sub {sub} to existing user with raw username {user_id}")
                await _create_oidc_binding_placeholder(db, sub, existing_user)
                return existing_user
            else:
                # sub 已经绑定到另一个用户，冲突，拒绝创建
                logger.warning(
                    f"Cannot create OIDC user with raw username {user_id}: "
                    f"sub {sub} is already bound to another user {user_by_sub.id}, conflict"
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"用户名 {user_id} 已存在且OIDC标识 {sub} 已绑定到其他账号，请联系管理员处理冲突",
                )
    else:
        user_id = f"oidc:{sub}"

    random_password = secrets.token_urlsafe(32)
    password_hash = AuthUtils.hash_password(random_password)

    username = await build_unique_oidc_username(db, preferred_username, sub)

    for retry_index in range(3):
        try:
            new_user = await user_repo.create(
                {
                    "username": username,
                    "user_id": user_id,
                    "phone_number": None,
                    "avatar": None,
                    "password_hash": password_hash,
                    "role": oidc_config.default_role,
                    "department_id": department_id,
                    "last_login": utc_now_naive(),
                }
            )
            logger.info(f"Created OIDC user: {new_user.username} ({user_id})")

            # use_raw_username 模式下，创建占位用户记录绑定关系
            if oidc_config.use_raw_username:
                await _create_oidc_binding_placeholder(db, sub, new_user)

            return new_user
        except IntegrityError:
            existing_user = await find_user_by_oidc_sub(db, sub)
            if existing_user:
                return existing_user
            username = await build_unique_oidc_username(db, f"{preferred_username}-{retry_index + 2}", sub)

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="创建 OIDC 用户失败，请重试",
    )


async def restore_deleted_oidc_user(db, deleted_user: User, user_info: dict) -> User:
    """恢复已注销的 OIDC 用户并返回可登录用户"""
    preferred_username = user_info["name"] or user_info["username"]

    deleted_user.is_deleted = 0
    deleted_user.deleted_at = None
    deleted_user.last_login = utc_now_naive()
    deleted_user.phone_number = None
    deleted_user.avatar = None

    if deleted_user.username.startswith("已注销用户-"):
        deleted_user.username = await build_unique_oidc_username(db, preferred_username, user_info["sub"])

    if deleted_user.password_hash == "DELETED":
        random_password = secrets.token_urlsafe(32)
        deleted_user.password_hash = AuthUtils.hash_password(random_password)

    await db.commit()
    await db.refresh(deleted_user)
    logger.info(f"Restored deleted OIDC user: {deleted_user.username} ({deleted_user.user_id})")
    return deleted_user


async def update_oidc_user_login(db, user: User) -> None:
    """更新 OIDC 用户登录时间"""
    user.last_login = utc_now_naive()
    await db.commit()


def _redirect_to_callback(exchange_code: str) -> RedirectResponse:
    """成功后重定向到前端 OIDC 回调页面，仅携带一次性 code"""
    url = f"{FRONTEND_CALLBACK_PATH}?{urlencode({'code': exchange_code})}"
    return RedirectResponse(url=url, status_code=302)


def _redirect_to_login_with_error(error_message: str) -> RedirectResponse:
    """失败时重定向到登录页并携带错误信息"""
    url = f"{FRONTEND_LOGIN_PATH}?{urlencode({'oidc_error': error_message})}"
    return RedirectResponse(url=url, status_code=302)


async def get_oidc_config_handler():
    """获取 OIDC 配置（供前端使用）"""
    if not oidc_config.enabled or not oidc_config.is_configured():
        return {"enabled": False}

    provider_name = oidc_config.provider_name
    return {"enabled": True, "provider_name": provider_name}


async def oidc_callback_handler(code: str, state: str, db, request: Request | None = None):
    """处理 OIDC 回调 - 重定向到前端 Vue 路由"""

    if not oidc_config.is_token_exchange_configured():
        return _redirect_to_login_with_error("OIDC 配置不完整，请联系管理员")

    if not OIDCUtils.verify_state(state):
        return _redirect_to_login_with_error("登录会话已过期，请返回登录页重试")

    token_response = await OIDCUtils.exchange_code_for_token(code)
    if not token_response:
        return _redirect_to_login_with_error("无法获取访问令牌，请返回登录页重试")

    access_token = token_response.get("access_token")
    if not access_token:
        return _redirect_to_login_with_error("无法获取访问令牌，请返回登录页重试")

    userinfo = await OIDCUtils.get_userinfo(access_token)
    if not userinfo:
        return _redirect_to_login_with_error("无法获取用户信息，请返回登录页重试")

    extracted_info = OIDCUtils.extract_user_info(userinfo)
    sub = extracted_info["sub"]

    if not sub:
        return _redirect_to_login_with_error("无法获取用户标识，请返回登录页重试")

    # 查找用户：总是先通过 sub 查找，保证绑定关系可验证
    user_by_sub = await find_user_by_oidc_sub(db, sub)

    if oidc_config.use_raw_username:
        # 使用原始用户名模式
        username = extracted_info["username"]
        user = None
        if username:
            result = await db.execute(select(User).filter(User.user_id == username, User.is_deleted == 0))
            user_by_name = result.scalar_one_or_none()

            if user_by_sub:
                # sub 已经绑定到一个用户
                if user_by_name and user_by_sub.id == user_by_name.id:
                    # sub 绑定的用户就是找到的用户名用户 -> 验证通过
                    user = user_by_name
                    logger.info(f"OIDC user logged in with raw username: {username} (sub: {sub})")
                else:
                    # sub 已经绑定到另一个用户，存在冲突，拒绝登录
                    conflict_name = user_by_sub.username if not user_by_name else user_by_name.username
                    logger.warning(
                        f"OIDC sub {sub} is already bound to a different user, "
                        f"login rejected to prevent account hijacking (conflict: {conflict_name})"
                    )
                    return _redirect_to_login_with_error("OIDC标识已绑定到其他账号，请联系管理员处理绑定冲突")
            else:
                # sub 尚未绑定到任何用户
                if user_by_name:
                    # 用户名存在，且 sub 没有绑定 -> 允许登录，并创建绑定记录
                    # 在不修改表结构的情况下，我们创建一个占位用户 oidc:{sub} 来记录绑定关系
                    # 这个占位用户不会被用来登录，仅用于存储sub -> 用户的绑定关系
                    user = user_by_name
                    logger.info(f"Binding new OIDC sub {sub} to existing user with raw username: {username}")
                    # 创建绑定占位用户（后台静默创建，不影响现有用户）
                    await _create_oidc_binding_placeholder(db, sub, user_by_name)
                else:
                    # 用户名不存在，需要创建新用户
                    if oidc_config.auto_create_user:
                        user = None  # 让后续逻辑创建
                    else:
                        return _redirect_to_login_with_error("用户不存在，请联系管理员开通账号")
        else:
            # 没有获取到 username，回退到按sub查找
            user = user_by_sub
    else:
        # 标准 OIDC 模式，通过 sub 查找
        user = user_by_sub

    if user:
        await update_oidc_user_login(db, user)
        logger.info(f"OIDC user logged in: {user.username}")
    elif oidc_config.auto_create_user:
        deleted_user = await find_deleted_oidc_user_by_sub(db, sub)
        if deleted_user:
            user = await restore_deleted_oidc_user(db, deleted_user, extracted_info)
            logger.info(f"OIDC deleted user restored and logged in: {user.username}")
        else:
            # 从用户信息中获取部门信息
            dept_name = extracted_info.get("department_name")
            dept_desc = extracted_info.get("department_description")
            dept = await get_or_create_oidc_department(db, dept_name, dept_desc)
            department_id = dept.id if dept else None
            user = await create_oidc_user(db, extracted_info, department_id)
    else:
        return _redirect_to_login_with_error("用户未注册，请联系管理员开通账号")

    if user.is_deleted:
        return _redirect_to_login_with_error("该账户已注销")

    token_data = {"sub": str(user.id)}
    jwt_token = AuthUtils.create_access_token(token_data)

    await log_operation(db, user.id, "OIDC 登录", request=request)

    department_name = None
    if user.department_id:
        result = await db.execute(select(Department.name).filter(Department.id == user.department_id))
        department_name = result.scalar_one_or_none()

    response_data = {
        "access_token": jwt_token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "user_id_login": user.user_id,
        "phone_number": user.phone_number,
        "avatar": user.avatar,
        "role": user.role,
        "department_id": user.department_id,
        "department_name": department_name,
    }

    exchange_code = OIDCUtils.generate_login_code(response_data)
    return _redirect_to_callback(exchange_code)


async def oidc_exchange_code_handler(code: str) -> dict:
    """用一次性 code 交换登录响应数据"""
    token_data = OIDCUtils.consume_login_code(code)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="登录 code 无效或已过期，请重新登录",
        )
    return token_data


async def oidc_login_url_handler(redirect_path: str = "/"):
    """获取 OIDC 登录 URL"""
    if not oidc_config.enabled or not oidc_config.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC 登录暂不可用，请联系管理员",
        )

    login_url = await OIDCUtils.build_authorization_url(redirect_path)
    if not login_url:
        metadata_error = OIDCUtils.get_last_metadata_error()
        if metadata_error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"生成登录链接失败：{metadata_error}",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="生成登录链接失败，请稍后重试或联系管理员",
        )

    return {"login_url": login_url}
