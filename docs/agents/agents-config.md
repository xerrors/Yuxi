# 配置智能体

本页是智能体配置参考，说明页面上的字段如何进入一次运行。新增智能体后端的代码结构见[开发智能体后端](./agent-backend-development.md)；只使用现成智能体时，从[快速开始](../intro/quick-start.md)开始。

## 配置模型

| 对象 | 负责什么 |
| --- | --- |
| `Agent` | 数据库中的智能体资源，保存名称、slug、私有／共享状态、共享范围和持久化配置 |
| `BaseAgent` | 代码中的后端类型，声明 `context_schema` 和 `get_graph()` |
| `BaseContext` | 配置字段和运行时输入的 Schema |
| `config_json.context` | 当前 Agent 保存的配置值 |
| Graph / middleware | 根据 Context 组合模型、工具、文件和扩展能力 |

内置会话使用 `ChatbotAgent` 执行后端。[协作会话](./session-cooperation.md)默认继承派发方配置，也可以显式选择另一个获授权的 Agent；每个成员独立保存消息历史与执行状态。

## 创建智能体

在“智能体 → 新增智能体”填写名称和基本信息，可以附带单 Skill ZIP。ZIP 成为这个 Agent 的专属操作指南；省略 ZIP 时不创建空 Skill，之后可以在“专属 Skill”中添加。包限制与编辑方式见 [Skills 管理](./skills-management.md#agent-专属操作指南)。

Agent 默认私有。管理员在基本信息中勾选“共享此 Agent”后配置读取和管理范围；取消勾选保留本地权限草稿，创建仍为私有。

点击底部的“高级配置”可指定模型、系统提示词、工具、MCP、共享 Skill 与其他 Context 项，使用与编辑智能体相同的表单。高级配置可跳过，两个区域都可直接创建；返回“基本信息”保留配置草稿。未修改的字段沿用后端默认值。刷新候选项保留草稿；切换后端会清除高级配置，请重新选择。

MCP 和共享 Skill 选择旁边提供独立创建入口：MCP 仅超级管理员可创建，共享 Skill 管理员可上传 ZIP 创建。新资源发布后刷新候选项并加入当前选择；“选择全部”模式继续包含新增资源。这些资源独立生效，取消 Agent 或 Agent 创建失败时仍保留，可以在资源管理页维护。远程连接配置见 [MCP 集成](./mcp-integration.md#添加远程-mcp)。

点击“创建”后，基本信息、Context 与专属 Skill 在同一事务中生效。ZIP、依赖、权限或配置校验失败时不留下 Agent 或专属包，表单保留输入供修正。创建响应中断或服务端结果不明确时，本次表单禁止直接重试；取消并刷新智能体列表，核对是否已经创建。

## 私有与共享定义

新建 Agent 默认 `visibility=private`。普通用户可以建设自己的私有 Agent，管理员还可显式创建共享 Agent。私有所有权仍遵循字段角色限制。详情接口返回 `can_manage`、`can_run`、`can_share` 和 `can_transfer`，界面按这些能力提供操作。

有管理权限的管理员可在已有私有 Agent 的基本信息中勾选“共享此 Agent”，配置范围后保存。转换保留同一 ID、slug、所有者、Context 和专属 Skill；共享定义继续编辑权限范围。共享转私有不在此入口提供，所有权转移沿用专用接口。完整授权与失败条件见[资源权限](../mechanisms/resource-permissions.md)。

## 配置页面从哪里来

`BaseContext` 的 dataclass 字段和 metadata 会生成配置项描述，前端不再维护一份独立字段清单：

```text
Context 字段
  → get_configurable_items()
  → Agent 详情接口
  → AgentRuntimeConfigForm
  → config_json.context
```

metadata 可以定义展示名称、说明、控件类型、选项和角色权限。运行期 ID、owner 和内部派生值应隐藏，不作为用户配置。

`metadata.auth` 只限制修改权限：`admin` 字段允许管理员和超级管理员修改，`superadmin` 字段只允许超级管理员修改。普通用户读取有权访问的智能体时，可以读到这些已保存值，运行也使用这些值；不可修改的字段不进入其编辑表单，后端明确拒绝越权提交的字段；省略字段保留已保存值。`auth` 不提供字段保密能力，Context 配置不得存放凭据。

例如管理员将 `max_execution_steps` 设置为 50，普通用户新建该智能体的会话时，`recursion_limit` 也为 50。会话创建时固化配置快照；已有会话的后续输入与 Run 继续使用该快照，修改智能体配置后需新建会话才能使用新值。知识库、Skills 等资源仍按运行用户的访问权限筛选。

## 基础字段

| 字段 | 作用 |
| --- | --- |
| `system_prompt` | 智能体角色和行为说明 |
| `model` | 主模型；留空时使用系统默认模型 |
| `tools` | 可使用的内置工具 |
| `knowledges` | 可检索的知识库范围 |
| `mcps` | 直接添加到 Agent 的已启用 MCP 服务器 |
| `skills` | 选择共享和内置 Skill；个人 Skill 始终可用 |
| `preload_skills` | 从首轮请求加载完整说明和依赖的 Skill 子集 |
| `summary_threshold`、`summary_keep_messages` | 上下文压缩的唯一压力阈值（单位 K，默认 256，即约 262144 token）和摘要后保留消息数；换算规则见[上下文压缩](../mechanisms/context-compression.md) |
| `summary_prompt`、`summary_tool_result_token_limit` | 摘要提示词和工具结果预览上限 |
| `max_execution_steps` | 单次运行的 LangGraph 执行步数上限，默认 2000，对应 `recursion_limit` |
| `model_retry_times` | 模型重试次数，默认 2 |

工具审批模式由会话或定时任务选择，不属于智能体配置。省略时使用 `default`；历史智能体 Context 中的审批值不参与新会话配置，已有会话和 Input 的审批快照继续生效。

遇到 `Recursion limit ... reached` 时，管理员可在智能体的高级配置中调整“最大执行步数”并保存，再新建会话。已保存的显式值优先于默认值；保存为 300 的智能体需要将该字段改为 2000。已有会话继续使用创建时的配置快照。提高上限允许更长的执行；如果任务反复执行相同步骤，还需检查循环的停止条件。

## 资源选择语义

资源字段 `tools`、`knowledges`、`skills`、`mcps` 和 `preload_skills` 接受 `"all"` 或字符串数组，本页是这组契约的唯一 Owner。`"all"` 表示每次运行使用当前用户可访问的全部资源，包括之后新增的资源；`[]` 表示不选择；非空数组表示固定范围。新写入拒绝 `null`、未知字符串和非字符串数组成员。

创建时省略字段使用其默认值：工具、知识库和 Skill 为 `"all"`，MCP 和预加载 Skill 为 `[]`。界面的「选择全部」保存 `"all"`；逐项勾选保存固定数组，即使勾选了当前所有选项也不会自动转换成全部模式。「清空全部」保存 `[]`。

各资源的附加规则：

- MCP 选择只控制直接加载的服务器；已激活 Skill 声明的 MCP 依赖仍按需加载。
- Skill 选择器只列共享和内置 Skill；运行时自动合并当前用户全部个人 Skill，`skills=[]` 只关闭共享选择。Skill 的激活时机见 [Skills 管理](./skills-management.md)。
- 预加载候选项限于已选且可访问的共享 Skill；`preload_skills="all"` 在每次运行预加载该范围及其授权依赖的完整说明，并从首轮开放依赖工具。`skills=[]` 时预加载全部仍得到空列表，个人 Skill 继续按需读取正文。

这些字段只会缩小当前用户已经拥有的权限；运行时如何消费解析结果见 [Agent 运行时上下文](../mechanisms/agent-runtime.md)。

### 共享智能体与编辑保存

共享智能体保存完整的期望选择，每次运行再与当前操作者的可访问资源取交集，运行不会改写保存的选择。例如创建者选择 10 个 Skill，委托管理员只能访问其中 5 个，委托管理员运行时生效 5 个，保存名称或模型后仍保留原有 10 个选择。

编辑页只提交修改过的配置字段；通过 API 更新 `config_json.context` 时，省略字段保留原值。修改非空资源列表时，后端保留当前管理员不可访问的既有引用，并拒绝新增无权访问的引用；保留的引用维持原相对顺序，新选择追加到末尾。「清空全部」移除所有引用（包括不可访问项），切换到「选择全部」则整体替换固定选择范围。界面会显示当前不在可选范围内的既有选择数量。

## 定时运行

智能体支持用户定时任务（Beta）：为智能体配置 5 段 cron 和 IANA 时区后，worker 到期自动在独立 Session 中触发一次运行，也支持手动立即运行；停机错过的多个周期合并为一次，已有未完成执行时跳过并记录。任务的定义、触发与恢复边界见 [Agent 运行时上下文](../mechanisms/agent-runtime.md#用户定时-agent)。

## 自定义 Context 字段

需要让用户配置额外行为时，扩展 Context，让后端和前端沿同一 Schema 工作：

```python
from dataclasses import dataclass, field
from yuxi.modules.agents.runtime import BaseContext

@dataclass(kw_only=True)
class MyAgentContext(BaseContext):
    response_style: str = field(
        default="concise",
        metadata={
            "name": "回答风格",
            "description": "控制回答的详细程度",
            "type": "string",
            "options": ["concise", "detailed"],
        },
    )
```

新增字段会影响保存结构、配置表单和运行期 Context。字段默认值、权限和选项变化时，同时更新相关测试和文档。

## 运行时入口

配置如何与用户身份、权限快照、Workdir、Memory 和 PostgreSQL checkpoint 组合，见[Agent 运行时上下文](../mechanisms/agent-runtime.md)。工具可见性和执行授权见[工具系统](./tools-system.md)，上下文压缩见[上下文压缩机制](../mechanisms/context-compression.md)。

## 相关页面

- [开发智能体后端](./agent-backend-development.md)
- [中间件](./middleware.md)
- [Skills 管理](./skills-management.md)
- [会话协作](./session-cooperation.md)
- [沙盒机制详解](../mechanisms/sandbox.md)
