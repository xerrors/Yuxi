"""checkpoint 保留治理的失败降级、计数口径与清扫去重 unit：异常不外泄、失败与跳过不混计。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import yuxi.modules.agents.services.checkpoint_retention as retention


class _FailingDb:
    async def execute(self, *_args, **_kwargs):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_purge_swallows_session_failure_and_reports_failed(monkeypatch):
    """裁剪失败只告警返回 PURGE_FAILED，绝不把异常泄给 Run 终态主链路。"""

    @asynccontextmanager
    async def fake_context():
        yield _FailingDb()

    class _Manager:
        get_async_session_context = staticmethod(fake_context)

    monkeypatch.setattr(retention, "pg_manager", _Manager())
    assert await retention.purge_thread_checkpoint_history("thread-x") == retention.PURGE_FAILED


@pytest.mark.asyncio
async def test_purge_rejects_non_positive_keep(monkeypatch):
    """保留数量必须是正整数：0 或负数会清空非活跃线程的全部历史，必须在触库前拒绝。"""

    @asynccontextmanager
    async def fake_context():
        yield _FailingDb()
        raise AssertionError("keep 非法时不得访问数据库")

    class _Manager:
        get_async_session_context = staticmethod(fake_context)

    monkeypatch.setattr(retention, "pg_manager", _Manager())
    for bad in (0, -1):
        with pytest.raises(ValueError, match="大于 0 的整数"):
            await retention.purge_thread_checkpoint_history("thread-x", keep=bad)


class _RecordingDb:
    """记录语句顺序，用于钉住超时设置先于删除语句。"""

    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, statement, _params=None):
        self.statements.append(str(statement))

        class _Result:
            def first(self):
                return None

        return _Result()

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_purge_bounds_wait_before_deleting(monkeypatch):
    """在线裁剪必须带锁等待与语句超时：优化不能把 Run 终态后处理变成无限等待。"""
    db = _RecordingDb()

    @asynccontextmanager
    async def fake_context():
        yield db

    class _Manager:
        get_async_session_context = staticmethod(fake_context)

    monkeypatch.setattr(retention, "pg_manager", _Manager())
    assert await retention.purge_thread_checkpoint_history("thread-1") == retention.PURGE_TRIMMED

    deletes = [index for index, statement in enumerate(db.statements) if "DELETE FROM" in statement]
    lock = [index for index, statement in enumerate(db.statements) if "lock_timeout" in statement]
    statement_timeout = [index for index, statement in enumerate(db.statements) if "statement_timeout" in statement]
    assert lock and statement_timeout, "缺少锁等待或语句超时设置"
    assert min(lock + statement_timeout) < min(deletes), "超时设置必须早于第一条删除语句"


class _SweepDb:
    """按 sweep 的两轮查询顺序（超 K、孤儿）返回给定线程清单。"""

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

    shared = _SweepDb([[], ["t2"]])
    monkeypatch.setattr(retention, "pg_manager", _Manager())
    monkeypatch.setattr(retention, "get_langgraph_checkpointer", lambda _m: _BrokenSaver())
    stats = await retention.sweep_checkpoint_garbage()
    assert stats == {"trimmed_threads": 0, "skipped_active_threads": 0, "orphan_threads": 0}


@pytest.mark.asyncio
async def test_sweep_counts_trimmed_and_skipped_separately(monkeypatch):
    """裁剪失败不计；因活跃 Run 跳过的线程单独计入 skipped_active_threads，两者不得混同。"""

    class _QuietSaver:
        async def adelete_thread(self, thread_id):
            return None

    class _Manager:
        @asynccontextmanager
        async def get_async_session_context(self):
            yield shared

    shared = _SweepDb([["t-ok", "t-skip", "t-fail"], []])

    async def fake_purge(thread_id, *, keep=retention.THREAD_CHECKPOINT_KEEP_LATEST):
        """按线程名返回三种结局。"""
        return {
            "t-ok": retention.PURGE_TRIMMED,
            "t-skip": retention.PURGE_SKIPPED_ACTIVE,
            "t-fail": retention.PURGE_FAILED,
        }[thread_id]

    monkeypatch.setattr(retention, "pg_manager", _Manager())
    monkeypatch.setattr(retention, "get_langgraph_checkpointer", lambda _m: _QuietSaver())
    monkeypatch.setattr(retention, "purge_thread_checkpoint_history", fake_purge)
    stats = await retention.sweep_checkpoint_garbage()
    assert stats["trimmed_threads"] == 1
    assert stats["skipped_active_threads"] == 1


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
