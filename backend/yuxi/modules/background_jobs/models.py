"""业务 PostgreSQL 映射。"""

import math
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from yuxi.infrastructure.postgres.base import BusinessBase as Base
from yuxi.shared.datetime import format_utc_datetime, utc_now


class BackgroundJobRecord(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'running', 'success', 'failed', 'cancelled')", name="ck_background_jobs_status"),
        UniqueConstraint("type", "dedupe_key", name="uq_background_jobs_active_dedupe"),
        Index("ix_background_jobs_status_lease_expires", "status", "lease_expires_at"),
    )

    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    progress = Column(Float, nullable=False, default=0.0)
    message = Column(Text, nullable=False, default="")
    payload = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    cancel_requested = Column(Integer, nullable=False, default=0)
    handler_version = Column(Integer, nullable=False, default=1)
    dedupe_key = Column(String(64), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    worker_id = Column(String(128), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    timeout_seconds = Column(Float, nullable=False, default=21600.0)
    created_at = Column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def to_observation_dict(self, *, include_result: bool = True) -> dict[str, Any]:
        """只序列化公开观察字段，结果详情按需生成。"""
        error = "任务已取消" if self.status == "cancelled" else "任务执行失败，请查看业务资源状态或日志"
        # public_error 由领域生产者显式脱敏，不能从 error 或 message 推断可公开内容。
        public_error = self.result.get("public_error") if isinstance(self.result, dict) else None
        if self.status == "failed" and isinstance(public_error, str) and public_error:
            error = public_error[:512]
        data = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "message": error if self.error else self.message,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
            "started_at": format_utc_datetime(self.started_at),
            "completed_at": format_utc_datetime(self.completed_at),
            "error": error if self.error else None,
            "cancel_requested": bool(self.cancel_requested),
        }
        if include_result:
            data["result"] = self._result_summary()
        return data

    def to_summary_dict(self) -> dict[str, Any]:
        """列表省略结果，不执行详情投影。"""
        return self.to_observation_dict(include_result=False)

    def _result_summary(self) -> dict[str, Any] | None:
        """对历史和新结果统一投影资源引用与计数，内部结果仍供领域 Hook 使用。"""
        if not isinstance(self.result, dict):
            return None
        result = self.result
        summary = {}
        for key in ("kb_id", "dataset_id", "run_id", "item_type", "outcome"):
            value = result.get(key)
            if isinstance(value, str):
                summary[key] = value[:128]
        for key in (
            "processed",
            "succeeded",
            "failed",
            "submitted",
            "item_count",
            "completed_items",
            "overall_score",
            "success",
            "extraction_failed",
            "write_failed",
            "remaining",
            "vector_failed",
        ):
            value = result.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value):
                summary[key] = value
        metrics = result.get("metrics")
        if isinstance(metrics, dict):
            summary["metrics"] = {
                str(key)[:64]: value
                for key, value in list(metrics.items())[:32]
                if isinstance(value, (int, float)) and math.isfinite(value)
            }
        items = result.get("items")
        if isinstance(items, list):
            references = []
            for item in items[:200]:
                if not isinstance(item, dict):
                    continue
                reference = {key: item[key][:128] for key in ("file_id", "status") if isinstance(item.get(key), str)}
                if item.get("error"):
                    public_error = item.get("public_error")
                    reference["error"] = (
                        public_error[:512] if isinstance(public_error, str) and public_error else "处理失败，详情请查看文件状态或日志"
                    )
                references.append(reference)
            summary["items"] = references
            summary["result_truncated"] = bool(result.get("result_truncated")) or len(items) > 200
        return summary
