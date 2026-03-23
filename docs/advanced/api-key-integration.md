# API Key 外部集成

Yuxi 平台提供了 API Key 认证机制，允许外部系统在无需用户登录的情况下调用智能体对话接口。本文档详细介绍 API Key 的使用方法、接口调用方式以及安全注意事项。

## API Key 概述

API Key 是一种用于身份验证的密钥字符串，外部系统可以通过它在请求头中携带凭据来访问 Yuxi 的对话接口。与传统的用户名密码登录方式相比，API Key 更加适合用于系统间的自动化调用场景。Yuxi 的 API Key 以 `yxkey_` 为前缀，长度为 56 个字符，采用 SHA-256 哈希存储，确保密钥本身不会在数据库中明文保存。系统会记录每个 API Key 的最后使用时间，方便管理员追踪使用情况。

## 创建 API Key

登录系统后，进入 API Key 管理界面，可以创建新的密钥。创建时需要为 API Key 设置一个名称，用于标识其用途，例如"外部客服系统"或"数据同步服务"。创建的 API Key 会自动绑定到当前登录用户，绑定后的 API Key 在调用接口时会以该用户的身份执行操作。API Key 还支持设置过期时间，过期后该密钥将自动失效。

需要特别注意的是，创建 API Key 时返回的完整密钥（secret）只会显示一次，务必在创建时将其安全保存。如果遗失，需要通过"重新生成"功能生成新的密钥，原有的密钥将立即失效。

## 接口调用方式

外部系统通过 HTTP 请求调用 Yuxi 的对话接口，需要在请求头中携带 API Key。接口地址为 `POST /api/chat/agent/{agent_id}`，其中 `{agent_id}` 是目标智能体的标识符。请求头需要包含 `Authorization` 字段，值格式为 `Bearer <api_key>`，其中 `<api_key>` 是创建 API Key 时获取的完整密钥。请求体为 JSON 格式，包含 `query` 字段表示用户问题，以及可选的 `config` 和 `meta` 字段用于配置对话参数。

以下是一个典型的 Python 调用示例：

```python
import requests
import json

url = "http://your-yuxi-server/api/chat/agent/agent_id"
headers = {
    "Authorization": "Bearer yxkey_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "Content-Type": "application/json"
}
payload = {
    "query": "你好，请介绍一下你自己",
    "config": {},
    "meta": {}
}

response = requests.post(url, headers=headers, json=payload, stream=True)
for line in response.iter_lines():
    if line:
        print(line.decode('utf-8'))
```

该接口返回的是流式响应（Server-Sent Events），每个事件是一行 JSON 数据，包含对话的增量内容。客户端需要逐行解析并处理这些事件来构建完整的对话结果。

## 响应格式

接口返回的流式响应采用 JSON Lines 格式，每行代表一个事件。常见的事件类型包括：

`event: data` 表示数据事件，携带实际的对话内容。`event: error` 表示错误事件，当对话过程中发生错误时会收到此类事件。`event: done` 表示完成事件，标志对话结束。

每次调用都会在响应中包含 `request_id`，这是本次对话的唯一标识符，可用于日志追踪和问题排查。如果需要在多轮对话中使用同一个会话，可以通过 `config.thread_id` 参数指定线程 ID，系统会将同一线程的消息串联起来形成连贯的对话上下文。

## 认证方式

Yuxi 的 API 接口统一支持两种认证方式：

1. **API Key 认证**：使用 `Authorization: Bearer <api_key>` 格式，其中 API Key 必须以 `yxkey_` 前缀开头
2. **JWT Token 认证**：使用 `Authorization: Bearer <jwt_token>` 格式

系统根据 token 的前缀自动判断认证方式。以 `yxkey_` 开头的 token 被视为 API Key，其他 token 则作为 JWT Token 处理。这种设计使得同一个接口可以同时支持外部系统（使用 API Key）和内部前端应用（使用用户登录态）调用。

## 安全注意事项

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
