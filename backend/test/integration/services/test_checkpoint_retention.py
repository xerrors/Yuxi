"""真实 PostgreSQL 验证 checkpoint 保留治理：终态裁剪、未终态跳过、清扫兜底、裁剪期新写入与命名空间独立性。"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from agent_run_test_helpers import cleanup_agent_run_threads, create_agent_run
from sqlalchemy import select, text

from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.services import checkpoint_retention as retention
from yuxi.modules.agents.services.run_lifecycle import finish_run_lifecycle

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


@pytest.fixture(autouse=True)
async def _isolate_global_pg_manager():
    """用例结束关闭全局连接池：pytest-asyncio 每用例独立事件循环，跨循环复用 asyncpg 连接会失败。"""
    yield
    from yuxi.infrastructure.postgres.manager import pg_manager

    await pg_manager.close()


async def _seed_history(sessions, thread_id: str, *, n: int, ts: datetime | None = None):
    """直插 n 个 checkpoint 版本及独占 blob/writes；checkpoint_id 字典序即新旧序。"""
    stamp = (ts or datetime.now(UTC)).isoformat()
    async with sessions() as db:
        for i in range(1, n + 1):
            ck_id = f"{i:04d}"
            await db.execute(
                text(
                    "insert into checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata)"
                    " values (:t, '', :id, null, 'json', cast(:ck as jsonb), '{}'::jsonb) on conflict do nothing"
                ),
                {
                    "t": thread_id,
                    "id": ck_id,
                    "ck": json.dumps({"ts": stamp, "id": ck_id, "channel_versions": {"messages": f"v{i}"}}),
                },
            )
            await db.execute(
                text(
                    "insert into checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)"
                    " values (:t, '', 'messages', :v, 'json', 'x') on conflict do nothing"
                ),
                {"t": thread_id, "v": f"v{i}"},
            )
            await db.execute(
                text(
                    "insert into checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob)"
                    " values (:t, '', :id, 'task', 0, 'messages', 'json', 'x') on conflict do nothing"
                ),
                {"t": thread_id, "id": ck_id},
            )
        await db.commit()


async def _counts(sessions, thread_id: str) -> dict:
    async with sessions() as db:
        return {
            "checkpoints": await db.scalar(text("select count(*) from checkpoints where thread_id = :t"), {"t": thread_id}),
            "blobs": await db.scalar(text("select count(*) from checkpoint_blobs where thread_id = :t"), {"t": thread_id}),
            "writes": await db.scalar(text("select count(*) from checkpoint_writes where thread_id = :t"), {"t": thread_id}),
            "kept_ids": sorted(
                (await db.scalars(text("select checkpoint_id from checkpoints where thread_id = :t"), {"t": thread_id})).all()
            ),
        }


async def _drop_checkpoint_rows(sessions, *thread_ids: str) -> None:
    """三表不属 cleanup_agent_run_threads 的清理面，测试自行归还 self_managed_resources 义务。"""
    async with sessions() as db:
        for t in thread_ids:
            await db.execute(text("delete from checkpoint_writes where thread_id = :t"), {"t": t})
            await db.execute(text("delete from checkpoint_blobs where thread_id = :t"), {"t": t})
            await db.execute(text("delete from checkpoints where thread_id = :t"), {"t": t})
        await db.commit()


async def _thread_uid(sessions, thread_id: str) -> str:
    async with sessions() as db:
        return await db.scalar(select(Session.uid).where(Session.thread_id == thread_id))


async def test_terminal_purge_trims_history_keeps_latest_and_exclusive_blobs(postgres_sessions):
    """终态裁剪保留最新 K 个版本，独占 blob 与 writes 同批删除。"""
    _run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="ck-keep", message_content="x", input_payload={}, status="completed"
    )
    try:
        await _seed_history(postgres_sessions, thread_id, n=8)
        assert await retention.purge_thread_checkpoint_history(thread_id, keep=5) == retention.PURGE_TRIMMED
        counts = await _counts(postgres_sessions, thread_id)
        assert counts["kept_ids"] == ["0004", "0005", "0006", "0007", "0008"]
        # 被删 checkpoint 独占的 v1..v3 blob 与 writes 一并收敛。
        assert counts["blobs"] == 5
        assert counts["writes"] == 5
    finally:
        await _drop_checkpoint_rows(postgres_sessions, thread_id)
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])


async def test_purge_keeps_shared_blob_version_still_referenced(postgres_sessions):
    """保留版本仍引用的共享 channel 版本必须留下：只按 checkpoint_id 关联删除会读空在用 channel。"""
    _run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="ck-shared", message_content="x", input_payload={}, status="completed"
    )
    stamp = datetime.now(UTC).isoformat()
    try:
        async with postgres_sessions() as db:
            for i in range(1, 11):
                ck_id = f"{i:04d}"
                await db.execute(
                    text(
                        "insert into checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,"
                        " type, checkpoint, metadata)"
                        " values (:t, '', :id, null, 'json', cast(:ck as jsonb), '{}'::jsonb) on conflict do nothing"
                    ),
                    {
                        "t": thread_id,
                        "id": ck_id,
                        "ck": json.dumps({"ts": stamp, "id": ck_id, "channel_versions": {"messages": f"v{i}", "shared": "shared-v"}}),
                    },
                )
                await db.execute(
                    text(
                        "insert into checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)"
                        " values (:t, '', 'messages', :v, 'json', 'x') on conflict do nothing"
                    ),
                    {"t": thread_id, "v": f"v{i}"},
                )
                await db.execute(
                    text(
                        "insert into checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)"
                        " values (:t, '', 'shared', 'shared-v', 'json', 'y') on conflict do nothing"
                    ),
                    {"t": thread_id},
                )
                await db.execute(
                    text(
                        "insert into checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx,"
                        " channel, type, blob) values (:t, '', :id, 'task', 0, 'messages', 'json', 'x')"
                        " on conflict do nothing"
                    ),
                    {"t": thread_id, "id": ck_id},
                )
            await db.commit()

        assert await retention.purge_thread_checkpoint_history(thread_id, keep=5) == retention.PURGE_TRIMMED
        async with postgres_sessions() as db:
            checkpoints = await db.scalar(text("select count(*) from checkpoints where thread_id = :t"), {"t": thread_id})
            writes = await db.scalar(text("select count(*) from checkpoint_writes where thread_id = :t"), {"t": thread_id})
            messages_blobs = await db.scalar(
                text("select count(*) from checkpoint_blobs where thread_id = :t and channel = 'messages'"),
                {"t": thread_id},
            )
            shared_blobs = await db.scalar(
                text("select count(*) from checkpoint_blobs where thread_id = :t and channel = 'shared'"),
                {"t": thread_id},
            )
        assert checkpoints == 5 and writes == 5, (checkpoints, writes)
        assert messages_blobs == 5, "独占 messages 版本应随旧 checkpoint 收敛"
        assert shared_blobs == 1, "被保留版本引用的 shared 版本不得删除"
    finally:
        await _drop_checkpoint_rows(postgres_sessions, thread_id)
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])


async def test_purge_skips_thread_with_unfinished_run(postgres_sessions):
    """pending/running/cancel_requested 的线程整体跳过，不与在途执行竞争删除。"""
    for status in ("pending", "running", "cancel_requested"):
        run_id, thread_id, _ = await create_agent_run(
            postgres_sessions, prefix=f"ck-{status}", message_content="x", input_payload={}, status=status
        )
        try:
            await _seed_history(postgres_sessions, thread_id, n=8)
            assert await retention.purge_thread_checkpoint_history(thread_id, keep=5) == retention.PURGE_SKIPPED_ACTIVE
            assert (await _counts(postgres_sessions, thread_id))["checkpoints"] == 8, status
            # 阳性对照：同一用例内把 Run 置终态再裁剪必须生效，排除"裁剪静默失败"伪装成跳过。
            async with postgres_sessions() as db:
                await db.execute(text("update agent_runs set status='completed' where id = :r"), {"r": str(run_id)})
                await db.commit()
            assert await retention.purge_thread_checkpoint_history(thread_id, keep=5) == retention.PURGE_TRIMMED
            assert (await _counts(postgres_sessions, thread_id))["checkpoints"] == 5, status
        finally:
            await _drop_checkpoint_rows(postgres_sessions, thread_id)
            await cleanup_agent_run_threads(postgres_sessions, [thread_id])


async def test_finish_run_lifecycle_hook_trims_terminal_thread(postgres_sessions):
    """生产 choke 守卫：终态 Run 走 finish_run_lifecycle 即裁剪本线程历史。"""
    run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="ck-hook", message_content="x", input_payload={}, status="yielded"
    )
    try:
        await _seed_history(postgres_sessions, thread_id, n=8)
        async with postgres_sessions() as db:
            run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id))
            db.expunge(run)
            await db.commit()
        await finish_run_lifecycle(run)
        assert (await _counts(postgres_sessions, thread_id))["checkpoints"] == 5
    finally:
        await _drop_checkpoint_rows(postgres_sessions, thread_id)
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])


async def test_sweep_covers_over_k_and_orphans_with_grace(postgres_sessions):
    """兜底清扫两路：活跃会话线程裁到 K、孤儿线程宽限期后整删（软删会话无入口，不在本批）。

    清扫按设计是全库的：它也会裁掉库里其它超 K 线程、整删其它宽限期外孤儿，因此断言只取
    `>= 1` 并显式核对本用例种下的线程；用例结束后本用例的线程必须自己归还三表行，
    不能留给下一轮 sweep 当作孤儿"顺手"删掉。
    """
    _run_id, over_k_thread, _ = await create_agent_run(
        postgres_sessions, prefix="ck-over", message_content="x", input_payload={}, status="completed"
    )
    old_orphan, fresh_orphan = uuid.uuid4().hex, uuid.uuid4().hex
    now = datetime.now(UTC)
    try:
        await _seed_history(postgres_sessions, over_k_thread, n=8)
        await _seed_history(postgres_sessions, old_orphan, n=3, ts=now - timedelta(days=8))
        await _seed_history(postgres_sessions, fresh_orphan, n=3, ts=now - timedelta(days=1))

        stats = await retention.sweep_checkpoint_garbage()
        assert stats["trimmed_threads"] >= 1
        assert stats["orphan_threads"] >= 1
        assert (await _counts(postgres_sessions, over_k_thread))["checkpoints"] == 5
        assert (await _counts(postgres_sessions, old_orphan))["checkpoints"] == 0
        # 宽限期内的孤儿不动，避免误删创建流程中断的在途线程。
        assert (await _counts(postgres_sessions, fresh_orphan))["checkpoints"] == 3
    finally:
        await cleanup_agent_run_threads(postgres_sessions, [over_k_thread])
        await _drop_checkpoint_rows(postgres_sessions, over_k_thread, old_orphan, fresh_orphan)


async def test_trim_keeps_checkpoint_written_after_thread_selection(postgres_sessions):
    """清理期间的新写入不会被旧快照删掉：保留集在每条删除语句里现场重算，最新版必然入选。"""
    _run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="ck-late", message_content="x", input_payload={}, status="completed"
    )
    try:
        # 目标线程清单先选好（模拟脚本 retain 的分批枚举），随后线程又写入更新的版本。
        await _seed_history(postgres_sessions, thread_id, n=6)
        selected = [thread_id]
        await _seed_history(postgres_sessions, thread_id, n=9)

        assert await retention.purge_thread_checkpoint_history(selected[0], keep=5) == retention.PURGE_TRIMMED
        kept = (await _counts(postgres_sessions, thread_id))["kept_ids"]
        assert kept == ["0005", "0006", "0007", "0008", "0009"], f"清理后保留集不符（新写入必须留下）：{kept}"
    finally:
        await _drop_checkpoint_rows(postgres_sessions, thread_id)
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])


async def test_trim_keeps_latest_checkpoint_readable_by_state_view(postgres_sessions):
    """真实 saver 写入 7 个版本、裁剪到 5 后，面板状态读仍拿到最新保留版本：恢复与审批只消费最新版。"""
    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    import yuxi.modules.agents.services.checkpoint_state as svc

    class _S(TypedDict, total=False):
        token_usage: dict

    _run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="ck-view", message_content="x", input_payload={}, status="completed"
    )
    uid = await _thread_uid(postgres_sessions, thread_id)
    dsn = os.environ["POSTGRES_URL"].replace("+asyncpg", "").replace("+psycopg", "")
    try:
        graph = StateGraph(_S)
        graph.add_node("bump", lambda state: {})
        graph.add_edge(START, "bump")
        graph.add_edge("bump", END)
        config = RunnableConfig(configurable={"uid": uid, "thread_id": thread_id, "checkpoint_ns": ""})
        async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
            compiled = graph.compile(checkpointer=saver)
            for i in range(1, 8):
                await compiled.ainvoke({"token_usage": {"total": i}}, config)

        await retention.purge_thread_checkpoint_history(thread_id, keep=5)
        async with postgres_sessions() as db:
            remaining = await db.scalar(text("select count(*) from checkpoints where thread_id = :t"), {"t": thread_id})
        assert remaining == 5

        async with postgres_sessions() as db:
            view = await svc.get_agent_state_view(
                thread_id=thread_id, current_user=SimpleNamespace(uid=uid), db=db, include_relations=False
            )
        assert view["agent_state"]["token_usage"] == {"total": 7}
    finally:
        await _drop_checkpoint_rows(postgres_sessions, thread_id)
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])


async def _seed_ns_history(sessions, thread_id: str, *, ns_pages: dict[str, int]) -> None:
    """同一线程的多个 checkpoint_ns 各插若干版本，blob 版本字符串跨命名空间重复。

    命名空间必须逐独立处理：按线程总数选目标、却按 (thread_id, checkpoint_ns) 各保留 K 版，
    任何把两维混算的实现都会把一个命名空间的版本当成另一个的多余历史删掉。
    """
    stamp = datetime.now(UTC).isoformat()
    async with sessions() as db:
        for ns, count in ns_pages.items():
            for i in range(1, count + 1):
                ck_id = f"{i:04d}"
                await db.execute(
                    text(
                        "insert into checkpoints (thread_id, checkpoint_ns, checkpoint_id,"
                        " parent_checkpoint_id, type, checkpoint, metadata)"
                        " values (:t, :ns, :id, null, 'json', cast(:ck as jsonb), '{}'::jsonb) on conflict do nothing"
                    ),
                    {
                        "t": thread_id,
                        "ns": ns,
                        "id": ck_id,
                        "ck": json.dumps({"ts": stamp, "id": ck_id, "channel_versions": {"messages": f"v{i}"}}),
                    },
                )
                await db.execute(
                    text(
                        "insert into checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)"
                        " values (:t, :ns, 'messages', :v, 'json', 'x') on conflict do nothing"
                    ),
                    {"t": thread_id, "ns": ns, "v": f"v{i}"},
                )
                await db.execute(
                    text(
                        "insert into checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob)"
                        " values (:t, :ns, :id, 'task', 0, 'messages', 'json', 'x') on conflict do nothing"
                    ),
                    {"t": thread_id, "ns": ns, "id": ck_id},
                )
        await db.commit()


async def _ns_kept(sessions, thread_id: str, ns: str) -> list[str]:
    async with sessions() as db:
        return sorted(
            (
                await db.scalars(
                    text("select checkpoint_id from checkpoints where thread_id = :t and checkpoint_ns = :ns"),
                    {"t": thread_id, "ns": ns},
                )
            ).all()
        )


async def test_sweep_skips_thread_whose_namespaces_are_all_within_k(postgres_sessions, monkeypatch):
    """逐命名空间判定：每个命名空间都未超 K 的线程不得进入清扫目标，否则计数虚报而零删除。"""
    _run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="ck-ck", message_content="x", input_payload={}, status="completed"
    )
    await _seed_ns_history(postgres_sessions, thread_id, ns_pages={"": 3, "sub:agent-1": 3})
    selected: list[str] = []

    async def spy(thread_id_arg: str, *, keep: int = retention.THREAD_CHECKPOINT_KEEP_LATEST) -> str:
        """记录被选中裁剪的线程，不真正删除：共享库里其它线程的存量不参与本用例判定。"""
        selected.append(thread_id_arg)
        return retention.PURGE_TRIMMED

    monkeypatch.setattr(retention, "purge_thread_checkpoint_history", spy)
    try:
        await retention.sweep_checkpoint_garbage()
        assert thread_id not in selected, f"各命名空间都未超 K 的线程被误选为裁剪目标：{selected}"
        kept = await _ns_kept(postgres_sessions, thread_id, "")
        assert kept == ["0001", "0002", "0003"]
    finally:
        await _drop_checkpoint_rows(postgres_sessions, thread_id)
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])


async def test_purge_trims_each_namespace_independently(postgres_sessions):
    """命名空间按各自历史独立裁剪：8 版留 5 版、3 版全留，版本标识跨命名空间重名也不互相顶替。"""
    _run_id, thread_id, _ = await create_agent_run(
        postgres_sessions, prefix="ck-ns", message_content="x", input_payload={}, status="completed"
    )
    root_ns, sub_ns = "", "sub:agent-1"
    await _seed_ns_history(postgres_sessions, thread_id, ns_pages={root_ns: 8, sub_ns: 3})
    expected = {root_ns: ["0004", "0005", "0006", "0007", "0008"], sub_ns: ["0001", "0002", "0003"]}
    try:
        assert await retention.purge_thread_checkpoint_history(thread_id) == retention.PURGE_TRIMMED

        for ns, kept_ids in expected.items():
            kept = await _ns_kept(postgres_sessions, thread_id, ns)
            assert kept == kept_ids, f"命名空间 {ns} 的保留集不符：{kept}"
            async with postgres_sessions() as db:
                rows = {
                    table: await db.scalar(
                        text(f"select count(*) from {table} where thread_id = :t and checkpoint_ns = :ns"),
                        {"t": thread_id, "ns": ns},
                    )
                    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
                }
            assert all(value == len(kept_ids) for value in rows.values()), f"命名空间 {ns} 的三表保留量不符：{rows}"
    finally:
        await _drop_checkpoint_rows(postgres_sessions, thread_id)
        await cleanup_agent_run_threads(postgres_sessions, [thread_id])
