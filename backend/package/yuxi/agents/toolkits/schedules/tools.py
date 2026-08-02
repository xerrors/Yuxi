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
