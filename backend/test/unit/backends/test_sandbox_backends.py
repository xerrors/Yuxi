"""Tests for sandbox backend components."""

from __future__ import annotations

import base64
import gc
import hashlib
import threading
import weakref
from types import MethodType, SimpleNamespace

import httpx
import pytest
import yuxi.agents.backends.sandbox.backend as sandbox_backend_module
from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import GlobResult, GrepResult, ReadResult
from deepagents.backends.sandbox import MAX_BINARY_BYTES
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolRuntime
from yuxi.agents.backends.composite import (
    create_agent_composite_backend,
    create_agent_filesystem_middleware,
    sync_agent_context_skills,
)
from yuxi.agents.backends.sandbox import ProvisionerSandboxProvider, sandbox_id_for_thread
from yuxi.agents.backends.sandbox.backend import ProvisionerSandboxBackend
from yuxi.agents.backends.sandbox.provider import SandboxIdentityMismatchError
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.agents.backends.paths import workdir_runtime_paths

WORKDIR_RELATIVE_PATH = "projects/11111111-1111-4111-8111-111111111111"
WORKDIR_PATH = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111"
VIRTUAL_PATH_LARGE_TOOL_RESULTS, VIRTUAL_PATH_CONVERSATION_HISTORY = workdir_runtime_paths(WORKDIR_PATH)


class _OwnedAsyncHttpClient:
    def __init__(self):
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True


def _install_async_file_client(monkeypatch, backend, file_client):
    backend._provider = SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(sandbox_url="http://sandbox"))
    http_client = _OwnedAsyncHttpClient()
    monkeypatch.setattr(sandbox_backend_module.httpx, "AsyncClient", lambda **_kwargs: http_client)

    def build_async_client(_url, owning_http_client):
        assert owning_http_client is http_client
        return SimpleNamespace(file=file_client)

    monkeypatch.setattr(backend, "_build_async_client", build_async_client)
    return http_client


def _runtime(
    *,
    thread_id: str | None = "thread-1",
    uid: str | None = "user-1",
):
    configurable = (
        {
            "thread_id": thread_id,
            "runtime_scope_id": thread_id,
            "workdir_relative_path": WORKDIR_RELATIVE_PATH,
            "workdir_path": WORKDIR_PATH,
            "uid": uid,
        }
        if thread_id and uid
        else {}
    )
    return SimpleNamespace(
        config={"configurable": configurable},
        context=SimpleNamespace(
            uid=uid,
            thread_id=thread_id,
            runtime_scope_id=thread_id,
            workdir_relative_path=WORKDIR_RELATIVE_PATH if thread_id and uid else None,
            workdir_path=WORKDIR_PATH if thread_id and uid else None,
        ),
    )


def _make_provider(client) -> ProvisionerSandboxProvider:
    provider = ProvisionerSandboxProvider.__new__(ProvisionerSandboxProvider)
    provider._client = client
    provider._lock = threading.Lock()
    provider._thread_locks = {}
    provider._connections = {}
    provider._last_touch_at = {}
    provider._touch_interval_seconds = 30
    return provider


def test_create_agent_composite_backend_uses_sandbox_filesystem(monkeypatch):
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())

    backend = create_agent_composite_backend(_runtime().context)

    assert isinstance(backend.default, ProvisionerSandboxBackend)
    assert backend.default._create_if_missing is True
    assert backend.routes == {}
    assert backend.artifacts_root == f"{WORKDIR_PATH}/outputs"


def test_create_agent_composite_backend_derives_virtual_workdir_from_relative_path(monkeypatch):
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    context = _runtime().context
    context.workdir_path = "/home/gem/user-data/projects/stale"

    backend = create_agent_composite_backend(context)

    assert backend.artifacts_root == f"{WORKDIR_PATH}/outputs"


def test_sandbox_provider_release_deletes_sandbox_and_clears_cache():
    deleted: list[tuple[str, str | None]] = []
    provider = object.__new__(ProvisionerSandboxProvider)
    provider._lock = threading.Lock()
    provider._thread_locks = {}
    provider._connections = {}
    provider._last_touch_at = {}
    provider._client = SimpleNamespace(
        delete=lambda sandbox_id, *, expected_generation=None: deleted.append((sandbox_id, expected_generation))
    )
    connection = SimpleNamespace(
        sandbox_id="sandbox-1",
        workdir_path=None,
        generation="generation-1",
    )
    cache_key = "user-1::thread-1"
    provider._connections[cache_key] = connection
    provider._last_touch_at[cache_key] = 1.0

    provider.release("thread-1", uid="user-1")

    assert deleted == [("sandbox-1", "generation-1")]
    assert cache_key not in provider._connections
    assert cache_key not in provider._last_touch_at


def test_sandbox_provider_discards_unused_thread_locks():
    provider = object.__new__(ProvisionerSandboxProvider)
    provider._lock = threading.Lock()
    provider._thread_locks = weakref.WeakValueDictionary()

    lock = provider._thread_lock("user-1::thread-1")
    lock_ref = weakref.ref(lock)
    assert provider._thread_lock("user-1::thread-1") is lock

    del lock
    gc.collect()

    assert lock_ref() is None
    assert not provider._thread_locks


def test_sandbox_provider_waiter_keeps_shared_thread_lock_alive():
    provider = object.__new__(ProvisionerSandboxProvider)
    provider._lock = threading.Lock()
    provider._thread_locks = weakref.WeakValueDictionary()
    cache_key = "user-1::thread-1"
    first_lock = provider._thread_lock(cache_key)
    waiter_ready = threading.Event()
    waiter_acquired = threading.Event()

    def wait_for_lock() -> None:
        waiting_lock = provider._thread_lock(cache_key)
        assert waiting_lock is first_lock
        waiter_ready.set()
        with waiting_lock:
            waiter_acquired.set()

    first_lock.acquire()
    waiter = threading.Thread(target=wait_for_lock)
    waiter.start()
    assert waiter_ready.wait(timeout=1)
    assert provider._thread_lock(cache_key) is first_lock
    assert not waiter_acquired.is_set()

    first_lock.release()
    waiter.join(timeout=1)

    assert waiter_acquired.is_set()


@pytest.mark.parametrize("clear_cache_on_delete_failure", [False, True])
def test_sandbox_provider_release_on_delete_failure(clear_cache_on_delete_failure):
    provider = object.__new__(ProvisionerSandboxProvider)
    provider._lock = threading.Lock()
    provider._thread_locks = {}
    provider._connections = {}
    provider._last_touch_at = {}

    def fail_delete(_sandbox_id, *, expected_generation=None):
        _ = expected_generation
        raise RuntimeError("delete failed")

    provider._client = SimpleNamespace(delete=fail_delete)
    connection = SimpleNamespace(
        sandbox_id="sandbox-1",
        workdir_path=None,
        generation="generation-1",
    )
    cache_key = "user-1::thread-1"
    provider._connections[cache_key] = connection
    provider._last_touch_at[cache_key] = 1.0

    with pytest.raises(RuntimeError, match="delete failed"):
        provider.release(
            "thread-1",
            uid="user-1",
            clear_cache_on_delete_failure=clear_cache_on_delete_failure,
        )

    if clear_cache_on_delete_failure:
        assert cache_key not in provider._connections
        assert cache_key not in provider._last_touch_at
    else:
        assert provider._connections[cache_key] is connection
        assert provider._last_touch_at[cache_key] == 1.0


@pytest.mark.asyncio
async def test_sync_agent_context_skills_projects_all_user_authorized_skills(monkeypatch):
    """Run 初始化应同步用户授权的全部 Skill，不将选中集合作为文件权限。"""
    calls = []

    async def refresh_user_skill_projection_async(uid):
        calls.append(uid)
        return {
            "worker-skill": "/tmp/worker-skill",
            "authorized-unselected": "/tmp/authorized-unselected",
        }

    monkeypatch.setattr(
        "yuxi.agents.backends.composite.refresh_user_skill_projection_async",
        refresh_user_skill_projection_async,
    )
    context = SimpleNamespace(
        thread_id="child-thread",
        runtime_scope_id="parent-thread",
        workdir_relative_path=WORKDIR_RELATIVE_PATH,
        workdir_path=WORKDIR_PATH,
        uid="user-1",
    )

    await sync_agent_context_skills(context)

    assert calls == ["user-1"]


def test_create_agent_composite_backend_requires_thread_id():
    with pytest.raises(ValueError, match="thread_id is required"):
        create_agent_composite_backend(_runtime(thread_id=None).context)


def test_create_agent_filesystem_middleware_uses_context_scope(monkeypatch):
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    context = SimpleNamespace(
        thread_id="child-thread",
        runtime_scope_id="parent-thread",
        workdir_relative_path=WORKDIR_RELATIVE_PATH,
        workdir_path=WORKDIR_PATH,
        uid="user-1",
    )

    middleware = create_agent_filesystem_middleware(
        backend=create_agent_composite_backend(context),
    )

    assert middleware.backend.default._thread_id == "parent-thread"


def test_context_backend_construction_does_not_sync_skill_projection(monkeypatch, tmp_path) -> None:
    """每轮模型调用重建 backend 时不得扫描或复制 Skill。"""
    from yuxi.agents.skills import service as skill_service

    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    monkeypatch.setenv("YUXI_USER_DATA_DIR", str(tmp_path / "threads"))
    source_dir = tmp_path / "source" / "shared-skill"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text("# Shared", encoding="utf-8")
    context = SimpleNamespace(
        thread_id="thread-1",
        runtime_scope_id="thread-1",
        workdir_relative_path=WORKDIR_RELATIVE_PATH,
        workdir_path=WORKDIR_PATH,
        uid="user-1",
    )

    create_agent_composite_backend(context)
    create_agent_composite_backend(context)

    user_skill = skill_service.get_user_skills_root_dir("user-1") / "shared-skill"
    assert not user_skill.exists()


def test_create_agent_filesystem_middleware_uses_outputs_for_internal_artifacts() -> None:
    class _Backend:
        pass

    middleware = create_agent_filesystem_middleware(
        500,
        backend=CompositeBackend(default=_Backend(), routes={}, artifacts_root=f"{WORKDIR_PATH}/outputs"),
    )

    assert middleware._tool_token_limit_before_evict == 500
    assert middleware._large_tool_results_prefix == VIRTUAL_PATH_LARGE_TOOL_RESULTS
    assert middleware._conversation_history_prefix == VIRTUAL_PATH_CONVERSATION_HISTORY


def test_filesystem_middleware_evicts_large_non_read_file_tool_result() -> None:
    class _Backend:
        def __init__(self):
            self.writes: list[tuple[str, str]] = []

        def write(self, path: str, content: str):
            self.writes.append((path, content))
            return SimpleNamespace(error=None, path=path)

    backend = _Backend()
    middleware = create_agent_filesystem_middleware(
        1,
        backend=CompositeBackend(default=backend, routes={}, artifacts_root=f"{WORKDIR_PATH}/outputs"),
    )
    request = SimpleNamespace(tool_call={"name": "query_kb"}, runtime=SimpleNamespace())
    content = "BEGIN\n" + ("middle\n" * 5000) + "END"

    result = middleware.wrap_tool_call(
        request,
        lambda _: ToolMessage(content=content, name="query_kb", tool_call_id="call-kb"),
    )

    assert backend.writes == [(f"{VIRTUAL_PATH_LARGE_TOOL_RESULTS}/call-kb", content)]
    assert isinstance(result, ToolMessage)
    assert len(result.content) < len(content)
    assert f"{VIRTUAL_PATH_LARGE_TOOL_RESULTS}/call-kb" in result.content


def test_filesystem_middleware_keeps_builtin_fs_tool_result_inline() -> None:
    """0.7 上游语义：内建文件工具自带截断（分页/max_count），不再做结果 eviction。"""

    class _Backend:
        def __init__(self):
            self.writes: list[tuple[str, str]] = []

        def write(self, path: str, content: str):
            self.writes.append((path, content))
            return SimpleNamespace(error=None, path=path)

    backend = _Backend()
    middleware = create_agent_filesystem_middleware(
        1,
        backend=CompositeBackend(default=backend, routes={}, artifacts_root=f"{WORKDIR_PATH}/outputs"),
    )
    content = "BEGIN\n" + ("middle\n" * 5000) + "END"

    for tool_name in ("grep", "glob", "ls", "edit_file", "write_file"):
        request = SimpleNamespace(tool_call={"name": tool_name}, runtime=SimpleNamespace())
        result = middleware.wrap_tool_call(
            request,
            lambda _: ToolMessage(content=content, name=tool_name, tool_call_id=f"call-{tool_name}"),
        )
        assert result.content == content

    assert backend.writes == []


def test_filesystem_middleware_keeps_read_file_result_inline_to_avoid_evict_loop() -> None:
    class _Backend:
        def __init__(self):
            self.writes: list[tuple[str, str]] = []

        def write(self, path: str, content: str):
            self.writes.append((path, content))
            return SimpleNamespace(error=None)

    backend = _Backend()
    middleware = create_agent_filesystem_middleware(
        1,
        backend=CompositeBackend(default=backend, routes={}, artifacts_root=f"{WORKDIR_PATH}/outputs"),
    )
    request = SimpleNamespace(tool_call={"name": "read_file"}, runtime=SimpleNamespace())
    content = "x" * 100

    result = middleware.wrap_tool_call(
        request,
        lambda _: ToolMessage(content=content, name="read_file", tool_call_id="call-read"),
    )

    assert backend.writes == []
    assert result.content == content


def test_filesystem_middleware_keeps_kb_document_result_inline() -> None:
    class _Backend:
        def __init__(self):
            self.writes: list[tuple[str, str]] = []

        def write(self, path: str, content: str):
            self.writes.append((path, content))
            return SimpleNamespace(error=None)

    backend = _Backend()
    middleware = create_agent_filesystem_middleware(
        1,
        backend=CompositeBackend(default=backend, routes={}, artifacts_root=f"{WORKDIR_PATH}/outputs"),
    )
    request = SimpleNamespace(tool_call={"name": "open_kb_document"}, runtime=SimpleNamespace())
    content = "x" * 100

    result = middleware.wrap_tool_call(
        request,
        lambda _: ToolMessage(content=content, name="open_kb_document", tool_call_id="call-kb"),
    )

    assert backend.writes == []
    assert result.content == content


def test_filesystem_middleware_tool_allowlist_excludes_delete() -> None:
    class _Backend:
        pass

    middleware = create_agent_filesystem_middleware(
        backend=CompositeBackend(default=_Backend(), routes={}, artifacts_root=f"{WORKDIR_PATH}/outputs"),
    )

    tool_names = {tool.name for tool in middleware.tools}
    assert "delete" not in tool_names
    assert {"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"} <= tool_names


def test_official_composite_glob_only_searches_routes_from_root() -> None:
    class _Backend:
        def __init__(self, name: str):
            self.name = name
            self.calls: list[tuple[str, str]] = []

        def glob(self, pattern: str, path: str = "/") -> GlobResult:
            self.calls.append((pattern, path))
            return GlobResult(matches=[{"path": f"{path.rstrip('/')}/{self.name}.md"}])

    default = _Backend("default")
    routed = _Backend("skill")
    backend = CompositeBackend(default=default, routes={"/skills/": routed})

    result = backend.glob("**/*.md", path="/home/gem/user-data")

    assert result.error is None
    assert default.calls == [("**/*.md", "/home/gem/user-data")]
    assert routed.calls == []


def test_official_composite_root_glob_merges_routes_and_propagates_truncated() -> None:
    class _Backend:
        def __init__(self, name: str, *, truncated: bool = False):
            self.name = name
            self.truncated = truncated

        def glob(self, pattern: str, path: str | None = None) -> GlobResult:
            return GlobResult(matches=[{"path": f"/{self.name}.md"}], truncated=self.truncated)

    default = _Backend("default")
    routed = _Backend("skill", truncated=True)
    backend = CompositeBackend(default=default, routes={"/skills/": routed})

    result = backend.glob("**/*.md", path="/")

    assert result.error is None
    assert [item["path"] for item in result.matches] == ["/default.md", "/skills/skill.md"]
    assert result.truncated is True


def test_skills_middleware_extracts_slug_for_new_paths() -> None:
    middleware = SkillsMiddleware()
    assert middleware.skills_sources_for_prompt == [
        "/home/gem/skills/",
        "/home/gem/user-data/agents/skills/",
    ]
    assert middleware._extract_skill_slug_from_skill_md_path("/home/gem/skills/demo-skill/SKILL.md") == "demo-skill"
    assert (
        middleware._extract_skill_slug_from_skill_md_path("/home/gem/user-data/agents/skills/personal-skill/SKILL.md")
        == "personal-skill"
    )


def test_sandbox_id_for_thread_is_stable():
    sid1 = sandbox_id_for_thread("thread-1")
    sid2 = sandbox_id_for_thread("thread-1")
    sid3 = sandbox_id_for_thread("thread-2")
    assert sid1 == sid2
    assert sid1 != sid3
    assert len(sid1) == 12


def test_provider_revalidates_runtime_generation_after_keepalive(monkeypatch) -> None:
    class FakeClient:
        def create(self, sandbox_id, *_args, **kwargs):
            return SimpleNamespace(
                sandbox_id=sandbox_id,
                sandbox_url="http://sandbox/generation-1",
                generation="generation-1",
                workdir_path=kwargs["workdir_path"],
            )

        def touch(self, _sandbox_id):
            return True

        def discover(self, sandbox_id):
            return SimpleNamespace(
                sandbox_id=sandbox_id,
                sandbox_url="http://sandbox/generation-2",
                generation="generation-2",
                workdir_path="projects/11111111-1111-4111-8111-111111111111",
            )

    provider = _make_provider(FakeClient())
    monkeypatch.setattr("yuxi.agents.backends.sandbox.provider.load_user_agent_env", lambda _uid: {})
    provider.get(
        "root-thread",
        uid="user-1",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        create_if_missing=True,
    )
    connection = next(iter(provider._connections.values()))
    provider._last_touch_at[connection.cache_key] = 0

    refreshed = provider.get("root-thread", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111")

    assert refreshed is connection
    assert refreshed.generation == "generation-2"
    assert refreshed.sandbox_url == "http://sandbox/generation-2"


def test_provider_recreates_cross_process_deleted_generation_before_touch_interval(monkeypatch) -> None:
    created = 0

    class FakeClient:
        def create(self, sandbox_id, *_args, **kwargs):
            nonlocal created
            created += 1
            return SimpleNamespace(
                sandbox_id=sandbox_id,
                sandbox_url=f"http://sandbox/generation-{created}",
                generation=f"generation-{created}",
                workdir_path=kwargs["workdir_path"],
            )

        def discover(self, _sandbox_id):
            return None

        def touch(self, _sandbox_id):
            raise AssertionError("fresh cache must revalidate generation without keepalive touch")

    provider = _make_provider(FakeClient())
    monkeypatch.setattr("yuxi.agents.backends.sandbox.provider.load_user_agent_env", lambda _uid: {})
    provider.get(
        "root-thread",
        uid="user-1",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        create_if_missing=True,
    )

    refreshed = provider.get(
        "root-thread",
        uid="user-1",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        create_if_missing=True,
    )

    assert created == 2
    assert refreshed.generation == "generation-2"
    assert refreshed.sandbox_url == "http://sandbox/generation-2"


def test_provider_rejects_project_workdir_drift_after_keepalive(monkeypatch) -> None:
    class FakeClient:
        def create(self, sandbox_id, *_args, **kwargs):
            return SimpleNamespace(
                sandbox_id=sandbox_id,
                sandbox_url="http://sandbox/generation-1",
                generation="generation-1",
                workdir_path=kwargs["workdir_path"],
            )

        def touch(self, _sandbox_id):
            return True

        def discover(self, sandbox_id):
            return SimpleNamespace(
                sandbox_id=sandbox_id,
                sandbox_url="http://sandbox/generation-2",
                generation="generation-2",
                workdir_path="projects/22222222-2222-4222-8222-222222222222",
            )

    provider = _make_provider(FakeClient())
    monkeypatch.setattr("yuxi.agents.backends.sandbox.provider.load_user_agent_env", lambda _uid: {})
    provider.get(
        "root-thread",
        uid="user-1",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        create_if_missing=True,
    )
    connection = next(iter(provider._connections.values()))
    provider._last_touch_at[connection.cache_key] = 0

    with pytest.raises(SandboxIdentityMismatchError, match="changed"):
        provider.get("root-thread", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111")


def test_provider_rejects_rebinding_cached_runtime_to_another_workdir(monkeypatch) -> None:
    created: list[str | None] = []

    class FakeClient:
        def create(self, sandbox_id, *_args, **kwargs):
            created.append(kwargs["workdir_path"])
            return SimpleNamespace(
                sandbox_id=sandbox_id,
                sandbox_url="http://sandbox",
                generation="generation-1",
                workdir_path=kwargs["workdir_path"],
            )

    provider = _make_provider(FakeClient())
    monkeypatch.setattr("yuxi.agents.backends.sandbox.provider.load_user_agent_env", lambda _uid: {})
    provider.get(
        "root-thread",
        uid="user-1",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        create_if_missing=True,
    )

    with pytest.raises(SandboxIdentityMismatchError, match="existing runtime scope"):
        provider.get(
            "root-thread",
            uid="user-1",
            workdir_path="projects/22222222-2222-4222-8222-222222222222",
            create_if_missing=True,
        )

    assert created == ["projects/11111111-1111-4111-8111-111111111111"]


def test_provider_release_uses_cached_generation() -> None:
    deleted: list[tuple[str, str | None]] = []

    class FakeClient:
        def delete(self, sandbox_id, *, expected_generation=None):
            deleted.append((sandbox_id, expected_generation))

    provider = _make_provider(FakeClient())
    cache_key = "user-1::root-thread"
    provider._connections[cache_key] = SimpleNamespace(
        sandbox_id="sandbox-1",
        uid="user-1",
        workdir_path="projects/11111111-1111-4111-8111-111111111111",
        generation="generation-1",
    )

    provider.release("root-thread", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111")

    assert deleted == [("sandbox-1", "generation-1")]


def test_provider_uses_distinct_sandbox_scope_for_different_uid(monkeypatch) -> None:
    created = []

    class FakeClient:
        def create(self, sandbox_id, thread_id, uid, env, **kwargs):
            created.append((sandbox_id, thread_id, uid, env))
            return SimpleNamespace(
                sandbox_id=sandbox_id,
                sandbox_url=f"http://sandbox/{uid}",
                generation="generation-1",
                workdir_path=kwargs["workdir_path"],
            )

        def touch(self, _sandbox_id):
            return True

    provider = _make_provider(FakeClient())
    monkeypatch.setattr("yuxi.agents.backends.sandbox.provider.load_user_agent_env", lambda uid: {"A": uid})

    sandbox_1 = provider.get(
        "child-thread",
        uid="user-1",
        create_if_missing=True,
    )
    sandbox_2 = provider.get(
        "child-thread",
        uid="user-2",
        create_if_missing=True,
    )

    assert sandbox_1.sandbox_id != sandbox_2.sandbox_id
    assert created[0][2] == "user-1"
    assert created[1][2] == "user-2"


def test_provider_maps_external_uid_only_at_provisioner_filesystem_boundary(monkeypatch) -> None:
    from yuxi.workspace.paths import workspace_uid_dirname

    calls = []

    class FakeClient:
        def create(self, sandbox_id, thread_id, uid, env, **kwargs):
            calls.append((uid, env))
            return SimpleNamespace(
                sandbox_id=sandbox_id,
                sandbox_url="http://sandbox",
                generation="generation-1",
                workdir_path=kwargs["workdir_path"],
            )

        def touch(self, _sandbox_id):
            return True

    provider = _make_provider(FakeClient())
    logical_uid = "oidc:12345678-1234-1234-1234-123456789abc"
    monkeypatch.setattr("yuxi.agents.backends.sandbox.provider.load_user_agent_env", lambda uid: {"OWNER": uid})

    provider.get("thread-1", uid=logical_uid, create_if_missing=True)

    assert calls == [(workspace_uid_dirname(logical_uid), {"OWNER": logical_uid})]
    assert calls[0][0].startswith("uid-")
    assert ":" not in calls[0][0]


def test_provider_get_create_if_missing_ensures_expected_runtime_scope(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def create(self, sandbox_id, thread_id, uid, env, **kwargs):
            calls.append((sandbox_id, thread_id, uid, env))
            return SimpleNamespace(
                sandbox_id=sandbox_id,
                sandbox_url="http://sandbox",
                generation="generation-1",
                workdir_path=kwargs["workdir_path"],
            )

        def discover(self, _sandbox_id):
            raise AssertionError("create_if_missing should ensure sandbox through provisioner create")

    provider = _make_provider(FakeClient())
    monkeypatch.setattr("yuxi.agents.backends.sandbox.provider.load_user_agent_env", lambda uid: {"A": uid})

    connection = provider.get(
        "child-thread",
        uid="user-1",
        create_if_missing=True,
    )

    sandbox_id = sandbox_id_for_thread("child-thread", uid="user-1")
    assert connection.sandbox_id == sandbox_id
    assert calls == [
        (
            sandbox_id,
            "child-thread",
            "user-1",
            {"A": "user-1"},
        )
    ]


def test_provider_can_create_sandbox_without_environment(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def create(self, sandbox_id, thread_id, uid, env, **kwargs):
            calls.append((env, kwargs["inherit_env"]))
            return SimpleNamespace(
                sandbox_id=sandbox_id,
                sandbox_url="http://sandbox",
                generation="generation-1",
                workdir_path=kwargs["workdir_path"],
            )

    provider = _make_provider(FakeClient())
    monkeypatch.setattr(
        "yuxi.agents.backends.sandbox.provider.load_user_agent_env",
        lambda _uid: pytest.fail("隔离 Sandbox 不应加载用户环境变量"),
    )

    provider.get("remote-skill-test", uid="remote-skill-test", create_if_missing=True, inherit_env=False)

    assert calls == [({}, False)]


def test_provisioner_uses_runtime_scope_directly(monkeypatch) -> None:
    provider_calls = []

    class FakeProvider:
        def get(self, thread_id, **kwargs):
            provider_calls.append((thread_id, kwargs))
            return SimpleNamespace(sandbox_url="http://sandbox")

    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: FakeProvider())

    backend = ProvisionerSandboxBackend(
        thread_id="child-thread",
        uid="user-1",
    )
    backend._build_client = MethodType(lambda self, sandbox_url: SimpleNamespace(url=sandbox_url), backend)

    client = backend._get_client()

    assert client.url == "http://sandbox"
    assert provider_calls == [
        (
            "child-thread",
            {
                "uid": "user-1",
                "create_if_missing": True,
                "inherit_env": True,
                "workdir_path": None,
            },
        )
    ]


def test_provisioner_denies_reads_outside_allowed_roots(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(
        thread_id="thread-1", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111"
    )

    result = backend.read("/etc/passwd")

    assert result.error == "permission denied for read on '/etc/passwd'"


def test_provisioner_rejects_skill_projection_writes(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    skill_path = "/home/gem/skills/demo/SKILL.md"

    assert "permission denied" in backend.write(skill_path, "content").error
    assert "permission denied" in backend.edit(skill_path, "old", "new").error
    assert backend.upload_files([(skill_path, b"content")])[0].error == "permission_denied"


def test_provisioner_allows_project_upload_writes(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(
        thread_id="thread-1", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111"
    )
    written: list[str] = []

    def read_file(**kwargs):
        del kwargs
        raise FileNotFoundError

    def write_file(**kwargs):
        written.append(kwargs["file"])
        return SimpleNamespace(success=True, message=None)

    client = SimpleNamespace(file=SimpleNamespace(read_file=read_file, write_file=write_file))
    monkeypatch.setattr(backend, "_get_client", lambda: client)
    monkeypatch.setattr(backend, "_ensure_parent_directory", lambda _path: None)

    root = "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/uploads"
    write_result = backend.write(f"{root}/note.txt", "content")
    upload_result = backend.upload_files([(f"{root}/data.bin", b"content")])

    assert write_result.error is None
    assert upload_result[0].error is None
    assert written == [f"{root}/note.txt", f"{root}/data.bin"]


def test_provisioner_creates_write_parent_without_following_symlinks(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    commands: list[str] = []

    def execute(command: str, **_kwargs):
        commands.append(command)
        return SimpleNamespace(exit_code=0, output="")

    monkeypatch.setattr(backend, "execute", execute)

    backend._ensure_parent_directory(f"{WORKDIR_PATH}/outputs/reports/result.md")

    encoded = commands[0].split("b64decode('", 1)[1].split("')", 1)[0]
    script = base64.b64decode(encoded).decode()
    assert "('projects', '11111111-1111-4111-8111-111111111111', 'outputs', 'reports')" in script
    assert "os.O_NOFOLLOW" in script


def test_provisioner_rejects_parent_directory_symlink_failure(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    monkeypatch.setattr(
        backend,
        "execute",
        lambda *_args, **_kwargs: SimpleNamespace(exit_code=1, output="Too many levels of symbolic links"),
    )

    with pytest.raises(PermissionError, match="symbolic links"):
        backend._ensure_parent_directory(f"{WORKDIR_PATH}/outputs/result.md")


def test_provisioner_allows_outputs_writes(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    def _missing_file(path, offset=0, limit=None):
        raise FileNotFoundError

    monkeypatch.setattr(backend, "_read_binary", _missing_file)

    calls = []

    def _write_file(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(success=True, message="")

    fake_client = SimpleNamespace(file=SimpleNamespace(write_file=_write_file))
    backend._get_client = MethodType(lambda self: fake_client, backend)
    backend._ensure_parent_directory = MethodType(lambda self, _path: None, backend)

    result = backend.write("/home/gem/user-data/outputs/report.md", "ok")

    assert result.error is None
    assert result.path == "/home/gem/user-data/outputs/report.md"
    assert calls[0]["file"] == "/home/gem/user-data/outputs/report.md"


def test_provisioner_glob_root_searches_readable_roots(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    calls = []

    def _find_files(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=SimpleNamespace(files=[f"{kwargs['path']}/match.md"]))

    fake_client = SimpleNamespace(file=SimpleNamespace(find_files=_find_files))
    backend._get_client = MethodType(lambda self: fake_client, backend)

    result = backend.glob("**/*.md")

    assert [call["path"] for call in calls] == ["/home/gem/user-data", "/home/gem/skills"]
    assert [item["path"] for item in result.matches] == [
        "/home/gem/skills/match.md",
        "/home/gem/user-data/match.md",
    ]


@pytest.mark.parametrize(
    ("encoding", "expected"),
    [
        (None, b"SGVsbG8="),
        ("base64", b"Hello"),
    ],
)
def test_provisioner_read_binary_preserves_or_decodes_base64_content(monkeypatch, encoding, expected) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    fake_client = SimpleNamespace(
        file=SimpleNamespace(
            read_file=lambda **_kwargs: SimpleNamespace(data=SimpleNamespace(content="SGVsbG8=", encoding=encoding))
        )
    )
    backend._get_client = MethodType(lambda self: fake_client, backend)

    assert backend._read_binary("/home/gem/user-data/outputs/file.bin") == expected


def test_provisioner_read_file_base64_reads_temp_file_not_shell_output(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    expected = base64.b64encode(b"\x89PNG\r\n\x1a\nimage-bytes").decode("ascii")
    shell_calls = []

    def _exec_command(**kwargs):
        shell_calls.append(kwargs)
        return SimpleNamespace(
            data=SimpleNamespace(
                exit_code=0,
                output="broken\n[... Observation truncated due to length ...]\nbase64",
            )
        )

    def _read_file(**kwargs):
        assert kwargs["file"].startswith("/tmp/yuxi-read-file-")
        return SimpleNamespace(data=SimpleNamespace(content=expected))

    fake_client = SimpleNamespace(
        shell=SimpleNamespace(exec_command=_exec_command),
        file=SimpleNamespace(read_file=_read_file),
    )
    backend._get_client = MethodType(lambda self: fake_client, backend)

    result = backend._read_file_base64("/home/gem/user-data/image.png")

    assert result == expected
    assert len(shell_calls) == 2
    assert shell_calls[0]["command"].startswith("python3 -c")
    assert shell_calls[1]["command"].startswith("rm -f /tmp/yuxi-read-file-")


@pytest.mark.parametrize(
    ("path", "base64_content"),
    [
        ("/home/gem/user-data/image.png", "iVBORw0KGgo="),
        ("/home/gem/user-data/image.gif", "R0lGODlh"),
    ],
)
def test_provisioner_read_treats_image_files_as_base64(monkeypatch, path, base64_content) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    monkeypatch.setattr(backend, "_file_size_bytes", lambda _path: 6)
    monkeypatch.setattr(backend, "_read_binary", lambda path, offset=0, limit=None: pytest.fail("file API used"))
    monkeypatch.setattr(backend, "_read_file_base64", lambda _path: base64_content)

    result = backend.read(path)

    assert result.file_data == {"content": base64_content, "encoding": "base64"}


def test_provisioner_read_rejects_large_known_binary_before_read(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    read_calls: list[tuple[str, int, int | None]] = []
    monkeypatch.setattr(backend, "_file_size_bytes", lambda _path: MAX_BINARY_BYTES + 1)

    def _read_binary(path, offset=0, limit=None):
        read_calls.append((path, offset, limit))
        return b"\x89PNG\r\n\x1a\n"

    monkeypatch.setattr(backend, "_read_binary", _read_binary)
    monkeypatch.setattr(backend, "_read_file_base64", lambda _path: pytest.fail("binary file was read"))

    result = backend.read("/home/gem/user-data/large.png")

    assert result.file_data is None
    assert result.error == f"Binary file exceeds maximum preview size of {MAX_BINARY_BYTES} bytes"
    assert read_calls == []


def test_provisioner_read_rejects_unknown_binary(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    read_calls: list[tuple[str, int, int | None]] = []

    def _read_binary(path, offset=0, limit=None):
        read_calls.append((path, offset, limit))
        return b"\x00binary prefix"

    monkeypatch.setattr(backend, "_read_binary", _read_binary)

    result = backend.read("/home/gem/user-data/large.unknown")

    assert result.file_data is None
    assert result.error == "read_file only supports UTF-8 text and image files. This file type is not supported."
    assert read_calls == [("/home/gem/user-data/large.unknown", 0, 2000)]


def test_provisioner_read_rejects_unknown_file_on_sandbox_utf8_decode_failure(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    def _read_binary_raises(path, offset=0, limit=None):
        raise RuntimeError("'utf-8' codec can't decode byte 0x89 in position 0")

    monkeypatch.setattr(backend, "_read_binary", _read_binary_raises)

    result = backend.read("/home/gem/user-data/uploaded.bin")

    assert result.file_data is None
    assert result.error == "read_file only supports UTF-8 text and image files. This file type is not supported."


@pytest.mark.parametrize("extension", ["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx"])
def test_provisioner_read_routes_documents_to_ocr(monkeypatch, extension: str) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    monkeypatch.setattr(backend, "_file_size_bytes", lambda _path: 8)
    monkeypatch.setattr(backend, "_read_binary", lambda *_args, **_kwargs: pytest.fail("document was read"))

    result = backend.read(f"/home/gem/user-data/uploads/document.{extension}")

    assert result.file_data is None
    assert result.error == (
        "read_file does not support PDF or Office documents. Use ocr_parse_file to convert the file to Markdown first."
    )


@pytest.mark.parametrize("extension", ["mp3", "mp4", "wav"])
def test_provisioner_read_rejects_other_known_modalities(monkeypatch, extension: str) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    monkeypatch.setattr(backend, "_file_size_bytes", lambda _path: 8)
    monkeypatch.setattr(backend, "_read_file_base64", lambda _path: pytest.fail("binary file was read"))

    result = backend.read(f"/home/gem/user-data/uploads/media.{extension}")

    assert result.file_data is None
    assert result.error == "read_file only supports UTF-8 text and image files. This file type is not supported."


def test_read_file_tool_returns_multimodal_block_for_small_binary() -> None:
    class _Backend:
        def read(self, path: str, offset: int = 0, limit: int = 100):
            return ReadResult(file_data={"content": "R0lGODlh", "encoding": "base64"})

    middleware = create_agent_filesystem_middleware(
        None,
        backend=CompositeBackend(default=_Backend(), routes={}, artifacts_root=f"{WORKDIR_PATH}/outputs"),
    )
    read_tool = next(tool for tool in middleware.tools if tool.name == "read_file")
    runtime = ToolRuntime(
        state={},
        context=None,
        config={},
        stream_writer=lambda _: None,
        tool_call_id="call-read",
        store=None,
    )

    result = read_tool.func(file_path="/home/gem/user-data/uploads/image.gif", runtime=runtime)

    assert result.status == "success"
    assert result.content_blocks == [{"type": "image", "base64": "R0lGODlh", "mime_type": "image/gif"}]
    assert result.additional_kwargs == {
        "read_file_path": "/home/gem/user-data/uploads/image.gif",
        "read_file_media_type": "image/gif",
    }


def test_provisioner_read_reports_invalid_path(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    result = backend.read("secret.txt")

    assert result.error == "Invalid path 'secret.txt': path must start with /"


def test_provisioner_read_reports_path_traversal(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    result = backend.read("/home/gem/user-data/../secret.txt")

    assert result.error == "Invalid path '/home/gem/user-data/../secret.txt': path traversal is not allowed"


def test_provisioner_read_returns_pagination_window(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    lines = [f"line-{index}" for index in range(10)]
    read_calls: list[tuple[int, int | None]] = []

    def _read_binary(path, offset=0, limit=None):
        read_calls.append((offset, limit))
        window = lines[offset : offset + limit if limit is not None else None]
        return ("\n".join(window) + "\n").encode("utf-8")

    monkeypatch.setattr(backend, "_read_binary", _read_binary)

    result = backend.read("/home/gem/user-data/outputs/report.md", offset=2, limit=3)

    assert read_calls == [(2, 3)]
    assert result.error is None
    assert result.start_line == 3
    assert result.end_line == 5
    assert result.next_offset == 5
    assert result.total_lines is None


def test_provisioner_read_non_positive_limit_reports_no_lines_requested(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    monkeypatch.setattr(backend, "_read_binary", lambda *_args, **_kwargs: pytest.fail("file was inspected"))

    result = backend.read("/home/gem/user-data/outputs/report.md", offset=0, limit=0)

    assert result.no_lines_requested is True
    assert result.file_data is None


def test_provisioner_read_negative_offset_clamps_to_first_line(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    read_calls: list[tuple[int, int | None]] = []

    def _read_binary(path, offset=0, limit=None):
        read_calls.append((offset, limit))
        return b"first\nsecond\n"

    monkeypatch.setattr(backend, "_read_binary", _read_binary)

    result = backend.read("/home/gem/user-data/outputs/report.md", offset=-5, limit=2)

    assert read_calls == [(0, 2)]
    assert result.start_line == 1
    assert result.end_line == 2
    assert result.next_offset == 2


@pytest.mark.asyncio
async def test_provisioner_aread_rejects_outside_path_before_async_client(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    monkeypatch.setattr(
        sandbox_backend_module.httpx,
        "AsyncClient",
        lambda **_kwargs: pytest.fail("unauthorized path constructed async client"),
    )

    result = await backend.aread("/tmp/preview_check.jpg")

    assert result.error == "permission denied for read on '/tmp/preview_check.jpg'"


@pytest.mark.asyncio
async def test_provisioner_aread_returns_pagination_and_closes_http_client(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    read_calls: list[dict] = []

    async def read_file(**kwargs):
        read_calls.append(kwargs)
        return SimpleNamespace(data=SimpleNamespace(content="line-2\nline-3\nline-4\n", encoding="utf-8"))

    owned_http_client = _install_async_file_client(
        monkeypatch,
        backend,
        SimpleNamespace(read_file=read_file),
    )

    result = await backend.aread("/home/gem/user-data/outputs/report.md", offset=2, limit=3)

    assert read_calls == [
        {
            "file": "/home/gem/user-data/outputs/report.md",
            "start_line": 2,
            "end_line": 5,
        }
    ]
    assert result.file_data == {"content": "line-2\nline-3\nline-4\n", "encoding": "utf-8"}
    assert result.start_line == 3
    assert result.end_line == 5
    assert result.next_offset == 5
    assert owned_http_client.closed is True


@pytest.mark.asyncio
async def test_provisioner_aread_image_streams_native_download_without_shell(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    download_calls: list[dict] = []

    async def download_file(**kwargs):
        download_calls.append(kwargs)
        yield b"\x89PNG"
        yield b"image-bytes"

    _install_async_file_client(monkeypatch, backend, SimpleNamespace(download_file=download_file))

    result = await backend.aread("/home/gem/user-data/uploads/image.png")

    assert result.file_data == {
        "content": base64.b64encode(b"\x89PNGimage-bytes").decode("ascii"),
        "encoding": "base64",
    }
    assert download_calls == [
        {
            "path": "/home/gem/user-data/uploads/image.png",
            "request_options": {"timeout_in_seconds": backend._command_timeout_seconds},
        }
    ]


@pytest.mark.asyncio
async def test_provisioner_aread_image_rejects_stream_over_limit(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    monkeypatch.setattr(sandbox_backend_module, "MAX_BINARY_BYTES", 5)
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    async def download_file(**_kwargs):
        yield b"1234"
        yield b"567"

    _install_async_file_client(monkeypatch, backend, SimpleNamespace(download_file=download_file))

    result = await backend.aread("/home/gem/user-data/uploads/large.png")

    assert result.file_data is None
    assert result.error == f"Binary file exceeds maximum preview size of {MAX_BINARY_BYTES} bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "expected_error"),
    [
        (
            "/home/gem/user-data/uploads/document.pdf",
            "read_file does not support PDF or Office documents. "
            "Use ocr_parse_file to convert the file to Markdown first.",
        ),
        (
            "/home/gem/user-data/uploads/audio.mp3",
            "read_file only supports UTF-8 text and image files. This file type is not supported.",
        ),
    ],
)
async def test_provisioner_aread_preserves_known_binary_type_errors(monkeypatch, path, expected_error) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    async def download_file(**_kwargs):
        yield b"content"

    _install_async_file_client(monkeypatch, backend, SimpleNamespace(download_file=download_file))

    result = await backend.aread(path)

    assert result.file_data is None
    assert result.error == expected_error


@pytest.mark.asyncio
async def test_provisioner_aread_rejects_unknown_binary_decode_failure(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    async def read_file(**_kwargs):
        raise RuntimeError("'utf-8' codec can't decode byte 0x89 in position 0")

    _install_async_file_client(monkeypatch, backend, SimpleNamespace(read_file=read_file))

    result = await backend.aread("/home/gem/user-data/uploads/data.unknown")

    assert result.file_data is None
    assert result.error == "read_file only supports UTF-8 text and image files. This file type is not supported."


def test_provisioner_grep_applies_global_max_count_across_roots(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    grep_calls: list[dict] = []

    def _grep_root(pattern, path, glob, max_count):
        grep_calls.append({"path": path, "max_count": max_count})
        count = 3 if path == "/home/gem/user-data" else 2
        matches = [{"path": f"{path}/file-{index}.md", "line": 1, "text": pattern} for index in range(count)]
        truncated = False
        if max_count is not None and len(matches) > max_count:
            matches = matches[:max_count]
            truncated = True
        return GrepResult(matches=matches, truncated=truncated)

    monkeypatch.setattr(backend, "_grep_root", _grep_root)

    result = backend.grep("NEEDLE", path="/", max_count=4)

    assert grep_calls == [{"path": "/home/gem/user-data", "max_count": 4}, {"path": "/home/gem/skills", "max_count": 1}]
    assert len(result.matches) == 4
    assert result.truncated is True


@pytest.fixture
def native_grep_backend(monkeypatch):
    """装配只替换 HTTP 传输的原生搜索后端。"""
    monkeypatch.setattr(sandbox_backend_module, "get_sandbox_provider", lambda: object())
    monkeypatch.setattr(sandbox_backend_module, "sandbox_provisioner_token", lambda: "test-token")
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    monkeypatch.setattr(backend, "_get_connection", lambda: SimpleNamespace(sandbox_url="http://sandbox.test"))
    return backend


@pytest.mark.asyncio
async def test_provisioner_grep_maps_native_results_for_sync_and_async(monkeypatch, native_grep_backend) -> None:
    """原生搜索的字段、过滤参数与异步入口保持一致。"""
    backend = native_grep_backend
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"])
        assert url == "http://sandbox.test/v1/file/grep"
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "success": True,
                "data": {
                    "path": "/home/gem/user-data",
                    "pattern": "a.b",
                    "matches": [
                        {"file": "/home/gem/user-data/nested/note:one.py", "line_number": 2, "line_content": "a.b"}
                    ],
                    "truncated": True,
                },
            },
        )

    monkeypatch.setattr(sandbox_backend_module.httpx, "post", post)
    for result in (
        backend.grep("a.b", path="/home/gem/user-data", glob="nested/**/*.py", max_count=1),
        await backend.agrep("a.b", path="/home/gem/user-data", glob="nested/**/*.py", max_count=1),
    ):
        assert result.error is None
        assert result.matches == [{"path": "/home/gem/user-data/nested/note:one.py", "line": 2, "text": "a.b"}]
        assert result.truncated is True
    assert all(call["fixed_strings"] is True and call["recursive"] is True for call in calls)
    assert all(call["include"] == ["/home/gem/user-data/nested/**/*.py"] for call in calls)
    assert all(call["max_results"] == 1 for call in calls)


@pytest.mark.parametrize("failure", ["remote", "exception", "malformed", "oversize", "missing", "http"])
def test_provisioner_grep_handles_native_failures(monkeypatch, native_grep_backend, failure) -> None:
    """缺失可读根返回空结果，其他失败不能伪装成无匹配。"""
    backend = native_grep_backend

    def post(url, **kwargs):
        if failure == "exception":
            raise httpx.ReadTimeout("request timed out")
        payload = {
            "success": True,
            "data": {
                "path": "/home/gem/user-data",
                "pattern": "x",
                "matches": [
                    {
                        "file": "/home/gem/user-data/a",
                        "line_number": 1,
                        "line_content": "x" * (backend._max_output_bytes + 1),
                    }
                ],
                "truncated": None if failure == "malformed" else False,
            },
        }
        if failure in {"remote", "missing"}:
            payload = {
                "success": False,
                "message": "read failed",
                "data": {"error_type": "not_found" if failure == "missing" else "permission_denied"},
            }
        return httpx.Response(503 if failure == "http" else 200, request=httpx.Request("POST", url), json=payload)

    monkeypatch.setattr(sandbox_backend_module.httpx, "post", post)
    result = backend.grep("x", path="/home/gem/user-data")
    if failure == "missing":
        assert result.error is None
        assert result.matches == []
    else:
        assert result.error
        assert result.matches is None


@pytest.mark.parametrize(("path", "glob"), [("/etc", None), ("/home/gem/user-data", "../private/*")])
def test_provisioner_grep_rejects_outside_requests_before_native_call(monkeypatch, path, glob) -> None:
    """非法请求必须在调用搜索服务前被拒绝。"""
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    def unexpected_client():
        pytest.fail("invalid search reached sandbox")

    monkeypatch.setattr(backend, "_get_connection", unexpected_client)
    result = backend.grep("secret", path=path, glob=glob)
    assert result.error
    assert result.matches is None


@pytest.mark.asyncio
async def test_provisioner_agrep_preserves_timeout(monkeypatch) -> None:
    """异步搜索停滞时恢复 DeepAgents 的有界工具响应。"""
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    monkeypatch.setattr(sandbox_backend_module, "ASYNC_GREP_TIMEOUT", 0.01)
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    release = threading.Event()

    def slow_grep(*_args, **_kwargs):
        release.wait(1)
        return GrepResult(matches=[])

    monkeypatch.setattr(backend, "grep", slow_grep)
    try:
        result = await backend.agrep("PEARL")
        assert result.error is not None
        assert "timed out" in result.error
    finally:
        release.set()


def test_provisioner_download_files_distinguishes_invalid_path_from_read_failure(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    def download_file(**_kwargs):
        raise RuntimeError("sandbox read timeout")

    backend._get_client = MethodType(
        lambda self: SimpleNamespace(file=SimpleNamespace(download_file=download_file)),
        backend,
    )

    responses = backend.download_files(["bad-path", "/home/gem/user-data/read-failed"])

    assert responses[0].error == "invalid_path"
    assert responses[1].error.startswith("read_failed")


def test_provisioner_download_files_treats_sandbox_404_as_missing(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    def download_file(**_kwargs):
        raise RuntimeError("status_code: 404, body: {'message': 'File does not exist'}")

    backend._get_client = MethodType(
        lambda self: SimpleNamespace(file=SimpleNamespace(download_file=download_file)),
        backend,
    )

    responses = backend.download_files(["/home/gem/user-data/outputs/missing.md"])

    assert responses[0].error == "file_not_found"


def test_provisioner_execute_returns_error_response_on_client_failure(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    class _FakeClient:
        class shell:
            @staticmethod
            def exec_command(**kwargs):
                raise RuntimeError("boom")

    backend._get_client = MethodType(lambda self: _FakeClient(), backend)
    result = backend.execute("echo hi")

    assert result.exit_code == 1
    assert "Error:" in result.output


def test_provisioner_execute_applies_timeout_to_command_and_http_request(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    calls: list[dict] = []

    def execute(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=SimpleNamespace(exit_code=0, output="done"))

    fake_client = SimpleNamespace(shell=SimpleNamespace(exec_command=execute))
    backend._get_client = MethodType(lambda self: fake_client, backend)

    result = backend.execute("echo hi", timeout=300)

    assert result.exit_code == 0
    assert calls == [
        {
            "command": "echo hi",
            "timeout": 300,
            "hard_timeout": 300,
            "request_options": {"timeout_in_seconds": 300},
        }
    ]


def test_provisioner_download_files_streams_binary_bytes(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    calls: list[dict] = []

    def download_file(**kwargs):
        calls.append(kwargs)
        return iter([b"\x00\xff", b"binary"])

    backend._get_client = MethodType(
        lambda self: SimpleNamespace(file=SimpleNamespace(download_file=download_file)),
        backend,
    )

    response = backend.download_files(["/home/gem/user-data/outputs/demo.bin"])[0]

    assert response.content == b"\x00\xffbinary"
    assert calls == [
        {
            "path": "/home/gem/user-data/outputs/demo.bin",
            "request_options": {"timeout_in_seconds": backend._command_timeout_seconds},
        }
    ]


def test_authorized_download_enforces_limit_during_actual_transfer(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(
        thread_id="thread-1", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111"
    )
    content = b"12345678"
    execute_calls = 0

    def execute(_command):
        nonlocal execute_calls
        execute_calls += 1
        return SimpleNamespace(
            exit_code=0,
            output=f"YUXI_FILE_SNAPSHOT {len(content)} {hashlib.sha256(content).hexdigest()}",
            truncated=False,
        )

    backend.execute = execute
    backend._get_client = MethodType(
        lambda self: SimpleNamespace(
            file=SimpleNamespace(download_file=lambda **_kwargs: iter([content[:4], content[4:]]))
        ),
        backend,
    )
    target_path = tmp_path / "snapshot.bin"

    with pytest.raises(ValueError, match="exceeds transfer limit"):
        backend.download_authorized_file_to_path(
            "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/growing.bin",
            str(target_path),
            max_bytes=5,
        )

    assert not target_path.exists()
    assert execute_calls == 2


def test_authorized_download_maps_sandbox_overflow_to_stable_limit_error() -> None:
    with pytest.raises(ValueError, match="exceeds transfer limit"):
        sandbox_backend_module._raise_authorized_path_operation_error(
            "Traceback: OverflowError: file exceeds transfer limit",
            "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/large.bin",
            "snapshot failed",
        )


def test_authorized_snapshot_attempts_cleanup_after_snapshot_command_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(
        thread_id="thread-1", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111"
    )
    execute_results = iter(
        [
            SimpleNamespace(exit_code=1, output="connection lost", truncated=False),
            SimpleNamespace(exit_code=0, output="", truncated=False),
        ]
    )
    backend.execute = lambda _command: next(execute_results)

    with pytest.raises(RuntimeError, match="connection lost"):
        backend.download_authorized_file_to_path(
            "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.txt",
            str(tmp_path / "report.txt"),
            max_bytes=1024,
        )

    with pytest.raises(StopIteration):
        next(execute_results)


def test_authorized_download_preserves_missing_and_symlink_boundary_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(
        thread_id="thread-1", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111"
    )

    for output, expected_error in (
        ("FileNotFoundError: [Errno 2] No such file or directory", FileNotFoundError),
        ("OSError: [Errno 40] Too many levels of symbolic links", PermissionError),
        ("IsADirectoryError: source is a directory", IsADirectoryError),
    ):
        execute_results = iter(
            [
                SimpleNamespace(exit_code=1, output=output, truncated=False),
                SimpleNamespace(exit_code=0, output="", truncated=False),
            ]
        )
        backend.execute = lambda _command: next(execute_results)
        with pytest.raises(expected_error):
            backend.download_authorized_file_to_path(
                "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/file.txt",
                str(tmp_path / "file.txt"),
                max_bytes=1024,
            )


def test_authorized_download_recovers_snapshot_metadata_when_first_stdout_is_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(
        thread_id="thread-1", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111"
    )
    content = b"live bytes"
    marker = f"YUXI_FILE_SNAPSHOT {len(content)} {hashlib.sha256(content).hexdigest()}"
    execute_results = iter(
        [
            SimpleNamespace(exit_code=0, output="", truncated=False),
            SimpleNamespace(exit_code=0, output=marker, truncated=False),
            SimpleNamespace(exit_code=0, output="", truncated=False),
        ]
    )
    backend.execute = lambda _command: next(execute_results)
    backend._get_client = MethodType(
        lambda self: SimpleNamespace(file=SimpleNamespace(download_file=lambda **_kwargs: iter([content]))),
        backend,
    )
    target = tmp_path / "file.txt"

    size = backend.download_authorized_file_to_path(
        "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/file.txt",
        str(target),
        max_bytes=1024,
    )

    assert size == len(content)
    assert target.read_bytes() == content


def test_authorized_upload_rejects_symlink_parent_as_permission_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(
        thread_id="thread-1", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111"
    )
    backend._get_client = MethodType(
        lambda self: SimpleNamespace(
            file=SimpleNamespace(upload_file=lambda **_kwargs: SimpleNamespace(success=True, message=""))
        ),
        backend,
    )
    backend.execute = lambda _command: SimpleNamespace(
        exit_code=1,
        output="NotADirectoryError: [Errno 20] Not a directory: 'escape-dir'",
        truncated=False,
    )
    source = tmp_path / "source.txt"
    source.write_text("safe", encoding="utf-8")

    with pytest.raises(PermissionError):
        backend.upload_authorized_file_from_path(
            "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/escape-dir/file.txt",
            str(source),
        )


def test_authorized_snapshot_cleanup_failure_blocks_download(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(
        thread_id="thread-1", uid="user-1", workdir_path="projects/11111111-1111-4111-8111-111111111111"
    )
    content = b"report"
    execute_results = iter(
        [
            SimpleNamespace(
                exit_code=0,
                output=f"YUXI_FILE_SNAPSHOT {len(content)} {hashlib.sha256(content).hexdigest()}",
                truncated=False,
            ),
            SimpleNamespace(exit_code=1, output="cleanup failed", truncated=False),
        ]
    )
    backend.execute = lambda _command: next(execute_results)
    backend._get_client = MethodType(
        lambda self: SimpleNamespace(file=SimpleNamespace(download_file=lambda **_kwargs: iter([content]))),
        backend,
    )
    target = tmp_path / "report.txt"

    with pytest.raises(RuntimeError, match="cleanup failed"):
        backend.download_authorized_file_to_path(
            "/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111/report.txt",
            str(target),
            max_bytes=1024,
        )

    assert not target.exists()


def test_workdir_paths_are_workspace_relative_and_reject_symlinks(monkeypatch, tmp_path) -> None:
    from yuxi.agents.backends import paths as backend_paths
    from yuxi.workspace import paths

    monkeypatch.setattr(paths, "get_user_data_dir", lambda: tmp_path / "user-data")
    paths.ensure_user_workspace("user-1")
    projects = paths.user_workspace_dir("user-1") / "projects"
    projects.mkdir()
    (projects / "11111111-1111-4111-8111-111111111111").mkdir()

    assert backend_paths.runtime_workdir_path("projects/11111111-1111-4111-8111-111111111111") == WORKDIR_PATH
    assert backend_paths.runtime_workdir_path("agents/skills") == "/home/gem/user-data/agents/skills"
    assert (
        paths.user_workdir_host_dir("user-1", "projects/11111111-1111-4111-8111-111111111111")
        == projects / "11111111-1111-4111-8111-111111111111"
    )

    for unsafe in ("../escape", "/absolute", "agents//skills", "https://example.com/repo"):
        with pytest.raises(ValueError):
            backend_paths.runtime_workdir_path(unsafe)

    outside = paths.user_workspace_dir("user-1") / "outside"
    outside.mkdir()
    linked_id = "22222222-2222-4222-8222-222222222222"
    (projects / linked_id).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接或非目录组件"):
        paths.user_workdir_host_dir("user-1", f"projects/{linked_id}")

    file_id = "33333333-3333-4333-8333-333333333333"
    (projects / file_id).write_text("file", encoding="utf-8")
    with pytest.raises(ValueError, match="符号链接或非目录组件"):
        paths.user_workdir_host_dir("user-1", f"projects/{file_id}")
