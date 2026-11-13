# 知识库 MCP 接入

外部项目和 OpenCode 通过 Yuxi API 内置的 Streamable HTTP MCP 访问只读知识库工具。MCP 与 API 使用同一进程、域名和端口，入口是 `/api/mcp`。接入需要该实例签发的 `knowledge` API Key，创建方法见 [API Key 接入](./api-key-integration.md)。每个客户端传入自己的 Key，可见知识库由 Key 所属用户的资源权限决定。

## 服务地址

默认启用 Knowledge 时，启动现有 API 后即可连接 MCP，无需独立容器或额外端口：

```bash
docker compose up -d api
```

默认本机地址为 `http://127.0.0.1:5050/api/mcp`。通过[可选关闭知识库](./deployment.md#可选关闭知识库)停用后，MCP 路由不注册，返回 HTTP 404；重新开启后恢复同一入口。MCP 会话管理器随 API lifespan 启动和关闭，启动失败会阻止 API 接流量。`/api/system/health` 只证明 API 进程存活，`/api/system/ready` 检查系统就绪；真实检索仍需调用工具并核对片段与来源。

API 启动时读取 `YUXI_MCP_ALLOWED_HOSTS`，默认 `localhost:*,127.0.0.1:*,api:*`。该值是逗号分隔的 HTTP Host 白名单，支持 `:*` 端口匹配；修改环境文件后重新创建 API 容器，并保留部署原有的 Compose 覆盖。通过既有 HTTPS 代理对外提供 MCP 时，保留 `Authorization` 请求头与 `/api/mcp` 路径，并将代理传递的 Host 加入白名单。服务拒绝带浏览器 Origin 的请求，适用客户端是 OpenCode 等服务端 MCP 客户端。

## 配置 OpenCode

以下配置适用于 OpenCode 2.x。将完整 Key 保存到本机 `~/.config/yuxi/knowledge-mcp.key`，文件权限设为 `600`。合并以下配置到 `~/.config/opencode/opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "yuxi-knowledge": {
        "type": "remote",
        "url": "http://127.0.0.1:5050/api/mcp",
        "oauth": false,
        "codemode": false,
        "headers": {
          "Authorization": "Bearer {file:~/.config/yuxi/knowledge-mcp.key}"
        },
        "timeout": { "catalog": 30000, "execution": 120000 }
      }
    }
  }
}
```

已有 OpenCode 后台进程时，可通过 CLI 添加或更新连接：

```bash
opencode mcp add yuxi-knowledge --global \
  --url http://127.0.0.1:5050/api/mcp \
  --header 'Authorization=Bearer {file:~/.config/yuxi/knowledge-mcp.key}'
opencode mcp list
```

`connected` 表示 MCP 握手和能力发现完成。选择已配置模型后，让 OpenCode “使用 yuxi-knowledge 的 list_kbs 选择知识库，再 query_kb 检索指定主题，并给出来源 file_id”。检查实际工具返回的 `results` 与 `content`，随后用 `open_kb_document` 读取来源原文。五个工具与 [Knowledge Public API](./knowledge-base-api.md#外部查询接口) 调用相同的知识库服务；MCP 的 `query_kb` 额外接受多个知识库 ID。

OpenCode 1.x 将服务直接放在 `mcp.yuxi-knowledge` 下，省略 `codemode`，并使用数字 `timeout`。配置格式以对应版本的 [OpenCode MCP 文档](https://dev.opencode.ai/v2/docs/mcp-servers/)为准。不要输出展开后的客户端配置，其中可能包含完整凭据。

## 调用结果

MCP 初始化返回工具使用指引，每个工具的说明包含参数选择、结果串联和分页方式。`query_kb` 检索正文片段，`search_file` 搜索文件名；文件名没有命中时仍可尝试正文检索。用结果中的 `kb_id`、`file_id` 定位并打开原文，引用时保留文件 ID 和行号。读取长文时用 `next_offset` 继续，并省略优先级更高的 `line`；文件列表在 `has_more` 为真时将 `offset` 加上 `limit` 继续。正文窗口同时受字节上限约束，可能短于请求行数；单行超过上限时返回错误，不丢弃该行。

`query_kb` 的 `kb_id` 可以是单个字符串或非空数组，例如 `{"kb_id": ["kb-product", "kb-troubleshooting"], "query_text": "连接超时"}`。字符串输入返回原有的 `kb_id`、`results`；数组输入返回去重后的 `kb_ids`、合并的 `results`。各目标库并行检索，片段按目标库顺序拼接，保留各库自己的检索参数与排名，不比较跨库分数；每条结果中的 `kb_id`、`file_id` 标识实际来源。`file_name` 过滤对所有目标库生效。服务先检查所有目标的当前可见性，任一库不可见时不启动检索；检索失败时等待其他检索收尾后整次返回失败，不返回部分结果。内部 Agent 还受会话选择范围约束。

成功响应同时提供 JSON 文本 `content[0].text` 和标准 MCP `structuredContent`。`list_kbs` 的文本仍是数组，结构化字段为 `{"knowledge_bases": [...]}`；其他工具的结构化字段与解析后的 JSON 文本对象相同。程序客户端可以直接读取结构化字段，文本客户端继续使用原有格式。

## 权限与排查

每次 MCP HTTP 请求复用 API 的 Key 认证依赖，检查绑定用户与权限；每次工具调用按该身份查询可见知识库。服务不缓存跨请求身份，撤销或禁用后下一次请求被拒绝。推荐 `knowledge` Key；`full` Key 仍受用户可见性约束，`agents` Key 返回 `403`。普通登录 JWT 不属于外部接入协议，Key 所属用户仍须绑定部门。平台内部 Agent 通过同一 MCP 地址使用专属短期委托，自动绑定当前用户和会话选择；它不需要客户端 OAuth 流程，机制见[Agent 如何看到知识库](../mechanisms/knowledge-base.md#agent-如何看到知识库)。

缺失、无效或撤销的 Key 返回 `401`，权限不足返回 `403`，用户未绑定部门返回 `400`。工具失败返回 MCP `isError`：输入或业务条件错误为 `400`，不可见或不存在的知识库为 `404`，未知服务故障为 `500`，错误正文不包含底层异常诊断。`421` 表示 Host 不在白名单，带 Origin 的请求返回 `403`。外部 API Key 与内部 Agent 委托使用相同的五个只读工具，不提供原件下载工具。

输入 Schema 校验失败提供参数级错误。业务错误提供固定的恢复指引：`400` 时检查参数及知识库是否支持文件操作；`404` 时重新发现可见知识库与文件；`500` 时稍后重试，持续失败时联系服务管理员。错误结果不提供成功的结构化数据。

协议与生命周期回归测试位于后端现有 unit 集合：

```bash
docker compose exec api uv run --no-sync --group test pytest test/unit/routers/test_knowledge_mcp.py -q
```

真实 HTTP 集成测试位于 `test/integration/api/test_knowledge_mcp.py`，使用[测试规范](../develop-guides/testing-guidelines.md)的标准管理凭据。Fixture 自动创建并回收 Key、私有知识库、用户和持久文档，对比 MCP 与 Public API 的原文结果，验证关键词定位、跨用户不可见及 Key 撤销；不依赖预存知识库或专属 Key。实际向量检索与模型调用另外通过 OpenCode 验证。
