# 登录卡片固定尺寸，错误用 Message 展示

状态：implemented
类型：feature
Owner：frontend/src/pages/LoginView.vue

## 问题

固定高度的登录卡片在显示卡片内错误文案时会裁切居中表单。让卡片随表单内容增高可以避免裁切，但错误出现时登录框尺寸会变化。

## 决策

### 实现方案

`LoginView` 把登录卡片固定为 900×560。登录失败、账户锁定、初始化失败、OIDC 失败和表单校验错误都通过 Ant Design `message` 弹出；同一次提交的多条校验错误合并为一条。未勾选协议使用 `message.warning`。表单项说明不再占用卡片高度，字段仍用红色边框标出校验状态。服务端连接失败保留页面顶部状态条和重试。768px 及以下仍隐藏左图并改为单列，卡片宽高不随内容变化。

## 替代方案

让卡片以固定高度为最小高度并随内容增高可避免裁切，但错误会改变登录框尺寸。在固定高度内增加表单滚动会多一层滚动。只去掉错误文案会让用户看不到失败原因。

## 后果

宽于约 940px 的窗口里，900×560 卡片完整显示。更窄的窗口里卡片可能超出视口并出现横向滚动，表单仍可操作。服务端不可用时仍显示顶部状态条，不进入 Message。

## 验证

- 原有负向案例：固定高度且错误留在卡片内时，800×600 视口下 OIDC 错误提示导致表单超出卡片。
- 以下为现有记录中的验证结果，不代表本次合并决策记录时重新执行。
- 浏览器回归由 `frontend/test/browser/enterprise-login.playwright.js` 拥有。`playwright-cli -s=yuxi-login-test run-code --filename=frontend/test/browser/enterprise-login.playwright.js`：Passed。OIDC 与密码入口、可选 SSO、800×600 下固定 900×560 卡片、OIDC Message 和初始化校验 Message 均通过，表单没有被卡片裁切。
- 真实登录页：空表单提交弹出「请输入UID或手机号」；未勾选协议弹出警告；错误账号弹出「用户名或密码错误」。卡片在提交前后保持 900×560。1440×800 与 1024×800 无横向溢出，浅色与深色下表单都在卡片内。768×800 卡片仍为 900×560，页面出现横向滚动，表单可见。
- `docker compose exec -T frontend pnpm run lint:check`：Passed。
- Not run：前端 unit、前端 build、docs build、真实 OIDC 提供方和账号创建。
