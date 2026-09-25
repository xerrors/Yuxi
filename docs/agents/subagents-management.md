# 使用子智能体

子智能体是一个特殊的 Agent：它仍然是 `agents` 表中的一级智能体，只是标记为 `is_subagent=true`，并使用 `SubAgentBackend`。因此，子智能体和普通智能体共用创建、权限和配置入口。

## 在页面中配置

进入“智能体”，点击“新增智能体”，在后端类型中选择 `SubAgentBackend`。然后像配置普通智能体一样设置名称、提示词、模型、工具、知识库、MCP 和 Skills。

子智能体有三个限制：

- 不会出现在普通聊天的智能体切换列表中；
- 不能设置为默认智能体；
- 不能继续调用其他子智能体，因此不会形成孙级调用链。

主智能体在运行配置的“子智能体”字段中选择允许调用的对象：

- 未配置或保存空列表时，使用当前用户可见的全部子智能体；
- 显式选择后，只允许调用所选项；
- 每个子智能体使用自己的 `config_json.context`，不会继承主智能体的模型或工具选择；
- 用户权限变化后，新运行会重新计算可见范围。

## 调用方式

主智能体通过工具调用子智能体，不要通过 Shell、`curl` 或 HTTP API 间接调用。

### 派发任务：`subagent_start`

所有子任务通过 `subagent_start` 派发，立即返回 `run_id`、`thread_id` 和访问链接。需要结果时调用 `subagent_await(run_id)`；即使短任务也先派发再等待。多个独立任务先全部派发，再按需等待。

工具参数：

```python
{
    "description": "整理这份文档的三条要点",
    "subagent_slug": "general-purpose",
    "thread_id": null
}
```

首次调用不需要 `thread_id`。如果要继续之前的子任务，把上一次结果中的子智能体线程 ID 传回去。

### 查询、等待与取消

生命周期工具统一按 Run 工作：

| 工具 | 作用 |
| --- | --- |
| `subagent_start` | 创建并立即返回 `run_id`、`thread_id` |
| `subagent_status` | 查询运行状态和最近几条进度摘要 |
| `subagent_await` | 等待终态并取得最终结果；超时返回当前快照 |
| `subagent_cancel` | 请求取消子智能体运行 |

同一个子智能体线程同时只能有一个运行中的 Run。忙碌时工具返回 `busy`，不会隐藏地把请求排队。终态后的同一 `thread_id` 可以继续创建新的 Run。

所有生命周期工具按 `run_id` 操作，并验证该 Run 由当前父 Run 创建，不能读取或控制其他任务。

## 运行时边界

一次子智能体调用会创建独立的 child checkpoint thread 和 `agent_runs(run_type=subagent)` 记录，同时继承父运行的用户身份和根执行树：

| 资源 | 主智能体 | 子智能体 |
| --- | --- | --- |
| LangGraph checkpoint | 当前 `thread_id` | 独立 `child thread_id` |
| Sandbox runtime | 根 `runtime_scope_id` | 与根运行相同 |
| Project Workdir | 当前 Project 的 Workdir | 与根 Conversation 绑定的 Project 相同 |
| UserWorkspace | 当前用户的工作区 | 同一用户的工作区 |
| 共享/内置 Skills | 当前用户授权的只读投影 | 同一授权投影 |

父子智能体看到的是同一份 Workdir 文件字节，不会通过 checkpoint 复制或合并文件。child thread 只隔离 LangGraph 上下文；它不是文件系统隔离边界。并发写同一路径仍按真实 POSIX 文件结果处理。

子智能体可以使用自己配置的文件工具和知识库范围，但所有资源访问仍以发起用户的后端权限为最终边界。`present_artifacts`、`ask_user_question`、`install_skill` 等不适合子智能体直接使用的工具会被过滤。

## API 和数据模型

子智能体沿用普通 Agent 管理 API：

- `GET /api/agent` 默认返回聊天可用的普通 Agent；
- `GET /api/agent?include_subagents=true` 返回包含子智能体的列表；
- 创建或更新 `SubAgentBackend` 时，后端会校验 `is_subagent=true`；
- 详情、更新和删除复用同一套 Agent 权限检查。

运行时，主智能体的 `subagents` 会先收敛为当前用户可见的允许列表。列表非空时挂载子智能体 middleware；子智能体自身不会挂载这一中间件。

## 查看结果

父 state 的 `subagent_runs` 保存子任务身份，页面加载时通过数据库父子关系补齐尚未进入 checkpoint 的记录。状态面板独立订阅各子 Run 的事件流，并从 Run 接口读取当前状态；父 graph 在 `subagent_await` 中等待时，先完成的子任务立即更新。切换会话会关闭订阅，重新打开时回读状态；完成后从持久化消息读取最终结果。Redis 原始事件只供运行基础设施和前端订阅，不作为主智能体的工具结果。

实现入口见 [子智能体 middleware](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/middlewares/subagent_task.py)、[SubAgentBackend](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/buildin/subagent/graph.py) 和 [AgentRun 服务](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/services/agent_run_service.py)。

页面最多同时订阅三个子 Run，其余活跃子 Run 每两秒查询状态，避免 HTTP/1.1 的同源连接被长连接占满。连接无事件时每十五秒核对持久状态；断线或查询失败显示重连提示。

历史 `task` 消息仍可查看，新模型不再获得该工具。升级前完成或取消旧版本的活跃 Run，并在智能体管理中将已保存的深度研究 Agent 及自定义提示词中的 `task` 指令改为先 `subagent_start`、再 `subagent_await`；已有配置不会被新的默认提示词覆盖。历史 checkpoint 中尚未执行的 `task` 调用会明确返回未知工具错误，不会被自动重放为新的子任务。父 Run 终态仍按原有策略取消活跃后代，派发与等待分离不改变此边界。
