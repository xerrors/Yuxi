from __future__ import annotations

import importlib
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user

agent_router_module = importlib.import_module("server.routers.agent_router")


def _user(role: str = "admin"):
    uid = "admin" if role in {"admin", "superadmin"} else "user"
    return SimpleNamespace(uid=uid, role=role, department_id=1)


def _agent(slug: str, *, backend_id: str = "ChatbotAgent", is_subagent: bool = False):
    return SimpleNamespace(
        id=slug,
        slug=slug,
        name=slug,
        backend_id=backend_id,
        description="",
        icon=None,
        pics=[],
        config_json={},
        share_config={"access_level": "user", "user_uids": ["admin"]},
        is_default=False,
        is_subagent=is_subagent,
        can_manage=True,
    )


class _ListRepo:
    items = [
        _agent("chatbot", backend_id="ChatbotAgent"),
        _agent("worker", backend_id="SubAgentBackend", is_subagent=True),
    ]
    include_subagent_definition_calls: list[bool] = []
    get_definition_calls: list[str] = []

    def __init__(self, _db):
        pass

    async def ensure_default_agent(self):
        return self.items[0]

    async def list_visible(self, *, user, include_subagent_definitions: bool = False):
        del user
        self.include_subagent_definition_calls.append(include_subagent_definitions)
        if include_subagent_definitions:
            return self.items
        return [item for item in self.items if not item.is_subagent]

    async def get_visible_by_slug(self, *, slug, user, kind="main"):
        del user
        if kind == "any":
            self.get_definition_calls.append(slug)
            return next((item for item in self.items if item.slug == slug), None)
        if kind == "subagent":
            return next((item for item in self.items if item.slug == slug and item.is_subagent), None)
        return next((item for item in self.items if item.slug == slug and not item.is_subagent), None)

    async def serialize(self, item, **_kwargs):
        return dict(item.__dict__)


class _CreateRepo(_ListRepo):
    created_payload = None

    async def create(self, **kwargs):
        type(self).created_payload = kwargs
        return _agent(kwargs["slug"], backend_id=kwargs["backend_id"], is_subagent=kwargs["is_subagent"])


class _RejectingCreateRepo(_ListRepo):
    async def create(self, **_kwargs):
        raise ValueError("SubAgentBackend 与 is_subagent 必须保持一致")


def _build_app(monkeypatch, repo_cls, *, role: str = "admin") -> TestClient:
    monkeypatch.setattr(agent_router_module, "AgentRepository", repo_cls)

    app = FastAPI()
    app.include_router(agent_router_module.agent_router, prefix="/api")

    async def fake_db():
        return None

    async def fake_user():
        return _user(role)

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_required_user] = fake_user
    app.dependency_overrides[get_admin_user] = fake_user
    return TestClient(app)


def test_agent_list_excludes_subagents_by_default(monkeypatch):
    _ListRepo.include_subagent_definition_calls = []
    client = _build_app(monkeypatch, _ListRepo)

    response = client.get("/api/agent")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [agent["slug"] for agent in payload["agents"]] == ["chatbot"]
    assert _ListRepo.include_subagent_definition_calls == [False]


def test_agent_management_list_can_include_subagents(monkeypatch):
    _ListRepo.include_subagent_definition_calls = []
    client = _build_app(monkeypatch, _ListRepo)

    response = client.get("/api/agent?include_subagents=true")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [agent["slug"] for agent in payload["agents"]] == ["chatbot", "worker"]
    assert payload["agents"][1]["is_subagent"] is True
    assert _ListRepo.include_subagent_definition_calls == [True]


def test_agent_detail_can_load_subagent_definition(monkeypatch):
    _ListRepo.get_definition_calls = []
    client = _build_app(monkeypatch, _ListRepo)

    response = client.get("/api/agent/worker")

    assert response.status_code == 200, response.text
    assert response.json()["agent"]["slug"] == "worker"
    assert response.json()["agent"]["is_subagent"] is True
    assert _ListRepo.get_definition_calls == ["worker"]


def test_normal_user_can_create_agent(monkeypatch):
    _CreateRepo.created_payload = None
    client = _build_app(monkeypatch, _CreateRepo, role="user")

    response = client.post(
        "/api/agent",
        json={
            "name": "Personal Bot",
            "slug": "personal-bot",
            "backend_id": "ChatbotAgent",
            "share_config": {
                "version": 2,
                "read_scope": {"access_level": "global"},
                "manage_scope": None,
            },
        },
    )

    assert response.status_code == 200, response.text
    assert _CreateRepo.created_payload["created_by"] == "user"
    assert _CreateRepo.created_payload["share_config"] == {
        "version": 2,
        "read_scope": {"access_level": "global"},
        "manage_scope": None,
    }


def test_create_subagent_backend_agent_sets_subagent_flag(monkeypatch):
    _CreateRepo.created_payload = None
    client = _build_app(monkeypatch, _CreateRepo)

    response = client.post(
        "/api/agent",
        json={
            "name": "Worker",
            "slug": "worker",
            "backend_id": "SubAgentBackend",
            "is_subagent": True,
        },
    )

    assert response.status_code == 200, response.text
    assert _CreateRepo.created_payload["backend_id"] == "SubAgentBackend"
    assert _CreateRepo.created_payload["is_subagent"] is True
    assert response.json()["agent"]["is_subagent"] is True


def test_create_subagent_backend_rejects_mismatched_flag(monkeypatch):
    client = _build_app(monkeypatch, _RejectingCreateRepo)

    response = client.post(
        "/api/agent",
        json={
            "name": "Worker",
            "slug": "worker",
            "backend_id": "SubAgentBackend",
            "is_subagent": False,
        },
    )

    assert response.status_code == 422
    assert "is_subagent" in response.json()["detail"]
