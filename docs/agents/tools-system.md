# 工具系统

Yuxi 的工具系统基于注册机制，支持多种工具类型的动态组装。

## 工具注册机制

Yuxi 的工具系统采用 `@tool` 装饰器注册机制，核心位于 `backend/package/yuxi/agents/toolkits/registry.py`。

### @tool 装饰器

```python
from yuxi.agents.toolkits.registry import tool

@tool(category="buildin", tags=["示例"], display_name="示例工具")
def example_tool(text: str) -> str:
    """示例工具：返回处理后的文本"""
    ...
```

装饰器参数：
- **category**: 工具分类，用于分组，例如 `buildin`、`knowledge`、`debug`
- **tags**: 标签列表，用于前端展示
- **display_name**: 显示名称（给人看的名字）
- **icon**: 图标名称（可选）

### 自动发现

导入 `toolkits` 包时会自动触发注册：

```python
from yuxi.agents.toolkits import buildin, debug  # 触发模块内 @tool 装饰器执行
```

`toolkits/__init__.py` 会导入 `buildin` 与 `debug` 模块；知识库工具由内置 `knowledge-base` Skill 的依赖显式注册，而不是作为所有 Agent 的默认工具。

## 工具分类

### 内置工具 (buildin)

| 工具 | 说明 |
|------|------|
| `ask_user_question` | 向用户发起交互式提问 |
| `ocr_parse_file` | 将 uploads、outputs 或 workspace 中的 PDF、Office 或图片文件转换为 Markdown |
| `present_artifacts` | 展示 Agent 沙盒 outputs 目录下的产物文件 |
| `install_skill` | 从沙盒路径或 Git 来源安装当前用户私有 Skill，并激活当前主智能体会话；子智能体禁用 |
| `tavily_search` | Tavily 网页搜索（需配置 `TAVILY_API_KEY`） |

Qwen-Image 生成能力已迁移为内置 Skill `image-gen`。模型调用与图片下载在 Agent 沙盒中完成，生成后的图片保存到 `/home/gem/user-data/outputs/`，再通过 `present_artifacts` 展示。

### 知识库工具 (kbs)

知识库工具使用 `@tool(category="knowledge")` 注册，并通过内置 `knowledge-base` Skill 的 `tool_dependencies` 按需加载。`get_common_kb_tools()` 仍可用于直接获取完整工具列表：

```python
from yuxi.agents.toolkits.kbs import get_common_kb_tools

kb_tools = get_common_kb_tools()
# 返回: [list_kbs, get_mindmap, query_kb, find_kb_document, open_kb_document]
```

| 工具 | 说明 |
|------|------|
| `list_kbs` | 列出用户可访问的知识库 |
| `get_mindmap` | 获取知识库的思维导图结构 |
| `query_kb` | 按 `kb_id` 检索内容，返回结构化的 `kb_id`、`file_id` 与命中片段 |
| `find_kb_document` | 在已知文件内按关键词或正则定位内容 |
| `open_kb_document` | 按 `file_id` 分段打开知识库文档（默认窗口 1800 行） |
| `search_file` | 按文件名在指定或全部可见知识库中搜索文件 |

## 工具组装

工具组装在 Graph 创建阶段完成。内置 Agent 会先调用 `prepare_agent_runtime_context` 过滤当前用户可用资源，再调用 `resolve_configured_runtime_tools(context)` 加载已配置工具：

1. **基础工具**：从 `context.tools` 中按名称筛选
2. **MCP 工具**：根据 `context.mcps` 加载 MCP 服务器工具
3. **Skill 依赖工具**：由 `SkillsMiddleware` 在 Skill 激活后按需追加，包括 `knowledge-base` 绑定的知识库工具

```python
from yuxi.agents.context import prepare_agent_runtime_context
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools

context = await prepare_agent_runtime_context(context, user=current_user, db=db)
tools = await resolve_configured_runtime_tools(context)
```

## Skills 集成

Skills 与工具是两种不同的扩展机制。工具是具体的功能实现，而 Skills 是包含提示词、工具依赖和元数据的完整技能包。通过 `context.skills` 配置 Skills 时，对应的技能文件会被挂载到沙盒的 `/home/gem/skills/<slug>/...`，智能体可以通过读取 SKILL.md 来了解如何使用这些技能。

关于 Skills 的详细机制，请参阅 [Skills 管理](./skills-management.md)。
