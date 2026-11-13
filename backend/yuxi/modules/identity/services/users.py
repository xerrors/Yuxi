"""用户管理、资料与头像用例，事务在服务入口收敛。"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.identity.models import User
from yuxi.modules.identity.repositories.departments import DepartmentRepository
from yuxi.modules.identity.repositories.users import UserRepository
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.identity.services.usernames import generate_unique_uid, is_valid_phone_number, validate_username
from yuxi.modules.schedules.repository import delete_user_schedules
from yuxi.shared.errors import InvalidInputError, InvalidOperationError, PermissionDeniedError, ResourceNotFoundError, StateConflictError


async def update_profile(db: AsyncSession, *, current_user: User, username: str | None = None, phone_number: str | None = None) -> dict:
    """更新当前用户的个人资料"""
    try:
        user_repository = UserRepository(db)

        if username is not None:
            is_valid, error_msg = validate_username(username)
            if not is_valid:
                raise InvalidOperationError(error_msg)

            existing_user = await user_repository.get_by_username(username, exclude_user_id=current_user.id)
            if existing_user:
                raise InvalidOperationError("用户名已存在")

            current_user.username = username

        if phone_number is not None:
            if phone_number and not is_valid_phone_number(phone_number):
                raise InvalidOperationError("手机号格式不正确")

            if phone_number:
                existing_phone = await user_repository.get_by_phone_excluding(phone_number, current_user.id)
                if existing_phone:
                    raise InvalidOperationError("手机号已被其他用户使用")

            current_user.phone_number = phone_number

        await user_repository.save(current_user)

        await db.commit()

        return current_user.to_dict()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidOperationError("用户ID、用户名、手机号或部门与现有约束冲突") from exc
    except BaseException:
        await db.rollback()
        raise


async def change_password(db: AsyncSession, *, current_user: User, current_password: str, new_password: str) -> None:
    """锁定当前本地账号，验证原密码后提交新密码。"""
    try:
        repository = UserRepository(db)
        user = await repository.get_active_by_id(current_user.id, for_update=True)
        if user is None or user.user_kind != "human":
            raise PermissionDeniedError("仅有效本地账号可以修改密码", code="password_change_not_allowed")
        if user.uid.startswith("oidc:"):
            raise PermissionDeniedError("企业账号请在统一身份服务中修改密码", code="enterprise_password_managed")
        if not AuthUtils.verify_password(user.password_hash, current_password):
            raise InvalidInputError("原密码不正确", code="incorrect_current_password")

        user.password_hash = AuthUtils.hash_password(new_password)
        await repository.save(user)
        await db.commit()
    except BaseException:
        await db.rollback()
        raise


async def create_user(
    db: AsyncSession,
    *,
    current_user: User,
    username: str,
    password: str,
    role: str = "user",
    phone_number: str | None = None,
    department_id: int | None = None,
) -> dict:
    """创建新用户（管理员权限）"""
    try:
        current_user, _ = await _lock_member_management(db, actor_id=current_user.id)
        user_repo = UserRepository(db)

        is_valid, error_msg = validate_username(username)
        if not is_valid:
            raise InvalidOperationError(error_msg)

        if await user_repo.get_by_username(username) is not None:
            raise InvalidOperationError("用户名已存在")

        if phone_number:
            if await user_repo.exists_by_phone(phone_number):
                raise InvalidOperationError("手机号已存在")

        existing_uids = await user_repo.get_all_uids()
        uid = generate_unique_uid(username, existing_uids)

        hashed_password = AuthUtils.hash_password(password)

        if current_user.role == "admin" and role != "user":
            raise PermissionDeniedError("管理员只能创建普通用户账户")

        if current_user.role == "superadmin":
            if department_id is None:
                dept_repo = DepartmentRepository(db)
                departments = await dept_repo.list_departments()
                default_dept = next((d for d in departments if d.name == "默认部门"), None)
                department_id = default_dept.id if default_dept else None
        else:
            if current_user.department_id is None:
                raise InvalidOperationError("管理员必须属于部门才能创建用户")
            if department_id is not None:
                raise PermissionDeniedError("普通管理员不能指定部门")
            department_id = current_user.department_id

        new_user = await user_repo.create(
            {
                "username": username,
                "uid": uid,
                "phone_number": phone_number,
                "password_hash": hashed_password,
                "role": role,
                "department_id": department_id,
            }
        )

        await db.commit()

        return new_user.to_dict()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidOperationError("用户ID、用户名、手机号或部门与现有约束冲突") from exc
    except BaseException:
        await db.rollback()
        raise


async def update_user(
    db: AsyncSession,
    *,
    current_user: User,
    user_id: int,
    role: str | None = None,
    username: str | None = None,
    password: str | None = None,
    phone_number: str | None = None,
    avatar: str | None = None,
    department_id: int | None = None,
) -> dict:
    """完成用户写入并提交，失败时回滚整个用例。"""
    try:
        user_repository = UserRepository(db)
        current_user, user = await _lock_member_management(db, actor_id=current_user.id, member_id=user_id)

        ensure_user_in_current_department(current_user, user)

        if user.role == "superadmin" and current_user.role != "superadmin":
            raise PermissionDeniedError("只有超级管理员才能修改超级管理员账户")

        if current_user.role == "admin" and user.role != "user":
            raise PermissionDeniedError("管理员只能修改普通用户账户")

        if current_user.role != "superadmin" and password is not None:
            raise PermissionDeniedError("普通管理员不能重置已有成员密码")
        if role is not None:
            if current_user.role != "superadmin":
                raise PermissionDeniedError("只有系统管理员可以提升角色")

            ranks = {"user": 0, "admin": 1, "superadmin": 2}
            if user.user_kind != "human":
                raise InvalidInputError("只能提升有效产品用户角色")
            if role not in ranks or ranks[role] < ranks[user.role]:
                raise InvalidInputError("角色只允许提升，禁止降级")
            user.role = role

        if username is not None:
            existing_user = await user_repository.get_by_username(username, exclude_user_id=user_id)
            if existing_user:
                raise InvalidOperationError("用户名已存在")
            user.username = username

        if password is not None:
            user.password_hash = AuthUtils.hash_password(password)

        if phone_number is not None:
            user.phone_number = phone_number

        if avatar is not None:
            user.avatar = avatar

        if department_id is not None and department_id != user.department_id:
            if current_user.role != "superadmin":
                raise PermissionDeniedError("只有超级管理员才能修改用户部门")

            user.department_id = department_id

        await user_repository.save(user)

        await db.commit()

        return user.to_dict()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidOperationError("用户ID、用户名、手机号或部门与现有约束冲突") from exc
    except BaseException:
        await db.rollback()
        raise


async def delete_user(db: AsyncSession, *, current_user: User, user_id: int) -> dict:
    """完成用户写入并提交，失败时回滚整个用例。"""
    try:
        user_repository = UserRepository(db)
        current_user, user = await _lock_member_management(db, actor_id=current_user.id, member_id=user_id)

        ensure_user_in_current_department(current_user, user)

        if current_user.role == "admin" and user.role != "user":
            raise PermissionDeniedError("管理员只能删除普通用户账户")

        if user.id == current_user.id:
            raise InvalidOperationError("不能删除自己的账户")

        try:
            await user_repository.delete_for_admin(user)
            await delete_user_schedules(db, uid=user.uid)
        except ValueError as exc:
            raise StateConflictError(str(exc)) from exc

        await db.commit()

        return {"success": True, "message": "用户已删除"}
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidOperationError("用户ID、用户名、手机号或部门与现有约束冲突") from exc
    except BaseException:
        await db.rollback()
        raise


async def upload_avatar(db: AsyncSession, *, current_user: User, content: bytes) -> str:
    """上传头像后提交用户引用；对象上传和数据库保持各自失败边界。"""
    from yuxi.infrastructure.minio import upload_image_to_minio

    try:
        avatar_url = await upload_image_to_minio(
            content,
            object_prefix=f"avatar/{current_user.id}",
            max_size_bytes=5 * 1024 * 1024,
            too_large_message="文件大小不能超过5MB",
        )
        current_user.avatar = avatar_url
        await UserRepository(db).save(current_user)
        await db.commit()
        return avatar_url
    except BaseException:
        await db.rollback()
        raise


async def is_first_run() -> bool:
    """系统是否尚未创建任何用户。"""
    return await UserRepository().is_first_run()


async def get_department_name(db: AsyncSession, *, current_user: User) -> str | None:
    """读取当前用户部门的展示名称。"""
    if not current_user.department_id:
        return None
    return await DepartmentRepository(db).get_name_by_id(current_user.department_id)


async def list_users(db: AsyncSession, *, current_user: User, skip: int, limit: int) -> list[dict]:
    """按管理员可见范围返回用户与部门名称。"""
    department_id = None if current_user.role == "superadmin" else current_user.department_id
    users_with_department = await UserRepository(db).list_with_department(
        skip=skip,
        limit=limit,
        department_id=department_id,
    )
    result = []
    for user, department_name in users_with_department:
        item = user.to_dict()
        item["department_name"] = department_name
        result.append(item)
    return result


async def list_access_options(db: AsyncSession, *, skip: int, limit: int) -> list[dict]:
    """返回授权选择器使用的精简用户列表。"""
    users_with_department = await UserRepository(db).list_with_department(skip=skip, limit=limit)
    return [
        {
            "uid": user.uid,
            "username": user.username,
            "role": user.role,
            "department_id": user.department_id,
            "department_name": department_name,
        }
        for user, department_name in users_with_department
    ]


async def get_managed_user(db: AsyncSession, *, current_user: User, user_id: int) -> User | None:
    """读取管理员可见的有效用户并执行部门隔离。"""
    user = await UserRepository(db).get_active_by_id(user_id)
    if user is not None:
        ensure_user_in_current_department(current_user, user)
    return user


async def suggest_username_uid(db: AsyncSession, *, username: str) -> str | None:
    """用户名可用时生成唯一 UID，已存在时返回 None。"""
    repository = UserRepository(db)
    if await repository.get_by_username(username) is not None:
        return None
    return generate_unique_uid(username, await repository.get_all_uids())


async def is_uid_available(db: AsyncSession, *, uid: str) -> bool:
    """检查 UID 是否未被占用。"""
    return not await UserRepository(db).exists_by_uid(uid)


def ensure_user_in_current_department(current_user: User, target_user: User) -> None:
    """限制普通管理员的成员可见范围。"""
    if current_user.role == "superadmin":
        return
    if target_user.department_id != current_user.department_id:
        raise PermissionDeniedError("只能管理本部门用户")


async def _lock_member_management(db: AsyncSession, *, actor_id: int, member_id: int | None = None) -> tuple[User, User | None]:
    """按共同锁序取得目标和操作人的当前身份，持有到写入事务结束。"""
    repo = UserRepository(db)
    await repo.lock_identity_changes()
    member = await repo.get_active_by_id(member_id, for_update=True) if member_id is not None else None
    if member_id is not None and member is None:
        raise ResourceNotFoundError("用户不存在")
    actor = await repo.get_active_by_id(actor_id, for_update=True)
    if actor is None or actor.role not in {"admin", "superadmin"} or actor.user_kind != "human":
        raise PermissionDeniedError("当前管理员身份已失效")
    return actor, member
