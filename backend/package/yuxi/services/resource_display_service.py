"""资源展示覆盖只装配响应，不修改源对象或运行身份。"""

import json
from yuxi.repositories import tool_display_repository

SKILL_NAMES = "builtin_skill_display_names"
MCP_NAMES = "builtin_mcp_display_names"
MCP_TOOL_NAMES = "mcp_tool_display_names"


def mcp_tool_key(slug, tool_name):
    """编码二元身份，避免分隔符与同名工具冲突。"""
    return json.dumps([slug, tool_name], ensure_ascii=False, separators=(",", ":"))


async def display_skills(rows):
    """仅覆盖内置技能卡片；个人同名覆盖继续优先。"""
    if not any(row.get("source_type") == "builtin" for row in rows):
        return rows
    names = await tool_display_repository.read_names(key=SKILL_NAMES)
    return [
        {**row, "name": names.get(row["slug"], row["name"])} if row.get("source_type") == "builtin" else row
        for row in rows
    ]


async def display_mcps(rows):
    """只覆盖内置服务器展示，连接配置保持原对象。"""
    if not any(row.get("is_builtin") for row in rows):
        return rows
    names = await tool_display_repository.read_names(key=MCP_NAMES)
    return [{**row, "name": names.get(row["slug"], row["name"])} if row.get("is_builtin") else row for row in rows]
