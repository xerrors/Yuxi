# 智能体配置

Yuxi 的智能体系统基于 LangGraph 构建。对开发者来说，最重要的不是单独理解某个页面或某个字段，而是理解三件事：

- Agent 如何被定义和发现
- Context 如何驱动配置界面
- Context 如何贯穿一次 Agent 运行周期

本文聚焦这三部分。

## 1. 整体结构

智能体开发围绕四个核心对象展开：

- **`BaseAgent`**：统一的 Agent 抽象，定义 `get_graph()`、`context_schema`、`capabilities`
- **`BaseContext`**：配置 Schema，也是前端配置项的来源
- **Graph / Middleware**：LangGraph 图与中间件链，决定运行时行为
- **Agent**：数据库中的一级智能体实例，保存展示信息、后端 `backend_id`、共享权限和 `config_json.context`

仓库中已经内置了可直接参考的智能体：

- `chatbot`：通用对话智能体，使用 `ChatBotContext` 扩展可调用子智能体配置
- `subagent`：专用子智能体后端，使用 `SubAgentContext`，用于被主 Agent 通过 task 工具调用

## 2. Agent 的代码组织

建议在 `backend/package/yuxi/agents` 下按包组织一个智能体：

```text
backend/package/yuxi/agents/
└── my_agent/
    ├── __init__.py
    ├── context.py
    └── graph.py
```

最小实现通常包含：

- 一个继承 `BaseAgent` 的主类
- 一个 `context_schema`
- 一个 `get_graph()` 实现

示例：

```python
from yuxi.agents import BaseAgent, BaseContext, load_chat_model
from langchain.agents import create_agent


class MyAgent(BaseAgent):
    name = "我的智能体"
    description = "示例智能体"
    context_schema = BaseContext

    async def get_graph(self, context=None, **kwargs):
        context = context or self.context_schema()
        graph = create_agent(
            model=load_chat_model(context.model),
            system_prompt=context.system_prompt,
            checkpointer=await self._get_checkpointer(),
        )
        return graph
```

## 3. Context 是配置模型，不只是运行时参数

### 3.1 `BaseContext` 的角色

`BaseContext` 定义在 `backend/package/yuxi/agents/context.py`，它不是一个普通的数据类，而是整个智能体配置链路的核心：

- 它定义了 Agent 可以配置哪些字段
- 它定义了这些字段在前端如何展示
- 它也是运行期传入 Graph 和中间件的上下文对象

当前基础字段包括：

| 字段 | 作用 |
| --- | --- |
| `system_prompt` | 系统提示词 |
| `model` | 主模型 |
| `tools` | 启用的内置工具 |
| `knowledges` | 关联知识库 |
| `mcps` | 启用的 MCP 服务器 |
| `skills` | 关联 Skills |
| `summary_threshold` | 摘要触发阈值 |
| `summary_prompt` | 摘要触发时使用的提示词 |
| `summary_keep_messages` | 摘要后保留的最近消息数 |
| `summary_tool_result_token_limit` | 摘要阶段工具结果预览上限 |
| `max_execution_steps` | 单次运行最大执行步数 |
| `thread_id` / `uid` | 运行期标识，不作为页面配置项暴露 |

`tools`、`knowledges`、`mcps`、`skills` 在未显式配置时会默认启用当前用户可访问的全部资源。

`ChatBotContext` 在 `BaseContext` 之上增加 `subagents` 字段，表示当前主 Agent 允许调用的子智能体。`subagents` 未显式配置或保存空列表时会默认启用当前用户可见的全部子智能体；显式选择后则作为允许列表过滤。

`SubAgentContext` 在 `BaseContext` 之上增加 `parent_thread_id`、`file_thread_id`、`skills_thread_id` 与 `is_subagent_runtime` 等隐藏运行态字段，不包含 `subagents`，因此子智能体不能继续配置下一层子智能体。

### 3.2 前端配置项如何从 Context 生成

`BaseContext.get_configurable_items()` 会遍历字段定义，把字段类型、默认值、描述、模板元数据整理成 `configurable_items`。

随后：

1. `BaseAgent.get_info()` 暴露 `configurable_items`
2. 前端读取 Agent 详情
3. `AgentRuntimeConfigForm` 按 `kind` 渲染不同控件

也就是说，`AgentRuntimeConfigForm` 不是手写每个字段，而是直接消费 `context_schema` 生成的配置描述。

这也是为什么：

- 新增一个 Context 字段，往往会直接影响侧边栏
- 字段的 `metadata` 信息会直接影响展示方式

### 3.3 配置表单与 Agent 的联动关系

这部分是最关键的。

在前端：

- `AgentRuntimeConfigForm.vue` 负责渲染配置表单
- `agentStore` 加载配置时，读取 `config_json.context`
- 如果某些字段未配置，会用 `configurable_items` 中的默认值补全
- 保存时，前端将当前表单写回 `config_json: { context: agentConfig }`

因此真实关系是：

```text
context_schema
  -> get_configurable_items()
  -> Agent detail API 返回 configurable_items
  -> AgentRuntimeConfigForm 渲染表单
  -> 用户编辑后保存到 config_json.context
```

这里需要特别注意两点：

- **侧边栏展示结构来自 `context_schema`**
- **配置实例值来自数据库中的 `config_json.context`**

前者决定“能配什么、怎么展示”，后者决定“当前配置实际选了什么”。

### 3.4 自定义 Context 的推荐方式

如果某个智能体有额外配置，不要在前端单独加一套表单，而是直接扩展 Context：

```python
from dataclasses import dataclass, field
from yuxi.agents import BaseContext


@dataclass(kw_only=True)
class MyAgentContext(BaseContext):
    custom_mode: str = field(
        default="default",
        metadata={
            "name": "运行模式",
            "description": "控制智能体的自定义行为",
            "options": ["default", "strict"],
        },
    )
```

然后在 Agent 中声明：

```python
class MyAgent(BaseAgent):
    context_schema = MyAgentContext
```

这会同时影响：

- 后端可接收的配置结构
- 前端配置侧边栏的展示内容
- 运行期 `context` 可访问的字段

## 4. Context 如何贯穿 Agent 的运行周期

Context 的价值不只在“配置页面”。它贯穿了从配置加载到实际执行的整条链路。

### 4.1 配置加载阶段

在聊天请求进入后端时，服务会先解析请求中的 `agent_id` 或线程已绑定的 Agent，再加载对应配置。

当前主流程在 `chat_service.py` 中：

1. 新线程通过 `agent_id` 查找用户可访问的 Agent
2. 已有线程通过 `thread_id` 读取 `Conversation.agent_id`，并拒绝运行中切换 Agent
3. 取出 Agent 的 `config_json.context`
4. 与 `uid`、`thread_id` 合并成运行时输入

也就是说，运行期 Context 的基础来源并不是前端临时状态，而是数据库中保存的 Agent。

此外，用户工作区会默认创建 `agents/AGENTS.md`。当 Agent 开始执行时，后端会读取当前用户工作区下的这个文件，并将其内容追加到 `system_prompt`，用于补充该用户对 Agent 的长期指令或工作区约定。该文件属于用户级共享工作区，内容会随 `uid` 和当前运行的线程作用域映射到运行时工作区路径；文件不存在、为空或不可读时不会影响 Agent 启动，单次注入内容最多读取 64 KiB，超出部分会截断并追加提示。

合并后的提示词结构可以理解为：

```text
Agent.config_json.context.system_prompt
  + 用户工作区 agents/AGENTS.md 内容
  + 运行期中间件继续追加的系统提示段
```

因此，`agents/AGENTS.md` 适合放置用户维度的稳定约束，不适合放置一次性任务要求；一次性要求仍应直接写在当前对话中。

### 4.2 Context 实例化阶段

`BaseAgent` 在运行前会创建 `context_schema()` 实例，并通过 `update_from_dict()` 注入配置值。

这一步完成后，Context 才真正成为运行期对象。

可以把它理解为：

```text
config_json.context + runtime ids -> context_schema instance
```

### 4.3 Graph 构建阶段

`get_graph(context=context)` 会收到这份 Context。

以内置 `chatbot` 为例，Context 会直接参与：

- 主模型选择：`context.model`
- 系统提示词拼接：`context.system_prompt`
- 可调用子智能体列表：`context.subagents`
- 摘要阈值：`context.summary_threshold`

因此 Graph 不是和 Context 解耦的。相反，Graph 的构造本身就依赖 Context。普通 Agent 在归一化后的 `context.subagents` 非空时会挂载 Yuxi 的 task middleware；`SubAgentBackend` 自身隐藏并清空 `subagents` 字段，因此子智能体不会继续调用子智能体。

### 4.4 Graph 构建与中间件运行阶段

`get_graph()` 创建 LangGraph 前会先调用 `prepare_agent_runtime_context`，用当前用户重新过滤资源字段，并派生运行时字段：

- `_visible_knowledge_bases`：当前会话实际可查询的知识库对象
- `_prompt_skills`：需要注入提示词的 Skill 闭包
- `_readable_skills`：`/home/gem/skills` 和沙盒可读的 Skill 闭包

随后 Graph 构建会直接使用这份 Context：

- `load_chat_model(context.model)` 选择主模型
- `build_prompt_with_context(context)` 生成系统提示词
- `resolve_configured_runtime_tools(context)` 组装已配置的内置工具和 MCP 工具
- `KnowledgeBaseMiddleware` 根据 `_visible_knowledge_bases` 暴露知识库工具
- `SkillsMiddleware` 根据 `_prompt_skills` 注入 Skill 提示段，并在 Skill 被激活后按需挂载工具与 MCP 依赖
- `save_attachments_to_fs` 将线程附件转换为运行时可读的文件提示

文件系统与沙盒接入同样读取这些运行时字段：

- 普通 Agent 默认使用当前 `thread_id` 作为文件与 Skills 作用域
- 子智能体使用 child `thread_id` 做 checkpoint，`file_thread_id` 指向父会话 uploads/outputs，`skills_thread_id` 指向子智能体自身 Skills 作用域
- 通过 `_readable_skills` 决定 `/home/gem/skills` 的可读范围

所以 Context 既是输入配置，也是 Graph 创建前整理出的运行时资源上下文。

### 4.5 文件系统与 Viewer 阶段

文件系统服务不会重新发明一套配置结构，而是再次从 `config_json.context` 还原出 runtime context，用于：

- 判断当前线程下 Agent 可见的 Skills
- 构造 Agent 视图的 composite backend
- 构造 Viewer 视图的文件系统展示

这也是为什么 Context 不只是聊天链路的一部分，它还影响：

- Agent 文件工具
- Viewer 文件浏览器
- Skills 可见性
- 沙盒挂载语义

### 4.6 恢复运行阶段

在 `resume` 流程中，系统同样会通过线程绑定的 Agent 重新构造 Context，再继续执行 Graph。

也就是说，无论是：

- 首次对话
- 中断恢复
- 文件系统查看

它们都依赖同一份 Context 配置来源。

## 5. `capabilities` 的作用

`capabilities` 用于声明前端可直接从 Agent 静态元数据判断的能力开关，控制上传入口、文件面板等固定 UI，不等同于 Context，也不适合表达运行中才会出现的状态。

示例：

```python
class MyAgent(BaseAgent):
    capabilities = ["file_upload", "files"]
```

当前常见能力包括：

| capability | 说明 |
| --- | --- |
| `file_upload` | 启用上传入口 |
| `files` | 启用文件面板 |

像 todo 这类运行态信息，不建议再放进 `capabilities`。Yuxi 当前会直接从 LangGraph state 中提取 `agent_state`，前端在创建对话后常态化展示状态入口，并在状态面板中渲染 `todos`、`files`、`artifacts`、`subagent_runs` 等运行时内容。

它解决的是“Agent 先天支持什么固定入口”，而不是“运行时当前产生了什么状态”。

## 6. 开发建议

### 6.1 新增配置时优先改 Context

如果一个配置项会影响 Agent 行为，优先考虑把它做成 `context_schema` 字段，而不是前端单独维护状态。

### 6.2 把 Graph 逻辑和配置逻辑分开

推荐做法：

- `context.py` 定义配置模型
- `graph.py` 使用这些配置构建 Graph

这样前后端联动关系会清晰很多。

### 6.3 把“配置来源”和“运行时状态”区分开

建议始终区分两层语义：

- `config_json.context`：持久化配置来源
- `runtime.context`：实际运行对象，可能被中间件继续补充或修改

## 7. 相关主题

- [工具系统](./tools-system.md)
- [中间件](./middleware.md)
- [沙盒架构与设计](./sandbox-architecture.md)
- [MCP 集成](./mcp-integration.md)
- [Skills 管理](./skills-management.md)
- [子智能体](./subagents-management.md)
