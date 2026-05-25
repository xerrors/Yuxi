"""独立模型供应商配置路由。"""

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.services.model_provider_service import (
    check_credential_status,
    create_provider_config,
    delete_provider_config,
    fetch_remote_models,
    get_all_model_providers,
    get_model_provider_by_id,
    test_model_status_by_spec,
    update_provider_config,
)
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger

model_providers = APIRouter(prefix="/system/model-providers", tags=["model-providers"])


async def _refresh_model_cache() -> None:
    """刷新模型缓存（CRUD 操作后调用）。"""
    from yuxi.services.model_cache import model_cache

    try:
        async with pg_manager.get_async_session_context() as session:
            providers = await get_all_model_providers(session)
            model_cache.rebuild(providers)
            logger.info(f"Model cache refreshed: {len(model_cache.get_all_specs())} models loaded")
    except Exception as e:
        logger.error(f"Failed to refresh model cache: {e}")


class ModelProviderPayload(BaseModel):
    provider_id: str | None = Field(None, description="供应商稳定标识")
    display_name: str | None = Field(None, description="展示名称")
    provider_type: str | None = Field(None, description="供应商适配类型，默认 openai")
    default_protocol: str | None = Field(None, description="默认协议")
    base_url: str | None = Field(None, description="API 基础 URL")
    embedding_base_url: str | None = Field(None, description="Embedding 模型请求基础 URL")
    rerank_base_url: str | None = Field(None, description="Rerank 模型请求基础 URL")
    models_endpoint: str | None = Field(None, description="聊天/通用模型列表端点")
    embedding_models_endpoint: str | None = Field(None, description="Embedding 模型列表端点")
    rerank_models_endpoint: str | None = Field(None, description="Rerank 模型列表端点")
    api_key_env: str | None = Field(None, description="API Key 环境变量名")
    api_key: str | None = Field(None, description="直接配置的 API Key")
    capabilities: list[str] | None = Field(None, description="支持能力")
    enabled_models: list[dict[str, Any]] | None = Field(None, description="已启用模型配置")
    headers_json: dict[str, Any] | None = Field(None, description="额外请求头")
    extra_json: dict[str, Any] | None = Field(None, description="扩展配置")
    is_enabled: bool | None = Field(None, description="是否启用")
    is_builtin: bool | None = Field(None, description="是否内置")


@model_providers.get("")
async def list_providers(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取独立模型供应商配置列表。"""
    providers = await get_all_model_providers(db)
    data = []
    for p in providers:
        d = p.to_dict()
        d["credential_status"] = check_credential_status(p)
        data.append(d)
    return {"success": True, "data": data}


@model_providers.post("")
async def create_provider(
    payload: ModelProviderPayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建独立模型供应商配置。"""
    try:
        provider = await create_provider_config(
            db,
            payload.model_dump(exclude_none=True),
            current_user.username,
        )
        await _refresh_model_cache()
        return {"success": True, "data": provider.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"创建模型供应商失败: {e}")
        raise HTTPException(status_code=500, detail="创建模型供应商失败")


@model_providers.get("/{provider_id}")
async def get_provider(
    provider_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个独立模型供应商配置。"""
    provider = await get_model_provider_by_id(db, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"供应商 {provider_id} 不存在")
    data = provider.to_dict()
    data["credential_status"] = check_credential_status(provider)
    return {"success": True, "data": data}


@model_providers.put("/{provider_id}")
async def update_provider(
    provider_id: str,
    payload: ModelProviderPayload,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新独立模型供应商配置。"""
    try:
        # 获取用户显式设置过的字段（即使值为 None），以便正确处理清空操作
        unset_fields = payload.model_fields_set
        data = payload.model_dump(exclude_none=True)
        for nullable_field in (
            "api_key_env",
            "api_key",
            "default_protocol",
            "embedding_base_url",
            "rerank_base_url",
            "models_endpoint",
            "embedding_models_endpoint",
            "rerank_models_endpoint",
        ):
            if nullable_field in unset_fields and getattr(payload, nullable_field) is None:
                data[nullable_field] = None
        provider = await update_provider_config(db, provider_id, data, current_user.username)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"供应商 {provider_id} 不存在")
        await _refresh_model_cache()
        return {"success": True, "data": provider.to_dict()}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"更新模型供应商失败 {provider_id}: {e}")
        raise HTTPException(status_code=500, detail="更新模型供应商失败")


@model_providers.delete("/{provider_id}")
async def delete_provider(
    provider_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除独立模型供应商配置。"""
    deleted = await delete_provider_config(db, provider_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"供应商 {provider_id} 不存在")
    await _refresh_model_cache()
    return {"success": True}


@model_providers.get("/{provider_id}/remote-models")
async def get_remote_models(
    provider_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """实时拉取远端 /models，不落库。"""
    provider = await get_model_provider_by_id(db, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"供应商 {provider_id} 不存在")
    try:
        models = await fetch_remote_models(provider)
        return {"success": True, "data": models}
    except httpx.HTTPStatusError as e:
        # 远程 API 返回的错误，不透传状态码避免前端误判为系统认证失败
        detail = e.response.text
        if e.response.status_code == 401:
            raise HTTPException(status_code=502, detail="远端 API 认证失败，请检查 API Key 配置")
        raise HTTPException(status_code=e.response.status_code, detail=f"Models 请求失败: {detail}")
    except Exception as e:
        logger.error(f"拉取远端模型失败 {provider_id}: {e}")
        raise HTTPException(status_code=400, detail=f"拉取远端模型失败: {e}")


@model_providers.post("/models/cache/refresh")
async def refresh_model_cache(
    current_user: User = Depends(get_admin_user),
):
    """强制刷新模型缓存，从数据库重新加载所有供应商配置到 Redis。"""
    await _refresh_model_cache()
    from yuxi.services.model_cache import model_cache

    return {"success": True, "message": "缓存已刷新", "model_count": len(model_cache.get_all_specs())}


@model_providers.get("/models/v2")
async def get_v2_models(
    model_type: str = "chat",
    _current_user: User = Depends(get_required_user),
):
    """获取 v2 格式的模型列表，按 provider 分组。

    v2 模型 spec 格式: provider_id:model_id（冒号分隔）
    返回数据供前端模型选择器使用。
    """
    from yuxi.services.model_cache import model_cache

    grouped = model_cache.get_specs_grouped_by_provider(model_type)

    result = {}
    for provider_id, models in grouped.items():
        result[provider_id] = {
            "models": [
                {
                    "spec": m.spec,
                    "model_id": m.model_id,
                    "display_name": m.display_name,
                    "dimension": m.dimension,
                    "batch_size": m.batch_size,
                }
                for m in models
            ]
        }

    return {"success": True, "data": result}


@model_providers.get("/models/status")
async def get_model_status_by_spec(
    spec: str,
    current_user: User = Depends(get_admin_user),
):
    """根据 full spec 检查模型状态（自动识别 V1/V2、Chat/Embedding）。"""
    try:
        result = await test_model_status_by_spec(spec)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"测试模型状态失败 {spec}: {e}")
        return {"success": False, "data": {"spec": spec, "status": "error", "message": str(e)}}
