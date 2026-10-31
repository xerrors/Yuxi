# 本人身份的受限 Agent API Key

状态：proposed
类型：feature
Owner：backend/yuxi/modules/identity/services/public_auth.py

## 问题

个人集成需要以已有 Yuxi 用户身份访问 Agent 会话与 Workspace，同时避免获得完整产品 API 访问面。当前无 APP 的 `full` Key 使用本人身份；`agents` Key 则必须绑定 APP，在 Agents Public API 中使用独立终端用户身份。绑定 APP 的 Key 不传 `X-End-User-Id` 时仍使用 APP 默认终端用户，不会回到 Key 所有者身份。

本提案供产品与实现评审确认这一缺失组合；前置概念沿用 [Agents Public API 身份隔离决定](../implemented/2026-09-27-agents-public-api.md)。目标是新增本人身份的受限凭据用法，不实现 Personal 助理接口、默认助理绑定、跨用户委托或收件箱。

## 提案

### 实现方案

复用 APIKey 的 `user_id`、`access_level`、`app_id`，允许 `access_level=agents` 且 `app_id=null`。不新增 Key 类型、身份表或 Schema 字段。产品界面以“本人身份”和“APP 终端用户身份”解释作用域，选择后者才要求 APP；本人 Key 不接受 `X-End-User-Id`，包括与本人同名的值。

| 凭据 | Agents Public API 资源身份 | API 访问面 |
|---|---|---|
| `agents`，无 APP | Key 所属用户，`app_id=None` | 仅现有 `/api/v1/agents/**` |
| `agents`，绑定 APP | APP 指定或默认终端用户 | 保持现有 Agent API 限制 |
| `full` | 保持当前按 APP 绑定解析的行为 | 保持现有接口与用户权限检查 |
| `knowledge` | 保持当前知识能力身份行为 | 保持现有知识接口限制 |

创建与更新由 identity 的 API Key service 校验有效组合，`public_auth` 解析本人或 APP 身份，PublicAgentContext 生成原有 ActorScope；查询与执行继续沿用既有 service/repository 权限。API Key 不能取得 JWT 专有权限或管理角色。路径限制仍由认证依赖执行，不靠界面隐藏。前端 Key 创建、编辑说明与 API 文档同步更新；APP Key 的默认用户、隔离和终端身份声明契约保持不变。

本人 Key 与网页使用同一用户资源作用域，不隔离“由不同 Key 创建的会话”。限制的是 API 面，不是 Agent 的读写行为，也不限制为单个 Agent；工具权限和审批继续由现有运行机制负责。保留过期、禁用、撤销及创建幂等语义，撤销不额外承诺取消已经接收的运行。

已有 Key 不自动迁移。编辑时只有显式清空 `app_id` 才切换为本人作用域，省略该字段保持原值；切换不移动历史 APP 数据。此操作改变凭据可访问的数据范围，编辑界面须明确展示结果。后续 Personal 使用独立接口，其授权在该接口设计中另行决定；本提案不扩大现有 Key 到未来接口的权限。

实现时更新 [Public API 身份决定](../implemented/2026-09-27-agents-public-api.md) 中“agents 必须绑定 APP”的局部结论，其余隔离约束继续有效。

## 替代方案

- 继续使用无 APP 的 `full` Key：无需改动，但不能满足限制产品 API 访问面的目标。
- 新增 `personal` 权限类型：容易把身份作用域与未来助理产品绑定；现有字段已能表达本人身份和 Agent API 权限，新增枚举没有当前必要性。
- 新增通用 scope、外部应用委托与资源授权表：可表达更细权限，但超过当前本人调用需求。只允许本 Key 新建会话或限制单个 Agent 时，再单独设计资源授权。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 无 APP 的 agents Key 可创建、编辑并使用本人资源 | 只放宽表单，认证仍拒绝；生成影子用户 | identity service、PublicAgentContext | 扩展 `test_public_agents_key_boundary.py`，真实 HTTP 创建并用 JWT 回读；PostgreSQL 核对 uid/app_id 与 Project | 请求头声明另一终端用户；另一用户访问已知 Thread | Not run |
| 受限 Key 不访问产品管理与审计接口 | 路径限制放宽或继承 JWT 专有权限 | `api/dependencies/auth.py` 及各路由依赖 | 真实 HTTP 验证允许与拒绝接口，并回读无修改 | 持管理员所属 agents Key 调用 Key 管理、系统管理、内部审计接口 | Not run |
| APP 与已有 Key 行为兼容 | 不带 Header 被解析成本人；编辑省略 APP 意外切换 | `public_auth.py`、Key 更新服务 | APP 默认/显式终端用户集成测试；编辑前后数据库回读 | 两 APP 使用相同外部 ID；省略与显式 null 的更新对照 | Not run |
| 生命周期与幂等不回退 | 撤销后重放创建复活；过期仍能使用 | API Key service/repository | 扩展 `test_apikey_router.py`，HTTP 与数据库回读 | 禁用、过期、撤销后再次认证与重放 | Not run |
| 界面明确本人/APP 选择与切换后果 | 必填 APP 残留；编辑静默改变资源范围 | Key 管理前端 | 表单 unit、lint 与真实页面创建/编辑验证 | 切换模式、取消编辑、保留 APP 与清空 APP | Not run |

当前身份与权限结论为源码审查（Inspected），不代表提案行为已实现。实现后的上述用例按 [测试规范](../../testing-guidelines.md) 接入对应阻断检查，并由独立 Reviewer 核对数据隔离；未覆盖的权限负例不得用文档构建结果替代。

## 风险

本人 agents Key 能读取用户已有的可见会话，并经 Agent 使用已授权工具；它不是只读 Key，也不是只可提交任务的凭据。应在创建界面说明这一范围。APP 绑定的编辑会改变可访问的资源命名空间，必须保持显式操作和测试覆盖，不通过迁移静默改变已有凭据。

未来独立助理接口不应放入既有通配路径后直接宣称有独立授权；路由归属与 Key 权限需在其提案中明确。首版不承诺 Key 撤销立即中止已接收执行，也不增加该语义。
