from __future__ import annotations
import pytest
from unittest.mock import MagicMock

from yuxi.services.mcp.cache_policy import (
    CachePolicyFactory,
    StaticCachePolicy,
    TokenInjectedCachePolicy,
    DynamicProxyCachePolicy,
)
from yuxi.services.mcp_auth.orchestrator import AuthContext
from yuxi.storage.postgres.models_business import MCPConnection


def test_cache_policy_factory():
    """测试 CachePolicyFactory 的策略派发逻辑"""
    # 静态/无鉴权
    assert isinstance(CachePolicyFactory.get_policy(None), StaticCachePolicy)
    assert isinstance(CachePolicyFactory.get_policy("legacy_static"), StaticCachePolicy)
    
    # 注入型
    assert isinstance(CachePolicyFactory.get_policy("bound_secret"), TokenInjectedCachePolicy)
    assert isinstance(CachePolicyFactory.get_policy("stdio_env"), TokenInjectedCachePolicy)
    
    # 动态 Token 型
    assert isinstance(CachePolicyFactory.get_policy("custom_http_token"), DynamicProxyCachePolicy)
    assert isinstance(CachePolicyFactory.get_policy("client_credentials"), DynamicProxyCachePolicy)
    assert isinstance(CachePolicyFactory.get_policy("authorization_code"), DynamicProxyCachePolicy)


def test_static_cache_policy():
    """测试 StaticCachePolicy"""
    policy = StaticCachePolicy()
    assert policy.should_cache_tool_object() is True
    
    auth_context = AuthContext()
    partition, is_shared = policy.resolve_cache_partition(auth_context, None)
    assert partition == "global"
    assert is_shared is True


def test_token_injected_cache_policy():
    """测试 TokenInjectedCachePolicy"""
    policy = TokenInjectedCachePolicy()
    assert policy.should_cache_tool_object() is True
    
    auth_context = AuthContext(user_id="user_1", department_id="dept_A")
    
    # connection 为 None 退避
    partition, is_shared = policy.resolve_cache_partition(auth_context, None)
    assert partition == "global"
    assert is_shared is True
    
    # 系统连接，共享
    conn_sys = MCPConnection(id=10, scope_type="system", scope_id="global")
    partition, is_shared = policy.resolve_cache_partition(auth_context, conn_sys)
    assert partition == "connection:10"
    assert is_shared is True
    
    # 部门连接，独占
    conn_dept = MCPConnection(id=11, scope_type="department", scope_id="dept_A")
    partition, is_shared = policy.resolve_cache_partition(auth_context, conn_dept)
    assert partition == "connection:11"
    assert is_shared is False
    
    # 个人连接，独占
    conn_user = MCPConnection(id=12, scope_type="user", scope_id="user_1")
    partition, is_shared = policy.resolve_cache_partition(auth_context, conn_user)
    assert partition == "connection:12"
    assert is_shared is False


def test_dynamic_proxy_cache_policy():
    """测试 DynamicProxyCachePolicy"""
    policy = DynamicProxyCachePolicy()
    # 动态鉴权必须禁止把带临时 Token 的 Tool 实例缓存在共享内存中
    assert policy.should_cache_tool_object() is False
    
    auth_context = AuthContext(user_id="user_1", department_id="dept_A")
    
    # 部门隔离连接，独占
    conn_dept = MCPConnection(id=20, scope_type="department", scope_id="dept_A")
    partition, is_shared = policy.resolve_cache_partition(auth_context, conn_dept)
    assert partition == "connection:20"
    assert is_shared is False
    
    # 个人隔离连接，独占
    conn_user = MCPConnection(id=21, scope_type="user", scope_id="user_1")
    partition, is_shared = policy.resolve_cache_partition(auth_context, conn_user)
    assert partition == "connection:21"
    assert is_shared is False
