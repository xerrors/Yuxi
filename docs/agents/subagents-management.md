# 子智能体

Yuxi 的子智能体是 Agent-backed 形态：它仍然是 `agents` 表中的一级 Agent，只是额外带有 `is_subagent=true` 标记，并使用专用后端 `SubAgentBackend`。子智能体不再有独立的创建入口、独立表或独立管理接口。

## 用户视角

### 子智能体能解决什么问题

当任务复杂、需要分工处理时，主 Agent 可以通过 `task` 工具把一个子任务交给子智能体。例如：

- 通用型子任务：交给内置 `general-purpose` 子智能体，使用默认运行配置处理分析、整理、写作或文件处理。
- 研究型子任务：聚焦检索和资料整理。
- 评审型子任务：对草稿进行结构和质量审查。
- 领域型子任务：使用指定模型、工具、知识库或 Skills 处理特定领域问题。

### 在哪里创建和编辑

子智能体与普通 Agent 使用同一个管理入口：进入模型配置中的“智能体”管理页，点击新增智能体，并在后端类型中选择 `SubAgentBackend`。

创建和编辑流程与普通 Agent 保持一致：

- 展示信息、共享权限、系统提示词和运行配置都保存在同一份 Agent 配置中。
- 模型、工具、知识库、MCP 和 Skills 仍通过 Agent runtime config 表单配置。
- 子智能体不会出现在聊天页的 Agent 快速切换列表中。
- 子智能体不能再配置或调用其他子智能体。

### 如何让主 Agent 调用子智能体

主 Agent 会通过 runtime config 的“子智能体”字段确定 `task` 工具可调用的子智能体范围。

`subagents` 字段表示当前主 Agent 的允许列表：

- 未选择或保存空列表时，默认启用当前用户可见的全部子智能体，包括内置 `general-purpose`。
- 显式选择后，只允许调用所选子智能体。
- 只会调用当前用户可访问且 `is_subagent=true` 的 Agent。
- 每个子智能体使用自己的 `config_json.context`，包括模型、工具、知识库、MCP、Skills 和系统提示词。

内置 `general-purpose` 的 `config_json.context` 为空，运行时会按 `SubAgentContext` 和 `BaseContext` 默认值解析模型、工具、知识库、MCP 与 Skills。

## 开发者视角

### 数据模型

子智能体复用 `agents` 表，核心字段包括：

| 字段 | 说明 |
|------|------|
| `backend_id` | 子智能体固定使用 `SubAgentBackend` |
| `is_subagent` | 子智能体标记，`SubAgentBackend` 必须对应 `true` |
| `config_json.context` | 子智能体自己的运行配置 |
| `share_config` | 可见性与管理权限，沿用 Agent 共享模型 |

后端会校验 `backend_id` 与 `is_subagent` 一致：普通 Agent 不能伪装成子智能体，`SubAgentBackend` 也不能以普通 Agent 形态保存。子智能体不能被设置为默认 Agent。

### API 与列表语义

子智能体沿用 `/api/agent` CRUD：

- `GET /api/agent` 默认只返回聊天可用的普通 Agent。
- `GET /api/agent?include_subagents=true` 返回管理页需要的完整 Agent 列表。
- 创建或更新 `SubAgentBackend` 时，payload 会携带或推导 `is_subagent=true`。
- 详情、更新和删除仍走同一套 Agent 管理接口，并复用现有权限过滤。

旧的独立 SubAgent 管理链路已经移除，不再维护单独的启停状态、内置初始化或 spec 缓存。

### 运行时调用链

主 Agent 构图时，会先把 `context.subagents` 归一化为当前用户可见的允许列表；允许列表非空时挂载 Yuxi 的 task middleware。middleware 会把允许的子智能体列表注入模型提示，并暴露一个 `task` 工具。

工具参数为：

```python
class TaskToolSchema(BaseModel):
    description: str
    subagent_slug: str
    thread_id: str | None = None
```

`thread_id` 是可选的子智能体线程 ID。新任务不需要填写；如果要继续之前同一个子智能体任务，应使用上一次 `task` 工具结果中的 `子智能体线程 ID`。

执行时的关键流程：

1. 从父 Agent 的 `context.subagents` 读取允许的子智能体 slug；未显式配置或空列表会展开为当前用户可见的全部子智能体。
2. 使用 `AgentRepository` 加载当前用户可见且 `is_subagent=true` 的 Agent。
3. 新任务会为本次调用生成 child checkpoint thread id，例如 `<parent_thread_id>_sub_<slug>_<uuid8>`；续跑任务会校验并复用传入的 `thread_id`。
4. 使用子智能体自己的 `SubAgentContext` 和 `config_json.context` 构建真实 Agent graph。
5. 调用结束后，把子智能体线程 ID 和最终 assistant 文本作为 `task` 工具结果返回给主 Agent。

`SubAgentBackend` 复用普通 Agent 的运行时资源归一化流程，但不会挂载 task middleware；它的 `subagents` 字段隐藏且默认为空，因此不会形成嵌套子智能体调用。

### 同步调用与异步调用

`task` 是同步工具：父智能体调用后会阻塞等待子智能体 run 走到终态，再拿到最终 assistant 文本。这种模式适合短任务，例如父智能体必须立即依赖子智能体结果继续推理时。

但当子任务耗时较长或可以并行多个时，同步等待会让父智能体长时间停在工具调用上，无法继续工作。因此 middleware 还同时暴露一组异步子智能体生命周期工具：

| 工具 | 作用 | 关键参数 |
|------|------|----------|
| `subagent_start` | 异步启动子智能体 run，立即返回 `run_id` 和 `thread_id` | `description`、`subagent_slug`、可选 `thread_id` |
| `subagent_status` | 按 `run_id` 查询状态，附带最近 3 条可读进度摘要；run 终态时返回最终结果 | `run_id` |
| `subagent_cancel` | 取消运行中的子智能体 run | `run_id` |
| `subagent_await` | 阻塞等待子智能体 run 终态并返回最终结果；超时返回当前快照和 `wait_timed_out` 标志 | `run_id` |

调用约束：

- 长任务或多个可并行任务优先使用 `subagent_start`，让父智能体继续推进主流程；短任务需要立即拿到结果时继续使用 `task`。
- `thread_id` 是子智能体的长期上下文 ID，同一个 `thread_id` 终态后可以再创建新的 run 续跑。若同线程已有运行中的 run，`subagent_start` 会返回 busy 结构，不会隐藏排队。
- `subagent_status`、`subagent_cancel`、`subagent_await` 都按 `run_id` 操作，并校验该 run 是否归属当前父 run 创建的子智能体，避免越权访问其它子任务。
- Redis 原始事件流只供运行基础设施和前端 SSE 订阅使用，不作为模型工具结果返回；父智能体通过 `subagent_status` 获取轻量进度，通过 `subagent_await` 获取最终结果。
- 父智能体不应通过 shell、curl 或 HTTP API 间接调用子智能体，所有调用必须走上述工具。

异步子智能体在状态面板的「子智能体」分组中按 `run_id` 展示运行身份；状态查询工具不会渲染成独立 Agent 卡片，弹窗会随子智能体条目补齐 `run_id` 后订阅对应 SSE，已完成的子智能体改为直接读取持久化 Message 历史。

### 文件系统与沙盒作用域

子智能体与主 Agent 共享文件系统时使用拆分作用域：

| 路径/作用域 | 普通 Agent | 子智能体 |
|------|------|------|
| LangGraph checkpoint | 当前 `thread_id` | child `thread_id` |
| `/home/gem/user-data/workspace` | 当前 `uid` 的共享工作区 | 同一 `uid` 的共享工作区 |
| `/home/gem/user-data/uploads` | 当前会话文件作用域 | 父会话 `file_thread_id` |
| `/home/gem/user-data/outputs` | 当前会话文件作用域 | 父会话 `file_thread_id` |
| `/home/gem/skills` | 当前 Agent 的 Skills 作用域 | 子智能体自己的 `skills_thread_id` |

这保证子智能体可以读取父会话上传、产物也会回到父会话 artifacts 中，同时子智能体的 Skills 不会污染主 Agent。
