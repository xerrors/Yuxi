# SubAgents 管理

SubAgent 是 Deep Agent 的可调用子智能体配置。你可以在管理界面维护多个子智能体，Deep Agent 在运行时会根据任务需要调用它们协作完成研究、评审等步骤。

这篇文档同时面向两类读者：

- 使用者：了解如何在 UI 中创建和维护 SubAgent。
- 开发者：了解后端数据结构、加载流程、调用链路和注意事项。

## 用户视角

### SubAgent 能解决什么问题

当任务复杂、需要并行信息收集或分工处理时，主智能体可以调用一个或多个 SubAgent 执行子任务。例如：

- 研究型子任务：聚焦检索和资料整理。
- 评审型子任务：对草稿进行结构和质量审查。
- 领域型子任务：使用指定工具和指定模型处理特定领域问题。

### 在哪里管理

在扩展管理页面中，切换到 Subagents 管理标签页即可进行增删改查。

主要字段说明：

| 字段 | 说明 |
|------|------|
| name | 唯一标识，创建后不可修改 |
| description | 子智能体用途说明 |
| system_prompt | 子智能体行为约束 |
| tools | 可用工具名称列表（从工具系统选择/输入） |
| model | 可选，子智能体模型覆盖 |
| is_builtin | 是否内置（内置项不可编辑、不可删除） |

### 配置建议

- name 建议语义化，例如 market-research-agent、fact-check-agent。
- system_prompt 尽量聚焦单一职责，避免一个 SubAgent 同时承担多个目标。
- tools 只保留该角色需要的最小集合。
- model 仅在该角色对模型能力有明确要求时设置；否则保持空值，使用默认策略。

### 使用方式与启用条件

SubAgent 配置保存在数据库中，但仅有配置并不会自动参与运行。要让子智能体真正可被调用，必须在 Agent 的 graph 中接入 `SubAgentMiddleware`。

可参考 Deep Agent 的实现：

```python
subagents_middleware = SubAgentMiddleware(
	default_model=sub_model,
	default_tools=search_tools,
	subagents=user_subagents,
	default_middleware=[...],
	general_purpose_agent=True,
)

graph = create_agent(
	model=model,
	middleware=[
		...,
		subagents_middleware,
		...,
	],
)
```

启用规则可以总结为：

- 未接入 `SubAgentMiddleware`：SubAgent 功能不生效（即使数据库里已配置）。
- 已接入 `SubAgentMiddleware`：SubAgent 默认启用，不需要额外开关。
- 可调用范围由 `subagents` 参数决定，通常来自数据库读取结果。

注意：当前 `SubAgent` 模型中没有独立的 `enabled` 字段，因此“是否启用”由是否纳入 middleware 的 `subagents` 列表决定。

## 开发者视角

### 数据模型

SubAgent 存储在业务库表 subagents 中，核心字段定义在：

- backend/package/yuxi/storage/postgres/models_business.py

模型转换方法：

- to_dict: 提供管理接口输出。
- to_subagent_spec: 提供 SubAgentMiddleware 所需结构。

### 后端分层

SubAgent 采用 Router -> Service -> Repository 的标准分层：

- 路由：backend/server/routers/subagent_router.py
- 服务：backend/package/yuxi/services/subagent_service.py
- 仓储：backend/package/yuxi/repositories/subagent_repository.py

职责划分：

- Router: 鉴权、请求参数校验、HTTP 错误语义。
- Service: 业务规则、缓存管理、工具解析、内置项初始化。
- Repository: 数据库读写与更新语义。

### 运行时加载与调用链

Deep Agent 构图时会动态加载 SubAgent：

1. 从数据库读取 SubAgent spec。
2. 将 tools 字符串列表解析为真实工具实例。
3. 传入 SubAgentMiddleware，参与执行。

关键代码：

- backend/package/yuxi/agents/buildin/deep_agent/graph.py
- backend/package/yuxi/services/subagent_service.py

在当前实现中，子智能体默认模型由 DeepContext.subagents_model 提供；单个 SubAgent 若配置了 model，会在子智能体级别覆盖默认模型。

### 内置 SubAgent 初始化

系统启动时会自动确保内置 SubAgent 存在：

- research-agent
- critique-agent

初始化逻辑：

- backend/package/yuxi/services/subagent_service.py 中的 _DEFAULT_SUBAGENTS 和 init_builtin_subagents
- backend/server/utils/lifespan.py 启动阶段调用

内置项保护规则：

- 内置 SubAgent 不可编辑。
- 内置 SubAgent 不可删除。

### API 概览

接口前缀：/api/system/subagents

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/system/subagents | 列表 |
| GET | /api/system/subagents/{name} | 详情 |
| POST | /api/system/subagents | 创建 |
| PUT | /api/system/subagents/{name} | 更新 |
| DELETE | /api/system/subagents/{name} | 删除 |

所有接口均要求管理员权限。

### 一致性与缓存

为了降低频繁查询数据库的开销，服务层维护了 subagent spec 缓存，并在创建/更新/删除后主动失效。

建议：

- 不要绕过 Service 直接写库，否则缓存不会自动失效。
- 如果新增导入/批量同步逻辑，务必在完成后调用缓存失效函数。

## 常见问题

### Q1: 为什么配置了 tools，但运行时没生效？

A: tools 字段保存的是工具名称。只有在运行时可用工具集合中存在同名工具，才会被解析并注入。请检查：

- 工具名称是否拼写正确。
- 该工具是否已在当前环境启用。
- MCP 工具服务是否已成功加载。
- Agent graph 是否已接入 `SubAgentMiddleware`。

### Q2: 删除后为什么主智能体偶尔还调用旧 SubAgent？

A: 通常与缓存和运行会话生命周期有关。确认删除接口成功返回后，新的图构建会使用最新配置。若是长会话，可重新开始一次运行。

### Q3: 内置 SubAgent 为什么不能直接改？

A: 内置项用于保证系统具备最小可用能力。若你需要完全自定义，建议新建自定义 SubAgent 并在提示词中引导主智能体优先使用。
