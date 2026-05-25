"""LangGraph checkpoint 表反射（纯被动 schema 定义）

这些表由 LangGraph 的 AsyncPostgresSaver 自动创建和管理，
没有 SQLAlchemy ORM 模型。此处仅声明表结构，供
checkpoint_repository 和上层服务通过 expression language 构造查询。
"""

from sqlalchemy import LargeBinary, column, Integer, String, table
from sqlalchemy.dialects.postgresql import JSONB


checkpoints = table(
    "checkpoints",
    column("thread_id", String),
    column("checkpoint_ns", String),
    column("checkpoint_id", String),
    column("parent_checkpoint_id", String),
    column("type", String),
    column("checkpoint", JSONB),
    column("metadata", JSONB),
)

checkpoint_writes = table(
    "checkpoint_writes",
    column("thread_id", String),
    column("checkpoint_ns", String),
    column("checkpoint_id", String),
    column("task_id", String),
    column("idx", Integer),
    column("channel", String),
    column("type", String),
    column("blob", LargeBinary),
    column("task_path", String),
)

checkpoint_blobs = table(
    "checkpoint_blobs",
    column("thread_id", String),
    column("checkpoint_ns", String),
    column("channel", String),
    column("version", String),
    column("type", String),
    column("blob", LargeBinary),
)
