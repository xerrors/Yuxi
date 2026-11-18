from __future__ import annotations

import copy
from collections.abc import Collection
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.presets import discover_agent_presets
from yuxi.modules.agents.runtime.agent_backends import get_agent_backend
from yuxi.modules.agents.runtime.context import (
    BaseContext,
    filter_config_by_role,
    resolve_agent_resource_options,
    validate_resource_selection,
)
from yuxi.modules.identity.models import User


async def prepare_agent_config_write(
    config_json: dict[str, Any],
    *,
    context_schema: type[BaseContext] | None,
    db: AsyncSession,
    user: User,
    agent_id: int | None = None,
) -> tuple[dict[str, Any], dict[str, set[str]]]:
    """过滤可写配置，并解析本次资源补丁对应的可访问键。"""
    filtered = filter_config_by_role(config_json, user.role, context_schema)
    if filtered != config_json:
        raise ValueError("配置包含当前角色不可写或未声明的字段")
    context = filtered.get("context")
    if not isinstance(context, dict):
        return filtered, {}

    resource_fields = (context_schema or BaseContext).get_resource_fields()
    for field_name in resource_fields.keys() & context.keys():
        context[field_name] = validate_resource_selection(field_name, context[field_name])
    submitted_fields = {
        field_name
        for field_name in resource_fields.keys() & context.keys()
        if isinstance(context[field_name], list) and context[field_name]
    }
    if not submitted_fields:
        return filtered, {}

    option_fields = {resource_fields[name] for name in submitted_fields}
    options = await resolve_agent_resource_options(option_fields, db=db, user=user, agent_id=agent_id)

    resource_access: dict[str, set[str]] = {}
    for field_name in submitted_fields:
        option_field = resource_fields[field_name]
        if option_field not in options:
            raise RuntimeError(f"智能体资源字段 {field_name} 缺少权限解析结果")
        resource_access[field_name] = {option["key"] for option in options[option_field]}
    return filtered, resource_access


async def initialize_agent_presets(db: AsyncSession) -> None:
    """确认所有角色后端存在，再按既有落库规则初始化。"""
    from yuxi.modules.agents.repositories.definitions import AgentRepository

    presets = discover_agent_presets()
    for preset in presets:
        get_agent_backend(preset.backend_id)
    repository = AgentRepository(db)
    changed = False
    for preset in presets:
        _, updated = await repository.ensure_preset(preset)
        changed = changed or updated
    if changed:
        await db.commit()


def merge_agent_config_json(
    existing: dict | None,
    patch: dict,
    *,
    resource_access: dict[str, Collection[str]],
    context_schema: type[BaseContext] = BaseContext,
) -> dict:
    """合并 Agent 配置补丁，并在资源字段上保留旧的不可见引用。"""
    if not isinstance(patch, dict):
        raise ValueError("智能体配置必须是对象")

    current = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    patch_copy = copy.deepcopy(patch)
    merged = {**current, **patch_copy}
    if "context" not in patch:
        return merged

    patch_context = patch_copy["context"]
    if not isinstance(patch_context, dict):
        raise ValueError("智能体 context 配置必须是对象")

    current_context = current.get("context")
    if not isinstance(current_context, dict):
        current_context = {}
    merged_context = {**current_context, **patch_context}

    for field_name in context_schema.get_resource_fields().keys() & patch_context.keys():
        requested = validate_resource_selection(field_name, patch_context[field_name])
        if requested == "all" or not requested:
            merged_context[field_name] = requested
            continue

        if field_name not in resource_access:
            raise ValueError(f"智能体资源字段 {field_name} 未经过权限校验")
        accessible = {str(item) for item in resource_access[field_name]}
        previous = current_context.get(field_name, [])
        if previous == "all":
            previous = []
        previous = validate_resource_selection(field_name, previous)
        previous_set = set(previous)
        unauthorized_new = [item for item in requested if item not in accessible and item not in previous_set]
        if unauthorized_new:
            raise ValueError(f"无权新增智能体资源 {field_name}: {', '.join(unauthorized_new)}")

        requested_set = set(requested)
        kept_existing = [item for item in previous if item not in accessible or item in requested_set]
        new_visible = [item for item in requested if item in accessible and item not in previous_set]
        merged_context[field_name] = [*kept_existing, *new_visible]

    merged["context"] = merged_context
    return merged
