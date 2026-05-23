from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage

from yuxi.services import subagent_service
from yuxi.agents.middlewares.skills_middleware import SkillsMiddleware, SkillsState


@pytest.mark.asyncio
async def test_get_subagents_from_names_mcps_and_skills(monkeypatch):
    """
    测试根据名称获取子代理规格时，能够正确解析普通工具、MCP工具并注入绑定 static_skills 的 SkillsMiddleware。
    """
    # 1. 模拟 get_subagent_specs 返回含有 mcps 和 skills 的子代理定义
    fake_subagent_spec = {
        "name": "mcp_skill_subagent",
        "description": "A subagent designed to test mcp and skills binding",
        "system_prompt": "You are a test subagent.",
        "tools": ["dummy_standard_tool"],
        "mcps": ["test_mcp_server"],
        "skills": ["dummy-skill-a", "dummy-skill-b"],
        "model": None,
        "is_builtin": False,
    }
    
    async def mock_get_subagent_specs(db=None):
        return [fake_subagent_spec]

    monkeypatch.setattr(
        subagent_service,
        "get_subagent_specs",
        mock_get_subagent_specs,
    )

    # 2. 模拟 get_all_tool_instances 返回标准普通工具
    fake_standard_tool = SimpleNamespace(
        name="dummy_standard_tool",
        description="A standard tool",
    )
    fake_dep_tool = SimpleNamespace(
        name="dummy_dep_tool",
        description="A skill dependency tool",
    )
    monkeypatch.setattr(
        "yuxi.agents.toolkits.get_all_tool_instances",
        lambda: [fake_standard_tool, fake_dep_tool],
    )

    # 3. 模拟 get_enabled_mcp_tools 返回 MCP 工具
    fake_mcp_tool = SimpleNamespace(
        name="dummy_mcp_tool",
        description="An MCP tool",
    )
    fake_dep_mcp_tool = SimpleNamespace(
        name="dummy_dep_mcp_tool",
        description="A dependency MCP tool",
    )
    async def mock_get_mcp_tools(server_name):
        if server_name == "test_mcp_server":
            return [fake_mcp_tool]
        elif server_name == "dummy_dep_mcp_server":
            return [fake_dep_mcp_tool]
        return []

    monkeypatch.setattr(
        "yuxi.services.mcp_service.get_enabled_mcp_tools",
        mock_get_mcp_tools,
    )

    # 4. Mock 技能依赖关系数据
    async def mock_get_dependency_map(db=None):
        return {
            "dummy-skill-a": {
                "tools": ["dummy_dep_tool"],
                "mcps": ["dummy_dep_mcp_server"],
                "skills": []
            },
            "dummy-skill-b": {
                "tools": [],
                "mcps": [],
                "skills": []
            }
        }

    def mock_expand_skill_closure(slugs, dependency_map):
        return slugs

    monkeypatch.setattr(
        "yuxi.agents.middlewares.skills_middleware.get_dependency_map",
        mock_get_dependency_map,
    )
    monkeypatch.setattr(
        "yuxi.agents.middlewares.skills_middleware.expand_skill_closure",
        mock_expand_skill_closure,
    )

    # 5. 执行获取子代理的逻辑
    resolved_specs = await subagent_service.get_subagents_from_names(["mcp_skill_subagent"])

    # 6. 断言验证
    assert len(resolved_specs) == 1
    spec = resolved_specs[0]
    
    assert spec["name"] == "mcp_skill_subagent"
    
    # 验证工具包含自身配置工具、自身 MCP 工具，以及技能依赖展开的工具和 MCP 工具
    tool_names = [t.name for t in spec["tools"]]
    assert "dummy_standard_tool" in tool_names
    assert "dummy_mcp_tool" in tool_names
    assert "dummy_dep_tool" in tool_names
    assert "dummy_dep_mcp_tool" in tool_names
    assert len(tool_names) == 4
    
    # 验证 SkillsMiddleware 的注入与 static_skills 绑定
    assert "middleware" in spec
    assert len(spec["middleware"]) == 1
    middleware = spec["middleware"][0]
    assert isinstance(middleware, SkillsMiddleware)
    assert middleware.static_skills == ["dummy-skill-a", "dummy-skill-b"]



@pytest.mark.asyncio
async def test_subagent_specs_cache_miss_and_populate(monkeypatch):
    """测试缓存未命中时能够查询 DB 并成功填充回写 Redis。"""
    from yuxi.services import subagent_service
    
    # 强制清空缓存状态
    monkeypatch.setattr(subagent_service, "_local_specs_cache", None)
    monkeypatch.setattr(subagent_service, "_local_specs_cache_at", 0.0)

    # 1. 模拟 DB
    mock_specs = [{"name": "db-subagent", "tools": []}]
    async def mock_list_all_specs(db=None):
        return mock_specs
    
    class MockRepo:
        def __init__(self, session): pass
        async def list_all_specs(self):
            return mock_specs

    monkeypatch.setattr(subagent_service, "SubAgentRepository", MockRepo)

    # 2. Mock Redis
    class MockRedis:
        def __init__(self):
            self.store = {}
        async def get(self, key):
            return self.store.get(key)
        async def setex(self, key, ttl, value):
            self.store[key] = value

    mock_redis = MockRedis()
    async def mock_get_redis_client():
        return mock_redis

    monkeypatch.setattr(subagent_service, "get_redis_client", mock_get_redis_client)

    # 3. 运行测试
    specs = await subagent_service.get_subagent_specs()
    assert specs == mock_specs

    # 4. 验证 Redis 确实被填充写入了
    import json
    cached = await mock_redis.get(subagent_service._REDIS_SPECS_KEY)
    assert cached is not None
    assert json.loads(cached) == mock_specs


@pytest.mark.asyncio
async def test_subagent_specs_l2_cache_hit(monkeypatch):
    """测试本地二级缓存（L2 Cache）在 TTL 周期内能够直接命中，避免多余 Redis/DB 查询。"""
    from yuxi.services import subagent_service
    
    # 1. 直接强行预先填充 L2 内存缓存
    mock_l2_specs = [{"name": "l2-cached-subagent", "tools": []}]
    import time
    monkeypatch.setattr(subagent_service, "_local_specs_cache", mock_l2_specs)
    monkeypatch.setattr(subagent_service, "_local_specs_cache_at", time.monotonic())

    # 2. 模拟 DB 和 Redis 抛出异常（确保它们绝对不被访问）
    async def mock_get_redis_client():
        raise RuntimeError("Should not access Redis!")
    
    class ErrorRepo:
        def __init__(self, session): pass
        async def list_all_specs(self):
            raise RuntimeError("Should not access DB!")

    monkeypatch.setattr(subagent_service, "get_redis_client", mock_get_redis_client)
    monkeypatch.setattr(subagent_service, "SubAgentRepository", ErrorRepo)

    # 3. 运行测试并验证命中返回
    specs = await subagent_service.get_subagent_specs()
    assert specs == mock_l2_specs


@pytest.mark.asyncio
async def test_subagent_specs_eviction(monkeypatch):
    """测试调用 clear_specs_cache 时，能正确清空本地 L2 缓存并向 Redis 发送删除信号。"""
    from yuxi.services import subagent_service
    
    # 1. 预设 L2 缓存
    monkeypatch.setattr(subagent_service, "_local_specs_cache", [{"name": "dummy"}])
    monkeypatch.setattr(subagent_service, "_local_specs_cache_at", 12345.6)

    # 2. Mock Redis delete
    deleted_keys = []
    class MockRedis:
        async def delete(self, key):
            deleted_keys.append(key)

    mock_redis = MockRedis()
    async def mock_get_redis_client():
        return mock_redis

    monkeypatch.setattr(subagent_service, "get_redis_client", mock_get_redis_client)

    # 3. 触发清理
    await subagent_service.clear_specs_cache()

    # 4. 验证本地 L2 和 Redis 都已被清除
    assert subagent_service._local_specs_cache is None
    assert subagent_service._local_specs_cache_at == 0.0
    assert subagent_service._REDIS_SPECS_KEY in deleted_keys


@pytest.mark.asyncio
async def test_subagent_specs_fallback_on_redis_error(monkeypatch):
    """测试 Redis 连接引发异常时，能无缝优雅退化降级到直接查询数据库，保障高可用。"""
    from yuxi.services import subagent_service
    
    # 强制清理 L2
    monkeypatch.setattr(subagent_service, "_local_specs_cache", None)
    monkeypatch.setattr(subagent_service, "_local_specs_cache_at", 0.0)

    # 1. Mock DB 提供数据
    mock_db_specs = [{"name": "fallback-subagent", "tools": []}]
    class MockRepo:
        def __init__(self, session): pass
        async def list_all_specs(self):
            return mock_db_specs

    monkeypatch.setattr(subagent_service, "SubAgentRepository", MockRepo)

    # 2. Mock Redis 产生连接异常
    async def mock_broken_redis_client():
        raise ConnectionError("Redis connection refused!")

    monkeypatch.setattr(subagent_service, "get_redis_client", mock_broken_redis_client)

    # 3. 运行测试，验证依然返回 DB 数据而不受 Redis 挂掉影响
    specs = await subagent_service.get_subagent_specs()
    assert specs == mock_db_specs

