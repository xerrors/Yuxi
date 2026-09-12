"""学生档案的最小 PostgreSQL 模型。"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from yuxi.storage.postgres.models_business import Base


class StudentRecord(Base):
    """以部门编号和负责人限定的当前学生档案。"""

    __tablename__ = "counseling_students"
    __table_args__ = (
        UniqueConstraint("department_id", "student_code", name="uq_counseling_students_department_code"),
        CheckConstraint("status IN ('active', 'closed')", name="ck_counseling_students_status"),
        Index("ix_counseling_students_owner", "department_id", "counselor_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=False)
    student_code = Column(String(64), nullable=False)
    counselor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    background_summary = Column(Text, nullable=False, default="", server_default="")
    status = Column(String(16), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
