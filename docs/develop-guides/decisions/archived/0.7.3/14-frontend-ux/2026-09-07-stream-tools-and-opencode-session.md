# 工具流展示与 OpenCode 会话路由

状态：archived
类型：bug-fix
Owner：web/src/composables/useAgentStreamHandler.js

## 问题

工具调用作为消息首块时，前端复制首块但未归并其中的工具字段，导致无前置正文的工具不显示。完整工具事件与参数增量需要区分，不能重复拼接。OpenCode Go 要求稳定的会话路由头，通用模型加载器目前未提供。过程摘要与展开内容缺少间距。

## 决策

前端 loading 只消费当前语义 stream_event；`web/src/utils/messageProcessor.js` 统一使用 name/args，首块也参与归并，完整事件覆盖对应工具快照，增量仅追加参数。工具结果按同一 Run 的调用 ID 关联，不添加旧 msg 格式兼容。展开内容采用现有 8px 间距节奏，收起不留空白。

`backend/package/yuxi/models/chat.py` 为 OpenCode 与 OpenCode Go 的请求附加 Yuxi User-Agent 和 x-opencode-session，遵循[官方客户端要求](https://opencode.ai/docs/go/#where-can-i-use-it)。Agent 主模型、动态模型与摘要器显式传入 Thread ID；无会话的独立模型操作使用实例级随机 ID。其他供应商不附加该头，不更改认证或持久化配置。

## 替代方案

保留旧流格式会维护两套展示事实；等待历史刷新才显示工具会丢失实时反馈。全局会话 ID 会混用不同对话的路由，每次模型调用随机生成则失去会话连续性。使用真实 Thread 关联并保留现有调用链，不引入全局可变状态。

## 后果

运行中的工具展示不依赖前置正文或历史回读；旧 loading msg 不再被消费。OpenCode 会话头只发送给对应供应商，不改变会话持久化及其他模型的协议。

## 验证

前端 `agentRequestQueue.test.js` 覆盖首块单工具、参数分片、完整快照、并行工具、结果与跨 Run 隔离、旧 loading 消息拒绝；修复前新增三例均失败，修复后通过。真实浏览器以当前语义事件驱动实际组件，验证工具立即显示、结果到达后的完成状态、展开 8px 间距、收起无空白，以及暗色 375px 布局与减少动态效果。

后端 `test_model_selectors.py` 通过真实 SDK 与 HTTP MockTransport 检查 OpenCode 流式和非流式请求头、同会话稳定性、独立会话隔离及其他供应商不受影响；`test_summary_graph_config.py` 验证主/子 Agent 与摘要模型装配的 Thread ID。相关后端测试 51 项通过；Web 全部 274 项测试、lint、构建通过。

工具专项后端回归使用 `uv run --no-sync --group test pytest test/unit -m "not slow" -k "not test_finish_run_terminal_loser_does_not_append_end_event"`，1881 通过、52 跳过、1 排除。排除项在验证中停滞，标准依赖同步也存在容器权限错误；不宣称全量通过。浏览器使用测试事件与 API 替身，不宣称正式账号页面端到端通过。共享模型接入链路的真实供应商工具续答、OpenCode HTTP/Worker/SSE/持久化 E2E 与最终回归结果见[推理适配决策](./2026-09-07-provider-reasoning-adapter.md)。
