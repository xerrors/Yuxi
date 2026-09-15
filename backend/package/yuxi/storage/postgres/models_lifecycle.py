"""个人文件回收操作的持久化状态。"""

from sqlalchemy import CheckConstraint, Column, DateTime, Index, String, Text
from yuxi.storage.postgres.models_business import JSON_VALUE, Base
from yuxi.utils.datetime_utils import utc_now_naive


class PersonalTrashEntry(Base):
    """记录原文件移动操作；附件内容和归属仍由Conversation拥有。"""

    __tablename__ = "personal_trash_entries"
    id = Column(String(64), primary_key=True)
    uid = Column(String(64), nullable=False)
    name = Column(Text, nullable=False)
    kind = Column(String(20), nullable=False)
    paths = Column(JSON_VALUE, nullable=False)
    state = Column(String(20), nullable=False)
    deleted_at = Column(DateTime, nullable=False, default=utc_now_naive)
    purge_after = Column(DateTime, nullable=False)
    error = Column(Text, nullable=True)
    retry_after = Column(DateTime, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending_delete','trashed','restoring','purging','purged','restored')",
            name="ck_personal_trash_state",
        ),
        CheckConstraint("kind IN ('workspace','attachment')", name="ck_personal_trash_kind"),
        Index("ix_personal_trash_owner_state", "uid", "state"),
        Index("ix_personal_trash_due", "state", "purge_after"),
    )
