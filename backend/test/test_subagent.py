"""SubAgent 单元测试"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from yuxi.storage.postgres.models_business import SubAgent
from yuxi.utils.datetime_utils import utc_now_naive


# =============================================================================
# Router Tests
# =============================================================================

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.routers.subagent_router import subagents_router
from server.utils.auth_middleware import get_admin_user, get_db
from yuxi.storage.postgres.models_business import User


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(subagents_router, prefix="/api")

    async def fake_db():
        return None

    async def fake_admin_user():
        return User(
            username="admin",
            user_id="admin",
            password_hash="x",
            role="admin",
        )

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_admin_user] = fake_admin_user
    return app


def test_list_subagents_returns_data(monkeypatch):
    async def fake_get_all_subagents(_db):
        return [
            {
                "name": "research-agent",
                "description": "Test research agent",
                "system_prompt": "You are a researcher",
                "tools": ["tavily_search"],
                "model": None,
                "is_builtin": True,
                "created_by": "system",
                "updated_by": "system",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }
        ]

    monkeypatch.setattr("server.routers.subagent_router.service.get_all_subagents", fake_get_all_subagents)

    app = _build_app()
    client = TestClient(app)
    resp = client.get("/api/system/subagents")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"][0]["name"] == "research-agent"
    assert payload["data"][0]["is_builtin"] is True


def test_get_single_subagent(monkeypatch):
    async def fake_get_subagent(name, db=None):
        if name == "research-agent":
            return {
                "name": "research-agent",
                "description": "Test research agent",
                "system_prompt": "You are a researcher",
                "tools": ["tavily_search"],
                "model": None,
                "is_builtin": True,
                "created_by": "system",
                "updated_by": "system",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
            }
        return None

    monkeypatch.setattr("server.routers.subagent_router.service.get_subagent", fake_get_subagent)

    app = _build_app()
    client = TestClient(app)
    resp = client.get("/api/system/subagents/research-agent")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert payload["data"]["name"] == "research-agent"


def test_get_single_subagent_not_found(monkeypatch):
    async def fake_get_subagent(name, db=None):
        return None

    monkeypatch.setattr("server.routers.subagent_router.service.get_subagent", fake_get_subagent)

    app = _build_app()
    client = TestClient(app)
    resp = client.get("/api/system/subagents/nonexistent")
    assert resp.status_code == 404, resp.text


def test_create_subagent(monkeypatch):
    captured = {}

    async def fake_create_subagent(data, created_by, db=None):
        captured["data"] = data
        captured["created_by"] = created_by
        return {
            "name": data["name"],
            "description": data["description"],
            "system_prompt": data["system_prompt"],
            "tools": data.get("tools", []),
            "model": data.get("model"),
            "is_builtin": False,
            "created_by": created_by,
            "updated_by": created_by,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }

    monkeypatch.setattr("server.routers.subagent_router.service.create_subagent", fake_create_subagent)

    app = _build_app()
    client = TestClient(app)
    resp = client.post(
        "/api/system/subagents",
        json={
            "name": "my-agent",
            "description": "My custom agent",
            "system_prompt": "You are a helpful assistant",
            "tools": ["tool_a", "tool_b"],
            "model": None,
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert captured["data"]["name"] == "my-agent"
    assert captured["created_by"] == "admin"


def test_create_subagent_duplicate_returns_409(monkeypatch):
    async def fake_create_subagent(data, created_by, db=None):
        raise IntegrityError(
            "duplicate",
            {},
            Exception('duplicate key value violates unique constraint "subagents_pkey"'),
        )

    monkeypatch.setattr("server.routers.subagent_router.service.create_subagent", fake_create_subagent)

    app = _build_app()
    client = TestClient(app)
    resp = client.post(
        "/api/system/subagents",
        json={
            "name": "my-agent",
            "description": "My custom agent",
            "system_prompt": "You are a helpful assistant",
            "tools": [],
            "model": None,
        },
    )
    assert resp.status_code == 409, resp.text


def test_update_subagent(monkeypatch):
    captured = {}

    async def fake_update_subagent(name, data, updated_by, db=None):
        captured["name"] = name
        captured["data"] = data
        captured["updated_by"] = updated_by
        return {
            "name": name,
            "description": data.get("description", "Updated description"),
            "system_prompt": data.get("system_prompt", "Updated prompt"),
            "tools": data.get("tools", []),
            "model": data.get("model"),
            "is_builtin": False,
            "created_by": "admin",
            "updated_by": updated_by,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }

    monkeypatch.setattr("server.routers.subagent_router.service.update_subagent", fake_update_subagent)

    app = _build_app()
    client = TestClient(app)
    resp = client.put(
        "/api/system/subagents/my-agent",
        json={
            "description": "Updated description",
            "system_prompt": "Updated prompt",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["success"] is True
    assert captured["name"] == "my-agent"
    assert captured["updated_by"] == "admin"


def test_update_builtin_subagent_fails(monkeypatch):
    async def fake_update_subagent(name, data, updated_by, db=None):
        raise ValueError("内置 SubAgent 不可编辑")

    monkeypatch.setattr("server.routers.subagent_router.service.update_subagent", fake_update_subagent)

    app = _build_app()
    client = TestClient(app)
    resp = client.put(
        "/api/system/subagents/research-agent",
        json={"description": "Try to update builtin"},
    )
    assert resp.status_code == 400, resp.text


def test_delete_subagent(monkeypatch):
    deleted_name = {"name": None}

    async def fake_delete_subagent(name, db=None):
        deleted_name["name"] = name
        return True

    monkeypatch.setattr("server.routers.subagent_router.service.delete_subagent", fake_delete_subagent)

    app = _build_app()
    client = TestClient(app)
    resp = client.delete("/api/system/subagents/my-agent")
    assert resp.status_code == 200, resp.text
    assert deleted_name["name"] == "my-agent"


def test_delete_builtin_subagent_fails(monkeypatch):
    async def fake_delete_subagent(name, db=None):
        raise ValueError("内置 SubAgent 不可删除")

    monkeypatch.setattr("server.routers.subagent_router.service.delete_subagent", fake_delete_subagent)

    app = _build_app()
    client = TestClient(app)
    resp = client.delete("/api/system/subagents/research-agent")
    assert resp.status_code == 400, resp.text


# =============================================================================
# Repository Tests
# =============================================================================

class TestSubAgentRepository:
    @pytest.mark.asyncio
    async def test_list_all(self):
        from yuxi.repositories.subagent_repository import SubAgentRepository

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            SubAgent(
                name="test-agent",
                description="Test agent",
                system_prompt="You are a test",
                tools=["tool_a"],
                model=None,
                is_builtin=False,
                created_by="admin",
                updated_by="admin",
                created_at=utc_now_naive(),
                updated_at=utc_now_naive(),
            )
        ]
        mock_db.execute.return_value = mock_result

        repo = SubAgentRepository(mock_db)
        result = await repo.list_all()

        assert len(result) == 1
        assert result[0].name == "test-agent"
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_name_found(self):
        from yuxi.repositories.subagent_repository import SubAgentRepository

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = SubAgent(
            name="test-agent",
            description="Test agent",
            system_prompt="You are a test",
            tools=[],
            model=None,
            is_builtin=False,
            created_by="admin",
            updated_by="admin",
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )
        mock_db.execute.return_value = mock_result

        repo = SubAgentRepository(mock_db)
        result = await repo.get_by_name("test-agent")

        assert result is not None
        assert result.name == "test-agent"

    @pytest.mark.asyncio
    async def test_get_by_name_not_found(self):
        from yuxi.repositories.subagent_repository import SubAgentRepository

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        repo = SubAgentRepository(mock_db)
        result = await repo.get_by_name("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_update_can_clear_model_when_provided(self):
        from yuxi.repositories.subagent_repository import SubAgentRepository

        mock_db = AsyncMock()
        repo = SubAgentRepository(mock_db)
        item = SubAgent(
            name="test-agent",
            description="Test agent",
            system_prompt="You are a test",
            tools=[],
            model="gpt-4",
            is_builtin=False,
            created_by="admin",
            updated_by="admin",
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )

        await repo.update(
            item,
            description=None,
            system_prompt=None,
            tools=None,
            model=None,
            model_provided=True,
            updated_by="admin",
        )

        assert item.model is None


# =============================================================================
# Service Tests
# =============================================================================

class TestSubAgentService:
    @pytest.mark.asyncio
    async def test_init_builtin_subagents_creates_agents(self, monkeypatch):
        from yuxi.services import subagent_service as service_module

        created_agents = []

        class MockRepo:
            def __init__(self, session):
                pass

            async def exists_name(self, name):
                return False

            async def create(self, **kwargs):
                created_agents.append(kwargs)
                return MagicMock()

        @asynccontextmanager
        async def mock_session_context(*args, **kwargs):
            yield MagicMock()

        class MockPgManager:
            get_async_session_context = mock_session_context

        monkeypatch.setattr(service_module, "SubAgentRepository", MockRepo)
        monkeypatch.setattr(service_module, "pg_manager", MockPgManager())

        await service_module.init_builtin_subagents()

        assert len(created_agents) == 2
        agent_names = [a["name"] for a in created_agents]
        assert "research-agent" in agent_names
        assert "critique-agent" in agent_names

    @pytest.mark.asyncio
    async def test_get_subagent_specs_returns_list(self, monkeypatch):
        from yuxi.services import subagent_service as service_module

        mock_spec = {
            "name": "test-agent",
            "description": "Test",
            "system_prompt": "You are a test",
            "tools": ["tool_a"],
        }

        class MockRepo:
            def __init__(self, session):
                pass

            async def list_all_specs(self):
                return [mock_spec]

        @asynccontextmanager
        async def mock_session_context(*args, **kwargs):
            yield MagicMock()

        class MockPgManager:
            get_async_session_context = mock_session_context

        monkeypatch.setattr(service_module, "SubAgentRepository", MockRepo)
        monkeypatch.setattr(service_module, "pg_manager", MockPgManager())

        result = await service_module.get_subagent_specs()

        assert len(result) == 1
        assert result[0]["name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_get_subagent_specs_returns_defensive_copy(self, monkeypatch):
        from yuxi.services import subagent_service as service_module

        service_module._subagent_specs_cache = [
            {
                "name": "test-agent",
                "description": "Test",
                "system_prompt": "You are a test",
                "tools": ["tool_a"],
            }
        ]

        first = await service_module.get_subagent_specs()
        first[0]["tools"].append("tool_b")
        second = await service_module.get_subagent_specs()

        assert second[0]["tools"] == ["tool_a"]
        service_module.clear_specs_cache()

    def test_resolve_subagent_tools_does_not_mutate_input(self):
        from yuxi.services import subagent_service as service_module

        mock_tool = MagicMock()
        mock_tool.name = "tool_a"
        specs = [
            {
                "name": "test-agent",
                "description": "Test",
                "system_prompt": "You are a test",
                "tools": ["tool_a"],
            }
        ]

        resolved = service_module.resolve_subagent_tools(specs, [mock_tool])

        assert specs[0]["tools"] == ["tool_a"]
        assert resolved[0]["tools"] == [mock_tool]


# =============================================================================
# Model Tests
# =============================================================================

class TestSubAgentModel:
    def test_to_dict(self):
        now = utc_now_naive()
        agent = SubAgent(
            name="test-agent",
            description="Test agent",
            system_prompt="You are a test",
            tools=["tool_a", "tool_b"],
            model="gpt-4",
            is_builtin=False,
            created_by="admin",
            updated_by="admin",
            created_at=now,
            updated_at=now,
        )

        result = agent.to_dict()

        assert result["name"] == "test-agent"
        assert result["description"] == "Test agent"
        assert result["system_prompt"] == "You are a test"
        assert result["tools"] == ["tool_a", "tool_b"]
        assert result["model"] == "gpt-4"
        assert result["is_builtin"] is False
        assert result["created_by"] == "admin"

    def test_to_subagent_spec(self):
        agent = SubAgent(
            name="test-agent",
            description="Test agent",
            system_prompt="You are a test",
            tools=["tool_a"],
            model="gpt-4",
            is_builtin=False,
            created_by="admin",
            updated_by="admin",
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )

        spec = agent.to_subagent_spec()

        assert spec["name"] == "test-agent"
        assert spec["description"] == "Test agent"
        assert spec["system_prompt"] == "You are a test"
        assert spec["tools"] == ["tool_a"]
        assert spec["model"] == "gpt-4"

    def test_to_subagent_spec_no_model(self):
        agent = SubAgent(
            name="test-agent",
            description="Test agent",
            system_prompt="You are a test",
            tools=[],
            model=None,
            is_builtin=False,
            created_by="admin",
            updated_by="admin",
            created_at=utc_now_naive(),
            updated_at=utc_now_naive(),
        )

        spec = agent.to_subagent_spec()

        assert "model" not in spec


class TestDeepAgentSubagentSelection:
    def test_filter_specs_by_names_empty_selection_returns_empty(self):
        from yuxi.services.subagent_service import filter_specs_by_names

        specs = [
            {"name": "research-agent", "description": "r"},
            {"name": "critique-agent", "description": "c"},
        ]

        filtered, missing = filter_specs_by_names(specs, [])

        assert filtered == []
        assert missing == []

    def test_filter_specs_by_names_none_selection_returns_all(self):
        from yuxi.services.subagent_service import filter_specs_by_names

        specs = [
            {"name": "research-agent", "description": "r"},
            {"name": "critique-agent", "description": "c"},
        ]

        filtered, missing = filter_specs_by_names(specs, None)

        assert filtered == specs
        assert missing == []

    def test_filter_specs_by_names_returns_subset_and_missing(self):
        from yuxi.services.subagent_service import filter_specs_by_names

        specs = [
            {"name": "research-agent", "description": "r"},
            {"name": "critique-agent", "description": "c"},
            {"name": 123, "description": "invalid"},
        ]

        filtered, missing = filter_specs_by_names(
            specs,
            ["research-agent", "missing-agent", "research-agent", ""],
        )

        assert [item["name"] for item in filtered] == ["research-agent"]
        assert missing == ["missing-agent"]

    @pytest.mark.asyncio
    async def test_get_subagents_from_names_filters_and_resolves_tools(self, monkeypatch):
        from yuxi.services import subagent_service as service_module

        async def fake_get_specs(_db=None):
            return [
                {
                    "name": "research-agent",
                    "description": "r",
                    "system_prompt": "s",
                    "tools": ["tool_a"],
                },
                {
                    "name": "critique-agent",
                    "description": "c",
                    "system_prompt": "s",
                    "tools": [],
                },
            ]

        mock_tool = MagicMock()
        mock_tool.name = "tool_a"

        monkeypatch.setattr(service_module, "get_subagent_specs", fake_get_specs)
        monkeypatch.setattr("yuxi.agents.common.toolkits.get_all_tool_instances", lambda: [mock_tool])

        resolved_specs = await service_module.get_subagents_from_names(["research-agent", "missing-agent"])

        assert [item["name"] for item in resolved_specs] == ["research-agent"]
        assert resolved_specs[0]["tools"] == [mock_tool]
