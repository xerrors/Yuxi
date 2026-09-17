# state 查询的中断恢复：由 checkpoint 原始写入保障

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/services/chat_service.py

## 问题

`get_agent_state_view` 必须返回待审批的中断，否则用户在等待工具批准 / 回答问题时
刷新页面，会丢失继续操作的入口。

中断原先从 `state.tasks[*].interrupts` 提取。而 LangGraph 的 `aget_state` 不只按 schema
读 `values`——它还依据**当前执行图的节点定义**重建 `tasks`。因此若 state 查询用一张与
原图不同的图去读同一份 checkpoint，停在审批节点上的 checkpoint 会得到空 `tasks`，
中断随之丢失，且这个差异不依赖模型或 MCP（可用真实 LangGraph + `interrupt(...)` 直接复现）。

## 决策

中断与 values 都**直接从 checkpoint 的原始写入读取**，不依赖任何图结构：

`_read_checkpoint_state` 用 checkpointer 的 `aget_tuple` 取 `CheckpointTuple`：

- `checkpoint["channel_values"]` → state 面板所需的 values（待办 / 用量 / 消息）；
- `pending_writes` 里的 `__interrupt__` channel → 待审批中断。

这样 state 查询完全不需要 agent / 图对象：既没有构图开销，也不存在「读取方的图
与写入时的原图不一致」这一类失败。

## 替代方案

- **用完整 `agent.get_graph` 读取**：能重建 `tasks`，但把 state 查询拖回 60–80s（重型
  Agent 需连接全部 MCP 装配工具）；拒绝。
- **用与原图同构的轻量图读取**：节点名由 `create_agent` 与 middleware 装配产生，
  复制即耦合其内部实现，且随上游变化静默失效；拒绝。
- **把中断塞进 `values["__interrupt__"]` 走既有兜底**：语义上污染 state values，
  且会让「中断」看起来像普通 state 字段；拒绝。

## 后果

state 读取的中断来源是「checkpoint 原始写入」，与读取方用什么图无关——`tasks` 是否
可重建不再是这条链路的依赖。`interrupted` 状态的 Run 每次查询多一次 `aget_tuple`
（单次索引读），其余状态零额外开销。

## 验证

`backend/test/integration/services/test_state_reader_interrupt_integration.py`
（真实 PostgreSQL 的 `AsyncPostgresSaver`，非 `InMemorySaver`）：

- 停在 interrupt 的真实 checkpoint，其**持久化** pending writes 里的 `__interrupt__`
  channel 可被 `_read_checkpoint_state` 恢复，`value` 与写入一致；
- 已完成、无中断的真实 checkpoint 返回 `None`。

覆盖范围说明：单元层（`InMemorySaver`）的中断恢复已由
`backend/test/unit/services/test_checkpoint_state_reader.py` 覆盖；本集成测试针对的是
**真实存储后端**下的持久化格式差异，这正是本项目现有集成测试
（`backend/test/integration/api/test_checkpoint_state_view.py` 只验快照读取与用户隔离）
未覆盖的角度。
