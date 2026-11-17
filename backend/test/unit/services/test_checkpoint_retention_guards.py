"""checkpoint 保留治理的失败降级与清扫计数 unit：异常不外泄、失败不计数、槽位锁单实例。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import yuxi.modules.agents.services.checkpoint_retention as retention


class _FailingDb:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_purge_swallows_session_failure_and_reports_false(monkeypatch):
    """裁剪失败只告警返回 False，绝不把异常泄给 Run 终态主链路。"""

    @asynccontextmanager
    async def fake_context():
        yield _FailingDb()

    class _Manager:
        get_async_session_context = staticmethod(fake_context)

    monkeypatch.setattr(retention, "pg_manager", _Manager())
    assert await retention.purge_thread_checkpoint_history("thread-x") is False


class _SweepDb:
    """按 sweep 的三次查询顺序返回给定线程清单。"""

    def __init__(self, batches):
        self._batches = list(batches)

    async def execute(self, *_args, **_kwargs):
        rows = self._batches.pop(0)

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return rows

        return _Result()


@pytest.mark.asyncio
async def test_sweep_survives_saver_failure_without_counting(monkeypatch):
    """adelete_thread 失败只跳过该线程：不中断清扫、不把失败计成成功。"""

    class _BrokenSaver:
        async def adelete_thread(self, thread_id):
            raise RuntimeError("saver down")

    class _Manager:
        @asynccontextmanager
        async def get_async_session_context(self):
            yield shared

    shared = _SweepDb([["t1"], [], ["t2"]])
    monkeypatch.setattr(retention, "pg_manager", _Manager())
    monkeypatch.setattr(retention, "get_langgraph_checkpointer", lambda _m: _BrokenSaver())
    stats = await retention.sweep_checkpoint_garbage()
    assert stats == {"deleted_threads": 0, "trimmed_threads": 0, "orphan_threads": 0}


@pytest.mark.asyncio
async def test_sweep_counts_trimmed_only_when_purge_succeeds(monkeypatch):
    """超 K 线程裁剪失败时 trimmed_threads 不计数，防止观测页看到虚增的处理量。"""

    class _QuietSaver:
        async def adelete_thread(self, thread_id):
            return None

    class _Manager:
        @asynccontextmanager
        async def get_async_session_context(self):
            yield shared

    shared = _SweepDb([[], ["t-ok", "t-bad"], []])

    async def fake_purge(thread_id, *, keep=retention.THREAD_CHECKPOINT_KEEP_LATEST):
        return thread_id == "t-ok"

    monkeypatch.setattr(retention, "pg_manager", _Manager())
    monkeypatch.setattr(retention, "get_langgraph_checkpointer", lambda _m: _QuietSaver())
    monkeypatch.setattr(retention, "purge_thread_checkpoint_history", fake_purge)
    stats = await retention.sweep_checkpoint_garbage()
    assert stats["trimmed_threads"] == 1


@pytest.mark.asyncio
async def test_sweeper_leader_slot_dedupes(monkeypatch):
    """每日槽位只属于一个副本：未抢到锁的 worker 不得重复清扫。"""
    from yuxi.bootstrap import worker as worker_bootstrap

    hits: list[int] = []

    class _Redis:
        async def set(self, *args, **kwargs):
            return False

    async def client():
        return _Redis()

    async def sweep():
        hits.append(1)

    monkeypatch.setattr(worker_bootstrap, "get_async_redis_client", client)
    monkeypatch.setattr(retention, "sweep_checkpoint_garbage", sweep)
    await worker_bootstrap._sweep_checkpoint_garbage_if_leader()
    assert hits == [], "未抢到每日槽位的副本不得清扫"


@pytest.mark.asyncio
async def test_sweeper_runs_once_when_slot_acquired(monkeypatch):
    """抢到当日槽位的副本必须真正清扫一次，否则去重测试可以靠永不执行蒙绿。"""
    from yuxi.bootstrap import worker as worker_bootstrap

    hits: list[int] = []

    class _Redis:
        async def set(self, *args, **kwargs):
            return True

    async def client():
        return _Redis()

    async def sweep():
        hits.append(1)

    monkeypatch.setattr(worker_bootstrap, "get_async_redis_client", client)
    monkeypatch.setattr(retention, "sweep_checkpoint_garbage", sweep)
    await worker_bootstrap._sweep_checkpoint_garbage_if_leader()
    assert hits == [1]
