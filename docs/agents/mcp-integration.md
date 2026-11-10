# 集成 MCP

MCP（Model Context Protocol）让智能体调用外部服务提供的工具。超级管理员在“扩展 → MCP”中维护服务目录；所有登录用户可以填写自己的 Token、API Key 或连接 OAuth 账号。Agent 可直接选择服务器，Skill 也可声明激活后加载的依赖。

点击 MCP 卡片打开简介与个人凭证。用户按管理员选择的方式填写凭证或完成授权；超级管理员在弹窗底部点击“管理 MCP”维护服务配置。未配置个人凭证不影响普通聊天，运行时暂不提供相关工具；配置后重新发起请求即可使用。卡片图标按服务标识与名称匹配，颜色按稳定标识映射。

## 选择接入方式

“接入方式”按凭证归属分组。个人方式由每位用户填写凭证或授权；共享方式由管理员填写整组认证请求头，所有用户共用其中的凭证。共享请求头直接显示在表单中，支持 Bearer Token 或 API Key 等服务文档要求的格式。

| 服务的认证要求 | 管理员填写 | 用户填写或操作 |
| --- | --- | --- |
| 无需认证 | `auth_mode=none`，无需凭证 | 无 |
| 个人 Bearer Token | `auth_mode=bearer`，固定使用 Authorization 请求头 | 只填原始 Token，系统添加 `Bearer ` 前缀 |
| 个人 API Key | `auth_mode=api_key`，填写 `api_key_header`，例如 `X-API-Key` | 只填原始 Key，系统原样发送到指定请求头 |
| 预注册 OAuth 客户端 | `auth_mode=oauth`、`registration=static`，填写 Issuer、Client ID，按服务方要求填写 Secret 和 scope | 点击授权，在服务方登录并同意 |
| 动态注册 OAuth | `auth_mode=oauth`、`registration=dynamic`，填写 Issuer 和 scope | 点击授权，客户端注册由后端完成 |
| 共享请求头（所有用户共用） | `auth_mode=headers`，直接填写共享请求头 JSON | 无；所有使用者共用管理员配置的服务身份 |

平台登录和 MCP 资源授权分别签发凭据；部署密钥、状态与调用隔离的实现见 [MCP 授权与运行身份](../mechanisms/mcp.md)。

平台自带 Knowledge 是内置 `knowledge` MCP server（见下节），由内置 `knowledge-base` Skill 的 `mcp_dependencies` 门控装配，每次调用使用内部短期用户委托，无需按本页添加服务器或连接 OAuth，管理页也不能为其配置共享凭据。对外客户端仍使用 `knowledge` API Key，见[知识库 MCP 接入](../advanced/knowledge-mcp.md)；内部身份与会话范围见[知识库机制](../mechanisms/knowledge-base.md#agent-如何看到知识库)。

选择个人 OAuth 前需确认服务支持发现协议、PKCE、回调地址和真实工具调用。个人 API Key 首期支持一个请求头，不支持查询参数、请求体或自定义前缀；Basic 用户名密码不在配置范围内。

## 支持的传输方式

| 传输方式 | 适用场景 |
| --- | --- |
| `streamable_http` | 新的远程 MCP 服务 |
| `sse` | 仍提供 SSE 接口的远程服务 |

管理接口只接受 `streamable_http` 和 `sse`。Yuxi 不支持 `stdio`，包括内置 MCP 和直接传入的运行时配置，也不接受 `command`、`args`、`env` 进程字段。

## 添加远程 MCP

在“扩展 → MCP”点击“新增服务”，填写稳定标识、名称、传输方式、URL，并显式选择接入方式。例如：

```json
{
  "slug": "custom-remote-mcp",
  "name": "Example MCP",
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "auth_mode": "none"
}
```

管理接口对应：

```http
POST /api/system/mcp-servers
Authorization: Bearer <superadmin-token>
Content-Type: application/json

{
  "slug": "custom-remote-mcp",
  "name": "Example MCP",
  "transport": "streamable_http",
  "url": "https://example.com/mcp",
  "description": "提供示例查询工具",
  "auth_mode": "none"
}
```

个人认证方式的公共 headers 只配置非敏感协议字段，不能再包含 `Authorization` 或个人 API Key 的认证头，大小写冲突会被拒绝。个人秘密由用户在卡片中单独填写；管理员只为明确需要共享服务身份的连接填写共享凭据。连接超时和 SSE 读取超时仍在高级连接选项中配置。

新增服务器立即启用。[创建智能体](./agents-config.md#创建智能体)的高级配置提供相同的独立创建入口，成功后选入 Context；取消或创建 Agent 失败时服务器仍保留。可以在 MCP 管理页点击“测试连接”确认工具发现结果，也可关闭服务器状态；状态关闭时记录仍保留，但不会进入运行时。

## 配置个人 Token 或 API Key

管理员选择“每名用户填写 Bearer Token”或“每名用户填写 API Key”。API Key 方式还需指定认证请求头名称；用户不填写服务地址或任意请求头。个人静态凭证支持 SSE 与 Streamable HTTP，生产环境要求 HTTPS。

用户打开 MCP 卡片，填写服务方提供的原始值并保存。页面显示“已配置”；点击“测试连接”才执行本人的工具发现，零个工具也是合法结果。保存不自动测试，测试不执行业务工具。已保存秘密不回填；右侧按钮组的下拉菜单提供替换和清除，替换时打开空输入框，取消保留原值。Token 不填写 `Bearer ` 前缀，API Key 不额外添加前缀。

个人凭证加密保存在后端，缺失、断开或失效时不回退共享身份。管理员测试个人服务也使用本人的凭证。服务停用后，有本人连接的卡片保留清除入口，禁止替换与测试；清除后该停用卡片隐藏。修改地址、传输、认证方式或认证头要求所有用户重新配置；仅启停服务保留凭证。HTTP 401 要求更新凭证，403 不自动清除。

已有共享服务可以由管理员显式切换为个人凭证或无需认证。切换表单提示并清空原共享请求头，不把共享秘密复制到个人连接；需要的非敏感协议头可以重新填写。通过管理 API 切换时，必须显式提交完整的 `headers`，留空使用 `{}`，不能省略并沿用旧共享头。静态凭证不自动刷新，用户从服务方取得新值后替换。

## 个人账号授权

超级管理员添加或编辑 MCP 时选择“每名用户 OAuth 授权”，填写授权服务器 Issuer、注册方式和授权范围。预注册方式填写 Client ID 和 Client Secret；修改时留空 Secret 保留同一客户端已有凭据。更换 Issuer 或 Client ID 会清除旧 Secret，必须重新配置。个人 OAuth 使用 Streamable HTTP，请求头不能同时包含共享 Authorization。

通过管理 API 更新时，`oauth_config` 作为完整配置校验，而不是逐字段补丁；只改其他服务器字段时可省略它。同一客户端省略或传 `null` 的 `client_secret` 保留已有 secret，显式空字符串清除；更换 Issuer、Client ID 或注册方式会清除旧客户端凭据和注册结果。

普通用户打开“扩展 → MCP”，点击服务卡片和“连接”，在授权服务器确认后回到 MCP 页。页面从 PostgreSQL 读取本人连接状态；“需要重新连接”表示配置改变、远端拒绝 token 或刷新失败。“断开”删除本人凭据，旧回调不能恢复它。普通退出 Yuxi 保留长期连接，账号停用后不能继续调用；断开只撤销本地使用能力，远端应用授权可在供应商账号设置中另外撤销。

Yuxi 登录与 MCP 资源授权各自独立，本地账号也可连接自己的资源账号，无需平台企业身份绑定。每个连接只属于发起授权的 Yuxi 用户；资源服务决定该资源账号可访问的数据。企业浏览器会话可以复用，MCP 使用独立签发的资源 token。

## 规范与接入边界

Yuxi 的所有个人 OAuth MCP 共用发现、客户端注册、授权码、PKCE、token 存储/刷新和 HTTP Bearer 适配，不按具体供应商切换实现。不同服务通过地址、Issuer、注册方式和 scope 表达差异。注册方式可以不同，身份 token 也不必都采用 JWT。

预注册客户端、Keycloak/JWT 与个人资源隔离是常见的自建服务组合。MCP 资源服务本身只验证专用 access token，其他标准客户端无需登录 Yuxi 也可调用。

| 能力 | 当前支持与限制 |
| --- | --- |
| 授权码、PKCE S256、resource、Bearer | 通用流程已实现；资源服务还需验证其专属 audience |
| 资源及授权服务器发现 | 当前依赖单一发现位置，未完整实现规范要求的 path/root 与 OAuth/OIDC 后备顺序 |
| scope challenge | scope 由管理员填写；尚未完整解析 challenge scope 和运行时 insufficient_scope，不自动扩大权限 |
| 客户端注册 | 支持静态预注册及 DCR；尚未内置 CIMD 文档托管。静态示例不要求所有服务支持动态注册 |
| 信任策略 | 固定 Issuer、端点同源和禁止重定向是平台额外限制，可能拒绝其他合规服务的部署拓扑 |
| 传输 | 正式接入要求授权端点 HTTPS、回调 HTTPS 或 loopback |

因此不能将“同一授权逻辑”理解为“任意 OAuth MCP 都已兼容”。

## 适配一个新的个人 OAuth MCP

符合当前发现与授权协议的服务通过配置接入，不需要新增后端供应商分支。管理员手动填写服务地址与 OAuth 配置，所有服务使用同一授权流程。

1. 确认资源 URL。远端提供受保护资源 metadata，其 `resource` 与配置 URL 完全一致，`authorization_servers` 包含配置 Issuer；重定向或尾斜杠差异也需要核对。
2. 确认授权服务器 metadata。使用 OAuth 授权服务器发现，须声明 PKCE S256，客户端认证方式须与实际注册匹配。
3. 选择客户端注册方式。预注册应用登记部署中的固定 MCP 回调并保管 Client Secret；动态注册服务需要接受 Authorization Code、refresh token 和 `token_endpoint_auth_method=none`，注册结果由平台持久保存。
4. 在超管 MCP 表单配置 Issuer、注册方式和 scope。Issuer 是稳定的授权服务器标识，不是带 state、nonce 的临时浏览器登录 URL。个人 OAuth 不能再填写共享 `Authorization`。
5. 用户连接后发现工具、执行一次真实读取，再验证两个不同用户的结果、断开后旧工具拒绝及真实模型最终回答。配置成功或“已连接”不能代替这些结果。

以下是自建企业服务的管理接口请求体示例；域名、客户端和 secret 均为占位值，secret 通过私有部署渠道填写：

```json
{
  "slug": "company-attendance",
  "name": "企业个人考勤",
  "transport": "streamable_http",
  "url": "https://attendance.example.com/mcp",
  "headers": {},
  "auth_mode": "oauth",
  "oauth_config": {
    "issuer_url": "https://auth.example.com/realms/company",
    "registration": "static",
    "client_id": "yuxi-attendance",
    "client_secret": "<private-client-secret>",
    "scopes": "attendance:read"
  }
}
```

动态注册使用 `registration=dynamic`，不预填静态 Client ID/Secret。企业资源服务自行验证 access token 的签名或可信验证结果、目标资源和业务 scope，查询条件从已验证员工身份取得，不接受模型提交的员工参数。SSO 登录本身不会授予另一项业务的资源权限。

资源和授权服务器可以位于不同来源。当前实现要求资源 metadata 与 MCP 同源，授权、token、注册端点与配置 Issuer 同源；OAuth 网络请求不跟随重定向。服务不满足这些条件时先核对实际协议与网络，不能用放宽来源、关闭 PKCE 或转发平台登录 token 代替适配。

协议支持的变更在 `oauth_protocol` 及其 HTTP 负向测试中闭合；业务授权状态在 `oauth_service`/repository 中闭合。完整职责和验证入口见 [源码定位与验证](../mechanisms/mcp.md#源码定位与验证)。企业示例的接入结果见 [实现决定](../develop-guides/decisions/implemented/2026-10-18-mcp-personal-oauth.md)。

## 让智能体使用 MCP

在智能体配置的 MCP 字段中显式选择要直接添加的服务器。该字段默认不添加服务器：

- 未显式配置或显式清空时，不直接加载 MCP 服务器；
- 显式选择后，只使用所选且当前可用的服务器；
- MCP 工具仍会在执行处使用当前用户身份和服务器配置；
- 管理员可以在 MCP 详情页单独禁用某个工具。

共享 Skill 的 `mcp_dependencies` 声明所需服务器。Skill 激活后按需加载这些服务器的工具；预加载 Skill 从首轮加载，均无需在 Agent 的 MCP 字段重复选择。服务器必须由管理员启用，禁用的服务器不会提供工具。

MCP 配置从 PostgreSQL 读取。共享 headers 和个人凭证工具都在每次 Run 按需发现一次目录并在运行内复用，新 Run 重新发现。个人凭证每次出站重新读取当前用户的有效值。修改地址或授权配置后需要重新连接；停用服务、停用工具和断开个人连接都会阻止旧工具对象的后续调用。

## 部署个人 OAuth

API 与 worker 从同一部署环境读取以下变量，配置后重新创建对应容器；静态个人凭证也使用相同加密密钥，OAuth 另需公开回调来源：

| 变量 | 用途 |
| --- | --- |
| `YUXI_ENCRYPTION_KEY` | 通用 Fernet 凭据密钥，必须持久保留；缺失时个人授权明确不可用 |
| `YUXI_PUBLIC_URL` | 浏览器可达的站点来源，如 `https://yuxi.example.com`；MCP 回调固定为 `/api/system/mcp-servers/oauth/callback` |

可以在安全终端使用 `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` 生成密钥，写入私有部署环境，勿提交仓库或发送给模型。更换密钥后旧凭据无法解密，需要重新连接；备份和恢复数据库时同步保管密钥。Secret 只写入加密列，管理接口只返回是否已配置。

OAuth 元数据只能使用超管配置的 MCP 来源与授权服务器来源，跨来源重定向会失败。企业内网服务需要浏览器与 API/worker 都能访问准确地址。地址、授权服务器或客户端配置变化后，用户需要重连。数据库结构与新库初始化边界见 [系统架构](https://github.com/xerrors/Yuxi/blob/main/ARCHITECTURE.md)。

## 已有部署升级个人凭证字段

业务 Schema v2 包含 `api_key_header`。此升级入口只适用于已包含个人 OAuth 表的 0.8 business v1 数据库，不提供从 0.7.x 升级的能力。升级前停止新任务提交，等待活动任务结束，并保管数据库备份与部署加密密钥。

生产环境先构建或拉取目标版本镜像，确认 PostgreSQL 健康且 `storage-init` 已成功。停止 API/worker 后，使用目标镜像的一次性进程执行迁移，再运行应用初始化并启动服务；迁移不依赖能够启动的新版本 API：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container stop api worker
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container run --rm --no-deps app-init uv run --no-sync --no-dev python -m yuxi.migrations.mcp_personal_credentials
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up --no-deps --force-recreate --exit-code-from app-init app-init
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up -d --no-build
```

每条命令成功后再执行下一条。开发环境可将上述命令的 `docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container` 替换为 `docker compose`。升级后按[部署验证](../advanced/deployment.md#_4-验证部署)检查就绪状态，并确认已有 MCP 连接和个人凭证可用。

升级只增加可空字段并登记业务 Schema v2，保留已有服务、共享请求头和个人 OAuth 凭证。空的新字段不改变旧 OAuth 配置修订。新库由 app-init 直接建立 v2；运行进程只检查版本，不自动修改结构。回退旧进程版本需显式处理 Schema 版本兼容。

## 内置远程 MCP

内置 DeepWiki 使用 `deepwiki-official` 标识，通过 `https://mcp.deepwiki.com/mcp` 提供 Streamable HTTP 服务，无需认证即可查询公开 GitHub 仓库。详见 [DeepWiki 官方文档](https://docs.devin.ai/work-with-devin/deepwiki-mcp)。

内置 `knowledge` 指向本平台 `/api/mcp`，默认启用，连接配置由代码管理。它使用平台委托鉴权（`auth_mode=delegation`，见[授权与运行身份](../mechanisms/mcp.md)）：工具调用按当前用户与会话选择签发短期委托，无需填写共享凭据。管理端工具发现和连接测试使用当前查看者身份。关闭 `YUXI_KNOWLEDGE_ENABLED` 后，内置身份和管理员开关保留，但服务不进入可用能力集合；重新开启仍按原开关提供工具。范围与权限见[知识库机制](../mechanisms/knowledge-base.md#agent-如何看到知识库)。

开发者在 [`builtin.py`](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/builtin.py) 的 `BUILTIN_MCP_MANIFEST` 中维护清单：

```python
BUILTIN_MCP_MANIFEST = {
    "mcpServers": {
        "deepwiki-official": {
            "type": "http",
            "url": "https://mcp.deepwiki.com/mcp",
            "extra_data": {
                "name": "DeepWiki",
                "description": "查询公开 GitHub 仓库的文档、架构与代码",
            },
        },
    },
}
```

清单连接字段支持 `type`（`http`、`sse`）或 `transport`（`streamable_http`、`sse`）、HTTP URL、headers、timeout 和 sse_read_timeout。同时声明 type 与 transport 时必须一致。展示字段 name、description 放在 `extra_data`，名称缺省时使用清单键。管理表单使用相同清单结构解析，再转换为上面的平铺管理接口字段；`extra_data` 不发送给远端 MCP 客户端。

`app-init` 部署任务把代码拥有的内置连接定义同步到 PostgreSQL，该阶段只处理本地定义；远端建连和工具发现由测试或运行时加载触发。运行时按对应鉴权方式装配连接，从数据库读取启停与禁用工具状态。内置 `knowledge` 默认启用且同时受部署开关限制，其他新内置 MCP 默认停用，管理员启用后才参与运行；页面不能修改其连接配置。同步保留管理员的启停与禁用工具列表，失败会阻止部署初始化完成。

## 常用管理接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/system/mcp-servers` | 查看服务器；普通用户只得到脱敏基础信息 |
| `POST` / `PUT` | `/api/system/mcp-servers`、`/{slug}` | 添加或修改远程 MCP |
| `PUT` | `/api/system/mcp-servers/{slug}/status` | 启用或停用服务器（对应页面上的添加/移除状态） |
| `PUT` | `/api/system/mcp-servers/{slug}/connection` | 用户保存或替换自己的 Token / API Key |
| `DELETE` | `/api/system/mcp-servers/{slug}/connection` | 用户清除自己的个人凭证或断开 OAuth |
| `POST` | `/api/system/mcp-servers/{slug}/test` | 使用本人凭证测试个人连接；其他方式仅超管可测试 |
| `GET` | `/api/system/mcp-servers/{slug}/tools` | 查看工具；停用服务也会建连，但不因此启用 |
| `PUT` | `/api/system/mcp-servers/{slug}/tools/{tool_name}/toggle` | 启用或禁用单个工具 |

接口字段和错误响应以实例 Swagger 为准。

管理端工具列表和连接测试都会按当前配置建连，停用服务也能看到目录，查看不会改启停或工具开关。建连或工具发现失败时，管理检查、Agent 配置工具装配与 Skill 依赖加载均明确失败，运行不会把故障当作成功的空工具列表继续执行。服务器正常返回零个工具仍是合法结果；未启用服务器不参与运行加载。运行日志包含组件、服务器标识、错误类型与不可用状态。

## 源码定位与验证

[MCP service](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/service.py)拥有配置用例、内置同步与启用状态，[客户端运行时](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/extensions/mcp/runtime.py)拥有远端工具发现，[授权中间件](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/agents/runtime/middlewares/authorization.py)在实际调用边界复查服务器与单工具状态。连接成功只证明当时可发现工具，远端资源权限仍由远程服务执行。

[HTTP 集成测试](https://github.com/xerrors/Yuxi/blob/main/backend/test/integration/api/test_mcp_router.py)覆盖管理权限和配置，[MCP service 单测](https://github.com/xerrors/Yuxi/blob/main/backend/test/unit/services/test_mcp_service.py)覆盖内置同步与加载错误；外部服务器的当前可用性需在目标部署中另行测试。
