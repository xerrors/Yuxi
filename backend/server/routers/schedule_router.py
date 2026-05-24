import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.storage.postgres.models_business import User, ScheduleDefinition, ScheduleLog
from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.services.schedule_service import ScheduleService
from yuxi.services.schedule_manager import compute_next_run
from yuxi.utils.logging_config import logger

schedule_router = APIRouter(prefix="/schedules", tags=["schedules"])


class ScheduleCreateRequest(BaseModel):
    name: str = Field(..., max_length=255, description="任务名称")
    description: str | None = Field(None, description="描述信息")
    agent_config_id: int = Field(..., description="智能体配置 ID")
    cron_expr: str = Field(..., description="Cron 表达式")
    timezone: str = Field(default="Asia/Shanghai", description="时区")
    query: str = Field(..., description="发送给 Agent 的 Query")
    image_content: str | None = Field(None, description="图片 base64 内容")
    config: dict = Field(default_factory=dict, description="其他运行配置")
    enabled: bool = Field(default=True, description="是否启用")


class ScheduleUpdateRequest(BaseModel):
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    agent_config_id: int | None = None
    cron_expr: str | None = None
    timezone: str | None = None
    query: str | None = None
    image_content: str | None = None
    config: dict | None = None
    enabled: bool | None = None


def _is_admin(user: User) -> bool:
    return user.role in ["admin", "superadmin"]


def _raise_not_found(message: str = "任务配置不存在"):
    raise HTTPException(status_code=404, detail=message)


def _raise_forbidden(message: str = "无权进行该操作"):
    raise HTTPException(status_code=403, detail=message)


@schedule_router.post("")
async def create_schedule_route(
    payload: ScheduleCreateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """创建定时任务"""
    try:
        next_run = None
        if payload.enabled:
            try:
                next_run = compute_next_run(payload.cron_expr, payload.timezone)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Cron 表达式或时区错误: {e}")

        schedule = ScheduleDefinition(
            id=str(uuid.uuid4()),
            name=payload.name,
            description=payload.description,
            user_id=str(current_user.id),
            agent_config_id=payload.agent_config_id,
            cron_expr=payload.cron_expr,
            timezone=payload.timezone,
            query=payload.query,
            image_content=payload.image_content,
            config=payload.config,
            enabled=payload.enabled,
            next_run_at=next_run,
        )

        repo = ScheduleRepository(db)
        await repo.create_schedule(schedule)
        await db.commit()
        return {"success": True, "data": schedule.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create schedule: {e}")
        raise HTTPException(status_code=500, detail=f"创建定时任务失败: {e}")


@schedule_router.get("")
async def list_schedules_route(
    current_user: User = Depends(get_required_user),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """查询定时任务列表"""
    try:
        repo = ScheduleRepository(db)
        user_filter = None if _is_admin(current_user) else str(current_user.id)
        
        items = await repo.list_schedules(user_id=user_filter, limit=limit, offset=offset)
        return {"success": True, "data": [item.to_dict() for item in items]}
    except Exception as e:
        logger.error(f"Failed to list schedules: {e}")
        raise HTTPException(status_code=500, detail="获取定时任务列表失败")


@schedule_router.get("/{schedule_id}")
async def get_schedule_route(
    schedule_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取定时任务详情"""
    repo = ScheduleRepository(db)
    schedule = await repo.get_by_id(schedule_id)
    if not schedule:
        _raise_not_found()
    
    if not _is_admin(current_user) and schedule.user_id != str(current_user.id):
        _raise_forbidden()
        
    return {"success": True, "data": schedule.to_dict()}


@schedule_router.put("/{schedule_id}")
async def update_schedule_route(
    schedule_id: str,
    payload: ScheduleUpdateRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """更新定时任务"""
    try:
        repo = ScheduleRepository(db)
        schedule = await repo.get_by_id(schedule_id)
        if not schedule:
            _raise_not_found()
        
        if not _is_admin(current_user) and schedule.user_id != str(current_user.id):
            _raise_forbidden()

        update_data = payload.model_dump(exclude_unset=True)
        
        # 如果修改了启用状态、时区或 Cron 表达式，重新计算下一次执行时间
        cron_expr = update_data.get("cron_expr", schedule.cron_expr)
        timezone_str = update_data.get("timezone", schedule.timezone)
        enabled = update_data.get("enabled", schedule.enabled)

        if "enabled" in update_data or "cron_expr" in update_data or "timezone" in update_data:
            if enabled:
                try:
                    update_data["next_run_at"] = compute_next_run(cron_expr, timezone_str)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Cron 表达式或时区错误: {e}")
            else:
                update_data["next_run_at"] = None

        updated_schedule = await repo.update_schedule(schedule_id, update_data)
        await db.commit()
        return {"success": True, "data": updated_schedule.to_dict() if updated_schedule else {}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update schedule {schedule_id}: {e}")
        raise HTTPException(status_code=500, detail=f"更新定时任务失败: {e}")


@schedule_router.delete("/{schedule_id}")
async def delete_schedule_route(
    schedule_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """删除定时任务"""
    try:
        repo = ScheduleRepository(db)
        schedule = await repo.get_by_id(schedule_id)
        if not schedule:
            _raise_not_found()
        
        if not _is_admin(current_user) and schedule.user_id != str(current_user.id):
            _raise_forbidden()

        success = await repo.delete_schedule(schedule_id)
        await db.commit()
        return {"success": success}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete schedule {schedule_id}: {e}")
        raise HTTPException(status_code=500, detail="删除定时任务失败")


@schedule_router.patch("/{schedule_id}")
async def patch_schedule_route(
    schedule_id: str,
    payload: dict,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """局部更新定时任务（例如单独切换启用状态）"""
    try:
        repo = ScheduleRepository(db)
        schedule = await repo.get_by_id(schedule_id)
        if not schedule:
            _raise_not_found()
        
        if not _is_admin(current_user) and schedule.user_id != str(current_user.id):
            _raise_forbidden()

        update_data = {}
        if "enabled" in payload:
            enabled = bool(payload["enabled"])
            update_data["enabled"] = enabled
            if enabled:
                try:
                    update_data["next_run_at"] = compute_next_run(schedule.cron_expr, schedule.timezone)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Cron 表达式错误: {e}")
            else:
                update_data["next_run_at"] = None

        updated_schedule = await repo.update_schedule(schedule_id, update_data)
        await db.commit()
        return {"success": True, "data": updated_schedule.to_dict() if updated_schedule else {}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to patch schedule {schedule_id}: {e}")
        raise HTTPException(status_code=500, detail="局部更新定时任务失败")


@schedule_router.post("/{schedule_id}/trigger")
async def trigger_schedule_route(
    schedule_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """手动立即触发一次定时任务的运行"""
    try:
        repo = ScheduleRepository(db)
        schedule = await repo.get_by_id(schedule_id)
        if not schedule:
            _raise_not_found()
        
        if not _is_admin(current_user) and schedule.user_id != str(current_user.id):
            _raise_forbidden()

        service = ScheduleService()
        thread_id, run_id = await service.manual_trigger_schedule(schedule=schedule, db=db)
        await db.commit()
        return {"success": True, "data": {"thread_id": thread_id, "run_id": run_id}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to manual trigger schedule {schedule_id}: {e}")
        raise HTTPException(status_code=500, detail=f"手动触发定时任务失败: {e}")


@schedule_router.get("/{schedule_id}/logs")
async def list_schedule_logs_route(
    schedule_id: str,
    current_user: User = Depends(get_required_user),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """获取指定定时任务配置的历史执行日志列表"""
    try:
        repo = ScheduleRepository(db)
        schedule = await repo.get_by_id(schedule_id)
        if not schedule:
            _raise_not_found()
        
        if not _is_admin(current_user) and schedule.user_id != str(current_user.id):
            _raise_forbidden()

        logs = await repo.get_logs_by_schedule_id(schedule_id, limit=limit, offset=offset)
        return {"success": True, "data": [log.to_dict() for log in logs]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list logs for schedule {schedule_id}: {e}")
        raise HTTPException(status_code=500, detail="获取调度日志失败")
