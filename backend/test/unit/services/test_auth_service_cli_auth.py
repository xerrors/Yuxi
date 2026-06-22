from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.auth_service import (
    CLIAuthError,
    approve_cli_auth_session,
    create_cli_auth_session,
    exchange_cli_auth_token,
    get_cli_auth_session_for_user,
)
from yuxi.storage.postgres.models_business import Base, Department, User

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        dept = Department(name="默认部门")
        user = User(
            username="Admin",
            uid="admin",
            password_hash="$argon2id$placeholder",
            role="superadmin",
            department=dept,
        )
        db.add_all([dept, user])
        await db.commit()
        await db.refresh(user)
        yield db, user
    await engine.dispose()


async def test_cli_auth_session_pending_then_exchange(session):
    db, user = session
    auth_session, device_code = await create_cli_auth_session(db)

    assert device_code.startswith("yxcli_")
    assert auth_session.user_code

    with pytest.raises(CLIAuthError) as pending:
        await exchange_cli_auth_token(db, device_code)
    assert pending.value.code == "authorization_pending"

    loaded = await get_cli_auth_session_for_user(db, auth_session.user_code)
    assert loaded.status == "pending"

    await approve_cli_auth_session(db, auth_session.user_code, user)
    token_data = await exchange_cli_auth_token(db, device_code)

    assert token_data["secret"].startswith("yxkey_")
    assert token_data["api_key"]["user_id"] == user.id
    assert token_data["user"]["uid"] == "admin"


async def test_cli_auth_session_token_exchange_is_single_use(session):
    db, user = session
    auth_session, device_code = await create_cli_auth_session(db)

    await approve_cli_auth_session(db, auth_session.user_code, user)
    await exchange_cli_auth_token(db, device_code)

    with pytest.raises(CLIAuthError) as consumed:
        await exchange_cli_auth_token(db, device_code)
    assert consumed.value.code == "already_consumed"


async def test_cli_auth_session_rejects_unknown_user_code(session):
    db, _user = session

    with pytest.raises(CLIAuthError) as missing:
        await get_cli_auth_session_for_user(db, "NOPE-NOPE")
    assert missing.value.code == "not_found"
