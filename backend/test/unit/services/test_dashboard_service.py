"""Unit tests for DashboardService and Thread analytics."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test.support.sqlite import create_utc_sqlite_engine
from yuxi.infrastructure.postgres.base import Base
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.identity.models import Department, User
from yuxi.modules.system.services.dashboard import DashboardService
from yuxi.shared.datetime import UTC, utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def dashboard_db():
    engine = create_utc_sqlite_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        dept = Department(name="Engineering")
        superadmin = User(
            username="Super Admin",
            uid="uid-superadmin",
            password_hash="$argon2id$placeholder",
            role="superadmin",
            department=dept,
        )
        user1 = User(
            username="Alice",
            uid="uid-alice",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept,
        )
        user2 = User(
            username="Bob",
            uid="uid-bob",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept,
        )
        deleted_user = User(
            username="Deleted User",
            uid="uid-deleted",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept,
            is_deleted=1,
        )

        agent1 = Agent(
            visibility="shared",
            slug="agent-helper",
            backend_id="b-1",
            name="Helper Agent",
            share_config={},
        )
        agent2 = Agent(
            visibility="shared",
            slug="agent-coder",
            backend_id="b-2",
            name="Coder Agent",
            share_config={},
        )

        now = utc_now()
        yesterday = now - timedelta(days=1)

        conv1 = Session(
            thread_id="thread-101",
            project_id="p-1",
            uid="uid-alice",
            agent_id="agent-helper",
            title="Alice Helper Query",
            status="active",
            is_pinned=True,
            created_at=yesterday,
            updated_at=now,
        )
        conv2 = Session(
            thread_id="thread-102",
            project_id="p-2",
            uid="uid-bob",
            agent_id="agent-coder",
            title="Bob Coder Task",
            status="active",
            is_pinned=False,
            created_at=now,
            updated_at=now,
        )
        conv3 = Session(
            thread_id="thread-103",
            project_id="p-3",
            uid="uid-alice",
            agent_id="agent-coder",
            title="Alice Python Debug",
            status="archived",
            is_pinned=False,
            created_at=yesterday,
            updated_at=yesterday,
        )
        deleted_session = Session(
            thread_id="thread-deleted",
            project_id="p-deleted",
            uid="uid-alice",
            agent_id="agent-helper",
            title="Deleted agent_session",
            status="deleted",
            created_at=yesterday,
            updated_at=now,
        )
        deleted_user_session = Session(
            thread_id="thread-deleted-user",
            project_id="p-deleted-user",
            uid="uid-deleted",
            agent_id="agent-helper",
            title="Deleted user agent_session",
            status="active",
            created_at=yesterday,
            updated_at=now,
        )
        missing_agent_session = Session(
            thread_id="thread-missing-agent",
            project_id="p-missing-agent",
            uid="uid-alice",
            agent_id="removed-agent",
            title="Removed agent agent_session",
            status="active",
            created_at=yesterday,
            updated_at=now,
        )
        cooperation_session = Session(
            thread_id="thread-cooperation",
            project_id="p-cooperation",
            uid="uid-alice",
            agent_id="agent-helper",
            title="Cooperation session",
            status="active",
            created_at=yesterday,
            updated_at=now,
        )

        msg1 = Message(agent_session=conv1, role="user", content="Hello", created_at=yesterday)
        msg2 = Message(agent_session=conv1, role="assistant", content="Hi there!", created_at=yesterday)
        msg3 = Message(agent_session=conv2, role="user", content="Write code", created_at=now)
        msg4 = Message(
            agent_session=conv2,
            role="assistant",
            content="Here is code",
            created_at=now,
            extra_metadata={"usage_metadata": {"input_tokens": 5, "output_tokens": 3}},
        )
        hidden_model_audit = Message(
            agent_session=conv2,
            role="assistant",
            content="Intermediate model output",
            message_type="model_audit",
            operation_id="model-audit-1",
            execution_status="completed",
            created_at=now,
            extra_metadata={"usage_metadata": {"input_tokens": 100, "output_tokens": 100}},
        )
        removed_agent_message = Message(
            agent_session=missing_agent_session,
            role="assistant",
            content="Historical removed agent output",
            created_at=now,
        )

        db.add_all(
            [
                dept,
                superadmin,
                user1,
                user2,
                deleted_user,
                agent1,
                agent2,
                conv1,
                conv2,
                conv3,
                deleted_session,
                deleted_user_session,
                missing_agent_session,
                cooperation_session,
                msg1,
                msg2,
                msg3,
                msg4,
                hidden_model_audit,
                removed_agent_message,
            ]
        )
        await db.flush()
        tool1 = Message(
            session_record_id=msg4.session_record_id,
            turn_id=msg4.turn_id,
            run_id=msg4.run_id,
            role="tool",
            message_type="tool_audit",
            operation_id="bash",
            content="",
            execution_status="completed",
            started_at=now,
            extra_metadata={"source_model_message_id": msg4.id, "tool_name": "bash", "input": {}},
            created_at=now,
        )
        removed_agent_tool = Message(
            session_record_id=removed_agent_message.session_record_id,
            turn_id=removed_agent_message.turn_id,
            run_id=removed_agent_message.run_id,
            role="tool",
            message_type="tool_audit",
            operation_id="legacy_tool",
            content="",
            execution_status="completed",
            started_at=now,
            extra_metadata={"source_model_message_id": removed_agent_message.id, "tool_name": "legacy_tool", "input": {}},
            created_at=now,
        )
        db.add_all([tool1, removed_agent_tool])
        await db.commit()
        yield db
    await engine.dispose()


async def test_dashboard_service_basic_stats(dashboard_db):
    service = DashboardService(dashboard_db)
    stats = await service.get_basic_stats()

    # 指向已删除智能体的会话同样计入：会话是否存在与 agent 行无关
    assert stats["total_sessions"] == 5
    assert stats["active_sessions"] == 4
    assert stats["total_messages"] == 5
    assert stats["total_users"] == 3
    assert "feedback_stats" not in stats

    tool_stats = await service.get_tool_call_stats()
    assert tool_stats["total_calls"] == 2  # 含未登记智能体会话的工具调用

    user_stats = await service.get_user_activity_stats()
    assert len(user_stats["daily_active_users"]) == 120
    assert user_stats["daily_active_users"][0]["date"] < user_stats["daily_active_users"][-1]["date"]


async def test_agent_analytics_omits_removed_top_performers_contract(dashboard_db):
    """智能体统计保留概览字段且不再生成 TOP 5 排行。"""
    analytics = await DashboardService(dashboard_db).get_agent_analytics()

    assert set(analytics) == {
        "total_agents",
        "agent_session_counts",
        "agent_tool_usage",
        "agent_names",
    }
    # total_agents 只数登记智能体；未登记 slug 仅进分组结果与 agent_names（两口径分别断言）。
    assert analytics["total_agents"] == 2
    assert analytics["agent_names"] == {
        "agent-helper": "Helper Agent",
        "agent-coder": "Coder Agent",
        "removed-agent": "未登记智能体（removed-agent）",
    }


async def test_tool_statistics_count_started_execution_statuses(dashboard_db):
    """审批拒绝不计执行，运行中与中断不冒充失败。"""
    session = await dashboard_db.scalar(select(Session).where(Session.thread_id == "thread-102"))
    assert session is not None
    now = utc_now()
    for status, started, source in [
        ("running", now, None),
        ("failed", now, None),
        ("interrupted", now, None),
        ("failed", None, "approval_rejection"),
    ]:
        dashboard_db.add(
            Message(
                session_record_id=session.id,
                role="tool",
                message_type="tool_audit",
                content="",
                execution_status=status,
                started_at=started,
                created_at=now,
                extra_metadata={"tool_name": source or status, "source": source},
            )
        )
    await dashboard_db.commit()

    stats = await DashboardService(dashboard_db).get_tool_call_stats()
    assert stats["total_calls"] == 5  # 未登记智能体的会话调用同样计入
    assert stats["successful_calls"] == 2
    assert stats["failed_calls"] == 1
    assert stats["success_rate"] == 40.0
    assert stats["tool_error_distribution"] == {"failed": 1}
    assert {tool["tool_name"] for tool in stats["most_used_tools"]} == {"bash", "running", "failed", "interrupted", "legacy_tool"}


async def test_dashboard_service_thread_analytics(dashboard_db):
    service = DashboardService(dashboard_db)
    analytics = await service.get_thread_analytics(time_range="7days")

    summary = analytics["summary"]
    assert summary["total_threads"] == 5  # 未登记智能体的会话不再被过滤掉
    assert summary["active_threads"] >= 1
    assert summary["pinned_threads"] == 1
    assert summary["total_messages"] == 5
    assert summary["total_tokens"] is None  # 无 Run 用量事实时保留未知
    assert summary["token_usage_complete"] is False
    assert summary["avg_messages_per_thread"] > 0
    assert summary["avg_tokens_per_thread"] is None

    assert len(analytics["daily_trends"]) == 7

    depth = analytics["depth_distribution"]
    assert depth["0 条"] == 2  # conv3 无消息
    assert depth["1-2 条"] == 3  # conv1/conv2 各 2 条，外加未登记智能体的会话
    assert depth["3-5 条"] == 0
    assert depth["6-10 条"] == 0

    agents = analytics["agent_distribution"]
    assert len(agents) == 3  # 含"未登记智能体（removed-agent）"系列
    coder_stat = next(a for a in agents if a["agent_id"] == "agent-coder")
    assert coder_stat["thread_count"] == 2
    assert coder_stat["agent_name"] == "Coder Agent"

    top_users = analytics["top_users"]
    assert len(top_users) >= 2
    alice_stat = next(u for u in top_users if u["uid"] == "uid-alice")
    assert alice_stat["username"] == "Alice"
    assert alice_stat["thread_count"] == 4  # 含她那条指向已删除智能体的会话

    assert analytics["status_distribution"]["active"] == 4  # 含未登记智能体的活跃会话
    assert analytics["status_distribution"]["archived"] == 1

    coder_only = await service.get_thread_analytics(time_range="7days", agent_id="agent-coder")
    assert coder_only["summary"]["total_threads"] == 2
    assert coder_only["summary"]["total_messages"] == 2
    assert [item["agent_id"] for item in coder_only["agent_distribution"]] == ["agent-coder"]
    assert coder_only["status_distribution"] == {"active": 1, "archived": 1}
    assert {item["uid"] for item in coder_only["top_users"]} == {"uid-alice", "uid-bob"}


async def test_thread_analytics_groups_daily_trends_by_shanghai_date(dashboard_db):
    service = DashboardService(dashboard_db)
    fixed_now = datetime(2026, 8, 24, 1, 0)
    baseline = await service.repo.get_thread_analytics(time_range="7days", now=fixed_now)
    baseline_by_date = {item["date"]: item for item in baseline["daily_trends"]}

    boundary_session = Session(
        thread_id="thread-shanghai-boundary",
        project_id="p-boundary",
        uid="uid-alice",
        agent_id="agent-helper",
        title="Shanghai boundary",
        status="active",
        created_at=datetime(2026, 8, 23, 16, 30),
        updated_at=datetime(2026, 8, 23, 16, 30),
    )
    boundary_message = Message(
        agent_session=boundary_session,
        role="user",
        content="After Shanghai midnight",
        created_at=datetime(2026, 8, 23, 16, 30),
    )
    dashboard_db.add_all([boundary_session, boundary_message])
    await dashboard_db.commit()

    analytics = await service.repo.get_thread_analytics(time_range="7days", now=fixed_now)
    trend_by_date = {item["date"]: item for item in analytics["daily_trends"]}

    assert trend_by_date["2026-08-24"]["new_threads"] == baseline_by_date["2026-08-24"]["new_threads"] + 1
    assert trend_by_date["2026-08-24"]["active_threads"] == baseline_by_date["2026-08-24"]["active_threads"] + 1
    assert trend_by_date["2026-08-24"]["message_count"] == baseline_by_date["2026-08-24"]["message_count"] + 1


async def test_thread_analytics_query_count_does_not_grow_with_time_range(dashboard_db):
    service = DashboardService(dashboard_db)
    engine = dashboard_db.bind.sync_engine
    statement_counts = []
    current_count = 0

    def count_statement(*_args):
        nonlocal current_count
        current_count += 1

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        await service.get_thread_analytics(time_range="7days")
        statement_counts.append(current_count)
        current_count = 0
        await service.get_thread_analytics(time_range="90days")
        statement_counts.append(current_count)
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert statement_counts[0] == statement_counts[1]
    assert statement_counts[0] <= 12


async def test_dashboard_service_list_sessions_search(dashboard_db):
    service = DashboardService(dashboard_db)

    all_convs = await service.list_sessions(limit=20)
    assert all_convs["total"] == 6
    assert len(all_convs["items"]) == 6
    assert all(item["status"] != "deleted" for item in all_convs["items"])
    deleted_convs = await service.list_sessions(status="deleted", limit=20)
    assert deleted_convs["total"] == 1
    assert deleted_convs["items"][0]["thread_id"] == "thread-deleted"
    deleted_item = next(item for item in all_convs["items"] if item["thread_id"] == "thread-deleted-user")
    missing_agent_item = next(item for item in all_convs["items"] if item["thread_id"] == "thread-missing-agent")
    assert deleted_item["user_deleted"] is True
    assert missing_agent_item["agent_deleted"] is True

    search_result = await service.list_sessions(search="Python")
    assert search_result["total"] == 1
    assert search_result["items"][0]["thread_id"] == "thread-103"
    assert search_result["items"][0]["username"] == "Alice"
    assert search_result["items"][0]["agent_name"] == "Coder Agent"

    active_only = await service.list_sessions(status="active")
    assert active_only["total"] == 5
    assert len(active_only["items"]) == 5

    options = await service.get_session_filter_options()
    assert next(item for item in options["users"] if item["uid"] == "uid-deleted")["is_deleted"] is True
    assert next(item for item in options["agents"] if item["agent_id"] == "removed-agent")["is_deleted"] is True


async def test_dashboard_audit_timestamps_carry_timezone_designator(dashboard_db):
    """审计接口对外时间必须带时区标识（约定 UTC + Z 后缀），否则前端会按本地时间误读。"""
    service = DashboardService(dashboard_db)

    sessions = await service.list_sessions(limit=20)
    assert sessions["items"]
    for item in sessions["items"]:
        assert item["created_at"].endswith("Z")
        assert item["updated_at"].endswith("Z")

    detail = await service.get_session_detail("thread-102")
    assert detail is not None
    assert detail["created_at"].endswith("Z")
    assert detail["updated_at"].endswith("Z")
    for message in detail["messages"]:
        assert message["created_at"].endswith("Z")


async def test_dashboard_service_session_detail(dashboard_db):
    service = DashboardService(dashboard_db)
    detail = await service.get_session_detail("thread-102")

    assert detail is not None
    assert detail["thread_id"] == "thread-102"
    assert detail["total_tokens"] is None  # 无 Run 用量事实时暴露未知而不是旧汇总
    assert detail["user_deleted"] is False
    assert detail["agent_deleted"] is False
    assert len(detail["messages"]) == 2
    assistant_msg = next(m for m in detail["messages"] if m["role"] == "assistant")
    assert "tool_executions" in assistant_msg
    assert assistant_msg["tool_executions"][0]["tool_name"] == "bash"


async def test_session_tokens_use_runs_and_expose_missing_usage(dashboard_db):
    """审计累加同会话 Run，忽略旧汇总并区分真实零和未知。"""
    from sqlalchemy import select
    from yuxi.modules.agents.models.runs import AgentRun
    from yuxi.modules.agents.models.turns import AgentTurn

    agent_session = (await dashboard_db.execute(select(Session).where(Session.thread_id == "thread-102"))).scalar_one()
    dashboard_db.add(
        AgentTurn(
            id="usage-turn",
            thread_id=agent_session.thread_id,
            uid=agent_session.uid,
            status="completed",
        )
    )
    await dashboard_db.flush()
    for index, usage in enumerate(
        [
            {"total": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}, "complete": True, "usage_reported_call_count": 1},
            {"total": {"input_tokens": 50, "output_tokens": 30, "total_tokens": 80}, "complete": True, "usage_reported_call_count": 1},
        ]
    ):
        dashboard_db.add(
            AgentRun(
                id=f"usage-run-{index}",
                session_record_id=agent_session.id,
                thread_id=agent_session.thread_id,
                runtime_scope_id=agent_session.thread_id,
                agent_slug=agent_session.agent_id,
                uid=agent_session.uid,
                status="yielded" if index == 0 else "completed",
                turn_id="usage-turn",
                run_type="chat" if index == 0 else "resume",
                resume_from_run_id="usage-run-0" if index else None,
                token_usage=usage,
            )
        )
        await dashboard_db.flush()
    await dashboard_db.commit()
    service = DashboardService(dashboard_db)
    detail = await service.get_session_detail(agent_session.thread_id)
    item = (await service.list_sessions(search="thread-102"))["items"][0]
    assert detail["total_tokens"] == item["total_tokens"] == 200
    assert item["token_usage_complete"] is True

    run = await dashboard_db.get(AgentRun, "usage-run-1")
    run.token_usage = {"available": False}
    await dashboard_db.commit()
    item = (await service.list_sessions(search="thread-102"))["items"][0]
    assert item["total_tokens"] == 120
    assert item["token_usage_complete"] is False

    run = await dashboard_db.get(AgentRun, "usage-run-0")
    run.token_usage = {"total": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "complete": False, "usage_reported_call_count": 0}
    await dashboard_db.commit()
    item = (await service.list_sessions(search="thread-102"))["items"][0]
    assert item["total_tokens"] is None
    assert item["token_usage_complete"] is False

    run.token_usage = {"total": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "complete": True, "usage_reported_call_count": 1}
    await dashboard_db.delete(await dashboard_db.get(AgentRun, "usage-run-1"))
    await dashboard_db.commit()
    item = (await service.list_sessions(search="thread-102"))["items"][0]
    assert item["total_tokens"] == 0
    assert item["token_usage_complete"] is True


async def test_tool_stats_preserve_rolling_boundaries_with_one_aggregate(dashboard_db):
    """滚动窗口保留下界、排除上界，单次聚合与时间桶缺口不改变统计。"""
    now = datetime(2026, 10, 15, 12)
    service = DashboardService(dashboard_db)
    baseline = await service.get_tool_call_stats(now=now)
    agent_session = await dashboard_db.scalar(select(Session).where(Session.thread_id == "thread-102"))
    for offset in [
        timedelta(),
        timedelta(microseconds=1),
        timedelta(days=1),
        timedelta(days=1, microseconds=1),
        timedelta(days=7),
        timedelta(days=7, microseconds=1),
    ]:
        dashboard_db.add(
            Message(
                session_record_id=agent_session.id,
                role="tool",
                message_type="tool_audit",
                content="",
                started_at=now - offset,
                created_at=now - offset,
                execution_status="completed",
                extra_metadata={"tool_name": "boundary"},
            )
        )
    await dashboard_db.commit()
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        """记录真实数据库执行预算，不替代业务结果断言。"""
        statements.append(statement)

    engine = dashboard_db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        stats = await service.get_tool_call_stats(now=now)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) == 1
    assert stats["total_calls"] == baseline["total_calls"] + 6
    assert stats["successful_calls"] == baseline["successful_calls"] + 6
    assert [
        after["call_count"] - before["call_count"] for after, before in zip(stats["daily_tool_calls"], baseline["daily_tool_calls"])
    ] == [1, 0, 0, 0, 0, 1, 2]


@pytest.mark.parametrize(
    "time_range,expected_threads,expected_average", [("all", 8, 1.4), ("3months", 6, 1.7), ("30days", 4, 1.8), ("7days", 2, 2.0)]
)
async def test_agent_distribution_uses_created_cohort_and_weighted_messages(dashboard_db, time_range, expected_threads, expected_average):
    """创建边界、闰月回退、空会话和消息排除共同约束同口径的摘要与排行。"""
    from zoneinfo import ZoneInfo

    template = await dashboard_db.scalar(select(Session).where(Session.thread_id == "thread-101"))
    now = datetime(2020, 5, 31, 12, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(UTC)
    dates_and_messages = [
        (datetime(2019, 1, 1), 1),
        (datetime(2020, 2, 28, 23, 59, 59, 999999), 0),
        (datetime(2020, 2, 29), 2),
        (datetime(2020, 5, 1, 23, 59, 59, 999999), 1),
        (datetime(2020, 5, 2), 3),
        (datetime(2020, 5, 24, 23, 59, 59, 999999), 0),
        (datetime(2020, 5, 25), 4),
        (datetime(2020, 5, 31), 0),
    ]
    sessions = []
    for index, (created, count) in enumerate(dates_and_messages):
        session = Session(
            thread_id=f"cohort-{index}",
            project_id=template.project_id,
            uid=template.uid,
            agent_id="agent-coder" if index == 7 else "agent-helper",
            status="active",
            created_at=created.replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(UTC),
        )
        dashboard_db.add(session)
        await dashboard_db.flush()
        sessions.append(session)
        for _ in range(count):
            dashboard_db.add(Message(session_record_id=session.id, role="assistant", content="sample", created_at=now))
    for message_type in ("model_audit", "tool_audit"):
        dashboard_db.add(Message(session_record_id=sessions[6].id, role="assistant", content="", created_at=now, message_type=message_type))
    dashboard_db.add(Message(session_record_id=sessions[6].id, role="assistant", content="future", created_at=now + timedelta(seconds=1)))
    for index, (created, status, uid) in enumerate(
        [(now + timedelta(seconds=1), "active", template.uid), (now, "deleted", template.uid), (now, "active", "uid-deleted")]
    ):
        dashboard_db.add(
            Session(
                thread_id=f"excluded-cohort-{index}",
                project_id=template.project_id,
                uid=uid,
                agent_id="agent-helper",
                status=status,
                created_at=created,
            )
        )
    await dashboard_db.flush()

    result = await DashboardService(dashboard_db).repo.get_agent_session_distribution(time_range=time_range, now=now)

    assert result["total_threads"] == expected_threads
    assert result["agent_count"] == 2
    assert result["avg_messages_per_thread"] == expected_average
    assert sum(row["thread_count"] for row in result["agent_distribution"]) == expected_threads
    assert (
        sum(row["message_count"] for row in result["agent_distribution"]) == {"all": 11, "3months": 10, "30days": 7, "7days": 4}[time_range]
    )
    empty_agent = next(row for row in result["agent_distribution"] if row["agent_id"] == "agent-coder")
    assert empty_agent["thread_count"] == 1
    assert empty_agent["avg_messages"] == 0


async def test_agent_distribution_empty_range_has_zero_summary(dashboard_db):
    """无会话范围返回可解释的零值与空排行。"""
    from yuxi.shared.datetime import UTC

    result = await DashboardService(dashboard_db).repo.get_agent_session_distribution(
        time_range="7days", now=datetime(2000, 1, 1, tzinfo=UTC)
    )
    assert result == {"total_threads": 0, "agent_count": 0, "avg_messages_per_thread": 0.0, "agent_distribution": []}
