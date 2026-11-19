"""业务 PostgreSQL 映射。"""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from yuxi.infrastructure.postgres.base import JSON_VALUE
from yuxi.infrastructure.postgres.base import BusinessBase as Base
from yuxi.shared.datetime import utc_now

UNVIEWED_RUN_MARKER = "__unviewed__"


class Session(Base):
    """Thread 的持久映射；id 是内部行主键，thread_id 是业务身份。"""

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    thread_id = Column(String(64), nullable=False, comment="Thread ID (UUID)")
    tree_root_thread_id = Column(
        String(64),
        ForeignKey("sessions.thread_id"),
        nullable=False,
        index=True,
        default=lambda context: context.get_current_parameters()["thread_id"],
    )
    parent_thread_id = Column(String(64), ForeignKey("sessions.thread_id"), nullable=True, index=True)
    cooperation_name = Column(String(64), nullable=False, default="root", server_default="root")
    cooperation_path = Column(String(1024), nullable=False, default="/root", server_default="/root")
    created_by_run_id = Column(String(64), ForeignKey("agent_runs.id", use_alter=True), nullable=True)
    config_snapshot = Column(JSON, nullable=True)
    creation_request_id = Column(String(64), nullable=True, comment="新建 Session 幂等请求 ID")
    uid = Column(String(128), index=True, nullable=False, comment="UID")
    app_id = Column(String(64), nullable=True, comment="Public API 可信 APP 归属")
    # 保存 Agent.slug 值；列名与契约字段 agent_id 一致。
    agent_id = Column(String(64), index=True, nullable=False, comment="Agent slug (agent_id)")
    title = Column(String(255), nullable=True, comment="Session title")
    status = Column(String(20), default="active", comment="Status: active/archived/deleted")
    queue_paused = Column(Boolean, nullable=False, default=False, server_default="false")
    is_pinned = Column(Boolean, default=False, nullable=False, index=True, comment="Is pinned to top")
    last_viewed_run_id = Column(String(64), nullable=True, comment="Latest top-level run id viewed by user")
    project_id = Column(String(64), nullable=False, index=True, comment="Session 绑定的 Project ID")
    created_at = Column(DateTime(timezone=True), default=utc_now, comment="Creation time")
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="Update time")
    extra_metadata = Column(JSON, nullable=True, comment="Additional metadata")
    agent_state_view = Column(
        JSON_VALUE,
        nullable=True,
        comment="Run 终态面板读模型投影：extract_agent_state 消费的小通道 + run_id 新鲜度标签 + 中断载荷",
    )

    # Relationships
    messages = relationship("Message", back_populates="agent_session", cascade="all, delete-orphan")
    project = relationship("Project", back_populates="sessions")

    __table_args__ = (
        UniqueConstraint("thread_id", name="uq_sessions_thread_id"),
        UniqueConstraint("id", "thread_id", "tree_root_thread_id", name="uq_sessions_runtime_binding"),
        UniqueConstraint("thread_id", "tree_root_thread_id", "uid", "project_id", name="uq_sessions_tree_owner"),
        UniqueConstraint("thread_id", "uid", "project_id", name="uq_sessions_workdir_owner"),
        ForeignKeyConstraint(
            ["tree_root_thread_id", "uid", "project_id"],
            ["sessions.thread_id", "sessions.uid", "sessions.project_id"],
            name="fk_sessions_tree_owner",
        ),
        ForeignKeyConstraint(
            ["parent_thread_id", "tree_root_thread_id", "uid", "project_id"],
            ["sessions.thread_id", "sessions.tree_root_thread_id", "sessions.uid", "sessions.project_id"],
            name="fk_sessions_parent_tree_owner",
        ),
        UniqueConstraint("tree_root_thread_id", "cooperation_path", name="uq_sessions_tree_path"),
        CheckConstraint(
            "(parent_thread_id IS NULL AND tree_root_thread_id = thread_id AND cooperation_path = '/root') "
            "OR (parent_thread_id IS NOT NULL AND parent_thread_id <> thread_id)",
            name="ck_sessions_tree_shape",
        ),
        ForeignKeyConstraint(
            ["project_id", "uid"],
            ["projects.id", "projects.uid"],
            name="fk_sessions_project_uid",
        ),
        UniqueConstraint("uid", "creation_request_id", name="uq_sessions_uid_creation_request_id"),
        UniqueConstraint("id", "thread_id", name="uq_sessions_id_thread"),
    )
