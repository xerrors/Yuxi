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
