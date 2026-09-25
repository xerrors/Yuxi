# Agent 工具异常隔离与 SSE 终态投递

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/middlewares/tool_error_guard.py

## 问题

工具执行体的普通异常可打断整次 Agent Run，模型无法读取工具错误并给出后续回答。数据库补发 SSE `end` 复用普通事件 ID 时会被前端去重丢弃。工具调用包装由 `backend/package/yuxi/agents/middlewares/tool_error_guard.py` 拥有，Run SSE 由 `backend/package/yuxi/services/agent_run_service.py` 拥有对应边界。

## 决策

主 Agent 与 SubAgent 的最外层工具包装把普通执行异常转换为绑定原 tool-call ID、标记 `status="error"` 的 ToolMessage；只向模型和日志提供异常类型，不暴露可能包含凭据的异常正文。GraphBubbleUp、取消和进程控制异常仍向外传播。Run SSE 的通知与游标规则见 [Run SSE 补发终态与事件游标](./2026-09-23-run-sse-fallback-cursor.md)。

同一补丁还收窄数据库初始化日志中的连接信息，并使信息配置路由的显式 HTTPException 继续传播；这些局部行为分别由 PostgreSQL manager 和路由拥有。

## 替代方案

- 让工具异常直接终结 Run：保留失败可见性，但模型失去对可恢复工具失败的处理机会。
- 在每个工具分别捕获异常：会让失败策略散落于工具实现，无法覆盖后续工具和子 Agent。

## 后果

普通工具异常可被模型读取；工具失败不等于操作成功，错误消息仍需关联原调用并接受审计。SSE 补发通知不携带 Redis ID；真实事件仍使用原游标，终态状态从 PostgreSQL 读取。

## 验证

工具包装单测使用真实 LangChain Agent 装配与假模型验证正常结果、错误状态、敏感异常正文不泄漏、取消与 interrupt 放行，并包含移除包装后异常打穿 Run 的对照。SSE 验证及环境限制由对应的游标决策记录维护。
