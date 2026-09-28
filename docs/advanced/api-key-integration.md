# 使用 API Key 调用 Yuxi

API Key 适合服务之间调用 Yuxi。它绑定到一个具体的 Yuxi 用户，请求会继承该用户的角色、部门和资源权限；它不是一个绕过权限的“超级凭证”。

## 创建 API Key

登录 Web 后，进入“设置 → API Keys”，点击“创建 API Key”。创建时填写名称和可选的过期时间。

也可以调用管理接口：

```http
POST /api/user/apikey/
Authorization: Bearer <your-jwt>
Content-Type: application/json

{
  "request_id": "crm-integration-2026",
  "name": "外部客服系统",
  "expires_at": "2027-01-01T00:00:00Z"
}
```

`request_id` 是创建意图的幂等标识，长度为 8–64 个字符，只能使用字母、数字、`.`、`_`、`:` 和 `-`。同一用户用相同的 `request_id` 重试会返回同一创建事实；用同一个 ID 提交不同意图会返回冲突。

响应中的 `secret` 会返回本次创建意图对应的完整密钥：

```json
{
  "api_key": {
    "id": 12,
    "key_prefix": "yxkey_abcdef",
    "name": "外部客服系统",
    "user_id": 3,
    "is_enabled": true
  },
  "secret": "yxkey_<your-secret>"
}
```

数据库只保存 secret 的哈希和前缀。请把完整 secret 立即放进外部系统的密钥管理器。若创建响应丢失，可以用同一用户、同一 `request_id` 和完全相同的创建参数重试；幂等重放会返回同一个 secret。改动创建意图、主密钥已经轮换或 Key 已撤销时，重试会返回冲突，此时应创建新的 `request_id` 并按需撤销旧 Key。

管理接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/user/apikey/` | 查看当前用户可见的 Key |
| `POST` | `/api/user/apikey/` | 创建 Key |
| `PUT` | `/api/user/apikey/{api_key_id}` | 修改名称、过期时间或启用状态 |
| `DELETE` | `/api/user/apikey/{api_key_id}` | 撤销 Key |

`superadmin` 可以查看和管理全局可见的 Key；其他用户只能操作自己有权限的 Key。删除用户或撤销 Key 后，旧 secret 不能继续使用，也不会因为重复提交旧的创建请求而复活。列表和详情响应还包含 `last_used_at`：它表示最近一次成功认证时间，`null` 表示尚未使用；`key_prefix` 只用于识别 Key，服务端不会再次返回完整 secret。

## 选择调用地址

API 服务在容器内监听 `5050`：

- 开发环境可使用 `http://localhost:5050`；
- 生产环境使用反向代理提供的 HTTPS 地址，例如 `https://yuxi.example.com`；
- 同一套 API 的 Web 入口通常是 `http://localhost:5173`（开发）或反向代理的根路径（生产）。

API Key 通过 `Authorization` 请求头发送。生产环境必须使用 HTTPS，避免密钥在网络中被窃听或篡改。

## 认证请求

```http
Authorization: Bearer yxkey_<your-secret>
```

服务端会根据 `yxkey_` 前缀进入 API Key 校验；其他 Bearer token 按 JWT 校验。当前派生的 secret 由 `yxkey_` 加 48 位十六进制字符组成，总长度为 54 个字符；客户端不要记录或打印完整 secret。两种方式可以调用同一个受保护接口，但 API Key 的实际权限仍等于它绑定的用户。

## 运行一次 Agent

通用 Run API 分为创建线程、提交运行和读取事件三步。创建线程时，`agent_id` 的值是智能体 slug，不是数据库自增 ID：

```bash
BASE_URL=https://yuxi.example.com
API_KEY=yxkey_<your-secret>

curl --fail "$BASE_URL/api/chat/thread" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"default-chatbot","title":"外部系统会话","metadata":{}}'
```

从响应中取出线程 `id`，再提交运行：

```bash
curl --fail "$BASE_URL/api/agent/runs" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "query":"你好，请介绍一下你自己",
    "agent_slug":"default-chatbot",
    "thread_id":"<thread-id>",
    "meta":{"request_id":"crm-run-2026-0001"},
    "queue_policy":"enqueue"
  }'
```

请求会返回 `run_id`、`request_id`、`thread_id`、状态和流地址。立即派发的 Run 提供 `stream_url`；仍在 FIFO 中等待的 Request 提供 `request_events_url`，具体状态以同一 `request_id` 查询结果为准。`agent_slug` 也使用智能体 slug；`thread_id` 用于把多轮输入放进同一上下文。

通用 Run 的可选字段如下：

| 字段 | 作用 |
| --- | --- |
| `meta.request_id` | 请求幂等和追踪标识；不传时服务端生成 UUID |
| `image_content` | 可选的 base64 图片：单张传字符串，多张传数组（最多 10 张、总量约 80MB）；普通 Chat 会把它作为图片消息提交 |
| `model_spec` | 本次运行的模型覆盖，格式为 `provider_id:model_id` |
| `tool_approval_mode` | 本次运行的工具审批模式覆盖 |
| `queue_policy` | 普通 Chat 可用 `enqueue`、`reject` 或 `steer`；默认是 `enqueue` |
| `resume` | LangGraph 恢复载荷；非空时走恢复路径，不进入普通 Request 队列 |
| `created_by_run_id` | 恢复时填写被恢复的 Run ID |

`resume` 不是布尔开关。恢复请求可以同时带 `query` 和 `image_content`（同样接受单值或数组），但 `queue_policy` 只适用于普通 Chat；恢复和 Steer 的状态、权限与失败语义见[Agent 请求队列与调度设计](../agents/agent-request-queue.md)。

### 读取 SSE

`stream_url` 是 Server-Sent Events 地址。使用 `curl` 订阅：

```bash
curl --no-buffer --fail "$BASE_URL<stream_url>" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Accept: text/event-stream'
```

每个事件包含 `event`、`data` 和 `id`：

- `event` 是事件类型；常见过程事件包括消息、工具和状态更新，`end` 表示该 Run 的终止事件，`error` 表示流中的错误事件；
- `data` 是 JSON envelope，包含 `run_id`、`thread_id` 和事件载荷；
- `id` 是 Redis Stream 游标；
- 以 `:` 开头的行是 heartbeat，客户端应忽略；
- 收到 `end` 或 `error` 后停止等待新的输出，并用同一 `run_id` 读取最终结果；
- 断线重连时可以发送 `Last-Event-ID`，也可以在 URL 中使用 `after_seq`；
- `?verbose=false` 返回面向客户端的精简载荷，适合普通 UI；默认模式保留更多调试字段。

不需要过程事件时，直接读取同一个 Run 的最终结果：

```http
GET /api/agent/runs/{run_id}/result
Authorization: Bearer yxkey_<your-secret>
```

结果接口只读，不会重复执行 Run。最终输出必须从该 `run_id` 绑定的结果读取，不要从相邻 Run 或最近一条消息猜测。

## Agent Call 接口

外部系统也可以使用面向调用方的 `agent-invocation` 接口。它不支持 `stream=true`：

| 接口 | 用途 | 关键字段 |
| --- | --- | --- |
| `POST /api/agent-invocation/agent-call/runs` | 创建 Agent Call；默认等待终态，`async_mode=true` 时立即返回运行信息 | `agent_slug`、`messages`、`thread_id`、`request_id`、`model_spec`、`tool_approval_mode`、`agent_call_meta`、`async_mode`、`queue_policy`、`stream` |
| `POST /api/agent-invocation/agent-call/runs/result` | 按 `run_id` 读取 OpenAI 风格的结果 | `run_id`、可选 `agent_slug` |
| `POST /api/agent-invocation/eval/runs` | 运行一次评估样例并返回结果 | `query`、`agent_slug`、`thread_id`、`evaluation`、`image_content`、`model_spec`、`tool_approval_mode`、`include_trajectory_summary` |

`evaluation` 可以包含 `dataset_name`、`dataset_item_id` 和 `experiment_name`，用于关联 Langfuse 评估上下文。评估端点的 `image_content` 同样接受数组，但它与普通 Run 走的是不同路径：网关只对 `/api/agent/runs` 放宽了请求体上限，打到本端点的多图请求会在网关处按默认上限被拒，需要部署侧一并放行。`include_trajectory_summary=true` 时，响应附带最多 500 个运行事件聚合出的工具调用、错误、中断和事件范围摘要；它不是完整事件流。

同步 Agent Call 不能排队，线程忙碌时会返回拒绝结果；异步调用默认使用 `enqueue`。一个最小请求：

```bash
curl --fail "$BASE_URL/api/agent-invocation/agent-call/runs" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_slug":"default-chatbot",
    "messages":[{"role":"user","content":"请总结这段文字：……"}],
    "async_mode":false
  }'
```

`messages` 使用 OpenAI 风格结构，系统会取最后一条 `user` 消息作为输入。文本可以直接使用字符串；图片使用多模态数组：

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "请描述这张图片"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,<base64-data>"}}
  ]
}
```

纯文本数组也可以使用；不支持的 content part 类型会返回 `422`。`model_spec` 可以覆盖本次运行使用的模型；不要通过 `agent_call_meta.context` 覆盖 Agent runtime context。`stream` 字段为兼容 OpenAI 客户端而保留，但只能传 `false`，传 `true` 会返回 `422`。同步 Agent Call 固定使用 `queue_policy=reject`；异步调用默认使用 `enqueue`，显式传入不适用的策略会被拒绝。

Agent Call 结果包含 `run_id`、`agent_slug`、`thread_id`、`status`、`output`、`choices` 和可用时的 `usage`。同步等待超时时，接口返回 HTTP `504`；当前 Run 快照放在错误响应的 `detail.run` 中，里面的 `agent_run_id` 就是后续查询所需的运行 ID。此时不要把 504 当作 Run 失败：可以继续调用 `POST /api/agent-invocation/agent-call/runs/result`，提交 `{"run_id":"<agent-run-id>"}`，读取最终状态。

```http
POST /api/agent-invocation/agent-call/runs/result
Authorization: Bearer yxkey_<your-secret>
Content-Type: application/json

{"run_id":"<agent-run-id>"}
```

## 安全建议

- 为不同外部系统创建不同的 Key，并设置过期时间。
- 只把 secret 放在密钥管理器或受保护的环境变量中，不要硬编码进源码、镜像或日志。
- 怀疑泄露时立即在“API Keys”中停用或删除，并检查外部系统的重试配置。
- API Key 继承绑定用户的权限。为集成创建权限最小化的专用用户，不要直接使用超级管理员 Key。
- 生产调用使用 HTTPS；HTTP 只适合本机开发。
- 排查时同时记录 `request_id`、`run_id` 和 `thread_id`，但不要记录完整 API Key。

完整请求 Schema、状态码和当前字段以部署实例的 Swagger 页面为准：`<base-url>/docs`。更多关于 Run、FIFO、SSE 和取消语义的说明见[Agent 请求队列与调度设计](../agents/agent-request-queue.md)。
