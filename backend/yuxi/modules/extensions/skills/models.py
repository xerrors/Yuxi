"""业务 PostgreSQL 映射。"""

from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from yuxi.infrastructure.postgres.base import JSON_VALUE
from yuxi.infrastructure.postgres.base import BusinessBase as Base
from yuxi.shared.datetime import format_utc_datetime, utc_now


class Skill(Base):
    """Skill 元数据模型（内容存文件系统，索引存数据库）"""

    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(128), nullable=False, unique=True, index=True, comment="技能唯一标识（目录名）")
    bound_agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, unique=True)
    bound_agent = relationship("Agent", lazy="selectin")
    name = Column(String(128), nullable=False, comment="技能名称（来自 SKILL.md frontmatter.name）")
    description = Column(Text, nullable=False, comment="技能描述（来自 SKILL.md frontmatter.description）")
    source_type = Column(String(32), nullable=False, default="upload", index=True, comment="来源: builtin/upload/remote")
    dir_path = Column(String(512), nullable=False, comment="共享技能目录路径（相对 Skill 数据根目录）")
    version = Column(String(64), nullable=True, comment="技能版本（内置 skill 使用语义化版本）")
    content_hash = Column(String(128), nullable=True, comment="技能当前完整目录的统一 SHA256 内容摘要")
    share_config = Column(JSON_VALUE, nullable=False, comment="共享权限配置")
    enabled = Column(Boolean, nullable=False, default=True, comment="是否启用")
    created_by = Column(String(128), nullable=True)
    updated_by = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bound_agent_id": self.bound_agent_id,
            "source_scope": "agent_bound" if self.bound_agent_id is not None else self.source_type,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "dir_path": self.dir_path,
            "version": self.version,
            "content_hash": self.content_hash,
            "share_config": self.share_config or {},
            "enabled": bool(self.enabled),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class SkillVersion(Base):
    """专属 Skill 的只读元数据与完整文件快照索引。"""

    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(String(17), nullable=False)
    dir_path = Column(String(512), nullable=False)
    metadata_json = Column(JSON_VALUE, nullable=False)
    content_hash = Column(String(128), nullable=False)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        """返回历史版本信息，不暴露持久目录。"""
        return {
            "version": self.version,
            "name": self.metadata_json["name"],
            "description": self.metadata_json["description"],
            "content_hash": self.content_hash,
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
        }
