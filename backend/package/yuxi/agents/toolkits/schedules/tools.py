"""agent 运行时 schedule 管理工具

7 个 LangGraph @tool：
  - list_my_schedules
  - get_schedule
  - create_schedule
  - update_schedule
  - delete_schedule
  - trigger_schedule
  - list_schedule_logs

所有工具通过 runtime.context.user_id 强制 owner 隔离；
admin 通过 runtime.context.is_admin（若 BaseContext 带）或
fallback 到 user.role 判断。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.toolkits.registry import tool
from yuxi.repositories.agent_config_repository import AgentConfigRepository
from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.services.schedule_manager import compute_next_run
from yuxi.services.schedule_service import ScheduleService
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import ScheduleDefinition, User
from yuxi.utils import logger


# ========== 内部 helpers ==========


def _resolve_user(runtime: ToolRuntime) -> str | None:
    """从 runtime.context 拿当前用户 user_id；缺失返回 None。"""
    context = getattr(runtime, "context", None)
    if context is None:
        return None
    return getattr(context, "user_id", None)


async def _is_admin(runtime: ToolRuntime, db_session: AsyncSession) -> bool:
    """判断当前用户是否为 admin。

    优先使用 runtime.context.is_admin（若 BaseContext 扩展过该字段）；
    否则通过 user_id 查 users.role 兜底。
    """
    context = getattr(runtime, "context", None)
    if context is not None:
        flag = getattr(context, "is_admin", None)
        if flag is not None:
            return bool(flag)
    user_id = _resolve_user(runtime)
    if not user_id:
        return False
    stmt = select(User.role).where(User.user_id == user_id).limit(1)
    result = await db_session.execute(stmt)
    role = result.scalar_one_or_none()
    return role in ("admin", "superadmin")


def _json_or_error(obj: Any, err: str) -> str:
    """成功返回 JSON 字符串；obj 为空/异常时返回 err。"""
    if obj is None:
        return err
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning(f"工具结果 JSON 序列化失败: {exc}")
        return err


async def _check_agent_ownership(
    db_session: AsyncSession,
    agent_config_id: int,
    user_id: str,
    is_admin: bool,
) -> str | None:
    """校验 agent_config 归属当前用户。

    返回 None 表示通过；返回字符串为面向 LLM 的中文错误消息。
    admin 跳过校验。
    """
    if is_admin:
        return None
    config = await AgentConfigRepository(db_session).get_by_id(agent_config_id)
    if config is None or str(config.user_id) != str(user_id):
        return "无权使用该 agent"
    return None


# ========== list_my_schedules ==========


LIST_DEFAULT_LIMIT = 20
LIST_MAX_LIMIT = 100


class ListMySchedulesInput(BaseModel):
    """列出当前用户的定时任务；admin 看全部。"""

    limit: int = LIST_DEFAULT_LIMIT
    offset: int = 0


@tool(args_schema=ListMySchedulesInput)  # type: ignore[misc]
async def list_my_schedules(  # type: ignore[no-redef]
    args: ListMySchedulesInput,
    runtime: ToolRuntime,
) -> str:
    """列出当前用户可访问的定时任务列表（admin 看全部）。

    Args:
        args.limit: 最多返回条数（默认 20，最大 100）
        args.offset: 分页偏移

    Returns:
        JSON 数组；每条含 id/name/cron_expr/enabled/next_run_at 等。
    """
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    limit = min(max(int(args.limit), 1), LIST_MAX_LIMIT)
    offset = max(int(args.offset), 0)

    async with pg_manager.get_async_session_context() as session:
        is_admin = await _is_admin(runtime, session)
        user_filter = None if is_admin else user_id
        repo = ScheduleRepository(session)
        rows = await repo.list_schedules(user_id=user_filter, limit=limit, offset=offset)

    payload = [
        {
            "id": r.id,
            "name": r.name,
            "cron_expr": r.cron_expr,
            "timezone": r.timezone,
            "enabled": r.enabled,
            "next_run_at": r.next_run_at,
            "agent_config_id": r.agent_config_id,
        }
        for r in rows
    ]
    return _json_or_error(payload, "未找到任何定时任务")


# ========== get_schedule ==========


class GetScheduleInput(BaseModel):
    """获取单条定时任务详情。"""

    schedule_id: str


@tool(args_schema=GetScheduleInput)  # type: ignore[misc]
async def get_schedule(schedule_id: str, runtime: ToolRuntime) -> str:  # type: ignore[no-redef]
    """获取单条定时任务详情（按 owner 隔离）。

    Args:
        schedule_id: 任务 ID

    Returns:
        JSON 字符串；无权访问时返回"未找到该任务"。
    """
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    async with pg_manager.get_async_session_context() as session:
        is_admin = await _is_admin(runtime, session)
        repo = ScheduleRepository(session)
        row = await repo.get_by_id_for_user(schedule_id, user_id, is_admin=is_admin)

    if row is None:
        return "未找到该任务"
    return _json_or_error(row.to_dict(), "未找到该任务")


# ========== create_schedule ==========


class CreateScheduleInput(BaseModel):
    """创建一个新的定时任务。"""

    name: str
    description: str | None = None
    agent_config_id: int
    cron_expr: str
    timezone: str = "Asia/Shanghai"
    query: str
    image_content: str | None = None
    config: dict = {}
    enabled: bool = True


@tool(args_schema=CreateScheduleInput)  # type: ignore[misc]
async def create_schedule(  # type: ignore[no-redef]
    name: str,
    description: str | None,
    agent_config_id: int,
    cron_expr: str,
    timezone: str,
    query: str,
    image_content: str | None,
    config: dict,
    enabled: bool,
    runtime: ToolRuntime,
) -> str:
    """创建新的定时任务。普通用户只能绑定自己创建的 agent_config；admin 不受限。"""
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    try:
        async with pg_manager.get_async_session_context() as session:
            is_admin = await _is_admin(runtime, session)
            err = await _check_agent_ownership(session, agent_config_id, user_id, is_admin)
            if err:
                return err

            # 计算 next_run_at；cron 失败由 compute_next_run 抛
            next_run = None
            if enabled:
                try:
                    next_run = compute_next_run(cron_expr, timezone)
                except Exception as e:
                    return f"cron 表达式无效: {e}"

            schedule = ScheduleDefinition(
                id=str(uuid.uuid4()),
                name=name,
                description=description,
                user_id=str(user_id),
                agent_config_id=agent_config_id,
                cron_expr=cron_expr,
                timezone=timezone,
                query=query,
                image_content=image_content,
                config=config or {},
                enabled=enabled,
                next_run_at=next_run,
            )
            repo = ScheduleRepository(session)
            created = await repo.create_schedule(schedule)
            return _json_or_error(created.to_dict(), "创建失败")
    except Exception as e:
        logger.error(f"create_schedule 工具异常: {e}")
        return f"创建失败: {e}"


# ========== update_schedule ==========


class UpdateScheduleInput(BaseModel):
    """更新定时任务字段；只更新提供的字段。"""

    schedule_id: str
    name: str | None = None
    description: str | None = None
    agent_config_id: int | None = None
    cron_expr: str | None = None
    timezone: str | None = None
    query: str | None = None
    image_content: str | None = None
    config: dict | None = None
    enabled: bool | None = None


@tool(args_schema=UpdateScheduleInput)  # type: ignore[misc]
async def update_schedule(  # type: ignore[no-redef]
    schedule_id: str,
    name: str | None,
    description: str | None,
    agent_config_id: int | None,
    cron_expr: str | None,
    timezone: str | None,
    query: str | None,
    image_content: str | None,
    config: dict | None,
    enabled: bool | None,
    runtime: ToolRuntime,
) -> str:
    """更新定时任务；agent_config_id 必须归属当前用户（admin 跳过）。"""
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    try:
        async with pg_manager.get_async_session_context() as session:
            is_admin = await _is_admin(runtime, session)
            if agent_config_id is not None:
                err = await _check_agent_ownership(session, agent_config_id, user_id, is_admin)
                if err:
                    return err

            update_data: dict[str, Any] = {}
            if name is not None:
                update_data["name"] = name
            if description is not None:
                update_data["description"] = description
            if agent_config_id is not None:
                update_data["agent_config_id"] = agent_config_id
            if cron_expr is not None:
                update_data["cron_expr"] = cron_expr
            if timezone is not None:
                update_data["timezone"] = timezone
            if query is not None:
                update_data["query"] = query
            if image_content is not None:
                update_data["image_content"] = image_content
            if config is not None:
                update_data["config"] = config
            if enabled is not None:
                update_data["enabled"] = enabled

            # 若改了 cron/时区/启用状态，重算 next_run_at
            if enabled or "cron_expr" in update_data or "timezone" in update_data:
                final_cron = update_data.get("cron_expr")
                final_tz = update_data.get("timezone")
                final_enabled = update_data.get("enabled", enabled if enabled is not None else True)
                if final_enabled:
                    try:
                        # 需要原值兜底；这里用 None 时抛错
                        if final_cron is None or final_tz is None:
                            raise ValueError("缺少 cron 或时区")
                        update_data["next_run_at"] = compute_next_run(final_cron, final_tz)
                    except Exception as e:
                        return f"cron 表达式无效: {e}"
                else:
                    update_data["next_run_at"] = None

            repo = ScheduleRepository(session)
            updated = await repo.update_for_user(schedule_id, user_id, update_data, is_admin=is_admin)
            if updated is None:
                return "未找到该任务"
            return _json_or_error(updated.to_dict(), "更新失败")
    except Exception as e:
        logger.error(f"update_schedule 工具异常: {e}")
        return f"更新失败: {e}"


# ========== delete_schedule ==========


class DeleteScheduleInput(BaseModel):
    """删除定时任务。"""

    schedule_id: str


@tool(args_schema=DeleteScheduleInput)  # type: ignore[misc]
async def delete_schedule(schedule_id: str, runtime: ToolRuntime) -> str:  # type: ignore[no-redef]
    """删除定时任务（按 owner 隔离）。"""
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    try:
        async with pg_manager.get_async_session_context() as session:
            is_admin = await _is_admin(runtime, session)
            repo = ScheduleRepository(session)
            ok = await repo.delete_for_user(schedule_id, user_id, is_admin=is_admin)
        if not ok:
            return "未找到该任务"
        return _json_or_error({"deleted": True, "schedule_id": schedule_id}, "删除失败")
    except Exception as e:
        logger.error(f"delete_schedule 工具异常: {e}")
        return f"删除失败: {e}"


# ========== list_schedule_logs ==========


class ListScheduleLogsInput(BaseModel):
    """列出指定定时任务的执行日志。"""

    schedule_id: str
    limit: int = LIST_DEFAULT_LIMIT
    offset: int = 0


@tool(args_schema=ListScheduleLogsInput)  # type: ignore[misc]
async def list_schedule_logs(  # type: ignore[no-redef]
    schedule_id: str,
    limit: int,
    offset: int,
    runtime: ToolRuntime,
) -> str:
    """列出指定任务的执行日志（按 owner 隔离；admin 可看全部）。"""
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    limit = min(max(int(limit), 1), LIST_MAX_LIMIT)
    offset = max(int(offset), 0)

    try:
        async with pg_manager.get_async_session_context() as session:
            is_admin = await _is_admin(runtime, session)
            repo = ScheduleRepository(session)
            logs = await repo.list_logs_for_user(schedule_id, user_id, limit=limit, offset=offset, is_admin=is_admin)
        if not logs:
            return "未找到该任务"
        return _json_or_error([log.to_dict() for log in logs], "未找到该任务")
    except Exception as e:
        logger.error(f"list_schedule_logs 工具异常: {e}")
        return f"查询失败: {e}"


# ========== trigger_schedule ==========


class TriggerScheduleInput(BaseModel):
    """立即触发一次定时任务。"""

    schedule_id: str


@tool(args_schema=TriggerScheduleInput)  # type: ignore[misc]
async def trigger_schedule(schedule_id: str, runtime: ToolRuntime) -> str:  # type: ignore[no-redef]
    """立即触发一次定时任务；不影响原 cron 周期。

    即使 schedule 处于 disabled 状态也可触发（与现有 manual_trigger_schedule 行为一致）。
    """
    user_id = _resolve_user(runtime)
    if not user_id:
        return "无法获取用户信息"

    try:
        async with pg_manager.get_async_session_context() as session:
            is_admin = await _is_admin(runtime, session)
            repo = ScheduleRepository(session)
            schedule = await repo.get_by_id_for_user(schedule_id, user_id, is_admin=is_admin)
            if schedule is None:
                return "未找到该任务"

            service = ScheduleService()
            thread_id, run_id = await service.manual_trigger_schedule(schedule=schedule, db=session)
        return _json_or_error({"thread_id": thread_id, "run_id": run_id, "schedule_id": schedule_id}, "触发失败")
    except Exception as e:
        logger.error(f"trigger_schedule 工具异常: {e}")
        return f"触发失败: {e}"
