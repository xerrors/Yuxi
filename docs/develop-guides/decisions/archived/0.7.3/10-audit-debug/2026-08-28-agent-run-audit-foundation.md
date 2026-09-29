# AgentRun 增量审计基础事实

状态：archived
类型：feature
Owner：backend/package/yuxi/storage/postgres/models_business.py

## 问题

Langfuse trace ID 只随最终 assistant Message 保存时，没有最终输出的失败、取消或中断 Run 无法直接关联 Trace。LangGraph v3 stream 转换丢弃根 StreamMux 的 `seq` 与进程观察时间 `params.timestamp`，后续增量审计也无法使用原始顺序和展示时间。

## 决策

AgentRun 直接保存可空 `langfuse_trace_id`。Langfuse 启用时，当前 lease owner 在模型执行前用独立短事务幂等固化 request 对应的预创建 trace ID；相同 ID 可重放，不同 ID 不可覆盖。Run 结果和调试跳转优先读取 AgentRun，历史 Run 继续兼容最终输出 Message metadata。

预创建 trace ID 是 Yuxi 的唯一关联来源。Langfuse callback 只有在上下文没有预创建 ID 时才提供 fallback，不能用不同的 `last_trace_id` 改写 Run 或 Message 关联。Langfuse 禁用时字段保持为空，不访问 repository，也不阻断 Run。

`BaseAgent._stream_input_with_state()` 在 message metadata 和 Model/Tool 生命周期 stream payload 中保留 ProtocolEvent 的 `seq` 与 `params.timestamp`。这两个字段仍只属于运行流：`seq` 是根 StreamMux 顺序，`timestamp` 是 Yuxi 进程观察时间；本决定不把它们持久化为 Message，也不宣称 timestamp 是 Provider 服务端时间。

唯一 storage migrator 从 0.7.2 发布版一次幂等补齐 AgentRun trace 与 Message 审计字段，完成后才记录当前版本；API 与 worker 继续只读校验精确版本。

本决定只建立增量审计基础，不增量持久化 AIMessage/ToolMessage，不改变 ToolCall、普通历史、终态 reconcile 或模型流执行期间不访问业务 PostgreSQL 的约束。

## 替代方案

- 继续只从最终 Message 读取 trace：无需迁移，但失败或取消且没有最终消息的 Run 仍无法跳转，AgentRun 也不是关联事实 Owner。
- 根据 request ID 在查询时重算 trace：避免持久化字段，但复制 Langfuse SDK 的 ID 生成语义，无法证明运行时实际使用该 trace。
- 同时实现 AIMessage/ToolMessage 增量写入：覆盖更多目标，但会把短事务、终态 reconcile 和历史兼容合入同一高风险变更，超出本次基础步骤。
- 让 callback 的 `last_trace_id` 覆盖预创建 ID：会让 AgentRun 与最终 Message 形成两个可漂移事实，因此拒绝。

## 后果

配置 Langfuse 的 Run 在模型开始前就具有稳定 Trace 关联；模型报错、执行中取消或中断且没有最终 AIMessage 时，仍可从 AgentRun 解析跳转。历史记录保持原有 Message metadata fallback。

Trace 固化增加一次执行前 PostgreSQL 短事务，并受当前 attempt lease 约束；写入失败时模型执行不会开始。远端 Langfuse 导出、采样或 URL 解析失败不改变已经提交的本地 Run 关联和业务终态。

stream consumer 可以取得原始 `seq/timestamp`，但 PostgreSQL 仍没有 Model/Tool 运行时间线；完成该能力仍需后续独立决策和实现。

## 验证

- `backend/test/unit/services/test_chat_service_langfuse_stream.py` 证明 shipping chat stream 在模型流开始前提交 trace，并证明 Langfuse 禁用时不访问 repository。
- `backend/test/unit/services/test_langfuse_service.py` 证明预创建 trace ID 不被 callback 的不同 ID 覆盖。
- `backend/test/unit/agents/test_base_tool_event_normalize.py` 证明 message 与 Tool 生命周期 payload 保留 `seq/timestamp`。
- `backend/test/integration/services/test_agent_run_lease.py` 在真实 PostgreSQL 上证明 trace 写入幂等、受 lease fencing 保护且不同 ID 不可覆盖。
- `backend/test/integration/services/test_schema_migration_version.py` 在隔离 PostgreSQL 中证明发布版审计字段升级幂等；`backend/test/unit/services/test_storage_migration.py` 证明 DDL 成功后才记录版本。
- `backend/test/integration/api/test_agent_run_result_causality.py` 通过真实 HTTP 与 PostgreSQL 证明 Run trace 优先于输出 Message trace，并保持用户隔离和历史结果因果约束。
- `backend/test/e2e/test_deterministic_agent_path_e2e.py` 通过真实 API、worker、SSE 与 PostgreSQL 回读证明配置 Langfuse 时 AgentRun 与最终 Message 使用同一非空 trace；模型请求开始后取消且没有任何 assistant Message 的 Run 仍保留非空 trace，并由结果 API 返回同一关联。
