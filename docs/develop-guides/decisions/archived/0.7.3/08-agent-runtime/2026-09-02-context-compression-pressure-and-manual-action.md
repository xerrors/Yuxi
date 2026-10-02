# 收敛上下文压缩阈值并提供主动压缩

状态：archived
类型：simplification
Owner：backend/package/yuxi/agents/middlewares/summary.py

## 问题

自动压缩曾用 `summary_threshold` 触发确定性压缩，再用
`summary_threshold * summary_l2_trigger_ratio` 决定是否调用摘要模型。第二个门槛让配置者无法从
一个预算判断摘要行为。通用头部裁剪也会丢掉检索结果中的文档标识、来源和 URL。另一方面，
用户需要在任务结束后主动压缩，但该操作不值得新增 AgentRun 类型、队列分支和数据库 shape。

## 决策

- `summary_threshold` 是唯一的上下文压力阈值。原始请求达到阈值后，先确定性压缩大工具结果并
  重新计量；低于同一阈值时直接调用主模型，否则生成摘要。Provider 报上下文溢出时仍生成
  摘要。`summary_l2_trigger_ratio` 已删除。
- 完整工具结果先写入当前 Workdir，再替换模型可见的 ToolMessage。通用结果保留
  head/middle/tail；`query_kb` 和 `web_search` 解析 JSON，按原顺序保留结果数量、文档或网页
  标识、来源、标题、URL、分数及受限正文预览。无法写入完整结果时不裁剪。
- 手动压缩是同步的线程维护请求，只允许在线程没有活跃 Run、等待交互 Run 和排队 Request 时
  执行。service 从检查空闲到 checkpoint 更新完成始终持有 Conversation 行锁；普通 intake
  使用同一把锁，因此两者不能并发写同一线程。线程忙时返回 `409 thread_busy`，不进入 FIFO。
- `AgentStateRepository` 只调用 canonical compiled graph 的 `aget_state` 和 `aupdate_state`，不
  直接读写 checkpoint 表。service 负责准备当前 Agent context 和摘要器；Agent graph 仍只负责
  graph 装配，并通过 capability 声明按钮是否可用。
- 状态面板在下一轮输入压力达到阈值的 85% 时建议手动压缩。85% 是固定提示线，不参与自动
  压缩。按钮只跟踪一次 HTTP 请求，成功后重新读取 Agent state。
- 摘要 event 只替换模型可见历史；后续 graph 仍注入当前 Agent 的原 system prompt 和 tools。
  Provider KV cache 没有可验证命中证据，本次不改变摘要调用形状。

事实 Owner 分工：summary middleware 拥有自动与手动压缩算法及确定性工具结果预览；context
compression service 拥有线程互斥和用例编排；Agent state repository 拥有
checkpoint 写入边界；token usage state 和 Agent chat UI 分别拥有压力指标与界面投影。

## 替代方案

- 保留第二个摘要比例：能更积极地调用摘要模型，但重新引入第二个压力门槛，因此拒绝。
- 把手动压缩建模为无消息的 AgentRunRequest/AgentRun：可以排队、取消和崩溃恢复，但需要新的
  Run 类型、schema 特例、worker 分支和 SSE 状态；当前只要求空闲时点击，拒绝承担该生命周期。
- 把 `/summary` 作为用户消息提交：可复用普通 Run，但会污染持久消息和可见对话，因此拒绝。
- repository 直接改 checkpoint 表：会绕过 reducer、channel version 和 metadata 语义，因此
  repository 必须经由 compiled graph。
- 在前端单独判断空闲：多标签页和直接 API 调用仍可竞态，后端必须共用 Conversation 行锁。

## 后果

- 自动压缩只由一个压力预算解释；确定性压缩成功降到阈值以下时不消耗摘要模型调用。
- 模型视图是完整结果的有损投影；“可恢复”由 Workdir 中的完整原文和 SHA-256 保证。
- 手动请求会占用 API 请求、数据库连接和 Conversation 行锁直到摘要结束。响应丢失后重试可能
  再次摘要，不提供 request-id 级幂等或后台恢复。
- checkpoint 保存摘要，PostgreSQL Message 仍保存完整聊天记录；两者不是同一个事实源。

## 验证

| 主张 | 语义 Owner | 证据 | 负向案例 | 结果 |
|---|---|---|---|---|
| 确定性压缩后只按原阈值决定是否摘要 | summary middleware | summary 与 graph unit | 压缩后低于入口阈值时摘要模型调用数必须为 0 | Passed |
| 工具原文可恢复，检索关键字段稳定保留 | summary middleware、Workdir backend | structured/fallback/offload unit | backend 缺失或写失败时不替换；坏 JSON 回退 head/middle/tail | Passed |
| 空闲线程通过 canonical graph 更新 checkpoint | context compression service、AgentStateRepository | service/repository unit、PostgreSQL HTTP integration | 无 checkpointer graph 被拒绝；历史不足时不写 checkpoint | Passed |
| 手动压缩与普通 intake 不并发 | Conversation 行锁、Run/Request repository | PostgreSQL 并发测试、HTTP busy 测试 | 活跃、interrupted 或 queued 状态返回 409 | Passed |
| 85% 只形成可操作提醒 | token usage state、Agent chat UI | backend/web unit、lint、build | 84.9% 不提示，85% 提示 | Passed |

执行结果：backend 非慢速 unit 1682 passed；真实 PostgreSQL 并发、HTTP busy 与 HTTP 成功回读测试各
1 passed；web unit 167 passed，lint 与 build passed；工程契约检查及其 61 个 unit
passed；`git diff --check` passed。真实 Provider connectivity 和人工页面检查未运行。

旧能力不存在：`intake_compression_request`、`compression` Run 类型、相关 schema/worker/队列/SSE
分支以及 `AgentGraphRuntime` 全部删除。

重新引入条件：产品明确要求手动压缩可排队、可取消、API 或 worker 崩溃后恢复，或拥有可查询的
持久终态时，再评估独立持久任务生命周期。
