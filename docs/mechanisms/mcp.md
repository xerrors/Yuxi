# MCP 配置、个人授权与运行身份

本页面向维护 MCP 接入和 Agent runtime 的开发者，解释配置、用户连接、工具对象和实际调用的边界。添加服务、填写参数和员工连接步骤见 [MCP 集成](../agents/mcp-integration.md)，企业平台登录见 [OIDC 配置](../advanced/third-party-auth.md)。

## 适用范围与机制边界

Yuxi 作为远程 MCP 客户端消费外部工具；知识库对外提供的只读 MCP 属于另一条服务端链路，见 [知识库 MCP 接入](../advanced/knowledge-mcp.md)。认证中心由部署方现有的 OIDC 服务提供，Yuxi 通过标准协议访问。

| 机制 | 无认证或共享 headers | 个人 Bearer / API Key | 个人 OAuth | 平台委托 |
| --- | --- | --- | --- | --- |
| 管理员配置 | 服务器、公共或共享 headers、启停和工具开关 | 服务器、认证方式；API Key 另声明请求头名称 | 服务器、Issuer、注册方式、scope、启停和工具开关 | 业务模块提供连接与 SDK Auth，管理员控制开关 |
| 使用的身份 | 匿名或管理员配置的共享身份 | 本次运行用户保存的静态凭证 | 本次运行用户的 OAuth 连接 | 业务模块取得的可信用户与资源范围 |
| 支持的传输 | SSE、Streamable HTTP | SSE、Streamable HTTP | Streamable HTTP | Streamable HTTP |
| 工具装配 | 每 Run 按需发现一次目录并复用工具 | 绑定可信 uid，按 Run 复用目录 | 绑定可信 uid，按 Run 复用目录 | 绑定可信 uid，按当前开关过滤 |
| 调用前检查 | 复查服务、工具与连接配置 | 还回读本人凭证、连接世代及配置修订 | 还回读本人连接，按期限刷新 | 复用通用执行策略，服务端复核资源权限 |

平台超管负责全局配置，普通管理员和用户管理自己的连接；超管个人连接也仅属于本人。平台登录会话、OAuth 客户端注册和 MCP 个人 token 分别保存，平台登录 token 不转发给资源服务。

`auth_mode` 与 `enabled` 是 Yuxi 的管理字段，分别表示认证方式和管理员启停，不是 MCP 协议字段。内部服务的 `delegation` 仅选择代码提供的认证模块，复用 SDK 原生 `httpx.Auth` 与现有工具 loader；默认认证方式和启停由内置代码装配，普通远程连接 parser 不接受这些内部默认值。内部自有凭据不等于完整的 OAuth 授权发现流程。

## 从连接到工具续答

静态个人凭证通过 `PUT /api/system/mcp-servers/{slug}/connection` 保存，输入只包含秘密及表单修订。MCP service 在服务锁内复核方式、启停和修订，锁定本人的连接，推进世代并加密提交。保存只表达“已配置”；个人测试复用本人出站认证做初始化与工具发现，不执行业务工具，不另建验证状态。Bearer 固定发送 `Authorization: Bearer <token>`；API Key 原样发送到服务声明的请求头，不进入 OAuth 发现或刷新。

OAuth 连接沿用以下流程：

1. **发起连接**：浏览器以当前平台用户请求 API。API 发现 MCP 授权元数据，固定个人连接世代和限时事务，再返回 PKCE 授权地址。
2. **授权回调**：用户在授权服务器登录并授权，浏览器携带 code/state 回到 API。API 消费事务并兑换目标资源的 token。
3. **发布连接**：回调只暂存一次性结果；浏览器回到 MCP 页面，用当前 JWT 确认归属后，API 才在 PostgreSQL 发布本人的 `connected` 连接。
4. **发现与调用**：worker 按可信 runtime uid 独立装配工具，每次 HTTP 出站回读并复查个人连接，再带个人 Bearer 访问 MCP；工具结果随后进入模型续答。

连接入口绑定当前用户、浏览器、服务器配置修订、连接世代和准确回调，使用 Authorization Code + PKCE S256。回调消费五分钟授权事务并兑换 token，但不会立即发布可用连接；浏览器回到 MCP 页面后，当前 JWT 用户必须在一分钟内确认一次性结果。错误浏览器、过期或重放事务、期间切换账号均不能完成绑定。

Agent 直接选择 MCP，或 Skill 激活后加载依赖，最终都以可信运行 uid 调用 `resolve_runtime_mcp_tools`。协作与定时运行继续使用各自已确定的执行用户；不从模型参数、Agent 创建者或全局管理员推导身份。MCP service 统一读取本次服务和本人连接；服务不存在、停用、未连接、修订失效或装配期间刷新被拒时排除该服务工具。除无认证、共享 headers 与个人认证外，平台托管的内置 server 可使用通用委托鉴权（`auth_mode=delegation`）：业务模块根据可信运行上下文提供 SDK Auth，凭据由该模块在 HTTP 请求时生成，通用 MCP 不解释业务资源选择；无身份共享装配拒绝该模式，管理工具发现和连接测试使用当前查看者身份；内置 knowledge 是当前使用方，见[知识库机制](./knowledge-base.md#agent-如何看到知识库)。Agent 与 Skills 消费可用服务和工具；个人连接状态与操作入口由扩展页面展示。普通聊天继续执行，依赖该服务的 Skill 工具同时在模型与执行边界关闭。单服务发现或调用失败只关闭本 Run 中该 MCP 及相关 Skill 工具，普通聊天继续。Context 保留本 Run 的不可用服务供授权门控，图状态的 `warnings: list[RuntimeWarning]` 保存警告，每项包含 `code`、`source` 和安全提示 `message`；现有状态增量和 checkpoint 回读将警告送到状态面板，刷新后仍可见，每次检查整体替换列表，下一轮能力恢复时写入 `[]` 清空，不弹出通知。凭据密钥缺失和授权失效使用固定安全提示，网络或未知协议错误使用连接失败提示，服务日志记录异常类型和安全原因。管理页面连接测试仍明确返回失败，worker 不等待浏览器交互。

个人工具对象保存服务器 slug、运行 uid 和配置修订。HTTP 授权适配器在每次出站时回读数据库，`tools/call` 还复查具体工具开关；token 仅用于实际请求。共享工具也在出站前核对加载时的地址、传输和 headers，以及当前认证方式和开关，运行中的旧共享对象不能绕过个人认证。Authorization 在执行前向 MCP service 批量读取本人可用服务与工具开关，嵌套 Skill 任意层的 MCP 不可用时关闭依赖工具；同一查询结果仅在当前调用内传递。Skills 只追加已激活 Skill 的 MCP 工具，本地工具从构图时的注册集合中筛选，嵌套连接依赖不触发未激活子 Skill 的工具发现。纯文本工具块在 Chat Completions 发送边界转换为字符串，原始结果和审计保持结构化；工具调用成功与真实模型最终续答成功需要分别验证，见 [工具文本协议决定](../develop-guides/decisions/implemented/2026-10-18-mcp-tool-text-wire.md)。

持久凭据使用基础设施提供的 `YUXI_ENCRYPTION_KEY`，MCP 只负责凭据载荷与领域错误；加密格式为 Fernet。`YUXI_PUBLIC_URL` 拥有部署公开来源，MCP 回调路径由代码固定，不从请求 Host 推导。

内部认证复用既有出站策略，旧工具对象不能绕过停用或配置变更。Knowledge 自己的 HTTP 客户端不读取环境代理。其产品目录和历史引用直接通过 SDK 调用 MCP，按当前用户与部署可用性授权，不受 Agent 工具启停开关限制；这些入口由 Knowledge 拥有，不向模型提供免检参数。

## 持久状态与并发失效

| 数据 | 拥有的事实 |
| --- | --- |
| `mcp_servers` | 全局连接配置、认证方式、API Key 请求头名称、脱敏 OAuth 配置、加密客户端 secret 与注册结果 |
| `mcp_connections` | 每个服务器与用户唯一的连接、加密凭据、期限、配置修订及世代 |
| `mcp_oauth_attempts` | 授权与确认阶段的短期事务；state 和浏览器只保存摘要，载荷加密 |

页面投影为 `not_required`（无认证、共享或平台委托）、`disconnected`、`connected` 或 `reconnect_required`。静态凭证的 `connected` 展示“已配置”，OAuth 展示“已授权”。切换 URL、传输、headers、API Key 请求头或授权配置会改变修订；发布变更的同一事务推进所有个人连接的世代并清空凭据，配置改回原值也不能恢复旧授权或旧回调。名称等展示字段不参与修订，服务及工具启停在调用边界单独检查，停用保留个人连接。

替换静态凭证只推进本人的连接世代；旧工具下次出站读取新值。远端 401 只撤销仍属于被拒绝世代的静态凭证，迟到的旧响应不能删除替换值；403 和网络错误不清除凭证。静态凭证不保存到期时间、不执行自动刷新。清除仅约束后续出站检查，已经发出的请求或已有工具结果不会回滚。

普通出站路径通过合并用户与服务查询，在连接行锁前后各检查策略，不执行远端 introspection。刷新仅在 token 临近过期时执行；远端调用结束后额外复查策略，轮换后的 refresh token 原子保存。等待行锁后和网络返回后复查当前策略，避免刷新期间撤权后继续出站。远端 401 只撤销实际被拒且仍为当前值的 token，旧请求不能覆盖新授权。

断开保留连接行并增加世代，清空本人凭据；旧回调和刷新不能恢复已断开的连接。所有锁、世代与持久状态的语义由 repository 和 OAuth service 拥有，前端状态不构成授权边界。

ARQ worker 在启动时及每分钟清理过期授权事务与待确认结果，新授权请求也保留顺带清理。OAuth service 在独立数据库事务中复用 repository 的过期查询，通过 `SKIP LOCKED` 跳过完成请求持有的结果，避免连接锁与结果锁反向等待；释放锁后由后续周期回收。清理保留未过期记录和长期连接。worker 停机、执行槽占满、数据库故障或持续行锁会延后物理删除，授权有效期仍在消费时校验；数据库错误由现有事务回滚与 ARQ 失败记录处理，恢复后的周期继续清理。取舍与验证见[临时记录清理决定](../develop-guides/decisions/implemented/2026-10-19-mcp-oauth-attempt-cleanup.md)。

模型前刷新按当前动态激活状态展开 Skill 闭包，向 MCP service 读取可用服务与工具开关；Authorization 统一判断配置选择与 Skill 依赖是否满足。Agent 与 Skills 不保存连接提示、工具开关投影或逐服务通知历史。运行中断开时，执行入口拒绝不可用 MCP 与依赖 Skill 工具，个人 HTTP 适配仍在实际出站前取得并复查凭据。

工具目录由本次 Run 的 Context 独占持有，直接配置的服务在构图时发现，Skill 服务在实际激活时发现；合法空目录同样复用，未激活依赖不触发发现。新 Run 创建独立 Context 并重新发现，服务配置、连接和工具开关仍在请求与出站边界复查。管理端读取工具列表时独立访问远端，包括已停用服务；工具开关来自当前禁用列表，读取不改变启停。已有 Run 的目录在下一 Run 更新。SDK 每次成功工具调用后的目录校验属于实际调用协议，不受运行目录复用影响。

## 权限与账号隔离

服务器配置修改在后端校验超级管理员，普通管理员也不能修改。连接、断开及回调确认只接受认证依赖取得的当前用户，不接受目标用户 ID。普通列表仅返回脱敏信息和本人状态；本人仍为 connected 或 reconnect_required 的停用个人服务保留清除入口，清除后隐藏。个人模式的公共 headers 不能包含 Authorization 或指定的 API Key 认证头，大小写冲突在配置边界拒绝。

平台身份拥有个人连接；资源身份由 MCP 根据 access token 验证并决定数据权限，两者无需对应同一企业员工。资源授权不会改变 Yuxi 平台角色。本地登录和企业登录均可发起个人 MCP 授权。

API 和 worker 共享持久化 Fernet 密钥；OAuth 凭据不进入浏览器返回、模型上下文或工具元数据。普通退出保留长期个人连接，Yuxi 账号停用后拒绝继续使用。部署参数和固定回调见 [个人 OAuth 部署](../agents/mcp-integration.md#部署个人-oauth)。

## 失败、恢复与观察边界

| 表现 | 优先核对 | 恢复方式 |
| --- | --- | --- |
| 个人授权提示未配置 | API/worker 密钥及固定回调 | 配置正确环境并重新创建容器 |
| 企业账号无法连接 | 资源授权的 issuer、scope、回调地址和主机 NTP | 纠正授权配置与网络，再重新授权 |
| 授权服务请求或元数据失败 | 资源元数据、端点来源、PKCE S256、协议、网络及供应商限制 | 纠正配置或服务协议；不回退共享 token |
| 需要重新连接 | 配置修订、远端 401、失效刷新或无法解密 | 用户重新连接，必要时修复密钥恢复流程 |
| 未连接但普通聊天可用 | 扩展页面的本人连接状态 | 在扩展 → MCP 连接后重新发起请求 |
| 工具列表或调用失败 | 服务启停、工具开关、个人连接与远端权限 | 保持原加载失败语义，按真实异常恢复 |
| 工具成功但模型续答失败 | 工具结果与供应商 wire 协议、最终 Run 错误 | 在模型适配边界修复，并补真实模型验收 |

HTTP 200、工具列表和固定模型结果各自只证明对应层级。真实授权需要浏览器和协议结果；真实资源隔离需要工具返回及持久审计；完整问答还需真实模型、最终 result Run 和页面正文。配置存在不等于远端工具可用。

## 源码定位与验证

| 修改的语义 | Owner 与入口 |
| --- | --- |
| 配置创建、启停、内置同步、共享/个人路径选择 | [service.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/service.py)，Agent/Skill 使用 `resolve_runtime_mcp_tools(..., uid=...)`，严格单服务发现入口仍保留 |
| OAuth 配置、secret 分离、密钥和修订规则 | [oauth_config.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/oauth_config.py)；共享远程连接字段由 [config.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/config.py) 校验 |
| 发现、注册和 token 请求 | [oauth_protocol.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/oauth_protocol.py)；此处处理协议，不决定平台用户归属 |
| 授权发布、刷新、断开和世代核对 | [oauth_service.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/oauth_service.py)，行锁及可见性查询由 [repository.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/repository.py) 拥有 |
| 每次 HTTP 取得个人凭据与工具装配 | [oauth_runtime.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/oauth_runtime.py)；远端工具发现由 [runtime.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/runtime.py) 拥有 |
| 连接按钮、回调确认和通用授权表单 | [前端 MCP 组件](https://github.com/xerrors/Yuxi/tree/main/frontend/src/modules/extensions/ui)及 [mcpOAuth.js](https://github.com/xerrors/Yuxi/blob/main/frontend/src/modules/extensions/model/mcpOAuth.js)；[HTTP 路由](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/api/routers/extensions/mcp_oauth.py)只适配请求和认证上下文 |

持久结构由 [models.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/models.py) 拥有，app-init 通过[当前基线初始化器](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/migrations/main.py)为新库建立完整结构，不保留历史增量脚本；已有数据库显式升级个人凭证字段，见[升级操作](../agents/mcp-integration.md#已有部署升级个人凭证字段)。修改协议、状态或权限时，以 [真实 HTTP/PostgreSQL 测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/services/test_mcp_oauth.py)证明隔离、重放拒绝、刷新轮换和断开竞争；[worker E2E](https://github.com/xerrors/Yuxi/blob/main/backend/test/e2e/test_mcp_personal_oauth_e2e.py)回读最终 Run 和审计。固定模型之外，真实浏览器加实际供应商模型仍是外部接入的独立验收；完整命令与测试层级见 [测试规范](../develop-guides/testing-guidelines.md)。

完整边界验收、收敛取舍与未验证范围见 [MCP 个人授权与运行边界](../develop-guides/decisions/implemented/2026-10-18-mcp-personal-oauth.md)。

个人静态凭证、无认证方式及现有 OAuth 的兼容取舍见 [个人凭证决定](../develop-guides/decisions/implemented/2026-11-06-mcp-personal-credentials.md)。
