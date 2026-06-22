from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.dashboard_router import (
    dashboard,
    get_all_conversations,
    get_conversation_detail,
    get_tool_call_stats,
    get_user_activity_stats,
)
from server.utils.auth_middleware import get_superadmin_user
from yuxi.storage.postgres.models_business import Base, Conversation, Department, Message, ToolCall, User
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def dashboard_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        dept_a = Department(name="Dept A")
        dept_b = Department(name="Dept B")
        superadmin = User(
            username="Super Admin",
            uid="superadmin",
            password_hash="$argon2id$placeholder",
            role="superadmin",
            department=dept_a,
        )
        admin_a = User(
            username="Admin A",
            uid="admin_a",
            password_hash="$argon2id$placeholder",
            role="admin",
            department=dept_a,
        )
        user_a = User(
            username="User A",
            uid="user_a",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept_a,
        )
        admin_b = User(
            username="Admin B",
            uid="admin_b",
            password_hash="$argon2id$placeholder",
            role="admin",
            department=dept_b,
        )
        user_b = User(
            username="User B",
            uid="user_b",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept_b,
        )
        now = utc_now_naive()
        conversation_a = Conversation(
            thread_id="thread-a",
            uid="user_a",
            agent_id="agent-shared",
            title="Dept A conversation",
            status="active",
            created_at=now,
            updated_at=now,
        )
        conversation_b = Conversation(
            thread_id="thread-b",
            uid="user_b",
            agent_id="agent-shared",
            title="Dept B conversation",
            status="active",
            created_at=now,
            updated_at=now,
        )
        message_a = Message(conversation=conversation_a, role="assistant", content="A", created_at=now)
        message_b = Message(conversation=conversation_b, role="assistant", content="B", created_at=now)
        tool_call_a = ToolCall(message=message_a, tool_name="dept_a_tool", status="success", created_at=now)
        tool_call_b = ToolCall(message=message_b, tool_name="dept_b_tool", status="success", created_at=now)
        db.add_all(
            [
                dept_a,
                dept_b,
                superadmin,
                admin_a,
                user_a,
                admin_b,
                user_b,
                conversation_a,
                conversation_b,
                message_a,
                message_b,
                tool_call_a,
                tool_call_b,
            ]
        )
        await db.commit()
        for item in [
            dept_a,
            dept_b,
            superadmin,
            admin_a,
            user_a,
            admin_b,
            user_b,
            conversation_a,
            conversation_b,
        ]:
            await db.refresh(item)
        yield {"db": db, "superadmin": superadmin, "admin_a": admin_a}
    await engine.dispose()


async def test_dashboard_routes_require_superadmin_dependency():
    dashboard_routes = [route for route in dashboard.routes if isinstance(route, APIRoute)]

    assert dashboard_routes
    for route in dashboard_routes:
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert get_superadmin_user in dependency_calls


async def test_dashboard_dependency_rejects_department_admin(dashboard_session):
    with pytest.raises(HTTPException) as exc:
        await get_superadmin_user(dashboard_session["admin_a"])

    assert exc.value.status_code == 403


async def test_conversation_list_superadmin_sees_all_departments(dashboard_session):
    response = await get_all_conversations(db=dashboard_session["db"], current_user=dashboard_session["superadmin"])

    assert {item["thread_id"] for item in response} == {"thread-a", "thread-b"}


async def test_conversation_detail_superadmin_can_view_other_department(dashboard_session):
    response = await get_conversation_detail(
        "thread-b",
        db=dashboard_session["db"],
        current_user=dashboard_session["superadmin"],
    )

    assert response["thread_id"] == "thread-b"


async def test_user_activity_stats_superadmin_include_all_departments(dashboard_session):
    stats = await get_user_activity_stats(db=dashboard_session["db"], current_user=dashboard_session["superadmin"])

    assert stats.total_users == 5
    assert stats.active_users_24h == 2
    assert stats.active_users_30d == 2


async def test_tool_stats_superadmin_include_all_departments(dashboard_session):
    stats = await get_tool_call_stats(db=dashboard_session["db"], current_user=dashboard_session["superadmin"])

    assert stats.total_calls == 2
    assert stats.successful_calls == 2
    assert {tool["tool_name"] for tool in stats.most_used_tools} == {"dept_a_tool", "dept_b_tool"}
