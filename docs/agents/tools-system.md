# 工具系统

Yuxi 的工具分成两层：本地注册的内置工具，以及由 MCP server 提供的工具（含内置 `knowledge` MCP 提供的知识库工具）。Graph 创建时准备可执行工具，运行时再根据用户权限、Agent 配置和 Skill 激活状态决定模型能看到什么。

## 注册一个内置工具

普通内置工具使用 `@tool` 注册：

```python
from yuxi.modules.extensions.tools.registry import tool


@tool(category="builtin", tags=["示例"], display_name="示例工具")
def example_tool(text: str) -> str:
    """返回处理后的文本。"""
    return text
```

- `category` 用于前端分组，当前只有本地注册的 `builtin`；知识库工具不再本地注册；
- `tags` 用于展示和筛选；
- `display_name` 是给用户看的名称，工具 ID 是给代码和模型协议使用的稳定名称；
- 内置工具模块由 `yuxi.modules.extensions.tools.builtin` 包显式导入，装饰器在进程加载时执行注册。

供应商或已构造的 BaseTool 使用 registry 的 `register_tool(tool, ToolExtraMetadata(...))` 登记。registry 返回独立的列表和附加元数据，catalog 每次从当前注册派生独立目录；后续注册立即可见。列表内的 BaseTool 仍是执行实例，读取者不得修改它的执行配置。

工具的注册不等于授权。产生文件、网络或数据库副作用的工具必须在执行边界再次校验当前用户和目标资源。

## 当前内置能力

常用内置工具包括：

| 工具 | 作用 |
| --- | --- |
| `ask_user_question` | 等待用户回答交互式问题 |
| `ocr_parse_file` | 把工作区中受支持的 PDF、Office 或图片转换为 Markdown |
| `present_artifacts` | 展示当前用户可见的文件产物 |
| `web_search` | 使用已配置的 Tavily、DeepSeek 或豆包 Custom 搜索网页 |
| `web_fetch` | 通过 Tavily Extract 或 DeepSeek Fetch 读取指定网页的 Markdown 正文 |

文件读写和命令执行由 Agent 的 Sandbox backend 提供。Agent 根据用户要求和项目结构选择合适的产物位置，通过 `present_artifacts` 展示结果文件；`large_tool_results` 和会话摘要等内部文件不会作为交付物展示。

`present_artifacts` 接受绝对路径列表 `filepaths`，显示文件卡片，支持详情预览、下载和保存到个人空间。对话内的图片展示由内置 `chat-widgets` Skill 的 Image 或 ImageGroup 直接展示，可选短语叠放在图片左下角；path 使用当前用户可见的沙盒虚拟绝对路径；前端经当前会话鉴权读取图片，无需先登记交付物。

图片生成能力由内置 `image-gen` Skill 提供，不再作为独立的 Python 工具注册。具体依赖和文件位置由该 Skill 说明。

## 内置工具：向用户提问

`ask_user_question` 的 `questions` 参数是包含 1-5 个问题的列表，每项必须提供非空 `question`。纯问答省略 `options`；选择题的 `options` 是最多 5 项的对象列表，每项提供非空 `label` 和 `value`，可附带 `description`。`multi_select` 和 `allow_other` 使用 JSON 布尔值，分别默认 `false` 和 `true`。

```json
{
  "questions": [
    {"question": "交付物面向哪些读者？"},
    {
      "question_id": "format",
      "question": "选择交付格式",
      "options": [{"label": "Markdown", "value": "markdown"}, {"label": "PDF", "value": "pdf"}],
      "allow_other": false
    }
  ]
}
```

工具入口校验参数、去除文本首尾空白并补齐默认字段；省略 `question_id` 时按问题位置生成 `q-1` 等稳定 ID。整组问题的 ID 必须唯一，显式 ID 与生成 ID 冲突也会拒绝。JSON 字符串、包装对象、字段别名、字符串选项和字符串布尔值会产生工具参数错误，不创建等待点。

收窄工具参数 schema 的部署升级前，需要停止接收新的提问，并完成或取消已有提问等待点，再替换 API 和 worker。LangGraph 恢复会重放 checkpoint 中的原始工具调用参数；旧入口接受的字符串选项等参数会被新 schema 拒绝，即使等待点问题已标准化也不能直接恢复。升级不自动迁移或取消这些调用。

LangGraph checkpoint 保存标准问题结构，执行服务将其投影为持久等待点。Web 和 Public API 客户端使用等待点中的 `question_id` 提交回答；Web 只转换展示字段和推导文本、单选、多选类型，不重新生成 ID。npm CLI 遇到等待点时只提示转到网页继续，不提交回答。恢复沿用同一 Turn，协议入口见 [Public API](../advanced/agents-public-api.md)。参数契约与工具实现在[提问模块](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/tools/builtin/ask_user_question.py)，完整恢复链路由[生命周期 E2E](https://github.com/xerrors/Yuxi/blob/main/backend/test/e2e/test_agent_lifecycle_e2e.py)验证。

## 内置工具：记录改进线索

`mark_for_improvement(reason)` 与 `ask_user_question` 同属基础内置工具，所有正常 Agent 图自动注册，不受工具选择、模型、专属 Skill 或 Skill 激活状态限制。基础工具集合由[内置工具模块](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/tools/builtin/__init__.py)统一维护，注册、授权和门控使用同一集合。工具描述要求 Agent 在用户纠正、反复不满意、工具失败或完成任务所需能力缺失时主动记录，无需等待用户要求或问题解决；找到有实际依据的可复用方法、脚本时也记录。

reason 去除首尾空白后为 1–4000 字符，写明实际问题或经验、相关步骤或错误、已尝试的处理和当前结果，并区分事实与推测；不要包含凭据和无关原始用户内容。同一问题或经验不重复记录，有新增事实或不同问题时再记录；普通完成任务不要求标记。记录后继续处理原任务，记录工具自身失败时不循环调用或声称已保存。

没有真实 Run 或工具调用 ID 时，工具明确拒绝保存。工具从注入的 runtime 取得当前身份、Run、worker token 和 tool_call_id，executor 在 PostgreSQL 事务中再次检查账号、Agent 使用权及有效执行租约。提交后返回 `{id, run_id, saved:true}`；同一 Run 的同一调用重放返回同一 ID，改变原因产生冲突。数据库失败或无效原因返回工具错误，原任务按普通工具错误处理继续；授权撤销仍终止执行。

标记保存为待复盘线索，Run 后续失败或取消不会删除已提交标记。它不表示经验已验证或 Skill 已更新，不自动读取正文中的文件路径，不修改 Skill 或同步 Langfuse。用户反馈和集中查询的权限、保留和分页见[反馈采集](../advanced/agent-feedback.md#反馈采集)。

## 知识库工具

知识库工具不再以 `@tool` 本地注册。它们由内置 `knowledge` MCP server（`extensions/mcp/builtin.py`，默认启用）提供，Agent 激活或预加载内置 `knowledge-base` Skill（`mcp_dependencies: ["knowledge"]`）后，运行装配从该 server 的真实目录加载：

| 工具 | 作用 |
| --- | --- |
| `list_kbs` | 列出当前运行可见的知识库 |
| `query_kb` | 按 `kb_id` 检索片段，返回 `kb_id`、`file_id` 和内容 |
| `find_kb_document` | 在指定文件中按关键词或正则定位内容 |
| `open_kb_document` | 按 `file_id` 分段读取解析后的文档 |
| `search_file` | 按文件名搜索可见知识库中的文件 |

工具名称、参数 Schema 与结构化结果直接来自 Knowledge MCP 的目录与响应，与其他托管 MCP 的装配路径一致。每次出站调用自动附带代表当前用户及会话选择的短期委托，无需在 Agent 的 MCP 字段添加本平台 Knowledge，也无需个人 OAuth 连接；管理页不能为该 server 配置共享凭据。

Knowledge 在每次工具调用处重新检查 `kb_id`、`file_id`、文件名与当前读取权限；模型提示词和 Agent 配置不能授予权限。关闭 Knowledge 时保留内置 server 身份，能力发现与执行按不可用处理。共享和 Agent 专属 Skill 通过 `mcp_dependencies: ["knowledge"]` 声明知识能力；这五个名称不能作为本地 `tool_dependencies`。鉴权、范围和存储边界见[知识库机制详解](../mechanisms/knowledge-base.md#agent-如何看到知识库)。

## 内置工具：网页搜索

`web_search` 支持 Tavily、DeepSeek 和豆包 Custom，返回标题、URL、短摘要、相关片段和可选正文；`web_fetch` 支持 Tavily Extract 和 DeepSeek Fetch，通过固定的供应商端点读取指定 HTTP(S) 网页的 Markdown 正文。豆包搜索摘要不能作为任意 URL 的抓取结果。分数保留供应商原始量程及 `score_max`，跨供应商比较前需核对含义；重要结论仍需回到来源核实。

超级管理员在“设置 → 网络搜索”分别选择搜索和网页抓取服务，编辑各供应商的 API Key。测试可以使用未保存的 Key 草稿，只影响该次测试；实际保存与环境变量的优先级、生效时机见[网络搜索配置](../advanced/configuration.md#网络搜索与网页正文)。凭据由服务端配置持有，不放进 Agent Context、Skill 或沙盒用户环境。

两个工具的注册名随代码稳定存在。每次构图时，运行时工具装配器读取有效供应商和凭据，只把已配置能力加入本轮 ToolNode；Skill 依赖也遵循这一规则。缺少配置的能力记录 `component=web_search` 或 `component=web_fetch`、原因和 `available=false`，本轮不绑定该工具。目录中出现工具名称只证明已注册，实际对话中的工具 schema 和结果才能证明本轮可用。

每次实际调用重新读取有效配置，页面保存可影响已绑定工具的后续调用；本轮尚未绑定的能力在后续 Run 构图时加入。显式选择的供应商缺少凭据或调用失败时返回工具错误，不跨供应商重试。正常搜索无命中返回成功的空列表；抓取没有可用正文时明确失败。

对话中的失败搜索显示失败图标、汇总和错误详情；成功的空列表显示“未找到相关搜索结果”。历史搜索结果只有非空顶层 `error`、外层仍标记完成时，页面也按失败展示，数据库历史内容保持原样。展示状态由[工具结果投影](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/session/model/toolCallProjection.js)拥有，额度失败与正常零结果通过注册工具回归和真实组件渲染分别验证。

实现由[工具入口](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/tools/builtin/web_search.py)、[网页服务](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/web/services.py)和[运行时装配](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/tools/runtime.py)共同拥有。[配置 HTTP 测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_web_tools_settings.py)检查权限、持久化与脱敏，[供应商协议测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/services/test_web_provider_protocol.py)核对归一化结果；固定重放不能替代实时供应商探针。

## 工具组装流程

内置 Agent 创建 Graph 时执行：

1. `prepare_agent_runtime_context` 按当前用户权限过滤工具、知识库、MCP、Skills。
2. `resolve_configured_runtime_tools(context)` 注册 Agent 配置和可见 Skill 依赖的可执行本地工具，统一加入基础工具集合，并加载配置的 MCP 工具。
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

MCP 工具可由 Agent 直接选择的已启用服务器提供，也可由已激活 Skill 的 `mcp_dependencies` 按需加载；预加载 Skill 从首轮开放本地工具和 MCP 依赖，普通 Skill 在读取 `SKILL.md` 激活后开放这些依赖。这一节只回答“工具从哪里来、谁决定模型可见”；服务器配置和启停归 [MCP 集成](./mcp-integration.md)，Skill 的创建、依赖与激活时机归 [Skills 管理](./skills-management.md)，资源选择的字段语义归[智能体配置](./agents-config.md)。

## 新增工具时检查

- 工具是否属于内置工具、知识库工具、MCP 还是 Skill；
- 是否需要在 `ToolNode` 注册，是否需要 Skill 激活后才让模型看见；
- 产生副作用时，执行处是否有用户、路径和资源权限校验；
- 错误是否以结构化结果返回，并保留可排查信息；
- 前端展示名称是否来自稳定元数据；
- 是否补充纯逻辑和真实 HTTP/文件链路的相应测试。
