"""业务 PostgreSQL 映射。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from yuxi.infrastructure.postgres.base import JSON_VALUE, NormalizedJSON
from yuxi.infrastructure.postgres.base import BusinessBase as Base
from yuxi.shared.datetime import utc_now

MODEL_AUDIT_MESSAGE_TYPE = "model_audit"


TOOL_AUDIT_MESSAGE_TYPE = "tool_audit"


AUDIT_MESSAGE_TYPES = (MODEL_AUDIT_MESSAGE_TYPE, TOOL_AUDIT_MESSAGE_TYPE)


class Message(Base):
    """Message table - 消息表"""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "message_type != 'tool_audit' OR content = ''",
            name="ck_messages_tool_result_single_representation",
        ),
        CheckConstraint(
            "execution_status IS NULL OR execution_status IN ('running', 'completed', 'failed', 'interrupted', 'abandoned')",
            name="ck_messages_execution_status",
        ),
        Index(
            "uq_messages_run_role_operation_id",
            "run_id",
            "role",
            "operation_id",
            unique=True,
            postgresql_where=text("operation_id IS NOT NULL"),
            sqlite_where=text("operation_id IS NOT NULL"),
        ),
        UniqueConstraint("id", "run_id", "turn_id", "session_record_id", name="uq_messages_id_run_turn_thread"),
        UniqueConstraint("source_input_id", "input_position", name="uq_messages_input_position"),
        UniqueConstraint("id", "source_input_id", name="uq_messages_input_origin"),
        CheckConstraint(
            "(source_input_id IS NULL AND input_position IS NULL AND received_at IS NULL) OR "
            "(source_input_id IS NOT NULL AND input_position IS NOT NULL AND input_position >= 0 AND received_at IS NOT NULL "
            "AND run_id IS NOT NULL AND turn_id IS NOT NULL AND role = 'user')",
            name="ck_messages_input_origin",
        ),
        ForeignKeyConstraint(
            ["source_input_id", "run_id", "turn_id"],
            ["agent_inputs.id", "agent_inputs.consumed_run_id", "agent_inputs.turn_id"],
            name="fk_messages_consumed_input",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("(run_id IS NULL) = (turn_id IS NULL)", name="ck_messages_execution_scope"),
        ForeignKeyConstraint(
            ["run_id", "turn_id", "session_record_id"],
            ["agent_runs.id", "agent_runs.turn_id", "agent_runs.session_record_id"],
            name="fk_messages_run_turn_session",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "ix_messages_run_sequence",
            "run_id",
            "sequence",
            postgresql_where=text("sequence IS NOT NULL"),
            sqlite_where=text("sequence IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    session_record_id = Column(Integer, ForeignKey("sessions.id"), nullable=False, index=True, comment="Session ID")
    role = Column(String(20), nullable=False, comment="Message role: user/assistant/system/tool")
    content = Column(Text, nullable=False, comment="Message content")
    message_type = Column(String(30), default="text", comment="Message type: text/tool_call/tool_result")
    created_at = Column(DateTime(timezone=True), default=utc_now, comment="Creation time")
    source_input_id = Column(String(64), nullable=True, index=True)
    input_position = Column(Integer, nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)
    # 承载模型/工具 JSON 全文：必须经 NormalizedJSON 归一化 NUL，否则 \u0000 转义入库会让
    # items 分页等 json_each 读取路径对该消息永久失败。
    extra_metadata = Column(NormalizedJSON, nullable=True, comment="Additional metadata (complete message dump)")
    image_content = Column(Text, nullable=True, comment="Base64 encoded image content for multimodal messages")
    run_id = Column(String(64), nullable=True, index=True, comment="Agent run ID")
    turn_id = Column(String(64), nullable=True, index=True)
    delivery_status = Column(String(32), nullable=False, default="complete", comment="Message status")
    operation_id = Column(String(128), nullable=True, comment="同一 Run 内的 Model/Tool 稳定来源键")
    started_at = Column(DateTime(timezone=True), nullable=True, comment="Yuxi 观察到操作开始的 wall-clock 时间")
    finished_at = Column(DateTime(timezone=True), nullable=True, comment="Yuxi 观察到操作结束的 wall-clock 时间")
    duration_ms = Column(BigInteger, nullable=True, comment="本进程 monotonic clock 计算的操作耗时")
    sequence = Column(BigInteger, nullable=True, comment="LangGraph 根 StreamMux 事件顺序")
    execution_status = Column(String(32), nullable=True, comment="Model/Tool 执行状态")
    usage = Column(JSON_VALUE, nullable=True, comment="Provider 返回的单次可靠 usage")

    # Relationships
    agent_session = relationship("Session", back_populates="messages")
