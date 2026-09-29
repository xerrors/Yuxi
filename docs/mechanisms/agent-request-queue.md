# Agent 请求队列

一次 Agent 运行可能包含多次模型调用、知识库检索、工具执行和文件操作。为了避免同一对话同时修改同一份上下文，Yuxi 把“收到请求”和“开始运行”分成两个阶段，并为每个线程维护 FIFO 队列。

本页说明调度行为和可观察状态；接口字段以 `/docs` 的 OpenAPI 为准。

## 调度范围

队列按用户、智能体和对话线程确定范围。同一范围最多运行一个普通 AgentRun；同一用户在不同线程中提交的任务可以并行。

```text
线程 A：请求 1（运行中） → 请求 2（排队） → 请求 3（排队）
线程 B：请求 4（运行中） → 请求 5（排队）
```

顺序由服务端保存的创建顺序决定，不使用浏览器时间。只有当前线程的队头请求可以被派发。

## Request 和 Run

- **Request** 表示输入已被系统接收。它先保存到 PostgreSQL，可以处于排队、已派发、已取消、已拒绝或派发前失败。
- **AgentRun** 表示请求已经进入执行链路。只有请求获得派发机会后，系统才创建对应 Run。

这种拆分让排队请求可以单独查询和取消，也让刷新页面或重启服务后仍能恢复队列。排队中的用户消息不会提前加入当前 Run 的上下文；请求派发后才成为下一轮运行的输入。审批或用户回答产生的 `resume` 是例外：它从 LangGraph checkpoint 直接创建新的 Run，不经过普通消息 Request 队列。

## 普通调度流程

1. API 在 PostgreSQL 中保存输入消息和 Request。
2. 线程空闲且请求是队头时，创建 AgentRun。
3. 数据库事务提交后，API 才把 Run 投递给 Redis/ARQ。
4. Worker 执行 Run。成功结束后，检查同一线程的队头。
5. 队头存在时，自动创建并投递下一条 Run。

同一个 `request_id` 重试会返回已有 Request/Run，不会重复排队。不同用户、智能体、线程或来源复用该 ID 时返回冲突。

## 队列策略

| 策略 | 线程空闲 | 线程忙碌 | 使用场景 |
| --- | --- | --- | --- |
| `enqueue` | 立即派发 | 保存并按 FIFO 等待 | 网页聊天、异步 Agent Call |
| `reject` | 立即派发 | 记录拒绝，不进入队列 | 需要立即得到结果的同步调用 |
| `steer` | 立即派发 | 保存为待接替请求 | 主会话 Chat/Channel 修正后续方向 |

### `enqueue`

这是普通聊天的默认策略。调用方可以查询排队位置，并在派发前取消。前端把排队输入和正在生成的回复分开显示，避免用户误以为排队消息已经执行。

### `reject`

只要请求不能立即成为并派发的 FIFO 队头，`reject` 就会返回拒绝结果。线程忙碌、已有积压、队列暂停或运行正在等待人工回答时都可能触发拒绝。拒绝是正常调度结果，不是服务器内部错误。

同步 Agent Call 固定使用 `reject`，这样调用方可以自己选择重试或切换线程，而不会把排队时间隐藏在同步请求里。

### `steer`

`steer` 只适用于主会话 Chat/Channel。它把请求保存为队列中的一项；当前 Run 完成已经开始的模型调用和完整工具批次后，`SteerMiddleware` 在下一次模型调用前发现该请求并结束当前 Graph，worker 再按 completed 接力流程派发它。

因此，Steer 不强制取消正在执行的工具。一个线程同时只能有一个待处理 Steer。普通 Chat 排队项可以提升为 Steer，但等待当前 Run 到达安全点时不能取消。

系统在模型调用前和无工具调用的模型轮次结束后检查 Steer；如果进程在接力前退出，worker 启动恢复会重新扫描 queued Request。这个兜底保证持久化的 Steer 意图最终进入下一次 Run，但不改变已开始批次不可强制终止的边界。

## 状态

### Request 状态

| 状态 | 含义 |
| --- | --- |
| `queued` | 已保存，等待派发 |
| `dispatched` | 已关联 AgentRun |
| `cancelled` | 派发前被取消 |
| `rejected` | `reject` 策略无法立即派发 |
| `failed` | 派发前处理失败 |

### Run 状态

| 状态 | 含义 |
| --- | --- |
| `pending` | 数据库已记录投递意图，worker 尚未取得执行 lease |
| `running` | 当前 attempt 持有 lease 并持续 heartbeat |
| `cancel_requested` | 已记录取消意图，当前 owner 会在安全边界停止 |
| `completed` | 执行成功结束 |
| `failed` | 执行失败或 lease 过期后被收敛 |
| `cancelled` | worker 确认取消 |
| `interrupted` | 等待用户回答或工具审批，可由 resume 请求恢复 |

终态写入只接受当前 worker attempt，并清除 lease。`pending` 不表示“没有投递”，而是已经提交、仍需被 worker 接收的投递事实。

## 取消和暂停

- **取消排队请求**：只影响该 Request，不会停止当前 Run；后续排队项会重新计算位置。
- **取消运行中的 Run**：先在 PostgreSQL 保存 `cancel_requested`，Redis 信号只用于加快 worker 感知；worker 再次确认数据库状态后才写入 `cancelled`。
- **运行失败或取消**：已经排队的请求会暂停，页面显示原因。点击“继续队列”只会派发当前 FIFO 队头。
- **运行中断**：等待审批或用户回答时，已有队列保留；新普通消息会在保存 Message/Request 前返回 `run_interrupted`。完成 resume 后，队列才继续。

Worker shutdown、ARQ 超时和用户取消不是同一种结果。基础设施取消会释放 lease 并继续向上传播；临时执行故障会释放 lease 并请求 ARQ 重试，不能把失败的投递意图留成“看起来已派发”。

## 恢复和一致性

API 只有在 PostgreSQL 事务提交后才投递 ARQ。completed 接力和 worker 启动恢复会优先重新投递已有 `pending` Run，再处理新的队头，避免数据库已有 Run 却没有投递任务。

Worker 取得 Run 时写入唯一 attempt token、heartbeat 和 lease 到期时间。过期的 `running` 或 `cancel_requested` 会被收敛为带 `worker_lease_expired` 原因的 `failed`。这只能证明执行 ownership 已丢失，外部工具副作用可能已经发生，系统不会把它伪装成安全的 exactly-once 重试。

intake、resume、continue 和自动接力会在同一线程的 Conversation 行锁内读取和修改 Request/Run；数据库唯一约束提供最后一道保护。SSE 是过程通知，断线后客户端仍以同一 Request/Run 的持久状态和结果为准。

## 对话展示

排队区只显示尚未开始的输入，正文只显示已经进入 Run 的消息。正常顺序是：

```text
请求 1 → 回复 1 → 请求 2 → 回复 2
```

排队中的请求不会覆盖正在生成的回复。前端在 Request SSE 中等待派发信息，收到对应 Run 后切换到 Run SSE。

## 当前边界

当前支持：

- `enqueue`、`reject`，以及主会话 Chat/Channel 的 `steer`；
- 同一线程串行、不同线程并行；
- 查询排队位置、刷新恢复和派发前取消；
- Run 结果、事件、错误和产物绑定到同一个 Request/Run。

当前不支持强制终止正在执行的模型或工具、多个 Steer 的合并与排序、通用优先级、失败后的自动回滚，以及把多个请求合并成一次 Run。

实现入口见 [Agent 路由](https://github.com/xerrors/Yuxi/blob/main/backend/server/routers/agent_router.py)、[请求队列服务](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/services/agent_request_queue_service.py) 和[运行 worker](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/services/run_worker.py)。
