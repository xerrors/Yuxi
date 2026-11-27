# 接入 OIDC 登录

本页供部署管理员配置单个企业 OIDC 认证中心，前提是已初始化 Yuxi 管理员、PostgreSQL 和 Redis。OIDC 默认关闭，本地密码登录始终保留。没有企业认证中心时，可以使用任意符合 OIDC 标准的测试 IdP 验证配置。

## 注册认证中心客户端

创建 confidential OIDC 客户端，启用授权码流程和 PKCE S256，关闭隐式授权与密码直授。记录 Issuer URL、Client ID 和 Client Secret，并登记准确地址：

| 设置 | 地址 |
| --- | --- |
| 登录 Redirect URI | `https://yuxi.example.com/api/auth/oidc/callback` |
| Back-channel Logout URL | `https://yuxi.example.com/api/auth/oidc/backchannel-logout` |

认证中心必须能访问 back-channel 地址，并发送带签名的标准 logout token。Keycloak 启用 Backchannel Logout Session Required。若不支持此协议，Yuxi 仍可主动撤销当前本地会话，无法接收认证中心会话结束通知。管理员停用员工时需同时结束其认证中心会话，或停用 Yuxi 账号。

Yuxi 要求 discovery 的 issuer 与配置精确一致，使用 JWKS 中的 RS256/384/512 或 ES256/384/512 公钥。ID token 的受众仅允许当前 client，若有 azp 也必须匹配。UserInfo 的 sub 必须与 ID token 一致。认证中心应提供 discovery、JWKS、授权、token 和 UserInfo 端点。

## 配置与启停

通过受保护的 `.env` 或 env file 配置 API。密钥不得提交到仓库或输出到日志：

```dotenv
YUXI_ENV=production
OIDC_ENABLED=true
OIDC_PROVIDER_NAME=企业统一登录
OIDC_ISSUER_URL=https://auth.example.com/realms/company
OIDC_CLIENT_ID=yuxi
OIDC_CLIENT_SECRET=<private-client-secret>
OIDC_REDIRECT_URI=https://yuxi.example.com/api/auth/oidc/callback
```

生产模式拒绝 HTTP issuer、回调和协议端点。浏览器到 Yuxi 的访问必须使用 HTTPS。浏览器到认证中心及 API 到认证中心的访问也必须使用 HTTPS。cookie 为 HttpOnly、SameSite=Lax，HTTPS 回调时附加 Secure。隔离开发模式允许 HTTP。公网只有 HTTP 时保持 `OIDC_ENABLED=false`，登录页自动隐藏第三方登录区域，个人空间只展示文件。

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `OIDC_SCOPES` | `openid profile email` | 必须包含 openid |
| `OIDC_AUTO_CREATE_USER` | `true` | 已验证但无映射的外部身份自动开普通用户；关闭时拒绝未开通身份 |
| `OIDC_DEFAULT_DEPARTMENT` | `OIDC用户` | 新用户默认部门，1–50 字符 |
| `OIDC_NAME_CLAIM` | `name` | 展示名称来源 |
| `OIDC_USERNAME_CLAIM` | `preferred_username` | 展示名称的备用来源，不用于绑定本地账号 |
| `OIDC_FETCH_DEPARTMENT_INFO` | `false` | 仅开户时读取 UserInfo 的部门字符串 |
| `OIDC_DEPARTMENT_CLAIM` | `department` | 部门字段，去空白并截为 50 字符 |
| `OIDC_FORCE_PROMPT_LOGIN` | `false` | 为 true 时每次要求认证中心重新登录 |
| `OIDC_AUTHORIZATION_ENDPOINT` / `OIDC_TOKEN_ENDPOINT` / `OIDC_USERINFO_ENDPOINT` | 空 | 可覆盖对应 discovery 端点，仍执行 discovery 与 issuer/JWKS 验证 |

`OIDC_USE_RAW_USERNAME=true` 和非 `user` 的 `OIDC_DEFAULT_ROLE` 会阻止启动。保留空的端点覆盖项即可使用 discovery，不需要邮箱字段。配置由 API 进程启动时读取。所有 API 副本必须使用同一配置、JWT 密钥、PostgreSQL 和 Redis。

更新 OIDC 配置后重建 API 使配置生效。保留部署本身使用的 env file 和全部 Compose 覆盖文件，例如生产部署：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --force-recreate api worker
```

关闭 OIDC 后，现有 OIDC JWT 与登录结果立即拒绝使用；本地密码登录不受影响。反向代理应使用仓库 Nginx 配置中的回调日志脱敏、no-store 和 no-referrer 设置。自建网关也应隐藏回调查询参数和 Referer，并配置入口限流，禁止记录 token 请求正文。

## 账号与会话边界

Yuxi 以已验证的 `issuer + sub` 唯一识别身份，首次登录默认自动创建普通用户，重复登录复用同一账号。显示名带随机后缀以避免名称碰撞。不会按用户名、邮箱自动合并现有账号，不恢复已注销用户，也不会自动授予管理员权限。现有账号的管理和权限仍由 Yuxi 拥有，部门声明仅在开户时使用。当前没有自助账号绑定功能。

OIDC 本地会话固定一小时。每次 API 访问和登录结果兑换都读取数据库中的账号及撤销状态。点击退出只撤销当前 Yuxi 会话、清除浏览器登录状态并返回 Yuxi 登录页，不访问认证中心 end_session 或结束共享 SSO。其他产品继续使用其认证中心会话。再次登录 Yuxi 可以复用仍有效的 SSO。Back-channel 通知只撤销匹配提供商及 sid/sub 的会话。合法通知重试不会重复产生副作用。

认证中心仅修改 Enabled 不保证发出登出通知。没有通知时，已签发的 Yuxi 会话可使用到期；需要即时停用时，结束认证中心会话并核对通知结果，或停用 Yuxi 用户。已经开始执行的任务不由登录撤销自动取消。密码登录属于独立凭据，不随认证中心会话结束而撤销；Yuxi 主动退出会持久撤销当前密码 JWT，旧密码 JWT 不可继续访问。

`app-init` 在空库建立当前 Schema，其中包含 OIDC 身份、会话、登出事件与本地 JWT 撤销表。已有数据库按[部署指南](./deployment.md#_3-数据库与文件目录)核对版本、备份并离线升级。历史绑定用户没有会话标识的旧 JWT 会被拒绝，需重新使用 OIDC 或密码登录。

## 排查与验收

1. 检查所有 API 实际读取相同 OIDC 配置，确认 Redirect URI 和 issuer 精确一致，API 可访问 discovery、JWKS、token 和 UserInfo。
2. 完整走一次登录：新用户为普通用户，重复登录 ID 不变；同名本地账号不被合并，注销用户无法登录。
3. 点击 Yuxi 退出，检查旧 JWT 访问返回 401；从认证中心结束会话，核对 back-channel 返回成功且对应本地会话同样失效。
4. 在未配置 OIDC 的环境确认只有本地登录、无冗余第三方区域；配置不完整时 API 启动失败，不能把入口缺失当作配置成功。
5. 身份校验或短期 state 失效会回到登录页提示重试。未知签名 kid 的远端刷新最多每 30 秒一次。认证中心轮换密钥应提前发布新公钥，若刚轮换失败，可稍后重新登录。

OIDC 配置在 API 启动时校验，配置读取入口按当前进程环境创建配置对象。密码登录、OIDC 结果兑换和首次初始化使用相同登录字段与公开头像地址。

授权事务有效期五分钟，结果有效期一分钟。不要分享完整回调 URL、cookie、JWT 或 token 正文；排障只提供固定错误类别和请求发生时间。API 重启不会丢失 Redis 中尚有效的事务，Redis 数据丢失时需要重新发起登录。
