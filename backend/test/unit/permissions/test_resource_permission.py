from types import SimpleNamespace

import pytest

from yuxi.permissions import (
    ResourcePermission,
    ResourcePermissionDenied,
    require_knowledge_base_permission,
    resolve_agent_permission,
    resolve_knowledge_base_permission,
    resolve_skill_permission,
)


def _user(uid="user-1", role="user", department_id=1):
    return SimpleNamespace(uid=uid, role=role, department_id=department_id)


def _resource(created_by="owner", share_config=None):
    return SimpleNamespace(created_by=created_by, share_config=share_config)


def test_knowledge_base_global_read_and_department_manage():
    config = {
        "version": 2,
        "read_scope": {"access_level": "global"},
        "manage_scope": {"access_level": "department", "department_ids": [1]},
    }
    resource = _resource(share_config=config)

    assert resolve_knowledge_base_permission(_user(department_id=1), resource) == ResourcePermission.READ
    managing_admin = _user(uid="admin-1", role="admin", department_id=1)
    readonly_admin = _user(uid="other", role="admin", department_id=2)
    assert resolve_knowledge_base_permission(managing_admin, resource) == ResourcePermission.MANAGE
    assert resolve_knowledge_base_permission(readonly_admin, resource) == ResourcePermission.READ


def test_invalid_v2_scope_does_not_expand_read_access_when_reading():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [1]},
            "manage_scope": {"access_level": "global"},
        }
    )

    assert resolve_knowledge_base_permission(_user(role="admin", department_id=2), resource) == ResourcePermission.NONE


def test_strict_config_rejects_manage_scope_outside_read_scope():
    from yuxi.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="管理范围"):
        normalize_permission_config(
            {
                "version": 2,
                "read_scope": {"access_level": "department", "department_ids": [1]},
                "manage_scope": {"access_level": "global"},
            },
            strict=True,
        )


def test_strict_config_rejects_user_manage_scope_under_department_read_scope():
    from yuxi.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="管理范围"):
        normalize_permission_config(
            {
                "version": 2,
                "read_scope": {"access_level": "department", "department_ids": [1]},
                "manage_scope": {"access_level": "user", "user_uids": ["user-1"]},
            },
            strict=True,
        )


def test_global_agent_scope_preserves_admin_management():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "global"},
        }
    )

    assert resolve_agent_permission(_user(role="admin"), resource) == ResourcePermission.MANAGE


def test_user_agent_and_skill_scope_preserves_user_management():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["user-1"]},
            "manage_scope": {"access_level": "user", "user_uids": ["user-1"]},
        }
    )

    assert resolve_agent_permission(_user(), resource) == ResourcePermission.MANAGE
    assert resolve_skill_permission(_user(), resource) == ResourcePermission.MANAGE


def test_personal_knowledge_base_only_owner_can_manage():
    resource = _resource(created_by="owner", share_config={"version": 2})

    assert resolve_knowledge_base_permission(_user(uid="owner"), resource) == ResourcePermission.MANAGE
    assert resolve_knowledge_base_permission(_user(uid="owner", role="admin"), resource) == ResourcePermission.MANAGE
    assert resolve_knowledge_base_permission(_user(role="superadmin"), resource) == ResourcePermission.NONE
    assert resolve_knowledge_base_permission(_user(uid="other"), resource) == ResourcePermission.NONE


def test_personal_knowledge_base_requires_owner_business_capability():
    resource = _resource(created_by="owner", share_config={"version": 2, "read_scope": None, "manage_scope": None})
    owner = _user(uid="owner")
    owner.business_roles = []
    assert resolve_knowledge_base_permission(owner, resource) == ResourcePermission.NONE
    owner.business_roles = ["counselor"]
    assert resolve_knowledge_base_permission(owner, resource) == ResourcePermission.MANAGE


def test_personal_knowledge_base_without_owner_is_inaccessible():
    resource = _resource(created_by="", share_config={"version": 2})
    assert resolve_knowledge_base_permission(_user(uid=""), resource) == ResourcePermission.NONE


def test_global_knowledge_base_share_remains_manage_for_admin():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "global"},
        }
    )

    assert resolve_knowledge_base_permission(_user(role="admin"), resource) == ResourcePermission.MANAGE
    assert resolve_knowledge_base_permission(_user(role="user"), resource) == ResourcePermission.READ


def test_team_knowledge_counselor_owner_is_read_only():
    resource = _resource(
        created_by="counselor",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": {"access_level": "global"},
        },
    )
    counselor = _user(uid="counselor")
    counselor.business_roles = ["counselor"]

    assert resolve_knowledge_base_permission(counselor, resource) == ResourcePermission.READ


def test_team_knowledge_business_admin_manages_only_authorized_scope():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "department", "department_ids": [1]},
            "manage_scope": None,
        }
    )
    business_admin = _user(role="user")
    business_admin.business_roles = ["business_admin"]
    counselor = _user(role="user")
    counselor.business_roles = ["counselor"]
    no_capability = _user(role="user")
    no_capability.business_roles = []

    assert resolve_knowledge_base_permission(business_admin, resource) == ResourcePermission.MANAGE
    assert resolve_knowledge_base_permission(counselor, resource) == ResourcePermission.READ
    assert resolve_knowledge_base_permission(no_capability, resource) == ResourcePermission.NONE
    business_admin.department_id = 2
    assert resolve_knowledge_base_permission(business_admin, resource) == ResourcePermission.NONE
    counselor.department_id = 2
    assert resolve_knowledge_base_permission(counselor, resource) == ResourcePermission.NONE


def test_team_business_admin_cannot_read_another_personal_knowledge_base():
    resource = _resource(
        created_by="counselor",
        share_config={"version": 2, "read_scope": None, "manage_scope": None},
    )
    business_admin = _user(uid="business-admin", role="user")
    business_admin.business_roles = ["business_admin"]

    assert resolve_knowledge_base_permission(business_admin, resource) == ResourcePermission.NONE


def test_legacy_permission_config_is_rejected_at_runtime():
    from yuxi.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="version 2"):
        normalize_permission_config({"access_level": "department", "department_ids": [1]})


def test_agent_and_skill_use_shared_resolver_with_resource_policy():
    resource = _resource(share_config={"version": 2, "manage_scope": {"access_level": "user", "user_uids": ["user-2"]}})

    assert resolve_agent_permission(_user(uid="user-2"), resource) == ResourcePermission.MANAGE
    assert resolve_skill_permission(_user(uid="user-2"), resource) == ResourcePermission.MANAGE


def test_personal_skill_permission_is_limited_to_owner():
    resource = SimpleNamespace(source_scope="personal", created_by="user-1", share_config=None)

    assert resolve_skill_permission(_user(uid="user-1"), resource) == ResourcePermission.MANAGE
    assert resolve_skill_permission(_user(uid="user-2"), resource) == ResourcePermission.NONE


def test_manage_only_scope_also_grants_read_to_matching_users():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": None,
            "manage_scope": {"access_level": "department", "department_ids": [1]},
        }
    )

    assert (
        resolve_knowledge_base_permission(_user(role="admin", department_id=1), resource) == ResourcePermission.MANAGE
    )
    assert resolve_knowledge_base_permission(_user(department_id=1), resource) == ResourcePermission.READ
    assert resolve_knowledge_base_permission(_user(role="admin", department_id=2), resource) == ResourcePermission.NONE


def test_require_permission_rejects_insufficient_access():
    from yuxi.permissions import require_resource_permission

    with pytest.raises(ResourcePermissionDenied):
        require_resource_permission(ResourcePermission.READ, ResourcePermission.MANAGE)


def test_require_knowledge_base_permission_uses_resolved_resource_permission():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global"},
            "manage_scope": None,
        }
    )

    assert (
        require_knowledge_base_permission(_user(role="admin"), resource, ResourcePermission.READ)
        == ResourcePermission.READ
    )
    with pytest.raises(ResourcePermissionDenied):
        require_knowledge_base_permission(_user(role="admin"), resource, ResourcePermission.MANAGE)


def test_v2_scope_validation_rejects_disallowed_access_level():
    from yuxi.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="共享范围"):
        normalize_permission_config(
            {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "manage_scope": None,
            },
            allowed_access_levels={"user"},
        )
