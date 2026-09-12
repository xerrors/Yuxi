"""学生档案最小 HTTP 入口。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_required_user
from yuxi.services.counseling import create_student, get_student, list_counselor_options, list_students, update_student
from yuxi.storage.postgres.models_business import User

counseling = APIRouter(prefix="/counseling/students", tags=["counseling"])


class StudentCreate(BaseModel):
    """创建档案时只提交部门内部编号和负责人。"""

    model_config = ConfigDict(extra="forbid")
    student_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    counselor_id: int = Field(gt=0)


class StudentUpdate(BaseModel):
    """负责人维护当前背景和状态。"""

    model_config = ConfigDict(extra="forbid")
    background_summary: str = Field(max_length=10000)
    status: Literal["active", "closed"]


def _raise_counseling_error(exc: Exception) -> None:
    """把业务边界错误映射为稳定 HTTP 状态。"""
    if isinstance(exc, PermissionError):
        code = 403
    elif isinstance(exc, LookupError):
        code = 404
    elif isinstance(exc, FileExistsError):
        code = 409
    else:
        code = 422
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@counseling.post("", status_code=status.HTTP_201_CREATED)
async def create_student_route(
    payload: StudentCreate, actor: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    """业务管理员在本部门指定初始负责人。"""
    try:
        return await create_student(db, actor, payload.student_code, payload.counselor_id)
    except (PermissionError, ValueError, FileExistsError) as exc:
        _raise_counseling_error(exc)


@counseling.get("")
async def list_students_route(actor: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """按角色列出可见档案的最小元数据。"""
    try:
        return await list_students(db, actor)
    except PermissionError as exc:
        _raise_counseling_error(exc)


@counseling.get("/counselors")
async def list_counselors_route(actor: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """获取当前部门可选的初始负责人。"""
    try:
        return await list_counselor_options(db, actor)
    except PermissionError as exc:
        _raise_counseling_error(exc)


@counseling.get("/{student_id}")
async def get_student_route(
    student_id: int, actor: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    """只向负责人返回背景。"""
    try:
        return await get_student(db, actor, student_id)
    except (PermissionError, LookupError) as exc:
        _raise_counseling_error(exc)


@counseling.put("/{student_id}")
async def update_student_route(
    student_id: int,
    payload: StudentUpdate,
    actor: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """只允许负责人修改当前背景和状态。"""
    try:
        return await update_student(db, actor, student_id, payload.background_summary, payload.status)
    except (PermissionError, LookupError) as exc:
        _raise_counseling_error(exc)
