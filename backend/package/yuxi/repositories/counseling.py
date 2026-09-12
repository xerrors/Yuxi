"""学生档案的归属查询与持久化边界。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_counseling import StudentRecord
from yuxi.utils.datetime_utils import utc_now_naive


class StudentRepository:
    """在查询条件中落实部门与负责人隔离。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def eligible_counselor(self, counselor_id: int, department_id: int) -> User | None:
        """锁定同部门且未删除的待分配用户。"""
        result = await self.db.execute(
            select(User)
            .where(User.id == counselor_id, User.department_id == department_id, User.is_deleted == 0)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def create(self, department_id: int, student_code: str, counselor_id: int) -> StudentRecord:
        """创建空背景的档案并等待调用方提交。"""
        record = StudentRecord(department_id=department_id, student_code=student_code, counselor_id=counselor_id)
        self.db.add(record)
        await self.db.flush()
        return record

    async def list_for_owner(self, department_id: int, counselor_id: int) -> list[StudentRecord]:
        """只读取当前负责人名下的档案。"""
        result = await self.db.execute(
            select(StudentRecord)
            .where(StudentRecord.department_id == department_id, StudentRecord.counselor_id == counselor_id)
            .order_by(StudentRecord.id)
        )
        return list(result.scalars().all())

    async def list_for_manager(self, department_id: int) -> list[StudentRecord]:
        """读取部门内用于分配的档案元数据。"""
        result = await self.db.execute(
            select(StudentRecord.id, StudentRecord.student_code, StudentRecord.counselor_id, StudentRecord.status)
            .where(StudentRecord.department_id == department_id)
            .order_by(StudentRecord.id)
        )
        return list(result.all())

    async def get_for_owner(self, student_id: int, department_id: int, counselor_id: int) -> StudentRecord | None:
        """只返回负责人能读取和修改的档案。"""
        result = await self.db.execute(
            select(StudentRecord).where(
                StudentRecord.id == student_id,
                StudentRecord.department_id == department_id,
                StudentRecord.counselor_id == counselor_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_for_owner(
        self, student_id: int, department_id: int, counselor_id: int, background_summary: str, status: str
    ) -> StudentRecord | None:
        """持锁更新负责人名下的背景和状态。"""
        result = await self.db.execute(
            select(StudentRecord)
            .where(
                StudentRecord.id == student_id,
                StudentRecord.department_id == department_id,
                StudentRecord.counselor_id == counselor_id,
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        record.background_summary = background_summary
        record.status = status
        record.updated_at = utc_now_naive()
        await self.db.flush()
        return record
