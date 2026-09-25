# 集成 MCP

MCP（Model Context Protocol）让智能体调用外部服务提供的工具。管理员在“扩展 → MCP”中添加远程服务器，智能体配置再决定哪些服务器进入运行时。

## 支持的传输方式

| 传输方式 | 适用场景 |
| --- | --- |
| `streamable_http` | 新的远程 MCP 服务 |
| `sse` | 仍提供 SSE 接口的远程服务 |

管理接口只接受 `streamable_http` 和 `sse`。Yuxi 不支持 `stdio`，包括内置 MCP 和直接传入的运行时配置；历史 `stdio` 配置会被禁用，应迁移为远程服务。

## 添加远程 MCP

在“扩展 → MCP”点击“添加 MCP”，填写稳定标识、名称、传输方式和 URL。例如：

```json
{
  "slug": "custom-remote-mcp",
  "name": "Example MCP",
  "transport": "streamable_http",
  "url": "https://example.com/mcp"
}
```

管理接口对应：

```http
POST /api/system/mcp-servers
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "slug": "custom-remote-mcp",
  "name": "Example MCP",
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "description": "提供示例查询工具"
}
```

需要认证的远程服务可以配置 HTTP headers、连接超时和 SSE 读取超时。凭证会随着连接请求发送，请只配置必要的 header，并把管理接口限制在可信的管理员范围。

添加后先点击“测试连接”，确认能发现工具，再把服务器状态设为“已添加”。状态关闭时，服务器记录仍保留，但不会进入运行时。

## 让智能体使用 MCP

在智能体配置的 MCP 字段中选择已添加的服务器：

- 未显式配置时，使用当前用户可见的全部已启用服务器；
- 显式选择后，只使用选择项；
- MCP 工具仍会在执行处使用当前用户身份和服务器配置；
- 管理员可以在 MCP 详情页单独禁用某个工具。

MCP 配置从 PostgreSQL 读取，工具对象按配置哈希缓存。修改连接配置或工具禁用列表后，下一次运行会使用新的配置键。

## 内置远程 MCP

内置 DeepWiki 使用 `deepwiki-official` 标识，通过 `https://mcp.deepwiki.com/mcp` 提供 Streamable HTTP 服务，无需认证即可查询公开 GitHub 仓库。详见 [DeepWiki 官方文档](https://docs.devin.ai/work-with-devin/deepwiki-mcp)。

开发者在 [`builtin.py`](https://github.com/xerrors/Yuxi/blob/main/backend/package/yuxi/agents/mcp/builtin.py) 的 `BUILTIN_MCP_SERVERS` 中添加固定远程定义：

```python
BUILTIN_MCP_SERVERS = {
    "deepwiki-official": {
        "transport": "streamable_http",
        "url": "https://mcp.deepwiki.com/mcp",
        "description": "查询公开 GitHub 仓库的文档、架构与代码",
        "icon": "📚",
        "tags": ["内置", "代码", "文档"],
    },
}
```

API/worker 启动时同步固定定义到数据库；运行时直接读取代码中的连接字段。新内置 MCP 默认未添加，管理员需要启用；连接配置不可通过页面修改。添加定义无需修改服务逻辑或启动入口。

原内置 `mcp-server-chart` 已退役，启动时删除系统创建的记录。其他历史 stdio 记录保留并禁用，管理员可迁移为远程服务或删除。MySQL 报表技能改用 Markdown 表格，不再依赖图表 MCP；自定义角色或 Skill 中对退役 MCP 的引用需要自行调整。

## 常用管理接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/system/mcp-servers` | 查看服务器；普通用户只得到脱敏基础信息 |
| `POST` / `PUT` | `/api/system/mcp-servers`、`/{slug}` | 添加或修改远程 MCP |
| `PUT` | `/api/system/mcp-servers/{slug}/status` | 添加或移除服务器 |
| `POST` | `/api/system/mcp-servers/{slug}/test` | 测试连接并发现工具 |
| `GET` | `/api/system/mcp-servers/{slug}/tools` | 查看工具 |
| `PUT` | `/api/system/mcp-servers/{slug}/tools/{tool_name}/toggle` | 启用或禁用单个工具 |

接口字段和错误响应以实例 Swagger 为准。
