"""冻结可配置 Context，运行身份与资源授权由执行入口解析。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.runtime.tool_approval import DEFAULT_TOOL_APPROVAL_MODE, normalize_tool_approval_mode
from yuxi.modules.models.providers.cache import model_cache
from yuxi.modules.system.options import system_options
from yuxi.shared.errors import InvalidInputError


async def resolve_agent_context_snapshot(
    model_spec: str | None,
    tool_approval_mode: str | None,
    agent_item,
    agent_backend,
    db: AsyncSession | None = None,
) -> dict:
    """保存配置值与 Schema 默认值，保留资源选择而不固化授权结果。"""
    context = agent_backend.context_schema()
    context.update_config((agent_item.config_json or {}).get("context") or {})
    if agent_item.backend_id == "PersonalAssistantAgent":
        context.personal_assistant_name = agent_item.name
        if model_spec is None and not context.model:
            context.model = str((await system_options.get(db))["personal_assistant_model"] or "").strip()
            if not context.model:
                raise InvalidInputError("请联系管理员配置个人助理模型", code="personal_assistant_model_missing")
    context.model = await resolve_agent_run_model_spec(model_spec, context.model, db)
    context.tool_approval_mode = resolve_agent_run_tool_approval_mode(tool_approval_mode, context.tool_approval_mode)
    return {item.name: deepcopy(getattr(context, item.name)) for item in fields(context) if item.metadata.get("configurable", True)}


async def resolve_agent_run_model_spec(requested_model: str | None, configured_model: str | None, db: AsyncSession | None = None) -> str:
    """按显式值、Agent 配置和系统默认值选择聊天模型。"""
    if requested_model is not None and not requested_model.strip():
        raise InvalidInputError("显式模型标识不能为空")
    model_spec = next(
        (candidate.strip() for candidate in (requested_model, configured_model) if isinstance(candidate, str) and candidate.strip()),
        None,
    )
    if model_spec is None:
        model_spec = str((await system_options.get(db))["default_model"]).strip()
    info = model_cache.get_model_info(model_spec)
    if not info or info.model_type != "chat":
        raise InvalidInputError(f"未找到可用聊天模型: '{model_spec}'", code="chat_model_not_found")
    return model_spec


def resolve_agent_run_tool_approval_mode(requested_mode: str | None, configured_mode: str | None) -> str:
    """解析当前执行段的工具审批模式。"""
    source = requested_mode if requested_mode is not None else configured_mode or DEFAULT_TOOL_APPROVAL_MODE
    try:
        return normalize_tool_approval_mode(source)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
