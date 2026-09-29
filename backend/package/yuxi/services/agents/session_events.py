"""跨 Request/Run 的 Public Session SSE 观察。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from time import monotonic

from sqlalchemy import select
from yuxi.services.agent_run_service import stream_agent_run_events
from yuxi.services.agents.public_api import get_public_session, get_public_turn
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.utils.sse_utils import SSE_HEARTBEAT_SECONDS, SSE_MAX_CONNECTION_MINUTES, format_heartbeat, format_sse


async def stream_public_session_events(
    *,
    thread_id: str,
    uid: str,
    app_id: str | None,
    turn_id: str | None,
    after_id: str,
    stream_url: str,
) -> AsyncIterator[str]:
    """用持久 Turn 关联跨 Run 接力，Redis 事件只负责临时增量。"""
    cursor_run_id, cursor_seq = after_id.split(":", 1) if ":" in after_id else (None, "0-0")
    processed_runs: set[str] = set()
    last_turn_status: dict[str, str] = {}
    started = last_heartbeat = monotonic()

    while monotonic() - started < SSE_MAX_CONNECTION_MINUTES * 60:
        async with pg_manager.get_async_session_context() as db:
            # 已在 HTTP 入口鉴权；流只用标量身份重读当前持久事实。
            user = await _load_user_by_uid(db, uid)
            if user is None:
                return
            if turn_id is None:
                session = await get_public_session(thread_id=thread_id, user=user, app_id=app_id, db=db)
                selected_turn_id = session.get("turn_id")
            else:
                selected_turn_id = turn_id
            snapshot = (
                await get_public_turn(thread_id=thread_id, turn_id=selected_turn_id, user=user, app_id=app_id, db=db)
                if selected_turn_id
                else None
            )

        if snapshot is None:
            if monotonic() - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
                yield format_heartbeat()
                last_heartbeat = monotonic()
            await asyncio.sleep(1)
            continue

        current_turn_id = snapshot["turn_id"]
        run_ids = snapshot["run_ids"]
        if cursor_run_id is not None and cursor_run_id in run_ids:
            run_ids = run_ids[run_ids.index(cursor_run_id) :]
        for run_id in run_ids:
            if run_id in processed_runs:
                continue
            if run_id == cursor_run_id and cursor_seq == "end":
                processed_runs.add(run_id)
                cursor_run_id, cursor_seq = None, "0-0"
                continue
            if run_id != cursor_run_id:
                yield format_sse(
                    {
                        "session_id": thread_id,
                        "turn_id": current_turn_id,
                        "run_id": run_id,
                        "stream_url": stream_url,
                    },
                    event="run_created",
                    event_id=f"{run_id}:0-0",
                )
            async for event in stream_agent_run_events(
                run_id=run_id,
                after_seq=cursor_seq if run_id == cursor_run_id else "0-0",
                current_uid=uid,
                verbose=False,
            ):
                yield _scope_event_id(event, run_id)
                if event.startswith("event: error\n"):
                    return
            processed_runs.add(run_id)
            cursor_run_id, cursor_seq = None, "0-0"

        async with pg_manager.get_async_session_context() as db:
            user = await _load_user_by_uid(db, uid)
            if user is None:
                return
            snapshot = await get_public_turn(
                thread_id=thread_id, turn_id=current_turn_id, user=user, app_id=app_id, db=db
            )

        status = snapshot["status"]
        if (
            status in {"completed", "failed", "cancelled", "waiting"}
            and last_turn_status.get(current_turn_id) != status
        ):
            yield format_sse(
                {"session_id": thread_id, "turn_id": current_turn_id, "status": status},
                event="turn_status",
            )
        last_turn_status[current_turn_id] = status
        if turn_id is not None and status in {"completed", "failed", "cancelled"}:
            return

        now = monotonic()
        if now - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
            yield format_heartbeat()
            last_heartbeat = now
        await asyncio.sleep(1)


async def _load_user_by_uid(db, uid: str) -> User | None:
    """短会话内重读用户身份，不沿用已关闭请求会话的 ORM 对象。"""
    result = await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))
    return result.scalar_one_or_none()


def _scope_event_id(event: str, run_id: str) -> str:
    """为每个 Run 的 Redis 游标添加 Run ID，避免跨执行段游标碰撞。"""
    if event.startswith("event: end\n"):
        lines = [line for line in event.rstrip("\n").split("\n") if not line.startswith("id: ")]
        return "\n".join([*lines, f"id: {run_id}:end", "", ""])
    return event.replace("\nid: ", f"\nid: {run_id}:", 1)
