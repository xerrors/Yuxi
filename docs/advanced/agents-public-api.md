# Agents Public API

`/api/v1/agents` 提供 Yuxi 的公开会话入口。产品前端使用登录 JWT；外部应用使用绑定 `app_id` 的 API Key。`agents` Key 只能访问 Public API。服务端从 Key 确定 APP 归属，客户端提供的 `X-App-Id` 不能改变资源作用域。

外部应用可通过 `X-End-User-Id`（1–128 个字符、无首尾空白）声明终端用户。服务端按 Key 用户、APP 和外部 ID 解析独立 User；会话、请求、运行和工作目录归该用户。后续读取及 SSE 必须使用同一个 Header。终端用户不能登录产品 API 或签发 Key。产品 JWT 不接受 `X-End-User-Id`，外部 Key 也不能读取产品 JWT 创建的会话。

本接口实现选定的 Session/Turn 生命周期，仍使用 Yuxi 的 SSE 事件载荷，不宣称与 OpenAI Agents API 完全兼容。内部 Thread/Conversation 表示会话；持久 Turn 记录一轮工作，Request 记录输入队列，Run 记录一次执行。引导输入和审批恢复可让同一个 Turn 关联多个 Request/Run。普通 follow-up 输入按线程 FIFO 排队，在当前轮结束后开启新 Turn。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/agents`、`/api/v1/agents/{agent_id}` | 查询可见主 Agent |
| `POST` | `/api/v1/agents/sessions` | 创建空 Session，或带 `input` 创建首轮；`stream=true` 返回 SSE |
| `GET` | `/api/v1/agents/sessions/{id}` | 查询 Session 当前状态和 Turn |
| `POST` | `/api/v1/agents/sessions/{id}/events` | 提交一条消息、取消或产品恢复事件，返回 `202` |
| `GET` | `/api/v1/agents/sessions/{id}/events` | 持续订阅 Session；`turn_id` 查询参数限定一轮 |
| `GET` | `/api/v1/agents/sessions/{id}/turns/{turn_id}` | 查询一轮状态、结果和关联的 Request/Run ID |
| `GET` | `/api/v1/agents/sessions/{id}/turns/{turn_id}/items` | 分页读取本轮持久用户和助手消息 |

原生 `/threads` 与 `/threads/{id}/requests` 入口继续存在，用于直接操作 Yuxi 的 FIFO Request；这些入口要求 `Idempotency-Key`。原生 Thread 首条请求与 Session 首条输入共用创建意图和幂等键。Session 创建和输入事件的 Key 可省略，省略时每次请求独立接收。需要安全重试的调用方应提供 1–128 字符的 `Idempotency-Key`；同键不同输入返回 `409`。`202` 只证明接收，终态须查询 Turn 或持久 Items。

空会话创建示例：

```bash
curl --fail "$BASE_URL/api/v1/agents/sessions" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'X-End-User-Id: crm-user-42' \
  -H 'Idempotency-Key: crm-session-0001' \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"default-chatbot","title":"客户咨询"}'
```

创建请求可带一条 `user` 消息作为 `input`，内容支持 `input_text` 和内联 `data:image/...;base64,...` 的 `input_image`；可选 `project_id`、`model_spec` 与 `tool_approval_mode`。`stream=true` 要求同时提供输入。服务端先提交持久 Request，再在同一 POST 中发送 `session_created` 和本轮事件；HTTP 断开不会取消已接收的工作。

空 Session 的数据库记录可能已提交，而工作目录随后暂时不可用；此时返回 `503`，错误详情含已持久化的 `session_id`。恢复目录后用原 `Idempotency-Key` 重试，返回同一 Session。带首条输入的创建在同一事务提交 Conversation、Message 和 Request；输入或配置校验失败不会占用该创建键。

向已有会话提交消息：

```bash
curl --fail -X POST "$BASE_URL/api/v1/agents/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'X-End-User-Id: crm-user-42' \
  -H 'Idempotency-Key: crm-message-0002' \
  -H 'Content-Type: application/json' \
  -d '{"events":[{"type":"agent.session.input.message","mode":"follow_up","input":[{"role":"user","content":[{"type":"input_text","text":"请继续"}]}]}]}'
```

`mode=follow_up` 在活跃工作期间排队开启下一轮，是产品前端的默认行为。`mode=steer` 将运行中输入归入当前 Turn；省略模式时按 `steer` 处理。模式参与幂等意图判定。单次请求仅接受一个事件，批量事件、远程图片及未知字段返回 `422`。

取消事件为 `{"events":[{"type":"agent.session.input.cancel"}]}`，默认针对服务端接收时的当前 Turn。产品停止按钮会附带 `run_id`；也可附带 `turn_id`，目标在接收时已变化则返回 `409`，不会误取消下一轮。首次接收时固定目标 Turn；同键重试返回原目标。空闲 Session 的无目标取消为已接收的空操作。处于等待审批或回答的 Turn 暂不接受取消，返回 `409`，因为运行 checkpoint 尚未实现可验证的清理路径。取消运行中 Turn 后，通过查询确认最终状态；已持久输出不因 `202` 消失。

产品审批或回答使用 Yuxi 扩展 `yuxi.session.input.resume`，须明确指定等待的 `turn_id`、中断的 `run_id` 和 `resume` 值；服务端校验三者归属并创建同一 Turn 的续跑 Run。这个扩展不等同于官方 `tool_result`，后者当前返回 `422`。

Session `status` 为 `idle`、`in_progress` 或 `requires_action`；完成、失败与取消属于 Turn。Turn 的 `request_ids`、`run_ids` 是明确持久关联，`output`、`usage`、`error` 从本轮绑定的 Run 读取。Items 只展示持久用户和助手 Message，以 `msg_<数据库 ID>` 为稳定 ID，支持 `after_id` 与 `limit` 分页。

Session SSE 可以在提交输入前订阅，也可以用 `turn_id` 限定一轮。流沿用 Yuxi 的 `run_created`、Run 过程事件、`end` 和 `turn_status`；过程事件游标使用 `<run_id>:<Redis seq>`，Run 结束游标为 `<run_id>:end`，重连可放在 `Last-Event-ID`。Redis 过程事件是短期数据，断线后以 Session、Turn 和 Items 的持久查询重建结果。`end` 只表示该 Run 结束；一个 Turn 可能继续关联后续 Run。
