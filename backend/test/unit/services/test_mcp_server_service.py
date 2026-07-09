from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.services.mcp.server_service import get_mcp_server_dependency_summary
from yuxi.storage.postgres.models_business import AgentConfig, Department, MCPConnection, MCPServer, Skill

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture
async def server_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Department.__table__.create)
        await conn.run_sync(MCPServer.__table__.create)
        await conn.run_sync(MCPConnection.__table__.create)
        await conn.run_sync(Skill.__table__.create)
        await conn.run_sync(AgentConfig.__table__.create)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def test_dependency_summary_filters_candidates_but_keeps_exact_mcp_matches(server_session):
    dept = Department(id=1, name="Engineering")
    server = MCPServer(
        name="charts",
        transport="streamable_http",
        url="http://charts.local/mcp",
        created_by="tester",
        updated_by="tester",
    )
    connection = MCPConnection(server_name="charts", scope_type="system", scope_id="global", status="active")
    matched_skill = Skill(
        slug="chart-skill",
        name="Chart Skill",
        description="Uses charts",
        mcp_dependencies=["charts"],
        dir_path="skills/chart-skill",
    )
    false_positive_skill = Skill(
        slug="chart-extra-skill",
        name="Chart Extra Skill",
        description="Uses a similarly named MCP",
        mcp_dependencies=["charts-extra"],
        dir_path="skills/chart-extra-skill",
    )
    matched_agent_config = AgentConfig(
        department_id=1,
        agent_id="agent-a",
        name="Agent A",
        config_json={"mcps": ["charts"]},
    )
    false_positive_agent_config = AgentConfig(
        department_id=1,
        agent_id="agent-b",
        name="Agent B",
        config_json={"mcps": ["charts-extra"]},
    )
    server_session.add_all(
        [
            dept,
            server,
            connection,
            matched_skill,
            false_positive_skill,
            matched_agent_config,
            false_positive_agent_config,
        ]
    )
    await server_session.commit()

    summary = await get_mcp_server_dependency_summary(server_session, "charts")

    assert summary["has_references"] is True
    assert summary["connections"] == [{"scope_type": "system", "scope_id": "global", "status": "active"}]
    assert summary["skills"] == [{"slug": "chart-skill", "name": "Chart Skill"}]
    assert summary["agent_configs"] == [{"id": 1, "name": "Agent A", "agent_id": "agent-a"}]
