"""
Shared pytest fixtures for integration tests that exercise the live API service.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import anyio
import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test.live_api_cleanup import (  # noqa: E402
    cleanup_pytest_knowledge_resources,
    cleanup_test_chat_resources,
)

load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(PROJECT_ROOT / "test/.env.test", override=False)

API_BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:5050").rstrip("/")
ADMIN_LOGIN = os.getenv("TEST_USERNAME")
ADMIN_PASSWORD = os.getenv("TEST_PASSWORD")

_ADMIN_TOKEN_CACHE: str | None = None
HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


def _has_shared_integration_tests(request: pytest.FixtureRequest) -> bool:
    """存在非自管测试时才触碰共享服务。"""
    integration_root = Path(__file__).parent.resolve()
    return any(
        item.path.resolve().is_relative_to(integration_root) and item.get_closest_marker("self_managed_resources") is None
        for item in request.session.items
    )


@pytest.fixture(scope="session", autouse=True)
def load_orm_models():
    """独立测试进程显式装配全部 ORM，避免依赖其他测试的导入顺序。"""
    from yuxi.infrastructure.postgres.models import load_models

    load_models()


@pytest.fixture
def enabled_personal_assistant(monkeypatch):
    """在当前用例中启用助理并注册 Backend，结束后恢复进程原状态。"""
    from yuxi.modules.agents.runtime import agent_backends

    monkeypatch.setenv("YUXI_PERSONAL_ASSISTANT_ENABLED", "true")
    monkeypatch.setitem(agent_backends.AGENT_BACKENDS, "PersonalAssistantAgent", agent_backends.PersonalAssistantAgent)


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema(request: pytest.FixtureRequest):
    if not _has_shared_integration_tests(request) or not ADMIN_LOGIN or not ADMIN_PASSWORD:
        return

    async def verify_schema_version() -> None:
        from yuxi.infrastructure.postgres.manager import pg_manager
        from yuxi.infrastructure.postgres.schema import require_current_schema

        pg_manager.initialize()
        try:
            await require_current_schema(pg_manager)
        finally:
            # 池必须在创建它的临时事件循环退出前关闭。
            await pg_manager.close()

    anyio.run(verify_schema_version)


def _require_admin_credentials() -> tuple[str, str]:
    if not ADMIN_LOGIN or not ADMIN_PASSWORD:
        pytest.skip("Integration credentials are not configured via TEST_USERNAME / TEST_PASSWORD.")
    return ADMIN_LOGIN, ADMIN_PASSWORD


@pytest_asyncio.fixture(scope="function")
async def postgres_sessions():
    """提供当前测试专用连接池，独立回读已提交事实并在同一事件循环关闭。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"], pool_pre_ping=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        yield client


@pytest_asyncio.fixture(scope="function")
async def admin_token() -> str:
    global _ADMIN_TOKEN_CACHE

    if _ADMIN_TOKEN_CACHE:
        return _ADMIN_TOKEN_CACHE

    username, password = _require_admin_credentials()

    async with httpx.AsyncClient(
        base_url=API_BASE_URL,
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    ) as bootstrap_client:
        response = await bootstrap_client.post(
            "/api/auth/token",
            data={"username": username, "password": password},
        )

        if response.status_code == 401:
            first_run_response = await bootstrap_client.get("/api/auth/check-first-run")
            if first_run_response.status_code == 200 and first_run_response.json().get("first_run", False):
                pytest.fail(
                    "Super admin account has not been initialized. Complete `/api/auth/initialize` before running integration tests."
                )

    if response.status_code != 200:
        pytest.fail(f"Failed to authenticate as admin (status={response.status_code}): {response.text}")

    token = response.json().get("access_token")
    if not token:
        pytest.fail("Admin authentication did not return an access token.")

    _ADMIN_TOKEN_CACHE = token
    return token


@pytest.fixture(scope="function")
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_resources(request: pytest.FixtureRequest):
    if not _has_shared_integration_tests(request):
        yield
        return

    async def run_cleanup() -> None:
        global _ADMIN_TOKEN_CACHE

        if not ADMIN_LOGIN or not ADMIN_PASSWORD:
            return

        if not _ADMIN_TOKEN_CACHE:
            async with httpx.AsyncClient(
                base_url=API_BASE_URL,
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
            ) as bootstrap_client:
                response = await bootstrap_client.post(
                    "/api/auth/token",
                    data={"username": ADMIN_LOGIN, "password": ADMIN_PASSWORD},
                )
                if response.status_code != 200:
                    raise RuntimeError(f"Test resource cleanup login failed (status={response.status_code}): {response.text}")
                token = response.json().get("access_token")
                if not token:
                    raise RuntimeError("Test resource cleanup login succeeded but no access token was returned")
                _ADMIN_TOKEN_CACHE = token

        headers = {"Authorization": f"Bearer {_ADMIN_TOKEN_CACHE}"}

        async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            current_user = await client.get("/api/auth/me", headers=headers)
            if current_user.status_code != 200:
                raise RuntimeError(f"Test resource cleanup failed to read current user: {current_user.text}")
            cleanup_uid = str(current_user.json().get("uid") or "")
            if not cleanup_uid:
                raise RuntimeError("Test resource cleanup current user payload is missing uid")

            await cleanup_test_chat_resources(client, headers, owner_uid=cleanup_uid)
            await cleanup_pytest_knowledge_resources(client, headers)

    anyio.run(run_cleanup)
    yield
    anyio.run(run_cleanup)


@pytest_asyncio.fixture(scope="function")
async def knowledge_base_resource(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
) -> AsyncGenerator[dict, None]:
    import time

    from test.support.knowledge import default_embedding_model_spec

    unique_id = uuid.uuid4().hex
    timestamp = int(time.time() * 1000000)
    db_name = f"pytest_kb_{timestamp}_{unique_id}"
    kb_id = None

    try:
        create_response = await test_client.post(
            "/api/knowledge/knowledge-bases",
            json={
                "name": db_name,
                "description": "Pytest managed knowledge base",
                "embedding_model_spec": default_embedding_model_spec(),
                "kb_type": "milvus",
                "additional_params": {},
            },
            headers=admin_headers,
        )

        if create_response.status_code == 200:
            db_payload = create_response.json()
            kb_id = db_payload["kb_id"]
        elif create_response.status_code == 409:
            error_detail = create_response.json().get("detail", "")
            pytest.fail(f"Knowledge database name conflict: {error_detail}. Please clean up old test databases first.")
        else:
            pytest.fail(f"Failed to create knowledge database (status={create_response.status_code}): {create_response.text}")

        yield db_payload

    finally:
        if kb_id:
            try:
                delete_response = await test_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=admin_headers)
                if delete_response.status_code != 200:
                    print(f"Warning: Failed to cleanup knowledge database {kb_id}: {delete_response.text}")
            except Exception as exc:
                print(f"Warning: Exception during cleanup of {kb_id}: {exc}")
