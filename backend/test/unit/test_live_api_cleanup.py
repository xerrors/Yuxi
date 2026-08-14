from __future__ import annotations

from typing import Any

import httpx
import pytest

from test.live_api_cleanup import cleanup_e2e_chat_resources, cleanup_pytest_knowledge_resources
from test.live_api_cleanup import remove_e2e_thread_storage

pytestmark = pytest.mark.asyncio


async def test_cleanup_deletes_pytest_evaluation_resources_and_knowledge_databases():
    """只删除 pytest 前缀资源，并使用知识库真实返回的 kb_id。"""

    deleted_paths: list[str] = []
    responses: dict[str, dict[str, Any]] = {
        "/api/knowledge/databases": {
            "databases": [
                {"kb_id": "kb_test", "name": "Pytest knowledge base"},
                {"kb_id": "kb_legacy", "name": "py_test_legacy"},
                {"kb_id": "kb_prod", "name": "Production knowledge base"},
            ]
        },
        "/api/evaluation/databases/kb_test/runs": {"data": [{"run_id": "run_test", "name": "PYTEST evaluation"}]},
        "/api/evaluation/databases/kb_test/datasets": {"data": [{"dataset_id": "dataset_test", "name": "pytest plan"}]},
        "/api/evaluation/databases/kb_legacy/runs": {"data": []},
        "/api/evaluation/databases/kb_legacy/datasets": {"data": []},
        "/api/evaluation/databases/kb_prod/runs": {"data": [{"run_id": "run_prod", "name": "Production evaluation"}]},
        "/api/evaluation/databases/kb_prod/datasets": {
            "data": [
                {"dataset_id": "dataset_shared_test", "name": "Pytest shared plan"},
                {"dataset_id": "dataset_prod", "name": "Production plan"},
            ]
        },
    }

    def handle_request(request: httpx.Request) -> httpx.Response:
        """返回清理 API 的最小真实 HTTP 响应。"""

        if request.method == "DELETE":
            deleted_paths.append(request.url.path)
            return httpx.Response(200, json={})
        return httpx.Response(200, json=responses[request.url.path])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_pytest_knowledge_resources(client, {"Authorization": "test"})

    assert set(deleted_paths) == {
        "/api/evaluation/databases/kb_test/runs/run_test",
        "/api/evaluation/datasets/dataset_test",
        "/api/evaluation/datasets/dataset_shared_test",
        "/api/knowledge/databases/kb_test",
        "/api/knowledge/databases/kb_legacy",
    }


async def test_cleanup_rejects_knowledge_list_error_payload():
    """知识库列表以 200 返回内部错误时，清理必须显式失败。"""

    def handle_request(request: httpx.Request) -> httpx.Response:
        """模拟知识库列表路由当前的 200 错误响应。"""

        return httpx.Response(200, json={"message": "获取数据库列表失败", "databases": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="获取数据库列表失败"):
            await cleanup_pytest_knowledge_resources(client, {"Authorization": "test"})


async def test_cleanup_deletes_e2e_threads_before_temporary_agents(tmp_path, monkeypatch):
    """只删除 E2E 标记的对话和智能体，并允许资源已经不存在。"""

    deleted_paths: list[str] = []
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    (tmp_path / "threads" / "thread-viewer").mkdir(parents=True)
    (tmp_path / "threads" / "thread-marked").mkdir(parents=True)
    responses: dict[str, object] = {
        "/api/chat/threads": [
            {
                "id": "thread-viewer",
                "title": "viewer-fs-e2e-deadbeef",
                "agent_id": "default-chatbot",
                "metadata": {"_yuxi_e2e": True, "test": "viewer-fs-e2e"},
            },
            {
                "id": "thread-user",
                "title": "用户自己的对话",
                "agent_id": "default-chatbot",
                "metadata": {},
            },
            {
                "id": "thread-marked",
                "title": "未使用固定前缀",
                "agent_id": "e2e-main-deadbeef",
                "metadata": {"_yuxi_e2e": True, "marker": "YUXI_SUBAGENT_STREAM_E2E_deadbeef"},
            },
        ],
        "/api/agent": {
            "agents": [
                {"slug": "e2e-main-deadbeef", "created_by": "test-user"},
                {"slug": "e2e-main-other-user", "created_by": "other-user"},
                {"slug": "default-chatbot", "created_by": "test-user"},
            ]
        },
    }

    def handle_request(request: httpx.Request) -> httpx.Response:
        """返回对话与智能体清理 API 的最小响应。"""

        if request.method == "DELETE":
            deleted_paths.append(request.url.path)
            return httpx.Response(200, json={})
        return httpx.Response(200, json=responses[request.url.path])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_e2e_chat_resources(
            client,
            {"Authorization": "test"},
            owner_uid="test-user",
            thread_storage_statuses={},
        )

    assert deleted_paths == [
        "/api/chat/thread/thread-viewer",
        "/api/chat/thread/thread-marked",
        "/api/agent/e2e-main-deadbeef",
    ]
    assert not (tmp_path / "threads" / "thread-viewer").exists()
    assert not (tmp_path / "threads" / "thread-marked").exists()


async def test_cleanup_paginates_active_threads(tmp_path, monkeypatch):
    """活动线程超过单页上限时仍需清理后续页面的 E2E 对话。"""

    deleted_paths: list[str] = []
    offsets: list[str] = []
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))

    def handle_request(request: httpx.Request) -> httpx.Response:
        """模拟分两页返回线程的清理 API。"""

        if request.method == "DELETE":
            deleted_paths.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/api/chat/threads":
            offset = request.url.params.get("offset") or "0"
            offsets.append(offset)
            if offset == "0":
                return httpx.Response(
                    200,
                    json=[{"id": f"thread-{index}", "is_pinned": False} for index in range(500)],
                )
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "thread-page-2",
                        "title": "任意标题",
                        "metadata": {"_yuxi_e2e": True, "test": "viewer-fs-e2e"},
                    }
                ],
            )
        if request.url.path == "/api/agent":
            return httpx.Response(200, json={"agents": []})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_e2e_chat_resources(
            client,
            {"Authorization": "test"},
            owner_uid="test-user",
            thread_storage_statuses={},
        )

    assert offsets == ["0", "500"]
    assert deleted_paths == ["/api/chat/thread/thread-page-2"]


async def test_cleanup_removes_deleted_and_subagent_thread_storage(tmp_path, monkeypatch):
    """已软删除和 subagent 状态的线程也必须回收本地沙盒目录。"""

    deleted_paths: list[str] = []
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    for thread_id in ("thread-deleted", "thread-child"):
        (tmp_path / "threads" / thread_id).mkdir(parents=True)

    def handle_request(request: httpx.Request) -> httpx.Response:
        """模拟无 active 线程但存在持久化线程的清理 API。"""

        if request.method == "DELETE":
            deleted_paths.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/api/chat/threads":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/agent":
            return httpx.Response(200, json={"agents": []})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_e2e_chat_resources(
            client,
            {"Authorization": "test"},
            owner_uid="test-user",
            thread_storage_statuses={"thread-deleted": "deleted", "thread-child": "subagent"},
        )

    assert deleted_paths == ["/api/chat/thread/thread-child"]
    assert not (tmp_path / "threads" / "thread-deleted").exists()
    assert not (tmp_path / "threads" / "thread-child").exists()


async def test_remove_e2e_thread_storage_rejects_symlink(tmp_path, monkeypatch):
    """沙盒目录是符号链接时必须拒绝删除，避免解析后误删用户目录。"""

    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    threads_root = tmp_path / "threads"
    threads_root.mkdir()
    user_dir = threads_root / "user-data"
    user_dir.mkdir()
    (threads_root / "thread-e2e").symlink_to(user_dir, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        remove_e2e_thread_storage("thread-e2e")

    assert user_dir.exists()
