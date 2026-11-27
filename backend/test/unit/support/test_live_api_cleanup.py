from __future__ import annotations

from typing import Any

import httpx
import pytest

from test.live_api_cleanup import (
    TEST_SESSION_TITLE_PREFIX,
    CleanupSessionResource,
    cleanup_test_chat_resources,
    cleanup_pytest_knowledge_resources,
    is_test_session_title,
    make_test_session_metadata,
    make_test_session_title,
    make_test_resource_id,
    remove_test_workdir,
)

pytestmark = pytest.mark.asyncio


async def _patch_chat_cleanup_database(
    monkeypatch: pytest.MonkeyPatch,
    resources: dict[str, CleanupSessionResource],
) -> list[set[str]]:
    """打桩清理器的持久化发现、校验与物理删除。"""

    collected: list[set[str]] = []

    async def fake_list_resources(_owner_uid: str) -> dict[str, CleanupSessionResource]:
        return resources

    async def fake_validate(*_args, **_kwargs) -> None:
        return None

    async def fake_list_pending(_thread_ids: set[str]) -> list[tuple[str, str]]:
        return []

    async def fake_delete_resources(workdirs, thread_ids: set[str], _project_ids: set[str]) -> None:
        for uid, workdir_path in workdirs:
            remove_test_workdir(uid, workdir_path)
        collected.append(set(thread_ids))

    monkeypatch.setattr("test.live_api_cleanup.list_test_session_resources", fake_list_resources)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_workdirs_exclusive", fake_validate)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_runs_terminal", fake_validate)
    monkeypatch.setattr("test.live_api_cleanup.list_test_pending_inputs", fake_list_pending)
    monkeypatch.setattr("test.live_api_cleanup.delete_test_session_resources", fake_delete_resources)
    monkeypatch.setattr("test.live_api_cleanup.delete_orphaned_test_projects", fake_validate)
    return collected


async def test_cleanup_deletes_pytest_evaluation_resources_and_knowledge_bases():
    """只删除 pytest 前缀资源，并使用知识库真实返回的 kb_id。"""

    deleted_paths: list[str] = []
    responses: dict[str, dict[str, Any]] = {
        "/api/knowledge/knowledge-bases": {
            "knowledge_bases": [
                {"kb_id": "kb_test", "name": "Pytest knowledge base"},
                {"kb_id": "kb_legacy", "name": "py_test_legacy"},
                {"kb_id": "kb_prod", "name": "Production knowledge base"},
            ]
        },
        "/api/evaluation/knowledge-bases/kb_test/runs": {"data": [{"run_id": "run_test", "name": "PYTEST evaluation"}]},
        "/api/evaluation/knowledge-bases/kb_test/datasets": {"data": [{"dataset_id": "dataset_test", "name": "pytest plan"}]},
        "/api/evaluation/knowledge-bases/kb_legacy/runs": {"data": []},
        "/api/evaluation/knowledge-bases/kb_legacy/datasets": {"data": []},
        "/api/evaluation/knowledge-bases/kb_prod/runs": {"data": [{"run_id": "run_prod", "name": "Production evaluation"}]},
        "/api/evaluation/knowledge-bases/kb_prod/datasets": {
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
        "/api/evaluation/knowledge-bases/kb_test/runs/run_test",
        "/api/evaluation/datasets/dataset_test",
        "/api/evaluation/datasets/dataset_shared_test",
        "/api/knowledge/knowledge-bases/kb_test",
        "/api/knowledge/knowledge-bases/kb_legacy",
    }


async def test_cleanup_rejects_knowledge_list_error_payload():
    """知识库列表以 200 返回内部错误时，清理必须显式失败。"""

    def handle_request(request: httpx.Request) -> httpx.Response:
        """模拟知识库列表路由当前的 200 错误响应。"""

        return httpx.Response(200, json={"message": "获取知识库列表失败", "knowledge_bases": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="获取知识库列表失败"):
            await cleanup_pytest_knowledge_resources(client, {"Authorization": "test"})


async def test_cleanup_deletes_e2e_threads_before_temporary_agents(monkeypatch):
    """只删除 E2E 标记的对话和智能体，并允许资源已经不存在。"""

    deleted_paths: list[str] = []
    deleted_row_threads = await _patch_chat_cleanup_database(
        monkeypatch,
        {
            thread_id: CleanupSessionResource(
                session_record_id=index,
                project_id=f"project-{index}",
                thread_id=thread_id,
                uid="test-user",
                status="active",
                workdir_path=None,
            )
            for index, thread_id in enumerate(("thread-viewer", "thread-marked"), start=1)
        },
    )
    responses: dict[str, object] = {
        "/api/agent": {
            "agents": [
                {"slug": "e2e-provider-deadbeef", "created_by": "test-user"},
                {"slug": "e2e-provider-other-user", "created_by": "other-user"},
                {"slug": "default-chatbot", "created_by": "test-user"},
            ]
        },
    }

    def handle_request(request: httpx.Request) -> httpx.Response:
        """返回对话与智能体清理 API 的最小响应。"""

        if request.method in {"DELETE", "POST"}:
            deleted_paths.append(request.url.path)
            return httpx.Response(200, json={})
        return httpx.Response(200, json=responses[request.url.path])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_test_chat_resources(
            client,
            {"Authorization": "test"},
            owner_uid="test-user",
        )

    assert deleted_paths == [
        "/api/v1/agents/sessions/thread-viewer/archive",
        "/api/v1/agents/sessions/thread-marked/archive",
        "/api/agent/e2e-provider-deadbeef",
    ]
    assert deleted_row_threads == [{"thread-viewer", "thread-marked"}]


async def test_cleanup_only_exempts_projects_whose_managed_workdir_is_deleted(tmp_path, monkeypatch):
    """目标 linked Project 共享路径时仍必须被 Workdir 冲突校验视为保留 Owner。"""

    workdir_path = "projects/project-managed"
    managed_dir = tmp_path / workdir_path
    managed_dir.mkdir(parents=True)
    monkeypatch.setattr("test.live_api_cleanup.user_workdir_host_dir", lambda _uid, _path: managed_dir)
    resources = {
        "thread-managed": CleanupSessionResource(
            1,
            "project-managed",
            "thread-managed",
            "test-user",
            "active",
            workdir_path,
        ),
        "thread-linked": CleanupSessionResource(
            2,
            "project-linked",
            "thread-linked",
            "test-user",
            "active",
            None,
        ),
    }
    await _patch_chat_cleanup_database(monkeypatch, resources)
    validated_project_ids: list[set[str]] = []

    async def capture_validation(_workdirs, project_ids: set[str]):
        validated_project_ids.append(set(project_ids))

    monkeypatch.setattr("test.live_api_cleanup.validate_test_workdirs_exclusive", capture_validation)

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.method in {"DELETE", "POST"}:
            return httpx.Response(200, json={})
        if request.url.path == "/api/agent":
            return httpx.Response(200, json={"agents": []})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        await cleanup_test_chat_resources(client, {"Authorization": "test"}, owner_uid="test-user")

    assert validated_project_ids == [{"project-managed"}]


@pytest.mark.parametrize(
    "states",
    [[("thread-archived", "archived")], [("thread-deleted", "deleted"), ("thread-child", "active")]],
    ids=["archived", "deleted-and-active-member"],
)
async def test_cleanup_reads_persisted_threads_and_only_archives_active(monkeypatch, states):
    """持久发现覆盖非活动记录，只有仍活动的目标需要先归档。"""
    resources = {
        thread_id: CleanupSessionResource(index, f"project-{index}", thread_id, "test-user", status, None)
        for index, (thread_id, status) in enumerate(states, start=1)
    }
    deleted_rows = await _patch_chat_cleanup_database(monkeypatch, resources)
    requests = []

    def respond(request):
        """记录真实 HTTP 请求，物理删除仍由单独的 PG 集成证据拥有。"""
        requests.append(request.url.path)
        return httpx.Response(200, json={"agents": []} if request.method == "GET" else {})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url="http://test") as client:
        await cleanup_test_chat_resources(client, {"Authorization": "test"}, owner_uid="test-user")

    expected_archives = [f"/api/v1/agents/sessions/{thread_id}/archive" for thread_id, status in states if status == "active"]
    assert requests == expected_archives + ["/api/agent"]
    assert deleted_rows == [set(resources)]


async def test_remove_test_workdir_rejects_real_workspace_symlink(tmp_path, monkeypatch):
    """当前 Project Workdir 不能通过符号链接删除工作区外文件。"""
    from yuxi.modules.workspace.paths import ensure_user_workspace, user_workspace_dir

    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "user-data"))
    ensure_user_workspace("test-user")
    projects = user_workspace_dir("test-user") / "projects"
    projects.mkdir(exist_ok=True)
    outside = tmp_path / "unrelated-user-files"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep")
    (projects / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="invalid Workdir"):
        remove_test_workdir("test-user", "projects/linked")
    assert marker.read_text() == "keep"


async def test_test_resource_names_use_one_visible_prefix():
    title = make_test_session_title("viewer 文件系统")
    metadata = make_test_session_metadata("viewer-filesystem")

    assert title.startswith(TEST_SESSION_TITLE_PREFIX)
    assert metadata["_yuxi_test"] is True
    assert make_test_resource_id("agent-call").startswith("YUXI_TEST_")


async def test_unmarked_titles_never_identify_test_sessions():
    """普通标题和已退役标题都不能成为删除用户数据的授权依据。"""
    for title in ("viewer-deadbeef", "pytest-channel-0123abcd", "pytest-queue-0123abcd", "viewer-notes", None):
        assert not is_test_session_title(title)


async def test_cleanup_discovery_failure_has_no_destructive_side_effect(monkeypatch):
    """数据库无法确认归属时，不得先软删会话或删除临时智能体。"""

    destructive_paths: list[str] = []

    async def fail_discovery(_owner_uid: str):
        raise OSError("postgres unavailable")

    monkeypatch.setattr("test.live_api_cleanup.list_test_session_resources", fail_discovery)

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            destructive_paths.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/api/agent":
            raise AssertionError("agent cleanup must not run after discovery failure")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="Failed to list persisted"):
            await cleanup_test_chat_resources(client, {"Authorization": "test"}, owner_uid="test-user")

    assert destructive_paths == []


async def test_cleanup_guard_failure_has_no_destructive_side_effect(monkeypatch):
    """Run/Workdir guard 拒绝时，对话、文件、历史和智能体均保持不变。"""

    destructive_paths: list[str] = []
    resources = {
        "thread-marked": CleanupSessionResource(
            1,
            "project-marked",
            "thread-marked",
            "test-user",
            "active",
            None,
        )
    }

    async def fake_list_resources(_owner_uid: str):
        return resources

    async def fake_workdir_guard(*_args):
        return None

    async def fail_run_guard(_thread_ids: set[str]):
        raise RuntimeError("test Run is not terminal")

    monkeypatch.setattr("test.live_api_cleanup.list_test_session_resources", fake_list_resources)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_workdirs_exclusive", fake_workdir_guard)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_runs_terminal", fail_run_guard)

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            destructive_paths.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/api/agent":
            raise AssertionError("agent cleanup must not run after guard failure")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="not terminal"):
            await cleanup_test_chat_resources(client, {"Authorization": "test"}, owner_uid="test-user")

    assert destructive_paths == []


async def test_cleanup_stops_when_cancelled_input_remains_pending(monkeypatch):
    """取消 API 未真正收敛 pending Input 时，不得继续删除会话、文件或历史。"""

    destructive_paths: list[str] = []

    async def fake_list_resources(_owner_uid: str):
        return {
            "thread-marked": CleanupSessionResource(
                1,
                "project-marked",
                "thread-marked",
                "test-user",
                "active",
                None,
            )
        }

    async def fake_validate(*_args):
        return None

    async def still_pending(_thread_ids: set[str]) -> list[tuple[str, str]]:
        return [("thread-marked", "YUXI_TEST_pending_input")]

    monkeypatch.setattr("test.live_api_cleanup.list_test_session_resources", fake_list_resources)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_workdirs_exclusive", fake_validate)
    monkeypatch.setattr("test.live_api_cleanup.validate_test_runs_terminal", fake_validate)
    monkeypatch.setattr("test.live_api_cleanup.list_test_pending_inputs", still_pending)

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/agents/sessions/thread-marked/events":
            assert request.headers["Idempotency-Key"] == "cleanup:YUXI_TEST_pending_input"
            assert request.content == (b'{"events":[{"type":"yuxi.session.input.cancel_input","input_id":"YUXI_TEST_pending_input"}]}')
            return httpx.Response(202, json={"status": "cancelled"})
        if request.method == "DELETE":
            destructive_paths.append(request.url.path)
            return httpx.Response(200, json={})
        if request.url.path == "/api/agent":
            raise AssertionError("agent cleanup must not run while an Input remains pending")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="left pending Inputs behind"):
            await cleanup_test_chat_resources(client, {"Authorization": "test"}, owner_uid="test-user")

    assert destructive_paths == []


@pytest.mark.parametrize("workdir_path", ["projects/11111111-1111-4111-8111-111111111111", "agents/tasks/2026-11-27/new-chat-a1b2c3"])
async def test_remove_test_workdir_stays_inside_project_boundary(tmp_path, monkeypatch, workdir_path):
    project = tmp_path / workdir_path
    project.mkdir(parents=True)
    (project / "artifact.txt").write_text("test", encoding="utf-8")
    monkeypatch.setattr("test.live_api_cleanup.user_workdir_host_dir", lambda _uid, _path: project)

    remove_test_workdir("test-user", workdir_path)

    assert not project.exists()


@pytest.mark.parametrize("workdir_path", ["agents/skills", "agents/tasks", "agents/tasks/2026-11-27", "agents/tasks/2026-11-27/custom"])
async def test_remove_test_workdir_rejects_non_managed_path(tmp_path, monkeypatch, workdir_path):
    outside = tmp_path / "agents"
    outside.mkdir()
    monkeypatch.setattr("test.live_api_cleanup.user_workdir_host_dir", lambda _uid, _path: outside)

    with pytest.raises(RuntimeError, match="non-managed Workdir"):
        remove_test_workdir("test-user", workdir_path)

    assert outside.exists()


async def test_remove_test_workdir_rejects_symlink(tmp_path, monkeypatch):
    """Project Workdir 是符号链接时必须拒绝，不能跟随到用户目录。"""

    target = tmp_path / "user-files"
    target.mkdir()
    workdir_path = "projects/22222222-2222-4222-8222-222222222222"
    symlink = tmp_path / workdir_path
    symlink.parent.mkdir()
    symlink.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr("test.live_api_cleanup.user_workdir_host_dir", lambda _uid, _path: symlink)

    with pytest.raises(RuntimeError, match="symlink Workdir"):
        remove_test_workdir("test-user", workdir_path)

    assert target.exists()


async def test_remove_test_workdir_is_idempotent_when_directory_is_gone(tmp_path, monkeypatch):
    workdir_path = "projects/33333333-3333-4333-8333-333333333333"
    missing = tmp_path / workdir_path
    monkeypatch.setattr("test.live_api_cleanup.user_workdir_host_dir", lambda _uid, _path: missing)

    remove_test_workdir("test-user", workdir_path)

    assert not missing.exists()


async def test_only_explicit_session_ownership_allows_cleanup():
    from test.live_api_cleanup import _is_test_thread

    marked = {"id": "t1", "agent_id": "default-chatbot", "metadata": {"_yuxi_e2e": True, "test": "viewer-fs-e2e"}}
    agent_prefix = {"id": "invocation_x", "agent_id": "e2e-agent-call-deadbeef"}
    unified = {"id": "t3", "title": f"{TEST_SESSION_TITLE_PREFIX}viewer_deadbeef", "metadata": {}}
    explicit = {"id": "t4", "metadata": {"_yuxi_test": True}}
    plain = {"id": "t2", "agent_id": "default-chatbot"}

    assert not _is_test_thread(marked)
    assert not _is_test_thread(agent_prefix)
    assert _is_test_thread(unified)
    assert _is_test_thread(explicit)
    assert not _is_test_thread(plain)
    assert not _is_test_thread("not-a-dict")
