"""仅在工具列表投影应用显示名，不修改 callable 或 schema。"""

from fastapi import HTTPException
from yuxi.agents.toolkits.service import get_tool_metadata
from yuxi.repositories import tool_display_repository


def validate_display_name(name):
    """显示名为最多80字符的单行纯文本，空值用于恢复默认。"""
    if not isinstance(name, str) or len(name) > 80 or any(not c.isprintable() or c in "<>" for c in name):
        raise HTTPException(422, "显示名称须为最多80字符的单行纯文本")
    return name.strip()


async def list_display_tools(category=None):
    """复制展示行，始终保持注册元数据及稳定 slug 原值。"""
    return await display_tools(get_tool_metadata(category))


async def display_tools(rows):
    """统一投影工具显示名，保留注册表及调用标识。"""
    names = await tool_display_repository.read_names()
    return [{**tool, "name": names.get(tool["slug"], tool["name"])} for tool in rows]


async def set_display_name(slug, name, actor):
    """只允许修改当前存在工具的显示名称。"""
    name = validate_display_name(name)
    if not any(tool["slug"] == slug for tool in get_tool_metadata()):
        raise HTTPException(404, "工具不存在")
    await tool_display_repository.save_name(slug, name, actor)
