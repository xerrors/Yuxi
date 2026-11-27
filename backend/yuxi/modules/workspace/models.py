"""业务 PostgreSQL 映射。"""

from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from yuxi.infrastructure.postgres.base import JSON_VALUE
from yuxi.infrastructure.postgres.base import BusinessBase as Base
from yuxi.shared.datetime import format_utc_datetime, utc_now

PROJECT_STATUS_CONSTRAINT_NAME = "ck_projects_status"
PROJECT_STATUS_CONSTRAINT_SQL = "status IN ('active', 'deleted')"


class Project(Base):
    """用户项目及其 Workdir 绑定。"""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("id", "uid", name="uq_projects_id_uid"),
        UniqueConstraint("uid", "idempotency_key", name="uq_projects_uid_idempotency_key"),
        CheckConstraint("selection_status IN ('implicit', 'selectable')", name="ck_projects_selection_status"),
        CheckConstraint("directory_mode IN ('managed', 'linked')", name="ck_projects_directory_mode"),
        CheckConstraint(PROJECT_STATUS_CONSTRAINT_SQL, name=PROJECT_STATUS_CONSTRAINT_NAME),
    )

    id = Column(String(64), primary_key=True, comment="Project UUID")
    uid = Column(
        String(128),
        ForeignKey("users.uid", ondelete="CASCADE", name="fk_projects_uid_users"),
        nullable=False,
        index=True,
        comment="UID",
    )
    name = Column(String(255), nullable=True, comment="项目名称；implicit Project 可为空")
    selection_status = Column(String(20), nullable=False, index=True, comment="implicit/selectable")
    workdir_path = Column(String(512), nullable=False, comment="UserWorkspace-relative Workdir path")
    directory_mode = Column(String(20), nullable=False, comment="managed/linked")
    status = Column(String(20), nullable=False, default="active", server_default="active", index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, comment="软删除时间")
    idempotency_key = Column(String(128), nullable=True, comment="幂等创建键")
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now(), nullable=False)

    sessions = relationship("Session", back_populates="project")

    def to_dict(self) -> dict[str, Any]:
        """序列化项目公开字段。"""
        return {
            "id": self.id,
            "uid": self.uid,
            "name": self.name,
            "selection_status": self.selection_status,
            "workdir_path": self.workdir_path,
            "directory_mode": self.directory_mode,
            "status": self.status,
            "deleted_at": format_utc_datetime(self.deleted_at),
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class WorkspaceRecentFiles(Base):
    """按用户保存个人空间最近成功打开的文件。"""

    __tablename__ = "workspace_recent_files"

    uid = Column(String(128), ForeignKey("users.uid", ondelete="CASCADE"), primary_key=True)
    entries = Column(JSON_VALUE, nullable=False)


class WorkspaceUsage(Base):
    """用户目录最近一次成功统计的物理用量。"""

    __tablename__ = "workspace_usage"
    __table_args__ = (CheckConstraint("used_bytes >= 0", name="ck_workspace_usage_nonnegative"),)

    uid = Column(String(128), ForeignKey("users.uid", ondelete="CASCADE"), primary_key=True)
    used_bytes = Column(BigInteger, nullable=True)
    measured_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=False)
