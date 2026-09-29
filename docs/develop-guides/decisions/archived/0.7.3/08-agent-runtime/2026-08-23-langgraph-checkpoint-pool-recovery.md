# LangGraph checkpoint 连接池恢复失效连接

状态：archived
类型：bug-fix
Owner：backend/package/yuxi/storage/postgres/manager.py

## 问题

PostgreSQL 重启或进入 recovery 后，worker 进程中的 LangGraph checkpoint 连接池可能保留重启前建立的失效连接。后续 AgentRun 在读取 checkpoint 时收到 `psycopg.OperationalError: consuming input failed: server closed the connection unexpectedly`，对话在模型执行前失败；同一 worker 内的后续请求也可能重复失败。

## 决策

LangGraph 专属 `AsyncConnectionPool` 在 checkout 边界使用 psycopg 官方 `check_connection`。池在交付连接前执行健康检查，丢弃失效连接并创建新连接。聊天和 Agent 层不重试整次执行，避免 checkpoint 写入或工具副作用发生后重复运行。

## 替代方案

- 在 `chat_service` 捕获 `OperationalError` 并重试 Agent 流。该方案可能重复模型调用、checkpoint 写入或工具副作用，不采用。
- PostgreSQL 重启后同步重启 worker。该方案依赖人工或编排时序，不能覆盖短暂 recovery 和网络断链，不采用。
- 缩短连接最大生命周期。该方案只能降低陈旧连接概率，不能证明 checkout 时连接仍可用，不采用。

## 后果

每次 checkpoint 连接 checkout 增加一次轻量健康查询。checkpoint pool 的大小、autocommit 和共享 checkpointer 语义保持不变；PostgreSQL 不可用期间请求仍然显式失败，数据库恢复后 worker 无需重启即可淘汰旧连接。

## 验证

| 验收主张 | 直接证据 / 命令 | 负向案例 | 结果 |
|---|---|---|---|
| LangGraph pool 在 checkout 前检查连接 | `docker compose exec api python -m pytest test/unit/storage/test_langgraph_checkpointer_setup.py` | 移除 `check` 参数后，构造测试因缺少连接检查失败 | Passed，3 passed |
| PostgreSQL 重启后 worker 无需重启即可完成对话 | 先运行确定性对话 E2E 预热连接，执行 `docker compose restart postgres`，确认 ready 后再次运行同一用例；核对 Run 为 `completed` 且 `/result` 输出为 `DETERMINISTIC_AGENT_E2E_OK` | 未修复日志在相同场景中显示 checkpoint cursor 为 `[BAD]` 并产生目标异常 | Inspected；关键协议与持久化结果已核对，完整用例随后在与本缺陷无关的预加载 tool-call 持久化断言失败 |
| 重启后的 worker 不再产生目标流错误 | 核对 worker 启动时间、PostgreSQL 重启时间及重启后 Run 日志 | 未修复日志包含 `Error streaming messages: consuming input failed` | Inspected；worker 未重启，重启后 Run 完成且无目标错误 |
