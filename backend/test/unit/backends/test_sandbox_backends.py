"""Tests for sandbox backend components."""

from __future__ import annotations

import threading
from types import MethodType, SimpleNamespace

import pytest
from deepagents.backends.protocol import GlobResult
from yuxi.agents.backends.composite import (
    CustomCompositeBackend,
    create_agent_composite_backend,
    create_agent_filesystem_middleware,
)
from yuxi.agents.backends.sandbox import resolve_virtual_path, sandbox_id_for_thread
from yuxi.agents.backends.sandbox.backend import ProvisionerSandboxBackend
from yuxi.agents.middlewares.skills import SkillsMiddleware
from yuxi.utils.paths import VIRTUAL_PATH_CONVERSATION_HISTORY, VIRTUAL_PATH_LARGE_TOOL_RESULTS


def _runtime(
    *,
    thread_id: str | None = "thread-1",
    uid: str | None = "user-1",
    skills: list[str] | None = None,
    readable_skills: list[str] | None = None,
    visible_kbs: list[dict] | None = None,
):
    configurable = {"thread_id": thread_id, "uid": uid} if thread_id and uid else {}
    return SimpleNamespace(
        config={"configurable": configurable},
        context=SimpleNamespace(
            skills=skills or [],
            _readable_skills=readable_skills,
            _visible_knowledge_bases=visible_kbs or [],
            uid=uid,
        ),
    )


def test_create_agent_composite_backend_uses_prepared_readable_skills(monkeypatch):
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())

    backend = create_agent_composite_backend(
        _runtime(readable_skills=["reporter"], visible_kbs=[{"slug": "db-1", "name": "Docs"}])
    )

    assert isinstance(backend.default, ProvisionerSandboxBackend)
    assert backend.default._readable_skills == ["reporter"]
    assert backend.artifacts_root == "/home/gem/user-data/outputs"
    assert "/skills/" in backend.routes
    assert "/home/gem/kbs/" not in backend.routes


def test_create_agent_composite_backend_requires_thread_id():
    with pytest.raises(ValueError, match="thread_id is required"):
        create_agent_composite_backend(_runtime(thread_id=None))


def test_create_agent_composite_backend_ignores_unprepared_context_skills(monkeypatch):
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())

    backend = create_agent_composite_backend(_runtime(skills=["configured"], readable_skills=None))

    assert backend.default._readable_skills == []


def test_create_agent_composite_backend_uses_split_thread_scopes(monkeypatch):
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    runtime = _runtime(thread_id="child-thread", uid="user-1", readable_skills=["worker-skill"])
    runtime.config["configurable"].update(
        {"file_thread_id": "parent-thread", "skills_thread_id": "child-skills-thread"}
    )

    backend = create_agent_composite_backend(runtime)

    assert backend.default._thread_id == "child-thread"
    assert backend.default._file_thread_id == "parent-thread"
    assert backend.default._skills_thread_id == "child-skills-thread"
    assert backend.default._readable_skills == ["worker-skill"]


def test_create_agent_composite_backend_uses_split_thread_scopes_from_state(monkeypatch):
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    runtime = _runtime(thread_id="child-thread", uid="user-1", readable_skills=["worker-skill"])
    runtime.state = {"file_thread_id": "parent-thread", "skills_thread_id": "child-skills-thread"}

    backend = create_agent_composite_backend(runtime)

    assert backend.default._thread_id == "child-thread"
    assert backend.default._file_thread_id == "parent-thread"
    assert backend.default._skills_thread_id == "child-skills-thread"


def test_create_agent_filesystem_middleware_uses_context_scope(monkeypatch):
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    context = SimpleNamespace(
        thread_id="child-thread",
        uid="user-1",
        file_thread_id="parent-thread",
        skills_thread_id="child-skills-thread",
        _readable_skills=["worker-skill"],
    )

    middleware = create_agent_filesystem_middleware(context=context)
    backend = middleware.backend

    assert backend.default._thread_id == "child-thread"
    assert backend.default._file_thread_id == "parent-thread"
    assert backend.default._skills_thread_id == "child-skills-thread"
    assert backend.default._readable_skills == ["worker-skill"]


def test_create_agent_filesystem_middleware_uses_outputs_for_internal_artifacts() -> None:
    middleware = create_agent_filesystem_middleware(tool_token_limit_before_evict=500)

    assert middleware._tool_token_limit_before_evict == 500
    assert middleware._large_tool_results_prefix == VIRTUAL_PATH_LARGE_TOOL_RESULTS
    assert middleware._conversation_history_prefix == VIRTUAL_PATH_CONVERSATION_HISTORY


def test_custom_composite_glob_only_searches_routes_from_root() -> None:
    class _Backend:
        def __init__(self, name: str):
            self.name = name
            self.calls: list[tuple[str, str]] = []

        def glob(self, pattern: str, path: str = "/") -> GlobResult:
            self.calls.append((pattern, path))
            return GlobResult(matches=[{"path": f"{path.rstrip('/')}/{self.name}.md"}])

    default = _Backend("default")
    routed = _Backend("skill")
    backend = CustomCompositeBackend(default=default, routes={"/skills/": routed})

    result = backend.glob("**/*.md", path="/home/gem/user-data")

    assert result.error is None
    assert default.calls == [("**/*.md", "/home/gem/user-data")]
    assert routed.calls == []


def test_skills_middleware_extracts_slug_for_new_paths() -> None:
    middleware = SkillsMiddleware()
    assert middleware.skills_sources_for_prompt == ["/home/gem/skills/"]
    assert middleware._extract_skill_slug_from_skill_md_path("/home/gem/skills/demo-skill/SKILL.md") == "demo-skill"


def test_resolve_virtual_path_rejects_outside_prefix():
    with pytest.raises(ValueError, match="path must start with"):
        resolve_virtual_path("thread-1", "/etc/passwd", uid="user-1")


def test_resolve_virtual_path_rejects_path_traversal():
    with pytest.raises(ValueError, match="path traversal"):
        resolve_virtual_path("thread-1", "/home/gem/user-data/../secrets", uid="user-1")


def test_sandbox_id_for_thread_is_stable():
    sid1 = sandbox_id_for_thread("thread-1")
    sid2 = sandbox_id_for_thread("thread-1")
    sid3 = sandbox_id_for_thread("thread-2")
    assert sid1 == sid2
    assert sid1 != sid3
    assert len(sid1) == 12


def test_sandbox_id_for_thread_includes_skills_scope():
    parent_only = sandbox_id_for_thread("parent-thread")
    split_scope = sandbox_id_for_thread("parent-thread", "child-skills-thread")

    assert split_scope == sandbox_id_for_thread("parent-thread", "child-skills-thread")
    assert split_scope != parent_only
    assert sandbox_id_for_thread("parent-thread", "parent-thread") == parent_only


def test_provider_uses_distinct_sandbox_scope_for_different_uid(monkeypatch) -> None:
    from yuxi.agents.backends.sandbox.provider import ProvisionerSandboxProvider

    created = []

    class FakeClient:
        def create(self, sandbox_id, thread_id, uid, env, *, file_thread_id=None, skills_thread_id=None):
            created.append((sandbox_id, thread_id, uid, env, file_thread_id, skills_thread_id))
            return SimpleNamespace(sandbox_id=sandbox_id, sandbox_url=f"http://sandbox/{uid}")

        def touch(self, _sandbox_id):
            return True

    provider = ProvisionerSandboxProvider.__new__(ProvisionerSandboxProvider)
    provider._client = FakeClient()
    provider._lock = threading.Lock()
    provider._thread_locks = {}
    provider._connections = {}
    provider._last_touch_at = {}
    provider._touch_interval_seconds = 30
    monkeypatch.setattr("yuxi.agents.backends.sandbox.provider.load_user_agent_env", lambda uid: {"A": uid})

    sandbox_1 = provider.acquire(
        "child-thread",
        uid="user-1",
        file_thread_id="parent-thread",
        skills_thread_id="child-skills-thread",
    )
    sandbox_2 = provider.acquire(
        "child-thread",
        uid="user-2",
        file_thread_id="parent-thread",
        skills_thread_id="child-skills-thread",
    )

    assert sandbox_1 != sandbox_2
    assert created[0][2] == "user-1"
    assert created[1][2] == "user-2"


def test_provider_get_create_if_missing_ensures_expected_split_scope(monkeypatch) -> None:
    from yuxi.agents.backends.sandbox.provider import ProvisionerSandboxProvider

    calls = []

    class FakeClient:
        def create(self, sandbox_id, thread_id, uid, env, *, file_thread_id=None, skills_thread_id=None):
            calls.append((sandbox_id, thread_id, uid, env, file_thread_id, skills_thread_id))
            return SimpleNamespace(sandbox_id=sandbox_id, sandbox_url="http://sandbox")

        def discover(self, _sandbox_id):
            raise AssertionError("create_if_missing should ensure sandbox through provisioner create")

    provider = ProvisionerSandboxProvider.__new__(ProvisionerSandboxProvider)
    provider._client = FakeClient()
    provider._lock = threading.Lock()
    provider._thread_locks = {}
    provider._connections = {}
    provider._last_touch_at = {}
    provider._touch_interval_seconds = 30
    monkeypatch.setattr("yuxi.agents.backends.sandbox.provider.load_user_agent_env", lambda uid: {"A": uid})

    connection = provider.get(
        "child-thread",
        uid="user-1",
        create_if_missing=True,
        file_thread_id="parent-thread",
        skills_thread_id="child-skills-thread",
    )

    sandbox_id = sandbox_id_for_thread("parent-thread", "child-skills-thread", uid="user-1")
    assert connection.sandbox_id == sandbox_id
    assert connection.file_thread_id == "parent-thread"
    assert connection.skills_thread_id == "child-skills-thread"
    assert calls == [
        (
            sandbox_id,
            "child-thread",
            "user-1",
            {"A": "user-1"},
            "parent-thread",
            "child-skills-thread",
        )
    ]


def test_provisioner_uses_file_and_skills_thread_ids(monkeypatch) -> None:
    provider_calls = []
    synced = []

    class FakeProvider:
        def get(self, thread_id, **kwargs):
            provider_calls.append((thread_id, kwargs))
            return SimpleNamespace(sandbox_url="http://sandbox")

    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: FakeProvider())
    monkeypatch.setattr(
        "yuxi.agents.backends.sandbox.backend.sync_thread_readable_skills",
        lambda thread_id, skills: synced.append((thread_id, skills)),
    )

    backend = ProvisionerSandboxBackend(
        thread_id="child-thread",
        uid="user-1",
        readable_skills=["worker-skill"],
        file_thread_id="parent-thread",
        skills_thread_id="child-skills-thread",
    )
    backend._build_client = MethodType(lambda self, sandbox_url: SimpleNamespace(url=sandbox_url), backend)

    client = backend._get_client()

    assert client.url == "http://sandbox"
    assert synced == [("child-skills-thread", ["worker-skill"])]
    assert provider_calls == [
        (
            "child-thread",
            {
                "uid": "user-1",
                "create_if_missing": True,
                "file_thread_id": "parent-thread",
                "skills_thread_id": "child-skills-thread",
            },
        )
    ]


def test_provisioner_denies_reads_outside_allowed_roots(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    result = backend.read("/etc/passwd")

    assert result.error == "permission denied for read on '/etc/passwd'"


def test_provisioner_denies_upload_writes(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    write_result = backend.write("/home/gem/user-data/uploads/blocked.txt", "blocked")
    upload_result = backend.upload_files([("/home/gem/user-data/uploads/blocked.bin", b"blocked")])

    assert write_result.error and "permission denied" in write_result.error
    assert upload_result[0].error == "permission_denied"


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

    assert result.error is None
    assert [call["path"] for call in calls] == ["/home/gem/user-data", "/home/gem/skills"]
    assert [item["path"] for item in result.matches] == [
        "/home/gem/skills/match.md",
        "/home/gem/user-data/match.md",
    ]


def test_provisioner_read_preserves_base64_like_plain_text(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    fake_client = SimpleNamespace(
        file=SimpleNamespace(
            read_file=lambda **_kwargs: SimpleNamespace(data=SimpleNamespace(content="SGVsbG8="))
        )
    )
    backend._get_client = MethodType(lambda self: fake_client, backend)

    result = backend.read("/home/gem/user-data/outputs/base64-looking.txt")

    assert result.error is None
    assert result.file_data == {"content": "SGVsbG8=", "encoding": "utf-8"}


def test_provisioner_read_decodes_explicit_base64(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    fake_client = SimpleNamespace(
        file=SimpleNamespace(
            read_file=lambda **_kwargs: SimpleNamespace(
                data=SimpleNamespace(content="SGVsbG8=", encoding="base64")
            )
        )
    )
    backend._get_client = MethodType(lambda self: fake_client, backend)

    assert backend._read_binary("/home/gem/user-data/outputs/file.bin") == b"Hello"


def test_provisioner_read_reports_binary_files(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")
    monkeypatch.setattr(backend, "_read_binary", lambda path, offset=0, limit=None: b"\x89PNG\r\n\x1a\n")

    result = backend.read("/home/gem/user-data/image.png")

    assert result.error is None
    assert result.file_data is not None
    assert result.file_data["encoding"] == "base64"


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


def test_provisioner_download_files_distinguishes_invalid_path_from_read_failure(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", uid="user-1")

    def _fake_read_binary(path, offset=0, limit=None):
        raise RuntimeError("sandbox read timeout")

    monkeypatch.setattr(backend, "_read_binary", _fake_read_binary)

    responses = backend.download_files(["bad-path", "/home/gem/user-data/read-failed"])

    assert responses[0].error == "invalid_path"
    assert responses[1].error.startswith("read_failed")


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
