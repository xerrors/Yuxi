from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.auth_router import delete_user
from server.routers.user_router import APIKeyCreate, create_api_key
from server.utils.auth_middleware import _verify_api_key
from yuxi.repositories import user_repository as user_repository_module
from yuxi.repositories.user_repository import UserRepository
from yuxi.storage.postgres.models_business import APIKey, Base, Department, User
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
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
        dept_b_admin = User(
            username="Dept B Admin",
            uid="dept_b_admin",
            password_hash="$argon2id$placeholder",
            role="admin",
            department=dept_b,
        )
        regular_user = User(
            username="Regular",
            uid="regular",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept_a,
        )
        deleted_user = User(
            username="Deleted",
            uid="deleted",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept_a,
            is_deleted=1,
        )
        db.add_all([dept_a, dept_b, superadmin, dept_b_admin, regular_user, deleted_user])
        await db.commit()
        for item in [dept_a, dept_b, superadmin, dept_b_admin, regular_user, deleted_user]:
            await db.refresh(item)
        yield {
            "db": db,
            "dept_a": dept_a,
            "dept_b": dept_b,
            "superadmin": superadmin,
            "dept_b_admin": dept_b_admin,
            "regular_user": regular_user,
            "deleted_user": deleted_user,
        }
    await engine.dispose()


async def test_api_key_rejects_deleted_bound_user_without_department_or_superadmin_fallback(session):
    db = session["db"]
    secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="deleted user key",
        user_id=session["deleted_user"].id,
        department_id=session["dept_b"].id,
        created_by=str(session["deleted_user"].id),
    )
    db.add(api_key)
    await db.commit()

    user, verified_key = await _verify_api_key(secret, db)

    assert user is None
    assert verified_key is None


async def test_department_only_api_key_does_not_fallback_to_superadmin(session):
    db = session["db"]
    empty_dept = Department(name="No Admin Dept")
    db.add(empty_dept)
    await db.commit()
    await db.refresh(empty_dept)

    secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    db.add(
        APIKey(
            key_hash=key_hash,
            key_prefix=key_prefix,
            name="department key",
            department_id=empty_dept.id,
            created_by=str(session["superadmin"].id),
        )
    )
    await db.commit()

    user, verified_key = await _verify_api_key(secret, db)

    assert user is None
    assert verified_key is None


async def test_create_api_key_rejects_mismatched_department(session):
    db = session["db"]

    with pytest.raises(HTTPException) as exc:
        await create_api_key(
            APIKeyCreate(name="wrong department", department_id=session["dept_b"].id),
            current_user=session["regular_user"],
            db=db,
        )

    assert exc.value.status_code == 403


async def test_create_api_key_allows_current_user_department(session):
    db = session["db"]

    response = await create_api_key(
        APIKeyCreate(name="own department", department_id=session["dept_a"].id),
        current_user=session["regular_user"],
        db=db,
    )

    assert response.api_key.user_id == session["regular_user"].id
    assert response.api_key.department_id == session["dept_a"].id
    assert response.secret.startswith(response.api_key.key_prefix)


async def test_delete_user_disables_owned_api_keys(session):
    db = session["db"]
    _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="owned key",
        user_id=session["regular_user"].id,
        created_by=str(session["regular_user"].id),
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    result = await delete_user(session["regular_user"].id, None, session["superadmin"], db)
    await db.refresh(api_key)

    assert result["success"] is True
    assert api_key.is_enabled is False


async def test_user_repository_soft_delete_disables_owned_api_keys(session, monkeypatch):
    db = session["db"]
    _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="repository owned key",
        user_id=session["regular_user"].id,
        created_by=str(session["regular_user"].id),
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    @asynccontextmanager
    async def fake_session_context():
        yield db
        await db.commit()

    monkeypatch.setattr(user_repository_module.pg_manager, "get_async_session_context", fake_session_context)

    assert await UserRepository().soft_delete(session["regular_user"].id) is True
    await db.refresh(api_key)

    assert api_key.is_enabled is False
