# toolkits 包
# 触发各模块的 @tool 装饰器执行，自动注册工具
from . import buildin, debug, mysql

# 工具获取函数
from .kbs import get_common_kb_tools
from .registry import (
    ToolExtraMetadata,
    get_all_extra_metadata,
    get_all_tool_instances,
    get_extra_metadata,
    tool,
)

__all__ = [
    "get_extra_metadata",
    "get_all_extra_metadata",
    "get_all_tool_instances",
    "ToolExtraMetadata",
    "tool",
    "get_common_kb_tools",
    # 触发各模块的 @tool 装饰器执行，自动注册工具
    "buildin",
    "debug",
    "mysql",
]
