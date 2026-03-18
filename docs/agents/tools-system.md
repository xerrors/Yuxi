# 工具系统

Yuxi 提供了统一的工具获取机制，支持多种工具类型的动态组装。

## 工具获取机制

系统提供统一的工具获取入口 `get_tools_from_context(context)`，它会自动组装三类工具：

1. **基础工具**：从 `context.tools` 筛选的内置工具
2. **知识库工具**：根据 `context.knowledges` 自动生成检索工具
3. **MCP 工具**：根据 `context.mcps` 加载并过滤的 MCP 服务器工具

```python
from yuxi.agents.common.tools import get_tools_from_context

async def get_graph(self, **kwargs):
    context = self.get_context()
    tools = await get_tools_from_context(context)
```

## 工具注册机制

Yuxi 的工具系统基于注册机制而非继承体系，这一点与 LangChain 原生的 `@tool` 装饰器有本质区别。

LangChain 的 `@tool` 装饰器通常需要继承特定基类或实现特定接口，创建的工具有着强烈的框架耦合。而 Yuxi 的工具注册表是一个独立的全局注册中心，任何符合规范的函数都可以通过 `@tool` 装饰器注册到系统中，无需继承任何基类，也不需要了解框架内部实现。

需要特别说明的是，Yuxi 的 `@tool` 装饰器并非全新实现，而是基于 LangChain 原生 `@tool` 的扩展。装饰器的核心逻辑继承自 LangChain，新增了 `category`、`tags`、`display_name` 等元数据字段用于前端展示和分类，原有的 LangChain 特性（如函数参数注解、描述文档等）完全兼容。

注册表的核心位于 `backend/package/yuxi/agents/common/toolkits/registry.py`，它维护着一个全局的工具实例列表。当系统启动时，所有导入 `toolkits` 包的模块都会自动执行其内部的工具注册逻辑，这意味着开发者只需要在自己的模块中添加装饰器，工具就会自动被发现和使用。

```python
from yuxi.agents.common.toolkits.registry import tool

@tool(category="buildin", tags=["计算"], display_name="计算器")
def calculator(a: float, b: float, operation: str) -> float:
    """计算器：对给定的2个数字进行基本数学运算"""
    if operation == "add":
        return a + b
    # ...
```

使用这个装饰器时，需要指定 `category` 和 `tags`，前者用于工具分类，后者用于前端展示。装饰器内部仍然调用 LangChain 的工具封装逻辑，因此 LangChain 工具的所有特性（如多参数支持、参数类型注解等）都保持兼容。

获取工具时，通过 `get_all_tool_instances()` 可以拿到所有已注册的工具实例列表，这个函数会被 `get_tools_from_context` 调用，根据上下文配置筛选出需要使用的工具。

## 内置工具

系统内置了几类常用工具。计算类包括 calculator，可进行加减乘除运算。搜索类包括 tavily_search，需要在环境变量中配置 `TAVILY_API_KEY` 才能启用。知识图谱类包括 query_knowledge_graph，用于查询通过三元组导入的全局知识图谱。交互类包括 ask_user_question，用于在智能体执行过程中向用户发起交互式提问，当前协议支持一次提交 `questions` 数组并返回 `{question_id: answer}` 的批量答案映射。数据库类包括 mysql_list_tables、mysql_describe_table 和 mysql_query，用于连接和查询 MySQL 数据库。

这些工具都通过上述注册机制自动加载，开发者无需手动引入。

## 知识库工具

与内置工具不同，知识库工具是动态生成的。当在智能体配置中指定 `context.knowledges` 时，系统会根据指定的 knowledge 名称动态创建对应的检索工具。这种设计使得知识库工具不需要预先注册，而是在运行时按需生成。

```python
from yuxi.agents.common.toolkits.kbs import get_common_kb_tools

kb_tools = get_common_kb_tools(knowledge_names=["kb1", "kb2"])
```

## Skills 集成

Skills 与工具是两种不同的扩展机制。工具是具体的功能实现，而 Skills 是包含提示词、工具依赖和元数据的完整技能包。通过 `context.skills` 配置 Skills 时，对应的技能文件会被挂载到 `/skills/<slug>/...`，智能体可以通过读取 SKILL.md 来了解如何使用这些技能。

关于 Skills 的详细机制，请参阅 [Skills 管理](./skills-management.md)。
