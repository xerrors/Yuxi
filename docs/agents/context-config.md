# 上下文配置

`BaseContext` 是智能体的配置基类，封装了常用的配置字段，定义了智能体的运行时行为。

## BaseContext 详解

```python
from yuxi.agents import BaseContext
from dataclasses import dataclass

@dataclass(kw_only=True)
class MyAgentContext(BaseContext):
    # 继承以下字段：
    # model: str - 使用的语言模型
    # system_prompt: str - 系统提示词
    # tools: list[str] - 启用的工具列表
    # knowledges: list[str] - 关联的知识库
    # mcps: list[str] - 启用的 MCP 服务器
    # skills: list[str] - 关联的 Skills

    # 可在此添加自定义字段
    custom_field: str = "默认值"
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| model | str | 使用的语言模型 |
| system_prompt | str | 系统提示词 |
| tools | list[str] | 启用的内置工具列表 |
| knowledges | list[str] | 关联的知识库 |
| mcps | list[str] | 启用的 MCP 服务器 |
| skills | list[str] | 关联的 Skills |

## 自定义工具选项

有时需要自定义工具选项，比如 ReporterAgent 需要包含 MySQL 工具：

```python
from yuxi.agents import BaseContext, get_tool_info
from yuxi.agents.toolkits.buildin import calculator, query_knowledge_graph
from yuxi.agents.toolkits.mysql import get_mysql_tools

@dataclass(kw_only=True)
class ReporterContext(BaseContext):
    tools: Annotated[list[dict], {"__template_metadata__": {"kind": "tools"}}] = field(
        default_factory=lambda: [t.name for t in get_mysql_tools()],
        metadata={
            "name": "工具",
            "options": lambda: get_tool_info(
                [calculator, query_knowledge_graph, _create_tavily_search()] + get_mysql_tools()
            ),
            "description": "包含内置工具和 MySQL 工具包。",
        },
    )

    def __post_init__(self):
        self.mcps = ["mcp-server-chart"]  # 默认启用图表 MCP
```
