"""MCP 服务器管理路由"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.services.mcp.server_service import (
    create_mcp_server,
    delete_mcp_server,
    get_all_mcp_servers,
    get_mcp_server,
    set_server_enabled,
    toggle_tool_enabled,
    update_mcp_server,
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


class UpdateMcpServerStatusRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用")


# =============================================================================
# === Helpers ===
# =============================================================================


async def get_server_or_404(db: AsyncSession, name: str):
    """Helper to get server or raise 404."""
    server = await get_mcp_server(db, name)
    if not server:
        raise HTTPException(status_code=404, detail=f"服务器 '{name}' 不存在")
    return server


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
            return {"success": True, "data": [s.to_dict() for s in servers]}
        else:
            # NOTE: 针对普通用户采用高安全显式白名单字段准入投影，使用 getattr 兼容 Mock
            # 仿真对象和历史数据，避免未来新增敏感字段或审计信息越权泄露
            data = []
            for s in servers:
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
            timeout=request.timeout,
            sse_read_timeout=request.sse_read_timeout,
            tags=request.tags,
            icon=request.icon,
            created_by=current_user.username,
        )
        return {"success": True, "data": server.to_dict()}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to create MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.get("/{name}")
async def get_mcp_server_route(
    name: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个 MCP 服务器配置"""
    try:
        server = await get_server_or_404(db, name)
        return {"success": True, "data": server.to_dict()}
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
        fields_set = getattr(request, "model_fields_set", getattr(request, "__fields_set__", set()))
        update_kwargs = {}
        if "env" in fields_set:
            update_kwargs["env"] = request.env

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
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to update MCP server: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@mcp.delete("/{name}")
async def delete_mcp_server_route(
    name: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 MCP 服务器"""
    try:
        # 检查是否为系统内置服务器
        server = await get_mcp_server(db, name)
        if server and server.created_by == "system":
            raise HTTPException(status_code=403, detail="系统内置的 MCP 服务器无法删除")

        deleted = await delete_mcp_server(db, name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"服务器 '{name}' 不存在")
        return {"success": True, "message": f"服务器 '{name}' 已删除"}
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
            "message": f"MCP '{name}' 已{'添加' if is_enabled else '移除'}",
        }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to toggle MCP server: {e}")
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
            # 获取所有工具（不过滤 disabled_tools）
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
            # 获取所有工具（不过滤 disabled_tools）
            tools = await get_all_mcp_tools(name)

            # 获取统计信息
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
