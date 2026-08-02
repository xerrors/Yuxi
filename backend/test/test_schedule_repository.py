"""ScheduleRepository owner-aware 仓储方法的单元测试。"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

# 必须在 import yuxi.* 之前设置，避免 yuxi/__init__.py 中的 config 加载抛错。
os.environ.setdefault("YUXI_SKIP_APP_INIT", "1")
os.environ.setdefault("OPENAI_API_KEY", "test-dummy-key")

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from yuxi.repositories.schedule_repository import ScheduleRepository
from yuxi.storage.postgres.models_business import (
    Base as BusinessBase,
)
from yuxi.storage.postgres.models_business import ScheduleDefinition, ScheduleLog


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db_session():
    """Provide an async SQLAlchemy session backed by an isolated SQLite in-memory DB.

    Foreign-key enforcement is disabled so tests can create rows without seeding
    the full parent table graph (users / departments / agent_configs). Each test
    gets a fresh, isolated schema.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk_off(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(BusinessBase.metadata.create_all)

    Session = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with Session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(BusinessBase.metadata.drop_all)
    await engine.dispose()


async def _make_schedule(db: AsyncSession, *, schedule_id: str, user_id: str) -> ScheduleDefinition:
    sched = ScheduleDefinition(
        id=schedule_id,
        name=f"s-{schedule_id}",
        user_id=user_id,
        agent_config_id=1,
        cron_expr="0 * * * *",
        timezone="Asia/Shanghai",
        query="hi",
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    return sched


async def _make_log(db: AsyncSession, *, schedule_id: str) -> ScheduleLog:
    log = ScheduleLog(
        id=str(uuid.uuid4()),
        schedule_id=schedule_id,
        run_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        status="triggered",
        execution_status="pending",
        trigger_delay_ms=0,
        created_at=datetime.now(UTC),
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


# ---------------------------------------------------------------------------
# get_by_id_for_user
# ---------------------------------------------------------------------------


async def test_get_by_id_for_user_returns_row_when_owner_matches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s1", user_id="u1")

    result = await repo.get_by_id_for_user("s1", "u1")

    assert result is not None
    assert result.id == "s1"


async def test_get_by_id_for_user_returns_none_when_owner_mismatches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s1", user_id="u1")

    result = await repo.get_by_id_for_user("s1", "u2")

    assert result is None


async def test_get_by_id_for_user_admin_skips_owner_filter(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s1", user_id="u1")

    result = await repo.get_by_id_for_user("s1", "u2", is_admin=True)

    assert result is not None
    assert result.user_id == "u1"


# ---------------------------------------------------------------------------
# update_for_user
# ---------------------------------------------------------------------------


async def test_update_for_user_modifies_row_when_owner_matches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s2", user_id="u1")

    result = await repo.update_for_user("s2", "u1", {"name": "renamed"})

    assert result is not None
    assert result.name == "renamed"


async def test_update_for_user_returns_none_when_owner_mismatches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s2", user_id="u1")

    result = await repo.update_for_user("s2", "u2", {"name": "renamed"})

    assert result is None


async def test_update_for_user_admin_can_modify_others(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s2", user_id="u1")

    result = await repo.update_for_user("s2", "u2", {"name": "renamed"}, is_admin=True)

    assert result is not None
    assert result.name == "renamed"


# ---------------------------------------------------------------------------
# delete_for_user
# ---------------------------------------------------------------------------


async def test_delete_for_user_removes_row_when_owner_matches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s3", user_id="u1")

    deleted = await repo.delete_for_user("s3", "u1")

    assert deleted is True
    assert await repo.get_by_id("s3") is None


async def test_delete_for_user_returns_false_when_owner_mismatches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s3", user_id="u1")

    deleted = await repo.delete_for_user("s3", "u2")

    assert deleted is False
    assert (await repo.get_by_id("s3")) is not None


async def test_delete_for_user_admin_can_remove_others(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s3", user_id="u1")

    deleted = await repo.delete_for_user("s3", "u2", is_admin=True)

    assert deleted is True


# ---------------------------------------------------------------------------
# list_logs_for_user
# ---------------------------------------------------------------------------


async def test_list_logs_for_user_returns_logs_when_owner_matches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s4", user_id="u1")
    await _make_log(db_session, schedule_id="s4")

    logs = await repo.list_logs_for_user("s4", "u1", limit=20, offset=0)

    assert len(logs) == 1


async def test_list_logs_for_user_returns_empty_when_owner_mismatches(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s4", user_id="u1")
    await _make_log(db_session, schedule_id="s4")

    logs = await repo.list_logs_for_user("s4", "u2", limit=20, offset=0)

    assert logs == []


async def test_list_logs_for_user_admin_can_read_others(db_session: AsyncSession) -> None:
    repo = ScheduleRepository(db_session)
    await _make_schedule(db_session, schedule_id="s4", user_id="u1")
    await _make_log(db_session, schedule_id="s4")

    logs = await repo.list_logs_for_user("s4", "u2", limit=20, offset=0, is_admin=True)

    assert len(logs) == 1
