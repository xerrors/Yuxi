"""看板统计的 agent 归属不该决定"这个人今天有没有用过"（sqlite 真实 SQL）。

会话指向的 slug 可以在 `agents` 表中没有对应行（智能体删除后历史会话保留 `agent_id`，
列无外键与重指派）。活跃、基础计数、分布与分析曾用 inner join 过滤，导致这些会话从
24h/30d/日活、会话与消息总数、分布与分析系列中整体消失。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test.support.sqlite import create_utc_sqlite_engine
from yuxi.infrastructure.postgres.base import Base
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.identity.models import User
from yuxi.modules.system.repositories.dashboard import DashboardRepository
from yuxi.modules.system.services.dashboard import DashboardService
from yuxi.shared.datetime import UTC

pytestmark = [pytest.mark.unit]

# 固定查询时刻：东八区 2026-10-07 18:00
NOW = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)
TODAY = (NOW + timedelta(hours=8)).date().isoformat()


def _user(uid: str) -> User:
    return User(
        username=f"user-{uid}",
        uid=uid,
        password_hash="$argon2id$placeholder",
        role="user",
        created_at=NOW - timedelta(days=90),
        is_deleted=0,
    )


def _agent(slug: str) -> Agent:
    return Agent(
        slug=slug,
        backend_id=f"backend-{slug}",
        name=slug,
        visibility="shared",
        pics=[],
        config_json={},
        share_config={},
    )


def _session(thread_id: str, uid: str, agent_id: str, *, status: str = "active", updated_at: datetime = NOW) -> Session:
    return Session(
        thread_id=thread_id,
        tree_root_thread_id=thread_id,
        uid=uid,
        agent_id=agent_id,
        project_id="p-1",
        status=status,
        created_at=updated_at,
        updated_at=updated_at,
    )


@pytest_asyncio.fixture()
async def activity_db():
    engine = create_utc_sqlite_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        db.add_all([_user(uid) for uid in ("orphan-only", "with-agent", "deleted-only", "stale-only")])
        db.add(_agent("chat-assistant"))
        db.add_all(
            [
                # agents 表里没有这一行：用户当天照样算活跃
                _session("t-orphan", "orphan-only", "orphan-agent", updated_at=NOW - timedelta(hours=1)),
                _session("t-agent", "with-agent", "chat-assistant", updated_at=NOW - timedelta(hours=2)),
                # 当天只剩已删除会话 → 不计
                _session("t-deleted", "deleted-only", "chat-assistant", status="deleted"),
                # 45 天前的会话 → 24h/30d 都不计，但当日日活桶里必须有他
                _session("t-stale", "stale-only", "orphan-agent", updated_at=NOW - timedelta(days=45)),
            ]
        )
        await db.commit()
        yield db
    await engine.dispose()


async def test_active_counts_users_whose_agent_row_is_missing(activity_db):
    stats = await DashboardService(activity_db).get_user_activity_stats(now=NOW)

    assert stats["active_users_24h"] == 2
    assert stats["active_users_30d"] == 2
    by_date = {item["date"]: item["active_users"] for item in stats["daily_active_users"]}
    assert by_date[TODAY] == 2
    assert by_date[(NOW - timedelta(days=45) + timedelta(hours=8)).date().isoformat()] == 1


async def test_deleted_only_sessions_stay_out_of_active_counts(activity_db):
    stats = await DashboardService(activity_db).get_user_activity_stats(now=NOW)

    assert stats["active_users_24h"] == 2
    assert stats["total_users"] == 4


def _message(session_record_id: int, *, role: str = "user", created_at: datetime = NOW) -> Message:
    return Message(
        session_record_id=session_record_id,
        role=role,
        content="hi",
        created_at=created_at,
    )


@pytest_asyncio.fixture()
async def stats_db():
    """一个用户只有未登记 slug 的会话（agents 表无该行），另一个用户有登记智能体会话。"""
    engine = create_utc_sqlite_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        db.add_all([_user("orphan-only"), _user("with-agent")])
        db.add(_agent("chat-assistant"))
        db.add_all(
            [
                _session("t-orphan", "orphan-only", "orphan-agent", updated_at=NOW - timedelta(hours=1)),
                _session("t-agent", "with-agent", "chat-assistant", updated_at=NOW - timedelta(hours=2)),
            ]
        )
        await db.flush()
        by_thread = {row.thread_id: row.id for row in (await db.execute(select(Session.thread_id, Session.id))).all()}
        db.add_all(
            [
                _message(by_thread["t-orphan"]),
                _message(by_thread["t-orphan"], role="assistant"),
                _message(by_thread["t-agent"]),
            ]
        )
        await db.commit()
        yield db
    await engine.dispose()


async def test_basic_stats_count_sessions_of_unregistered_agents(stats_db):
    stats = await DashboardService(stats_db).get_basic_stats()

    assert stats["total_sessions"] == 2
    assert stats["total_messages"] == 3


async def test_agent_session_distribution_keeps_unregistered_agent(stats_db):
    payload = await DashboardService(stats_db).get_agent_session_distribution(time_range="all")

    assert payload["total_threads"] == 2
    by_id = {item["agent_id"]: item for item in payload["agent_distribution"]}
    assert by_id["orphan-agent"]["agent_name"] == "未登记智能体（orphan-agent）"
    assert by_id["orphan-agent"]["thread_count"] == 1
    assert by_id["chat-assistant"]["agent_name"] == "chat-assistant"


async def test_agent_analytics_adds_unregistered_series(stats_db):
    payload = await DashboardService(stats_db).get_agent_analytics()

    assert payload["agent_names"]["orphan-agent"] == "未登记智能体（orphan-agent）"
    assert {item["agent_id"] for item in payload["agent_session_counts"]} == {"orphan-agent", "chat-assistant"}
    assert next(i for i in payload["agent_session_counts"] if i["agent_id"] == "orphan-agent")["session_count"] == 1
    # 两口径分别钉住：总数只数登记智能体，未登记 slug 只进分组与名称表。
    assert payload["total_agents"] == 1


async def test_call_timeseries_agents_series_uses_unregistered_name():
    """调用时序的 agents 指标：悬空 slug 系列名与分布/分析同口径，不裸显 slug。"""
    engine = create_utc_sqlite_engine()
    # 时序分组用 PG 的 timezone/to_char；SQLite 侧按 support/sqlite 的约定只做逻辑钉桩，
    # timezone 取恒等、to_char 取日期前缀，真实时间语义由 PostgreSQL 集成用例证明。
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _pg_time_functions(dbapi_conn, _record):
        dbapi_conn.create_function("timezone", 2, lambda _tz, value: value)
        dbapi_conn.create_function("to_char", 2, lambda value, _fmt: str(value)[:10])

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            db.add(_user("orphan-only"))
            db.add(_session("t-orphan", "orphan-only", "orphan-agent", updated_at=NOW - timedelta(hours=1)))
            await db.commit()
            payload = await DashboardRepository(db).get_call_timeseries(metric_type="agents", time_range="7days", now=NOW)
        assert "orphan-agent" in payload["categories"]
        assert payload["agent_names"]["orphan-agent"] == "未登记智能体（orphan-agent）"
    finally:
        await engine.dispose()
