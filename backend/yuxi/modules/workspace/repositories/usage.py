"""个人用量快照的 PostgreSQL 查询。"""

from datetime import datetime

from sqlalchemy import String, any_, bindparam, select
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.identity.models import User
from yuxi.modules.workspace.models import WorkspaceUsage


async def usage_attempts(db: AsyncSession, uids: list[str]) -> dict[str, datetime | None]:
    """筛出存续的标记用户及尝试时间，便于优先处理最久未扫描的项。"""
    candidates = bindparam("uids", uids, type_=ARRAY(String))
    query = (
        select(User.uid, WorkspaceUsage.last_attempt_at)
        .outerjoin(WorkspaceUsage, WorkspaceUsage.uid == User.uid)
        .where(User.uid == any_(candidates), User.is_deleted == 0)
    )
    return dict((await db.execute(query)).all())


async def get_usage(db: AsyncSession, uid: str) -> WorkspaceUsage | None:
    """读取最后一次完整扫描及最近一次尝试。"""
    return await db.get(WorkspaceUsage, uid)


async def save_usage(db: AsyncSession, uid: str, used_bytes: int | None, started_at: datetime) -> None:
    """巡检锁串行写入观测，失败只更新尝试时间。"""
    values = {"last_attempt_at": started_at}
    if used_bytes is not None:
        values.update(used_bytes=used_bytes, measured_at=started_at)
    statement = insert(WorkspaceUsage).values(uid=uid, **values)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[WorkspaceUsage.uid],
            set_=values,
        )
    )
