# 使用 API Key 调用 Yuxi

API Key 适合服务之间调用 Yuxi。它绑定到一个具体的 Yuxi 用户，未绑定 APP 的个人请求使用该用户的角色、部门和资源权限。绑定 APP 的 Agents 请求使用独立终端用户身份。共享 Agent 可见性按该 Yuxi 用户当前读取授权匹配，知识库、Skill 内容与插件引用资源按终端用户授权解析，资源边界见[资源权限](../mechanisms/resource-permissions.md)。

## 创建 API Key

登录 Web 后，进入“设置 → API Keys”，点击“创建 API Key”。创建时填写名称、权限、可选的 APP 标识与过期时间。Web 默认选择 `agents`；管理 API 省略 `access_level` 时仍默认 `full`。`agents` 权限只允许访问 [Agents Public API](./agents-public-api.md)。选择“本人身份”时不绑定 APP，选择“APP 终端用户身份”时必须填写 `app_id`。`knowledge` 权限只允许访问版本化的[知识库 HTTP 查询](./knowledge-base-api.md#外部查询接口)和[知识库 MCP](./knowledge-mcp.md)，不要求 `app_id`。`full` Key 受绑定用户权限及各入口的认证要求约束，产品 JWT 专用接口仍会拒绝 API Key。

本人身份的 `agents` Key 与网页使用同一用户资源范围：能读取本人已有的可见会话和工作区，并通过 Agent 使用已授权工具。它不是只读凭据，不限制为单个 Agent，也不隔离不同 Key 创建的会话。本人 Key 不接受 `X-End-User-Id`，包括与本人 UID 相同的值。

也可以调用管理接口。以下示例创建 APP Key；创建本人 Key 时省略 `app_id` 或传 `null`：

```http
POST /api/user/apikey/
Authorization: Bearer <your-jwt>
Content-Type: application/json

{
  "request_id": "crm-integration-2026",
  "name": "外部客服系统",
  "access_level": "agents",
  "app_id": "crm-service",
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
    "access_level": "agents",
    "app_id": "crm-service",
    "user_id": 3,
    "is_enabled": true
  },
  "secret": "yxkey_<your-secret>"
}
```

数据库只保存 secret 的哈希和前缀。请把完整 secret 立即放进外部系统的密钥管理器。若创建响应丢失，可以用同一用户、同一 `request_id` 和完全相同的创建参数重试。幂等重放会返回同一个 secret。改动创建意图、主密钥已经轮换或 Key 已撤销时，重试会返回冲突，此时应创建新的 `request_id` 并按需撤销旧 Key。

管理接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/user/apikey/` | 查看当前用户可见的 Key |
| `POST` | `/api/user/apikey/` | 创建 Key |
| `PUT` | `/api/user/apikey/{api_key_id}` | 修改名称、过期时间、启用状态、权限或 APP 来源 |
| `DELETE` | `/api/user/apikey/{api_key_id}` | 撤销 Key |

`superadmin` 可以查看和管理全局可见的 Key；其他用户只能操作自己有权限的 Key。管理接口也支持修改 `access_level` 与 `app_id`。更新时省略 `app_id` 保持原绑定；显式传 `null` 才清空 APP、切换为本人身份。界面保存前展示切换后的资源范围。切换不移动历史 APP 会话或文件，也不自动迁移已有 Key。禁用、过期、删除用户或撤销 Key 后，旧 secret 不能继续认证。撤销不取消已经接收的运行，也不会因为重复提交旧的创建请求而复活。列表和详情响应还包含 `last_used_at`：它表示最近一次成功认证时间，`null` 表示尚未使用。这些读取接口只返回 `key_prefix`，完整 secret 仅通过创建及其幂等重放返回。

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

服务端会根据 `yxkey_` 前缀进入 API Key 校验；其他 Bearer token 按 JWT 校验。当前派生的 secret 由 `yxkey_` 加 48 位十六进制字符组成，总长度为 54 个字符。客户端不要记录或打印完整 secret。`full` Key 的权限受绑定用户约束；`agents` Key 还受 Agents Public API 路由边界约束，访问旧产品接口会返回 `403`。普通登录用户的 JWT 也可调用 Public API。无 APP 的 `agents` Key 使用绑定用户身份。`knowledge` Key 可通过 [MCP 入口](./knowledge-mcp.md) 或 HTTP 访问 [五个只读知识库工具](./knowledge-base-api.md#外部查询接口)；知识库管理与上传接口返回 `403`，未注册的下载工具路径返回 `404`，具体知识库仍按绑定用户的资源权限过滤。

例如，用 `knowledge` Key 列出可见知识库：

```bash
curl --fail "https://yuxi.example.com/api/v1/knowledge/tools/list_kbs" \
  -H 'Authorization: Bearer yxkey_<your-secret>'
```

## 使用 Agents Public API 运行 Agent

Agent 对话统一使用 [Agents Public API](./agents-public-api.md)。绑定 APP 的 `agents` 或 `full` Key 可用 `X-End-User-Id` 在该 APP 内区分终端用户，未提供时使用 APP 默认终端用户。未绑定 APP 的 `agents` 或 `full` Key 使用密钥所属用户的产品作用域，不接受 `X-End-User-Id`。创建 Thread 时 `agent_id` 使用智能体 slug，创建和事件提交都需要 `Idempotency-Key`。下面示例使用绑定 APP 的 `agents` Key。

```bash
BASE_URL=https://yuxi.example.com
API_KEY='yxkey_<your-secret>'

curl --fail "$BASE_URL/api/v1/agents/sessions" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'X-End-User-Id: crm-user-42' \
  -H 'Idempotency-Key: crm-thread-0001' \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"default-chatbot","input":[{"role":"user","content":[{"type":"input_text","text":"请总结资料"}]}]}'
```

响应中的 `id` 标识公开 Session，`yuxi.receipt.input_id` 标识已接收输入；普通消息接收回执的 `turn_id/run_id` 始终为空。用 `GET /api/v1/agents/sessions/{session_id}/inputs/{input_id}` 等待消费并取得实际 Turn/Run，再通过 `/turns/{turn_id}` 读取整轮状态与明确结果。`/queue` 查看待处理输入，`/events` 订阅 Session SSE。断线后带 `Last-Event-ID` 续订，并回读持久快照。

继续对话时向 `POST /api/v1/agents/sessions/{session_id}/events` 提交 `agent.session.input.message`。明确排队使用 `yuxi.mode=follow_up`，修正当前轮使用 `yuxi.mode=steer`。未指定 mode 时，服务在 Thread 锁内选择。运行中 steer，空闲时 follow-up；等待回答、审批或取消清理时拒绝普通消息，协作等待只接收 FIFO 输入。等待问题或审批时，通过 Turn 快照获取等待点，并提交结构化 `yuxi.session.input.resume`。取消当前轮和继续暂停队列是两个独立控制事件。字段和示例见 [Public 协议参考](./agents-public-api.md)。

## 排查

记录 Thread、Input、Turn、Run ID 和 HTTP 状态，避免记录完整 API Key、图片内容或用户消息。`409` 表示幂等键意图冲突、状态或目标已变化。`404` 也用于隐藏跨用户或跨 APP 资源。`422` 表示输入格式无效。`202` 只表示事件已经接收，最终业务状态以持久查询为准。完整请求 Schema 与状态码以部署实例的 Swagger 页面 `<base-url>/docs` 为准。调度和 worker 恢复机制见 [Agent 输入队列与调度](../mechanisms/agent-request-queue.md)。
