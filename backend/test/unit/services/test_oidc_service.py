from __future__ import annotations

import os
from urllib.parse import unquote

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("OPENAI_API_KEY", "dummy")

from yuxi.services import oidc_service
from yuxi.storage.postgres.models_business import User


pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def oidc_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def _create_user(session, uid: str = "alice") -> User:
    user = User(username="alice", uid=uid, password_hash="x", role="user", is_deleted=0)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _create_end_user(session) -> User:
    """构造具有真实绑定关系的 Public API 终端用户。"""
    owner = await _create_user(session)
    user = User(
        username="visitor",
        uid="endusr_test_visitor",
        password_hash="!disabled",
        role="user",
        user_kind="end_user",
        owner_user_id=owner.id,
        app_id="test-app",
        end_user_id="visitor",
        is_deleted=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_find_user_by_oidc_sub_resolves_placeholder_when_sub_contains_colon(oidc_session):
    user = await _create_user(oidc_session)

    await oidc_service._create_oidc_binding_placeholder(oidc_session, "tenant:user", user)

    resolved = await oidc_service.find_user_by_oidc_sub(oidc_session, "tenant:user")

    assert resolved is not None
    assert resolved.id == user.id
    assert resolved.uid == user.uid
    assert resolved.is_deleted == 0


async def test_find_deleted_oidc_user_by_sub_resolves_deleted_target_when_sub_contains_colon(oidc_session):
    user = await _create_user(oidc_session)
    user.is_deleted = 1
    await oidc_session.commit()

    await oidc_service._create_oidc_binding_placeholder(oidc_session, "tenant:user", user)

    resolved = await oidc_service.find_deleted_oidc_user_by_sub(oidc_session, "tenant:user")

    assert resolved is not None
    assert resolved.id == user.id
    assert resolved.uid == user.uid
    assert resolved.is_deleted == 1


@pytest.mark.parametrize("identity", ["human", "end_user", "deleted_end_user"])
async def test_oidc_callback_allows_only_human_binding_when_sub_contains_colon(oidc_session, monkeypatch, identity):
    user = await (_create_user(oidc_session) if identity == "human" else _create_end_user(oidc_session))
    await oidc_service._create_oidc_binding_placeholder(oidc_session, "tenant:user", user)
    if identity == "deleted_end_user":
        user.is_deleted = 1
        await oidc_session.commit()

    monkeypatch.setattr(oidc_service.oidc_config, "enabled", True)
    monkeypatch.setattr(oidc_service.oidc_config, "client_id", "cid")
    monkeypatch.setattr(oidc_service.oidc_config, "client_secret", "secret")
    monkeypatch.setattr(oidc_service.oidc_config, "token_endpoint", "https://example/token")
    monkeypatch.setattr(oidc_service.oidc_config, "authorization_endpoint", "https://example/auth")
    monkeypatch.setattr(oidc_service.oidc_config, "userinfo_endpoint", "https://example/userinfo")
    monkeypatch.setattr(oidc_service.oidc_config, "use_raw_username", True)
    monkeypatch.setattr(oidc_service.oidc_config, "auto_create_user", True)

    monkeypatch.setattr(
        oidc_service.OIDCUtils,
        "verify_state",
        classmethod(lambda cls, state: {"redirect_path": "/"}),
    )

    async def fake_exchange(cls, code):
        return {"access_token": "token"}

    async def fake_userinfo(cls, access_token):
        return {"sub": "tenant:user", "preferred_username": user.uid}

    async def fake_log_operation(db, user_id, operation, request=None):
        return None

    monkeypatch.setattr(oidc_service.OIDCUtils, "exchange_code_for_token", classmethod(fake_exchange))
    monkeypatch.setattr(oidc_service.OIDCUtils, "get_userinfo", classmethod(fake_userinfo))
    monkeypatch.setattr(oidc_service, "log_operation", fake_log_operation)

    response = await oidc_service.oidc_callback_handler("dummy-code", "dummy-state", oidc_session)

    assert response.status_code == 302
    location = unquote(response.headers["location"])
    if identity == "human":
        assert location.startswith("/auth/oidc/callback?code=")
    else:
        assert location.startswith("/login?oidc_error=")
        await oidc_session.refresh(user)
        assert user.is_deleted == (identity == "deleted_end_user")


async def test_oidc_lookup_creation_and_restore_reject_end_user(oidc_session, monkeypatch):
    """历史 OIDC 绑定不能找到、创建凭据或恢复终端用户。"""
    user = await _create_end_user(oidc_session)
    await oidc_service._create_oidc_binding_placeholder(oidc_session, "tenant:user", user)
    assert await oidc_service.find_user_by_oidc_sub(oidc_session, "tenant:user") is None

    monkeypatch.setattr(oidc_service.oidc_config, "use_raw_username", True)
    info = {"sub": "tenant:user", "name": user.uid, "username": user.uid}
    with pytest.raises(HTTPException) as error:
        await oidc_service.create_oidc_user(oidc_session, info)
    assert error.value.status_code == 403

    user.is_deleted = 1
    await oidc_session.commit()
    assert await oidc_service.find_deleted_oidc_user_by_sub(oidc_session, "tenant:user") is None
    with pytest.raises(HTTPException) as error:
        await oidc_service.restore_deleted_oidc_user(oidc_session, user, info)
    assert error.value.status_code == 403
    await oidc_session.refresh(user)
    assert user.is_deleted == 1
