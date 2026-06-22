from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp_auth.crypto import encrypt_credential_blob
from yuxi.storage.postgres.models_business import Department, MCPConnection, User

logger = logging.getLogger("yuxi.mcp.connection_service")

_UNSET = object()
_VALID_MCP_CONNECTION_SCOPE_TYPES = {"system", "department", "user"}
_VALID_MCP_CONNECTION_STATUSES = {"active", "disabled", "reauth_required", "invalid"}
_MCP_CONNECTION_SCOPE_LABELS = {
    "system": "全局",
    "department": "部门",
    "user": "个人",
}
_MCP_CONNECTION_HEALTH_FILTERS = {"all", "active", "attention", "disabled"}


def requires_bound_mcp_connection(auth_config: MCPAuthConfig) -> bool:
    """Check if the auth config requires a bound MCP connection with credentials."""
    return auth_config.binding_scope != "inline" and bool(auth_config.get_secret_fields())


def _normalize_mcp_connection_scope(scope_type: str, scope_id: str | None) -> tuple[str, str]:
    normalized_scope_type = str(scope_type or "").strip().lower()
    if normalized_scope_type not in _VALID_MCP_CONNECTION_SCOPE_TYPES:
        raise ValueError("scope_type must be one of: system, department, user")

    normalized_scope_id = str(scope_id or "").strip()
    if normalized_scope_type == "system":
        return normalized_scope_type, "global"
    if not normalized_scope_id:
        raise ValueError(f"scope_id is required for {normalized_scope_type}-scoped MCP connections")
    return normalized_scope_type, normalized_scope_id


def _normalize_mcp_connection_status(status: str) -> str:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _VALID_MCP_CONNECTION_STATUSES:
        raise ValueError("status must be one of: active, disabled, reauth_required, invalid")
    return normalized_status


def _format_duplicate_connection_message(server_name: str, scope_type: str) -> str:
    scope_label = _MCP_CONNECTION_SCOPE_LABELS.get(scope_type, "未知")
    return f'MCP "{server_name}" 的{scope_label}作用域下已存在连接，每个作用域只允许一个连接'


def _configured_connection_scope(server) -> str | None:
    payload = getattr(server, "auth_config_json", None)
    if not payload:
        return None
    try:
        auth_config = MCPAuthConfig.model_validate(payload)
    except Exception:
        return None
    if auth_config.binding_scope in _VALID_MCP_CONNECTION_SCOPE_TYPES:
        return auth_config.binding_scope
    return None


def _ensure_connection_scope_matches_server(server, scope_type: str) -> None:
    configured_scope = _configured_connection_scope(server)
    if not configured_scope or scope_type == configured_scope:
        return
    scope_label = _MCP_CONNECTION_SCOPE_LABELS.get(configured_scope, "未配置")
    server_name = getattr(server, "name", "")
    raise ValueError(f'MCP "{server_name}" 配置的鉴权范围为{scope_label}，无法创建{scope_label}范围之外的连接')


async def get_mcp_connection(db: AsyncSession, connection_id: int) -> MCPConnection | None:
    """Get a single connection by ID."""
    result = await db.execute(select(MCPConnection).where(MCPConnection.id == connection_id))
    return result.scalar_one_or_none()


async def list_mcp_connections(
    db: AsyncSession,
    *,
    server_name: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> list[MCPConnection]:
    """List connections with optional filters."""
    stmt = select(MCPConnection)
    if server_name is not None:
        stmt = stmt.where(MCPConnection.server_name == server_name)
    if scope_type is not None:
        stmt = stmt.where(MCPConnection.scope_type == scope_type)
    if scope_id is not None:
        stmt = stmt.where(MCPConnection.scope_id == scope_id)
    stmt = stmt.order_by(MCPConnection.id.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _connection_has_credential_condition():
    return and_(MCPConnection.credential_blob.is_not(None), MCPConnection.credential_blob != "")


def _connection_missing_credential_condition():
    return or_(MCPConnection.credential_blob.is_(None), MCPConnection.credential_blob == "")


def _connection_search_condition(search: str):
    keyword = str(search or "").strip()
    like_keyword = f"%{keyword}%"
    lowered_keyword = keyword.lower()
    conditions = [
        MCPConnection.display_name.ilike(like_keyword),
        MCPConnection.external_subject.ilike(like_keyword),
        MCPConnection.scope_id.ilike(like_keyword),
        MCPConnection.created_by.ilike(like_keyword),
        MCPConnection.updated_by.ilike(like_keyword),
        and_(
            MCPConnection.scope_type == "department",
            select(Department.id)
            .where(
                cast(Department.id, String) == MCPConnection.scope_id,
                Department.name.ilike(like_keyword),
            )
            .exists(),
        ),
        and_(
            MCPConnection.scope_type == "user",
            select(User.id)
            .where(
                or_(
                    cast(User.id, String) == MCPConnection.scope_id,
                    User.user_id == MCPConnection.scope_id,
                ),
                or_(User.username.ilike(like_keyword), User.user_id.ilike(like_keyword)),
            )
            .exists(),
        ),
    ]
    if any(token in lowered_keyword for token in ("system", "global", "全局", "内置", "系统")):
        conditions.append(MCPConnection.scope_type == "system")
    if any(token in lowered_keyword for token in ("department", "dept", "部门")):
        conditions.append(MCPConnection.scope_type == "department")
    if any(token in lowered_keyword for token in ("user", "用户", "个人")):
        conditions.append(MCPConnection.scope_type == "user")
    return or_(*conditions)


def _connection_health_condition(
    status_filter: str,
    *,
    effective_scope_type: str | None,
    credentials_required: bool,
):
    normalized_filter = str(status_filter or "all").strip().lower()
    if normalized_filter not in _MCP_CONNECTION_HEALTH_FILTERS:
        raise ValueError("status filter must be one of: all, active, attention, disabled")
    if normalized_filter == "all":
        return None
    if normalized_filter == "disabled":
        return MCPConnection.status == "disabled"

    conditions = []
    if normalized_filter == "active":
        conditions.append(MCPConnection.status == "active")
        if effective_scope_type:
            conditions.append(MCPConnection.scope_type == effective_scope_type)
        if credentials_required:
            conditions.append(_connection_has_credential_condition())
        return and_(*conditions)

    conditions.append(MCPConnection.status.in_(("reauth_required", "invalid")))
    if effective_scope_type:
        conditions.append(MCPConnection.scope_type != effective_scope_type)
    if credentials_required:
        conditions.append(_connection_missing_credential_condition())
    return or_(*conditions)


def _build_mcp_connections_query(
    *,
    server_name: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    status_filter: str = "all",
    effective_scope_type: str | None = None,
    credentials_required: bool = False,
    search: str | None = None,
):
    conditions = []
    if server_name is not None:
        conditions.append(MCPConnection.server_name == server_name)
    if scope_type is not None:
        conditions.append(MCPConnection.scope_type == scope_type)
    if scope_id is not None:
        conditions.append(MCPConnection.scope_id == scope_id)

    health_condition = _connection_health_condition(
        status_filter,
        effective_scope_type=effective_scope_type,
        credentials_required=credentials_required,
    )
    if health_condition is not None:
        conditions.append(health_condition)

    if search and str(search).strip():
        conditions.append(_connection_search_condition(str(search).strip()))

    return conditions


async def count_mcp_connections(
    db: AsyncSession,
    *,
    server_name: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    status_filter: str = "all",
    effective_scope_type: str | None = None,
    credentials_required: bool = False,
    search: str | None = None,
) -> int:
    """Count connections matching the given filters."""
    stmt = select(func.count()).select_from(MCPConnection)
    for condition in _build_mcp_connections_query(
        server_name=server_name,
        scope_type=scope_type,
        scope_id=scope_id,
        status_filter=status_filter,
        effective_scope_type=effective_scope_type,
        credentials_required=credentials_required,
        search=search,
    ):
        stmt = stmt.where(condition)
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def list_mcp_connections_page(
    db: AsyncSession,
    *,
    server_name: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    status_filter: str = "all",
    effective_scope_type: str | None = None,
    credentials_required: bool = False,
    search: str | None = None,
    page: int = 1,
    page_size: int = 12,
) -> tuple[list[MCPConnection], int]:
    """List connections with pagination."""
    normalized_page = max(1, int(page or 1))
    normalized_page_size = min(max(1, int(page_size or 12)), 100)
    conditions = _build_mcp_connections_query(
        server_name=server_name,
        scope_type=scope_type,
        scope_id=scope_id,
        status_filter=status_filter,
        effective_scope_type=effective_scope_type,
        credentials_required=credentials_required,
        search=search,
    )
    stmt = select(MCPConnection).order_by(MCPConnection.id.asc())
    count_stmt = select(func.count()).select_from(MCPConnection)
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    stmt = stmt.limit(normalized_page_size).offset((normalized_page - 1) * normalized_page_size)

    total_result = await db.execute(count_stmt)
    result = await db.execute(stmt)
    return list(result.scalars().all()), int(total_result.scalar_one() or 0)


async def create_mcp_connection(
    db: AsyncSession,
    *,
    server_name: str,
    scope_type: str,
    scope_id: str,
    display_name: str | None = None,
    external_subject: str | None = None,
    status: str = "active",
    credential_blob: str | None = None,
    meta_json: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> MCPConnection:
    """Create a new MCP connection."""
    from yuxi.services.mcp.server_service import get_mcp_server

    server = await get_mcp_server(db, server_name)
    if server is None:
        raise ValueError(f"Server '{server_name}' does not exist")
    normalized_scope_type, normalized_scope_id = _normalize_mcp_connection_scope(scope_type, scope_id)
    normalized_status = _normalize_mcp_connection_status(status)
    _ensure_connection_scope_matches_server(server, normalized_scope_type)

    # 校验 department scope_id 对应的部门存在
    if normalized_scope_type == "department":
        from yuxi.storage.postgres.models_business import Department

        dept_id_int = int(normalized_scope_id) if normalized_scope_id.isdigit() else None
        if dept_id_int is None:
            raise ValueError(f"Invalid department scope_id: '{normalized_scope_id}'")
        dept_result = await db.execute(select(Department).where(Department.id == dept_id_int))
        if dept_result.scalar_one_or_none() is None:
            raise ValueError(f"Department with id '{normalized_scope_id}' does not exist")

    encrypted_credential_blob = (
        encrypt_credential_blob(credential_blob)
        if isinstance(credential_blob, str) and credential_blob.strip()
        else credential_blob
    )

    connection = MCPConnection(
        server_name=server_name,
        scope_type=normalized_scope_type,
        scope_id=normalized_scope_id,
        display_name=display_name,
        external_subject=external_subject,
        status=normalized_status,
        credential_blob=encrypted_credential_blob,
        meta_json=meta_json or {},
        created_by=created_by,
        updated_by=created_by,
    )
    db.add(connection)
    from sqlalchemy.exc import IntegrityError

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ValueError(_format_duplicate_connection_message(server_name, normalized_scope_type))
    await db.refresh(connection)
    return connection


async def update_mcp_connection(
    db: AsyncSession,
    connection_id: int,
    *,
    display_name: str | None = None,
    external_subject: str | None = None,
    credential_blob: Any = _UNSET,
    meta_json: dict[str, Any] | None = None,
    status: str | None = None,
    updated_by: str | None = None,
) -> MCPConnection:
    """Update an MCP connection."""
    connection = await get_mcp_connection(db, connection_id)
    if connection is None:
        raise ValueError(f"MCP connection '{connection_id}' does not exist")

    if display_name is not None:
        connection.display_name = display_name
    if external_subject is not None:
        connection.external_subject = external_subject
    if credential_blob is not _UNSET:
        if isinstance(credential_blob, str) and credential_blob.strip():
            connection.credential_blob = encrypt_credential_blob(credential_blob)
        else:
            connection.credential_blob = credential_blob
    if meta_json is not None:
        connection.meta_json = meta_json
    if status is not None:
        normalized_status = _normalize_mcp_connection_status(status)
        if normalized_status == "active":
            from yuxi.services.mcp.server_service import get_mcp_server

            server = await get_mcp_server(db, connection.server_name)
            if server is None:
                raise ValueError(f"Server '{connection.server_name}' does not exist")
            _ensure_connection_scope_matches_server(server, connection.scope_type)
        connection.status = normalized_status
    if updated_by is not None:
        connection.updated_by = updated_by

    await db.commit()
    await db.refresh(connection)
    return connection


async def delete_mcp_connection(db: AsyncSession, connection_id: int) -> bool:
    """Delete an MCP connection."""
    connection = await get_mcp_connection(db, connection_id)
    if connection is None:
        return False
    await db.delete(connection)
    await db.commit()
    return True


async def set_mcp_connection_status(
    db: AsyncSession,
    connection_id: int,
    *,
    status: str,
    updated_by: str | None = None,
) -> MCPConnection:
    """Set connection status."""
    connection = await get_mcp_connection(db, connection_id)
    if connection is None:
        raise ValueError(f"MCP connection '{connection_id}' does not exist")

    normalized_status = _normalize_mcp_connection_status(status)
    if normalized_status == "active":
        from yuxi.services.mcp.server_service import get_mcp_server

        server = await get_mcp_server(db, connection.server_name)
        if server is None:
            raise ValueError(f"Server '{connection.server_name}' does not exist")
        _ensure_connection_scope_matches_server(server, connection.scope_type)

    connection.status = normalized_status
    if updated_by is not None:
        connection.updated_by = updated_by
    await db.commit()
    await db.refresh(connection)
    return connection
