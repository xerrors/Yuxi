# API Key 外部集成

Yuxi 平台提供了 API Key 认证机制，允许外部系统在无需用户登录的情况下调用智能体对话接口。本文档详细介绍 API Key 的使用方法、接口调用方式以及安全注意事项。

## API Key 概述

API Key 是一种用于身份验证的密钥字符串，外部系统可以通过它在请求头中携带凭据来访问 Yuxi 的对话接口。与传统的用户名密码登录方式相比，API Key 更加适合用于系统间的自动化调用场景。Yuxi 的 API Key 以 `yxkey_` 为前缀，长度为 54 个字符，采用 SHA-256 哈希存储，确保密钥本身不会在数据库中明文保存。系统会记录每个 API Key 的最后使用时间，方便管理员追踪使用情况。

## 创建 API Key

登录系统后，进入 API Key 管理界面，可以创建新的密钥。创建时需要为 API Key 设置一个名称，用于标识其用途，例如"外部客服系统"或"数据同步服务"。创建的 API Key 会自动绑定到当前登录用户，绑定后的 API Key 在调用接口时会以该用户的身份执行操作。API Key 还支持设置过期时间，过期后该密钥将自动失效。

需要特别注意的是，创建 API Key 时返回的完整密钥（secret）只会显示一次，务必在创建时将其安全保存。如果遗失，需要通过"重新生成"功能生成新的密钥，原有的密钥将立即失效。

管理接口同样走通用认证：

- `GET /api/user/apikey/`：列出当前用户可见的 API Key
- `POST /api/user/apikey/`：创建 API Key
- `PUT /api/user/apikey/{api_key_id}`：更新名称、状态或过期时间
- `POST /api/user/apikey/{api_key_id}/regenerate`：重新生成密钥
- `DELETE /api/user/apikey/{api_key_id}`：删除密钥

## 确定 API 访问地址

Yuxi 后端服务绑定在 `0.0.0.0:5050`，不会自动探测或对外宣告本机 IP。实际访问地址取决于部署环境：

- **本地开发**：`http://localhost:5050`
- **生产部署（Nginx 反向代理）**：**强烈建议使用 HTTPS**，即 `https://<服务器域名>`（443 端口）。由于 API Key 会在请求头中以明文形式传输，使用 HTTP（80 端口）会导致密钥在网络传输过程中被窃听或篡改，必须避免

完整的 API 交互流程可参考自动生成的 Swagger 文档：`{base_url}/docs`。

## 接口调用方式

> **关于 `agent_id` 的说明**：下文所有示例中的 `agent_id` 对应的是智能体的 **slug** 字段（如 `default-chatbot`），而非数据库自增 ID 或 `agent_config_id`。请通过 `GET /api/agent` 列表接口确认目标智能体的 slug 值。

外部系统通过 HTTP 请求调用 Yuxi 接口时，需要在请求头中携带 API Key：

```http
Authorization: Bearer yxkey_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

当前智能体对话采用 run + SSE 流程：

1. 创建对话线程：`POST /api/chat/thread`
2. 创建运行任务：`POST /api/agent/runs`
3. 订阅事件流：`GET /api/agent/runs/{run_id}/events`

`POST /api/agent/runs` 请求体必填 `query`、`agent_id` 和 `thread_id`，可选字段包括 `meta`、`image_content`、`resume`、`parent_run_id`、`resume_request_id`。接口返回 `run_id`、`thread_id`、`status`、`request_id` 和 `stream_url`。

以下是一个典型的 Python 调用示例：

```python
import json
import requests

base_url = "https://your-yuxi-server"  # 生产环境务必使用 HTTPS；本地开发可改为 http://localhost:5050
headers = {
    "Authorization": "Bearer yxkey_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "Content-Type": "application/json",
}

thread_resp = requests.post(
    f"{base_url}/api/chat/thread",
    headers=headers,
    json={
        "agent_id": "default-chatbot",
        "title": "外部系统会话",
        "metadata": {},
    },
)
thread_resp.raise_for_status()
thread_id = thread_resp.json()["id"]

run_resp = requests.post(
    f"{base_url}/api/agent/runs",
    headers=headers,
    json={
        "query": "你好，请介绍一下你自己",
        "agent_id": "default-chatbot",
        "thread_id": thread_id,
        "meta": {"request_id": "external-request-001"},
    },
)
run_resp.raise_for_status()
run = run_resp.json()

with requests.get(f"{base_url}{run['stream_url']}", headers=headers, stream=True) as response:
    response.raise_for_status()
    event_type = None
    data_lines = []

    for line in response.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line.startswith(":"):
            continue

        if line == "":
            if event_type and data_lines:
                payload = json.loads("\n".join(data_lines))
                print(event_type, payload)
                if event_type == "end":
                    break
            event_type = None
            data_lines = []
            continue

        if line.startswith("event:"):
            event_type = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
```

如果已经有会话线程，可以复用已有 `thread_id` 直接创建 run：

```json
{
    "query": "继续上一轮话题",
    "agent_id": "default-chatbot",
    "thread_id": "existing-thread-id",
    "meta": {}
}
```

## 响应格式

运行事件流采用 Server-Sent Events 格式，响应头为 `text/event-stream`。每个事件包含：

- `event`：事件类型，可能是模型输出、工具调用、子智能体输出等语义事件，也可能是 `error` 或终止事件 `end`
- `data`：JSON 编码的事件 envelope，包含 `run_id`、`thread_id`、事件载荷等字段
- `id`：Redis Stream 序号，可作为断线重连游标

服务端还会定期发送以 `:` 开头的 heartbeat 注释，客户端应忽略。断线重连时，可以在请求头中传 `Last-Event-ID`，或在 query 参数中传 `after_seq`，服务端会从该序号后继续回放事件。

事件流默认返回完整载荷，便于排查 LangGraph/Langfuse 运行细节。如果只需要渲染消息、工具调用、工具结果、Agent state 和终止状态，可以在订阅地址追加 `?verbose=false`。精简模式会保留 SSE `event/data/id`、data 中的 `run_id/thread_id/request_id/payload` 以及客户端消费所需字段；同一 data 内的 `request_id` 会外提为单个字段。精简模式还会跳过 `metadata` 和空 `yuxi.agent_state`，并去掉每个 chunk 中重复的 `meta`、`metadata`、`thread_id`、`response`、空 `namespace` 和图片 base64 等调试字段。

每次创建 run 都会返回 `request_id`，可用于日志追踪和问题排查。如果需要在多轮对话中使用同一个会话，请复用 `thread_id`，系统会将同一线程的消息串联起来形成连贯的对话上下文。

## 认证方式

Yuxi 的 API 接口统一支持两种认证方式：

1. **API Key 认证**：使用 `Authorization: Bearer <api_key>` 格式，其中 API Key 必须以 `yxkey_` 前缀开头
2. **JWT Token 认证**：使用 `Authorization: Bearer <jwt_token>` 格式

系统根据 token 的前缀自动判断认证方式。以 `yxkey_` 开头的 token 被视为 API Key，其他 token 则作为 JWT Token 处理。这种设计使得同一个接口可以同时支持外部系统（使用 API Key）和内部前端应用（使用用户登录态）调用。

## 安全注意事项

**传输层安全**：API Key 在请求头中以明文形式传输，**生产环境必须通过 HTTPS（443 端口）调用**，避免在公网上以 HTTP 明文传输造成密钥泄露。建议在 Nginx 反向代理层启用 TLS 并强制 HTTP 重定向到 HTTPS。

保管好 API Key 密钥是最重要的安全原则。由于 API Key 一旦泄露就可能被滥用，建议不要将密钥硬编码在代码中，而是通过环境变量或配置中心来管理。如果怀疑密钥泄露，应立即在管理界面禁用该 API Key 并重新生成。启用密钥过期功能是一种良好的安全实践，可以设置较短的有效期并定期轮换。

在生产环境中，建议为不同的外部系统创建独立的 API Key，这样可以在某个密钥泄露时快速定位问题并限制影响范围。同时，建议在管理界面定期查看 API Key 的使用记录，检查是否存在异常调用情况。

关于权限控制，API Key 的权限等同于其绑定的用户在系统中的角色。如果 API Key 绑定到特定用户，则该用户的所有权限都会体现在 API Key 的操作中，因此务必妥善保管。

## 常见问题

**Q: API Key 认证失败返回什么错误？**
A: 认证失败时返回 401 Unauthorized 错误，错误信息为"无效的凭证"。请检查请求头中 `Authorization` 字段的格式是否正确，是否包含完整的密钥，且密钥必须以 `yxkey_` 开头。

**Q: 可以同时使用 API Key 和 JWT Token 吗？**
A: 不可以。系统根据 token 前缀自动判断认证方式。以 `yxkey_` 开头的 token 使用 API Key 认证，其他 token 使用 JWT 认证。

**Q: API Key 是否有调用频率限制？**
A: 目前没有单独的频率限制，但 API Key 的行为等同于其绑定的用户身份，因此会受到用户角色相关的一些限制。

**Q: 对话返回的内容是乱码怎么办？**
A: 确保客户端正确处理了 UTF-8 编码。流式响应中可能包含中文字符，需要使用正确的编码方式解析。如果在终端显示乱码，可以检查终端的编码设置。
