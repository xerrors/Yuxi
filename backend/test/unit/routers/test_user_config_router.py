from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.user_router import get_user_config, update_user_config
from yuxi.config import UserConfigSchema
from yuxi.storage.postgres.models_business import Base, Department, User

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        department = Department(name="User Config Dept")
        user_a = User(
            username="User A",
            uid="user_a",
            password_hash="$argon2id$placeholder",
            role="user",
            department=department,
        )
        user_b = User(
            username="User B",
            uid="user_b",
            password_hash="$argon2id$placeholder",
            role="user",
            department=department,
        )
        db.add_all([department, user_a, user_b])
        await db.commit()
        for user in [user_a, user_b]:
            await db.refresh(user)
        yield db, user_a, user_b
    await engine.dispose()


async def test_user_config_routes_scope_to_current_user(session):
    db, user_a, user_b = session

    own_config = await update_user_config(
        UserConfigSchema(enable_memory=True),
        current_user=user_a,
        db=db,
    )
    other_config = await get_user_config(current_user=user_b, db=db)

    assert own_config["enable_memory"] is True
    assert other_config["enable_memory"] is False
