# Model/AIMessage 增量审计闭环

状态：archived
类型：feature
Owner：backend/package/yuxi/storage/postgres/models_business.py

## 问题

AgentRun 已能在模型执行前持久化 Langfuse Trace，LangGraph v3 stream 也保留了根 StreamMux 的 `seq` 和进程观察时间；但 PostgreSQL 仍只在 Run 终态从 State 一次性投影 AIMessage。执行中的多次 Model 调用、失败或取消时尚未进入最终 State 的调用无法作为独立业务事实查询。

阶段二只闭合 Model/AIMessage 审计，不同时引入 ToolMessage。阶段结束时，即使不实施后续阶段，PostgreSQL 也必须能可靠回答同一 Run 中每次可见 Model 调用的顺序、状态、内容和单次可靠 usage，并且普通对话语义不变。

## 决策

### 数据契约

Message 增加可空 `operation_id`、`started_at`、`finished_at`、`duration_ms`、`sequence`、`execution_status` 和 `usage`。同一 Run、role 内非空 `operation_id` 建立唯一约束，允许独立命名空间的 Model 与 Tool 使用相同 ID。历史 Message 的新增字段保持为空，不进行推测性回填。

LangGraph v3 `message-start.id` 是 Model 审计的首选稳定来源键。当前锁定协议的真实 replay 已确认生命周期形状为：

```text
message-start(id, role=ai, metadata)
content-block-start/delta/finish
message-finish(usage, metadata)
```

若 Provider 没有提供 message id，则使用同一 lifecycle metadata 中的 LangChain model run id作为运行期来源键；终态 State 无法证明同一来源时不得按内容或相邻位置猜测关联。

增量行使用 `message_type=model_audit` 与普通会话输出隔离。只有最终 State 能按稳定 operation ID 证明的最后一条 AIMessage，才在现有 lease-fenced 终态事务中转为普通输出并绑定 `AgentRun.output_message_id`。运行中的审计行不进入普通历史；终态 State 对账会写入显式 `state_reconciled` 证明，只有同时属于终态 Run、已有该证明且承载 ToolCall 的中间行，才继续作为刷新后工具展示的兼容载体。该兼容行不进入普通消息计数和 Dashboard，Memory 也只在显式读取工具时包含它；普通 History 对 operation metadata 使用公开字段 allowlist。

### 生命周期与事务

`message-start` 通过独立、可等待的短事务幂等创建 `running` AIMessage；重复 start 保留同进程已经聚合的内容和 monotonic 起点，来源键相同但 sequence 或 operation ID 冲突时显式失败。`message-finish` 更新同一行的内容、usage、结束时间、monotonic duration 和 `completed`。不持久化 token delta。

每次写入都必须锁定并校验 AgentRun 的 run/request/thread、当前 worker lease 和非终态状态。commit 后立即归还 session。失效 lease、归属冲突、来源键冲突或关键写入持续失败时显式终止当前 Run，不继续启动后续 Model 步骤；禁止 fire-and-forget 写入。

同一进程内 duration 使用 `time.monotonic()` 的 start/finish 差值。`started_at/finished_at` 来自 ProtocolEvent 的 wall-clock 毫秒，只用于展示；`sequence` 拥有严格排序。重试只有在拥有同一 Run 当前 lease 时才能重放相同 operation，且不得覆盖已完成事实为 running。

### 终态 reconcile

现有终态 State 投影改为先按当前 Run 的 `operation_id` 查找增量 AIMessage，再补全内容和 metadata；不能重复插入。State 可以补写完全遗漏但具有稳定 message id 的完成 AIMessage，但不得覆盖 stream 已保存的 `started_at`、`sequence` 和 monotonic duration。

AgentRun 进入 terminal status 时，在同一 owning transaction 内关闭残留 `running` Model 行：失败映射为 `failed`，取消或中断映射为 `interrupted`，成功终态仍未得到 finish/State 证明的行映射为 `abandoned`。最终 State 中最后一条属于当前 Run 的 AIMessage 只有在当前 Run 审计能按 operation ID 匹配时才绑定 `output_message_id`；interrupt 不得退回绑定同一 State 中更早的已对账 Model 行。

### 兼容边界

Dashboard/message count 和最终结果必须显式排除非最终 `model_audit`。普通历史为了保持 ToolCall 刷新语义，可以返回已经终态 State 证明且实际关联 ToolCall 的审计行；Memory 仅在调用方显式要求工具信息时采用同一兼容规则。现有 ToolCall、ToolMessage State 投影、Run usage 聚合和 TokenUsageMiddleware 在本阶段保持原语义。

本阶段不保存 Langfuse observation ID；AgentRun trace 关联继续由阶段一拥有。Langfuse 禁用或远端失败不影响 Model 审计。

## 替代方案

- 只增加 Message 字段和 repository，后续再接 stream：没有独立业务收益，也不能形成可验收阶段，因此拒绝。
- 同时实现 Model 与 Tool：会把 ToolCall Owner 迁移、前端兼容和两类 reconcile 合入同一高风险阶段，因此拆到阶段三。
- 每个 token delta 都更新 PG：写放大和连接压力显著，且 token delta 不是本任务要求的业务事实，因此只保存 start/finish/error。
- 使用内存队列或 `asyncio.create_task()` 异步写入：崩溃和取消时缺少交付确认，不能作为审计主链路。
- 从最终 State 中按内容或位置匹配增量行：会把推测当因果关联，可能跨模型调用合并错误事实，因此只接受稳定来源键。
- 让所有增量 AIMessage 直接进入普通历史：会把 running 或无用户展示意义的中间文本暴露给会话，因此使用 audit 类型隔离；只为已有 ToolCall 展示保留终态兼容载体。

## 后果

Run 执行期间会在每次 Model start/finish 边界增加一次 PostgreSQL 短事务，但流式 token 期间不持有连接，也不逐 token 写库。关键审计持久化失败会使 Run 明确失败，这是业务审计完整性的直接后果。

阶段二完成后 PostgreSQL 能独立查询 Model 时间线，但 Tool 仍使用现有 ToolCall/终态投影；完整 Model/Tool 时间线属于阶段三。Langfuse observation 深关联、极端恢复矩阵和旧 ToolCall 清理属于阶段四。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| v3 lifecycle 形成同 Run 单行 Model 审计 | start/finish 重复插入或丢失来源顺序 | Message schema、Model audit repository | lifecycle unit、真实 PostgreSQL integration | 重放 start/finish、不同结果覆盖、跨 owner 写入 | Passed |
| 发布版升级只通过幂等迁移发布 | 提前记录版本或缺少审计列 | storage migrator、schema version 表 | 隔离 PostgreSQL schema migration integration | DDL 重复执行、未知版本 fail-closed | Passed |
| 正常、失败、取消和 lease 过期没有残留 running | Run 已终态但审计仍伪装执行中 | AgentRun terminal transaction | AgentRun lease integration、deterministic worker E2E | 模型请求首块后取消、lease 过期 | Passed |
| running/普通计数不暴露审计，刷新后 ToolCall 仍可见 | 新增 AIMessage 污染统计，或工具型中间消息整行消失 | Conversation/Dashboard repositories、output_message_id | repository unit、history HTTP 与 worker E2E 回读 | running 行、工具型中间行、同线程后续 Run | Passed |
| Run 执行中和终态后均可按 sequence 查询 Model 时间线 | 只能在最终 State 看到合并结果 | PostgreSQL Message、Model audit repository | API→worker→SSE→PostgreSQL E2E | 取消前回读 running、终态回读两次 Model | Passed |

实际证据：

- 全量 backend unit 通过；真实 PostgreSQL/HTTP integration 覆盖 lease、schema migration 和 Run 结果因果约束。
- `test_deterministic_agent_path_e2e.py` 通过真实 API→worker→SSE→PostgreSQL 证明：正常 Run 保存两次有序 Model 调用及可靠 usage，同线程后续 Run 不复制旧 operation；取消前回读 running，取消后收敛为 interrupted；history HTTP 结构化回读 ToolCall ID、名称、状态和真实输出。
- shipping storage migrator 从 0.7.2 发布版一次补齐当前 business schema；七个审计列均由 information schema 回读存在。
- Ruff check/format、工程信任检查及其 61 个 unit、`git diff --check` 通过；排除本地 ignored `docs/vibe` 后的隔离 VitePress build 通过（现有 VitePress/Rolldown 兼容警告不阻断）。

## 风险

每次可见 Model 调用增加 start/finish 两次短事务，可能扩大数据库连接和 Run 行锁竞争；实现必须证明 token delta 期间不写 PG、事务提交后立即归还 session，并在真实 worker 链路观察连接池与时序。LangGraph v3 协议仍标记 experimental，operation key、usage 和 content block 的真实形状必须由锁定版本测试固定，不能只依赖文档描述。

中间 AIMessage 与普通历史复用同一张表，查询若既不隔离 running 审计、也不保留终态 ToolCall 兼容载体，就会污染统计或让刷新后的工具消失。各查询 Owner 需要显式负向测试，最终输出仍只由 `output_message_id` 决定。Provider 缺少稳定 message id 时只能使用同一 lifecycle 的 model run id；State 无法证明最终关联时必须 fail-closed，禁止按内容、相邻位置或最后完成行猜测。
