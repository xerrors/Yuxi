# 远程 MCP 个人 OAuth 接入核查

本页面向 MCP 维护者，核查日期为 2026-10-18。外部协议以供应商和 MCP 官方资料为准，Yuxi 当前行为由 [MCP 集成](../../agents/mcp-integration.md)及对应实现拥有。平台 OIDC 登录和 MCP 资源授权分别签发 token。企业共享 IdP 可以复用浏览器会话，资源服务器仍需要独立 audience。

## GitHub 与 Notion

GitHub remote MCP 的入口为 `https://api.githubcopilot.com/mcp/`，客户端负责取得用户 access token 并以 Bearer 传入。GitHub 不提供动态客户端注册，官方建议使用 GitHub App，也支持 OAuth App。组织策略可能要求安装或审批。参见 [GitHub host integration](https://github.com/github/github-mcp-server/blob/main/docs/host-integration.md)。只读探测确认资源挑战声明 `https://github.com/login/oauth`，授权服务器元数据支持 S256；用户授权与组织资源读取未验证。

Notion hosted MCP 入口为 `https://mcp.notion.com/mcp`，支持动态客户端注册。客户端必须保存注册结果。refresh token 轮换要求串行刷新并原子持久化，不能让不同 worker 同时使用旧 refresh token。普通个人连接使用 `default` scope。参见 [Notion 客户端指南](https://developers.notion.com/guides/mcp/build-mcp-client)。Yuxi 隔离环境已实际完成元数据发现、动态注册、用户浏览器授权和 65 项工具发现。`notion-get-self` 返回非空且 `isError=false` 的真实协议结果，未输出个人资料。早期探测的 Cloudflare 拒绝未在该环境重现，不能据此推断所有部署网络都可达。

## 企业资源与 Keycloak

[MCP 授权规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)要求 PKCE、资源元数据发现以及目标资源绑定。[Keycloak MCP 指南](https://www.keycloak.org/securing-apps/mcp-authz-server)提供 audience mapper 方案。实验使用专用 optional scope 把考勤 MCP URL 放入 access token 的 audience，并在资源服务器验证签名、issuer、audience、有效期和 scope。

Keycloak 26.8 的真实导入与 token 结果还证明：自定义 `clientScopes` 后必须显式提供并绑定平台需要的 `profile`、`email` 与 `basic` scopes。考勤客户端的 `basic` scope 使用 subject mapper，签发带员工 subject 的 access token；仅有正确 audience、缺少 subject 的 token 被考勤服务器拒绝。两名虚构员工经真实浏览器分别完成平台登录与个人 MCP 授权，worker 通过直接 MCP 和 Skill 依赖得到各自记录。固定模型只消费真实工具结果。

## 证据边界

HTTP/PostgreSQL 集成测试拥有 state、浏览器、用户、连接世代、配置修订及刷新竞争的确定性证据。考勤测试使用真实 RSA 签名证明错误 audience、issuer、scope 和员工缺失被拒绝。真实 Keycloak 与 worker E2E 证明部署互通和业务结果，两者不互相替代。GitHub 按用户选择暂不执行真实授权。Notion 除本人信息外，已通过 Yuxi 的个人授权调用 `notion-fetch` 读取用户指定测试页，核对返回页面 ID 一致，并包含元数据、标题和正文；页面标识与内容未写入仓库，未修改页面。写操作及外部供应商撤销/重连未验证。
