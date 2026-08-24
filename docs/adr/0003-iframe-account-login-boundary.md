# iframe OA 账号登录边界

状态：implemented
类型：feature
Owner：`backend/package/yuxi/services/oa_sso_service.py`

## 问题

OA 助手以 iframe 嵌入父项目时，父项目只能向页面提供 OA 账号。账号本身不是可信身份断言，Yuxi 不能据此直接创建或登录本地用户。

## 决策

iframe 只从 `VITE_YUXI_EMBED_ALLOWED_ORIGINS` 中声明的父页面接收 `login-params` 消息，并以 `request-login-params` 请求首次授权或续期。消息中的账号仅作为 OA 换票和查询用户信息的线索。

服务端仅在非生产环境启用 `OA_ACCOUNT_LOGIN_*` 时使用账号调用 OA 换票接口；换得 `oaToken` 后，服务端使用 `OA_SSO_*` 用户信息接口校验返回账号、公司编码和在职状态。两个配置的公司编码必须一致。校验通过后，服务端按 OA 身份创建或复用 Yuxi 用户、解析部门并签发 Yuxi access token。OA token 和 `saToken` 不写入本地用户数据，也不返回给 iframe。

嵌入模式的登录态由父页面重新下发账号驱动；应用隐藏退出登录入口，避免 iframe 用户注销本地登录态后脱离父项目认证上下文。

## 替代方案

- 直接信任 iframe 传入的账号：拒绝。页面消息可被伪造，无法证明账号归属。
- 将 OA token 直接交给前端或持久化保存：拒绝。扩大敏感凭据暴露和泄漏范围。
- 在生产环境使用账号换票：拒绝。生产环境返回未配置错误；生产接入需要父项目后端提供短时、可验证的签名断言。

## 后果

运行账号换票必须同时配置 OA 换票与用户信息接口，且两个公司编码一致；缺失或不一致时，服务端拒绝登录。OA 用户信息服务不可用时，登录请求返回错误而不降级为账号直登。

嵌入场景依赖父项目允许来源和消息协议；来源配置缺失或消息不合法时，页面保持等待授权状态。

## 验证

- `backend/test/unit/services/test_oa_sso_service.py` 覆盖 token 缺失、账号不一致、公司不一致、非在职状态、换票响应异常及 OA token 不泄露；容器内执行结果为 `10 passed`。
- `web/test/unit/oaEmbedBridge.test.js` 覆盖允许来源、非法消息与授权消息处理；前端单元测试结果为 `24 passed`。
- 受影响前端文件的 ESLint 和 `pnpm run build` 已通过。
- 真实 OA 服务与浏览器 iframe 联调未执行，保留为部署前验证项。
