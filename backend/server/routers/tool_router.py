from fastapi import APIRouter, Depends, Body

from yuxi.services.tool_display_service import list_display_tools, set_display_name
from server.utils.auth_middleware import get_required_user, get_admin_user
from yuxi.storage.postgres.models_business import User

tools = APIRouter(prefix="/system/tools", tags=["tools"])


@tools.get("")
async def list_tools(
    category: str = None,
    user: User = Depends(get_required_user),
):
    """获取工具列表"""
    return {"success": True, "data": await list_display_tools(category)}


@tools.get("/mcp-display-names")
async def get_mcp_tool_display_names(user: User = Depends(get_required_user)):
    """返回聊天展示映射，不向注册表添加可调用工具。"""
    from yuxi.services.mcp_display_service import list_mcp_tool_display_names

    return {"success": True, "data": await list_mcp_tool_display_names()}


@tools.get("/options")
async def get_tool_options(
    user: User = Depends(get_required_user),
):
    """获取工具选项（前端下拉框用）"""
    all_tools = await list_display_tools()
    return {"success": True, "data": [{"label": t["name"], "value": t["slug"]} for t in all_tools]}


@tools.put("/{slug}/display-name")
async def update_display_name(slug: str, name: str = Body(..., embed=True), user: User = Depends(get_admin_user)):
    """管理员设置独立显示名称；空名称恢复原展示。"""
    await set_display_name(slug, name, user.uid)
    return {"success": True}
