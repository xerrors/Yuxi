"""
Shared pytest fixtures for exercising FastAPI routers over the running API service.
"""
# ruff: noqa: E402

from __future__ import annotations

import os
import json
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator

# Ensure /app is on sys.path so 'server' and 'yuxi' modules resolve correctly.
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

import anyio
import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv

# Load project and test specific environment variables.
load_dotenv(".env", override=False)
load_dotenv("test/.env.test", override=False)

API_BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:5050").rstrip("/")
ADMIN_LOGIN = os.getenv("TEST_USERNAME")
ADMIN_PASSWORD = os.getenv("TEST_PASSWORD")

assert ADMIN_LOGIN, "TEST_USERNAME is not set"
assert ADMIN_PASSWORD, "TEST_PASSWORD is not set"

_ADMIN_TOKEN_CACHE: str | None = None
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
SANDBOX_CONTAINER_PREFIX = os.getenv("YUXI_SANDBOX_CONTAINER_PREFIX", "yuxi-sandbox")


@pytest_asyncio.fixture(scope="function")
async def test_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client bound to the live API base URL."""
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        yield client


@pytest_asyncio.fixture(scope="function")
async def admin_token() -> str:
    """Authenticate with super admin credentials and cache the bearer token."""
    global _ADMIN_TOKEN_CACHE

    if _ADMIN_TOKEN_CACHE:
        return _ADMIN_TOKEN_CACHE

    if not ADMIN_LOGIN or not ADMIN_PASSWORD:
        pytest.skip("Admin credentials are not configured via environment variables.")

    async with httpx.AsyncClient(
        base_url=API_BASE_URL, timeout=HTTP_TIMEOUT, follow_redirects=True
    ) as bootstrap_client:
        response = await bootstrap_client.post(
            "/api/auth/token", data={"username": ADMIN_LOGIN, "password": ADMIN_PASSWORD}
        )

        if response.status_code == 401:
            first_run_response = await bootstrap_client.get("/api/auth/check-first-run")
            if first_run_response.status_code == 200 and first_run_response.json().get("first_run", False):
                pytest.fail(
                    "Super admin account has not been initialized. Please complete `/api/auth/initialize` "
                    "before running router tests."
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
    """Authorization headers for the super admin user."""
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_knowledge_databases():
    """
    Best-effort cleanup for leftover test knowledge databases (e.g. pytest_* / py_test*).
    """

    async def run_cleanup() -> None:
        global _ADMIN_TOKEN_CACHE

        if not ADMIN_LOGIN or not ADMIN_PASSWORD:
            return

        if not _ADMIN_TOKEN_CACHE:
            async with httpx.AsyncClient(
                base_url=API_BASE_URL, timeout=HTTP_TIMEOUT, follow_redirects=True
            ) as bootstrap_client:
                response = await bootstrap_client.post(
                    "/api/auth/token", data={"username": ADMIN_LOGIN, "password": ADMIN_PASSWORD}
                )
                if response.status_code != 200:
                    return
                token = response.json().get("access_token")
                if not token:
                    return
                _ADMIN_TOKEN_CACHE = token

        headers = {"Authorization": f"Bearer {_ADMIN_TOKEN_CACHE}"}

        async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            try:
                list_response = await client.get("/api/knowledge/databases", headers=headers)
            except Exception as e:
                print(f"Warning: Failed to list knowledge databases for cleanup: {e}")
                return

            if list_response.status_code != 200:
                return

            databases = list_response.json().get("databases", [])
            prefixes = ("pytest_", "py_test")
            for entry in databases:
                name = entry.get("name") or ""
                db_id = entry.get("db_id")
                if not db_id or not isinstance(name, str) or not name.startswith(prefixes):
                    continue
                try:
                    delete_response = await client.delete(f"/api/knowledge/databases/{db_id}", headers=headers)
                    if delete_response.status_code not in (200, 404):
                        print(f"Warning: Failed to cleanup knowledge database {db_id}: {delete_response.text}")
                except Exception as e:
                    print(f"Warning: Exception during cleanup of {db_id}: {e}")

    try:
        anyio.run(run_cleanup)
    except Exception as e:
        print(f"Warning: Exception during session cleanup startup: {e}")
    yield
    try:
        anyio.run(run_cleanup)
    except Exception as e:
        print(f"Warning: Exception during session cleanup teardown: {e}")


def _docker_api_request(method: str, path: str) -> list[dict] | dict:
    cmd = [
        "curl",
        "-sS",
        "--unix-socket",
        os.getenv("YUXI_DOCKER_API_SOCKET", "/var/run/docker.sock"),
        "-X",
        method,
        f"{os.getenv('YUXI_DOCKER_API_BASE', 'http://localhost').rstrip('/')}{path}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Docker API request failed")
    text = (result.stdout or "").strip()
    if not text:
        return {}
    return json.loads(text)


def _cleanup_sandbox_containers() -> None:
    try:
        containers = _docker_api_request("GET", "/containers/json?all=true")
    except Exception as exc:
        print(f"Warning: Failed to list sandbox containers for cleanup: {exc}")
        return

    for container in containers if isinstance(containers, list) else []:
        names = container.get("Names") or []
        if not any(name.lstrip("/").startswith(f"{SANDBOX_CONTAINER_PREFIX}-") for name in names):
            continue
        container_id = container.get("Id")
        if not container_id:
            continue
        try:
            _docker_api_request("POST", f"/containers/{container_id}/stop?t=2")
        except Exception:
            pass
        try:
            _docker_api_request("DELETE", f"/containers/{container_id}?force=true")
        except Exception as exc:
            print(f"Warning: Failed to cleanup sandbox container {container_id[:12]}: {exc}")


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_sandboxes():
    _cleanup_sandbox_containers()
    yield
    _cleanup_sandbox_containers()


@pytest_asyncio.fixture(scope="function")
async def standard_user(test_client: httpx.AsyncClient, admin_headers: dict[str, str]) -> dict:
    """
    Provision a temporary standard user for permission checks.

    Yields a dictionary with `user`, `password`, and `headers` keys.
    """
    username = f"pytest_user_{uuid.uuid4().hex[:8]}"
    password = f"Pw!{uuid.uuid4().hex[:8]}"

    response = await test_client.post(
        "/api/auth/users",
        json={"username": username, "password": password, "role": "user"},
        headers=admin_headers,
    )
    if response.status_code != 200:
        pytest.fail(f"Failed to create standard user (status={response.status_code}): {response.text}")

    user_payload = response.json()
    login_response = await test_client.post(
        "/api/auth/token",
        data={"username": user_payload["user_id"], "password": password},
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
        response = await test_client.delete(f"/api/auth/users/{user_payload['id']}", headers=admin_headers)
        assert response.status_code == 200, f"Failed to cleanup test user {user_payload['user_id']}: {response.text}"


@pytest_asyncio.fixture(scope="function")
async def knowledge_database(test_client: httpx.AsyncClient, admin_headers: dict[str, str]) -> dict:
    """
    Create a temporary knowledge database for tests that need LightRAG metadata.
    """
    import time

    # 使用UUID作为数据库名称的一部分，确保唯一性
    unique_id = uuid.uuid4().hex
    timestamp = int(time.time() * 1000000)  # 微秒级时间戳
    db_name = f"pytest_kb_{timestamp}_{unique_id}"
    db_id = None

    try:
        create_response = await test_client.post(
            "/api/knowledge/databases",
            json={
                "database_name": db_name,
                "description": "Pytest managed knowledge base",
                "embed_model_name": "siliconflow/BAAI/bge-m3",
                "kb_type": "lightrag",
                "additional_params": {},
            },
            headers=admin_headers,
        )

        if create_response.status_code == 200:
            db_payload = create_response.json()
            db_id = db_payload["db_id"]
        elif create_response.status_code == 409:
            error_detail = create_response.json().get("detail", "")
            pytest.fail(f"Knowledge database name conflict: {error_detail}. Please clean up old test databases first.")
        else:
            pytest.fail(
                f"Failed to create knowledge database (status={create_response.status_code}): {create_response.text}"
            )

        yield db_payload if db_id else {"db_id": db_id, "name": db_name}

    finally:
        # 确保清理，即使测试失败
        if db_id:
            try:
                delete_response = await test_client.delete(f"/api/knowledge/databases/{db_id}", headers=admin_headers)
                if delete_response.status_code != 200:
                    print(f"Warning: Failed to cleanup knowledge database {db_id}: {delete_response.text}")
            except Exception as e:
                print(f"Warning: Exception during cleanup of {db_id}: {e}")


def pytest_configure(config: pytest.Config) -> None:
    """Register commonly used custom markers."""
    config.addinivalue_line("markers", "auth: marks tests that require authentication")
    config.addinivalue_line("markers", "integration: marks tests that hit the live API service")


pytest_plugins = ["pytest_asyncio"]
