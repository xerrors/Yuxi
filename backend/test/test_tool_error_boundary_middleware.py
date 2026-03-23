from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.messages import ToolMessage

from yuxi.agents.middlewares.tool_error_boundary_middleware import ToolErrorBoundaryMiddleware


@pytest.mark.asyncio
async def test_tool_error_boundary_converts_exception_to_tool_message() -> None:
    middleware = ToolErrorBoundaryMiddleware()
    request = SimpleNamespace(tool_call={"id": "call-1", "name": "ls"})

    async def _handler(_request):
        raise RuntimeError("boom")

    result = await middleware.awrap_tool_call(request, _handler)

    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call-1"
    assert result.name == "ls"
    assert result.status == "error"
    assert "boom" in str(result.content)
