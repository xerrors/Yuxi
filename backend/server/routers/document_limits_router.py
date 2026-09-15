"""超级管理员文档处理限制接口。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from yuxi.repositories.document_limits_repository import DocumentLimitsConflict
from yuxi.services.document_limits_service import get_document_limits, save_document_limits
from yuxi.storage.postgres.models_business import User
from server.utils.auth_middleware import get_required_user, get_superadmin_user

document_limits_router = APIRouter(prefix="/system/document-limits", tags=["system"])


class LimitsRevision(BaseModel):
    """并发版本请求。"""

    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)


class LimitsUpdate(LimitsRevision):
    """整数限制值请求。"""

    upload_max_mib: int = Field(ge=1, strict=True)
    ocr_max_pages: int = Field(ge=1, strict=True)


@document_limits_router.get("")
async def read_limits(current_user: User = Depends(get_required_user)):
    """读取当前有效限制，不暴露其他配置。"""
    return await get_document_limits()


async def apply_limits(value: dict, revision: int, actor: str):
    """将服务异常映射到稳定 HTTP 状态。"""
    try:
        return await save_document_limits(value, revision, actor)
    except DocumentLimitsConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@document_limits_router.put("")
async def update_limits(body: LimitsUpdate, current_user: User = Depends(get_superadmin_user)):
    """保存超级管理员设置。"""
    return await apply_limits(body.model_dump(exclude={"revision"}), body.revision, current_user.username)


@document_limits_router.delete("")
async def reset_limits(body: LimitsRevision, current_user: User = Depends(get_superadmin_user)):
    """清除覆盖值并恢复部署默认值。"""
    return await apply_limits({}, body.revision, current_user.username)
