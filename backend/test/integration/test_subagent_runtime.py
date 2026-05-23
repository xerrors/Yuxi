from __future__ import annotations

import pytest
import httpx


@pytest.mark.asyncio
async def test_subagent_crud_mcps_and_skills_api(test_client: httpx.AsyncClient, admin_headers: dict[str, str]):
    """
    通过真实的 API 测试子代理的 CRUD 操作，验证 mcps 和 skills 字段在网络传输与数据库落库中的正确性。
    """
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_business import MCPServer, Skill
    from sqlalchemy import delete

    subagent_name = "integration-test-subagent"
    
    # 0. 准备测试依赖的 MCP 服务器和 Skill 并入库以通过 capability 校验
    test_mcps = ["test-mcp-server-1", "test-mcp-server-2", "test-mcp-server-3"]
    test_skills = ["dummy-skill-x", "dummy-skill-y", "dummy-skill-z"]

    async with pg_manager.get_async_session_context() as session:
        # 清理可能残留的脏数据，防止冲突
        await session.execute(delete(MCPServer).where(MCPServer.name.in_(test_mcps)))
        await session.execute(delete(Skill).where(Skill.slug.in_(test_skills)))
        
        # 写入 Mock MCP 服务器
        for name in test_mcps:
            server = MCPServer(
                name=name,
                transport="stdio",
                command="echo",
                enabled=1,
                created_by="system",
                updated_by="system",
            )
            session.add(server)
            
        # 写入 Mock Skills
        for slug in test_skills:
            skill = Skill(
                slug=slug,
                name=f"Mock {slug}",
                description="Mock Skill for integration test",
                dir_path=f"mock_skills/{slug}",
                tool_dependencies=[],
                mcp_dependencies=[],
                skill_dependencies=[],
                created_by="system",
                updated_by="system",
            )
            session.add(skill)
        await session.commit()

    try:
        # 1. 创建子代理
        create_payload = {
            "name": subagent_name,
            "description": "Integration test description",
            "system_prompt": "Integration test prompt",
            "tools": ["calculator"],  # 使用真实存在的内置工具
            "mcps": ["test-mcp-server-1", "test-mcp-server-2"],
            "skills": ["dummy-skill-x", "dummy-skill-y"],
            "model": None,
        }
        
        # 清理已存在的同名子代理，防止干扰
        await test_client.delete(f"/api/system/subagents/{subagent_name}", headers=admin_headers)

        create_response = await test_client.post(
            "/api/system/subagents",
            json=create_payload,
            headers=admin_headers,
        )
        
        assert create_response.status_code == 200, f"创建子代理失败: {create_response.text}"
        created_data = create_response.json().get("data", {})
        assert created_data["name"] == subagent_name
        assert created_data["mcps"] == ["test-mcp-server-1", "test-mcp-server-2"]
        assert created_data["skills"] == ["dummy-skill-x", "dummy-skill-y"]
        assert created_data["tools"] == ["calculator"]

        # 2. 查询单个子代理详情
        get_response = await test_client.get(
            f"/api/system/subagents/{subagent_name}",
            headers=admin_headers,
        )
        assert get_response.status_code == 200, f"获取子代理失败: {get_response.text}"
        fetched_data = get_response.json().get("data", {})
        assert fetched_data["name"] == subagent_name
        assert fetched_data["mcps"] == ["test-mcp-server-1", "test-mcp-server-2"]
        assert fetched_data["skills"] == ["dummy-skill-x", "dummy-skill-y"]

        # 3. 更新子代理的 mcps 和 skills
        update_payload = {
            "name": subagent_name,
            "description": "Integration test description updated",
            "system_prompt": "Integration test prompt updated",
            "tools": [],
            "mcps": ["test-mcp-server-3"],
            "skills": ["dummy-skill-z"],
            "model": None,
        }
        
        update_response = await test_client.put(
            f"/api/system/subagents/{subagent_name}",
            json=update_payload,
            headers=admin_headers,
        )
        assert update_response.status_code == 200, f"更新子代理失败: {update_response.text}"
        
        # 再次查询验证更新是否成功落库
        get_again_response = await test_client.get(
            f"/api/system/subagents/{subagent_name}",
            headers=admin_headers,
        )
        assert get_again_response.status_code == 200
        updated_fetched_data = get_again_response.json().get("data", {})
        assert updated_fetched_data["description"] == "Integration test description updated"
        assert updated_fetched_data["mcps"] == ["test-mcp-server-3"]
        assert updated_fetched_data["skills"] == ["dummy-skill-z"]

        # 4. 删除子代理
        delete_response = await test_client.delete(
            f"/api/system/subagents/{subagent_name}",
            headers=admin_headers,
        )
        assert delete_response.status_code == 200, f"删除子代理失败: {delete_response.text}"

        # 验证已被删除
        get_after_delete = await test_client.get(
            f"/api/system/subagents/{subagent_name}",
            headers=admin_headers,
        )
        assert get_after_delete.status_code == 404

    finally:
        # 清理数据库中的 Mock 依赖
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(MCPServer).where(MCPServer.name.in_(test_mcps)))
            await session.execute(delete(Skill).where(Skill.slug.in_(test_skills)))
            await session.commit()
