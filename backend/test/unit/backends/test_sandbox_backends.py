"""Tests for sandbox backend components."""

from __future__ import annotations

from types import MethodType, SimpleNamespace

import pytest
from yuxi.agents.backends.composite import create_agent_composite_backend
from yuxi.agents.backends.sandbox import resolve_virtual_path, sandbox_id_for_thread
from yuxi.agents.backends.sandbox.backend import ProvisionerSandboxBackend
from yuxi.agents.middlewares.skills_middleware import SkillsMiddleware


def _runtime(
    *,
    thread_id: str | None = "thread-1",
    user_id: str | None = "user-1",
    skills: list[str] | None = None,
    visible_kbs: list[dict] | None = None,
):
    configurable = {"thread_id": thread_id, "user_id": user_id} if thread_id and user_id else {}
    return SimpleNamespace(
        config={"configurable": configurable},
        context=SimpleNamespace(
            skills=skills or [],
            _visible_knowledge_bases=visible_kbs or [],
            user_id=user_id,
        ),
    )


def test_create_agent_composite_backend_uses_provisioner_default(monkeypatch):
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())

    backend = create_agent_composite_backend(
        _runtime(skills=["reporter"], visible_kbs=[{"db_id": "db-1", "name": "Docs"}])
    )

    assert isinstance(backend.default, ProvisionerSandboxBackend)
    assert backend.default._visible_skills == ["reporter"]
    assert "/skills/" in backend.routes
    assert "/home/gem/kbs/" not in backend.routes


def test_create_agent_composite_backend_requires_thread_id():
    with pytest.raises(ValueError, match="thread_id is required"):
        create_agent_composite_backend(_runtime(thread_id=None))


def test_skills_middleware_extracts_slug_for_new_paths() -> None:
    middleware = SkillsMiddleware()
    assert middleware.skills_sources_for_prompt == ["/home/gem/skills/"]
    assert middleware._extract_skill_slug_from_skill_md_path("/home/gem/skills/demo-skill/SKILL.md") == "demo-skill"


def test_resolve_virtual_path_rejects_outside_prefix():
    with pytest.raises(ValueError, match="path must start with"):
        resolve_virtual_path("thread-1", "/etc/passwd", user_id="user-1")


def test_resolve_virtual_path_rejects_path_traversal():
    with pytest.raises(ValueError, match="path traversal"):
        resolve_virtual_path("thread-1", "/home/gem/user-data/../secrets", user_id="user-1")


def test_sandbox_id_for_thread_is_stable():
    sid1 = sandbox_id_for_thread("thread-1")
    sid2 = sandbox_id_for_thread("thread-1")
    sid3 = sandbox_id_for_thread("thread-2")
    assert sid1 == sid2
    assert sid1 != sid3
    assert len(sid1) == 12


def test_provisioner_read_reports_binary_files(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", user_id="user-1")
    monkeypatch.setattr(backend, "_read_binary", lambda path, offset=0, limit=None: b"\x89PNG\r\n\x1a\n")

    result = backend.read("/home/gem/user-data/image.png")

    assert result == "Error: File '/home/gem/user-data/image.png' is binary and cannot be rendered as text"


def test_provisioner_read_reports_invalid_path(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", user_id="user-1")

    def _raise_invalid_path(path, offset=0, limit=None):
        raise ValueError("path traversal is not allowed")

    monkeypatch.setattr(backend, "_read_binary", _raise_invalid_path)

    result = backend.read("../secret.txt")

    assert result == "Error: Invalid path '../secret.txt': path traversal is not allowed"


def test_provisioner_download_files_distinguishes_invalid_path_from_read_failure(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", user_id="user-1")

    def _fake_read_binary(path, offset=0, limit=None):
        if path == "/bad-path":
            raise ValueError("path is required")
        raise RuntimeError("sandbox read timeout")

    monkeypatch.setattr(backend, "_read_binary", _fake_read_binary)

    responses = backend.download_files(["/bad-path", "/read-failed"])

    assert responses[0].error == "invalid_path"
    assert responses[1].error.startswith("read_failed")


def test_provisioner_execute_returns_error_response_on_client_failure(monkeypatch) -> None:
    monkeypatch.setattr("yuxi.agents.backends.sandbox.backend.get_sandbox_provider", lambda: object())
    backend = ProvisionerSandboxBackend(thread_id="thread-1", user_id="user-1")

    class _FakeClient:
        class shell:
            @staticmethod
            def exec_command(**kwargs):
                raise RuntimeError("boom")

    backend._get_client = MethodType(lambda self: _FakeClient(), backend)
    result = backend.execute("echo hi")

    assert result.exit_code == 1
    assert "Error:" in result.output
