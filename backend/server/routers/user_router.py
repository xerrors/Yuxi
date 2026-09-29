"""用户级配置与凭据路由"""

import re
from typing import Any
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_current_user, get_db, get_required_user
from yuxi.config import UserConfig, UserConfigSchema
from yuxi.repositories.agent_env_repository import AgentEnvRepository
from yuxi.repositories.api_key_repository import (
    APIKeyDepartmentConflict,
    APIKeyIdempotencyConflict,
    APIKeyRepository,
    APIKeySubjectUnavailable,
)
from yuxi.storage.minio import upload_image_to_minio
from yuxi.storage.postgres.models_business import User
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import coerce_any_to_utc_datetime, format_utc_datetime, utc_now_naive

user_router = APIRouter(prefix="/user", tags=["user"])

ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_ENV_COUNT = 200
MAX_ENV_KEY_LENGTH = 128
MAX_ENV_VALUE_LENGTH = 32768
MAX_USER_IMAGE_SIZE_BYTES = 5 * 1024 * 1024


class APIKeyCreate(BaseModel):
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    name: str
    user_id: int | None = None
    department_id: int | None = None
    expires_at: str | None = None
    access_level: Literal["full", "agents", "knowledge"] = "full"
    app_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class APIKeyUpdate(BaseModel):
    name: str | None = None
    expires_at: str | None = None
    is_enabled: bool | None = None
    access_level: Literal["full", "agents", "knowledge"] | None = None
    app_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class APIKeyResponse(BaseModel):
    id: int
    key_prefix: str
    name: str
    access_level: Literal["full", "agents", "knowledge"]
    app_id: str | None
    user_id: int
    department_id: int | None
    expires_at: str | None
    is_enabled: bool
    last_used_at: str | None
    created_by: str
    created_at: str


class APIKeyCreateResponse(BaseModel):
    api_key: APIKeyResponse
    secret: str


class AgentEnvUpdate(BaseModel):
    env: dict[str, Any] = Field(default_factory=dict)


class AgentEnvResponse(BaseModel):
    env: dict[str, str]
    updated_at: str | None = None


async def get_logged_in_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请登录后再访问",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@user_router.get("/config", response_model=dict)
async def get_user_config(
    current_user: User = Depends(get_logged_in_user),
    db: AsyncSession = Depends(get_db),
):
    user_config = await UserConfig.load(db, current_user.uid)
    return user_config.dump_config()


@user_router.put("/config", response_model=dict)
async def update_user_config(
    data: UserConfigSchema,
    current_user: User = Depends(get_logged_in_user),
    db: AsyncSession = Depends(get_db),
):
    user_config = await UserConfig(uid=current_user.uid, schema=data).save(db)
    return user_config.dump_config()


@user_router.post("/upload-image", response_model=dict)
async def upload_user_image(file: UploadFile = File(...), current_user: User = Depends(get_required_user)):
    try:
        image_url = await upload_image_to_minio(
            file,
            object_prefix=f"images/{current_user.uid}",
            max_size_bytes=MAX_USER_IMAGE_SIZE_BYTES,
            too_large_message="图片大小不能超过 5MB",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"success": True, "image_url": image_url, "url": image_url}


def validate_agent_env(env: dict[str, Any]) -> dict[str, str]:
    if len(env) > MAX_ENV_COUNT:
        raise HTTPException(status_code=400, detail=f"环境变量数量不能超过 {MAX_ENV_COUNT} 个")

    normalized: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str):
            raise HTTPException(status_code=400, detail="环境变量名必须是字符串")
        name = key.strip()
        if not name:
            raise HTTPException(status_code=400, detail="环境变量名不能为空")
        if len(name) > MAX_ENV_KEY_LENGTH:
            raise HTTPException(status_code=400, detail=f"环境变量名长度不能超过 {MAX_ENV_KEY_LENGTH}")
        if not ENV_KEY_PATTERN.match(name):
            raise HTTPException(status_code=400, detail=f"环境变量名 {name} 格式不正确")
        if name in normalized:
            raise HTTPException(status_code=400, detail=f"环境变量名 {name} 重复")
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"环境变量 {name} 的值必须是字符串")
        if len(value) > MAX_ENV_VALUE_LENGTH:
            raise HTTPException(status_code=400, detail=f"环境变量 {name} 的值过长")
        normalized[name] = value
    return normalized


async def get_accessible_api_key(repository: APIKeyRepository, api_key_id: int, current_user: User):
    """读取当前用户可见的 API Key，并保持既有错误状态码。"""
    access = await repository.get_accessible(
        api_key_id=api_key_id,
        requester_user_id=current_user.id,
        is_superadmin=current_user.role == "superadmin",
    )
    if access.api_key is not None:
        return access.api_key
    if not access.exists:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    raise HTTPException(status_code=403, detail="无权操作此 API Key")


@user_router.get("/apikey/", response_model=dict)
async def list_api_keys(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    api_keys, total = await APIKeyRepository(db).list_visible(
        requester_user_id=current_user.id,
        is_superadmin=current_user.role == "superadmin",
        skip=skip,
        limit=limit,
    )

    return {
        "api_keys": [key.to_dict() for key in api_keys],
        "total": total,
    }


@user_router.post("/apikey/", response_model=APIKeyCreateResponse)
async def create_api_key(
    data: APIKeyCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    if data.user_id and data.user_id != current_user.id and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="无权为其他用户创建 API Key")
    if data.access_level == "agents" and not data.app_id:
        raise HTTPException(status_code=422, detail="Agents API Key 必须填写 app_id")

    target_user_id = data.user_id or current_user.id

    full_key, key_hash, key_prefix = AuthUtils.derive_api_key(
        f"user-request:{data.request_id}",
        target_user_id,
    )
    expires_at = None
    if data.expires_at:
        aware_dt = coerce_any_to_utc_datetime(data.expires_at)
        if aware_dt:
            expires_at = aware_dt.replace(tzinfo=None)

    try:
        api_key = await APIKeyRepository(db).create(
            key_hash=key_hash,
            key_prefix=key_prefix,
            request_id=data.request_id,
            name=data.name,
            user_id=target_user_id,
            department_id=data.department_id,
            expires_at=expires_at,
            created_by=str(current_user.id),
            access_level=data.access_level,
            app_id=data.app_id,
        )
        await db.commit()
    except APIKeyIdempotencyConflict as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except APIKeySubjectUnavailable as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except APIKeyDepartmentConflict as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    return APIKeyCreateResponse(
        api_key=APIKeyResponse(**api_key.to_dict()),
        secret=full_key,
    )


@user_router.get("/apikey/{api_key_id}", response_model=dict)
async def get_api_key(
    api_key_id: int,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    api_key = await get_accessible_api_key(APIKeyRepository(db), api_key_id, current_user)
    return {"api_key": api_key.to_dict()}


@user_router.put("/apikey/{api_key_id}", response_model=dict)
async def update_api_key(
    api_key_id: int,
    data: APIKeyUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    repository = APIKeyRepository(db)
    api_key = await get_accessible_api_key(repository, api_key_id, current_user)
    access_level = data.access_level or api_key.access_level
    app_id = data.app_id if "app_id" in data.model_fields_set else api_key.app_id
    if access_level == "agents" and not app_id:
        raise HTTPException(status_code=422, detail="Agents API Key 必须填写 app_id")

    updates = {}
    if data.name is not None:
        updates["name"] = data.name
    if data.expires_at is not None:
        aware_dt = coerce_any_to_utc_datetime(data.expires_at)
        updates["expires_at"] = aware_dt.replace(tzinfo=None) if aware_dt else None
    if data.is_enabled is not None:
        updates["is_enabled"] = data.is_enabled
    if data.access_level is not None:
        updates["access_level"] = data.access_level
    if "app_id" in data.model_fields_set:
        updates["app_id"] = data.app_id

    api_key = await repository.update(api_key, updates)
    return {"api_key": api_key.to_dict()}


@user_router.delete("/apikey/{api_key_id}", response_model=dict)
async def delete_api_key(
    api_key_id: int,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    repository = APIKeyRepository(db)
    api_key = await get_accessible_api_key(repository, api_key_id, current_user)

    await repository.delete(api_key)
    return {"success": True}


@user_router.get("/agent-env", response_model=AgentEnvResponse)
async def get_agent_env(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    agent_env = await AgentEnvRepository(db).get_by_uid(current_user.uid)
    if agent_env is None:
        return AgentEnvResponse(env={})
    return AgentEnvResponse(env=agent_env.env or {}, updated_at=format_utc_datetime(agent_env.updated_at))


@user_router.put("/agent-env", response_model=AgentEnvResponse)
async def update_agent_env(
    data: AgentEnvUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    env = validate_agent_env(data.env)
    now = utc_now_naive()
    result = await AgentEnvRepository(db).upsert(uid=current_user.uid, env=env, updated_at=now)
    return AgentEnvResponse(env=result.env, updated_at=format_utc_datetime(result.updated_at))
