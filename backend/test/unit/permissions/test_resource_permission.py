from types import SimpleNamespace

import pytest

from yuxi.modules.identity.permissions import (
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
    from yuxi.modules.identity.permissions import normalize_permission_config

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
    from yuxi.modules.identity.permissions import normalize_permission_config

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


def test_user_agent_and_skill_scope_is_read_only():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": {"access_level": "user", "user_uids": ["user-1"]},
            "manage_scope": {"access_level": "user", "user_uids": ["user-1"]},
        }
    )

    assert resolve_agent_permission(_user(), resource) == ResourcePermission.READ
    assert resolve_skill_permission(_user(), resource) == ResourcePermission.READ


def test_knowledge_base_owner_and_superadmin_can_manage():
    resource = _resource(created_by="owner", share_config={"version": 2})

    assert resolve_knowledge_base_permission(_user(uid="owner"), resource) == ResourcePermission.NONE
    assert resolve_knowledge_base_permission(_user(uid="owner", role="admin"), resource) == ResourcePermission.MANAGE
    assert resolve_knowledge_base_permission(_user(role="superadmin"), resource) == ResourcePermission.MANAGE


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


def test_legacy_permission_config_is_rejected_at_runtime():
    from yuxi.modules.identity.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="version 2"):
        normalize_permission_config({"access_level": "department", "department_ids": [1]})


@pytest.mark.parametrize(
    "share_config",
    [
        None,
        {"access_level": "global"},
        {"version": 2, "read_scope": {"access_level": "department", "department_ids": [None]}},
        {"version": 2, "read_scope": {"access_level": "department", "department_ids": 1}},
        {"version": 2, "manage_scope": {"access_level": "user", "user_uids": 1}},
        {"version": 2, "read_scope": {"access_level": ["global"]}},
        *[{"version": 2, scope: {"access_level": value}} for scope in ("read_scope", "manage_scope") for value in ([], {}, False, 0)],
        *[
            {"version": 2, "read_scope": {"access_level": "department", "department_ids": value}}
            for value in ("1", {"1": True}, [True], [1.5], [float("inf")])
        ],
        *[
            {"version": 2, "manage_scope": {"access_level": "user", "user_uids": value}}
            for value in ("owner", {"owner": True}, [None], [True], [1])
        ],
    ],
)
def test_invalid_resource_permission_config_denies_access(share_config):
    """损坏的持久化共享配置不得授予权限或中断解析。"""
    resource = _resource(created_by="owner", share_config=share_config)

    for resolver in (resolve_knowledge_base_permission, resolve_agent_permission, resolve_skill_permission):
        assert resolver(_user(uid="owner"), resource) == ResourcePermission.NONE
        assert resolver(_user(uid="other"), resource) == ResourcePermission.NONE
    assert resolve_agent_permission(_user(role="superadmin"), resource) == ResourcePermission.MANAGE


@pytest.mark.parametrize("scope", [{}, {"access_level": None}, {"access_level": ""}])
def test_valid_default_scope_preserves_global_read(scope):
    """既有省略、空值和空字符串默认范围仍允许全局读取。"""
    resource = _resource(share_config={"version": 2, "read_scope": scope})
    assert resolve_agent_permission(_user(uid="other"), resource) == ResourcePermission.READ


def test_department_integer_strings_remain_valid():
    """合法的整数字符串部门成员仍按部门匹配。"""
    resource = _resource(share_config={"version": 2, "read_scope": {"access_level": "department", "department_ids": ["1", 1]}})
    assert resolve_agent_permission(_user(department_id=1), resource) == ResourcePermission.READ
    assert resolve_agent_permission(_user(department_id=2), resource) == ResourcePermission.NONE


def test_agent_and_skill_use_shared_resolver_with_resource_policy():
    resource = _resource(share_config={"version": 2, "manage_scope": {"access_level": "user", "user_uids": ["user-2"]}})

    assert resolve_agent_permission(_user(uid="user-2"), resource) == ResourcePermission.NONE
    assert resolve_skill_permission(_user(uid="user-2"), resource) == ResourcePermission.NONE


def test_personal_skill_permission_is_limited_to_owner():
    resource = SimpleNamespace(source_scope="personal", created_by="user-1", share_config=None)

    assert resolve_skill_permission(_user(uid="user-1"), resource) == ResourcePermission.MANAGE
    assert resolve_skill_permission(_user(uid="user-2"), resource) == ResourcePermission.NONE


def test_manage_only_scope_never_grants_access():
    resource = _resource(
        share_config={
            "version": 2,
            "read_scope": None,
            "manage_scope": {"access_level": "department", "department_ids": [1]},
        }
    )

    assert resolve_knowledge_base_permission(_user(role="admin", department_id=1), resource) == ResourcePermission.NONE
    assert resolve_knowledge_base_permission(_user(department_id=1), resource) == ResourcePermission.NONE
    assert resolve_knowledge_base_permission(_user(role="admin", department_id=2), resource) == ResourcePermission.NONE


def test_require_permission_rejects_insufficient_access():
    from yuxi.modules.identity.permissions import require_resource_permission

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

    assert require_knowledge_base_permission(_user(role="admin"), resource, ResourcePermission.READ) == ResourcePermission.READ
    with pytest.raises(ResourcePermissionDenied):
        require_knowledge_base_permission(_user(role="admin"), resource, ResourcePermission.MANAGE)


def test_v2_scope_validation_rejects_disallowed_access_level():
    from yuxi.modules.identity.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="共享范围"):
        normalize_permission_config(
            {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "manage_scope": None,
            },
            allowed_access_levels={"user"},
        )


@pytest.mark.parametrize("role", ["user", "admin", "superadmin"])
def test_private_agent_access_is_owner_or_system_governance(role):
    resource = SimpleNamespace(visibility="private", created_by="owner", share_config={"version": 2})
    assert resolve_agent_permission(_user(uid="owner", role=role), resource) == ResourcePermission.MANAGE
    expected = ResourcePermission.MANAGE if role == "superadmin" else ResourcePermission.NONE
    assert resolve_agent_permission(_user(uid="other", role=role), resource) == expected
    end_user = SimpleNamespace(uid="owner", role=role, user_kind="end_user")
    assert resolve_agent_permission(end_user, resource) == ResourcePermission.NONE


def test_empty_read_scope_rejects_nonempty_management():
    from yuxi.modules.identity.permissions import normalize_permission_config

    with pytest.raises(ValueError, match="管理范围"):
        normalize_permission_config({"version": 2, "read_scope": None, "manage_scope": {"access_level": "global"}}, strict=True)


@pytest.mark.parametrize(
    "role,deleted,expected",
    [("admin", 0, ResourcePermission.READ), ("superadmin", 0, ResourcePermission.MANAGE), ("superadmin", 1, ResourcePermission.NONE)],
)
def test_builtin_skill_permission_respects_role_ceiling_and_deleted_identity(role, deleted, expected):
    """内置管理权限遵循统一角色上限，已删除超级管理员不能绕过身份边界。"""
    from yuxi.modules.extensions.skills.access import user_can_manage_skill

    user = _user(role=role)
    user.is_deleted = deleted
    resource = _resource(
        created_by=user.uid,
        share_config={"version": 2, "read_scope": {"access_level": "global"}, "manage_scope": {"access_level": "global"}},
    )
    resource.source_type = "builtin"
    assert resolve_skill_permission(user, resource) == expected
    assert user_can_manage_skill(user, resource) == (expected == ResourcePermission.MANAGE)
