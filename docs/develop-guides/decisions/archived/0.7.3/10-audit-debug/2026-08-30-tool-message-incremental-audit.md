# ToolMessage 增量审计与兼容投影

状态：archived
类型：feature
Owner：backend/package/yuxi/repositories/tool_message_audit_repository.py

## 问题

只从终态 AIMessage 投影 ToolCall 无法表达工具运行期间的 effective input、严格执行顺序、真实时间、耗时和失败状态。ToolCall 同时承载模型声明意图与工具执行结果也会形成可独立漂移的事实源，调试面板因此不能提供完整的 Model/Tool 时间线。

## 决策

shipping graph 已注册的 LangGraph v3 `tools` stream 是 Tool lifecycle 来源。当前 worker lease owner 在 `tool-started` 时以独立短事务创建 `role=tool`、`message_type=tool_audit` 的 running ToolMessage；`tool-finished` 或 `tool-error` 以另一短事务更新同一 Run、同一 `tool_call_id` 的行。ToolMessage 保存 effective input、原始 output 或 error、根 StreamMux sequence、本地观察时间和同进程 monotonic duration，不逐 delta 写 PostgreSQL。裸 `tool-error` 可能由 LangGraph interrupt 产生，因此先保存错误观察并等待 Run 终态裁决：failed/cancelled/lease loss 关闭执行，interrupted 保留 pending ToolCall 供 resume；ToolMessage 自带 `status=error` 的受控失败仍立即记为 failed。

ToolCall 在工具尚未开始时保留 Model 声明的 pending 调用意图，以维持审批和中断展示。Tool start 必须按 `tool_call_id` 找到当前 Run 或经过 conversation 校验的 resume 祖先 Run 中的声明 Model；找不到时 fail-closed。工具开始后的 effective input、输出、错误和状态只由 ToolMessage 单向覆盖 ToolCall。终态 State 从完整 checkpoint 中按当前 Run 已持久化的 operation 集合选择同一来源键最后一条 ToolMessage，但只补全仍为 running、带 `awaiting_run_terminal` 的裸 tool-error；已经由 lifecycle 关闭的审计不再由可能经过大结果卸载的 State 二次提交。repository 对同一 lifecycle terminal 的幂等重放忽略 LangGraph 后补的 ToolMessage `id/name`，但比较其余原始 output。该边界由 [Tool 审计与卸载后 State 的终态对账](./2026-08-30-tool-audit-offloaded-state-reconcile.md) 修正并验证。

Run 失败、取消、中断、completed 时遗留的未关闭 Tool 或 lease 过期由 AgentRun owning transaction 与 Model audit 一起收敛，并同步关闭 ToolCall 兼容状态。普通 History、Memory、Dashboard 消息口径和 Conversation count 排除 `tool_audit`；ToolCall 统计继续读取单向兼容投影。

超级管理员通过唯一的线程级 `/api/chat/thread/{thread_id}/audits` 接口读取自身线程最新 500 条 Model/Tool DTO，响应以 `truncated` 明示截断。调试面板按 Run 与 sequence 展示 Tool 状态、effective input、输出或错误、起止时间和后端 duration，并沿用 Message ID 或 `(run_id, role, operation_id)` 合并规则。不保留无独立 consumer 的 Model-only `/model-audits`，接口收敛理由见[线程 Message 审计读接口收敛](./2026-09-03-unify-message-audit-read-api.md)。从 0.7.2 发布版升级时一次补齐 Model/Tool 共用字段。Langfuse observation ID 与更深恢复加固属于后续阶段。

## 替代方案

- 继续只保存 AIMessage.ToolCall：无法查询工具运行状态和严格顺序，也不能区分模型声明参数与实际 effective input。
- 从终态 State 重建 ToolMessage：State 不拥有 ProtocolEvent sequence 和 monotonic duration，会制造推算时间。
- 逐个工具 output delta 写 PostgreSQL：扩大主链路写放大，没有当前查询 consumer。
- 同时接入 Langfuse observation：把本地审计与可选远端观测耦合，扩大失败面。
- 为 Tool 审计增加独立表或后续 schema 版本：v4 的 Message 字段已经覆盖当前数据契约，新增迁移和约束没有必要。

## 后果

- PostgreSQL 可以独立回答一次 Run 的 Tool 实际输入、输出或错误、严格顺序、状态和耗时，并与 Model 调用组成完整时间线。
- Tool start/terminal 各增加一次可等待的短事务；delta 和模型流期间不持有数据库连接。
- 原始 Tool output 保存在 Message metadata，ToolCall 兼容投影只接收 output `content`，不把 envelope 其他字段带入普通 History、Memory 或 Dashboard；调试接口继续限制为超级管理员自己的线程。
- ToolCall 仍是现有 History、Memory、Dashboard 和工具 UI 的兼容读模型，不再拥有工具开始后的执行事实。
- 缺少来源 Model、错误 lease、跨 conversation resume ancestry 或不同 terminal 结果会显式失败，避免产生不可见或错绑投影。
- 没有同进程 monotonic 起点的恢复关闭行保持 `duration_ms` 为空，前端显示耗时不可用。

## 验证

- 全量 backend unit 覆盖重复 start 保留 monotonic 起点、完整 checkpoint 复用 tool_call_id、pending 调用忽略历史结果、SubAgent namespace 拒绝和多级 resume ancestry。
- 真实 PostgreSQL + HTTP integration 覆盖 lease fencing、幂等 start/terminal、原始 output 比较、缺失 Model fail-closed、interrupt 后同 ID resume、失败/取消/lease expiry 关闭、权限和普通 History 隔离。
- deterministic API→worker→SSE→PostgreSQL E2E：正常 Tool、ToolNode error 和取消 3 个场景通过；正常场景回读两次 Model 与一次 Tool 的严格 sequence、effective input、output、duration、ToolCall 投影和审计 API DTO。
- Web lint、全量 unit 与 production build 通过；调试面板 unit 覆盖 Tool 行的状态、sequence、时间、duration 和输出。
- 工程信任检查及其 61 个 unit、目标 Ruff/format、`git diff --check` 通过；隔离 VitePress build 通过，保留现有 VitePress/Rolldown 兼容警告。
