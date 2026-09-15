"""回收站30天期限规则；状态、授权和并发领取由持久化用例负责。"""

from datetime import UTC, datetime, timedelta


def retention_deadline(deleted_at: datetime) -> datetime:
    """按绝对经过时间计算截止点，不把30天解释为下个月同一天。"""
    if deleted_at.utcoffset() is None:
        raise ValueError("deleted_at must include timezone")
    return deleted_at.astimezone(UTC) + timedelta(days=30)


def restore_window_open(deleted_at: datetime, *, now: datetime) -> bool:
    """只判断时间窗口；截止点已到期，未来删除时间也不开放恢复。"""
    if now.utcoffset() is None:
        raise ValueError("now must include timezone")
    deadline = retention_deadline(deleted_at)
    return deleted_at.astimezone(UTC) <= now.astimezone(UTC) < deadline
