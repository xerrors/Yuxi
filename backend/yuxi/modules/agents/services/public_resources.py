"""HTTP 与实时事件共享的公开资源投影。"""

import re
from datetime import datetime

_INTERPRETER_DUMP = re.compile(r"<(?:[A-Za-z_][\w.]*)(?: [^>\n]*)? at 0x[0-9a-fA-F]+>|Traceback \(most recent call last\)")
PUBLIC_RUN_FAILURE = "执行未能完成，请稍后重试。"


def public_run_error_message(message: str | None) -> str | None:
    """公开执行错误保留有意文本；解释器对象地址和堆栈换成稳定说明。"""
    if not message or not _INTERPRETER_DUMP.search(message):
        return message
    return PUBLIC_RUN_FAILURE


def turn_core(turn, run, *, started_at: datetime | None, status: str | None = None) -> dict:
    """从明确 Turn 与 Run 生成公开核心，不掩盖取消清理。"""
    if status is None:
        status = turn_status(turn, run)
    public_error = public_run_error_message(run.error_message) if run else None
    return {
        "id": turn.id,
        "object": "agent.session.turn",
        "session_id": turn.thread_id,
        "agent_id": run.agent_slug if run else None,
        "subagent_id": None,
        "status": status,
        "created_at": turn.created_at.timestamp(),
        "started_at": started_at.timestamp() if started_at else None,
        "completed_at": turn.finished_at.timestamp() if turn.finished_at and status in {"completed", "failed", "cancelled"} else None,
        "error": {"code": "internal_error", "message": public_error or "执行失败"} if status == "failed" and run else None,
        "usage": None,
    }


def session_resource(session, turn, run, queued_count, last_active_at, unread, *, receipt=None) -> dict:
    """从批量读取的事实一次投影 Session，工作状态与未读独立。"""
    current = None
    if turn is not None:
        current = {
            "id": turn.id,
            "status": turn_status(turn, run),
            "current_run_id": turn.current_run_id,
            "result_run_id": turn.result_run_id,
            "waitpoint": turn.waitpoint,
        }
    status = current["status"] if current else "idle"
    if status == "queued" or (queued_count and not session.queue_paused and status in {"idle", "completed", "failed", "cancelled"}):
        status = "in_progress"
    config = session.config_snapshot
    if config is None:
        raise ValueError("Session 缺少配置快照")
    return {
        "id": session.thread_id,
        "object": "agent.session",
        "agent": {"id": session.agent_id, "model": config["model"]},
        "created_at": int(session.created_at.timestamp()),
        "last_active_at": int(last_active_at.timestamp()),
        "status": status,
        "yuxi": {
            "title": session.title,
            "project_id": session.project_id,
            "is_pinned": bool(session.is_pinned),
            "archived": session.status == "archived",
            "parent_session_id": session.parent_thread_id,
            "tool_approval_mode": config["tool_approval_mode"],
            "unread": unread,
            "current_turn": current,
            "queue_paused": bool(session.queue_paused),
            "queued_input_count": queued_count,
            "receipt": event_receipt(receipt) if receipt else None,
        },
    }


def event_receipt(result: dict) -> dict:
    """回执只表达已经提交的接收事实。"""
    return {
        "object": "yuxi.session.event.accepted",
        "event_id": result["event_id"],
        "session_id": result["thread_id"],
        "input_id": result.get("input_id"),
        "turn_id": result.get("turn_id"),
        "run_id": result.get("run_id"),
        "status": "accepted",
    }


def turn_status(turn, run) -> str:
    """协作等待是执行中；只有等待用户输入才要求调用方处理。"""
    if turn.status == "waiting":
        return "in_progress" if (turn.waitpoint or {}).get("kind") == "cooperation" else "requires_action"
    if turn.status == "running":
        return "queued" if run and run.status == "pending" else "in_progress"
    return "in_progress" if turn.status == "cancelling" else turn.status
