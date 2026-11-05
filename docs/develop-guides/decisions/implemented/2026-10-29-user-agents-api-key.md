# 本人身份的受限 Agent API Key

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/services/public_auth.py

## 问题

个人集成需要以已有 Yuxi 用户身份访问 Agent 会话与 Workspace，同时避免取得完整产品 API 访问面。仅提供无 APP 的 `full` Key 和绑定 APP 的 `agents` Key，不能同时满足本人资源身份与受限 API 面。

## 决策

### 实现方案

复用 APIKey 的 `user_id`、`access_level`、`app_id`，允许 `access_level=agents` 且 `app_id=null`。不新增 Key 类型、身份表或 Schema 字段。identity 的 Key service 接受该组合，`public_auth` 返回 Key 所属用户，PublicAgentContext 继续生成原有 ActorScope。API 认证依赖限制访问面，业务 service/repository 继续执行资源可见性与执行权限。

| 凭据 | Agents Public API 资源身份 | API 访问面 |
|---|---|---|
| `agents`，无 APP | Key 所属用户，`app_id=None` | 仅现有 `/api/v1/agents/**` |
| `agents`，绑定 APP | APP 指定或默认终端用户 | 原有 Agents API 限制 |
| `full` | 按 APP 绑定解析本人或终端用户 | 原有接口与用户权限检查 |
| `knowledge` | 原有知识能力身份 | 原有知识接口限制 |

本人 Key 与网页使用同一用户资源范围，不隔离不同 Key 创建的会话；它不限制为只读、单个 Agent 或仅此 Key 新建的资源。本人 Key 不接受任何 `X-End-User-Id`，包括空值和本人 UID。API Key 不取得 JWT 专有权限或治理角色；模型与工具审计仍要求超级管理员 JWT。工具权限和审批继续由现有运行机制负责。

更新省略 `app_id` 保持原绑定，显式 `null` 才切换为本人身份；切换不移动历史 APP 数据，不自动迁移已有 Key。前端创建与编辑使用“本人身份”和“APP 终端用户身份”，仅后者要求 APP；保存前展示身份切换后的数据范围与历史数据保留后果。API 使用与管理契约由 [API Key 接入](../../../advanced/api-key-integration.md)拥有。

本记录部分取代 [Agents Public API 身份隔离决定](./2026-09-27-agents-public-api.md)中“agents 必须绑定 APP”的局部结论，其余 APP 默认用户、隔离与终端身份声明约束继续有效。

## 替代方案

- 无 APP 的 `full` Key：不需要实现，但开放的产品 API 面超出个人集成需求。
- 新增 `personal` 权限类型：把身份范围与未来助理产品绑定，现有字段已经能表达当前组合。
- 通用 scope、外部委托或资源授权表：提供更细粒度控制，但当前没有仅限单个 Agent 或仅新建会话的需求。

## 后果

本人 `agents` Key 能读取用户已有的可见会话，并通过 Agent 使用已授权工具。界面说明其非只读范围；后端执行路径白名单，不以 UI 隐藏作为授权。APP 身份切换会改变凭据可访问的数据命名空间，必须由显式编辑触发。

过期、禁用、撤销与创建幂等语义保持不变；重放创建不恢复凭据有效性。撤销阻断后续认证，不承诺取消已经接收的运行。

本决定不提供 Personal 助理接口、默认助理绑定、跨用户委托或收件箱，不授权未来独立接口。未来接口设计需要单独明确路由归属与 Key 权限。

## 验证

`test_public_agents_key_boundary.py` 通过真实 HTTP 创建与 JWT 回读，并从 PostgreSQL 核对 human UID、空 APP、Project 归属；本人 Key 与 JWT 读取相同 Workspace 字节，不同 Key 共享同用户会话。负例使用另一用户的 Key 读写已知会话、伪造终端身份、访问 Key/系统管理与内部审计。`test_public_end_user.py` 覆盖两 APP 同外部 ID 的独立身份，默认 APP 用户的 Project/Workspace 隔离仍由现有边界测试证明。

`test_apikey_router.py` 从数据库对照省略与显式清空 APP，确认历史会话不迁移、重新绑定恢复原作用域、原创建意图重放不重置编辑结果；禁用、过期与撤销后重新认证均拒绝，重放不能恢复 Key。表单测试完整编译 SFC，验证本人/APP 选择、条件必填、取消编辑、明确清空 APP 与请求 ID 重试。

实际执行结果与环境限制随交付记录；测试分层与阻断入口由[测试规范](../../testing-guidelines.md)和现有 API 集成、前端工作流拥有，不以文档构建替代权限负例。
