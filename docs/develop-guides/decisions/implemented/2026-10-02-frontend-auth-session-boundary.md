# 前端认证会话边界收敛

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/identity/model/user.js

## 问题

前端超级管理员 API 包装器调用的权限检查只返回布尔值却没有检查结果，普通登录用户可以进入请求发送阶段，客户端守卫因此不是 fail-closed。后端仍是最终授权边界，但前端 API 契约与其它管理员包装器不一致。

OIDC 回调和超管模拟登录还绕过用户 Store，直接写入 `user_token`。这使会话状态更新和持久化写入存在多个 Owner，后续容易出现 Pinia 状态、项目缓存和持久 token 不一致。

重新登录期间，旧请求的 `401`、资料响应或 `/me` 校验仍可能返回。若回调直接操作当前会话，旧结果会清除或覆盖新会话，延迟的导航也可能把已登录用户带回登录页。

## 决策

由 identity Store 的 `applySession` 统一更新会话响应式状态并写入持久 token；登录、OIDC 回调和模拟登录都调用该动作。API 的认证和客户端权限断言由无业务依赖的 `apis/auth_context.js` 承担，identity Store 注入 token、角色判断和失效回调；`checkSuperAdminPermission` 保持布尔谓词语义，供已有界面早退逻辑使用。

### 实现方案

会话入口仍由各 API/页面拥有，但所有成功的会话载荷经过 `useUserStore().applySession(data)`；该动作负责清理项目上下文、更新 Store 状态并写入 `user_token`。超级管理员 API 包装器通过 `assertApiSuperAdminPermission()` 读取 identity 注入的角色上下文，失败即抛出“需要超级管理员权限”，不触发 `fetch`。

HTTP 客户端用发出请求时的 token 核对 `401` 的归属。身份 Store 在应用 `/me`、资料和头像响应前核对同一个 token，路由守卫在校验返回后以当前会话决定导航。账户菜单退出也保留已经生效的替代会话。只有属于当前会话的 `401` 可以清理状态并提示过期，清理前核对持久 token，保留另一个标签页写入的会话。延迟跳转前再次检查是否已有会话或已经在登录页。

## 替代方案

- 让每个页面继续直接写 token：改动最小，但会继续保留多个会话 Owner，不选择。
- 让 `checkSuperAdminPermission` 改为抛异常：可以统一 API 与页面调用契约，但会改变 Debug 页面现有的布尔早退路径，且错误处理范围更大，暂不选择。
- 只依赖后端拒绝：安全边界仍成立，但客户端会继续发送无效请求并保留错误的 API 包装器契约，不选择。
- 切换会话时统一中止旧请求需要维护在途请求清单。已经返回的响应仍需核对身份，直接用请求 token 验证归属即可。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 非超级管理员调用 `apiSuperAdmin*` 在客户端同步失败 | 包装器忽略布尔权限结果并发出请求 | `frontend/src/apis/base.js` | `frontend/test/unit/frontendAuthBoundary.test.js` | 普通用户调用 `apiSuperAdminGet`，断言抛出权限错误且 `fetch` 次数为 0 | Passed |
| 成功会话写入只有 identity Store 是持久 token Owner | OIDC 或模拟登录重新直接写 `user_token` | `frontend/src/modules/identity/model/user.js` | 前端认证边界单测的源码检查 + 相关单测 | 在 OIDC/Debug 文件中恢复直接写入，测试应失败 | Passed |
| 认证响应只提交给发起请求的会话 | 旧账号响应清理替代凭据、覆盖资料或误跳转 | identity Store / API 请求 / 路由 | `cd frontend && node --test test/unit/api_boundary.test.js test/unit/routeIdentityRecovery.test.js`、`authSessionOwnership.js` | 并发 `401`、延迟跳转或退出、旧资料和头像响应、旧 `/me` 的成功与失败 | Passed |
| 后端授权语义由后端执行 | 客户端 guard 被误当成安全边界 | 后端认证依赖与 API service | 后端权限链路未参与前端验证 | 直接请求后端仍由后端权限检查拒绝 | Inspected |

浏览器协议夹具延迟第一次退出和旧请求的 `401`，回读替代会话的可见用户名、持久凭据和页面位置。恢复旧处理后，定向用例出现凭据清除、身份资料覆盖或路由权限错误。

## 后果

认证成功后的响应式状态、项目缓存清理和持久 token 由 identity Store 的同一个动作拥有，适用于 OIDC、模拟登录和普通登录。普通登录和 OIDC 各自维护返回目标。同一凭据的并发 `401` 只处理一次会话过期。超级管理员 API 在浏览器端拒绝无效请求，后端负责最终授权。模块依赖方向由[认证上下文决定](2026-10-02-frontend-api-auth-context.md)维护。

会话写入边界交付时，`cd frontend && pnpm exec node --test --test-concurrency=1 test/unit/frontendAuthBoundary.test.js test/unit/api_boundary.test.js` 通过 14 项。该阶段的 lint 因缺少 `@typescript-eslint/parser` 未进入检查，完整前端单测、浏览器、构建和后端授权链路均未执行。

旧能力不存在：OIDC 回调和超管模拟登录中的直接 `user_token` 写入已删除；API 超级管理员包装器不再忽略权限谓词结果。

重新引入条件：只有 identity Store 的认证会话 Owner 发生明确决策变更，或 API 基础层改为新的认证上下文注入机制时，才允许增加其他 token 写入入口，并须同步更新负向测试。
