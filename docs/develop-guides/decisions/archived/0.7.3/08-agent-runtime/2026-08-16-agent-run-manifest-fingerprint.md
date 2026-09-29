# AgentRun 运行清单与执行指纹

状态：archived
类型：architecture
Owner：backend/package/yuxi/services/agent_run_manifest_service.py

## 问题

Run 的结果、事件与 request/run 因果关系已有约束，但执行时实际采用的 Agent 配置、模型、工具、Skill 与关键运行参数没有一份由 Run 自身拥有、可持久化查询的事实。配置或 Skill 在运行后变化时，复盘者无法回答“这个结果究竟由哪一组输入与运行时资产产生”，也无法做失败归因与版本对比。

## 决策

AgentRun 行拥有 `manifest`（JSONB）、`manifest_fingerprint`（VARCHAR 64）与 `manifest_recorded_at` 三列。worker 在取得执行所有权、完成输入校验后、真正构造 LangGraph 执行上下文前，通过 `agent_run_manifest_service.build_run_manifest` 从数据库解析本次运行实际采用的资产并构建 manifest，经 `AgentRunRepository.record_run_manifest` 以当前 lease owner 身份 write-once 写入；随后才进入流式执行。

manifest 直接字段只包含稳定标识与非敏感摘要：agent slug/backend_id、派发时已解析的 model spec 与 tool_approval_mode（存于 input_payload）、规范化 context 中的 tools/mcps/skills 标识、Skill 的 version/content_hash、关键 limit（max_execution_steps、model_retry_times、摘要阈值组），以及完整规范化 context 的 SHA-256 `config_digest`（prompt 等内容只以摘要形式存在）。代码 revision 来自 `YUXI_CODE_REVISION` 环境变量，缺失时显式记为 `unresolved`。指纹为规范化 JSON（键排序、紧凑分隔符）的 SHA-256，字段顺序不影响结果。API key、token、用户正文与宿主机路径不进入 manifest。

写入语义：`record_run_manifest` 要求调用者是仍持有有效 lease 的当前 owner（running 状态、worker 匹配、lease 未过期），已存在指纹时幂等跳过，保证配置后续变化不改写历史 Run。manifest 固化失败时 worker 将 Run 置为 `manifest_persist_failed` 失败终态，执行不开始。历史 Run 的 manifest 保持 NULL 表示 unknown，读取方不从当前配置反推。

## 替代方案

- 在 Run 创建（派发）时生成 manifest：被拒绝。派发时的配置可能在排队期间变化，记录的是“计划配置”而非“实际配置”。
- 复用 Redis 事件或日志保存运行参数：被拒绝。Redis 事件是短期投递介质，不是业务事实 Owner，会被最终状态覆盖或过期。
- 保存完整 prompt 与配置原文：被拒绝。manifest 需要可长期持久并可经 run 详情 API 暴露，敏感与大体量内容只应进入摘要。
- 每次执行 attempt 单独固化 manifest：暂不采用。当前 manifest 的语义是“本次 Run 首次执行采用的资产”，attempt 级差异由 RunAttempt 历史表达；若未来重试需要区分资产变化，再演进为 per-attempt 清单。

## 后果

- 审计、失败归因与版本对比可以直接从数据库回读每个 Run 的执行资产指纹，不依赖日志。
- worker 主链路新增一个必须成功的持久化步骤；该步骤失败会让 Run 显式失败，未知配置不会进入执行阶段。
- Agent 配置、Skill 版本变化只影响之后执行的 Run；已固化 Run 的指纹稳定。
- `AgentRun.to_dict` 暴露 manifest 与指纹，读取面沿既有 run 查询权限边界，未新增独立管理接口。

## 验证

- 单元测试：`backend/test/unit/services/test_agent_run_manifest_service.py` 断言同一资产生成同一指纹、字段顺序不影响指纹、prompt/密钥形态的值不进入 manifest 直接字段、缺省 revision 记为 unresolved。
- 集成测试（真实 PostgreSQL）：`backend/test/integration/services/test_agent_run_manifest_and_attempts.py` 断言 owner 固化成功、非 owner 与过期 lease 不能写入、已固化 manifest 不随再次写入变化（恢复“从当前配置反推历史 Run”缺陷的负向案例）。
- E2E：deterministic agent path 执行后从数据库回读 manifest 与指纹，验证真实 worker 链路固化事实。
