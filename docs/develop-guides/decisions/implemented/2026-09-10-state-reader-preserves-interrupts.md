# state 骨架图读取必须保留审批中断

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/services/chat_service.py

## 问题

为把 state 面板查询从秒级（重型 Agent 完整构图 60-80s）降到毫秒级，`_read_checkpoint_state` 改用零工具骨架图 `_get_state_reader_graph`（单 `state_reader_noop` 节点）读取 checkpoint。

但 `aget_state` 不只是按 schema 读 `values`：它还依据**当前执行图的节点定义**，用 checkpoint 的 `next` 与 `pending_writes` 重建 `tasks`。骨架图没有原图的 `tools`/中间件审批节点，停在审批中断上的 checkpoint 会得到空 `tasks`。

`_extract_interrupt_info` 首选 `state.tasks[*].interrupts`，其 `values["__interrupt__"]` 兜底在骨架图下同样为空（实测 `get_state` 返回的 values 只有常规 channel）。结果是 `get_agent_state_view` 不再返回 `interrupt`——**用户等待工具审批或提问时刷新页面，会丢失继续操作的入口**。

最小复现（真实 LangGraph + InMemorySaver，`tools` 节点内 `interrupt(...)`）：原图返回 1 个 task 且 `interrupts` 非空；骨架图返回 0 个 task。该差异不依赖模型或 MCP。

## 决策

中断不从图结构推导，而是**直接从 checkpoint 的 pending writes 读取**：新增 `_read_pending_interrupt`，用 checkpointer 的 `aget_tuple` 取 `CheckpointTuple.pending_writes`，返回 `__interrupt__` channel 中的中断值。

调用点只在 `get_agent_state_view` 且 `latest_run.status == "interrupted"` 时回退到它——普通状态查询不额外读一次 checkpoint，保留骨架图的性能收益。

骨架图继续负责 `values`（state 面板的待办/用量/消息）。两条读取路径共同覆盖「真实图 in-flight 中断」（`check_and_handle_interrupts`，走 `tasks`）与「历史/刷新后的中断」（走 pending writes）。

## 替代方案

- 回退到完整 `agent.get_graph`：恢复 `tasks` 但把 state 查询重新拖回 60-80s；拒绝。
- 骨架图伪造与原图同名的节点：原图节点名由 `create_agent` 与 middleware 装配产生，复制即耦合其内部实现，且随上游变化静默失效；拒绝。
- 把中断塞进 `values["__interrupt__"]` 走既有兜底：语义上污染 state values，且会让「中断」看起来像普通 state 字段；拒绝。

## 后果

state 读取的中断来源从「图结构」变为「checkpoint 原始写入」，与图是否为零工具骨架无关。`interrupted` 状态的 Run 每次查询多一次 `aget_tuple`（单次索引读），其余状态零额外开销。

骨架图无法重建 `tasks` 是 LangGraph 的固有行为，已由测试显式记录，避免后续误以为 `tasks` 可用。

## 验证

`backend/test/unit/services/test_state_reader_interrupt_recovery.py`（真实 LangGraph checkpoint，非 mock）：

- 记录骨架图固有限制：原图 `tasks` 为 1 且含 interrupt，骨架图 `tasks` 为空、`_extract_interrupt_info` 返回 `None`；
- 修复点：`_read_pending_interrupt` 从 checkpoint 写入恢复出中断，其 `id` 与 `value` 与真实图 `tasks[0].interrupts[0]` 完全一致；
- 骨架图 `values` 与真实图一致（确认性能优化未被破坏）；
- 负向：已完成、无中断的 checkpoint 返回 `None`；不存在的 thread 返回 `None`。
