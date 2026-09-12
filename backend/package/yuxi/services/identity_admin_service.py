"""组织与初始身份管理用例的事务 Owner。"""

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.permissions import BusinessRole
from yuxi.repositories.department_repository import DepartmentRepository
from yuxi.repositories.user_repository import UserRepository
from yuxi.services.operation_log_service import log_operation
from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import utc_now_naive

_INITIALIZATION_LOCK_KEY = 0x59555849


class IdentityConflictError(Exception):
    """唯一身份或部门事实在提交时发生冲突。"""


class SystemAlreadyInitializedError(Exception):
    """系统初始化已由当前或并发请求完成。"""


@dataclass(frozen=True)
class DepartmentAdminCreation:
    """同一事务创建的部门和管理员。"""

    department: Department
    admin: User


async def list_managed_users_page(
    db: AsyncSession,
    *,
    offset: int,
    limit: int,
    is_superadmin: bool,
    visible_department_id: int | None,
    department_id: int | None,
    role: str | None,
    search: str | None,
) -> dict:
    """返回管理员可见范围内的用户分页。"""
    effective_department_id = department_id if is_superadmin else visible_department_id
    if not is_superadmin and effective_department_id is None:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}
    rows, total = await UserRepository(db).list_page_with_department(
        offset=offset,
        limit=limit,
        department_id=effective_department_id,
        role=role,
        search=search,
    )
    items = []
    for user, department_name in rows:
        item = user.to_dict()
        item["department_name"] = department_name
        items.append(item)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def create_department_with_admin(
    db: AsyncSession,
    *,
    name: str,
    description: str | None,
    admin_uid: str,
    admin_password: str,
    admin_phone: str | None,
    actor_user_id: int,
    request: Request | None = None,
) -> DepartmentAdminCreation:
    """原子创建部门、首位管理员和强制审计事实。"""

    password_hash = AuthUtils.hash_password(admin_password)
    try:
        try:
            department = await DepartmentRepository(db).create(
                {
                    "name": name,
                    "description": description,
                }
            )
            admin = await UserRepository(db).create(
                {
                    "username": admin_uid,
                    "uid": admin_uid,
                    "phone_number": admin_phone,
                    "password_hash": password_hash,
                    "role": "admin",
                    "business_roles": [BusinessRole.BUSINESS_ADMIN],
                    "department_id": department.id,
                }
            )
        except IntegrityError as exc:
            raise IdentityConflictError("部门名称、管理员用户ID、用户名或手机号已存在") from exc

        await log_operation(
            db,
            actor_user_id,
            "创建部门",
            f"创建部门: {name}，并创建管理员: {admin_uid}",
            request,
        )
        await db.commit()
        return DepartmentAdminCreation(department=department, admin=admin)
    except Exception:
        await db.rollback()
        raise


async def initialize_system_admin(
    db: AsyncSession,
    *,
    uid: str,
    password: str,
    phone_number: str | None,
) -> DepartmentAdminCreation:
    """串行、原子地创建默认部门、超级管理员和初始化审计。"""

    password_hash = AuthUtils.hash_password(password)
    try:
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": _INITIALIZATION_LOCK_KEY})

        if not await UserRepository(db).is_first_run():
            raise SystemAlreadyInitializedError("系统已经初始化，无法再次创建初始管理员")

        try:
            department = await DepartmentRepository(db).create(
                {
                    "name": "默认部门",
                    "description": "系统初始化时创建的默认部门",
                }
            )
            admin = await UserRepository(db).create(
                {
                    "username": uid,
                    "uid": uid,
                    "phone_number": phone_number,
                    "avatar": None,
                    "password_hash": password_hash,
                    "role": "superadmin",
                    "business_roles": [BusinessRole.TECHNICAL_ADMIN],
                    "department_id": department.id,
                    "last_login": utc_now_naive(),
                }
            )
        except IntegrityError as exc:
            raise IdentityConflictError("初始化身份事实与现有数据库约束冲突") from exc

        await log_operation(db, admin.id, "系统初始化", "创建超级管理员账户")
        await db.commit()
        return DepartmentAdminCreation(department=department, admin=admin)
    except SystemAlreadyInitializedError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
