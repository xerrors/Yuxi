from types import SimpleNamespace

import pytest

import yuxi.modules.workspace.services.bindings as svc
from yuxi.modules.workspace.workdir import Workdir
from yuxi.shared.errors import DomainError, ResourceNotFoundError


def test_workdir_access_resolves_only_scope_relative_paths():
    workspace = object()
    workdir = Workdir("projects/11111111-1111-4111-8111-111111111111", workspace)
    access = svc.AuthorizedWorkdir(
        session_record_id=1,
        thread_id="thread-1",
        workdir=workdir,
    )

    assert access.workdir.workspace is workspace
    assert access.workdir.resolve_path("/") == "/projects/11111111-1111-4111-8111-111111111111"
    assert access.workdir.resolve_path("/outputs/report.md") == "/projects/11111111-1111-4111-8111-111111111111/outputs/report.md"
    assert access.workdir.scope_path("/projects/11111111-1111-4111-8111-111111111111/outputs/report.md") == "/outputs/report.md"
    for invalid in ("relative.txt", "/../escape", "\\escape"):
        with pytest.raises(ValueError):
            access.workdir.resolve_path(invalid)


@pytest.mark.asyncio
async def test_binding_uses_project_workdir(monkeypatch: pytest.MonkeyPatch):
    agent_session = SimpleNamespace(
        id=1,
        thread_id="thread-1",
        uid="user-1",
        status="active",
        project_id="project-1",
    )

    class _SessionRepository:
        def __init__(self, _db):
            pass

        async def get_session_by_thread_id(self, _thread_id):
            return agent_session

    opened = {}

    class _Workdir:
        relative_path = "projects/11111111-1111-4111-8111-111111111111"
        root_path = f"/{relative_path}"
        workspace = SimpleNamespace(uid="user-1")

        @classmethod
        def open_existing(cls, uid, workdir_path):
            opened.update(uid=uid, workdir_path=workdir_path)
            return cls()

    class _ProjectRepository:
        def __init__(self, _db):
            pass

        async def get_for_user(self, project_id, uid):
            assert (project_id, uid) == ("project-1", "user-1")
            return SimpleNamespace(
                id="project-1",
                uid="user-1",
                workdir_path="projects/11111111-1111-4111-8111-111111111111",
                directory_mode="managed",
            )

    monkeypatch.setattr(svc, "SessionRepository", _SessionRepository)
    monkeypatch.setattr(svc, "ProjectRepository", _ProjectRepository)
    monkeypatch.setattr(svc, "Workdir", _Workdir)

    binding = await svc.resolve_authorized_workdir(thread_id="thread-1", uid="user-1", db=object())

    assert binding.workdir_path == "projects/11111111-1111-4111-8111-111111111111"
    assert binding.workdir.root_path == "/projects/11111111-1111-4111-8111-111111111111"
    assert binding.workdir.workspace.uid == "user-1"
    assert opened == {"uid": "user-1", "workdir_path": "projects/11111111-1111-4111-8111-111111111111"}


@pytest.mark.asyncio
async def test_binding_rejects_cross_user_session(monkeypatch: pytest.MonkeyPatch):
    agent_session = SimpleNamespace(
        id=1,
        thread_id="thread-1",
        uid="other-user",
        status="active",
        project_id="project-1",
    )

    class _SessionRepository:
        def __init__(self, _db):
            pass

        async def get_session_by_thread_id(self, _thread_id):
            return agent_session

    monkeypatch.setattr(svc, "SessionRepository", _SessionRepository)

    with pytest.raises(DomainError) as exc:
        await svc.resolve_authorized_workdir(thread_id="thread-1", uid="user-1", db=object())

    assert isinstance(exc.value, ResourceNotFoundError)


@pytest.mark.asyncio
async def test_binding_rejects_same_user_from_other_app():
    """Workdir executor 不接受同 UID 的另一个 APP 身份。"""
    agent_session = SimpleNamespace(uid="user-1", app_id="app-a", status="active")
    with pytest.raises(DomainError) as exc:
        await svc.resolve_authorized_session_workdir(agent_session=agent_session, uid="user-1", app_id="app-b", db=object())
    assert isinstance(exc.value, ResourceNotFoundError)


@pytest.mark.asyncio
async def test_binding_resolves_project_workdir_without_session_path(monkeypatch: pytest.MonkeyPatch):
    agent_session = SimpleNamespace(
        id=1,
        thread_id="thread-1",
        uid="user-1",
        status="active",
        project_id="project-1",
    )

    class _SessionRepository:
        def __init__(self, _db):
            pass

        async def get_session_by_thread_id(self, _thread_id):
            return agent_session

    class _ProjectRepository:
        def __init__(self, _db):
            pass

        async def get_for_user(self, project_id, uid):
            assert (project_id, uid) == ("project-1", "user-1")
            return SimpleNamespace(
                id="project-1",
                uid="user-1",
                workdir_path="client/demo",
                directory_mode="linked",
            )

    class _Workdir:
        relative_path = "client/demo"

        @classmethod
        def open_existing(cls, uid, workdir_path):
            assert (uid, workdir_path) == ("user-1", "client/demo")
            return cls()

    monkeypatch.setattr(svc, "SessionRepository", _SessionRepository)
    monkeypatch.setattr(svc, "ProjectRepository", _ProjectRepository)
    monkeypatch.setattr(svc, "Workdir", _Workdir)

    binding = await svc.resolve_authorized_workdir(thread_id="thread-1", uid="user-1", db=object())

    assert binding.workdir_path == "client/demo"


@pytest.mark.asyncio
async def test_ensure_linked_workdir_opens_existing_without_materializing(monkeypatch: pytest.MonkeyPatch):
    agent_session = SimpleNamespace(id=1, thread_id="thread-1", project_id="project-1", uid="user-1")

    async def resolve_binding(**_kwargs):
        return svc.WorkdirBinding(
            uid="user-1",
            workdir_path="client/demo",
        )

    opened = []

    class _Workdir:
        @classmethod
        def open_existing(cls, uid, workdir_path):
            opened.append((uid, workdir_path))

    monkeypatch.setattr(svc, "resolve_session_workdir_binding", resolve_binding)
    monkeypatch.setattr(svc, "Workdir", _Workdir)

    result = await svc.ensure_session_workdir_available(
        agent_session=agent_session,
        uid="user-1",
        db=object(),
    )

    assert result == "client/demo"
    assert opened == [("user-1", "client/demo")]
