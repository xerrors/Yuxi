"""MCP 服务器管理路由"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.mcp.service import (
    MCPServerNotFoundError,
    create_mcp_server,
    delete_mcp_server,
    get_all_mcp_servers,
    get_all_mcp_tools,
    inspect_mcp_server_tools,
    get_mcp_server,
    get_mcp_tools_stats,
    is_builtin_mcp_server,
    requires_mcp_transport_migration,
    set_server_enabled,
    toggle_tool_enabled,
    update_mcp_server,
)
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger

from server.utils.auth_middleware import get_admin_user, get_db, get_required_user

mcp = APIRouter(prefix="/system/mcp-servers", tags=["mcp"])


# =============================================================================
# === DTOs ===
# =============================================================================


class CreateMcpServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., description="稳定标识")
    name: str = Field(..., description="展示名称")
    transport: str = Field(..., description="传输类型：sse/streamable_http")
    url: str | None = Field(None, description="服务器 URL")
    description: str | None = Field(None, description="描述")
    headers: dict | None = Field(None, description="HTTP 请求头")
    timeout: int | None = Field(None, description="HTTP 超时时间（秒）")
    sse_read_timeout: int | None = Field(None, description="SSE 读取超时（秒）")
    tags: list | None = Field(None, description="标签数组")
    icon: str | None = Field(None, description="图标（emoji）")


class UpdateMcpServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, description="展示名称")
    transport: str | None = Field(None, description="传输类型")
    url: str | None = Field(None, description="服务器 URL")
    description: str | None = Field(None, description="描述")
    headers: dict | None = Field(None, description="HTTP 请求头")
    timeout: int | None = Field(None, description="HTTP 超时时间（秒）")
    sse_read_timeout: int | None = Field(None, description="SSE 读取超时（秒）")
    tags: list | None = Field(None, description="标签数组")
    icon: str | None = Field(None, description="图标（emoji）")


class UpdateMcpServerStatusRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用")


# =============================================================================
# === Helpers ===
# =============================================================================


async def get_server_or_404(db: AsyncSession, slug: str):
    """Helper to get server or raise 404."""
    server = await get_mcp_server(db, slug)
    if not server:
        raise HTTPException(status_code=404, detail=f"服务器 '{slug}' 不存在")
    return server


def serialize_mcp_server(server) -> dict:
    """序列化 MCP，并补充代码内置与迁移状态。"""
    data = server.to_dict()
    data["is_builtin"] = is_builtin_mcp_server(server)
    data["requires_migration"] = requires_mcp_transport_migration(server)
    if data["requires_migration"]:
        data["enabled"] = False
    return data


def ensure_mcp_server_runnable(server) -> None:
    """拒绝连接尚未迁移的非远程 MCP。"""
    if requires_mcp_transport_migration(server):
        raise HTTPException(status_code=400, detail="历史 stdio MCP 已被禁用，请先迁移为远程 MCP")


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
        if current_user.role in ["admin", "superadmin"]:
            return {"success": True, "data": [serialize_mcp_server(s) for s in servers]}

        data = []
        for s in servers:
            data.append(
                {
                    "name": getattr(s, "name", ""),
                    "description": getattr(s, "description", None),
                    "icon": getattr(s, "icon", None),
                    "enabled": bool(getattr(s, "enabled", True)) and not requires_mcp_transport_migration(s),
                    "tags": getattr(s, "tags", None) or [],
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
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建新的 MCP 服务器"""
    # 校验传输类型
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
            tags=request.tags,
            icon=request.icon,
            created_by=current_user.username,
        )
        return {"success": True, "data": serialize_mcp_server(server)}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.get("/{slug}")
async def get_mcp_server_route(
    slug: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个 MCP 服务器配置"""
    try:
        server = await get_server_or_404(db, slug)
        return {"success": True, "data": serialize_mcp_server(server)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{slug}")
async def update_mcp_server_route(
    slug: str,
    request: UpdateMcpServerRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 MCP 服务器配置"""
    # 校验传输类型
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
            tags=request.tags,
            icon=request.icon,
            updated_by=current_user.username,
        )
        return {"success": True, "data": serialize_mcp_server(server)}
    except HTTPException:
        raise
    except MCPServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to update MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.delete("/{slug}")
async def delete_mcp_server_route(
    slug: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 MCP 服务器"""
    try:
        # 检查是否为系统内置服务器
        server = await get_mcp_server(db, slug)
        if server and is_builtin_mcp_server(server):
            raise HTTPException(status_code=403, detail="系统内置的 MCP 服务器无法删除")

        deleted = await delete_mcp_server(db, slug)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"服务器 '{slug}' 不存在")
        return {"success": True, "message": f"服务器 '{slug}' 已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# === MCP 服务器操作 ===
# =============================================================================


@mcp.post("/{slug}/test")
async def test_mcp_server(
    slug: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """测试 MCP 服务器连接"""
    try:
        server = await get_server_or_404(db, slug)
        ensure_mcp_server_runnable(server)

        try:
            tools = await inspect_mcp_server_tools(server)
            return {
                "success": True,
                "message": f"连接成功，共发现 {len(tools)} 个工具",
                "tool_count": len(tools),
            }
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=502, detail="MCP 连接失败，请检查服务地址、凭据和网络后重试") from None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to test MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{slug}/status")
async def update_mcp_server_status_route(
    slug: str,
    request: UpdateMcpServerStatusRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 MCP 服务器启用状态"""
    try:
        is_enabled, server = await set_server_enabled(db, slug, request.enabled, current_user.username)
        return {
            "success": True,
            "enabled": is_enabled,
            "data": serialize_mcp_server(server),
            "message": f"MCP '{slug}' 已{'添加' if is_enabled else '移除'}",
        }
    except MCPServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# === MCP 工具管理 ===
# =============================================================================


@mcp.get("/{slug}/tools")
async def get_mcp_server_tools(
    slug: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取 MCP 服务器的工具列表"""
    try:
        server = await get_server_or_404(db, slug)
        ensure_mcp_server_runnable(server)
        disabled_tools = server.disabled_tools or []

        try:
            # 获取所有工具（不过滤 disabled_tools）
            tools = await get_all_mcp_tools(slug)
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
                # 提取参数信息
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


@mcp.post("/{slug}/tools/refresh")
async def refresh_mcp_server_tools(
    slug: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """刷新 MCP 服务器的工具列表（清除缓存重新获取）"""
    try:
        server = await get_server_or_404(db, slug)
        ensure_mcp_server_runnable(server)

        try:
            # 获取所有工具（不过滤 disabled_tools）
            tools = await get_all_mcp_tools(slug)

            # 获取统计信息
            stats = get_mcp_tools_stats(slug)
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
        except HTTPException:
            raise
        except Exception as tool_error:
            raise HTTPException(status_code=500, detail=f"刷新失败: {str(tool_error)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to refresh MCP server tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.put("/{slug}/tools/{tool_name}/toggle")
async def toggle_mcp_server_tool_route(
    slug: str,
    tool_name: str,
    current_user: User = Depends(get_admin_user),
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
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle MCP server tool: {e}")
        raise HTTPException(status_code=500, detail=str(e))
