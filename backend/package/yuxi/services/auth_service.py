"""认证服务。"""

import hashlib
import secrets
import string
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.storage.postgres.models_business import APIKey, CLIAuthSession, Department, User
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import utc_now_naive

CLI_AUTH_SESSION_TTL_SECONDS = 10 * 60
CLI_AUTH_POLL_INTERVAL_SECONDS = 2
CLI_AUTH_DEFAULT_KEY_NAME = "Yuxi CLI"
CLI_AUTH_USER_CODE_ALPHABET = "".join(ch for ch in string.ascii_uppercase + string.digits if ch not in "0O1I")

CLI_AUTH_STATUS_PENDING = "pending"
CLI_AUTH_STATUS_APPROVED = "approved"
CLI_AUTH_STATUS_CONSUMED = "consumed"
CLI_AUTH_STATUS_EXPIRED = "expired"


@dataclass
class CLIAuthError(Exception):
    code: str
    message: str
    status_code: int = 400


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _generate_device_code() -> str:
    return f"yxcli_{secrets.token_urlsafe(32)}"


def _generate_user_code() -> str:
    raw = "".join(secrets.choice(CLI_AUTH_USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


async def _generate_unique_user_code(db: AsyncSession) -> str:
    for _ in range(10):
        user_code = _generate_user_code()
        result = await db.execute(select(CLIAuthSession.id).filter(CLIAuthSession.user_code == user_code))
        if result.scalar_one_or_none() is None:
            return user_code
    raise RuntimeError("无法生成唯一 CLI 授权码")


def _expire_if_needed(session: CLIAuthSession, now=None) -> bool:
    now = now or utc_now_naive()
    if session.status in {CLI_AUTH_STATUS_PENDING, CLI_AUTH_STATUS_APPROVED} and session.expires_at <= now:
        session.status = CLI_AUTH_STATUS_EXPIRED
        return True
    return False


async def create_cli_auth_session(db: AsyncSession, key_name: str | None = None) -> tuple[CLIAuthSession, str]:
    device_code = _generate_device_code()
    now = utc_now_naive()
    session = CLIAuthSession(
        device_code_hash=_hash_secret(device_code),
        user_code=await _generate_unique_user_code(db),
        status=CLI_AUTH_STATUS_PENDING,
        key_name=(key_name or CLI_AUTH_DEFAULT_KEY_NAME).strip() or CLI_AUTH_DEFAULT_KEY_NAME,
        created_at=now,
        expires_at=now + timedelta(seconds=CLI_AUTH_SESSION_TTL_SECONDS),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session, device_code


async def get_cli_auth_session_for_user(
    db: AsyncSession, user_code: str, *, for_update: bool = False
) -> CLIAuthSession:
    stmt = select(CLIAuthSession).filter(CLIAuthSession.user_code == user_code.strip().upper())
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if session is None:
        raise CLIAuthError("not_found", "授权会话不存在", status_code=404)
    if _expire_if_needed(session):
        await db.commit()
        raise CLIAuthError("expired_token", "授权会话已过期", status_code=410)
    return session


async def approve_cli_auth_session(db: AsyncSession, user_code: str, user: User) -> CLIAuthSession:
    session = await get_cli_auth_session_for_user(db, user_code, for_update=True)
    if session.status == CLI_AUTH_STATUS_CONSUMED:
        raise CLIAuthError("already_consumed", "授权会话已完成", status_code=409)
    if session.status == CLI_AUTH_STATUS_APPROVED:
        raise CLIAuthError("already_approved", "授权会话已批准", status_code=409)
    if session.status != CLI_AUTH_STATUS_PENDING:
        raise CLIAuthError("invalid_state", "授权会话状态无效", status_code=409)

    session.status = CLI_AUTH_STATUS_APPROVED
    session.approved_user_id = user.id
    session.approved_at = utc_now_naive()
    await db.commit()
    await db.refresh(session)
    return session


async def exchange_cli_auth_token(db: AsyncSession, device_code: str) -> dict:
    result = await db.execute(
        select(CLIAuthSession).filter(CLIAuthSession.device_code_hash == _hash_secret(device_code)).with_for_update()
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise CLIAuthError("invalid_request", "授权会话不存在", status_code=404)
    if _expire_if_needed(session):
        await db.commit()
        raise CLIAuthError("expired_token", "授权会话已过期", status_code=410)
    if session.status == CLI_AUTH_STATUS_PENDING:
        raise CLIAuthError("authorization_pending", "等待浏览器授权", status_code=400)
    if session.status == CLI_AUTH_STATUS_CONSUMED:
        raise CLIAuthError("already_consumed", "授权会话已完成", status_code=409)
    if session.status != CLI_AUTH_STATUS_APPROVED or not session.approved_user_id:
        raise CLIAuthError("invalid_state", "授权会话状态无效", status_code=409)

    user_result = await db.execute(
        select(User, Department.name)
        .outerjoin(Department, User.department_id == Department.id)
        .filter(User.id == session.approved_user_id, User.is_deleted == 0)
    )
    row = user_result.one_or_none()
    if row is None:
        raise CLIAuthError("invalid_user", "授权用户不存在", status_code=409)
    user, department_name = row

    full_key, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=session.key_name,
        user_id=user.id,
        created_by=str(user.id),
    )
    db.add(api_key)
    await db.flush()

    session.status = CLI_AUTH_STATUS_CONSUMED
    session.api_key_id = api_key.id
    session.consumed_at = utc_now_naive()
    await db.commit()
    await db.refresh(api_key)

    user_data = user.to_dict()
    user_data["department_name"] = department_name

    return {
        "api_key": api_key.to_dict(),
        "secret": full_key,
        "user": user_data,
    }
