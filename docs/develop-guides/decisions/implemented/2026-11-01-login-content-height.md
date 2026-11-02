# 登录卡片由表单内容决定高度

状态：implemented
类型：bug-fix
Owner：frontend/src/pages/LoginView.vue

## 问题

登录卡片固定为 560px 高。800×600 视口下，OIDC 错误提示使表单超过卡片高度；表单居中与圆角裁切共同隐藏顶部和底部信息。

## 决策

### 实现方案

LoginView 把桌面卡片的固定高度改为最小高度，让表单、协议与错误信息共同决定高度，页面自然滚动。左侧图片绝对定位填充所在栏，避免图片固有高度撑高普通登录卡片。768px 单列布局取消最小高度。登录 API、校验和认证状态处理沿用既有流程。

## 替代方案

保留固定高度并增加表单内部滚动会引入嵌套滚动，也需要处理居中内容的滚动起点。缩小字号或隐藏提示不能保证错误信息完整可读。

## 后果

较短窗口中的卡片可以高于视口，用户通过页面滚动访问全部表单；图片继续填充卡片左栏。不新增布局状态、断点或 JavaScript 高度计算。

## 验证

- 浏览器回归由 `frontend/test/browser/enterprise-login.playwright.js` 拥有，使用真实 Vue 页面及 HTTP fixture 验证 OIDC、密码入口、可选 SSO 和初始化校验；不提交真实账号或修改认证数据库。
- 负向案例：保留固定高度时，800×600 视口下 OIDC 错误提示导致表单超出卡片，浏览器断言报告“登录表单及错误信息被登录卡片裁切”。
- `playwright-cli -s=yuxi-code-review run-code --filename=frontend/test/browser/enterprise-login.playwright.js`：Passed；OIDC 与密码入口、可选 SSO、800×600 登录错误和初始化校验均通过 DOM 检查，错误状态截图完整显示表单内容。
- 浏览器临时探针在 768、1024、1440px 宽度覆盖浅色、深色及登录、初始化校验共 12 个场景：Passed；表单位于卡片内、无页面横向溢出，截图已检查普通登录与初始化边界状态。
- `docker compose exec -T frontend pnpm run lint:check`：Passed。
- `docker compose exec -T frontend pnpm run test:unit`：Passed；`docker compose exec -T frontend pnpm run build`：Passed，包含 TypeScript 检查。
- 工程契约检查及其 56 项单测、修改文档的相对链接检查、`pnpm run build`（docs）和 `git diff --check`：Passed。
- Not run：真实 OIDC 提供方、账号创建及认证数据库副作用；浏览器使用 HTTP fixture。
