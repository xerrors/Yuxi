# 工具系统

Yuxi 的工具分成三层：内置工具、知识库工具和 MCP 工具。Graph 创建时准备可执行工具，运行时再根据用户权限、Agent 配置和 Skill 激活状态决定模型能看到什么。

## 注册一个内置工具

普通内置工具使用 `@tool` 注册：

```python
from yuxi.agents.toolkits.registry import tool


@tool(category="buildin", tags=["示例"], display_name="示例工具")
def example_tool(text: str) -> str:
    """返回处理后的文本。"""
    return text
```

- `category` 用于前端分组，常见值是 `buildin`、`knowledge` 和 `debug`；
- `tags` 用于展示和筛选；
- `display_name` 是给用户看的名称，工具 ID 是给代码和模型协议使用的稳定名称；
- 工具模块需要被 `toolkits` 包导入，装饰器才会执行注册。

工具的注册不等于授权。产生文件、网络或数据库副作用的工具必须在执行边界再次校验当前用户和目标资源。

## 当前内置能力

常用内置工具包括：

| 工具 | 作用 |
| --- | --- |
| `ask_user_question` | 等待用户回答交互式问题 |
| `ocr_parse_file` | 把工作区中受支持的 PDF、Office 或图片转换为 Markdown |
| `present_artifacts` | 展示当前用户可见的文件产物 |
| `install_skill` | 从允许的沙盒路径或 Git 来源安装个人 Skill；子智能体不可用 |
| `web_search` | 使用已配置的豆包或 Tavily 搜索网页 |

文件读写和命令执行由 Agent 的 Sandbox backend 提供。`present_artifacts` 推荐展示当前 Project 的 `outputs/` 文件；`large_tool_results` 和会话摘要等内部文件不会作为交付物展示。

图片生成能力由内置 `image-gen` Skill 提供，不再作为独立的 Python 工具注册。具体依赖和文件位置由该 Skill 说明。

## 知识库工具

知识库工具以 `@tool(category="knowledge")` 注册，但不默认出现在模型工具列表。Agent 激活内置 `knowledge-base` Skill 后，Skills middleware 才会向模型开放：

| 工具 | 作用 |
| --- | --- |
| `list_kbs` | 列出当前运行可见的知识库 |
| `query_kb` | 按 `kb_id` 检索片段，返回 `kb_id`、`file_id` 和内容 |
| `find_kb_document` | 在指定文件中按关键词或正则定位内容 |
| `open_kb_document` | 按 `file_id` 分段读取解析后的文档 |
| `get_mindmap` | 读取知识导图 |
| `search_file` | 按文件名搜索可见知识库中的文件 |
| `download_kb_file` | 把有权限的原始文件下载到当前 Project 的 `outputs/` |

工具参数中的 `kb_id`、`file_id` 和文件名都会在工具执行处重新检查，不能用模型提示词或 Agent 配置绕过知识库权限。知识库不会挂载为沙盒目录，读取方式见[知识库机制详解](../mechanisms/knowledge-base.md)。

需要在 Python 中直接取得知识库工具时：

```python
from yuxi.agents.toolkits.kbs import get_common_kb_tools

kb_tools = get_common_kb_tools()
```

返回的具体顺序由函数实现维护，不要把顺序当作协议。

## 工具组装流程

内置 Agent 创建 Graph 时执行：

1. `prepare_agent_runtime_context` 按当前用户权限过滤工具、知识库、MCP、Skills 和子智能体。
2. `resolve_configured_runtime_tools(context)` 注册 Agent 配置和可见 Skill 依赖的可执行本地工具，并加载配置的 MCP 工具。
3. `SkillsMiddleware` 根据当前已预加载或已激活的 Skill，向模型请求开放相应工具 schema。
4. 工具执行器再次检查具体文件、知识库、MCP 和用户身份。

```text
Agent 配置 + 用户权限
        ↓
运行时资源快照
        ↓
可执行工具注册 ──→ Skill 激活门控 ──→ 模型可见工具
        ↓
执行处的目标授权
```

“可执行”与“模型可见”是两件事：工具可以先注册到 ToolNode，等 Skill 激活后才对模型开放；模型看见某个工具也不代表它可以访问任意资源。

## MCP 和 Skills

MCP 工具可由 Agent 直接选择的已启用服务器提供，也可由已激活 Skill 的 `mcp_dependencies` 按需加载；服务器配置和工具禁用列表由 MCP 管理链路读取。预加载 Skill 从首轮开放本地工具和 MCP 依赖，普通 Skill 在读取 `SKILL.md` 激活后开放这些依赖。

- 工具实现放在 `toolkits`；
- Skill 的使用说明和依赖放在 Skill 目录；
- MCP 的连接和工具发现由 MCP 服务负责；
- Agent 配置只选择资源范围，不直接复制工具实现。

详细规则见 [Skills 管理](./skills-management.md) 和 [MCP 集成](./mcp-integration.md)。

## 新增工具时检查

- 工具是否属于内置工具、知识库工具、MCP 还是 Skill；
- 是否需要在 `ToolNode` 注册，是否需要 Skill 激活后才让模型看见；
- 产生副作用时，执行处是否有用户、路径和资源权限校验；
- 错误是否以结构化结果返回，并保留可排查信息；
- 前端展示名称是否来自稳定元数据；
- 是否补充纯逻辑和真实 HTTP/文件链路的相应测试。
