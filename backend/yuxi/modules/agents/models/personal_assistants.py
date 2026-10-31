"""个人助理与其用户、项目及主会话的持久绑定。"""

from sqlalchemy import Column, ForeignKey, ForeignKeyConstraint, Integer, String

from yuxi.infrastructure.postgres.base import BusinessBase as Base


class PersonalAssistant(Base):
    """一个用户最多拥有一个个人助理。"""

    __tablename__ = "personal_assistants"

    uid = Column(String(128), ForeignKey("users.uid", ondelete="CASCADE"), primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False, unique=True)
    project_id = Column(String(64), nullable=False, unique=True)
    main_thread_id = Column(String(64), nullable=False, unique=True)

    __table_args__ = (
        ForeignKeyConstraint(["project_id", "uid"], ["projects.id", "projects.uid"], name="fk_personal_project_owner"),
        ForeignKeyConstraint(
            ["main_thread_id", "uid", "project_id"],
            ["sessions.thread_id", "sessions.uid", "sessions.project_id"],
            name="fk_personal_session_owner",
        ),
    )
