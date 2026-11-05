# Agents Public API 的凭据与终端用户隔离

状态：implemented
类型：feature
Owner：backend/yuxi/api/routers/public_v1/agents/auth.py

## 问题

外部 APP 需要以受限 API Key 调用 Agent，同时在同一 APP 内隔离终端用户。仅靠客户端 `X-App-Id`、Thread metadata 或前端隐藏接口，无法保证来源可信、查询隔离和 Workdir 归属。

## 决策

### 实现方案

API Key 持久化 `access_level` 和 `app_id`；`agents` Key 的认证依赖只允许其访问 `/api/v1/agents/**`。无 APP 的本人 Key 沿用 Key 所属用户作用域，见[本人身份的受限 Agent Key](./2026-10-29-user-agents-api-key.md)；绑定 APP 的 Key 使用下述终端用户隔离。服务端从已认证 Key 得到 APP，响应中的来源头来自该快照。产品 JWT 的 `app_id` 为 `None`，不接受 `X-End-User-Id`；绑定 APP 的 Key 可用该 Header 声明 1–128 字符且无首尾空白的外部用户标识。APP Key 未带 Header 时使用该 APP 固定的默认终端用户，拥有独立 UID 与 Workspace。

`modules/identity/services/public_auth.py` 以 Key 所属用户、APP 和外部 ID 解析独立 User。`modules/identity/models.py` 的唯一约束固定终端用户身份；Thread、Input、Turn、Run、Project 和 Workdir 属于该真实用户和 APP 作用域。Agent 可见性以 Key 所属用户判断，实际文件和业务副作用以终端用户执行。终端用户没有可用的产品登录或签发 Key 凭据，认证入口拒绝其作为 JWT 主体。

Public Thread 是唯一 Agent 对话主协议；Session 路径仅在 HTTP 边界映射名称。生命周期和执行归属由 [Agent 生命周期决定](./2026-09-29-agent-lifecycle-framework.md) 及当前源码拥有，本记录只解释凭据、来源和终端用户隔离。

## 替代方案

- 只在 Public 路由检查 Key：同一受限 Key 仍可调用产品接口。
- 只给 Run 添加外部用户标签：Thread、Project 和 Workdir 仍归 Key 所属用户。
- 信任客户端 APP header 或 metadata：调用方可改变资源命名空间。
- 新建 APP 成员系统：现有受限凭据与用户唯一约束已能闭合当前身份需求。

## 后果

持有 APP Key 的调用方可声明该 APP 内的任意终端用户；该 Header 是 APP 的声明，不是终端用户的独立认证。首次出现的外部 ID 会创建 User。撤销 Key 阻止后续认证，不改写已接收的输入与运行来源。跨 APP、跨用户查询返回 404；模型与工具审计仍要求超级管理员 JWT，不随 Public Key 扩权。

## 验证

真实 PostgreSQL Schema 测试核对终端用户唯一约束；真实 HTTP/文件测试覆盖 `agents` Key 的产品接口拒绝、可信 APP 来源头、JWT 禁止终端用户 Header、默认终端用户与产品 Project/Workspace 隔离、跨 APP/用户 Thread 隔离和管理员审计权限。实际命令、结果与未验证范围以交付 PR 为准。
