"""从持久生命周期和 Redis 投影订阅公开事件，共用 Thread cursor。"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.agents.repositories.input_receipt import AgentInputReceiptRepository
from yuxi.modules.agents.repositories.runs import TERMINAL_RUN_STATUSES, AgentRunRepository
from yuxi.modules.agents.repositories.turn import AgentTurnRepository
from yuxi.modules.agents.scope import ActorScope
from yuxi.modules.agents.services.openai_events import OpenAIEventAdapter
from yuxi.modules.agents.services.public_resources import turn_core
from yuxi.modules.agents.services.thread_access import require_thread
from yuxi.modules.agents.services.transport import list_recent_run_stream_events, list_run_stream_events
from yuxi.shared.datetime import utc_now
from yuxi.shared.errors import InvalidInputError
from yuxi.shared.hashing import hash_id

SSE_HEARTBEAT_SECONDS = int(os.getenv("RUN_SSE_HEARTBEAT_SECONDS", "15"))
SSE_MAX_CONNECTION_MINUTES = int(os.getenv("RUN_SSE_MAX_CONNECTION_MINUTES", "30"))
_CURSOR = re.compile(r"^2\.(\d+)\.(\d+)\.(\d+-\d+)\.([0-7])$")


@dataclass(slots=True)
class _Cursor:
    """分别保存接收序号、执行序号、Redis 位置和持久边界。"""

    receipt_seq: int = 0
    run_seq: int = 0
    event_seq: str = "0-0"
    run_phase: int = 0

    def encode(self) -> str:
        """生成本协议版本的 Last-Event-ID。"""
        return f"2.{self.receipt_seq}.{self.run_seq}.{self.event_seq}.{self.run_phase}"


def _parse_cursor(raw: str | None) -> _Cursor:
    """拒绝旧版本和损坏 cursor。"""
    if not raw:
        return _Cursor()
    match = _CURSOR.fullmatch(raw)
    if match is None:
        raise InvalidInputError("事件游标无效或版本不兼容")
    receipt, run, event, phase = match.groups()
    return _Cursor(int(receipt), int(run), event, int(phase))


def validate_event_cursor(raw: str | None) -> None:
    """在发送响应头前校验恢复位置。"""
    _parse_cursor(raw)


async def stream_thread_events(
    *,
    scope: ActorScope,
    thread_id: str,
    after_cursor: str | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """读取已提交的业务边界并直接转发逐条公开事件。"""
    cursor = _parse_cursor(after_cursor)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SSE_MAX_CONNECTION_MINUTES * 60
    next_heartbeat = loop.time() + SSE_HEARTBEAT_SECONDS
    resynced = set()
    while loop.time() < deadline:
        async with pg_manager.get_async_session_context() as db:
            await require_thread(db=db, scope=scope, thread_id=thread_id)
            receipts = await AgentInputReceiptRepository(db).list_after_sequence(
                uid=scope.uid,
                app_id=scope.app_id,
                thread_id=thread_id,
                after_sequence=cursor.receipt_seq,
            )
            runs = await AgentRunRepository(db).list_thread_runs_after_sequence(
                thread_id=thread_id,
                uid=scope.uid,
                app_id=scope.app_id,
                after_sequence=max(0, cursor.run_seq - 1),
            )
            turn_ids = list({run.turn_id for run in runs})
            repo = AgentTurnRepository(db)
            turns = await repo.list_for_scope(turn_ids=turn_ids, thread_id=thread_id, uid=scope.uid, app_id=scope.app_id)
            facts = [(run, turns.get(run.turn_id)) for run in runs]
            input_ids = await AgentInputRepository(db).input_ids_for_runs([run.id for run in runs])
            start_times = await repo.get_start_times(turn_ids=turn_ids, uid=scope.uid, app_id=scope.app_id)
        emitted = False
        for receipt in receipts:
            cursor.receipt_seq = receipt.receive_seq
            event = {
                "type": "yuxi.session.input.accepted",
                "event_id": receipt.id,
                "session_id": thread_id,
                "yuxi": {
                    "input_id": receipt.input_id,
                    "turn_id": receipt.turn_id,
                    "run_id": receipt.run_id,
                    "input_type": receipt.event_type,
                },
            }
            yield cursor.encode(), event
            emitted = True
        for run, turn in facts:
            if run.execution_seq < cursor.run_seq or (run.execution_seq == cursor.run_seq and cursor.run_phase == 6):
                continue
            if turn is None:
                raise ValueError("订阅 Run 缺少自身 Thread 的 Turn")
            adapter = OpenAIEventAdapter(run_id=run.id, turn_id=turn.id, thread_id=thread_id, worker_id="")
            if run.execution_seq > cursor.run_seq:
                cursor.run_seq, cursor.event_seq, cursor.run_phase = run.execution_seq, "0-0", 0
            if cursor.run_phase == 0:
                cursor.run_phase = 1
                yield (
                    cursor.encode(),
                    adapter.extension("run.created", "created", input_ids=input_ids[run.id], created_by_run_id=run.created_by_run_id),
                )
                emitted = True
            if cursor.run_phase == 1:
                cursor.run_phase = 2
                if run.resume_from_run_id is None:
                    yield cursor.encode(), _turn_event(adapter, run, turn, "created", "queued", start_times[turn.id], input_ids[run.id])
                    emitted = True
            if cursor.run_phase == 2:
                if run.started_at is None and run.status not in TERMINAL_RUN_STATUSES:
                    break
                cursor.run_phase = 3
                if run.started_at is not None:
                    yield (
                        cursor.encode(),
                        _turn_event(adapter, run, turn, "in_progress", "in_progress", start_times[turn.id], input_ids[run.id]),
                    )
                    emitted = True
            if cursor.run_phase == 3:
                try:
                    events = await list_run_stream_events(run.id, after_seq=cursor.event_seq)
                    oldest = await list_run_stream_events(run.id, after_seq="0-0", limit=1)
                except Exception as exc:
                    logger.warning("读取 Run 增量失败: run={} error={}", run.id, exc)
                    events, oldest = [], []
                expired = (cursor.event_seq != "0-0" and (not oldest or _redis_id(oldest[0]["seq"]) > _redis_id(cursor.event_seq))) or (
                    run.status in TERMINAL_RUN_STATUSES and not events and not oldest
                )
                position = (run.id, cursor.event_seq)
                if expired and position not in resynced:
                    resynced.add(position)
                    yield (
                        cursor.encode(),
                        adapter.extension("resync", f"resync:{cursor.event_seq}", reason="run_events_expired"),
                    )
                    emitted = True
                for row in events:
                    cursor.event_seq = row["seq"]
                    event = row["event"]
                    owner = event
                    if owner["session_id"] != thread_id or owner.get("turn_id") != turn.id:
                        raise ValueError("Redis 公开事件与订阅执行归属不一致")
                    yield cursor.encode(), event
                    emitted = True
                if len(events) == 200:
                    break
                if run.status not in TERMINAL_RUN_STATUSES or run.runtime_cleanup_pending:
                    break
                try:
                    latest = await list_recent_run_stream_events(run.id, limit=1)
                except Exception:
                    latest = []
                has_settled = bool(latest and latest[0]["event"]["type"] == "yuxi.session.run.settled")
                age = (utc_now() - run.finished_at).total_seconds() if run.finished_at else 0
                if not has_settled and not expired and age < 2:
                    break
                cursor.run_phase = 4
            if cursor.run_phase == 4:
                cursor.run_phase = 5
                yield cursor.encode(), adapter.extension("run.settled", f"settled:{run.status}", status=run.status)
                emitted = True
            if cursor.run_phase in {5, 7}:
                if turn.current_run_id != run.id:
                    cursor.run_phase = 6
                    continue
                if turn.status == "waiting":
                    if cursor.run_phase == 7:
                        break
                    cursor.run_phase = 7
                    yield (
                        cursor.encode(),
                        {
                            **adapter.extension("turn.waiting", "waiting", waitpoint=turn.waitpoint, current_run_id=turn.current_run_id),
                            "turn": turn_core(turn, run, started_at=start_times[turn.id]),
                        },
                    )
                    emitted = True
                elif turn.status in {"completed", "failed", "cancelled"}:
                    cursor.run_phase = 6
                    yield (
                        cursor.encode(),
                        _turn_event(adapter, run, turn, turn.status, turn.status, start_times[turn.id], input_ids[run.id]),
                    )
                    emitted = True
                else:
                    break
        if emitted:
            next_heartbeat = loop.time() + SSE_HEARTBEAT_SECONDS
            continue
        if loop.time() >= next_heartbeat:
            yield (
                cursor.encode(),
                {
                    "type": "yuxi.session.heartbeat",
                    "session_id": thread_id,
                    "event_id": hash_id("event_", f"{thread_id}:heartbeat:{loop.time()}", length=64),
                },
            )
            next_heartbeat = loop.time() + SSE_HEARTBEAT_SECONDS
        await asyncio.sleep(0.2)


def _turn_event(adapter: OpenAIEventAdapter, run, turn, name: str, status: str, started_at, input_ids: list[str]) -> dict:
    """标准 Turn 字段使用官方类型，业务错误分类留在扩展中。"""

    return {
        "type": f"agent.session.turn.{name}",
        "event_id": hash_id("event_", f"{run.id}:turn:{name}", length=64),
        "session_id": run.thread_id,
        "turn_id": turn.id,
        "turn": turn_core(turn, run, started_at=started_at, status=status),
        "yuxi": {
            "run_id": run.id,
            "input_ids": input_ids,
            "result_run_id": turn.result_run_id,
            "current_run_id": turn.current_run_id,
            "created_by_run_id": run.created_by_run_id,
            "error_type": run.error_type,
        },
    }


def _redis_id(value: str) -> tuple[int, int]:
    """比较 Redis 裁剪位置。"""
    millisecond, sequence = value.split("-", 1)
    return int(millisecond), int(sequence)
