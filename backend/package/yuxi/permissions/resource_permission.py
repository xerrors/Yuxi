"""统一解析 Agent、Skill 与知识库的共享权限。"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ResourcePermission(StrEnum):
    """资源权限等级，数值顺序用于判断权限是否足够。"""

    NONE = "none"
    READ = "read"
    MANAGE = "manage"


class ResourcePermissionDenied(PermissionError):
    """当前用户的资源权限不足。"""


class ShareableResource(Protocol):
    """声明可通过共享配置进行权限解析的资源字段。"""

    created_by: str | None
    share_config: dict | None


@dataclass(frozen=True)
class ResourcePermissionPolicy:
    """声明资源类型允许的角色上限，不包含共享范围匹配逻辑。"""

    role_ceiling: dict[str, ResourcePermission]


RESOURCE_PERMISSION_ORDER = {
    ResourcePermission.NONE: 0,
    ResourcePermission.READ: 1,
    ResourcePermission.MANAGE: 2,
}

DEFAULT_SCOPE = {"access_level": "global", "department_ids": [], "user_uids": []}
KNOWLEDGE_BASE_PERMISSION_POLICY = ResourcePermissionPolicy(
    role_ceiling={
        "user": ResourcePermission.READ,
        "admin": ResourcePermission.MANAGE,
        "superadmin": ResourcePermission.MANAGE,
    }
)
AGENT_PERMISSION_POLICY = ResourcePermissionPolicy(
    role_ceiling={
        "user": ResourcePermission.MANAGE,
        "admin": ResourcePermission.MANAGE,
        "superadmin": ResourcePermission.MANAGE,
    }
)
SKILL_PERMISSION_POLICY = AGENT_PERMISSION_POLICY


def _normalize_scope(scope: dict | None) -> dict | None:
    """规范化共享范围并校验其访问级别与成员列表。"""

    if scope is None:
        return None
    if not isinstance(scope, dict):
        raise ValueError("权限范围必须是对象")

    access_level = scope.get("access_level") or "global"
    if access_level not in {"global", "department", "user"}:
        raise ValueError("无效的资源权限范围")

    if access_level == "global":
        return DEFAULT_SCOPE.copy()
    if access_level == "department":
        department_ids = sorted({int(value) for value in scope.get("department_ids") or []})
        if not department_ids:
            raise ValueError("部门权限至少需要选择一个部门")
        return {"access_level": access_level, "department_ids": department_ids, "user_uids": []}

    user_uids = sorted({str(value).strip() for value in scope.get("user_uids") or [] if str(value).strip()})
    if not user_uids:
        raise ValueError("指定用户权限至少需要选择一个用户")
    return {"access_level": access_level, "department_ids": [], "user_uids": user_uids}


def _validate_manage_scope(read_scope: dict | None, manage_scope: dict | None) -> None:
    """确保管理范围不会超出读取范围。"""

    if not read_scope or not manage_scope or read_scope["access_level"] == "global":
        return

    read_level = read_scope["access_level"]
    manage_level = manage_scope["access_level"]
    if manage_level != read_level:
        raise ValueError("管理范围必须包含在读取范围内")
    if read_level == manage_level == "department":
        if not set(manage_scope["department_ids"]).issubset(read_scope["department_ids"]):
            raise ValueError("管理范围必须包含在读取范围内")
    elif read_level == manage_level == "user":
        if not set(manage_scope["user_uids"]).issubset(read_scope["user_uids"]):
            raise ValueError("管理范围必须包含在读取范围内")


def normalize_permission_config(
    share_config: dict | None,
    *,
    allowed_access_levels: Collection[str] | None = None,
    unauthorized_access_level_message: str = "当前用户无权使用该资源共享范围",
    strict: bool = False,
) -> dict:
    """规范化并校验 v2 共享配置。"""

    config = share_config if isinstance(share_config, dict) else {}
    if config.get("version") == 2:
        read_scope = _normalize_scope(config.get("read_scope"))
        manage_scope = _normalize_scope(config.get("manage_scope"))
        try:
            _validate_manage_scope(read_scope, manage_scope)
        except ValueError:
            if strict:
                raise
            # 读取历史配置时保持原值；保存时由 strict 校验拒绝越界配置。
        normalized = {
            "version": 2,
            "read_scope": read_scope,
            "manage_scope": manage_scope,
        }
        if allowed_access_levels is not None:
            for scope in (normalized["read_scope"], normalized["manage_scope"]):
                if scope and scope["access_level"] not in allowed_access_levels:
                    raise ValueError(unauthorized_access_level_message)
        return normalized
    raise ValueError("资源共享配置必须使用 version 2")


def scope_matches(user: Any, scope: dict | None) -> bool:
    """判断用户是否命中一个共享范围。"""

    if not scope:
        return False
    access_level = scope.get("access_level")
    if access_level == "global":
        return True
    if access_level == "department":
        department_id = _value(user, "department_id")
        try:
            return department_id is not None and int(department_id) in scope.get("department_ids", [])
        except (TypeError, ValueError):
            return False
    if access_level == "user":
        return str(_value(user, "uid", "") or "") in scope.get("user_uids", [])
    return False


def _value(source: Any, key: str, default: Any = None) -> Any:
    """从字典或对象读取属性，统一权限解析的输入访问方式。"""

    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _minimum_permission(left: ResourcePermission, right: ResourcePermission) -> ResourcePermission:
    """按权限等级顺序返回两者中更低的权限。"""

    return left if RESOURCE_PERMISSION_ORDER[left] <= RESOURCE_PERMISSION_ORDER[right] else right


def resolve_resource_permission(
    user: Any,
    resource: ShareableResource,
    policy: ResourcePermissionPolicy,
) -> ResourcePermission:
    """解析资源所有权、共享范围和角色上限后的有效权限。"""

    if _value(user, "role") == "superadmin":
        return ResourcePermission.MANAGE

    raw_share_config = _value(resource, "share_config")
    config = normalize_permission_config(
        raw_share_config,
    )
    if str(_value(resource, "created_by", "") or "") == str(_value(user, "uid", "") or ""):
        return ResourcePermission.MANAGE
    elif scope_matches(user, config["manage_scope"]) and (
        config["read_scope"] is None or scope_matches(user, config["read_scope"])
    ):
        granted = ResourcePermission.MANAGE
    elif scope_matches(user, config["read_scope"]):
        granted = ResourcePermission.READ
    else:
        granted = ResourcePermission.NONE

    ceiling = policy.role_ceiling.get(_value(user, "role"), ResourcePermission.READ)
    return _minimum_permission(granted, ceiling)


def require_resource_permission(
    actual: ResourcePermission,
    required: ResourcePermission,
) -> None:
    """在权限不足时显式失败。"""

    if RESOURCE_PERMISSION_ORDER[actual] < RESOURCE_PERMISSION_ORDER[required]:
        raise ResourcePermissionDenied(f"需要 {required.value} 权限，当前为 {actual.value}")


def resolve_knowledge_base_permission(user: Any, resource: ShareableResource) -> ResourcePermission:
    """个人库仅所有者可访问，共享库继续使用既有角色上限。"""

    if is_personal_knowledge_base(resource):
        owner = str(_value(resource, "created_by", "") or "")
        if not owner or owner != str(_value(user, "uid", "") or ""):
            return ResourcePermission.NONE
        from yuxi.permissions.business_roles import BusinessCapability, resolve_business_capabilities

        if _value(user, "role") in {"admin", "superadmin"} or (
            BusinessCapability.MANAGE_PERSONAL_KNOWLEDGE in resolve_business_capabilities(user)
        ):
            return ResourcePermission.MANAGE
        return ResourcePermission.NONE

    return resolve_resource_permission(
        user,
        resource,
        KNOWLEDGE_BASE_PERMISSION_POLICY,
    )


def is_personal_knowledge_base(resource: ShareableResource) -> bool:
    """用既有空共享范围识别仅所有者的个人知识库。"""

    config = _value(resource, "share_config")
    return (
        isinstance(config, dict)
        and config.get("version") == 2
        and config.get("read_scope") is None
        and config.get("manage_scope") is None
    )


def require_knowledge_base_permission(
    user: Any,
    resource: ShareableResource,
    required: ResourcePermission,
) -> ResourcePermission:
    """校验用户是否具备知识库所需权限，并返回实际权限。"""

    actual = resolve_knowledge_base_permission(user, resource)
    require_resource_permission(actual, required)
    return actual


def resolve_agent_permission(user: Any, resource: ShareableResource) -> ResourcePermission:
    """解析 Agent 权限。"""

    return resolve_resource_permission(
        user,
        resource,
        AGENT_PERMISSION_POLICY,
    )


def resolve_skill_permission(user: Any, resource: ShareableResource) -> ResourcePermission:
    """解析 Skill 权限。"""

    if _value(resource, "source_scope") == "personal":
        if str(_value(resource, "created_by", "") or "") == str(_value(user, "uid", "") or ""):
            return ResourcePermission.MANAGE
        return ResourcePermission.NONE

    return resolve_resource_permission(
        user,
        resource,
        SKILL_PERMISSION_POLICY,
    )
