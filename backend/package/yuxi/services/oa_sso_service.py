"""OA 自定义 token 与 Yuxi 登录态的安全交换。"""

import os
import secrets
import urllib.parse
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from yuxi.permissions.authorization import build_authorization_context
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.operation_log_service import log_operation
from yuxi.services.user_identity_service import build_unique_external_username, resolve_external_department
from yuxi.services.user_role_service import serialize_user
from yuxi.storage.postgres.models_business import User
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger

MAX_OA_TOKEN_LENGTH = 16_384
MAX_OA_ACCOUNT_LENGTH = 64
ACTIVE_OA_USER_STATE = "service"
LOCAL_OA_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


class OASSOConfig(BaseModel):
    """OA 自定义 SSO 配置。"""

    enabled: bool = False
    userinfo_url: str = ""
    company_code: str = ""

    @classmethod
    def from_env(cls) -> "OASSOConfig":
        """从环境变量读取 OA SSO 配置。"""
        return cls(
            enabled=os.environ.get("OA_SSO_ENABLED", "false").strip().lower() == "true",
            userinfo_url=os.environ.get("OA_SSO_USERINFO_URL", "").strip(),
            company_code=os.environ.get("OA_SSO_COMPANY_CODE", "").strip(),
        )

    def is_configured(self) -> bool:
        """检查 OA SSO 配置是否可用。"""
        if not self.enabled or not self.company_code:
            return False
        try:
            parsed = urllib.parse.urlsplit(self.userinfo_url)
        except ValueError:
            return False
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        if parsed.scheme == "https":
            return True
        environment = os.environ.get("YUXI_ENV", "development").strip().lower()
        return environment == "development" and parsed.scheme == "http" and parsed.hostname in LOCAL_OA_HOSTS


class OAAccountLoginConfig(BaseModel):
    """父项目仅提供账号时使用的临时 OA 登录配置。"""

    enabled: bool = False
    login_url: str = ""
    company_code: str = ""

    @classmethod
    def from_env(cls) -> "OAAccountLoginConfig":
        """从环境变量读取 account 换票配置。"""
        return cls(
            enabled=os.environ.get("OA_ACCOUNT_LOGIN_ENABLED", "false").strip().lower() == "true",
            login_url=os.environ.get("OA_ACCOUNT_LOGIN_URL", "").strip(),
            company_code=os.environ.get("OA_ACCOUNT_LOGIN_COMPANY_CODE", "").strip(),
        )

    def is_configured(self) -> bool:
        """仅允许非生产环境启用 account 换票。"""
        environment = os.environ.get("YUXI_ENV", "development").strip().lower()
        if environment in {"prod", "production"} or not self.enabled or not self.company_code:
            return False
        try:
            parsed = urllib.parse.urlsplit(self.login_url)
        except ValueError:
            return False
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        if parsed.scheme == "https":
            return True
        return environment == "development" and parsed.scheme == "http" and parsed.hostname in LOCAL_OA_HOSTS


class OAIdentity(BaseModel):
    """OA 用户接口验证后的最小可信身份。"""

    company_code: str
    account: str
    full_name: str
    department_name: str | None = None
    department_code: str | None = None

    @property
    def uid(self) -> str:
        """返回 Yuxi 中稳定的 OA 身份键。"""
        return f"oa:{self.company_code}:{self.account}"


oa_sso_config = OASSOConfig.from_env()
oa_account_login_config = OAAccountLoginConfig.from_env()


def extract_oa_token_account(token: str) -> str:
    """从 OA 双 JWT 载荷取出一致账号，最终身份仍以 OA 接口为准。"""
    if not isinstance(token, str) or not token or len(token) > MAX_OA_TOKEN_LENGTH:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 登录凭证无效")

    token_parts = token.split("|")
    if len(token_parts) != 2:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 登录凭证格式无效")

    accounts = []
    for token_part in token_parts:
        try:
            claims = jwt.decode(token_part, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 登录凭证格式无效") from exc
        data = claims.get("data")
        account = str(data.get("account", "")).strip() if isinstance(data, dict) else ""
        if not account or len(account) > 64:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 登录凭证缺少账号")
        accounts.append(account)

    if accounts[0] != accounts[1]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 登录凭证账号不一致")
    return accounts[0]


def _primary_job(user_data: dict[str, Any]) -> dict[str, Any] | None:
    jobs = user_data.get("userJobInformationDtos")
    if not isinstance(jobs, list):
        return None
    valid_jobs = [job for job in jobs if isinstance(job, dict)]
    if not valid_jobs:
        return None

    def sort_key(job: dict[str, Any]) -> float:
        try:
            return float(job.get("pagingSort"))
        except (TypeError, ValueError):
            return float("inf")

    return min(valid_jobs, key=sort_key)


async def fetch_oa_identity(token: str, account: str) -> OAIdentity:
    """携带 OA token 请求固定用户接口并返回可信身份。"""
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10.0) as client:
            response = await client.get(
                oa_sso_config.userinfo_url,
                params={"Account": account},
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        logger.error(f"OA user info request failed: {type(exc).__name__}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "OA 用户服务暂不可用") from exc

    if response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 登录凭证已失效")
    if response.status_code != status.HTTP_200_OK:
        logger.error(f"OA user info request returned status {response.status_code}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "OA 用户服务返回异常")

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "OA 用户服务返回无效数据") from exc
    user_data = payload.get("data") if isinstance(payload, dict) and payload.get("status") == 1 else None
    if not isinstance(user_data, dict):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 登录凭证已失效")

    returned_account = str(user_data.get("account", "")).strip()
    company_code = str(user_data.get("companyCode", "")).strip()
    if returned_account != account:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 用户账号校验失败")
    if company_code != oa_sso_config.company_code:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "OA 用户不属于允许接入的公司")
    if user_data.get("userStateCode") != ACTIVE_OA_USER_STATE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "OA 用户不是在职状态")

    full_name = str(user_data.get("fullName") or user_data.get("userName") or "").strip() or account
    primary_job = _primary_job(user_data) or {}
    department_name = str(primary_job.get("appointmentDepartmentName") or "").strip()[:50] or None
    department_code = str(primary_job.get("appointmentDepartmentCode") or "").strip()[:64] or None
    return OAIdentity(
        company_code=company_code,
        account=account,
        full_name=full_name[:100],
        department_name=department_name,
        department_code=department_code,
    )


async def _complete_oa_login(
    identity: OAIdentity, db, request: Request | None, department=None, operation: str = "OA SSO 登录"
) -> dict[str, Any]:
    """按已验证的 OA 身份复用本地用户并签发 Yuxi 登录态。"""
    user_repo = UserRepository()

    result = await db.execute(select(User).where(User.uid == identity.uid))
    user = result.scalar_one_or_none()
    if user and user.is_deleted:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该 Yuxi 账户已注销")

    if user:
        user.last_login = utc_now_naive()
        if department:
            user.department_id = department.id
        await db.commit()
        await db.refresh(user)
    else:
        username = await build_unique_external_username(db, identity.full_name, identity.uid)
        user = await user_repo.create_with_db(
            db,
            {
                "username": username,
                "uid": identity.uid,
                "phone_number": None,
                "avatar": None,
                "password_hash": AuthUtils.hash_password(secrets.token_urlsafe(32)),
                "department_id": department.id if department else None,
                "last_login": utc_now_naive(),
            },
        )
        try:
            await db.commit()
            await db.refresh(user)
        except IntegrityError as exc:
            await db.rollback()
            result = await db.execute(select(User).where(User.uid == identity.uid, User.is_deleted == 0))
            user = result.scalar_one_or_none()
            if not user:
                raise HTTPException(status.HTTP_409_CONFLICT, "OA 用户创建冲突，请重试") from exc

    user = await user_repo.get_by_id_with_db(db, user.id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OA 用户不存在")

    await log_operation(db, user.id, operation, request=request)
    return {
        "access_token": AuthUtils.create_access_token({"sub": str(user.id)}),
        "token_type": "bearer",
        "user_id": user.id,
        **serialize_user(user, department.name if department else None),
        "effective_permissions": list(build_authorization_context(user).effective_permissions),
    }


async def exchange_oa_token_handler(token: str, db, request: Request | None = None) -> dict[str, Any]:
    """验证 OA token，匹配本地用户并签发 Yuxi token。"""
    if not oa_sso_config.is_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OA 免登录未配置")

    account = extract_oa_token_account(token)
    identity = await fetch_oa_identity(token, account)
    department = await resolve_external_department(db, identity.department_name)
    return await _complete_oa_login(identity, db, request, department)


async def exchange_oa_account_handler(account: str, db, request: Request | None = None) -> dict[str, Any]:
    """使用父项目提供的账号换取 OA 凭证并建立临时内网登录态。"""
    if not oa_account_login_config.is_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OA 账号登录未配置")
    # 账号只是父页面传入的待校验线索，必须使用换到的 OA token 查询用户信息后才能建立本地登录态。
    if not oa_sso_config.is_configured() or oa_sso_config.company_code != oa_account_login_config.company_code:
        logger.error("OA account login requires a matching OA user info configuration")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OA 账号身份校验未配置")

    normalized_account = account.strip() if isinstance(account, str) else ""
    if not normalized_account or len(normalized_account) > MAX_OA_ACCOUNT_LENGTH:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 账号无效")

    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10.0) as client:
            response = await client.post(
                oa_account_login_config.login_url,
                json={
                    "account": normalized_account,
                    "deviceId": "H5",
                    "companyCode": oa_account_login_config.company_code,
                    "loginType": "8",
                },
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        logger.error(f"OA account login request failed: {type(exc).__name__}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "OA 账号登录服务暂不可用") from exc

    if response.status_code != status.HTTP_200_OK:
        logger.error(f"OA account login returned status {response.status_code}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "OA 账号登录服务返回异常")
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "OA 账号登录服务返回无效数据") from exc

    response_data = payload.get("data") if isinstance(payload, dict) else None
    tokens = response_data
    if isinstance(response_data, dict) and isinstance(response_data.get("data"), dict):
        # 真实网关比 H5 的 Axios 返回值多一层 data，并用字符串 "1" 表示成功。
        if str(response_data.get("status")) != "1":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 账号登录失败")
        tokens = response_data["data"]
    if not isinstance(tokens, dict) or not all(
        isinstance(tokens.get(key), str) and tokens[key].strip() for key in ("oaToken", "saToken")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 账号登录失败")

    returned_account = str(tokens.get("account") or "").strip()
    if returned_account and returned_account != normalized_account:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OA 用户账号校验失败")

    identity = await fetch_oa_identity(tokens["oaToken"].strip(), normalized_account)
    department = await resolve_external_department(db, identity.department_name)
    return await _complete_oa_login(identity, db, request, department, operation="OA账号代入登录")
