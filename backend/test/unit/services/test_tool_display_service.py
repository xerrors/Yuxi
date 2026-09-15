"""工具显示投影与稳定身份隔离。"""

from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from yuxi.services import tool_display_service as service


@pytest.mark.asyncio
async def test_display_overlay_does_not_mutate_registry(monkeypatch):
    """覆盖可重读、可清除；slug/schema 和缓存保持原值。"""
    original = [{"slug": "run_tool", "name": "Run", "args": [{"name": "input"}]}]
    monkeypatch.setattr(service, "get_tool_metadata", lambda category=None: original)
    stored = {"run_tool": "执行工具"}
    monkeypatch.setattr(service.tool_display_repository, "read_names", AsyncMock(side_effect=lambda: dict(stored)))

    async def save(slug, name, actor):
        if name:
            stored[slug] = name
        else:
            stored.pop(slug, None)

    monkeypatch.setattr(service.tool_display_repository, "save_name", save)
    result = await service.list_display_tools()
    assert result[0]["name"] == "执行工具"
    assert result[0]["slug"] == "run_tool"
    assert result[0]["args"] == original[0]["args"]
    assert original[0]["name"] == "Run"
    await service.set_display_name("run_tool", "  新名称  ", "admin")
    assert (await service.list_display_tools())[0]["name"] == "新名称"
    await service.set_display_name("run_tool", "", "admin")
    assert (await service.list_display_tools())[0]["name"] == "Run"
    with pytest.raises(HTTPException) as exc:
        await service.set_display_name("missing", "名称", "admin")
    assert exc.value.status_code == 404


@pytest.mark.parametrize("name", ["x" * 81, "<b>名称</b>", "行\n名称", "制\t表", None])
def test_reject_non_plain_display_name(name):
    """错误输入在写入前拒绝。"""
    with pytest.raises(HTTPException) as exc:
        service.validate_display_name(name)
    assert exc.value.status_code == 422
