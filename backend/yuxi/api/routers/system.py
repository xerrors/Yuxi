"""系统状态、配置、日志与品牌信息路由。"""

import os
from collections import deque
from pathlib import Path
from typing import Literal

import aiofiles
import yaml
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi import get_version
from yuxi.api.dependencies.auth import get_db, get_required_user, get_superadmin_user
from yuxi.api.routers.agents.feedback import FeedbackPage
from yuxi.bootstrap.capabilities import knowledge_enabled, personal_assistant_enabled
from yuxi.infrastructure.observability.logging import LOG_FILE, logger
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.feedback import list_feedback
from yuxi.modules.identity.models import User
from yuxi.modules.system.options import system_options
from yuxi.modules.system.readiness import get_readiness
from yuxi.modules.system.services.options import save_configuration

BRAND_STATIC_DIR = Path(__file__).resolve().parents[2] / "modules/system/static"

system = APIRouter(prefix="/system", tags=["system"])


# --- 健康检查与能力发现 ---


@system.get("/health")
async def health_check():
    """返回 API 进程 liveness，不代表依赖或业务链路就绪。"""
    return {"status": "ok", "message": "进程正常运行", "version": get_version()}


@system.get("/ready")
async def readiness_check(request: Request):
    """验证 API 接流量所需的启动状态与核心依赖。"""

    result = await get_readiness(
        startup_complete=bool(getattr(request.app.state, "startup_complete", False)),
        startup_components=getattr(request.app.state, "startup_components", None),
    )
    result["version"] = get_version()
    return JSONResponse(status_code=200 if result["status"] == "ready" else 503, content=result)


@system.get("/discovery")
async def discovery():
    """系统能力发现接口（公开接口）"""
    enabled = knowledge_enabled()
    return {
        "name": "Yuxi",
        "version": get_version(),
        "api_prefix": "/api",
        "capabilities": {
            "features": {"knowledge": enabled, "personal_assistant": personal_assistant_enabled()},
            "cli": {
                "min_cli_version": "0.1.0",
                "browser_login": True,
                "api_key_auth": True,
                "remote_config": True,
                "agent_list": True,
                "agent_show": True,
                "kb_list": enabled,
                "kb_files": enabled,
                "kb_query": enabled,
                "kb_open": enabled,
                "kb_find": enabled,
            },
        },
        "endpoints": {
            "health": "/api/system/health",
            "readiness": "/api/system/ready",
            "auth_me": "/api/auth/me",
            "cli_auth_sessions": "/api/auth/cli/sessions",
            "cli_auth_authorize": "/auth/cli/authorize",
        },
    }


@system.get("/agent-feedback", response_model=FeedbackPage)
async def get_agent_feedback(
    request: Request,
    agent_id: str | None = Query(default=None, max_length=80),
    source: Literal["user", "agent"] | None = None,
    rating: Literal["like", "dislike"] | None = None,
    after: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """仅产品 JWT 平台管理员可集中查看反馈，所有 API Key 均拒绝。"""
    if getattr(request.state, "api_key", None) is not None:
        raise HTTPException(status_code=403, detail="API Key 无权查询集中反馈")
    return await list_feedback(
        db=db,
        scope=ActorScope(uid=user.uid, app_id=None, is_superadmin=True),
        platform=True,
        agent_id=agent_id,
        source=source,
        rating=rating,
        after=after,
        limit=limit,
    )


# --- 配置管理 ---


@system.get("/config")
async def get_config(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取系统配置"""
    return _serialize_system_config(await system_options.get(db))


@system.post("/config")
async def update_config_single(
    key=Body(...),
    value=Body(...),
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """更新单个配置项"""
    if not isinstance(key, str) or key not in {field["key"] for field in system_options.fields}:
        raise HTTPException(status_code=400, detail=f"未知配置项: {key}")
    try:
        await _ensure_web_provider_configured(db, {key: value})
        await save_configuration(db, system_options.key, {key: value}, current_user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_system_config(await system_options.get(db))


@system.post("/config/update")
async def update_config_batch(
    items: dict = Body(...),
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量更新配置项"""
    try:
        await _ensure_web_provider_configured(db, items)
        await save_configuration(db, system_options.key, items, current_user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_system_config(await system_options.get(db))


@system.get("/logs")
async def get_system_logs(levels: str | None = None, current_user: User = Depends(get_superadmin_user)):
    """获取当前 API 进程日志。

    Args:
        levels: 可选的日志级别过滤，多个级别用逗号分隔，如 "INFO,ERROR,DEBUG,WARNING"
    """
    try:
        level_filter = None
        if levels:
            level_filter = {level.strip().upper() for level in levels.split(",") if level.strip()}

        # 只保留最后 1000 行，deque 超限自动淘汰最旧行
        tail_lines: deque[str] = deque(maxlen=1000)
        # 修复 GBK 编码报错：强制 utf-8 读取，忽略错误
        async with aiofiles.open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
            async for line in f:
                content = line.rstrip("\n\r")
                if level_filter:
                    # 日志格式: 2025-03-10 08:26:37,269 - INFO - module - message
                    parts = content.split(" - ")
                    if len(parts) < 2 or parts[1].strip() not in level_filter:
                        continue
                tail_lines.append(content + "\n")

        return {"log": "".join(tail_lines), "message": "success", "log_file": LOG_FILE, "scope": "api"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取系统日志失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取系统日志失败: {str(e)}") from e


# --- 品牌信息 ---


async def load_info_config():
    """加载品牌信息配置；文件缺失回退模板，解析失败返回空配置。"""
    try:
        brand_file_path = os.environ.get("YUXI_BRAND_FILE_PATH")
        config_path = Path(brand_file_path) if brand_file_path else BRAND_STATIC_DIR / "info.local.yaml"

        if not config_path.exists():
            logger.debug(f"The config file {config_path} does not exist, using default config")
            config_path = BRAND_STATIC_DIR / "info.template.yaml"

        async with aiofiles.open(config_path, encoding="utf-8") as file:
            content = await file.read()

        return yaml.safe_load(content.replace("{{YUXI_VERSION}}", get_version()))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to load info config: {e}")
        return {}


@system.get("/info")
async def get_info_config():
    """获取系统信息配置（公开接口，无需认证）"""
    try:
        config = await load_info_config()
        return {"success": True, "data": config}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取信息配置失败: {e}")
        raise HTTPException(status_code=500, detail="获取信息配置失败") from e


@system.post("/info/reload")
async def reload_info_config(current_user: User = Depends(get_superadmin_user)):
    """重新加载信息配置"""
    try:
        config = await load_info_config()
        return {"success": True, "message": "配置重新加载成功", "data": config}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重新加载信息配置失败: {e}")
        raise HTTPException(status_code=500, detail="重新加载信息配置失败") from e


# --- 通用配置项与 OCR ---


class ConfigOptionValuePayload(BaseModel):
    """管理员可更新的通用配置值。"""

    value: dict


@system.get("/config/options")
async def get_config_options(
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """返回系统定义的通用配置表单和值。"""

    from yuxi.modules.system.repositories.options import list_options
    from yuxi.modules.system.services.options import serialize_option

    return {"options": [serialize_option(record) for record in await list_options(db)]}


@system.put("/config/options/{key}")
async def put_config_option(
    key: str,
    payload: ConfigOptionValuePayload,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """保存一个通用配置项的 JSON 值。"""

    from yuxi.modules.system.services.options import serialize_option

    if key == system_options.key:
        raise HTTPException(status_code=404, detail=f"配置项不存在: {key}")
    try:
        record = await save_configuration(db, key, payload.value, current_user.username)
        if record is None:
            raise HTTPException(status_code=404, detail=f"配置项不存在: {key}")
        await db.refresh(record)
        return {"option": serialize_option(record)}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@system.get("/ocr/options")
async def get_ocr_engine_options(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """返回所有代码支持的 OCR 方法和默认项。"""

    from yuxi.modules.documents.service import get_ocr_options

    return await get_ocr_options(db)


@system.get("/ocr/health")
async def get_ocr_health(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """供登录用户使用当前有效配置检查全部 OCR 方法。"""

    from yuxi.modules.documents.service import check_all_ocr_health

    return {"health": await check_all_ocr_health(db)}


class ResourceOwnerUpdate(BaseModel):
    """共享资源的目标所有者。"""

    owner_uid: str


@system.put("/resources/{kind}/{resource_id}/owner")
async def transfer_resource_owner(
    kind: str,
    resource_id: str,
    payload: ResourceOwnerUpdate,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """系统管理员转移一个共享资源。"""
    from yuxi.modules.system.services.resource_ownership import transfer_shared_resource

    try:
        await transfer_shared_resource(db, kind=kind, resource_id=resource_id, owner_uid=payload.owner_uid, actor=current_user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"success": True}


def _serialize_system_config(values: dict) -> dict:
    """把系统配置值与字段元数据组装为前端契约。"""
    fields = {
        field["key"]: {
            "des": field["label"],
            "default": field.get("default"),
            "type": field.get("type", "string"),
            "exclude": False,
        }
        for field in system_options.fields
    }
    return {**values, "_config_items": fields}


async def _ensure_web_provider_configured(db: AsyncSession, items: dict) -> None:
    """默认网页供应商必须已有凭据；协议与凭据 Owner 保持在 extensions。"""
    from yuxi.modules.extensions.options import WEB_PROVIDER_OPTIONS

    for field in ("web_search_provider", "web_fetch_provider"):
        provider = items.get(field)
        if not isinstance(provider, str) or not provider.strip():
            continue
        option = WEB_PROVIDER_OPTIONS.get(provider.strip().lower())
        if option is None:
            continue
        if not (await option.get(db)).get("api_key"):
            raise ValueError("请先配置该供应商的 API Key")
