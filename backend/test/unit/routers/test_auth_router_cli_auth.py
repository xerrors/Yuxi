from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.auth_router import auth
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.storage.postgres.models_business import Base, Department, User

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def app_client():
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

        app = FastAPI()
        app.include_router(auth, prefix="/api")

        async def override_db():
            yield db

        async def override_user():
            return user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_required_user] = override_user

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    await engine.dispose()


async def test_auth_router_cli_auth_create_approve_and_exchange(app_client):
    create_response = await app_client.post("/api/auth/cli/sessions", json={})
    assert create_response.status_code == 200, create_response.text
    session = create_response.json()
    assert session["verification_uri"] == "/auth/cli/authorize"

    pending_response = await app_client.post(
        "/api/auth/cli/sessions/token", json={"device_code": session["device_code"]}
    )
    assert pending_response.status_code == 400
    assert pending_response.json()["detail"]["error"] == "authorization_pending"

    read_response = await app_client.get(f"/api/auth/cli/sessions/{session['user_code']}")
    assert read_response.status_code == 200
    assert read_response.json()["status"] == "pending"

    approve_response = await app_client.post(f"/api/auth/cli/sessions/{session['user_code']}/approve")
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"

    token_response = await app_client.post("/api/auth/cli/sessions/token", json={"device_code": session["device_code"]})
    assert token_response.status_code == 200, token_response.text
    token_data = token_response.json()
    assert token_data["secret"].startswith("yxkey_")
    assert token_data["user"]["uid"] == "admin"
