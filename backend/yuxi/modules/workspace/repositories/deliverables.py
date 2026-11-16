"""个人成果的持久化与用户可见性查询。"""

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.workspace.models import Deliverable


class DeliverableRepository:
    """以用户和 APP 为边界读取已发布成果。"""

    def __init__(self, db: AsyncSession):
        """绑定调用方拥有事务的数据库会话。"""
        self.db = db

    async def get_for_user(self, deliverable_id: str, uid: str, *, app_id: str | None = None) -> Deliverable | None:
        """读取指定身份可见的成果，其他身份统一返回不存在。"""
        return await self.db.scalar(
            select(Deliverable).where(Deliverable.id == deliverable_id, Deliverable.uid == uid, Deliverable.app_id == app_id)
        )

    async def get_publication(self, run_id: str, operation_id: str, source_path: str) -> Deliverable | None:
        """按发布来源键读取同一工具调用已经保存的副本。"""
        return await self.db.scalar(
            select(Deliverable).where(
                Deliverable.run_id == run_id,
                Deliverable.operation_id == operation_id,
                Deliverable.source_path == source_path,
            )
        )

    async def add(self, deliverable: Deliverable) -> Deliverable:
        """在当前事务登记已经落盘的成果，不自行提交。"""
        self.db.add(deliverable)
        await self.db.flush()
        return deliverable

    async def list_for_user(
        self, *, uid: str, query: str, file_type: str, page: int, page_size: int, app_id: str | None = None
    ) -> tuple[list[tuple[Deliverable, str | None]], int]:
        """按文件名与类型分页查询当前用户成果及来源对话标题。"""
        predicates = [Deliverable.uid == uid, Deliverable.app_id == app_id]
        if query:
            predicates.append(Deliverable.name.icontains(query, autoescape=True))
        categories = {
            "image": Deliverable.mime_type.startswith("image/"),
            "video": Deliverable.mime_type.startswith("video/"),
            "audio": Deliverable.mime_type.startswith("audio/"),
            "document": or_(
                Deliverable.mime_type.startswith("text/"),
                Deliverable.mime_type.in_(
                    [
                        "application/pdf",
                        "application/msword",
                        "application/vnd.ms-excel",
                        "application/vnd.ms-powerpoint",
                        "application/rtf",
                    ]
                ),
                Deliverable.mime_type.startswith("application/vnd.openxmlformats-officedocument."),
                Deliverable.mime_type.startswith("application/vnd.oasis.opendocument."),
            ),
        }
        if file_type == "other":
            predicates.append(not_(or_(*categories.values())))
        elif file_type != "all":
            predicates.append(categories[file_type])
        total = await self.db.scalar(select(func.count()).select_from(Deliverable).where(*predicates))
        rows = await self.db.execute(
            select(Deliverable, Session.title)
            .outerjoin(
                Session,
                and_(Session.thread_id == Deliverable.thread_id, Session.uid == uid, Session.app_id == app_id),
            )
            .where(*predicates)
            .order_by(Deliverable.created_at.desc(), Deliverable.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows.all()), int(total or 0)
