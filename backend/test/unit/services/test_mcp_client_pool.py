from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from yuxi.services.mcp.client_pool import MCPClientPool, LongLivedSession


@pytest.mark.asyncio
async def test_long_lived_session_lifecycle():
    """测试 LongLivedSession 正常的启动与停止流程"""
    mock_client = MagicMock()
    mock_session = MagicMock()
    
    # 模拟 client.session() 返回一个 AsyncContextManager
    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_session
    mock_client.session.return_value = mock_context
    
    ll_session = LongLivedSession(mock_client, "test_server")
    
    # 启动
    await ll_session.start()
    assert ll_session._running is True
    assert ll_session.session == mock_session
    mock_client.session.assert_called_once_with("test_server")
    
    # 停止
    await ll_session.stop()
    assert ll_session._running is False
    assert ll_session.session is None


@pytest.mark.asyncio
async def test_client_pool_reuse_and_recreate():
    """测试 MCPClientPool 的复用逻辑与配置脏变重构逻辑"""
    pool = MCPClientPool()
    
    config_1 = {
        "transport": "stdio",
        "command": "node",
        "args": ["file1.js"],
        "__yuxi_cache_partition": "p1",
    }
    
    config_2 = {
        "transport": "stdio",
        "command": "node",
        "args": ["file1.js"],
        "__yuxi_cache_partition": "p1",
    }
    
    config_changed = {
        "transport": "stdio",
        "command": "node",
        "args": ["file2.js"],  # 配置发生改变
        "__yuxi_cache_partition": "p1",
    }

    mock_client_instance = MagicMock()
    mock_session_instance = MagicMock()
    
    # Mock LongLivedSession 的 start/stop 以防真实建连
    with patch("yuxi.services.mcp.client_pool.MultiServerMCPClient", return_value=mock_client_instance), \
         patch("yuxi.services.mcp.client_pool.LongLivedSession") as MockLongLivedSession:
        
        mock_ll_instance = MagicMock()
        mock_ll_instance.session = mock_session_instance
        mock_ll_instance.start = AsyncMock()
        mock_ll_instance.stop = AsyncMock()
        MockLongLivedSession.return_value = mock_ll_instance
        
        # 1. 首次获取，创建新 Session
        session_1 = await pool.get_session("test_server", "p1", config_1)
        assert session_1 == mock_session_instance
        assert MockLongLivedSession.call_count == 1
        mock_ll_instance.start.assert_called_once()
        
        # 2. 相同配置获取，直接复用
        session_2 = await pool.get_session("test_server", "p1", config_2)
        assert session_2 == mock_session_instance
        assert MockLongLivedSession.call_count == 1  # 没增加，说明复用了
        
        # 3. 配置改变获取，销毁旧的，重新创建
        session_changed = await pool.get_session("test_server", "p1", config_changed)
        assert session_changed == mock_session_instance
        # 销毁被调用了
        mock_ll_instance.stop.assert_called_once()
        # 创建计数增加
        assert MockLongLivedSession.call_count == 2
        
        # 4. shutdown 清理
        await pool.shutdown()
        assert mock_ll_instance.stop.call_count == 2  # 新增的那个也被 stop
        assert len(pool._sessions) == 0


@pytest.mark.asyncio
async def test_calculate_config_hash_with_non_serializable():
    """测试配置中包含非 JSON 序列化对象时配置哈希计算不崩溃"""
    from datetime import datetime
    pool = MCPClientPool()
    
    class DummyObj:
        def __str__(self):
            return "dummy"

    config = {
        "transport": "sse",
        "url": "http://example.com/sse",
        "custom_obj": DummyObj(),
        "created_at": datetime(2026, 6, 5),
    }
    
    # 验证在含有不可 JSON 序列化的对象时依然能正常计算出哈希，不抛出异常
    config_hash = pool._calculate_config_hash(config)
    assert isinstance(config_hash, str)
    assert len(config_hash) == 16


@pytest.mark.asyncio
async def test_dynamic_mcp_token_auth_cache():
    """测试 DynamicMCPTokenAuth 的 in-memory 缓存及联动清除逻辑"""
    from yuxi.services.mcp.client_pool import (
        DynamicMCPTokenAuth,
        clear_resolved_headers_cache,
        clear_server_resolved_headers_cache,
        _resolved_headers_cache,
    )
    from yuxi.services.mcp_auth.orchestrator import mcp_auth_context_var, AuthContext
    
    # 清空可能存在的全局缓存
    clear_resolved_headers_cache()
    
    auth = DynamicMCPTokenAuth("test_server")
    mock_req = MagicMock(headers={})
    
    auth_ctx = AuthContext(user_id="u1", department_id="d1")
    cache_key = ("test_server", "u1", "d1")
    
    # 模拟 get_runtime_mcp_server_config 返回的数据
    mock_runtime_config = {"headers": {"Authorization": "Bearer token123"}}
    
    token = mcp_auth_context_var.set(auth_ctx)
    try:
        with patch("yuxi.storage.postgres.manager.pg_manager.get_async_session_context") as mock_session_ctx, \
             patch("yuxi.services.mcp.server_service.get_runtime_mcp_server_config", return_value=mock_runtime_config) as mock_get_config:
            
            # 模拟 async with pg_manager.get_async_session_context() as session
            mock_session = MagicMock()
            mock_ctx_mgr = AsyncMock()
            mock_ctx_mgr.__aenter__.return_value = mock_session
            mock_session_ctx.return_value = mock_ctx_mgr
            
            # 1. 第一次请求：应该执行 DB 查询，获取最新运行时配置
            generator = auth.async_auth_flow(mock_req)
            results = [r async for r in generator]
            assert len(results) == 1
            assert results[0].headers["Authorization"] == "Bearer token123"
            assert mock_get_config.call_count == 1
            
            # 2. 第二次请求（+5秒）：应该命中缓存，不会执行 DB 查询
            mock_req_2 = MagicMock(headers={})
            generator_2 = auth.async_auth_flow(mock_req_2)
            results_2 = [r async for r in generator_2]
            assert len(results_2) == 1
            assert results_2[0].headers["Authorization"] == "Bearer token123"
            # call_count 依然是 1，说明命中了缓存
            assert mock_get_config.call_count == 1
            
            # 3. 细粒度清除缓存：清除指定 server_name
            clear_server_resolved_headers_cache("test_server")
            
            # 4. 第三次请求（+10秒）：清除缓存后，应该再次执行 DB 查询
            mock_req_3 = MagicMock(headers={})
            generator_3 = auth.async_auth_flow(mock_req_3)
            results_3 = [r async for r in generator_3]
            assert len(results_3) == 1
            assert mock_get_config.call_count == 2
            # 4. 测试 clear_mcp_cache / clear_mcp_server_tools_cache 联动清除所有 resolved_headers 缓存
            from yuxi.services.mcp.tool_registry_service import clear_mcp_cache, invalidate_mcp_server_tools_cache
            # 确保当前有缓存项
            _resolved_headers_cache[cache_key] = {"Auth": "Bearer test"}
            await invalidate_mcp_server_tools_cache("test_server")
            assert len(_resolved_headers_cache) == 0
    
            _resolved_headers_cache[cache_key] = {"Auth": "Bearer test"}
            await clear_mcp_cache()
            assert len(_resolved_headers_cache) == 0
    finally:
        mcp_auth_context_var.reset(token)
