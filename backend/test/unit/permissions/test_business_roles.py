from types import SimpleNamespace

import pytest

from yuxi.permissions import (
    BusinessCapability,
    BusinessRole,
    normalize_business_roles,
    resolve_business_capabilities,
    resolve_business_roles,
)


def test_legacy_platform_roles_map_without_expanding_student_access():
    assert resolve_business_roles(SimpleNamespace(role="user")) == (BusinessRole.COUNSELOR,)
    assert resolve_business_roles(SimpleNamespace(role="admin")) == (BusinessRole.BUSINESS_ADMIN,)
    assert resolve_business_roles(SimpleNamespace(role="superadmin")) == (BusinessRole.TECHNICAL_ADMIN,)

    admin_capabilities = resolve_business_capabilities(SimpleNamespace(role="admin"))
    technical_capabilities = resolve_business_capabilities(SimpleNamespace(role="superadmin"))
    assert BusinessCapability.MANAGE_ASSIGNED_STUDENTS not in admin_capabilities
    assert BusinessCapability.MANAGE_ASSIGNED_STUDENTS not in technical_capabilities


def test_multiple_business_roles_merge_capabilities_without_role_inheritance():
    user = SimpleNamespace(
        role="user",
        business_roles=[BusinessRole.TECHNICAL_ADMIN, BusinessRole.COUNSELOR, BusinessRole.COUNSELOR],
    )

    assert resolve_business_roles(user) == (BusinessRole.COUNSELOR, BusinessRole.TECHNICAL_ADMIN)
    assert resolve_business_capabilities(user) == frozenset(
        {
            BusinessCapability.MANAGE_ASSIGNED_STUDENTS,
            BusinessCapability.MANAGE_PERSONAL_KNOWLEDGE,
            BusinessCapability.READ_AUTHORIZED_TEAM_KNOWLEDGE,
            BusinessCapability.MANAGE_SYSTEM,
        }
    )


def test_persisted_empty_roles_do_not_fall_back_to_legacy_role():
    user = SimpleNamespace(role="superadmin", business_roles=[])

    assert resolve_business_roles(user) == ()
    assert resolve_business_capabilities(user) == frozenset()


def test_unknown_business_role_fails_closed():
    with pytest.raises(ValueError, match="not a valid BusinessRole"):
        normalize_business_roles(["unknown"])
