# 开发智能体后端

本页面向需要在 Yuxi 中新增或维护 Agent 后端的贡献者。它只讲代码装配；配置字段、权限和运行时上下文分别见[配置智能体](./agents-config.md)和[Agent 运行时上下文](../mechanisms/agent-runtime.md)。

## 后端放在哪里

随服务发布的 Agent 后端放在：

```text
backend/package/yuxi/agents/buildin/<your_agent>/
├── __init__.py
├── context.py
└── graph.py
```

`buildin` 包会遍历包含 `__init__.py` 的子目录，发现并注册其中的 `BaseAgent` 子类。`__init__.py` 需要导出该类。

## 最小实现

```python
from langchain.agents import create_agent
from yuxi.agents import BaseAgent, BaseContext, load_chat_model


class MyAgent(BaseAgent):
    name = "我的智能体"
    description = "用于示例的智能体后端"
    context_schema = BaseContext

    async def get_graph(self, *, context, **kwargs):
        if not getattr(context, "_runtime_prepared", False):
            raise ValueError("构图需要已准备的 Context")
        return create_agent(
            model=load_chat_model(fully_specified_name=context.model),
            system_prompt=context.system_prompt,
            checkpointer=await self._get_checkpointer(),
        )
```

这个示例展示最小的 Context、模型、提示词和 PostgreSQL checkpoint 装配。真实后端还要根据需要接入文件 backend、工具、Skills、审批、Summary、用量和子智能体 middleware。

worker 和主动压缩在执行入口显式调用 `prepare_agent_runtime_context`，为 Context 追加工作区提示词、按当前用户过滤资源，并在模型为空时补齐系统默认模型。`get_graph(context=...)` 只消费准备后的对象。独立调用同样先创建 `context_schema()`，用 `update_config` 装载持久配置、用 `update` 注入已授权身份和运行覆盖，再 await 准备函数；流和 invoke 接口只接受 `context`，不接收配置字典。

## Context 和配置表单

需要让管理员或用户配置 Agent 行为时，在 `context.py` 扩展 `BaseContext`：

```python
from dataclasses import dataclass, field
from yuxi.agents import BaseContext


@dataclass(kw_only=True)
class MyAgentContext(BaseContext):
    response_style: str = field(
        default="concise",
        metadata={
            "name": "回答风格",
            "description": "控制回答的详细程度",
            "type": "string",
            "options": ["concise", "detailed"],
        },
    )
```

metadata 会影响 Agent 详情接口和 `AgentRuntimeConfigForm`。不要只在前端添加一个字段，也不要把运行期 ID、worker 身份和权限快照暴露成可保存配置。

新增字段后，沿下面的链路检查：

```text
context_schema
  → get_configurable_items()
  → Agent 详情接口
  → 前端配置表单
  → config_json.context
  → get_graph(context)
```

## 中间件和工具

资源权限和默认资源选择在 Graph 创建前处理；模型提示注入、工具动态开放、文件结果处理、state 更新和观测才适合放入 middleware。内置 Agent 的工具可见性和执行注册分为两层：工具可以先进入 ToolNode，再由 Skill 激活状态决定是否让模型看到。

优先复用：

- [工具系统](./tools-system.md) 的注册和目标校验；
- [中间件](./middleware.md) 的装配顺序；
- [Skills 管理](./skills-management.md) 的依赖和激活规则；
- [沙盒机制](../mechanisms/sandbox.md) 的文件和命令边界。

新 middleware 不要绕过 `prepare_agent_runtime_context`，也不要用 Prompt、前端隐藏或 schema omission 代替后端授权。

## 检查清单

- `BaseAgent` 子类位于可被 `agent_manager` 发现的包中，并被 `__init__.py` 导出；
- `context_schema` 的默认值、字段权限和选项能被前端正确渲染；
- Graph 使用 Yuxi 的模型、工具、文件和 checkpoint 装配入口；
- 工具副作用在执行处验证用户、路径和资源；
- 新的模型可见输入、状态、文件或协议有正向和负向测试；
- 相关 API、机制和用户文档已更新。

## 源码和测试

- [BaseAgent](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/base.py)
- [Context](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/context.py)
- [Chatbot graph](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/buildin/chatbot/graph.py)
- [Agent 自动发现](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/buildin/__init__.py)
- [Agent unit tests](https://github.com/xerrors/Yuxi/tree/main/backend/test/unit/agents)
- [Agent integration/E2E](https://github.com/xerrors/Yuxi/tree/main/backend/test/e2e)

改变持久配置、权限、模型可见输入、Run 生命周期或文件边界时，先按 [Yuxi Spec Loop](../develop-guides/spec-loop.md) 建立相应的决策和验证范围。
