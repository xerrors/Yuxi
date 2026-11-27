"""用户最近打开的个人文件记录。"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.workspace.models import WorkspaceRecentFiles
from yuxi.shared.datetime import utc_isoformat


async def record_file_open(db: AsyncSession, uid: str, path: str) -> None:
    """串行更新同一用户的五个最近路径，重复打开移至首位。"""
    await db.execute(insert(WorkspaceRecentFiles).values(uid=uid, entries=[]).on_conflict_do_nothing(index_elements=["uid"]))
    history = (await db.scalars(select(WorkspaceRecentFiles).where(WorkspaceRecentFiles.uid == uid).with_for_update())).one()
    previous = [entry for entry in history.entries if entry["path"] != path]
    history.entries = [{"path": path, "opened_at": utc_isoformat()}, *previous[:4]]


async def recent_file_opens(db: AsyncSession, uid: str) -> list[dict]:
    """读取当前用户最后成功打开的路径和时间。"""
    return await db.scalar(select(WorkspaceRecentFiles.entries).where(WorkspaceRecentFiles.uid == uid)) or []
