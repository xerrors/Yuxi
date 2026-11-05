# 运行图的工具身份与 checkpoint 拓扑

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/runtime/agent_backends/chatbot/graph.py

## 问题

业务 Run 身份不能占用 LangGraph 为图流重连保留的 metadata.run_id。运行与 checkpoint 清理必须共享协作节点和状态通道。MCP loader 只知道显式配置工具，最终构图需要同时检查 middleware 工具身份；精确协作结果查询需要保留 Turn/Run 归属并避免读取无关大载荷。

## 决策

### 实现方案

BaseAgent 只在交给 LangGraph 的 metadata 副本中把业务 run_id 移为 yuxi_run_id，追踪与数据库保持原业务身份。Chatbot 构图收集 middleware 工具名后，在 create_agent 前拒绝与 MCP 来源工具重名的项。沿用现有 Chatbot 构图函数，不引入新的图工厂。

checkpoint_only 继续禁止模型、资源发现和实际工具执行，同时保留只声明节点与工具的协作中间件，使状态 API 看到原始节点和通道。协作精确结果查询只选择普通 get_result 用例需要的 Input、Run 和 Message 字段，保留 Session、Turn 归属及 result_run_id 校验；不新增状态查询 API。

## 替代方案

- 全量提取通用构图框架：当前普通 Chatbot 修复没有第二个消费者，不承担该维护表面。
- 只在 MCP loader 校验：loader 不拥有 middleware 的最终工具集合。
- 清理时简化协作节点：会让同一 checkpoint 在两个不同拓扑间恢复。
- 保留结果查询的整行读取：功能正确，但额外读取 Input 正文、配置和 Run 输入载荷；采用最小列投影后以实际 SQL 证明边界。

## 后果

LangGraph checkpoint 保存 yuxi_run_id 和 turn_id，追踪与数据库继续使用原业务 Run ID。MCP 工具与 middleware 同名会使构图显式失败；该检查不替代执行授权。checkpoint_only 保留协作节点声明，状态 API 不执行模型或工具，也不读取运行资源。

协作结果仍以精确 Turn.result_run_id 指向的 Run 和输出 Message 为准，不从相邻 Run 补结果。列投影只服务现有 get_result 消费者，不新增状态查询 API 或输出开关。结果查询不再读取整个 Input、Run 或 Message 行，实际 SQL 证明大字段不进入选择列表；这不是延迟或吞吐基准。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| checkpoint 持久业务 ID 且继续不重放输入 | metadata 保留键冲突 | BaseAgent | `test_chat_service_langfuse_stream.py` 的真实图三安全边界；`test_steer_aggregates_and_yields_into_same_turn` 的真实 worker | 含原业务 run_id 的图继续执行，输入和工具不重放 | Passed |
| 持久 checkpoint 绑定精确业务结果 | metadata 或结果身份串用 | BaseAgent、checkpoint storage | `test_worker_preserves_selection_and_completes_after_knowledge_error` 后独立 AsyncPostgresSaver 回读 | yuxi_run_id 等于精确 Turn.result_run_id，turn_id 匹配，完成状态和持久输出 Message 归属一致 | Passed |
| MCP 不覆盖 middleware 工具 | 模型工具身份混用 | Chatbot graph | `test_summary_graph_config.py::test_graph_rejects_mcp_tools_that_shadow_middleware`，正式工具 loader 与构图，MCP 发现边界使用替身 | MCP read_file、create_session 重名被拒绝，合法远端工具通过 | Passed |
| 清理图保留协作节点和状态 | 取消 checkpoint 丢拓扑 | Chatbot graph | 真实图 nodes/channels 与取消 checkpoint 回读；`test_sessions_use_public_state_and_shared_sandbox[False-False-True]` 真实 worker 取消链 | 去掉清理图协作中间件时拓扑断言失败；清理构图读取资源时失败 | Passed |
| 精确结果不读取无关大载荷 | 结果查询额外列 | cooperation repository/service | `test_session_cooperation.py::test_exact_result_survives_next_turn_and_queued_input_keeps_identity` 的 PostgreSQL get_result 与实际 SQL | Input messages/input_payload 和 Run input_payload 不被选择，A 完成且 B 派发后仍返回 A | Passed |
| Knowledge 关闭拓扑中的 checkpoint 消费者 | 部署装配差异 | Knowledge bootstrap、BaseAgent | `test_worker_preserves_selection_and_completes_after_knowledge_error` 的 disabled 分支 | 未切换到关闭 Knowledge 的独立拓扑 | Not run |

工具冲突、metadata、协作拓扑与查询列的目标缺陷均验证恢复时失败、修复后通过。真实 worker 证据来自启用 Knowledge 的部署，错误处理链的 checkpoint、数据库状态、输出归属及 Session/Run 知识选择经过独立回读；它不证明 Knowledge 关闭拓扑。
