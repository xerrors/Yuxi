"""真实 PG + HTTP：内置展示名持久化、权限与运行身份隔离。"""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.mcp_router import mcp
from server.routers.skill_router import skills, user_skills
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.agents.skills import service as skill_service
from yuxi.agents.mcp import service as mcp_service
from yuxi.repositories import tool_display_repository as repo
from yuxi.services import resource_display_service as display, mcp_display_service
from yuxi.storage.postgres.models_business import ConfigOption, Skill, MCPServer, User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def fixture(tmp_path, monkeypatch):
    """只在显式隔离数据库运行，所有文件和行均属于此测试。"""
    if os.getenv("TEST_ALLOW_DISPLAY_DB") != "1":
        pytest.skip("Set TEST_ALLOW_DISPLAY_DB=1 with a dedicated PostgreSQL database")
    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
    async with engine.begin() as connection:
        for model in (ConfigOption, Skill, MCPServer):
            await connection.run_sync(lambda sync, model=model: model.__table__.create(sync, checkfirst=True))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    keys = [display.SKILL_NAMES, display.MCP_NAMES, display.MCP_TOOL_NAMES]
    async with sessions() as db:
        assert not (await db.scalars(select(ConfigOption).where(ConfigOption.key.in_(keys)))).all(), "Use empty test DB"
    slug = "fixture-" + uuid.uuid4().hex
    source = tmp_path / "source"
    source.mkdir()
    source_file = source / "SKILL.md"
    source_file.write_text(f"---\nname: {slug}\ndescription: fixture\n---\nimmutable body\n", encoding="utf8")
    monkeypatch.setattr(skill_service, "get_skills_root_dir", lambda: tmp_path / "installed")
    monkeypatch.setattr(
        skill_service,
        "list_builtin_skill_specs",
        lambda: [
            {
                "slug": slug,
                "name": "Original",
                "description": "fixture",
                "source_dir": str(source),
                "tool_dependencies": [],
                "mcp_dependencies": [],
                "skill_dependencies": [],
                "version": "1",
                "content_hash": "fixture",
            }
        ],
    )
    monkeypatch.setattr(mcp_service, "is_builtin_mcp_server", lambda server: server.slug == slug)
    monkeypatch.setattr(mcp_display_service, "is_builtin_mcp_server", lambda server: server.slug == slug)
    monkeypatch.setattr("server.routers.mcp_router.is_builtin_mcp_server", lambda server: server.slug == slug)
    monkeypatch.setattr(
        mcp_service, "_DEFAULT_MCP_SERVERS", {slug: {"transport": "streamable_http", "name": "Original MCP"}}
    )
    monkeypatch.setattr(mcp_service, "_BUILTIN_MCP_SERVER_SLUGS", {slug})
    monkeypatch.setattr(mcp_service, "_RETIRED_BUILTIN_MCP_SERVER_SLUGS", set())
    tool = SimpleNamespace(name="draw_chart", metadata={"id": "stable-tool-id"}, description="Draw", args_schema=None)

    async def discover(_slug):
        return [tool]

    monkeypatch.setattr(mcp_display_service, "inspect_mcp_server_tools", discover)
    monkeypatch.setattr("server.routers.mcp_router.inspect_mcp_server_tools", discover)

    @asynccontextmanager
    async def context():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    monkeypatch.setattr(repo.pg_manager, "get_async_session_context", context)

    async def get_session():
        async with sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(skills, prefix="/api")
    app.include_router(user_skills, prefix="/api")
    app.include_router(mcp, prefix="/api")
    actor = User(uid="fixture-admin", username="fixture-admin", role="admin", password_hash="fixture")

    async def user():
        return actor

    async def admin():
        from fastapi import HTTPException

        if actor.role != "admin":
            raise HTTPException(403)
        return actor

    app.dependency_overrides[get_db] = get_session
    app.dependency_overrides[get_required_user] = user
    app.dependency_overrides[get_admin_user] = admin
    async with sessions() as db:
        await skill_service.init_builtin_skills(db)
        db.add(
            MCPServer(
                slug=slug,
                name="Original MCP",
                transport="streamable_http",
                url="http://fixture.invalid/mcp",
                headers={"Fixture": "keep"},
                command="keep",
                args=["arg"],
                env={"Fixture": "value"},
                created_by="fixture",
                updated_by="fixture",
            )
        )
        await db.commit()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            yield client, sessions, slug, source_file, actor, tool
    finally:
        async with sessions() as db:
            await db.execute(delete(ConfigOption).where(ConfigOption.key.in_(keys)))
            await db.execute(delete(Skill).where(Skill.slug == slug))
            await db.execute(delete(MCPServer).where(MCPServer.slug == slug))
            await db.commit()
        await engine.dispose()


async def test_builtin_names_survive_sync_and_reconnect(fixture):
    """HTTP 写入后同步源数据并用新连接重读，文件与连接参数不变。"""
    client, sessions, slug, source, actor, tool = fixture
    original = source.read_bytes()
    response = await client.put(f"/api/system/skills/{slug}/display-name", json={"display_name": "中文技能"})
    assert response.status_code == 200, response.text
    response = await client.put(f"/api/system/mcp-servers/{slug}/display-name", json={"name": "中文服务"})
    assert response.status_code == 200, response.text
    response = await client.put(
        f"/api/system/mcp-servers/{slug}/tools/draw_chart/display-name", json={"name": "绘制图表"}
    )
    assert response.status_code == 200, response.text
    async with sessions() as db:
        await skill_service.init_builtin_skills(db)
        server = await db.scalar(select(MCPServer).where(MCPServer.slug == slug))
        assert (server.name, server.command, server.args, server.env, server.headers) == (
            "Original MCP",
            "keep",
            ["arg"],
            {"Fixture": "value"},
            {"Fixture": "keep"},
        )
    await mcp_service.ensure_builtin_mcp_servers_in_db()
    assert source.read_bytes() == original
    response = await client.get("/api/system/skills/builtin")
    assert response.status_code == 200, response.text
    assert next(row for row in response.json()["data"] if row["slug"] == slug)["name"] == "中文技能"
    assert (await client.get(f"/api/system/mcp-servers/{slug}")).json()["data"]["name"] == "中文服务"
    row = (await client.get(f"/api/system/mcp-servers/{slug}/tools")).json()["data"][0]
    assert (row["name"], row["id"], row["display_name"]) == ("draw_chart", "stable-tool-id", "绘制图表")
    assert tool.name == "draw_chart" and tool.metadata == {"id": "stable-tool-id"}
    actor.role = "user"
    for path, payload in [
        (f"skills/{slug}", {"display_name": "Forbidden"}),
        (f"mcp-servers/{slug}", {"name": "Forbidden"}),
        (f"mcp-servers/{slug}/tools/draw_chart", {"name": "Forbidden"}),
    ]:
        response = await client.put(f"/api/system/{path}/display-name", json=payload)
        assert response.status_code in (403, 404), response.text
    assert (await repo.read_names(key=display.SKILL_NAMES))[slug] == "中文技能"
    actor.role = "admin"
    response = await client.put(f"/api/system/skills/{slug}/file", json={"path": "SKILL.md", "content": "overwrite"})
    assert response.status_code == 400 and "内置" in response.text, response.text
    assert source.read_bytes() == original


async def test_concurrent_names_and_invalid_input(fixture):
    """并发写同映射保留全部值，非法文本与未知工具没有写入。"""
    client, sessions, slug, source, actor, tool = fixture
    await asyncio.gather(
        *(
            repo.save_name(f"resource-{index}", f"名称{index}", actor.uid, key=display.SKILL_NAMES)
            for index in range(12)
        )
    )
    assert await repo.read_names(key=display.SKILL_NAMES) == {f"resource-{i}": f"名称{i}" for i in range(12)}
    for name in ["<script>", "bad\nname", "bad\u200bname", "x" * 81]:
        response = await client.put(
            f"/api/system/mcp-servers/{slug}/tools/draw_chart/display-name", json={"name": name}
        )
        assert response.status_code == 422, response.text
    response = await client.put(f"/api/system/mcp-servers/{slug}/tools/missing/display-name", json={"name": "不存在"})
    assert response.status_code == 404
    assert await repo.read_names(key=display.MCP_TOOL_NAMES) == {}
