"""公开事件逐条写入 Redis，批量仅优化传输。"""

from __future__ import annotations

import time

from yuxi.infrastructure.observability.logging import logger
from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.agents.services.openai_events import OpenAIEventAdapter
from yuxi.modules.agents.services.transport import append_run_stream_events

LOADING_FLUSH_INTERVAL_MS = 100
LOADING_FLUSH_MAX_CHARS = 512


class PublicEventWriter:
    """首输出和内容边界及时发布，文本增量有界缓冲。"""

    def __init__(self, run_id: str, interval_ms: int = 100, max_chars: int = 512):
        """绑定一个独立 Run 的短期事件流。"""
        self.run_id = run_id
        self.interval_seconds = interval_ms / 1000
        self.max_chars = max_chars
        self.events: list[dict] = []
        self.chars = 0
        self.last_flush = time.monotonic()
        self.first_output = True

    async def append(self, event: dict) -> None:
        """保持原始公开事件，不合并 data 或改变 item 顺序。"""
        self.events.append(event)
        self.chars += len(event.get("delta", ""))
        is_delta = event["type"].endswith(".delta")
        if not is_delta or self.first_output or self.chars >= self.max_chars or time.monotonic() - self.last_flush >= self.interval_seconds:
            if contains_model_output(event):
                self.first_output = False
            await self.flush()

    async def flush(self) -> None:
        """批量发布后清空缓冲；失败不影响业务状态收敛。"""
        if not self.events:
            return
        events, self.events = self.events, []
        self.chars = 0
        self.last_flush = time.monotonic()
        try:
            await append_run_stream_events(self.run_id, events)
        except Exception:
            logger.opt(exception=True).warning("公开事件发布失败: run={}", self.run_id)


async def append_run_event_best_effort(run_id: str, event: dict) -> bool:
    """发布已适配的事件，PostgreSQL 终态不依赖 Redis 可用性。"""
    try:
        await append_run_stream_events(run_id, [event])
    except Exception:
        logger.opt(exception=True).warning("公开事件发布失败: run={} type={}", run_id, event.get("type"))
        return False
    return True


async def flush_writer_best_effort(writer: PublicEventWriter) -> None:
    """退出执行前发布已生成的公开增量。"""
    await writer.flush()


def contains_model_output(event: dict) -> bool:
    """识别正文、原始推理或实际函数参数的首输出。"""
    return bool(event.get("delta")) or (
        event["type"] == "agent.session.turn.item.added" and event.get("item", {}).get("type") == "function_call"
    )


async def publish_run_settlement(run_id: str, status: str, *, thread_id: str | None = None) -> None:
    """只根据已提交的 Run 事实发布执行段收敛，整轮事件由 Turn Owner 投影。"""
    from yuxi.modules.agents.repositories.runs import AgentRunRepository

    async with pg_manager.get_async_session_context() as db:
        run = await AgentRunRepository(db).get_run(run_id)
        if run is None or run.status != status:
            raise ValueError("执行段通知缺少相同 PostgreSQL 事实")
    adapter = OpenAIEventAdapter(run_id=run.id, turn_id=run.turn_id, thread_id=run.thread_id, worker_id="")
    await append_run_event_best_effort(
        run_id,
        adapter.extension(
            "run.settled",
            f"settled:{status}",
            status=status,
        ),
    )
