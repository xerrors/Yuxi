"""业务 PostgreSQL 映射。"""

from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from yuxi.infrastructure.postgres.base import BusinessBase as Base
from yuxi.shared.datetime import format_utc_datetime, utc_now


class MCPServer(Base):
    """MCP 服务器配置模型"""

    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(100), nullable=False, unique=True, index=True, comment="稳定标识")
    name = Column(String(100), nullable=False, comment="展示名称")
    description = Column(String(500), nullable=True, comment="描述")

    # 连接配置
    transport = Column(String(20), nullable=False, comment="传输类型：sse/streamable_http")
    url = Column(String(500), nullable=True, comment="服务器 URL（sse/streamable_http）")
    headers = Column(JSON, nullable=True, comment="HTTP 请求头")
    timeout = Column(Integer, nullable=True, comment="HTTP 超时时间（秒）")
    sse_read_timeout = Column(Integer, nullable=True, comment="SSE 读取超时（秒）")

    auth_mode = Column(String(20), nullable=False, default="headers", server_default="headers")
    api_key_header = Column(String(128), nullable=True)
    oauth_config = Column(JSON, nullable=True)
    oauth_secret = Column(Text, nullable=True)
    oauth_registration = Column(Text, nullable=True)

    # UI 增强字段

    # 状态字段
    enabled = Column(Integer, nullable=False, default=1, comment="是否启用：1=是，0=否")
    disabled_tools = Column(JSON, nullable=True, comment="禁用的工具名称列表")

    # 用户追踪
    created_by = Column(String(100), nullable=False, comment="创建人用户名")
    updated_by = Column(String(100), nullable=False, comment="修改人用户名")

    # 时间戳
    created_at = Column(DateTime(timezone=True), default=utc_now, comment="创建时间")
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, comment="更新时间")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "transport": self.transport,
            "url": self.url,
            "headers": self.headers or {},
            "auth_mode": self.auth_mode or "headers",
            "api_key_header": self.api_key_header,
            "oauth_config": self.oauth_config,
            "oauth_client_secret_set": bool(self.oauth_secret),
            "timeout": self.timeout,
            "sse_read_timeout": self.sse_read_timeout,
            "enabled": bool(self.enabled),
            "disabled_tools": self.disabled_tools or [],
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }

    def to_mcp_config(self) -> dict[str, Any]:
        """生成远程 MCP 连接配置。"""
        import json

        if self.transport not in ("sse", "streamable_http"):
            raise ValueError("MCP 仅支持 sse 或 streamable_http，不支持 stdio 等其他 transport")
        config = {"transport": self.transport}
        if self.transport in ("sse", "streamable_http") and self.url:
            config["url"] = self.url
        # headers 只用于 sse/streamable_http 传输类型
        if self.transport in ("sse", "streamable_http") and self.headers:
            if isinstance(self.headers, dict):
                config["headers"] = self.headers
            elif isinstance(self.headers, str):
                try:
                    config["headers"] = json.loads(self.headers)
                except json.JSONDecodeError:
                    pass
        if self.timeout is not None:
            config["timeout"] = self.timeout
        if self.sse_read_timeout is not None:
            config["sse_read_timeout"] = self.sse_read_timeout
        if self.disabled_tools:
            config["disabled_tools"] = self.disabled_tools
        return config


class MCPConnection(Base):
    """每名用户每个服务器只有一个连接，世代阻止旧授权复活。"""

    __tablename__ = "mcp_connections"
    __table_args__ = (
        UniqueConstraint("server_id", "user_id", name="uq_mcp_connection_user"),
        CheckConstraint("status IN ('disconnected', 'connected', 'reconnect_required')", name="ck_mcp_connection_status"),
    )
    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    generation = Column(Integer, nullable=False, default=0, server_default="0")
    status = Column(String(24), nullable=False, default="disconnected", server_default="disconnected")
    config_revision = Column(String(64), nullable=False, default="", server_default="")
    credentials = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class MCPOAuthAttempt(Base):
    """一次性浏览器授权事务，不拥有长期授权状态。"""

    __tablename__ = "mcp_oauth_attempts"
    state_hash = Column(String(64), primary_key=True)
    connection_id = Column(Integer, ForeignKey("mcp_connections.id", ondelete="CASCADE"), nullable=False)
    browser_hash = Column(String(64), nullable=False)
    generation = Column(Integer, nullable=False)
    config_revision = Column(String(64), nullable=False)
    payload = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
