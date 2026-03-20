# 智能体开发指南

Yuxi 的智能体系统基于 LangGraph 构建，提供了灵活而强大的 Agent 开发能力。通过统一的 `AgentManager`，系统能够自动发现和管理所有智能体，让开发者能够专注于业务逻辑的实现。

## 智能体架构

### 核心概念

系统的智能体架构围绕几个核心组件展开：

- **BaseAgent**：所有智能体的基类，定义了统一的接口规范
- **AgentContext**：智能体的配置上下文，包含模型、提示词、工具等配置
- **Graph**：LangGraph 图结构，定义智能体的执行流程
- **Middleware**：中间件系统，用于扩展和定制智能体行为

### 自动发现机制

智能体采用自动发现模式。在 `backend/package/yuxi/agents/__init__.py` 中，系统会遍历 `backend/package/yuxi/agents` 目录，自动注册所有继承自 `BaseAgent` 的类。这意味着开发者只需要按照规范编写代码，智能体就会自动被系统识别，无需手动配置。

仓库预置了几个可以直接使用的智能体示例：

- **chatbot**：通用对话智能体，支持动态工具调度
- **reporter**：报表生成智能体，演示多工具协作
- **deep_agent**：深度分析智能体，支持复杂推理任务

这些示例展示了如何组织代码结构、如何定义上下文、如何组合中间件，新增智能体时可以作为参考。

## 创建自定义智能体

### 目录结构

在 `backend/package/yuxi/agents` 目录下创建新的智能体包，建议保持以下结构：

```
backend/package/yuxi/agents/
└── my_agent/
    ├── __init__.py          # 暴露主类
    ├── graph.py              # Graph 构造逻辑
    └── metadata.toml         # 元数据配置（可选）
```

### 基本实现

智能体类需要继承 `BaseAgent` 并实现异步的 `get_graph` 方法：

```python
from yuxi.agents import BaseAgent
from langgraph.prebuilt import create_agent

class MyAgent(BaseAgent):
    async def get_graph(self, **kwargs):
        # 获取配置上下文
        context = self.get_context()

        # 获取工具列表
        tools = await get_tools_from_context(context)

        # 构建 LangGraph 图
        graph = create_agent(
            model=load_chat_model(context.model),
            tools=tools,
            checkpointer=await self._get_checkpointer(),
        )

        return graph
```

### 能力配置

`capabilities` 属性用于声明智能体的前端能力，控制 UI 组件的显示：

```python
class MyAgent(BaseAgent):
    capabilities = ["file_upload", "files", "todo"]  # 支持文件上传、文件管理、待办事项
```

**可用能力：**

| capability | 说明 | 前端效果 |
|------------|------|----------|
| `file_upload` | 文件上传 | 显示上传按钮 |
| `files` | 文件管理 | 显示文件管理面板 |
| `todo` | 待办事项 | 显示待办组件 |

**示例：**

```python
# 只需要文件上传能力
capabilities = ["file_upload"]

# 需要文件上传和待办事项
capabilities = ["file_upload", "todo"]

# 全部能力
capabilities = ["file_upload", "files", "todo"]
```

注意：即使启用了能力，也需要在中间件中正确配置对应的处理逻辑，功能才能正常工作。例如启用 `file_upload` 需要配合 `inject_attachment_context` 中间件。

### 配置文件

可以通过 `metadata.toml` 定义智能体的元数据：

```toml
name = "我的智能体"
description = "这是一个示例智能体"
examples = [
    "帮我写一首诗",
    "解释一下量子计算",
]
```

这些信息会在前端界面展示，帮助用户了解每个智能体的用途。

## 相关主题

- [上下文配置](./context-config.md) - BaseContext 和自定义配置
- [工具系统](./tools-system.md) - 工具获取机制和 Skills 集成
- [中间件系统](./middleware.md) - 中间件开发与使用
- [MCP 集成](./mcp-integration.md) - MCP 服务器配置
- [SubAgents 管理](./subagents-management.md) - 子智能体配置、调用链和开发注意事项

## 开发建议

### 代码组织

- 将智能体的核心逻辑放在 `graph.py` 中
- 复杂的工具逻辑单独放在 `toolkits` 目录下
- 共享的组件放在 `common` 目录下

### 热重载

在容器环境中，修改代码后会自动触发热重载。如果需要强制刷新，可以调用：

```python
agent_manager.get_agent(<agent_id>, reload=True)
```

### 调试技巧

1. 使用前端的「调试面板」查看详细的请求和响应
2. 查看后端日志：`docker logs api-dev -f`
3. 利用 LangGraph 的可视化能力理解图结构

---

智能体系统的设计目标是让开发者能够快速构建和迭代 AI 应用。通过本文档介绍的概念和示例，你应该能够掌握创建自定义智能体的核心方法。遇到问题时，建议先参考预置智能体的实现，它们涵盖了大多数常见场景。
