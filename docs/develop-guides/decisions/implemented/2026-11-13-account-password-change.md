# 本地账号自助修改密码

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/services/users.py

## 问题

本地账号只能由超级管理员重置密码，账户设置缺少本人校验原密码后更换密码的入口。管理员成员管理接口拥有重置权限，不能作为普通用户自助改密的通路。

## 决策

### 实现方案

账户设置页头在刷新旁提供三点菜单，包含修改密码和复制用户 ID；移除账号行内复制按钮。修改密码弹窗接收原密码、新密码与确认密码，新密码沿用至少八个字符的规则。企业账号的修改密码项禁用并解释由统一身份服务管理。

身份域增加仅针对认证当前用户的改密用例及 `/api/auth/password` HTTP 入口，不接受目标用户 ID。服务通过已有 repository 锁定并刷新当前用户，拒绝非产品用户、企业账号及错误原密码，验证成功后写入 Argon2 哈希并提交。管理员重置接口保持独立。会话沿用现有有效期，不增加全局会话撤销或数据库结构。

消息活动格子按可用宽度缩短高度，最高保留既有 18px，星期标签随行高对齐。

## 替代方案

- 复用成员修改接口：该接口承诺管理员管理他人，扩大其权限会混淆本人改密与管理重置。
- 仅提供菜单入口：无法完成用户独立更换密码的目标。
- 在资料更新接口添加密码字段：资料修改无需原密码，把两种操作合并会使校验与失败语义不清晰。

## 后果

密码修改不撤销已有 JWT 或 API Key，与管理员重置的现有会话行为一致。测试仅创建、修改和清理专用临时用户，不触碰现有用户密码。

## 验证

- `Passed`：`docker compose exec api uv run --no-sync --group test pytest test/integration/services/test_identity_usecase_transactions.py -k password -q`，13 项通过。真实 TCP HTTP 与隔离 PostgreSQL schema 验证三种本地角色改密、旧密码拒绝和新密码登录、错误原密码、短密码、未登录、额外目标 ID、企业/已删除/终端身份拒绝、并发原密码刷新及提交失败回滚；通过新数据库会话回读哈希。
- `Passed`：`playwright-cli -s=account-compact run-code --filename=frontend/test/browser/accountActions.js`。复用真实设置页面，API 使用浏览器合成响应；浅深色与 768、1024、1440、1920px 验证菜单与弹窗边界、365 个日期、格子等宽高和星期对齐、剪贴板完整 ID、表单拒绝与服务错误恢复、关闭后清空密码、企业账号禁用，并生成最终截图。此证据不替代后端持久化测试。
- `Passed`：前端 `lint:check`、1073 项 unit 与 build；后端 Ruff check、format 与 import 检查，unit 为 3766 项通过、127 项跳过；工程契约检查及其 56 项单测、文档 build 与 `git diff --check`。
- 扩大执行完整身份事务 integration 为 31 项通过、1 项既有失败：`test_http_key_replay_permissions_and_tombstone` 预期更新 API Key `access_level` 返回 422，当前接口模型允许该字段并返回 200。该断言和接口不属于本人改密范围。
