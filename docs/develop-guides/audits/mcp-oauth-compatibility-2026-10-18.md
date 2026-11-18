# MCP OAuth 兼容性核对（2026-10-18）

本文面向 MCP 集成维护者，记录 MCP 2025-11-25、Notion 官方客户端说明与当前 OAuth 实现的只读对照。前置知识是 OAuth 授权码、PKCE 与 MCP Streamable HTTP。目标是确定可小范围修复的协议差距及真实远程验证候选。范围不含供应商预设、凭据配置、动态注册写操作或用户授权。源码核对是本日实现快照，后续变更以 [MCP 机制 Owner](../../mechanisms/mcp.md)和源码为准。

当前核查结论是。Yuxi 已实现授权码、S256、资源参数、个人 token 隔离，但发现后备与挑战处理仍有协议差距，不能声明完整兼容 MCP 2025-11-25。考勤走相同客户端主链路，使用超管配置的固定 issuer，可作为预注册示例。独立 YuxiAuth 示例的 scope 分类已修正。8 项测试通过，并包含真实 ASGI HTTP 协议验证。远程个人授权和考勤链路已通过[主站 E2E](./mcp-main-e2e-2026-10-18.md)，无效 scope 的远端复测未执行。本文记录核查与验证证据。Yuxi 客户端 fallback、scope、DCR 认证方式与实验 HTTP 例外尚未修复。早期 localhost 实验曾有成功授权和只读读取。该历史结果不计入最终部署验收。主站 Notion 的已定位失败是动态注册拒绝公网 HTTP 回调，用户要求删除配置并停止联调，后续没有恢复配置或继续联调。

## 协议差距与修复边界

以下结论来自 [MCP 2025-11-25 Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) 与源码的对照，属于 `Inspected`。

| 位置 | 核对结果 | 小范围建议与负向 oracle |
| --- | --- | --- |
| `oauth_protocol.discover` | 无 challenge 时只请求带 MCP path 的资源元数据，缺少根路径后备；授权服务器只请求一类元数据 | 资源按 path、root 顺序；AS 按 OAuth insertion、OIDC insertion、OIDC appending 顺序。重放首选 404、次选合法 JSON；恶意 issuer、跨来源与缺少 S256 仍须拒绝 |
| `oauth_protocol.discover`、`oauth_service.start` | challenge 只用正则提取 `resource_metadata`，忽略 Bearer scheme、scope、资源 `scopes_supported`；授权 scope 只取管理员字段 | 解析 Bearer challenge 并承认所需 scope；若管理员策略不允许该 scope，明确拒绝并解释。不要静默忽略。无 challenge scope 时采用资源 scope。用不同的资源/AS scopes 证明不会请求 AS 管理权限 |
| `oauth_service.register_client`、`oauth_protocol.token_request` | DCR 固定请求 `none`，只保存 id/secret；兑换时依据 AS 支持列表重新挑认证方式 | 保存并遵循注册确认的认证方式；`none`、basic、post 分别有协议重放。DCR 请求 none 后返回 secret 不应使兑换自行变为 basic |
| `oauth_runtime.PersonalMCPAuth` | 任意 401 将当前 token 标记重新连接；403 没有结构化的 scope 处理 | 401 保留当前 token 比较保护；403 `insufficient_scope` 明确提示所需 scope 和重新授权，保留与普通 403 的区别。自动权限升级需要另定产品行为 |

授权和兑换均发送 `resource=server.url`，符合资源绑定要求；不得因为供应商示例省略该参数而删除。严格资源相等与固定 issuer 是当前部署信任边界；资源元数据迁移到根路径时仍需校验其 `resource` 与实际配置匹配，不能为了通过 discovery 任意接受其他资源。当前限制发现端点同来源比协议更严格，属于已知部署边界。本次候选的已核实授权端点均未要求扩大它。

Client ID Metadata Documents 是 2025-11-25 推荐能力，当前 static/dynamic 配置没有托管元数据文档功能。已有静态 URL client_id 可由部署自行托管；完整 CIMD 产品支持涉及发布文档与回调契约，超出最小修复。

## 强制要求、推荐能力与部署策略

| 类别 | 精确条文与当前判断 |
| --- | --- |
| MUST：发现与资源绑定 | [资源元数据发现](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#protected-resource-metadata-discovery-requirements)要求支持 challenge 与 path→root 后备，并把 challenge scope 作为当前请求的权威需求；[AS 发现](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#authorization-server-metadata-discovery)要求 OAuth/OIDC 多位置顺序；[resource 参数](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#resource-parameter-implementation)要求授权与兑换均发送。当前 resource 已覆盖，前两项未完整覆盖 |
| MUST：安全传输与 S256 | [通信安全](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#communication-security)要求 AS endpoints HTTPS、回调 HTTPS 或 localhost；[授权码保护](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#authorization-code-protection)要求验证 PKCE 元数据并采用 S256。当前 S256 已覆盖；实验 allowlist 放行公网 HTTP 是显式测试例外，不属于标准兼容行为 |
| MUST 与 SHOULD：scope 错误 | [错误状态](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#error-handling)将无效 token 对应 401、权限/scope 不足对应 403；[scope challenge](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#runtime-insufficient-scope-errors)推荐 Bearer insufficient_scope + scope + resource_metadata；[step-up](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#step-up-authorization-flow)为 SHOULD。客户端当前没有 step-up；不能把这项推荐直接写成所有实现必须自动扩大授权 |
| SHOULD/MAY：注册 | [CIMD](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#client-id-metadata-documents)是 SHOULD；启用后文档 HTTPS、字段与 redirect 校验是条件性 MUST。[预注册](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#preregistration)是 SHOULD，[DCR](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#dynamic-client-registration)是 MAY。缺少内置 CIMD 托管是推荐能力差距，DCR 非必选；考勤静态预注册是有效简化 |
| Yuxi 策略：固定 issuer、同源 | [授权服务器选择](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#authorization-server-location)由客户端拥有，PRM 可列多个 issuer。Yuxi 要求超管预先固定其中一个 issuer，并限制 PRM 与资源同源、token/register 与 issuer 同源，是额外部署约束；规范没有要求这些端点同源。平台与资源账号各自独立，客户端不核对两个账号是否对应同一员工 |

固定 issuer 策略可以保留；不能用它解释缺少标准发现后备。若未来支持跨来源元数据或 AS endpoints，需要在真实网络信任边界定义允许来源与 SSRF 控制；当前应直接说明兼容范围，不把同源限制描述为 MCP 标准规则。

## 考勤示例的通用部分与不足

考勤由独立 YuxiAuth 仓库的 `examples/attendance/server.py` 拥有。FastMCP 只作为资源服务器，`EmployeeTokenVerifier` 验证 RS256 签名、issuer、audience、有效期与 subject；`AuthSettings` 声明资源 URL、issuer、`attendance:read` 与资源校验。查询工具只接受日期，SQL 始终使用已验证 issuer + subject，模型不能提交员工 ID 覆盖它。该结构可以作为接入外部身份中心的简单示例。JWKS 路径拼接 `/protocol/openid-connect/certs` 是 Keycloak 适配。它并非所有 OAuth 服务的通用发现方式。

`keycloak/attendance-realm-template.json` 预注册 confidential client、固定回调、S256 与 consent；`attendance:read` scope 的 audience mapper 给 access token 写入 `${ATTENDANCE_RESOURCE}`，不写入 ID Token。`realm-template.json` 是平台登录模板，未代替考勤资源 audience。这种 scope→audience 映射证明受控 Keycloak 示例可签发目标资源 token；它没有证明任意 `resource` 参数均被 AS 动态校验。模板 `sslRequired=none` 服务于实验装配，正式参考部署仍需 HTTPS。

PRM 与 HTTP challenge 来自 Dockerfile 固定的 `mcp==1.30.0`。核对该版本 [FastMCP 装配源码](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/fastmcp/server.py)和[资源元数据路由](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/auth/routes.py)。SDK 生成 path PRM，内容包含 resource、authorization_servers、scopes_supported，并给认证 middleware 提供其 URL。不需要示例自行再实现 OAuth 授权服务器。

示例的 verifier 验证 token 真实性与资源身份，并把 scopes 交给已有 `AuthSettings.required_scopes` 判定。[SDK v1.30.0 Bearer middleware](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/auth/middleware/bearer_auth.py)对无效 token 返回 401 invalid_token，对签名有效但 scope 不足返回 403 insufficient_scope。源码已移除一个分支。该分支在 verifier 内提前把缺 scope 变成 `None`。该 SDK 的 challenge 包含 resource_metadata 和 error_description，但没有推荐的 scope 参数，仍属于进一步可改进项。

`examples/attendance/test_server.py` 包含真实 JWT 算法、错误 issuer/audience 与员工 SQL 隔离测试，并通过 ASGI HTTP 边界核对 PRM resource/issuer/scope、无 token→401+resource_metadata、合法 token 缺 scope→403 insufficient_scope、错误 audience→401，以及两个员工各自 initialize、tools/list、tools/call 后读回各自记录；请求只使用标准 Bearer 与 MCP 头，没有 Yuxi 专用身份头。主任务执行结果为 8 passed。恢复缺 scope 提前拒绝分支后，HTTP 403 oracle 实际收到 401 并失败，还原修正后再次 8 passed。此证据覆盖本地应用协议与持久记录。补充证据见[主站 E2E 验收](./mcp-main-e2e-2026-10-18.md)，覆盖远程 HTTPS、真实 Keycloak 授权及考勤调用。无效 scope 的远端复测仍未执行。示例说明由 YuxiAuth 的 `docs/mcp-example.md` 拥有。用户授权客户端复用 Yuxi 通用 static 模式，平台账号与资源账号分别授权。

## Notion 的核实结果

[Notion 官方客户端指南](https://developers.notion.com/guides/mcp/build-mcp-client)确认 Streamable HTTP 地址为 `https://mcp.notion.com/mcp`，支持 DCR 与 PKCE。指南内部有两处不能直接作为实现 oracle：资源发现 TypeScript 示例构造根 `/.well-known/oauth-protected-resource`，说明文字却列 `/mcp/.well-known/oauth-protected-resource`；PKCE 元数据缺失时示例仅告警，而 MCP 2025-11-25 要求拒绝继续。实现应遵循版本化 MCP 规范及真实 challenge，保持 S256 验证。

通过 web 工具回读的 [Notion AS 元数据](https://mcp.notion.com/.well-known/oauth-authorization-server)包含 issuer `https://mcp.notion.com`、`/authorize`、`/token`、`/register`，支持 S256、none/basic/post 与 CIMD。其 AS `scopes_supported` 含 `default` 和 `ema_issuer:manage`，这不构成自动请求管理 scope 的依据；规范的初始 fallback 数据源是资源元数据。

子 Agent 的宿主 Python 只读探针返回 Cloudflare 1010、403，资源元数据也未通过该子 Agent 的 web 工具读取；该结果只说明此探针的边缘网络拒绝。主任务此前在 API 容器内已真实获得 Notion GET 的 401 challenge 与 metadata GET 200，并核对元数据。因此发现阶段已有成功证据。两处网络结果的执行环境不同，最终用户授权仍未完成，不能声称 Notion 已接通。

主任务的真实注册联调另已取得 `https://mcp.notion.com/register` 返回的 HTTP 400、`invalid_redirect_uri`，原因是公网 HTTP 回调。远端要求 HTTPS 或 loopback HTTP。该注册结果解释本轮连接失败，独立于本调研环境的 Cloudflare 拒绝。用户明确选择删除已配置 Notion，后续不继续注册、授权或变更回调部署。

## 真实远程验证候选

以下端点用于人工配置和后续验证，不进入内置配置。DCR 可发现不等于任意回调已被接受，最终还需账号、浏览器 consent 和实际 MCP 结果。

| 候选 | 官方入口与注册条件 | 当前实现支持判断 |
| --- | --- | --- |
| Linear | [官方说明](https://linear.app/docs/mcp)：`https://mcp.linear.app/mcp`，交互流程支持 DCR；需要 Linear 账号并授权。只读可配置 `read` scope 或 `/mcp/readonly` | `Inferred`：核心 none+S256 流程吻合；显式 scope 可用。401、PRM 和 AS 已真实回读；授权、refresh、工具调用 `Not run` |
| Sentry | [官方入口](https://mcp.sentry.dev/)：`https://mcp.sentry.dev/mcp`；支持按 org/project 加路径，需要 Sentry 账号。公开 [AS 元数据](https://mcp.sentry.dev/.well-known/oauth-authorization-server)提供 `/oauth/register`、`/oauth/token`，none/basic/post、S256 | `Inferred`：标准 DCR 基础吻合；资源发现尚未确认。直连被 Cloudflare 1010 拒绝，未尝试注册与授权 |
| Atlassian | [官方当前入口](https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/)：`https://mcp.atlassian.com/v2/mcp`；需要账号与站点权限，[OAuth 说明](https://support.atlassian.com/atlassian-ai-gateway/docs/configure-oauth-2-1/)包含回调 domain allowlist 条件 | `Inferred`：v2 PRM 指向独立且带 path 的 issuer，当前 OAuth insertion 与同来源 token/DCR 可覆盖；需配置正确 issuer 和 scopes。未执行注册、consent、refresh 或工具调用 |

Linear 的无授权 GET 实际返回 401，challenge 指向 [path PRM](https://mcp.linear.app/.well-known/oauth-protected-resource/mcp)，scope 为 `read write`。回读 JSON 的 resource 为 `https://mcp.linear.app/mcp`、issuer 为 `https://mcp.linear.app`。[AS 元数据](https://mcp.linear.app/.well-known/oauth-authorization-server)包含 DCR `/register`、S256 及 none/basic/post。根 PRM 的 resource 是根 URI，不能当成 `/mcp` 的等价资源绕过校验。

Atlassian v2 的无授权 GET 实际返回 401，challenge 指向 [v2 PRM](https://mcp.atlassian.com/.well-known/oauth-protected-resource/v2/mcp)。回读 JSON 的 resource 为完整 v2 URL，issuer 为 `https://auth.atlassian.com/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3`，并公布资源 scopes。[该 issuer 的 OAuth insertion 元数据](https://auth.atlassian.com/.well-known/oauth-authorization-server/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3)实际返回 S256、DCR `/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3/dcr/register`、token `/oauth/token`，支持 none/basic/post/private_key_jwt。MCP 主机根 AS 元数据另有旧 issuer，不能替代 v2 PRM 的授权信任根。

## 证据与未验证范围

`Inspected`：读取四个 OAuth 源码文件、考勤 server/test/Dockerfile、两份 tracked Keycloak 模板、SDK v1.30.0 官方源码、上述官方页面与公开 JSON；Python `urllib.request` 无凭据 GET 回读 Linear/Atlassian。回读内容包括状态、challenge 和元数据字段。公开元数据只证明发现接口结构，不证明 DCR、token 或工具可用。

主任务提供的本地考勤验证：`examples/attendance/test_server.py` 8 passed，包含上述 ASGI HTTP 协议断言。scope 分支恢复实验使 403 oracle 按预期失败，还原后再次 8 passed。本子 Agent 回读修改后的 server/test，未重复执行主任务测试。

`Not run`：本子 Agent 未执行外部 DCR POST、token 兑换、refresh、工具调用或账号授权；主任务已取得 Notion API 容器发现与注册拒绝结果。Linear/Sentry/Atlassian 未完成真实授权，本地考勤测试未替代远程部署验证，不发布端到端兼容结论。


## 本次参考示例交付与部署边界

YuxiAuth 的示例修正、标准 HTTP 测试和参考说明已提交至独立私有仓库，提交为 `69e7bdf`。源代码的 scope 拒绝交由 SDK，实际认证和资源校验保留。主任务确认 8 项示例测试及 10 项配置/迁移测试通过，恢复原缺陷后 403 oracle 收到 401 并失败。独立 Reviewer 已复跑。

远程认证入口恢复后的主站浏览器授权、真实刷新和考勤调用已通过[主站 E2E 验收](./mcp-main-e2e-2026-10-18.md)。该验证没有重建考勤容器，scope 错误分类修正是否已在远端生效仍未通过无效 scope 的 HTTP 结果证明。源码提交、本地 ASGI 结果与成功读取不能替代这项负向协议证据。其他服务的 OAuth 配置保持不变，Notion 未恢复。
