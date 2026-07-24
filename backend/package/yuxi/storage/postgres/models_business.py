"""PostgreSQL 业务数据模型 - 用户、部门、对话等相关表"""

from datetime import timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from yuxi.storage.minio.client import normalize_public_minio_url
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive

Base = declarative_base()

MAX_LOGIN_FAILED_ATTEMPTS = 5
LOGIN_LOCK_DURATION_SECONDS = 300
AGENT_RUN_TERMINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted")


class Department(Base):
    """部门模型"""

    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, unique=True, index=True)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)

    # 关联关系
    users = relationship("User", back_populates="department", cascade="all, delete-orphan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": format_utc_datetime(self.created_at),
        }


class User(Base):
    """用户模型"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, unique=True, index=True)  # 显示名称
    uid = Column(String, nullable=False, unique=True, index=True)  # 登录标识
    phone_number = Column(String, nullable=True, unique=True, index=True)  # 手机号
    avatar = Column(String, nullable=True)  # 头像URL
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="user")  # 角色: superadmin, admin, user
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True)  # 部门ID
    created_at = Column(DateTime, default=utc_now_naive)
    last_login = Column(DateTime, nullable=True)

    # 登录失败限制相关字段
    login_failed_count = Column(Integer, nullable=False, default=0)  # 登录失败次数
    last_failed_login = Column(DateTime, nullable=True)  # 最后一次登录失败时间
    login_locked_until = Column(DateTime, nullable=True)  # 锁定到什么时候

    # 软删除相关字段
    is_deleted = Column(Integer, nullable=False, default=0, index=True)  # 是否已删除：0=否，1=是
    deleted_at = Column(DateTime, nullable=True)  # 删除时间

    # 关联操作日志
    operation_logs = relationship("OperationLog", back_populates="user", cascade="all, delete-orphan")

    # 关联部门
    department = relationship("Department", back_populates="users")

    # 关联 API Keys
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")

    agent_env = relationship("AgentEnv", back_populates="user", cascade="all, delete-orphan", uselist=False)
    user_config = relationship("UserConfig", back_populates="user", cascade="all, delete-orphan", uselist=False)

    def to_dict(self, include_password: bool = False) -> dict[str, Any]:
        result = {
            "id": self.id,
            "username": self.username,
            "uid": self.uid,
            "phone_number": self.phone_number,
            "avatar": normalize_public_minio_url(self.avatar),
            "role": self.role,
            "department_id": self.department_id,
            "created_at": format_utc_datetime(self.created_at),
            "last_login": format_utc_datetime(self.last_login),
            "login_failed_count": self.login_failed_count,
            "last_failed_login": format_utc_datetime(self.last_failed_login),
            "login_locked_until": format_utc_datetime(self.login_locked_until),
            "is_deleted": self.is_deleted,
            "deleted_at": format_utc_datetime(self.deleted_at),
        }
        if include_password:
            result["password_hash"] = self.password_hash
        return result

    def is_login_locked(self) -> bool:
        """检查用户是否处于登录锁定状态"""
        if self.login_locked_until is None:
            return False
        return utc_now_naive() < self.login_locked_until

    def get_remaining_lock_time(self) -> int:
        """获取剩余锁定时间（秒）"""
        if self.login_locked_until is None:
            return 0
        remaining = int((self.login_locked_until - utc_now_naive()).total_seconds())
        return max(0, remaining)

    def increment_failed_login(self):
        """增加登录失败计数，并在达到阈值后锁定登录"""
        self.login_failed_count += 1
        self.last_failed_login = utc_now_naive()
        if self.login_failed_count >= MAX_LOGIN_FAILED_ATTEMPTS:
            self.login_locked_until = self.last_failed_login + timedelta(seconds=LOGIN_LOCK_DURATION_SECONDS)

    def reset_failed_login(self):
        """重置登录失败相关字段"""
        self.login_failed_count = 0
        self.last_failed_login = None
        self.login_locked_until = None


class AgentEnv(Base):
    """用户级 Agent 沙盒环境变量"""

    __tablename__ = "agent_envs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String, ForeignKey("users.uid"), nullable=False, unique=True, index=True)
    env = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    user = relationship("User", back_populates="agent_env")

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "env": self.env or {},
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class UserConfig(Base):
    """用户级配置"""

    __tablename__ = "user_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String, ForeignKey("users.uid"), nullable=False, unique=True, index=True)
    enable_memory = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    user = relationship("User", back_populates="user_config")

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "enable_memory": bool(self.enable_memory),
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class Agent(Base):
    """用户可管理、可授权、可切换的智能体。"""

    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(80), nullable=False, unique=True, index=True)
    backend_id = Column(String(64), nullable=False, index=True)

    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(255), nullable=True)

    pics = Column(JSON, nullable=False, default=list)
    config_json = Column(JSON, nullable=False, default=dict)
    share_config = Column(JSON, nullable=False, default=dict)

    is_default = Column(Boolean, nullable=False, default=False, index=True)
    is_subagent = Column(Boolean, nullable=False, default=False, index=True)

    created_by = Column(String(64), nullable=True, index=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    __table_args__ = (Index("uq_agents_default", "is_default", unique=True, postgresql_where=is_default.is_(True)),)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "agent_id": self.slug,
            "backend_id": self.backend_id,
            "name": self.name,
            "description": self.description,
            "icon": normalize_public_minio_url(self.icon),
            "pics": [normalize_public_minio_url(pic) for pic in (self.pics or [])],
            "config_json": self.config_json or {},
            "share_config": self.share_config or {},
            "is_default": bool(self.is_default),
            "is_subagent": bool(self.is_subagent),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class Skill(Base):
    """Skill 元数据模型（内容存文件系统，索引存数据库）"""

    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(128), nullable=False, unique=True, index=True, comment="技能唯一标识（目录名）")
    name = Column(String(128), nullable=False, comment="技能名称（来自 SKILL.md frontmatter.name）")
    description = Column(Text, nullable=False, comment="技能描述（来自 SKILL.md frontmatter.description）")
    source_type = Column(
        String(32), nullable=False, default="upload", index=True, comment="来源: builtin/upload/remote"
    )
    tool_dependencies = Column(JSON, nullable=False, default=list, comment="依赖的内置工具名列表")
    mcp_dependencies = Column(JSON, nullable=False, default=list, comment="依赖的 MCP 服务名列表")
    skill_dependencies = Column(JSON, nullable=False, default=list, comment="依赖的其他 skill slug 列表")
    dir_path = Column(String(512), nullable=False, comment="技能目录路径（相对 save_dir）")
    version = Column(String(64), nullable=True, comment="技能版本（内置 skill 使用语义化版本）")
    content_hash = Column(String(128), nullable=True, comment="技能目录内容哈希（内置 skill 安装时计算）")
    share_config = Column(JSON, nullable=False, default=dict, comment="共享权限配置")
    enabled = Column(Boolean, nullable=False, default=True, comment="是否启用")
    created_by = Column(String(64), nullable=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "tool_dependencies": self.tool_dependencies or [],
            "mcp_dependencies": self.mcp_dependencies or [],
            "skill_dependencies": self.skill_dependencies or [],
            "dir_path": self.dir_path,
            "version": self.version,
            "content_hash": self.content_hash,
            "share_config": self.share_config or {},
            "enabled": bool(self.enabled),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class Conversation(Base):
    """Conversation table - 对话表"""

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    thread_id = Column(String(64), unique=True, index=True, nullable=False, comment="Thread ID (UUID)")
    uid = Column(String(64), index=True, nullable=False, comment="UID")
    # 历史字段名，实际保存的是 Agent.slug。
    agent_id = Column(String(64), index=True, nullable=False, comment="Agent slug (legacy column name: agent_id)")
    title = Column(String(255), nullable=True, comment="Conversation title")
    status = Column(String(20), default="active", comment="Status: active/archived/deleted")
    is_pinned = Column(Boolean, default=False, nullable=False, index=True, comment="Is pinned to top")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="Update time")
    extra_metadata = Column(JSON, nullable=True, comment="Additional metadata")

    # Relationships
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    stats = relationship(
        "ConversationStats", back_populates="conversation", uselist=False, cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict[str, Any]:
        metadata = self.extra_metadata or {}
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "uid": self.uid,
            "agent_id": self.agent_id,
            "title": self.title,
            "status": self.status,
            "is_pinned": bool(self.is_pinned),
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
            "metadata": metadata,
        }


class SubagentThread(Base):
    """SubagentThread table - 子智能体长期线程归属关系表"""

    __tablename__ = "subagent_threads"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    uid = Column(String(64), index=True, nullable=False, comment="UID")
    parent_conversation_id = Column(
        Integer, ForeignKey("conversations.id"), nullable=False, index=True, comment="Parent conversation ID"
    )
    child_conversation_id = Column(
        Integer,
        ForeignKey("conversations.id"),
        nullable=False,
        unique=True,
        index=True,
        comment="Child conversation ID",
    )
    child_thread_id = Column(String(64), nullable=False, unique=True, index=True, comment="Child thread ID")
    subagent_slug = Column(String(64), nullable=False, index=True, comment="Subagent slug")
    created_by_run_id = Column(String(64), nullable=False, index=True, comment="Run that created this subagent thread")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="Update time")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uid": self.uid,
            "parent_conversation_id": self.parent_conversation_id,
            "child_conversation_id": self.child_conversation_id,
            "child_thread_id": self.child_thread_id,
            "subagent_slug": self.subagent_slug,
            "created_by_run_id": self.created_by_run_id,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class Message(Base):
    """Message table - 消息表"""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    conversation_id = Column(
        Integer, ForeignKey("conversations.id"), nullable=False, index=True, comment="Conversation ID"
    )
    role = Column(String(20), nullable=False, comment="Message role: user/assistant/system/tool")
    content = Column(Text, nullable=False, comment="Message content")
    message_type = Column(String(30), default="text", comment="Message type: text/tool_call/tool_result")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    token_count = Column(Integer, nullable=True, comment="Token count (optional)")
    extra_metadata = Column(JSON, nullable=True, comment="Additional metadata (complete message dump)")
    image_content = Column(Text, nullable=True, comment="Base64 encoded image content for multimodal messages")
    run_id = Column(String(64), ForeignKey("agent_runs.id"), nullable=True, index=True, comment="Agent run ID")
    request_id = Column(String(64), nullable=True, index=True, comment="Request ID for idempotency")
    delivery_status = Column(String(32), nullable=False, default="complete", comment="Message status")

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")
    tool_calls = relationship("ToolCall", back_populates="message", cascade="all, delete-orphan")
    feedbacks = relationship("MessageFeedback", back_populates="message", cascade="all, delete-orphan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "message_type": self.message_type,
            "created_at": format_utc_datetime(self.created_at),
            "token_count": self.token_count,
            "metadata": self.extra_metadata or {},
            "image_content": self.image_content,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "status": self.delivery_status,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls] if self.tool_calls else [],
        }

    def to_simple_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
        }


class ToolCall(Base):
    """ToolCall table - 工具调用表"""

    __tablename__ = "tool_calls"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False, index=True, comment="Message ID")
    langgraph_tool_call_id = Column(String(100), nullable=True, index=True, comment="LangGraph tool_call_id")
    tool_name = Column(String(100), nullable=False, comment="Tool name")
    tool_input = Column(JSON, nullable=True, comment="Tool input parameters")
    tool_output = Column(Text, nullable=True, comment="Tool execution result")
    status = Column(String(20), default="pending", comment="Status: pending/success/error")
    error_message = Column(Text, nullable=True, comment="Error message if failed")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")

    # Relationships
    message = relationship("Message", back_populates="tool_calls")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "langgraph_tool_call_id": self.langgraph_tool_call_id,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input or {},
            "tool_output": self.tool_output,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": format_utc_datetime(self.created_at),
        }


class ConversationStats(Base):
    """ConversationStats table - 对话统计表"""

    __tablename__ = "conversation_stats"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    conversation_id = Column(
        Integer, ForeignKey("conversations.id"), unique=True, nullable=False, comment="Conversation ID"
    )
    message_count = Column(Integer, default=0, comment="Total message count")
    total_tokens = Column(Integer, default=0, comment="Total tokens used")
    model_used = Column(String(100), nullable=True, comment="Model used")
    user_feedback = Column(JSON, nullable=True, comment="User feedback")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="Update time")

    # Relationships
    conversation = relationship("Conversation", back_populates="stats")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "model_used": self.model_used,
            "user_feedback": self.user_feedback or {},
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class OperationLog(Base):
    """操作日志模型"""

    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    operation = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    timestamp = Column(DateTime, default=utc_now_naive)

    # 关联用户
    user = relationship("User", back_populates="operation_logs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "operation": self.operation,
            "details": self.details,
            "ip_address": self.ip_address,
            "timestamp": format_utc_datetime(self.timestamp),
        }


class MessageFeedback(Base):
    """Message feedback table - 消息反馈表"""

    __tablename__ = "message_feedbacks"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    message_id = Column(
        Integer, ForeignKey("messages.id"), nullable=False, index=True, comment="Message ID being rated"
    )
    uid = Column(String(64), nullable=False, index=True, comment="UID who provided feedback")
    rating = Column(String(10), nullable=False, comment="Feedback rating: like or dislike")
    reason = Column(Text, nullable=True, comment="Optional reason for dislike feedback")
    created_at = Column(DateTime, default=utc_now_naive, comment="Feedback creation time")

    # Relationships
    message = relationship("Message", back_populates="feedbacks")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "uid": self.uid,
            "rating": self.rating,
            "reason": self.reason,
            "created_at": format_utc_datetime(self.created_at),
        }


class MCPServer(Base):
    """MCP 服务器配置模型"""

    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(100), nullable=False, unique=True, index=True, comment="稳定标识")
    name = Column(String(100), nullable=False, comment="展示名称")
    description = Column(String(500), nullable=True, comment="描述")

    # 连接配置
    transport = Column(String(20), nullable=False, comment="传输类型：sse/streamable_http/stdio")
    url = Column(String(500), nullable=True, comment="服务器 URL（sse/streamable_http）")
    command = Column(String(500), nullable=True, comment="命令（stdio）")
    args = Column(JSON, nullable=True, comment="命令参数数组（stdio）")
    env = Column(JSON, nullable=True, comment="环境变量（stdio）")
    headers = Column(JSON, nullable=True, comment="HTTP 请求头")
    timeout = Column(Integer, nullable=True, comment="HTTP 超时时间（秒）")
    sse_read_timeout = Column(Integer, nullable=True, comment="SSE 读取超时（秒）")

    # UI 增强字段
    tags = Column(JSON, nullable=True, comment="标签数组")
    icon = Column(String(50), nullable=True, comment="图标（emoji）")

    # 状态字段
    enabled = Column(Integer, nullable=False, default=1, comment="是否启用：1=是，0=否")
    disabled_tools = Column(JSON, nullable=True, comment="禁用的工具名称列表")

    # 用户追踪
    created_by = Column(String(100), nullable=False, comment="创建人用户名")
    updated_by = Column(String(100), nullable=False, comment="修改人用户名")

    # 时间戳
    created_at = Column(DateTime, default=utc_now_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="更新时间")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "transport": self.transport,
            "url": self.url,
            "command": self.command,
            "args": self.args or [],
            "env": self.env or {},
            "headers": self.headers or {},
            "timeout": self.timeout,
            "sse_read_timeout": self.sse_read_timeout,
            "tags": self.tags or [],
            "icon": self.icon,
            "enabled": bool(self.enabled),
            "disabled_tools": self.disabled_tools or [],
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }

    def to_mcp_config(self) -> dict[str, Any]:
        """转换为 MCP 配置格式（用于加载到 MCP_SERVERS 缓存）"""
        import json

        config = {"transport": self.transport}
        if self.url:
            config["url"] = self.url
        if self.command:
            config["command"] = self.command
        # args 只用于 stdio 传输类型，必须是列表
        if self.transport == "stdio" and self.args:
            if isinstance(self.args, list):
                config["args"] = self.args
            elif isinstance(self.args, str):
                try:
                    config["args"] = json.loads(self.args)
                except json.JSONDecodeError:
                    pass
        if self.transport == "stdio" and self.env:
            if isinstance(self.env, dict):
                config["env"] = self.env
            elif isinstance(self.env, str):
                try:
                    config["env"] = json.loads(self.env)
                except json.JSONDecodeError:
                    pass
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


class ModelProvider(Base):
    """模型供应商配置，存储 provider 基础信息、模型端点和可用模型。"""

    __tablename__ = "model_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String(100), nullable=False, unique=True, index=True, comment="供应商稳定标识")
    display_name = Column(String(100), nullable=False, comment="展示名称")
    provider_type = Column(String(32), nullable=False, default="openai", comment="供应商适配类型，默认 openai")

    default_protocol = Column(String(64), nullable=True, comment="默认协议，如 openai_compatible")
    base_url = Column(String(500), nullable=False, comment="API 基础 URL")
    embedding_base_url = Column(String(500), nullable=True, comment="Embedding 模型请求基础 URL")
    rerank_base_url = Column(String(500), nullable=True, comment="Rerank 模型请求基础 URL")
    models_endpoint = Column(String(200), nullable=True, comment="聊天/通用模型列表端点")
    embedding_models_endpoint = Column(String(200), nullable=True, comment="Embedding 模型列表端点")
    rerank_models_endpoint = Column(String(200), nullable=True, comment="Rerank 模型列表端点")
    api_key_env = Column(String(128), nullable=True, comment="API Key 环境变量名")
    api_key = Column(String(500), nullable=True, comment="直接配置的 API Key")

    capabilities = Column(JSON, nullable=False, default=list, comment="支持能力：chat/embedding/rerank")
    enabled_models = Column(JSON, nullable=False, default=list, comment="已启用模型配置对象")
    headers_json = Column(JSON, nullable=True, comment="额外请求头")
    extra_json = Column(JSON, nullable=True, comment="扩展配置")

    is_enabled = Column(Boolean, nullable=False, default=True, index=True, comment="供应商是否启用")
    is_builtin = Column(Boolean, nullable=False, default=False, comment="是否内置")

    created_by = Column(String(100), nullable=True)
    updated_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="更新时间")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "provider_type": self.provider_type,
            "default_protocol": self.default_protocol,
            "base_url": self.base_url,
            "embedding_base_url": self.embedding_base_url,
            "rerank_base_url": self.rerank_base_url,
            "models_endpoint": self.models_endpoint,
            "embedding_models_endpoint": self.embedding_models_endpoint,
            "rerank_models_endpoint": self.rerank_models_endpoint,
            "api_key_env": self.api_key_env,
            "api_key": self.api_key,
            "capabilities": self.capabilities or [],
            "enabled_models": self.enabled_models or [],
            "headers_json": self.headers_json or {},
            "extra_json": self.extra_json or {},
            "is_enabled": bool(self.is_enabled),
            "is_builtin": bool(self.is_builtin),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ConfigOption(Base):
    """系统定义、管理员维护值的通用配置项。"""

    __tablename__ = "config_options"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=False, default="")
    params = Column(JSON, nullable=False, default=dict)
    value = Column(JSON, nullable=False, default=dict)
    created_by = Column(String(100), nullable=True)
    updated_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class TaskRecord(Base):
    __tablename__ = "tasks"

    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    progress = Column(Float, nullable=False, default=0.0)
    message = Column(Text, nullable=False, default="")
    payload = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    cancel_requested = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
            "started_at": format_utc_datetime(self.started_at),
            "completed_at": format_utc_datetime(self.completed_at),
            "payload": self.payload or {},
            "result": self.result,
            "error": self.error,
            "cancel_requested": bool(self.cancel_requested),
        }

    def to_summary_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("payload", None)
        data.pop("result", None)
        return data


class APIKey(Base):
    """API Key 模型"""

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    key_prefix = Column(String(16), nullable=False)
    name = Column(String(100), nullable=False)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)

    expires_at = Column(DateTime, nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    last_used_at = Column(DateTime, nullable=True)

    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=utc_now_naive)

    # 关联
    user = relationship("User", back_populates="api_keys")
    department = relationship("Department")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key_prefix": self.key_prefix,
            "name": self.name,
            "user_id": self.user_id,
            "department_id": self.department_id,
            "expires_at": format_utc_datetime(self.expires_at),
            "is_enabled": bool(self.is_enabled),
            "last_used_at": format_utc_datetime(self.last_used_at),
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
        }

    def is_valid(self) -> bool:
        """检查 Key 是否有效"""
        if not self.is_enabled:
            return False
        if self.expires_at and utc_now_naive() > self.expires_at:
            return False
        return True


class CLIAuthSession(Base):
    """CLI 浏览器授权会话。"""

    __tablename__ = "cli_auth_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_code_hash = Column(String(64), nullable=False, unique=True, index=True)
    user_code = Column(String(16), nullable=False, unique=True, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    key_name = Column(String(100), nullable=False)

    approved_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=True, index=True)

    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    consumed_at = Column(DateTime, nullable=True)

    approved_user = relationship("User")
    api_key = relationship("APIKey")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_code": self.user_code,
            "status": self.status,
            "key_name": self.key_name,
            "approved_user_id": self.approved_user_id,
            "api_key_id": self.api_key_id,
            "created_at": format_utc_datetime(self.created_at),
            "expires_at": format_utc_datetime(self.expires_at),
            "approved_at": format_utc_datetime(self.approved_at),
            "consumed_at": format_utc_datetime(self.consumed_at),
        }


class AgentRun(Base):
    """AgentRun table - 运行任务表"""

    __tablename__ = "agent_runs"

    id = Column(String(64), primary_key=True, comment="Run ID (UUID)")
    conversation_thread_id = Column(String(64), index=True, nullable=False, comment="Conversation thread ID snapshot")
    agent_slug = Column(String(64), index=True, nullable=False, comment="Agent slug")
    uid = Column(String(64), index=True, nullable=False, comment="UID")
    status = Column(
        String(32),
        index=True,
        nullable=False,
        default="pending",
        comment="Run status: pending/running/completed/failed/cancel_requested/cancelled/interrupted",
    )
    request_id = Column(String(64), unique=True, index=True, nullable=False, comment="Idempotency request ID")
    conversation_id = Column(
        Integer, ForeignKey("conversations.id"), nullable=True, index=True, comment="Conversation ID"
    )
    created_by_run_id = Column(String(64), nullable=True, index=True, comment="Run that created this run")
    subagent_thread_relation_id = Column(
        Integer,
        ForeignKey("subagent_threads.id"),
        nullable=True,
        index=True,
        comment="Subagent thread relation record ID",
    )
    run_type = Column(
        String(32),
        nullable=False,
        default="chat",
        comment="Run type: chat/resume/subagent",
    )
    input_message_id = Column(Integer, nullable=True, comment="Input message ID")
    output_message_id = Column(Integer, nullable=True, comment="Output message ID")
    last_event_id = Column(String(64), nullable=True, comment="Last Redis stream event ID")
    input_payload = Column(JSON, nullable=False, default=dict, comment="Original input payload")
    error_type = Column(String(64), nullable=True, comment="Error type")
    error_message = Column(Text, nullable=True, comment="Error message")
    started_at = Column(DateTime, nullable=True, comment="Start time")
    finished_at = Column(DateTime, nullable=True, comment="Finish time")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="Update time")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_thread_id": self.conversation_thread_id,
            "agent_slug": self.agent_slug,
            "uid": self.uid,
            "status": self.status,
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "created_by_run_id": self.created_by_run_id,
            "subagent_thread_relation_id": self.subagent_thread_relation_id,
            "run_type": self.run_type,
            "input_message_id": self.input_message_id,
            "output_message_id": self.output_message_id,
            "last_event_id": self.last_event_id,
            "input_payload": self.input_payload or {},
            "error_type": self.error_type,
            "error_message": self.error_message,
            "started_at": format_utc_datetime(self.started_at),
            "finished_at": format_utc_datetime(self.finished_at),
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


Index(
    "uq_agent_runs_one_active_per_thread",
    AgentRun.uid,
    AgentRun.agent_slug,
    AgentRun.conversation_thread_id,
    unique=True,
    postgresql_where=AgentRun.status.notin_(AGENT_RUN_TERMINAL_STATUSES),
    sqlite_where=AgentRun.status.notin_(AGENT_RUN_TERMINAL_STATUSES),
)


class AgentRunRequest(Base):
    """AgentRunRequest table - 智能体线程请求队列表。

    表示一次用户/外部请求；派发后由对应 AgentRun 表达执行状态。
    外部统一以 request_id 作为幂等键引用；id 为自增主键，仅用于 FIFO 排序。
    """

    __tablename__ = "agent_run_requests"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    request_id = Column(String(64), unique=True, index=True, nullable=False, comment="幂等请求 ID")
    uid = Column(String(64), nullable=False, comment="UID")
    agent_slug = Column(String(64), nullable=False, comment="Agent slug")
    conversation_thread_id = Column(String(64), nullable=False, comment="Conversation thread ID")
    source = Column(String(32), nullable=False, default="chat", comment="请求来源: chat/agent_call/eval")
    queue_policy = Column(
        String(16),
        nullable=False,
        default="enqueue",
        comment="排队策略: enqueue/reject",
    )
    status = Column(
        String(32),
        nullable=False,
        default="queued",
        comment="请求状态: queued/dispatched/cancelled/rejected/failed",
    )
    input_message_id = Column(Integer, ForeignKey("messages.id"), nullable=False, comment="关联输入消息 ID")
    dispatched_run_id = Column(String(64), ForeignKey("agent_runs.id"), nullable=True, comment="已派发的 AgentRun ID")
    input_payload = Column(JSON, nullable=False, default=dict, comment="原始输入载荷快照")
    error_message = Column(Text, nullable=True, comment="rejected/failed 时的错误信息")
    created_at = Column(DateTime, nullable=False, default=utc_now_naive, comment="创建时间")
    dispatched_at = Column(DateTime, nullable=True, comment="派发时间")
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        comment="更新时间",
    )

    # Relationships
    input_message = relationship("Message", foreign_keys=[input_message_id])
    dispatched_run = relationship("AgentRun", foreign_keys=[dispatched_run_id])

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "uid": self.uid,
            "agent_slug": self.agent_slug,
            "thread_id": self.conversation_thread_id,
            "source": self.source,
            "queue_policy": self.queue_policy,
            "status": self.status,
            "input_message_id": self.input_message_id,
            "dispatched_run_id": self.dispatched_run_id,
            "error_message": self.error_message,
            "created_at": format_utc_datetime(self.created_at),
            "dispatched_at": format_utc_datetime(self.dispatched_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


Index(
    "ix_agent_run_requests_queue",
    AgentRunRequest.uid,
    AgentRunRequest.agent_slug,
    AgentRunRequest.conversation_thread_id,
    AgentRunRequest.status,
    AgentRunRequest.created_at,
    AgentRunRequest.id,
)
