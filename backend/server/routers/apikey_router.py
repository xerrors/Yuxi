"""API Key 管理路由"""

import hashlib
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import User, APIKey
from server.utils.auth_middleware import get_db, get_required_user, get_superadmin_user
from yuxi.utils.datetime_utils import coerce_any_to_utc_datetime, utc_now_naive

apikey_router = APIRouter(prefix="/apikey", tags=["apikey"])


def generate_api_key() -> tuple[str, str, str]:
    """生成新的 API Key

    Returns: (full_key, key_hash, key_prefix)
    - full_key: 完整密钥，仅在创建时返回一次
    - key_hash: 存储到数据库的哈希值
    - key_prefix: 保存前缀用于显示
    """
    random_part = secrets.token_hex(24)
    full_key = f"yxkey_{random_part}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    key_prefix = full_key[:12]
    return full_key, key_hash, key_prefix


class APIKeyCreate(BaseModel):
    name: str
    user_id: int | None = None
    department_id: int | None = None
    expires_at: str | None = None


class APIKeyUpdate(BaseModel):
    name: str | None = None
    expires_at: str | None = None
    is_enabled: bool | None = None


class APIKeyResponse(BaseModel):
    id: int
    key_prefix: str
    name: str
    user_id: int | None
    department_id: int | None
    expires_at: str | None
    is_enabled: bool
    last_used_at: str | None
    created_by: str
    created_at: str


class APIKeyCreateResponse(BaseModel):
    api_key: APIKeyResponse
    secret: str


@apikey_router.get("/", response_model=dict)
async def list_api_keys(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """列出所有 API Keys"""
    result = await db.execute(select(APIKey).order_by(APIKey.created_at.desc()).offset(skip).limit(limit))
    api_keys = result.scalars().all()

    total_result = await db.execute(select(func.count(APIKey.id)))
    total = total_result.scalar()

    return {
        "api_keys": [key.to_dict() for key in api_keys],
        "total": total,
    }


@apikey_router.post("/", response_model=APIKeyCreateResponse)
async def create_api_key(
    data: APIKeyCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """创建新 API Key（secret 仅在此处返回一次）"""
    # 生成 Key
    full_key, key_hash, key_prefix = generate_api_key()

    # 验证关联用户
    if data.user_id:
        result = await db.execute(select(User).filter(User.id == data.user_id))
        user = result.scalar_one_or_none()
        if not user or user.is_deleted:
            raise HTTPException(status_code=404, detail="关联的用户不存在")
    else:
        # 自动绑定为当前登录用户
        data.user_id = current_user.id

    # 解析过期时间（转换为 naive datetime 以匹配数据库字段）
    expires_at = None
    if data.expires_at:
        aware_dt = coerce_any_to_utc_datetime(data.expires_at)
        if aware_dt:
            expires_at = aware_dt.replace(tzinfo=None)

    # 创建记录
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=data.name,
        user_id=data.user_id,
        department_id=data.department_id,
        expires_at=expires_at,
        created_by=str(current_user.id),
    )

    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return APIKeyCreateResponse(
        api_key=APIKeyResponse(**api_key.to_dict()),
        secret=full_key,
    )


@apikey_router.get("/{api_key_id}", response_model=dict)
async def get_api_key(
    api_key_id: int,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个 API Key"""
    result = await db.execute(select(APIKey).filter(APIKey.id == api_key_id))
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    return {"api_key": api_key.to_dict()}


@apikey_router.put("/{api_key_id}", response_model=dict)
async def update_api_key(
    api_key_id: int,
    data: APIKeyUpdate,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 API Key"""
    result = await db.execute(select(APIKey).filter(APIKey.id == api_key_id))
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    if data.name is not None:
        api_key.name = data.name

    if data.expires_at is not None:
        aware_dt = coerce_any_to_utc_datetime(data.expires_at)
        api_key.expires_at = aware_dt.replace(tzinfo=None) if aware_dt else None

    if data.is_enabled is not None:
        api_key.is_enabled = data.is_enabled

    await db.commit()
    await db.refresh(api_key)

    return {"api_key": api_key.to_dict()}


@apikey_router.delete("/{api_key_id}", response_model=dict)
async def delete_api_key(
    api_key_id: int,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 API Key"""
    result = await db.execute(select(APIKey).filter(APIKey.id == api_key_id))
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    await db.delete(api_key)
    await db.commit()

    return {"success": True}


@apikey_router.post("/{api_key_id}/regenerate", response_model=APIKeyCreateResponse)
async def regenerate_api_key(
    api_key_id: int,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """重新生成 API Key 密钥（secret 仅在此处返回一次）"""
    result = await db.execute(select(APIKey).filter(APIKey.id == api_key_id))
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    # 生成新密钥
    full_key, key_hash, key_prefix = generate_api_key()

    api_key.key_hash = key_hash
    api_key.key_prefix = key_prefix

    await db.commit()
    await db.refresh(api_key)

    return APIKeyCreateResponse(
        api_key=APIKeyResponse(**api_key.to_dict()),
        secret=full_key,
    )
