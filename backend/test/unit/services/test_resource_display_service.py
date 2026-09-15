"""展示覆盖与运行身份分离。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from yuxi.services import resource_display_service as service, mcp_display_service as mcp


@pytest.mark.asyncio
async def test_builtin_overlay_never_changes_sources_or_personal_shadow(monkeypatch):
    """相同 slug 的个人技能保留自己的名称。"""
    rows = [
        {"slug": "same", "name": "Builtin", "source_type": "builtin"},
        {"slug": "same", "name": "Personal", "source_type": "upload", "source_scope": "personal"},
    ]
    monkeypatch.setattr(service.tool_display_repository, "read_names", AsyncMock(return_value={"same": "中文"}))
    result = await service.display_skills(rows)
    assert [row["name"] for row in result] == ["中文", "Personal"]
    assert [row["name"] for row in rows] == ["Builtin", "Personal"]


@pytest.mark.asyncio
async def test_mcp_tool_names_are_server_scoped_and_callable_unchanged(monkeypatch):
    """同名工具在不同服务器保留独立覆盖。"""
    tool = SimpleNamespace(name="draw_chart", metadata={"id": "original"})
    monkeypatch.setattr(
        mcp, "lock_server", AsyncMock(return_value=SimpleNamespace(slug="one", transport="streamable_http"))
    )
    monkeypatch.setattr(mcp, "inspect_mcp_server_tools", AsyncMock(return_value=[tool]))
    values = {}

    async def save(slug, name, actor, *, key):
        values[slug] = name

    monkeypatch.setattr(mcp.tool_display_repository, "save_name", save)
    await mcp.set_mcp_tool_display_name(None, "one", "draw_chart", "图一", "actor")
    await mcp.set_mcp_tool_display_name(None, "two", "draw_chart", "图二", "actor")
    assert values == {
        service.mcp_tool_key("one", "draw_chart"): "图一",
        service.mcp_tool_key("two", "draw_chart"): "图二",
    }
    assert vars(tool) == {"name": "draw_chart", "metadata": {"id": "original"}}
    with pytest.raises(HTTPException) as exc:
        await mcp.set_mcp_tool_display_name(None, "one", "absent", "中文", "actor")
    assert exc.value.status_code == 404
    for bad in ["<b>", "x\n", "x\u200b", "x" * 81]:
        with pytest.raises(HTTPException) as exc:
            await mcp.set_mcp_tool_display_name(None, "one", "draw_chart", bad, "actor")
        assert exc.value.status_code == 422
    assert len(values) == 2
