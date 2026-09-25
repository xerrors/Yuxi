from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from yuxi.agents.mcp.service import ensure_builtin_mcp_servers_in_db, get_mcp_tools
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import MCPServer

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def current_loop_pg_manager():
    """让全局 PostgreSQL 引擎绑定到当前测试事件循环。"""
    if pg_manager.async_engine is not None:
        await pg_manager.async_engine.dispose()
    pg_manager._initialized = False
    pg_manager.initialize()
    yield pg_manager


async def test_stdio_mcp_payload_is_rejected_without_side_effects(e2e_client, e2e_headers):
    """恶意 stdio 请求应在持久化和进程启动前被拒绝。"""
    unique_id = uuid.uuid4().hex[:8]
    slug = f"pytest-unsafe-mcp-{unique_id}"
    marker = Path(f"/tmp/{slug}.marker")

    try:
        response = await e2e_client.post(
            "/api/system/mcp-servers",
            headers=e2e_headers,
            json={
                "slug": slug,
                "name": "pytest unsafe MCP",
                "transport": "stdio",
                "command": "sh",
                "args": ["-c", f"touch {marker}"],
            },
        )

        # 如果创建边界回归，继续触发连接测试，以验证本地进程副作用仍会被测试捕获。
        if response.is_success:
            await e2e_client.post(f"/api/system/mcp-servers/{slug}/test", headers=e2e_headers)

        assert response.status_code == 422, response.text

        list_response = await e2e_client.get("/api/system/mcp-servers", headers=e2e_headers)
        assert list_response.status_code == 200, list_response.text
        assert slug not in {server["slug"] for server in list_response.json()["data"]}
        assert not marker.exists(), "stdio MCP payload created a file in the API container"
    finally:
        try:
            cleanup_response = await e2e_client.delete(f"/api/system/mcp-servers/{slug}", headers=e2e_headers)
            assert cleanup_response.status_code in (200, 404), cleanup_response.text
        finally:
            marker.unlink(missing_ok=True)


async def test_legacy_stdio_mcp_is_disabled_without_starting_process(
    e2e_client,
    e2e_headers,
    current_loop_pg_manager,
):
    """历史 stdio 记录应被迁移逻辑和运行时加载双重拦截。"""
    unique_id = uuid.uuid4().hex[:8]
    slug = f"pytest-legacy-stdio-{unique_id}"
    marker = Path(f"/tmp/{slug}.marker")

    try:
        async with current_loop_pg_manager.get_async_session_context() as db:
            db.add(
                MCPServer(
                    slug=slug,
                    name="pytest legacy stdio MCP",
                    transport="stdio",
                    command="sh",
                    args=["-c", f"touch {marker}"],
                    enabled=1,
                    created_by="admin",
                    updated_by="admin",
                )
            )
            await db.commit()

        await ensure_builtin_mcp_servers_in_db()
        assert await get_mcp_tools(slug, cache=False, force_refresh=True) == []

        async with current_loop_pg_manager.get_async_session_context() as db:
            server = await db.scalar(select(MCPServer).where(MCPServer.slug == slug))
            assert server is not None
            assert server.enabled == 0

        test_response = await e2e_client.post(f"/api/system/mcp-servers/{slug}/test", headers=e2e_headers)
        assert test_response.status_code == 400, test_response.text
        assert not marker.exists(), "legacy stdio MCP created a file in the API container"
    finally:
        async with current_loop_pg_manager.get_async_session_context() as db:
            await db.execute(delete(MCPServer).where(MCPServer.slug == slug))
            await db.commit()
        marker.unlink(missing_ok=True)


async def test_direct_stdio_config_cannot_start_process(tmp_path):
    """直接传入内置标识的 stdio 配置也不能启动命令。"""
    from yuxi.agents.mcp.service import get_mcp_client

    marker = tmp_path / "stdio.marker"
    config = {"deepwiki-official": {"transport": "stdio", "command": "sh", "args": ["-c", f"touch {marker}"]}}
    with pytest.raises(ValueError, match="不支持 stdio"):
        await get_mcp_tools("deepwiki-official", additional_servers=config, cache=False)
    with pytest.raises(ValueError, match="不支持 stdio"):
        client = await get_mcp_client(config)
        if client is not None:
            await client.get_tools()
    assert not marker.exists()


async def test_retired_chart_removed_and_deepwiki_registered(current_loop_pg_manager):
    """真实 PostgreSQL 同步删除系统图表，注册固定远程 DeepWiki。"""
    async with current_loop_pg_manager.get_async_session_context() as db:
        existing = await db.scalar(select(MCPServer).where(MCPServer.slug == "mcp-server-chart"))
        assert existing is None, "retired chart must be absent before seeding the migration case"
        db.add(
            MCPServer(
                slug="mcp-server-chart",
                name="Legacy chart",
                transport="stdio",
                command="npx",
                args=["-y", "@antv/mcp-server-chart"],
                enabled=1,
                created_by="system",
                updated_by="system",
            )
        )
        await db.commit()
    try:
        await ensure_builtin_mcp_servers_in_db()
        await ensure_builtin_mcp_servers_in_db()
        async with current_loop_pg_manager.get_async_session_context() as db:
            assert await db.scalar(select(MCPServer).where(MCPServer.slug == "mcp-server-chart")) is None
            server = await db.scalar(select(MCPServer).where(MCPServer.slug == "deepwiki-official"))
            assert server is not None
            assert server.transport == "streamable_http"
            assert server.url == "https://mcp.deepwiki.com/mcp"
            assert server.command is None
    finally:
        async with current_loop_pg_manager.get_async_session_context() as db:
            await db.execute(
                delete(MCPServer).where(MCPServer.slug == "mcp-server-chart", MCPServer.created_by == "system")
            )
            await db.commit()


async def test_official_builtin_preserves_user_deepwiki(current_loop_pg_manager):
    """官方内置标识不占用已有用户 deepwiki 配置。"""
    from yuxi.agents.mcp.service import get_enabled_mcp_server_config, is_builtin_mcp_server

    async with current_loop_pg_manager.get_async_session_context() as db:
        assert await db.scalar(select(MCPServer.id).where(MCPServer.slug == "deepwiki")) is None, (
            "测试要求未占用的 deepwiki 标识，不能覆盖共享环境的已有配置"
        )
        server = MCPServer(
            slug="deepwiki",
            name="User DeepWiki",
            transport="streamable_http",
            url="https://private.example/mcp",
            headers={"X-Test": "preserve"},
            enabled=1,
            created_by="admin",
            updated_by="admin",
        )
        db.add(server)
        await db.commit()
        server_id = server.id
    try:
        await ensure_builtin_mcp_servers_in_db()
        await ensure_builtin_mcp_servers_in_db()
        async with current_loop_pg_manager.get_async_session_context() as db:
            server = await db.get(MCPServer, server_id)
            assert server.created_by == "admin"
            assert server.enabled == 1
            assert not is_builtin_mcp_server(server)
            config = await get_enabled_mcp_server_config("deepwiki", db=db)
            assert config["url"] == "https://private.example/mcp"
            assert config["headers"] == {"X-Test": "preserve"}
    finally:
        async with current_loop_pg_manager.get_async_session_context() as db:
            await db.execute(delete(MCPServer).where(MCPServer.id == server_id))
            await db.commit()
