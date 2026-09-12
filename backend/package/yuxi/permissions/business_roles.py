"""知伴业务角色及其能力映射。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any


class BusinessRole(StrEnum):
    """第一版固定业务角色。"""

    COUNSELOR = "counselor"
    BUSINESS_ADMIN = "business_admin"
    TECHNICAL_ADMIN = "technical_admin"


class BusinessCapability(StrEnum):
    """后续业务入口使用的最小授权能力。"""

    MANAGE_ASSIGNED_STUDENTS = "students.manage_assigned"
    ASSIGN_STUDENTS = "students.assign"
    MANAGE_PERSONAL_KNOWLEDGE = "knowledge.personal.manage"
    READ_AUTHORIZED_TEAM_KNOWLEDGE = "knowledge.team.read_authorized"
    MANAGE_TEAM_KNOWLEDGE = "knowledge.team.manage"
    MANAGE_SYSTEM = "system.manage"


BUSINESS_ROLE_ORDER = (
    BusinessRole.COUNSELOR,
    BusinessRole.BUSINESS_ADMIN,
    BusinessRole.TECHNICAL_ADMIN,
)

BUSINESS_ROLE_CAPABILITIES = {
    BusinessRole.COUNSELOR: frozenset(
        {
            BusinessCapability.MANAGE_ASSIGNED_STUDENTS,
            BusinessCapability.MANAGE_PERSONAL_KNOWLEDGE,
            BusinessCapability.READ_AUTHORIZED_TEAM_KNOWLEDGE,
        }
    ),
    BusinessRole.BUSINESS_ADMIN: frozenset(
        {
            BusinessCapability.ASSIGN_STUDENTS,
            BusinessCapability.MANAGE_TEAM_KNOWLEDGE,
        }
    ),
    BusinessRole.TECHNICAL_ADMIN: frozenset({BusinessCapability.MANAGE_SYSTEM}),
}

LEGACY_PLATFORM_ROLE_DEFAULTS = {
    "user": (BusinessRole.COUNSELOR,),
    "admin": (BusinessRole.BUSINESS_ADMIN,),
    "superadmin": (BusinessRole.TECHNICAL_ADMIN,),
}


def normalize_business_roles(values: Iterable[str | BusinessRole]) -> tuple[BusinessRole, ...]:
    """校验业务角色、去重并按固定顺序返回。"""

    roles = {BusinessRole(value) for value in values}
    return tuple(role for role in BUSINESS_ROLE_ORDER if role in roles)


def default_business_roles_for_platform_role(platform_role: str) -> tuple[BusinessRole, ...]:
    """为旧平台角色提供不扩大数据访问权的迁移默认值。"""

    return LEGACY_PLATFORM_ROLE_DEFAULTS.get(platform_role, ())


def resolve_business_roles(user: Any) -> tuple[BusinessRole, ...]:
    """读取持久业务角色；旧对象缺少字段时按平台角色兼容。"""

    if isinstance(user, Mapping):
        stored_roles = user.get("business_roles")
        platform_role = str(user.get("role", ""))
    else:
        stored_roles = getattr(user, "business_roles", None)
        platform_role = str(getattr(user, "role", ""))

    if stored_roles is None:
        return default_business_roles_for_platform_role(platform_role)
    return normalize_business_roles(stored_roles)


def resolve_business_capabilities(user: Any) -> frozenset[BusinessCapability]:
    """合并用户兼任角色的能力，不引入角色继承。"""

    capabilities: set[BusinessCapability] = set()
    for role in resolve_business_roles(user):
        capabilities.update(BUSINESS_ROLE_CAPABILITIES[role])
    return frozenset(capabilities)
