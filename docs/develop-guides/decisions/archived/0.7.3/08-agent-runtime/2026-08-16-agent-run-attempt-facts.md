# 持久化 RunAttempt 与失败事实

状态：archived
类型：architecture
Owner：backend/package/yuxi/repositories/agent_run_repository.py

## 问题

AgentRun、lease、heartbeat 与恢复流程能表达 Run 的最终状态，但一次 Run 在不同执行 Owner 之间发生过多少次投递、启动、失联、接管与失败，散落在日志与短期事件里，或被最终状态覆盖。恢复策略一旦调整，历史失败事实无法稳定审计；“发生过什么”与“系统下一步怎么恢复”没有被分离建模。

## 决策

新增 `agent_run_attempts` 表：每次执行占有一条不可变事实记录，包含 `run_id` 外键、Run 内递增 `attempt_no`（由 `(run_id, attempt_no)` 唯一约束保证唯一）、owner token、started/heartbeat/lease_expires/finished 时间、`outcome` 终止事实与 error 分类。AgentRun 继续保存面向业务查询的当前聚合状态；Attempt 表是执行历史与失败事实的 Owner。

生命周期由 `AgentRunRepository` 在既有 run 行锁事务内闭合：`mark_running` 的 initial claim 创建新 attempt（序号在锁内取 max+1），并先把遗留的开放 attempt 收敛为 `lease_expired`；同一 live owner 重复取得只刷新活性字段，不产生新记录。`renew_lease` 同步续租当前 attempt。`release_lease_for_retry` 以 `retry_released` 终结当前 attempt，下一次接管使用新序号。`set_terminal_status` 在真实终态转换发生时以对应 outcome（completed/failed/cancelled/interrupted）终结 owner 的开放 attempt，并携带 error 分类。`reconcile_expired_leases` 将失联 Run 的开放 attempt 收敛为 `lease_expired`。

恢复策略与 reconciler 只读取、不删除、不改写已终结的 attempt；只有仍开放（`finished_at IS NULL`）的 attempt 会被收敛。历史 Run 没有 attempt 行即 legacy/unknown，不从日志回填。本阶段只记录事实，不改变重试次数、FIFO 与恢复策略本身。

## 替代方案

- 在 AgentRun 行上累加 attempt 计数：被拒绝。只能表达次数，无法保留每次占有的时间、owner 与失败分类，且会被最终状态覆盖。
- 把投递与失败事实写入 Redis Stream 或日志：被拒绝。它们是短期介质，不是审计事实 Owner，也无法参与数据库约束。
- 让恢复策略立即依赖 attempt 历史做决策：暂不采用。先以只记录模式落地事实，策略显式消费 attempt 的事实另行演进，避免一次变更同时改变观测与行为。
- 回填历史 Run 的 attempt：被拒绝。从日志猜测 attempt 违反事实语义，缺失行本身即 legacy 标注。

## 后果

- 每次 worker 崩溃、lease 过期或接管后，旧 attempt 保留终止/失联事实，新 attempt 使用新序号；Run 的最终状态可以由 attempt 历史解释。
- `mark_running`、`renew_lease`、终态与收敛事务的写入量增加一次 attempt 读改写；仍在原 run 行锁事务内，不引入新的锁顺序。
- 并发接管由 run 行锁加 `(run_id, attempt_no)` 唯一约束双重保证只有一个 attempt 获得有效执行权。
- Attempt 目前无独立 API；回读通过 repository 查询与集成/E2E 断言完成，暴露管理视图的需求出现时再评估。

## 验证

- 集成测试（真实 PostgreSQL 行锁与唯一约束）：`backend/test/integration/services/test_agent_run_manifest_and_attempts.py` 断言 claim 创建 attempt、retry 释放后新接管使用新序号且旧 attempt 保留 retry_released 事实、终态终结 owner attempt、reconciler 将开放 attempt 收敛为 lease_expired、并发 claim 只产生一个有效 attempt。
- 既有 lease 集成测试（`test_agent_run_lease.py`）继续通过，证明 lease/恢复语义未被改变。
- E2E：deterministic agent path 执行后从数据库回读 attempt 历史，验证真实 worker 链路的执行占有事实。
