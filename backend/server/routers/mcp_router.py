"""MCP 服务器管理路由"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.services.mcp_auth.config_models import MCPAuthConfig
from yuxi.services.mcp.server_service import (
    create_mcp_server,
    delete_mcp_server,
    get_all_mcp_servers,
    get_mcp_server,
    get_mcp_server_dependency_summary,
    set_server_enabled,
    toggle_tool_enabled,
    update_mcp_server,
)
from yuxi.services.mcp.connection_service import (
    count_mcp_connections,
    create_mcp_connection,
    delete_mcp_connection,
    get_mcp_connection,
    list_mcp_connections,
    list_mcp_connections_page,
    requires_bound_mcp_connection,
    set_mcp_connection_status,
    update_mcp_connection,
)
from yuxi.services.mcp.tool_registry_service import get_all_mcp_tools, get_mcp_tools_stats
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user

mcp = APIRouter(prefix="/system/mcp-servers", tags=["mcp"])


# =============================================================================
# === DTOs ===
# =============================================================================


class CreateMcpServerRequest(BaseModel):
    name: str = Field(..., description="服务器名称")
    transport: str = Field(..., description="传输类型：sse/streamable_http/stdio")
    url: str | None = Field(None, description="服务器 URL（sse/streamable_http）")
    command: str | None = Field(None, description="命令（stdio）")
    args: list | None = Field(None, description="命令参数数组（stdio）")
    env: dict | None = Field(None, description="环境变量（stdio）")
    description: str | None = Field(None, description="描述")
    headers: dict | None = Field(None, description="HTTP 请求头")
    timeout: int | None = Field(None, description="HTTP 超时时间（秒）")
    sse_read_timeout: int | None = Field(None, description="SSE 读取超时（秒）")
    tags: list | None = Field(None, description="标签数组")
    icon: str | None = Field(None, description="图标（emoji）")
    auth_config: dict | None = Field(None, description="MCP 鉴权配置")


class UpdateMcpServerRequest(BaseModel):
    transport: str | None = Field(None, description="传输类型")
    url: str | None = Field(None, description="服务器 URL")
    command: str | None = Field(None, description="命令（stdio）")
    args: list | None = Field(None, description="命令参数数组（stdio）")
    env: dict | None = Field(None, description="环境变量（stdio）")
    description: str | None = Field(None, description="描述")
    headers: dict | None = Field(None, description="HTTP 请求头")
    timeout: int | None = Field(None, description="HTTP 超时时间（秒）")
    sse_read_timeout: int | None = Field(None, description="SSE 读取超时（秒）")
    tags: list | None = Field(None, description="标签数组")
    icon: str | None = Field(None, description="图标（emoji）")
    auth_config: dict | None = Field(None, description="MCP 鉴权配置")


class UpdateMcpServerStatusRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用")


class CreateMcpConnectionRequest(BaseModel):
    scope_type: str = Field(..., description="作用域类型：system/department/user")
    scope_id: str | None = Field(None, description="作用域标识")
    display_name: str | None = Field(None, description="显示名称")
    external_subject: str | None = Field(None, description="外部服务用户/应用标识")
    credential: dict | str | None = Field(None, description="凭据信息")
    meta_json: dict | None = Field(None, description="扩展元信息")
    status: str = Field("active", description="连接状态")


class UpdateMcpConnectionStatusRequest(BaseModel):
    status: str = Field(..., description="连接状态")


class UpdateMcpConnectionRequest(BaseModel):
    display_name: str | None = Field(None, description="显示名称")
    external_subject: str | None = Field(None, description="外部服务用户/应用标识")
    credential: dict | str | None = Field(None, description="凭据信息")
    meta_json: dict | None = Field(None, description="扩展元信息")
    status: str | None = Field(None, description="连接状态")


# =============================================================================
# === Helpers ===
# =============================================================================


async def get_server_or_404(db: AsyncSession, name: str):
    """Helper to get server or raise 404."""
    server = await get_mcp_server(db, name)
    if not server:
        raise HTTPException(status_code=404, detail=f"服务器 '{name}' 不存在")
    return server


def _is_admin_user(current_user: User) -> bool:
    return current_user.role in ["admin", "superadmin"]


def _current_user_scope_id(current_user: User) -> str:
    db_id = getattr(current_user, "id", None)
    login_id = getattr(current_user, "user_id", None)
    resolved_user_id = db_id if db_id is not None else login_id
    if resolved_user_id is None:
        raise HTTPException(status_code=400, detail="无法解析当前用户标识")
    return str(resolved_user_id)


def _validate_auth_config_or_400(payload: dict | None) -> dict | None:
    if not payload:
        return None
    try:
        return MCPAuthConfig.model_validate(payload).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"auth_config 校验失败: {exc}") from exc


def _ensure_mcp_server_visible_to_user(server, current_user: User) -> None:
    if _is_admin_user(current_user):
        return
    if not bool(getattr(server, "enabled", True)):
        raise HTTPException(status_code=404, detail=f"服务器 '{getattr(server, 'name', '')}' 不存在")


def _ensure_personal_connection_server(server, current_user: User, *, include_admin: bool = False) -> None:
    _ensure_mcp_server_visible_to_user(server, current_user)
    if _is_admin_user(current_user) and not include_admin:
        return
    try:
        auth_config = MCPAuthConfig.model_validate(getattr(server, "auth_config_json", None) or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail="该 MCP 服务器没有有效的鉴权配置") from exc
    if auth_config.binding_scope != "user":
        raise HTTPException(status_code=403, detail="该 MCP 服务器不支持个人连接管理")


def _ensure_connection_accessible_to_user(connection, current_user: User) -> None:
    if _is_admin_user(current_user):
        return
    if connection.scope_type != "user" or connection.scope_id != _current_user_scope_id(current_user):
        raise HTTPException(status_code=404, detail=f"连接 '{connection.id}' 不存在")


async def get_connection_for_server_or_404(
    db: AsyncSession,
    server_name: str,
    connection_id: int,
    current_user: User | None = None,
):
    connection = await get_mcp_connection(db, connection_id)
    if connection is None or connection.server_name != server_name:
        raise HTTPException(status_code=404, detail=f"连接 '{connection_id}' 不存在")
    if current_user is not None:
        _ensure_connection_accessible_to_user(connection, current_user)
    return connection


def _normalize_credential_blob(value: dict | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


# =============================================================================
# === MCP 服务器 CRUD ===
# =============================================================================


@mcp.get("")
async def get_mcp_servers(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取所有 MCP 服务器配置（普通用户仅获取脱敏的基础信息）"""
    try:
        servers = await get_all_mcp_servers(db)
        if _is_admin_user(current_user):
            return {"success": True, "data": [s.to_dict() for s in servers]}
        else:
            data = []
            for s in servers:
                if not bool(getattr(s, "enabled", True)):
                    continue
                data.append(
                    {
                        "name": getattr(s, "name", ""),
                        "description": getattr(s, "description", None),
                        "icon": getattr(s, "icon", None),
                        "enabled": bool(getattr(s, "enabled", True)),
                        "tags": getattr(s, "tags", None) or [],
                    }
                )
            return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Failed to get MCP servers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.post("")
async def create_mcp_server_route(
    request: CreateMcpServerRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建新的 MCP 服务器"""
    # 校验传输类型
    valid_transports = ("sse", "streamable_http", "stdio")
    if request.transport not in valid_transports:
        raise HTTPException(status_code=400, detail=f"传输类型必须是 {', '.join(valid_transports)} 之一")

    # 根据传输类型校验必填字段
    if request.transport in ("sse", "streamable_http") and not request.url:
        raise HTTPException(status_code=400, detail=f"传输类型为 {request.transport} 时，url 必填")
    if request.transport == "stdio" and not request.command:
        raise HTTPException(status_code=400, detail="传输类型为 stdio 时，command 必填")

    try:
        auth_config = _validate_auth_config_or_400(request.auth_config)
        server = await create_mcp_server(
            db,
            name=request.name,
            transport=request.transport,
            url=request.url,
            command=request.command,
            args=request.args,
            env=request.env,
            description=request.description,
            headers=request.headers,
            auth_config=auth_config,
            timeout=request.timeout,
            sse_read_timeout=request.sse_read_timeout,
            tags=request.tags,
            icon=request.icon,
            created_by=current_user.username,
        )
        return {"success": True, "data": server.to_dict()}
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to create MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.get("/{name}")
async def get_mcp_server_route(
    name: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个 MCP 服务器配置（普通用户仅获取脱敏信息）"""
    try:
        server = await get_server_or_404(db, name)
        _ensure_mcp_server_visible_to_user(server, current_user)
        if _is_admin_user(current_user):
            return {"success": True, "data": server.to_dict()}
        # 普通用户：只返回基本信息和脱敏 auth_config
        from yuxi.services.mcp_auth.config_models import MCPAuthConfig

        payload = getattr(server, "auth_config_json", None)
        public_auth = {}
        if payload:
            try:
                ac = MCPAuthConfig.model_validate(payload)
                public_auth = {
                    "version": ac.version,
                    "provider": ac.provider,
                    "binding_scope": ac.binding_scope,
                    "manifest_scope": ac.manifest_scope,
                    "secret_fields": ac.get_secret_fields(),
                }
            except Exception:
                pass
        return {
            "success": True,
            "data": {
                "name": getattr(server, "name", ""),
                "description": getattr(server, "description", None),
                "transport": getattr(server, "transport", None),
                "auth_config": public_auth,
                "tags": getattr(server, "tags", None) or [],
                "icon": getattr(server, "icon", None),
                "enabled": bool(getattr(server, "enabled", True)),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{name}")
async def update_mcp_server_route(
    name: str,
    request: UpdateMcpServerRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 MCP 服务器配置"""
    # 校验传输类型
    valid_transports = ("sse", "streamable_http", "stdio")
    if request.transport is not None and request.transport not in valid_transports:
        raise HTTPException(status_code=400, detail=f"传输类型必须是 {', '.join(valid_transports)} 之一")

    try:
        fields_set = request.model_fields_set
        update_kwargs = {}
        if "env" in fields_set:
            update_kwargs["env"] = request.env
        if "auth_config" in fields_set:
            update_kwargs["auth_config"] = _validate_auth_config_or_400(request.auth_config)

        server = await update_mcp_server(
            db,
            name=name,
            description=request.description,
            transport=request.transport,
            url=request.url,
            command=request.command,
            args=request.args,
            headers=request.headers,
            timeout=request.timeout,
            sse_read_timeout=request.sse_read_timeout,
            tags=request.tags,
            icon=request.icon,
            updated_by=current_user.username,
            **update_kwargs,
        )
        return {"success": True, "data": server.to_dict()}
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to update MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.delete("/{name}")
async def delete_mcp_server_route(
    name: str,
    hard: bool = False,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 MCP 服务器（soft delete 或 hard delete）"""
    try:
        # 检查是否为系统内置服务器
        server = await get_mcp_server(db, name)
        if not server:
            raise HTTPException(status_code=404, detail=f"服务器 '{name}' 不存在")
        if server.created_by == "system":
            raise HTTPException(status_code=403, detail="系统内置的 MCP 服务器无法删除")

        if not hard:
            await set_server_enabled(db, name, False, current_user.username)
            return {"success": True, "message": f"服务器 '{name}' 已停用"}

        if bool(server.enabled):
            raise HTTPException(status_code=409, detail="服务器仍处于启用状态，请先停用")

        dependency_summary = await get_mcp_server_dependency_summary(db, name)
        if dependency_summary["has_references"]:
            raise HTTPException(status_code=409, detail=dependency_summary)

        deleted = await delete_mcp_server(db, name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"服务器 '{name}' 不存在")
        return {"success": True, "message": f"服务器 '{name}' 已彻底删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# === MCP 服务器操作 ===
# =============================================================================


@mcp.post("/{name}/test")
async def test_mcp_server(
    name: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """测试 MCP 服务器连接"""
    try:
        await get_server_or_404(db, name)

        try:
            tools = await get_all_mcp_tools(name)
            return {
                "success": True,
                "message": f"连接成功，共发现 {len(tools)} 个工具",
                "tool_count": len(tools),
            }
        except Exception as test_error:
            raise HTTPException(status_code=500, detail=f"连接失败: {str(test_error)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to test MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{name}/status")
async def update_mcp_server_status_route(
    name: str,
    request: UpdateMcpServerStatusRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 MCP 服务器启用状态"""
    try:
        is_enabled, server = await set_server_enabled(db, name, request.enabled, current_user.username)
        return {
            "success": True,
            "enabled": is_enabled,
            "data": server.to_dict(),
            "message": f"MCP '{name}' 已{'启用' if is_enabled else '停用'}",
        }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to toggle MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# === MCP 连接管理 ===
# =============================================================================


@mcp.get("/{name}/connections")
async def get_mcp_connections(
    name: str,
    mine: bool = False,
    paginated: bool = False,
    status: str = Query("all", description="状态筛选：all/active/attention/disabled"),
    search: str | None = Query(None, description="搜索关键字"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(12, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        server = await get_server_or_404(db, name)
        _ensure_mcp_server_visible_to_user(server, current_user)
        list_kwargs = {"server_name": name}
        if mine or not _is_admin_user(current_user):
            _ensure_personal_connection_server(server, current_user, include_admin=mine)
            list_kwargs.update({"scope_type": "user", "scope_id": _current_user_scope_id(current_user)})
        if paginated or status != "all" or search:
            effective_scope_type = None
            credentials_required = False
            try:
                auth_config = MCPAuthConfig.model_validate(getattr(server, "auth_config_json", None) or {})
                if auth_config.binding_scope in {"system", "department", "user"}:
                    effective_scope_type = auth_config.binding_scope
                credentials_required = requires_bound_mcp_connection(auth_config)
            except Exception:
                effective_scope_type = None
                credentials_required = False

            connections, total = await list_mcp_connections_page(
                db,
                **list_kwargs,
                status_filter=status,
                effective_scope_type=effective_scope_type,
                credentials_required=credentials_required,
                search=search,
                page=page,
                page_size=page_size,
            )
            summary = {
                "total": await count_mcp_connections(db, **list_kwargs),
                "active": await count_mcp_connections(
                    db,
                    **list_kwargs,
                    status_filter="active",
                    effective_scope_type=effective_scope_type,
                    credentials_required=credentials_required,
                ),
                "attention": await count_mcp_connections(
                    db,
                    **list_kwargs,
                    status_filter="attention",
                    effective_scope_type=effective_scope_type,
                    credentials_required=credentials_required,
                ),
                "disabled": await count_mcp_connections(db, **list_kwargs, status_filter="disabled"),
            }
            return {
                "success": True,
                "data": {
                    "items": [item.to_dict() for item in connections],
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "summary": summary,
                },
            }
        connections = await list_mcp_connections(db, **list_kwargs)
        return {"success": True, "data": [item.to_dict() for item in connections]}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list MCP connections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.post("/{name}/connections")
async def create_mcp_connection_route(
    name: str,
    request: CreateMcpConnectionRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        server = await get_server_or_404(db, name)
        _ensure_personal_connection_server(server, current_user)
        scope_type = request.scope_type
        scope_id = request.scope_id
        if scope_type == "user" and not scope_id:
            scope_id = _current_user_scope_id(current_user)
        if not _is_admin_user(current_user):
            if request.scope_type != "user":
                raise HTTPException(status_code=403, detail="普通用户只能创建个人 MCP 连接")
            scope_type = "user"
            scope_id = _current_user_scope_id(current_user)
        connection = await create_mcp_connection(
            db,
            server_name=name,
            scope_type=scope_type,
            scope_id=scope_id,
            display_name=request.display_name,
            external_subject=request.external_subject,
            status=request.status,
            credential_blob=_normalize_credential_blob(request.credential),
            meta_json=request.meta_json,
            created_by=current_user.username,
        )
        return {"success": True, "data": connection.to_dict()}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create MCP connection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{name}/connections/{connection_id}")
async def update_mcp_connection_route(
    name: str,
    connection_id: int,
    request: UpdateMcpConnectionRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        server = await get_server_or_404(db, name)
        _ensure_personal_connection_server(server, current_user)
        await get_connection_for_server_or_404(db, name, connection_id, current_user)
        fields_set = request.model_fields_set
        update_kwargs = {}
        if "credential" in fields_set:
            update_kwargs["credential_blob"] = _normalize_credential_blob(request.credential)
        if "display_name" in fields_set:
            update_kwargs["display_name"] = request.display_name
        if "external_subject" in fields_set:
            update_kwargs["external_subject"] = request.external_subject
        if "meta_json" in fields_set:
            update_kwargs["meta_json"] = request.meta_json
        if "status" in fields_set:
            update_kwargs["status"] = request.status

        connection = await update_mcp_connection(
            db,
            connection_id,
            updated_by=current_user.username,
            **update_kwargs,
        )
        return {"success": True, "data": connection.to_dict(), "message": "连接已更新"}
    except ValueError as ve:
        err_msg = str(ve)
        if "does not exist" in err_msg:
            raise HTTPException(status_code=404, detail=err_msg)
        raise HTTPException(status_code=400, detail=err_msg)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update MCP connection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{name}/connections/{connection_id}/status")
async def update_mcp_connection_status_route(
    name: str,
    connection_id: int,
    request: UpdateMcpConnectionStatusRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        server = await get_server_or_404(db, name)
        _ensure_personal_connection_server(server, current_user)
        await get_connection_for_server_or_404(db, name, connection_id, current_user)
        connection = await set_mcp_connection_status(
            db,
            connection_id,
            status=request.status,
            updated_by=current_user.username,
        )
        return {"success": True, "data": connection.to_dict(), "message": "状态已更新"}
    except ValueError as ve:
        err_msg = str(ve)
        if "does not exist" in err_msg:
            raise HTTPException(status_code=404, detail=err_msg)
        raise HTTPException(status_code=400, detail=err_msg)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update MCP connection status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.delete("/{name}/connections/{connection_id}")
async def delete_mcp_connection_route(
    name: str,
    connection_id: int,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        server = await get_server_or_404(db, name)
        _ensure_personal_connection_server(server, current_user)
        await get_connection_for_server_or_404(db, name, connection_id, current_user)
        deleted = await delete_mcp_connection(db, connection_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"连接 '{connection_id}' 不存在")
        return {"success": True, "message": "连接已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete MCP connection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# === MCP 工具管理 ===
# =============================================================================


@mcp.get("/{name}/tools")
async def get_mcp_server_tools(
    name: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 MCP 服务器的工具列表"""
    try:
        server = await get_server_or_404(db, name)
        disabled_tools = server.disabled_tools or []

        try:
            tools = await get_all_mcp_tools(name)
            tool_list = []

            for tool in tools:
                original_name = tool.name
                unique_id = tool.metadata.get("id") if tool.metadata else original_name

                tool_info = {
                    "name": original_name,
                    "id": unique_id,
                    "description": getattr(tool, "description", ""),
                    "enabled": original_name not in disabled_tools,
                }
                if hasattr(tool, "args_schema") and tool.args_schema:
                    schema = tool.args_schema.schema() if hasattr(tool.args_schema, "schema") else {}
                    tool_info["parameters"] = schema.get("properties", {})
                    tool_info["required"] = schema.get("required", [])
                else:
                    tool_info["parameters"] = {}
                    tool_info["required"] = []
                tool_list.append(tool_info)

            return {
                "success": True,
                "data": tool_list,
                "total": len(tool_list),
            }
        except Exception as tool_error:
            logger.error(f"Failed to get tools from MCP server '{name}': {tool_error}")
            raise HTTPException(status_code=500, detail=f"获取工具失败: {str(tool_error)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get MCP server tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.post("/{name}/tools/refresh")
async def refresh_mcp_server_tools(
    name: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """刷新 MCP 服务器的工具列表（清除缓存重新获取）"""
    try:
        await get_server_or_404(db, name)

        try:
            tools = await get_all_mcp_tools(name)

            stats = get_mcp_tools_stats(name)
            enabled_count = stats.get("enabled", len(tools)) if stats else len(tools)
            disabled_count = stats.get("disabled", 0) if stats else 0

            message = "工具列表已刷新"
            if disabled_count > 0:
                message += f"，{enabled_count} 个已启用，{disabled_count} 个已禁用"
            else:
                message += f"，共发现 {enabled_count} 个工具"

            return {
                "success": True,
                "message": message,
                "tool_count": enabled_count,
                "enabled_count": enabled_count,
                "disabled_count": disabled_count,
            }
        except Exception as tool_error:
            raise HTTPException(status_code=500, detail=f"刷新失败: {str(tool_error)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to refresh MCP server tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{name}/tools/{tool_name}/toggle")
async def toggle_mcp_server_tool_route(
    name: str,
    tool_name: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """切换单个工具的启用状态"""
    try:
        enabled, server = await toggle_tool_enabled(db, name, tool_name, current_user.username)
        return {
            "success": True,
            "tool_name": tool_name,
            "enabled": enabled,
            "message": f"工具 '{tool_name}' 已{'启用' if enabled else '禁用'}",
        }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to toggle MCP server tool: {e}")
        raise HTTPException(status_code=500, detail=str(e))
