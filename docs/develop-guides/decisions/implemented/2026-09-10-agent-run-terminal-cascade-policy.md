# AgentRun 终态级联与 runtime cleanup 策略：仅取消类终态收敛 execution tree

状态：implemented
类型：architecture
Owner：backend/package/yuxi/services/run_worker.py

## 问题

主 Run 因模型/网络错误以 `failed` 结束时，会级联取消所有仍在执行的子 Run，前序调研成果作废；断网恢复后主 Run 从 checkpoint 续跑时已无子 Run 可收割。

根因是「终态 -> 收敛 execution tree」这条策略分散在三处，且都按 `TERMINAL_RUN_STATUSES` 一刀切：

1. `mark_run_terminal` 的 `cancel_active_execution_tree_descendants`；
2. `execute_agent_run` 的 `finally`：对任何终态调用 `_finish_execution_tree_children`；
3. `process_agent_run` 的「已终态跳过」路径：同样无条件收敛。

只在第 1 处加开关不成立：`_finish_run` 之后的 `_release_runtime_before_terminal_event` 会因 execution tree 未收敛（子 Run 仍活跃、占用同一 runtime scope）抛 `RuntimeCleanupPendingError`，异常冒泡后 `finally` 仍会取消子 Run 并抛出重试。承诺的「错误后子 Run 继续执行」在真实 worker 路径上不生效。

## 决策

把「收敛 execution tree」的触发条件统一为**取消类终态**：`CASCADE_CANCEL_STATUSES = {cancelled, cancel_requested, interrupted}`。

- `failed` / `completed` 不取消后代：子 Run 继续执行并落库，主 Run 续跑时可收割。
- 非取消终态在 execution tree 未收敛时**不强求** runtime cleanup：保持 `runtime_cleanup_pending=True`，由 `reconcile_pending_runtime_cleanups` 在子 Run 收敛后完成清理；cleanup 自身抛错（provisioner 故障）仍抛 `RuntimeCleanupPendingError` 让 ARQ 重试。
- 三处调用点（`mark_run_terminal` 决策、`finally`、`process_agent_run` 终态跳过，以及 `execute_agent_run` 的 `terminal_committed` 完成分支）共用同一常量，避免任何一处漏改重新打开该缺陷。
- `chat_service.save_messages_from_langgraph_state` 的完成/中断终态落库路径同样遵循该策略：`completed` 不级联取消子 Run，`interrupted` 才收敛 execution tree。此处终态仅 `completed`/`interrupted` 两种，且 `chat_service` 被 `run_worker` 反向 import（循环依赖），故内联判断 `terminal_status == "interrupted"` 而非引用 `run_worker.CASCADE_CANCEL_STATUSES`，语义与该常量对齐。

## 替代方案

- 只在 `mark_run_terminal` 加开关：`finally` 与终态跳过路径仍会取消子 Run，承诺不成立；拒绝。
- 保留级联，改为「子 Run 被取消后可恢复」：需要给子 Run 增加恢复语义与产物重建，成本远高于本改动，且与「取消即终态」的现有契约冲突；拒绝。
- 引入执行树级别的调度器决定收敛时机：当前只有三处调用点，抽象没有第二个 consumer；拒绝。

## 后果

子 Run 的生命周期不再由主 Run 的失败决定，只由用户取消或自身终态决定；主 Run `failed` 期间 runtime（沙盒）被子 Run 继续占用，清理推迟到子 Run 收敛后由 reconcile 完成，终态事件也可能在 cleanup 之后补发。

`mark_run_terminal` 的机制层参数 `cascade_cancel_descendants` 默认仍为 `True`（保持机制中立的显式语义），策略决策在 `_finish_run`。

## 验证

- `test/unit/services/test_run_worker.py`：非取消终态延迟 cleanup、取消类终态仍 fail-closed、cleanup 自身故障仍重试、终态跳过路径 failed 不取消 / cancelled 取消、子 Run 增量事件挂父线程，共 6 个新用例（66 passed）。
- `test/integration/services/test_agent_run_lease.py`（真实 PostgreSQL）：`test_root_failed_without_cascade_keeps_live_child_running` 回读确认父 Run `failed` 后子 Run 仍为 `running`、lease 未被剥夺、取消信号为空；既有 `test_root_terminal_atomically_cancels_live_child_and_clears_lease` 保持通过。
