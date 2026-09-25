# 开发智能体后端

本页面向需要在 Yuxi 中新增或维护 Agent 后端的贡献者。它只讲代码装配；配置字段、权限和运行时上下文分别见[配置智能体](./agents-config.md)和[Agent 运行时上下文](../mechanisms/agent-runtime.md)。

## 新增预置角色

仅改变提示词、模型或能力选择时，在 `backend/package/yuxi/agents/presets/` 新增一个 Python 文件并导出 `PRESET`；子智能体定义放在其 `subagents/` 子目录。发现逻辑递归读取文件，角色类型仍由 `backend_id` 决定：

```python
from yuxi.agents.presets import AgentPreset

PRESET = AgentPreset(
    slug="report-assistant",
    name="报告助手",
    description="根据材料整理报告。",
    backend_id="ChatbotAgent",
    context={
        "system_prompt": "根据用户材料生成结构清晰、来源可核对的报告。",
        "skills": ["html-preview"],
    },
)
```

子智能体使用 `backend_id="SubAgentBackend"`，其类型由后端推导。`context` 复用[智能体配置](./agents-config.md)字段；长提示词可以直接使用 Python 多行字符串。角色模块不得执行外部操作。

API 启动时按文件名发现所有非下划线开头的 Python 模块，校验定义类型、重复 slug 和后端存在性，再统一初始化数据库记录。新增角色无需修改注册清单、repository 或启动调用。模块导入失败、缺少 `PRESET`、重复 slug 或不存在的后端会阻止初始化成功。

预置角色仅在 slug 不存在时创建，已有名称、提示词和能力配置保留；修改源码不会覆盖管理员定制。默认智能助手另行维护原有默认与共享约束。重启 API 后，在智能体管理页核对新增角色的名称、后端和能力配置；子智能体还需在主智能体配置中选用。

## 后端放在哪里

随服务发布的 Agent 后端放在：

```text
backend/package/yuxi/agents/buildin/<your_agent>/
├── __init__.py
├── context.py
└── graph.py
```

执行后端由 `buildin/__init__.py` 中的显式字典注册：

```python
BUILTIN_BACKENDS = {
    "ChatbotAgent": ChatbotAgent,
    "SubAgentBackend": SubAgentBackend,
}
```

新增后端时显式导入其类并添加字典条目。字典键是持久配置与接口使用的稳定后端 ID，Python 类重命名不改变该 ID；仅增加目录或导出类不会启用后端。修改注册表时审查执行图、中间件和运行边界，并验证已有配置仍可解析。

`get_agent_backend(backend_id)` 每次创建一个轻量后端对象，`list_agent_backend_info()` 查询已注册后端的基础信息。后端不持有用户、线程或 Graph 缓存；运行状态由本次 Context、Graph 和 middleware 持有。`get_graph(context=...)` 每次构建独立执行图，知识库增删无需重载后端。未知 ID 抛出 `AgentBackendNotFoundError`；接入用例与后端配置接口将该错误转换为 HTTP 404，worker 执行边界按现有失败流程收敛。

后端的 `get_info()` 返回名称、能力与配置描述，不自行生成后端 ID。新增构图逻辑时验证两次运行分别使用各自的 Context 和资源。

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

- `BaseAgent` 子类在 `BUILTIN_BACKENDS` 中通过稳定 ID 显式注册；
- `context_schema` 的默认值、字段权限和选项能被前端正确渲染；
- Graph 使用 Yuxi 的模型、工具、文件和 checkpoint 装配入口；
- 工具副作用在执行处验证用户、路径和资源；
- 新的模型可见输入、状态、文件或协议有正向和负向测试；
- 相关 API、机制和用户文档已更新。

## 源码和测试

- [BaseAgent](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/base.py)
- [Context](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/context.py)
- [Chatbot graph](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/buildin/chatbot/graph.py)
- [执行后端显式注册](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/buildin/__init__.py)
- [Agent unit tests](https://github.com/xerrors/Yuxi/tree/main/backend/test/unit/agents)
- [Agent integration/E2E](https://github.com/xerrors/Yuxi/tree/main/backend/test/e2e)

改变持久配置、权限、模型可见输入、Run 生命周期或文件边界时，先按 [Yuxi Spec Loop](../develop-guides/spec-loop.md) 建立相应的决策和验证范围。
