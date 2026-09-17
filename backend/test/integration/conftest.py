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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test.live_api_cleanup import (  # noqa: E402
    cleanup_provisioned_sandboxes,
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
SANDBOX_PROVISIONER_URL = os.getenv("SANDBOX_PROVISIONER_URL", "http://sandbox-provisioner:8002").rstrip("/")
SANDBOX_PROVISIONER_TOKEN = os.getenv("SANDBOX_PROVISIONER_TOKEN", "")


@pytest.fixture(scope="session", autouse=True)
def ensure_live_api_schema():
    if not ADMIN_LOGIN or not ADMIN_PASSWORD:
        return

    async def verify_schema_version() -> None:
        from yuxi.storage.postgres.manager import pg_manager

        pg_manager.initialize()
        try:
            await pg_manager.require_current_schema()
        finally:
            # 池必须在创建它的临时事件循环退出前关闭。
            await pg_manager.close()

    anyio.run(verify_schema_version)


def _require_admin_credentials() -> tuple[str, str]:
    if not ADMIN_LOGIN or not ADMIN_PASSWORD:
        pytest.skip("Integration credentials are not configured via TEST_USERNAME / TEST_PASSWORD.")
    return ADMIN_LOGIN, ADMIN_PASSWORD


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
                    "Super admin account has not been initialized. Complete `/api/auth/initialize` before "
                    "running integration tests."
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
def cleanup_test_knowledge_resources():
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
                    raise RuntimeError(
                        f"Test resource cleanup login failed (status={response.status_code}): {response.text}"
                    )
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


def _cleanup_provisioned_sandboxes() -> None:
    if not SANDBOX_PROVISIONER_TOKEN:
        raise RuntimeError("SANDBOX_PROVISIONER_TOKEN is required for integration sandbox cleanup")

    async def run_cleanup() -> None:
        headers = {"Authorization": f"Bearer {SANDBOX_PROVISIONER_TOKEN}"}
        async with httpx.AsyncClient(
            base_url=SANDBOX_PROVISIONER_URL,
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            await cleanup_provisioned_sandboxes(client, headers)

    anyio.run(run_cleanup)


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    _cleanup_provisioned_sandboxes()
    yield
    _cleanup_provisioned_sandboxes()


@pytest_asyncio.fixture(scope="function")
async def standard_user(test_client: httpx.AsyncClient, admin_headers: dict[str, str]) -> AsyncGenerator[dict, None]:
    username = f"pytest_user_{uuid.uuid4().hex[:8]}"
    password = f"Pw!{uuid.uuid4().hex[:8]}"

    # 用户隔离重构后所有登录用户必须绑定部门，创建时显式指定一个已存在部门
    dept_response = await test_client.get("/api/departments", headers=admin_headers)
    if dept_response.status_code != 200 or not dept_response.json():
        pytest.fail(f"No department available to bind standard user: {dept_response.text}")
    department_id = dept_response.json()[0]["id"]

    response = await test_client.post(
        "/api/auth/users",
        json={"username": username, "password": password, "role": "user", "department_id": department_id},
        headers=admin_headers,
    )
    if response.status_code != 200:
        pytest.fail(f"Failed to create standard user (status={response.status_code}): {response.text}")

    user_payload = response.json()
    login_response = await test_client.post(
        "/api/auth/token",
        data={"username": user_payload["uid"], "password": password},
    )
    if login_response.status_code != 200:
        pytest.fail(
            f"Failed to authenticate as standard user (status={login_response.status_code}): {login_response.text}"
        )

    access_token = login_response.json().get("access_token")
    if not access_token:
        pytest.fail("Standard user login succeeded but no access token was returned.")

    try:
        yield {
            "user": user_payload,
            "password": password,
            "headers": {"Authorization": f"Bearer {access_token}"},
        }
    finally:
        await cleanup_test_chat_resources(
            test_client,
            {"Authorization": f"Bearer {access_token}"},
            owner_uid=str(user_payload["uid"]),
        )
        cleanup_error = None
        for _ in range(3):
            response = await test_client.delete(f"/api/auth/users/{user_payload['id']}", headers=admin_headers)
            if response.status_code in (200, 404):
                cleanup_error = None
                break
            cleanup_error = response
            await anyio.sleep(0.3)
        if cleanup_error is not None:
            assert cleanup_error.status_code == 200, (
                f"Failed to cleanup test user {user_payload['uid']}: {cleanup_error.text}"
            )


@pytest_asyncio.fixture(scope="function")
async def knowledge_database(
    test_client: httpx.AsyncClient,
    admin_headers: dict[str, str],
) -> AsyncGenerator[dict, None]:
    import time

    unique_id = uuid.uuid4().hex
    timestamp = int(time.time() * 1000000)
    db_name = f"pytest_kb_{timestamp}_{unique_id}"
    kb_id = None

    try:
        create_response = await test_client.post(
            "/api/knowledge/databases",
            json={
                "database_name": db_name,
                "description": "Pytest managed knowledge base",
                "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
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
            pytest.fail(
                f"Failed to create knowledge database (status={create_response.status_code}): {create_response.text}"
            )

        yield db_payload

    finally:
        if kb_id:
            try:
                delete_response = await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
                if delete_response.status_code != 200:
                    print(f"Warning: Failed to cleanup knowledge database {kb_id}: {delete_response.text}")
            except Exception as exc:
                print(f"Warning: Exception during cleanup of {kb_id}: {exc}")
