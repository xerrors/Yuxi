# 开发插件

插件把一项功能的说明、专属工具和调用逻辑放在一起，随 Yuxi 部署发布。每个插件类继承 `YuxiPlugin`，智能体选择它后，平台为普通业务对象创建独立的 `PluginMiddleware` 适配器。本文以“统计文本长度”为例，完成编写、注册和启用。

开发前需要了解 Python 和 LangChain 工具。插件属于经过审阅的服务端代码；调用数据库或外部服务的工具仍需在执行处检查用户权限。

## 1. 创建插件目录

在 `backend/yuxi/modules/extensions/plugins/` 下为代码插件创建一个直接子目录。

```text
text_metrics/
  plugin.py
  skills/
    text-metrics/
      SKILL.md
```

`plugin.py` 拥有功能实现，`skills/` 保存随功能发布的说明、脚本和参考文件。可参考同级的 `knowledge/` 和 `abridge/` 插件。

在 `skills/text-metrics/SKILL.md` 写入：

```markdown
---
name: text-metrics
description: 统计用户提供文本的字符数。
---

# 文本统计

调用 count_text_characters 统计原文字符数，结果包含空白与标点。
向用户说明统计口径，并展示工具返回的数值。
```

目录名与入口 Skill 标识保持一致。插件自带的说明、脚本和参考文件都放进这个入口包。例如，把配套说明写在 `skills/text-metrics/skills/word-count/SKILL.md`，在入口正文用相对链接 `[分词规则](skills/word-count/SKILL.md)` 引用。用户取得入口读取权后可按需读取整个目录，撤销入口权限会撤下整包投影。配套说明不需要登记独立 Skill 或再授权。

## 2. 实现插件

在 `plugin.py` 中定义专属工具和插件类：

```python
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool

from yuxi.modules.extensions.plugins.base import YuxiPlugin


@tool
def count_text_characters(text: str) -> int:
    """统计原文字符数，包含空白与标点。"""
    return len(text)


class TextMetricsPlugin(YuxiPlugin):
    """提供文本统计说明、工具与回答要求。"""

    plugin_id = "text-metrics"
    name = "文本统计"
    description = "精确统计文本字符数并解释统计口径。"
    entry_skill = "text-metrics"
    skills = ("text-metrics",)
    mcps = ()
    tools = (count_text_characters,)

    async def on_model_call(self, request, handler):
        """激活后为每次模型调用补充统计口径。"""
        blocks = list(request.system_message.content_blocks) if request.system_message else []
        blocks.append({"type": "text", "text": "报告字符数时说明结果包含空白与标点。"})
        request = request.override(system_message=SystemMessage(content=blocks))
        return await handler(request)
```

`load_plugin` 成功后返回入口 Skill 的路径和正文，模型按入口文件的位置解析文内相对路径。`on_model_call` 可为激活后的请求补充动态提示，适用于需要每轮生效的规则；固定操作说明放在 Skill 中即可。

需要包装工具调用时实现 `on_tool_call(self, request, handler)`。它接收当前工具请求，可以在 `await handler(request)` 前后处理。该钩子会看到经过本插件包装的所有工具调用；只处理某个工具时，先检查 `request.tool_call["name"]`。

两个钩子均由平台适配器检查激活状态。取消、审批中断与执行错误应继续向外传播。实例在构图时创建，跨 Run 或恢复所需事实应使用已有持久化能力，不能依赖实例变量保留。

## 3. 显式注册

编辑 `backend/yuxi/modules/extensions/plugins/registry.py`，导入类并加入 `REGISTERED_PLUGINS`：

```python
from yuxi.modules.extensions.plugins.text_metrics.plugin import TextMetricsPlugin

REGISTERED_PLUGINS = (KnowledgePlugin, AbridgePlugin, TextMetricsPlugin)
```

元组顺序就是插件装配顺序，智能体配置只选择其中哪些生效。多个包装钩子按此顺序进入，调用返回时按相反顺序退出。

注册会检查插件 ID、名称、入口和专属工具的冲突。插件自带 Skill 使用现有内置内容同步流程发布，来源是已注册插件旁的 `skills/` 目录；相同 Skill 标识在多个源码目录出现时初始化失败。只创建目录而未登记插件，其代码和配套说明都不会启用。

`plugins/deployed/*.json` 用于接收历史 Skill 依赖的离线迁移结果。新增包含业务代码的插件使用上述 Python 注册方式。

## 4. 发布并启用

将代码与配套目录一起部署，通过现有 `app-init` 初始化流程同步 Skill，再启动同一版本的 API 和 worker。采用日常部署流程操作；已有 Schema 升级要求见 [Skills 升级说明](./skills-management.md#从运行依赖升级)。

在智能体配置的“插件”中选择“文本统计”并保存，然后创建新会话：

1. 模型首先看到文本统计插件的功能摘要。
2. 模型调用 `load_plugin`，参数为 `{"plugin_id": "text-metrics"}`。
3. 加载返回入口说明，并记录 `activated_plugins`。
4. 下一次模型请求获得 `count_text_characters`，插件的调用钩子开始执行。

用户可以通过加载结果、工具调用和最终字符数确认功能生效。插件未出现时，先检查注册及内容同步；加载失败时，根据返回信息检查入口说明访问权、辅助 Skill、MCP 连接或工具重名。

## 资源如何组织

| 资源 | 开发方式 |
| --- | --- |
| 入口与私有配套内容 | 入口位于 `skills/<entry_skill>/SKILL.md`；私有文件放在同一个包内，在入口正文按需引用 |
| 共享辅助 Skill | 直接在 `skills` 中引用已存在的共享标识，例如 `chat-widgets` |
| 专属工具 | 在插件目录中实现，通过类属性 `tools` 声明；名称须避开其他插件及平台工具 |
| 专属 MCP | 在插件类的 `mcps` 中提供 `BuiltinMCP`，平台从该插件收集连接定义。Agent 激活插件后才获得对应工具 |
| 共享 MCP | 在 `mcps` 中填写可直接选择的服务标识，沿用当前用户连接与权限 |
| 通用工具 | 由智能体的工具配置选择 |

插件目录只发布一个顶层入口包。已有自定义部署若在插件旁并列放置多个包，升级前将私有包移入入口目录，更新入口引用并从类的 `skills` 中移除其独立标识。曾经独立发布的旧条目沿用原权限，管理员应停用旧条目并核对已有投影。仍要独立共享的 Skill 应移至普通内置 Skill 目录，保留标识和独立权限。其他插件可以引用这些共享 Skill。

插件拥有专属 MCP 的地址和认证装配，MCP 管理页维护可用状态和单工具开关。

例如，业务插件可以在自己的 `plugin.py` 中声明一个无需凭据的只读连接：

```python
from yuxi.modules.extensions.mcp.config import BuiltinMCP

# 放入插件类的 mcps 属性
mcps = (BuiltinMCP(slug="reports", manifest={"type": "http", "url": "https://reports.example/mcp"}),)
```

需要按用户委托时，参考内置知识库插件的 `delegation` 实现。平台在当前用户和会话范围内发现工具；用户启用插件一次即可使用获准的能力。

## 中间件与接口边界

`PluginLoaderMiddleware` 是平台公共加载器，负责目录、`load_plugin` 和激活记录；注册的每个 `YuxiPlugin` 业务对象由独立的 `PluginMiddleware` 加入中间件链。

插件通过 `on_model_call`、`on_tool_call` 包装请求。插件对象不继承 LangChain `AgentMiddleware`。平台适配器只调用这两个钩子，独立判定激活；业务对象的框架同名方法、状态声明均不进入图。图结构与 checkpoint schema 由平台维护。

工具与资源使用类属性声明，注册校验、授权检查和平台适配器都读取同一份类声明。构造函数可初始化本图的业务对象，但实例变量不决定工具集合，也不保存跨 Run 状态。只有操作说明的功能使用普通 Skill，例如内置 `image-gen`；需要实际工具、MCP 或调用逻辑时再使用插件。

## 如何验证

验证至少覆盖：注册后进入候选目录；启用后独立装配；激活前模型看不到专属工具；加载后正文、工具和业务钩子同时生效；强制提前调用工具被拒绝；撤销访问后已有激活记录无法绕过权限。

参考 `backend/test/unit/middlewares/test_plugins_middleware.py` 中的真实 LangChain 图测试。涉及数据库、MCP 或文件操作时，继续补充对应边界的集成验证。运行时权限与恢复机制见 [插件激活与恢复](../mechanisms/agent-runtime.md#插件激活与恢复)。
