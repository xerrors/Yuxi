from __future__ import annotations

from yuxi.agents.toolkits.registry import tool


def test_tool_decorator_sets_handle_tool_error():
    """测试通过 @tool 装饰器注册的工具是否自动设置了 handle_tool_error 为 True"""

    @tool(
        category="test",
        display_name="测试工具",
        description="这是一个单元测试工具",
    )
    def my_test_tool(arg: str) -> str:
        return f"hello {arg}"

    assert my_test_tool.name == "my_test_tool"
    assert my_test_tool.handle_tool_error is True
