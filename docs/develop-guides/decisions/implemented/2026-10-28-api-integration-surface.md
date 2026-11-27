# API 集成入口与知识库 MCP 接入说明

状态：implemented
类型：feature
Owner：frontend/src/modules/settings/ui/ApiIntegrationPanel.vue

## 问题

对外 API 的接入信息分散在文档和 API Key 列表中：管理员看不到有哪些 APP 在用、各自有多少影子用户与调用量；知识库使用者想接 MCP 时，需要自己拼服务地址和请求头，还要跳到别处创建 Key。

## 决策

### 实现方案

系统管理的「API 集成」将 Key 管理与接入概览放在同一页，入口与角色见[系统管理导航与旧链接迁移](./2026-11-26-system-management-navigation.md)。`ApiIntegrationPanel` 读取 `GET /api/user/apikey/apps`，同一响应提供完整的 APP 凭据明细与汇总，避免默认列表分页截断。加载失败时保留接入说明，隐藏不可用的统计，刷新可恢复。

HTTP 入口在 `backend/yuxi/api/routers/identity/users.py`，identity 服务执行超级管理员权限检查并组合仓储结果。`APIKeyRepository` 读取绑定 APP、未撤销且所属用户仍有效的凭据；有效 Key 另按模型的启用、过期及撤销规则计数。禁用或过期的 Key 保留在明细中并标记无效。影子用户只统计未删除的 `end_user`；`AgentInputRepository` 统计 APP 的持久输入，排除继承 APP 身份的内部协作输入。汇总明确命名为“累计输入”，不表达 HTTP 请求量、Token 或模型调用量。

知识库详情页提供“MCP 接入”弹窗，展示同源服务地址、Bearer 请求头和当前知识库 ID，说明 Streamable HTTP 与远程域名许可要求。开发代理与生产反向代理转发该同源地址。复制复用共享剪贴板实现，兼容局域网 HTTP。「管理 API Key」链接打开系统管理的「API 集成」。主要 Public API 端点维护在 `apis/apikey_api.js`，按方法与路径唯一标识，并以运行服务的 OpenAPI 验证。

## 替代方案

新增独立的 APP 管理表和 CRUD 会重复已有归属事实；前端拼接分页 Key 列表与独立统计会产生截断或刷新时的不一致。完整接入概览直接由服务组合现有事实，权限仍由后端执行。

## 后果

统计无需新增持久化。APP 范围由当前未撤销且所属用户未删除的 Key 确定，最后一个 Key 撤销后该 APP 不再出现在概览。`input_count` 按留存的外部 Input 计数，包含取消的提交；幂等重放不新增 Input。端点清单只展示主要接入操作，协议变化时需同步核对。

## 验证

真实 HTTP 集成测试 `test/integration/api/test_apikey_router.py` 共 18 项通过，其中概览覆盖未认证 401、已绑定部门的普通用户和管理员精确返回 403、超过 500 个 Key 的完整明细、凭据有效性、停用用户及内部协作输入排除。计数使用提交到 PostgreSQL 的独立样本和精确预期值。

`frontend/test/browser/apiIntegrationSurfaces.js` 在真实 Chromium 中使用合成响应，核对运行 OpenAPI、浅深主题及 1280/1024/768px 布局、错误恢复、无效 Key 状态、剪贴板实际回读和 API 集成跳转。外部 MCP 客户端及公网部署需要各自的环境验证。
