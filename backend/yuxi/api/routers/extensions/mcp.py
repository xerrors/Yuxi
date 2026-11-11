"""MCP 服务器管理路由"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_db, get_required_user, get_superadmin_user
from yuxi.api.routers.extensions.mcp_oauth import router as oauth_router
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.extensions.mcp.oauth_config import PERSONAL_AUTH_MODES, STATIC_AUTH_MODES, config_revision
from yuxi.modules.extensions.mcp.repository import get_all_mcp_servers, get_mcp_server
from yuxi.modules.extensions.mcp.service import (
    MCPServerNotFoundError,
    available_mcp_servers,
    create_mcp_server,
    delete_mcp_server,
    get_all_mcp_tools,
    inspect_mcp_server_tools,
    is_builtin_mcp_server,
    personal_connection_states,
    runtime_mcp_failure,
    set_server_enabled,
    toggle_tool_enabled,
    update_mcp_server,
)
from yuxi.modules.identity.models import User
from yuxi.shared.errors import DomainError

mcp = APIRouter(prefix="/system/mcp-servers", tags=["mcp"])
mcp.include_router(oauth_router)


# --- DTOs ---


class CreateMcpServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_mode: str = "headers"
    oauth_config: dict | None = None
    api_key_header: str | None = None
    slug: str = Field(..., description="稳定标识")
    name: str = Field(..., description="展示名称")
    transport: str = Field(..., description="传输类型：sse/streamable_http")
    url: str | None = Field(None, description="服务器 URL")
    description: str | None = Field(None, description="描述")
    headers: dict | None = Field(None, description="HTTP 请求头")
    timeout: StrictInt | None = Field(None, description="HTTP 超时时间（秒）")
    sse_read_timeout: StrictInt | None = Field(None, description="SSE 读取超时（秒）")


class UpdateMcpServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_mode: str | None = None
    oauth_config: dict | None = None
    api_key_header: str | None = None

    name: str | None = Field(None, description="展示名称")
    transport: str | None = Field(None, description="传输类型")
    url: str | None = Field(None, description="服务器 URL")
    description: str | None = Field(None, description="描述")
    headers: dict | None = Field(None, description="HTTP 请求头")
    timeout: StrictInt | None = Field(None, description="HTTP 超时时间（秒）")
    sse_read_timeout: StrictInt | None = Field(None, description="SSE 读取超时（秒）")


class UpdateMcpServerStatusRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用")


# --- MCP 服务器 CRUD ---


@mcp.get("")
async def get_mcp_servers(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """获取所有 MCP 服务器配置（普通用户仅获取脱敏的基础信息）"""
    try:
        servers = await get_all_mcp_servers(db)
        states = await personal_connection_states(db, servers, current_user.id)
        available = await available_mcp_servers(db, [server.slug for server in servers], user_id=current_user.id)
        if current_user.role == "superadmin":
            return {
                "success": True,
                "data": [
                    {**serialize_mcp_server(server), "connection_status": states[server.slug], "available": server.slug in available}
                    for server in servers
                ],
            }

        data = []
        for server in servers:
            if not server.enabled and states[server.slug] not in {"connected", "reconnect_required"}:
                continue
            data.append(
                {
                    "slug": server.slug,
                    "auth_mode": server.auth_mode,
                    **(
                        {"config_revision": config_revision(server), "api_key_header": server.api_key_header}
                        if server.auth_mode in STATIC_AUTH_MODES
                        else {}
                    ),
                    "connection_status": states[server.slug],
                    "available": server.slug in available,
                    "can_manage": False,
                    "name": getattr(server, "name", ""),
                    "description": getattr(server, "description", None),
                    "enabled": bool(getattr(server, "enabled", True)),
                    "is_builtin": is_builtin_mcp_server(server),
                }
            )
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get MCP servers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.post("")
async def create_mcp_server_route(
    request: CreateMcpServerRequest,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建新的 MCP 服务器"""
    valid_transports = ("sse", "streamable_http")
    if request.transport not in valid_transports:
        raise HTTPException(status_code=400, detail=f"传输类型必须是 {', '.join(valid_transports)} 之一")

    # 根据传输类型校验必填字段
    if not request.url:
        raise HTTPException(status_code=400, detail=f"传输类型为 {request.transport} 时，url 必填")

    try:
        server = await create_mcp_server(
            db,
            slug=request.slug,
            name=request.name,
            transport=request.transport,
            url=request.url,
            description=request.description,
            headers=request.headers,
            timeout=request.timeout,
            sse_read_timeout=request.sse_read_timeout,
            created_by=current_user.username,
            auth_mode=request.auth_mode,
            oauth_config=request.oauth_config,
            api_key_header=request.api_key_header,
        )
        return {"success": True, "data": serialize_mcp_server(server)}
    except ValueError:
        raise HTTPException(status_code=400, detail="连接器配置无效，请检查地址、认证方式和必填字段")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.get("/{slug}")
async def get_mcp_server_route(
    slug: str,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个 MCP 服务器配置"""
    try:
        server = await get_server_or_404(db, slug)
        states = await personal_connection_states(db, [server], current_user.id)
        return {"success": True, "data": {**serialize_mcp_server(server), "connection_status": states[server.slug]}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{slug}")
async def update_mcp_server_route(
    slug: str,
    request: UpdateMcpServerRequest,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 MCP 服务器配置"""
    valid_transports = ("sse", "streamable_http")
    if request.transport is not None and request.transport not in valid_transports:
        raise HTTPException(status_code=400, detail=f"传输类型必须是 {', '.join(valid_transports)} 之一")

    try:
        server = await update_mcp_server(
            db,
            slug=slug,
            name=request.name,
            description=request.description,
            transport=request.transport,
            url=request.url,
            headers=request.headers,
            timeout=request.timeout,
            sse_read_timeout=request.sse_read_timeout,
            updated_by=current_user.username,
            auth_mode=request.auth_mode,
            oauth_config=request.oauth_config,
            api_key_header=request.api_key_header,
        )
        return {"success": True, "data": serialize_mcp_server(server)}
    except HTTPException:
        raise
    except MCPServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except ValueError:
        raise HTTPException(status_code=400, detail="连接器配置无效，请检查地址、认证方式和必填字段")
    except Exception as e:
        logger.error(f"Failed to update MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.delete("/{slug}")
async def delete_mcp_server_route(
    slug: str,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 MCP 服务器"""
    try:
        server = await get_mcp_server(db, slug)
        if server and is_builtin_mcp_server(server):
            raise HTTPException(status_code=403, detail="系统内置的连接器无法删除")

        deleted = await delete_mcp_server(db, slug)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"服务器 '{slug}' 不存在")
        return {"success": True, "message": f"服务器 '{slug}' 已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- MCP 服务器操作 ---


@mcp.post("/{slug}/test")
async def test_mcp_server(
    slug: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """测试 MCP 服务器连接"""
    try:
        server = await get_server_or_404(db, slug)
        if server.auth_mode not in PERSONAL_AUTH_MODES and current_user.role != "superadmin":
            raise HTTPException(status_code=403, detail="只有超级管理员可以测试共享或内置服务")
        try:
            tools = await inspect_mcp_server_tools(server, uid=current_user.uid)
            return {
                "success": True,
                "message": f"连接成功，共发现 {len(tools)} 个工具",
                "tool_count": len(tools),
            }
        except HTTPException:
            raise
        except Exception as exc:
            detail = "连接器连接失败，请检查服务地址、凭据和网络后重试"
            if server.auth_mode in PERSONAL_AUTH_MODES:
                reason, _ = runtime_mcp_failure(exc)
                detail = {"code": "mcp_connection_failed", "message": reason}
            raise HTTPException(status_code=502, detail=detail) from None
    except (HTTPException, DomainError):
        raise
    except Exception as e:
        logger.error(f"Failed to test MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{slug}/status")
async def update_mcp_server_status_route(
    slug: str,
    request: UpdateMcpServerStatusRequest,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 MCP 服务器启用状态"""
    try:
        is_enabled, server = await set_server_enabled(db, slug, request.enabled, current_user.username)
        return {
            "success": True,
            "enabled": is_enabled,
            "data": serialize_mcp_server(server),
            "message": f"连接器 '{slug}' 已{'添加' if is_enabled else '移除'}",
        }
    except MCPServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError:
        raise HTTPException(status_code=400, detail="连接器配置无效，请检查地址、认证方式和必填字段")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- MCP 工具管理 ---


@mcp.get("/{slug}/tools")
async def get_mcp_server_tools(
    slug: str,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 MCP 服务器的工具列表"""
    try:
        server = await get_server_or_404(db, slug)
        disabled_tools = server.disabled_tools or []

        try:
            # 全量工具列表，启用状态由 disabled_tools 在响应中标记
            tools = await get_all_mcp_tools(slug, uid=current_user.uid)
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
                # 提取参数信息：从 args_schema 读取属性与必填项
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
        except HTTPException:
            raise
        except Exception as tool_error:
            logger.error(f"Failed to get tools from MCP server '{slug}': {tool_error}")
            raise HTTPException(status_code=500, detail=f"获取工具失败: {str(tool_error)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get MCP server tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{slug}/tools/{tool_name}/toggle")
async def toggle_mcp_server_tool_route(
    slug: str,
    tool_name: str,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """切换单个工具的启用状态"""
    try:
        enabled, _ = await toggle_tool_enabled(db, slug, tool_name, current_user.username)
        return {
            "success": True,
            "tool_name": tool_name,
            "enabled": enabled,
            "message": f"工具 '{tool_name}' 已{'启用' if enabled else '禁用'}",
        }
    except ValueError:
        raise HTTPException(status_code=404, detail="连接器配置无效，请检查地址、认证方式和必填字段")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle MCP server tool: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_server_or_404(db: AsyncSession, slug: str):
    """按 slug 加载服务器，不存在时返回 404。"""
    server = await get_mcp_server(db, slug)
    if not server:
        raise HTTPException(status_code=404, detail=f"服务器 '{slug}' 不存在")
    return server


def serialize_mcp_server(server) -> dict:
    """序列化 MCP，并补充代码内置标识。"""
    data = server.to_dict()
    if data.get("auth_mode") in STATIC_AUTH_MODES:
        data["config_revision"] = config_revision(server)
    data["can_manage"] = True
    data["is_builtin"] = is_builtin_mcp_server(server)
    return data
