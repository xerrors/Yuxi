"""SubAgent 管理路由"""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException

from server.utils.auth_middleware import get_admin_user, get_db
from yuxi.services import subagent_service as service
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger

subagents_router = APIRouter(prefix="/system/subagents", tags=["subagents"])


class SubAgentCreateRequest(BaseModel):
    name: str = Field(..., description="唯一标识")
    description: str = Field(..., description="描述")
    system_prompt: str = Field(..., description="系统提示词")
    tools: list[str] = Field(default_factory=list, description="工具名称列表")
    mcps: list[str] = Field(default_factory=list, description="MCP 服务名列表")
    skills: list[str] = Field(default_factory=list, description="Skill slug 列表")
    model: str | None = Field(None, description="可选的模型覆盖")


class SubAgentUpdateRequest(BaseModel):
    description: str | None = Field(None, description="描述")
    system_prompt: str | None = Field(None, description="系统提示词")
    tools: list[str] | None = Field(None, description="工具名称列表")
    mcps: list[str] | None = Field(None, description="MCP 服务名列表")
    skills: list[str] | None = Field(None, description="Skill slug 列表")
    model: str | None = Field(None, description="可选的模型覆盖")


class SubAgentStatusRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用")


def _raise_from_value_error(e: ValueError) -> None:
    message = str(e)
    status_code = 404 if "不存在" in message else 400
    raise HTTPException(status_code=status_code, detail=message)


def _raise_internal_error(action: str, error: Exception) -> None:
    logger.exception("SubAgent %s failed: %s", action, error)
    raise HTTPException(status_code=500, detail=f"{action}失败")


def _is_subagent_name_duplicate_error(error: IntegrityError) -> bool:
    raw_message = str(getattr(error, "orig", error)).lower()
    return (
        "duplicate key" in raw_message
        and "subagents" in raw_message
        and ("(name)" in raw_message or "subagents_pkey" in raw_message)
    )


@subagents_router.get("")
async def list_subagents_route(
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 SubAgent 列表（管理员可读）"""
    try:
        items = await service.get_all_subagents(db)
        return {"success": True, "data": items}
    except Exception as e:
        _raise_internal_error("获取列表", e)


@subagents_router.get("/{name}")
async def get_subagent_route(
    name: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个 SubAgent（管理员可读）"""
    try:
        item = await service.get_subagent(name, db)
        if not item:
            raise HTTPException(status_code=404, detail=f"SubAgent '{name}' 不存在")
        return {"success": True, "data": item}
    except HTTPException:
        raise
    except Exception as e:
        _raise_internal_error("获取", e)


@subagents_router.post("")
async def create_subagent_route(
    payload: SubAgentCreateRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建 SubAgent（管理员）"""
    try:
        data = payload.model_dump()
        item = await service.create_subagent(data, created_by=current_user.username, db=db)
        return {"success": True, "data": item}
    except IntegrityError as e:
        if _is_subagent_name_duplicate_error(e):
            raise HTTPException(status_code=409, detail=f"SubAgent '{payload.name}' 已存在")
        _raise_internal_error("创建", e)
    except HTTPException:
        raise
    except ValueError as e:
        _raise_from_value_error(e)
    except Exception as e:
        _raise_internal_error("创建", e)


@subagents_router.put("/{name}")
async def update_subagent_route(
    name: str,
    payload: SubAgentUpdateRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 SubAgent（管理员）"""
    try:
        data = payload.model_dump(exclude_unset=True)
        item = await service.update_subagent(name, data, updated_by=current_user.username, db=db)
        if not item:
            raise HTTPException(status_code=404, detail=f"SubAgent '{name}' 不存在")
        return {"success": True, "data": item}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        _raise_internal_error("更新", e)


@subagents_router.delete("/{name}")
async def delete_subagent_route(
    name: str,
    _current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 SubAgent（管理员）"""
    try:
        deleted = await service.delete_subagent(name, db=db)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"SubAgent '{name}' 不存在")
        return {"success": True}
    except ValueError as e:
        _raise_from_value_error(e)
    except HTTPException:
        raise
    except Exception as e:
        _raise_internal_error("删除", e)


@subagents_router.put("/{name}/status")
async def update_subagent_status_route(
    name: str,
    payload: SubAgentStatusRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 SubAgent 启用状态（管理员）。"""
    try:
        item = await service.set_subagent_enabled(name, payload.enabled, updated_by=current_user.username, db=db)
        if not item:
            raise HTTPException(status_code=404, detail=f"SubAgent '{name}' 不存在")
        return {
            "success": True,
            "data": item,
            "message": f"SubAgent '{name}' 已{'添加' if payload.enabled else '移除'}",
        }
    except HTTPException:
        raise
    except Exception as e:
        _raise_internal_error("更新状态", e)
