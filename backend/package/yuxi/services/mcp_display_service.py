"""MCP 纯展示改名，保留连接字段与运行身份。"""

from fastapi import HTTPException
from yuxi.agents.mcp.service import inspect_mcp_server_tools, is_builtin_mcp_server, requires_mcp_stdio_migration
from yuxi.repositories import tool_display_repository
from yuxi.repositories.mcp_display_repository import lock_server
from yuxi.services.resource_display_service import MCP_NAMES, MCP_TOOL_NAMES, mcp_tool_key
from yuxi.services.tool_display_service import validate_display_name


async def set_mcp_display_name(db, slug, name, actor):
    """内置保存展示覆盖，其他服务器只更新名称，不重配置连接。"""
    name = validate_display_name(name)
    server = await lock_server(db, slug)
    if server is None:
        raise HTTPException(404, "MCP不存在")
    if is_builtin_mcp_server(server):
        await tool_display_repository.save_name(slug, name, actor, key=MCP_NAMES)
        return {"slug": slug, "name": name or server.name}
    if not name:
        raise HTTPException(422, "MCP显示名称需为非空文本")
    server.name = name
    server.updated_by = actor
    await db.commit()
    return {"slug": slug, "name": name}


async def set_mcp_tool_display_name(db, slug, tool_name, name, actor):
    """只给已发现工具写展示覆盖，协议工具名保持原值。"""
    name = validate_display_name(name)
    server = await lock_server(db, slug)
    if server is None:
        raise HTTPException(404, "MCP不存在")
    if requires_mcp_stdio_migration(server):
        raise HTTPException(400, "历史 stdio MCP 已被禁用，请先迁移为远程 MCP")
    try:
        tools = await inspect_mcp_server_tools(server)
    except Exception:
        raise HTTPException(502, "MCP 连接失败，请检查服务地址、凭据和网络后重试") from None
    if not any(tool.name == tool_name for tool in tools):
        raise HTTPException(404, "MCP工具不存在")
    await tool_display_repository.save_name(mcp_tool_key(slug, tool_name), name, actor, key=MCP_TOOL_NAMES)
    return {"name": tool_name, "display_name": name or tool_name}


async def list_mcp_tool_display_names():
    """沿用运行时 MCP ID 编码，只返回展示字段。"""
    import json

    from yuxi.agents.mcp.service import to_camel_case

    names = await tool_display_repository.read_names(key=MCP_TOOL_NAMES)
    result = []
    for key, name in names.items():
        slug, tool_name = json.loads(key)
        result.append({"slug": f"mcp__{to_camel_case(slug)}__{to_camel_case(tool_name)}", "name": name})
    return result
