from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yuxi.services.mcp_auth.orchestrator import AuthContext
    from yuxi.storage.postgres.models_business import MCPConnection


class MCPCachePolicy(ABC):
    """MCP 缓存策略抽象基类"""

    @abstractmethod
    def should_cache_tool_object(self) -> bool:
        """是否在内存中缓存底层的 Tool 实例对象"""
        pass

    @abstractmethod
    def resolve_cache_partition(
        self,
        auth_context: AuthContext,
        connection: MCPConnection | None,
    ) -> tuple[str, bool]:
        """
        解析该连接应被划分到哪一个缓存分区中。

        返回:
            tuple[partition_key, is_shared_across_users]
            - partition_key: 用于区分 Redis 缓存或内存缓存隔离区段的 Key。
            - is_shared_across_users: 表明该分区下的缓存在不同用户间是否可以共享。
        """
        pass


class StaticCachePolicy(MCPCachePolicy):
    """静态配置（无鉴权）缓存策略"""

    def should_cache_tool_object(self) -> bool:
        # NOTE: 静态服务无任何鉴权和状态变化，完全可以缓存 Tool 对象以提升性能
        return True

    def resolve_cache_partition(
        self,
        auth_context: AuthContext,
        connection: MCPConnection | None,
    ) -> tuple[str, bool]:
        # NOTE: 静态连接全局共享同一个分区
        return "global", True


class TokenInjectedCachePolicy(MCPCachePolicy):
    """静态凭据/环境变量注入型缓存策略（例如 bound_secret, stdio_env）"""

    def should_cache_tool_object(self) -> bool:
        # NOTE: 绑定了静态凭据或环境变量的连接，一旦 Connection 确定，其工具列表也是确定的，支持缓存 Tool 对象
        return True

    def resolve_cache_partition(
        self,
        auth_context: AuthContext,
        connection: MCPConnection | None,
    ) -> tuple[str, bool]:
        if connection is None:
            return "global", True

        # NOTE: 仅系统级别（system）是多用户共享的，部门和个人级别一律判定为独占
        is_shared = connection.scope_type == "system"
        return f"connection:{connection.id}", is_shared


class DynamicProxyCachePolicy(MCPCachePolicy):
    """动态 Token 鉴权代理缓存策略（例如 custom_http_token, authorization_code）"""

    def should_cache_tool_object(self) -> bool:
        # NOTE: 动态 Token 具有时效性且可能因用户身份变化，为了安全性，禁止在内存中缓存带有具体 Token 的 Tool 实例
        return False

    def resolve_cache_partition(
        self,
        auth_context: AuthContext,
        connection: MCPConnection | None,
    ) -> tuple[str, bool]:
        if connection is None:
            return "global", True

        # NOTE: 仅系统级别（system）是多用户共享的，部门和个人级别一律判定为独占
        is_shared = connection.scope_type == "system"
        return f"connection:{connection.id}", is_shared


class CachePolicyFactory:
    """缓存策略工厂，根据 auth_provider 获取匹配的 CachePolicy 实例"""

    @staticmethod
    def get_policy(provider: str | None) -> MCPCachePolicy:
        if not provider or provider == "legacy_static":
            return StaticCachePolicy()
        elif provider in ("bound_secret", "stdio_env"):
            return TokenInjectedCachePolicy()
        else:
            # 默认为动态代理鉴权策略（支持 custom_http_token, client_credentials, authorization_code 等）
            return DynamicProxyCachePolicy()
