# AgentRun 调试入口精确跳转 Langfuse

状态：archived
类型：feature
Owner：backend/package/yuxi/services/agent_run_service.py

## 问题

消息调试面板按持久化 `run_id` 展示 Run 分组。Langfuse 的精确页面 URL 包含项目 ID，前端只有 Run ID 和消息 metadata，无法可靠拼接；跳转还需要保持调试模式的超级管理员权限与 AgentRun 用户可见性，并且不能从同一对话的相邻消息猜测 trace。

## 决策

AgentRun service 优先从 AgentRun 自身取得 `langfuse_trace_id`，历史 Run 再从权威输出消息兼容读取；trace 的持久化与 stream 基础事实由 [AgentRun 增量审计基础事实](./2026-08-28-agent-run-audit-foundation.md)拥有。服务结束只读数据库事务后调用 Langfuse service 按 trace ID 惰性解析精确 URL。HTTP 路由要求超级管理员身份，并按当前 uid 查询 Run。无 trace 或可选 Langfuse 服务不可用时，接口返回结构化不可用原因，不改变 Run 状态。

`langfuse_service.py` 只接受 Langfuse SDK 返回的、与 `LANGFUSE_BASE_URL`（未配置时为 Langfuse Cloud 默认地址）同源的 HTTP(S) URL。`agent_api.js` 负责前端 API 适配；`MessageDebugPanel.vue` 在有 `run_id` 的分组头显示轻量文本按钮，按 Run 展示 loading，并在收到前端再次校验的 HTTP(S) URL 后打开隔离的新标签页。未关联 Run、无 trace、远端失败和浏览器阻止弹窗时，调试面板保持可用并显示明确反馈。

本决定只处理已有持久化 trace 关联的 Run。现有消息 feedback、Langfuse score、消息引用和 observation/span 级定位保持原样。

## 替代方案

- 前端用 `LANGFUSE_BASE_URL` 和 trace ID 拼接 URL：缺少 Langfuse 项目 ID，还会把部署配置扩散到浏览器，无法保证精确页面。
- Run 执行时同步获取并持久化完整 URL：会把远端项目查询加入运行链路，URL 还会随 Langfuse host 或项目配置变化而陈旧。
- 根据 `request_id` 重算 trace ID：可以覆盖输出形成前失败的 Run，但会建立第二套关联推导，并可能与 callback 最终 trace ID 漂移。
- 从 Run 分组任意消息提取 trace ID 后直接访问 Langfuse：无法生成可靠项目 URL，也绕过后端权限检查。AgentRun 自身字段与历史权威输出 fallback 已取代根据任意消息猜测的做法。

## 后果

点击调试入口会产生一次有界的 Langfuse 项目查询，AgentRun 执行和聊天请求不承担这段远端延迟。配置 Langfuse 且已在模型执行前固化 trace 的 Run，即使没有最终输出消息也能跳转；Langfuse 未配置、trace 固化前即失败或仅有不含 trace 的历史 Run 仍明确显示不可用。

Langfuse URL API 是超级管理员调试能力，同时继续按 uid 隔离 Run。后端拒绝非配置 Langfuse 源站，前端 wire 响应继续限制为 HTTP(S)。浏览器弹窗策略仍可能阻止新标签页，此时界面提示用户允许弹窗后重试。

## 验证

- `backend/test/unit/services/test_agent_run_service.py` 证明 Run 入口优先使用 Run 自身 trace、兼容历史输出消息、在远端调用前结束只读事务，并区分无 trace、Langfuse 不可用和 Run 不存在。
- `backend/test/unit/services/test_langfuse_service.py` 证明 trace URL 按 ID 解析，并拒绝非 HTTP(S) URL 与跨源 HTTPS URL。
- `backend/test/integration/api/test_agent_run_result_causality.py` 通过真实 HTTP 与 PostgreSQL 证明普通用户返回 403、跨超级管理员 uid 返回 404，错误输出绑定不会读取同会话其他 Run 的 trace。
- `web/test/unit/messageDebug.test.js` 证明前端只接受后端确认的 HTTP(S) URL；前端 lint、unit 和 build 验证装配。
- 真实页面验证证明浅色与暗色 Run 分组均显示稳定按钮；点击完成 Run 与失败 Run 分别打开各自的 Langfuse `/project/.../traces/<trace-id>` 页面；无 trace mock 关闭空白标签页并显示可恢复提示。
