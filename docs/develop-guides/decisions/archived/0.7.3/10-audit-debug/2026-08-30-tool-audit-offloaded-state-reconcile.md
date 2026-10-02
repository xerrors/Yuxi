# Tool 审计与卸载后 State 的终态对账

状态：archived
类型：bug-fix
Owner：backend/package/yuxi/services/chat_service.py

## 问题

Tool lifecycle 流在 `tool-finished` 时已经把原始 output 持久化为已关闭 Tool 审计。DeepAgents FilesystemMiddleware 会在结果超过阈值时把完整内容写入 `outputs/large_tool_results`，并将 LangGraph State 中的 ToolMessage content 替换为路径和预览。Run 结束时若把这份派生 State 再次提交给已关闭审计，repository 会把合法的表示变化判为不同 terminal 结果，导致最终 assistant 输出事务回滚并将 Run 标记为 `output_persistence_error`。resume Run 与普通 Run 都经过同一终态保存路径。

## 决策

Shipping `tools` lifecycle 是已开始 Tool 执行事实的 Owner。`tool-finished` 或受控失败已经关闭审计后，终态 State 不再二次提交或覆盖该事实；repository 继续拒绝 lifecycle 自身对同一已关闭 operation 提交不同结果。

终态 State 只处理流中出现裸 `tool-error`、审计仍为 running 且带 `awaiting_run_terminal` 的 operation，并且只接受同一 `tool_call_id` 的最后一条 error ToolMessage 来补全内容。大结果卸载阈值、文件格式、ToolCall 单向投影和普通 History 契约保持不变。本记录聚焦修正 [ToolMessage 增量审计与兼容投影](./2026-08-30-tool-message-incremental-audit.md) 的终态 State 边界。

## 替代方案

- 识别 State content 中的 DeepAgents 卸载提示并视为等价：依赖第三方英文文案，普通工具也可能返回相同文本，不能形成稳定信任边界。
- 从卸载路径读取文件并与原始 output 比较：在最终数据库事务中引入 filesystem I/O、路径解析和大文件读取，扩大失败面；而原始 lifecycle output 已经是当前 Owner。
- 放宽 repository，允许任意 terminal 结果覆盖：会破坏重复 lifecycle 冲突保护，无法接受。
- 提高或关闭卸载阈值：只能隐藏特定输入，不能修复两个合法表示来源的所有权冲突。

## 后果

- FilesystemMiddleware 可以继续用路径和预览替换模型 State，而 PostgreSQL Tool 审计保留 lifecycle 观察到的原始 output。
- 最终输出事务不再因这两种合法表示不同而失败，普通 Run 与 resume Run 使用同一规则。
- 终态 State 不再为已关闭审计提供第二次内容一致性检查；该检查不具有独立性且会观察到 middleware 派生表示。重复或冲突的 lifecycle terminal 仍在 repository 短事务入口被拒绝。
- 没有 terminal lifecycle 事件但 State 成功的 operation 仍不会从 State 猜测成功；遗留 running 由 Run owning transaction 收敛，保持 fail-closed。

## 验证

- 全量 backend unit 通过；负向案例证明已关闭审计不进入 State reconcile，等待 Run 裁决的最后一条 error 仍会补全。
- 真实 PostgreSQL lease integration 拒绝不同 lifecycle terminal，并保留 interrupt/resume ToolCall 语义。
- deterministic API→worker→SSE→PostgreSQL E2E 覆盖正常 Tool、受控 Tool error、replay 负控，以及审批中断→resume→大结果卸载。回读确认 resume Run 为 completed、绑定最终 output、Tool 审计保留超过卸载阈值的原始结果，且 `outputs/large_tool_results/<tool_call_id>` 文件存在。
- 目标 Ruff check/format、工程信任检查及其 61 个 unit、`git diff --check` 通过；从 tracked docs 加本次 decision overlay 的隔离 VitePress build 通过，保留现有 Rolldown/VitePress 兼容警告。
