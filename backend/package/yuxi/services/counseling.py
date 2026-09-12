"""最小学生档案用例与对外字段。"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.permissions.business_roles import BusinessCapability, resolve_business_capabilities
from yuxi.repositories.counseling import StudentRepository
from yuxi.storage.postgres.models_business import User


def _metadata(record) -> dict:
    return {
        "id": record.id,
        "student_code": record.student_code,
        "counselor_id": record.counselor_id,
        "status": record.status,
    }


def _details(record) -> dict:
    return {**_metadata(record), "background_summary": record.background_summary}


async def create_student(db: AsyncSession, actor: User, student_code: str, counselor_id: int) -> dict:
    """由同部门业务管理员为辅导人员创建空档案。"""
    if BusinessCapability.ASSIGN_STUDENTS not in resolve_business_capabilities(actor):
        raise PermissionError("需要学生分配权限")
    repository = StudentRepository(db)
    counselor = await repository.eligible_counselor(counselor_id, actor.department_id)
    if counselor is None or BusinessCapability.MANAGE_ASSIGNED_STUDENTS not in resolve_business_capabilities(counselor):
        raise ValueError("负责人必须是本部门在职辅导人员")
    try:
        record = await repository.create(actor.department_id, student_code, counselor_id)
        result = _metadata(record)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise FileExistsError("本部门学生编号已存在") from exc
    return result


async def list_students(db: AsyncSession, actor: User) -> list[dict]:
    """按角色读取本部门分配元数据或本人档案列表。"""
    capabilities = resolve_business_capabilities(actor)
    repository = StudentRepository(db)
    if BusinessCapability.ASSIGN_STUDENTS in capabilities:
        records = await repository.list_for_manager(actor.department_id)
    elif BusinessCapability.MANAGE_ASSIGNED_STUDENTS in capabilities:
        records = await repository.list_for_owner(actor.department_id, actor.id)
    else:
        raise PermissionError("需要学生档案权限")
    return [_metadata(record) for record in records]


async def get_student(db: AsyncSession, actor: User, student_id: int) -> dict:
    """仅负责人读取学生背景。"""
    if BusinessCapability.MANAGE_ASSIGNED_STUDENTS not in resolve_business_capabilities(actor):
        raise PermissionError("需要负责学生权限")
    record = await StudentRepository(db).get_for_owner(student_id, actor.department_id, actor.id)
    if record is None:
        raise LookupError("学生档案不存在")
    return _details(record)


async def update_student(db: AsyncSession, actor: User, student_id: int, background_summary: str, status: str) -> dict:
    """仅负责人更新学生背景与状态。"""
    if BusinessCapability.MANAGE_ASSIGNED_STUDENTS not in resolve_business_capabilities(actor):
        raise PermissionError("需要负责学生权限")
    record = await StudentRepository(db).update_for_owner(
        student_id, actor.department_id, actor.id, background_summary, status
    )
    if record is None:
        raise LookupError("学生档案不存在")
    result = _details(record)
    await db.commit()
    return result
