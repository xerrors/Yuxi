# Run 终态沙盒清理移出数据库事务

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/services/run_lifecycle.py

## 问题

`complete_run_runtime_cleanup` 在持 `AgentRun` 行 KEY SHARE 锁的事务内调用 `get_sandbox_provider().cleanup_run`——那是阻塞式 runner HTTP（生产现场实测单次数十秒，idle in transaction 归因均为 worker 侧、末条 SQL 停在会话查询）。期间 worker 连接空闲在事务中、Run 行锁被钉住，取消释放与 retry 领取一并排队；`settle_waiting_cancel` 更在持有 Thread/Turn 锁的事务内嵌套该调用，慢清理同时放大为整条会话串行停摆。

## 决策

沙盒销毁不持数据库事务与行锁，清理责任以持久标志与短事务确认闭合。

### 实现方案

- `complete_run_runtime_cleanup` 拆为三段：短事务读 `runtime_cleanup_pending` 责任标志并快照 Run 代次（status/worker_id/updated_at）；事务外 `await_io(asyncio.to_thread(cleanup_run, uid, run_id))` 执行销毁；再进短事务持同一 Run 锁，仅当标志仍为真且代次与快照全等时才交还责任——责任不清除时由恢复扫描按新一代标志重新收敛，旧清理者绝不越代交还。runner 的 `remove_run` 幂等且容忍 404，同代并发重复销毁安全；`finish_cancelled_run` 以确认后的状态为准，取消释放语义不变。
- `settle_waiting_cancel` 先以无锁短事务预读定位承担清理的 interrupted Run 并完成销毁，再进入持 Thread/Turn 锁的事务重做权威校验（cancelling/waitpoint/run 绑定逐条复查），迟到或重复清理由锁内校验与幂等销毁兜底。
- 调用方（runner 终态收尾、`reconcile_pending_runtime_cleanups`、取消落点）不变；`runtime_cleanup_pending` 仍是"外部清理未完成"的唯一事实，中途崩溃由 lease 过期与恢复扫描兜底重试。

## 替代方案

- 保留事务、给 `cleanup_run` 加超时：慢响应仍在锁内，超时后清理未完成但事务只能带着未清责任结束，问题从停摆变成漏清理。
- session 级 advisory 锁串行化清理者：需要新增锁工具与连接失效（解锁失败即弃用物理连接）处理，且只互斥清理者、不闭合领取交错；短事务标志加代次 CAS 已满足互斥需求，不引入第二套锁机制。
- 把清理挪到独立后台任务队列：新增状态与调度面；现有恢复扫描已覆盖"进程中途失联"的兜底重试。

## 后果

- 同一 Run 的并发清理者可能各自发起一次销毁 HTTP（幂等、404 容忍），换取不再出现数十秒级 idle in transaction 与行锁占用。
- 残余竞态（接受并记录）：销毁请求在 phase1 之后长时间迟到（线程池饥饿/网络重传）而新一代已被领取时，迟到的 DELETE 可能移除运行中新一代的沙盒容器；runner 的 exec 会按需重建同名容器，代价是该 attempt 的 tmpfs 与后台进程被重置、容器标签竞态下短暂 409 由既有维护扫描（reason=owner_changed/run_terminal）收敛。彻底闭合需要给 DELETE 协议携带 attempt 令牌，跨 runner 进程协议变更不在本次范围。
- `settle_waiting_cancel` 对预读 Run 的判断做一次无害重复读；权威判定仍在锁事务内。
- 无 Schema 变更、无配置项。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| cleanup_run 阻塞期间不持 Run 行锁、不持 idle 事务，责任标志确认仍持久生效 | 慢销毁钉住行锁，取消释放/retry 排队，连接池被占 | run_lifecycle.complete_run_runtime_cleanup | `pytest test/integration/services/test_agent_run_lease.py::test_runtime_cleanup_does_not_hold_db_transaction_during_release`（真实 PostgreSQL：stub 阻塞中 NOWAIT 取行锁成功且标志为真，放行后标志落库为假） | 恢复"事务内销毁"旧实现：NOWAIT 探针以 LockNotAvailableError 红 | Passed |
| 取消落点仍等待清理确认后结束 Turn；旧清理者不得越代清除新一代责任标志 | 取消在清理前放行，或迟到确认吞掉新 attempt 的清理责任 | run_lifecycle 与 leases/runner 既有契约 | `pytest test/integration/services/test_agent_run_lease.py test/integration/services/test_agent_stop_recovery.py` 全量回归；`test_runtime_cleanup_does_not_release_another_generations_flag`（三方交错：标志交还→新领取→再挂起，旧确认醒来只清本代） | 去掉代次 CAS（恢复只比布尔）后交错用例红 | Passed |

交叉链接：与[状态读事务边界](2026-11-17-state-view-releases-read-transaction.md)同族——长阻塞外呼不持有请求/事务级数据库资源。
