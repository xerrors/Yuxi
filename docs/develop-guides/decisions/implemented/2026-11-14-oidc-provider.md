# 独立的 OIDC 身份提供端

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/services/provider.py

## 问题

使用 Yuxi 账户的外部应用需要标准身份认证入口，不能复用 Yuxi 登录 JWT 或依赖共享管理员凭据。

## 决策

### 实现方案

Identity 提供显式启用的 OIDC Provider，仅信任部署时登记的一个机密客户端。授权码流程要求准确回调、S256 PKCE、state 与 nonce；浏览器登录确认页调用独立的 Provider API。客户端必须显式配置，协议与前端不依赖设备集成。

授权用例统一校验浏览器绑定并签发授权码，HTTP 路由只调用完整用例。授权码和浏览器会话存于短期 Redis，兑换在客户端、PKCE 和当前用户验证后原子消费授权码；PostgreSQL 拥有用户有效状态。ID Token 与 userinfo 访问令牌使用独立持久 RSA 密钥签发，以 audience 和 purpose 隔离。API/worker 启动校验已启用的配置，缺失时失败；关闭时不要求私钥。不增加动态客户端注册、refresh token 或认证数据库。

## 替代方案

直接交付 Yuxi 登录 JWT 会扩大凭据用途；仅接入外部 IdP 不能覆盖已有 Yuxi 本地账户。完整认证平台超出单个静态客户端的当前需求。

## 后果

Yuxi 成为身份信任根，私钥必须独立保管。客户端按 issuer 与稳定 sub 识别用户；更换 issuer/sub 会产生不同身份。身份认证不表达外部应用的资源授权，也不自动合并同名账户。

## 验证

配置单测覆盖关闭、弱凭据、缺少客户端 ID、非法地址和密钥算法；真实 HTTP、PostgreSQL 与 Redis integration 验证公钥和声明、并发单次兑换、回调/PKCE/浏览器绑定、失效用户及令牌用途隔离。前端单测验证授权参数白名单、危险跳转拒绝和重试。入口与验证分别由 identity Provider、启动装配与 `test_oidc_provider.py` 拥有，不依赖具体外部应用。
