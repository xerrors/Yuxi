# 网络重试预算归属：外层 ModelRetry 不重试网络类错误

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/middlewares/network_retry.py

## 问题

断网时任务应挂起等待恢复，而不是烧尽重试次数后「假完成」（把错误文本写成 assistant 消息、Run 标 completed）。

初版只加了 `NetworkRetryMiddleware`（预算内退避重试）挂在 `ModelRetryMiddleware` 之内，但外层 `ModelRetryMiddleware(max_retries=2)` 仍会重试网络类错误。后果有二：

1. **预算被放大**：`NetworkRetryMiddleware.awrap_model_call` 每次调用都重新记录起始时间，外层每轮重试都会开启一个全新的 600 秒预算，配置的「总预算」实际可重复 `max_retries + 1` 次。
2. **仍然假完成**：预算耗尽后 `NetworkRetry` 抛出的网络错误被外层 `on_failure="continue"` 吞成含错误文本的 `AIMessage` 并作为成功响应返回。

## 决策

网络重试预算由 `NetworkRetryMiddleware` 独家拥有：外层 `ModelRetryMiddleware` 通过 `retry_on=retry_non_network_errors` 排除网络类错误。

`ModelRetryMiddleware` 对 `retry_on` 返回 `False` 的异常**直接抛出，不经过 `on_failure`**，因此预算耗尽后网络错误按异常显式失败，不再产生「假完成」。

预算耗尽后的失败语义：抛出的仍是原始网络异常（保留 `error_type`/`error_message` 归因），Run 以 `failed` 终态结束。

## 替代方案

- 让预算是进程级/全局共享：会把互不相关的模型调用互相拖累；拒绝。
- 用请求对象标识跨外层重试共享预算：需要为「同一次模型调用」建立稳定身份，而 `ModelRequest` 无此语义，容易误判；拒绝。
- 保持现状（外层继续重试网络错误）：预算不可预期且仍有假完成；拒绝。

## 后果

网络错误的唯一重试入口是 `NetworkRetryMiddleware`，其 `budget_seconds` 是真实上限。非网络错误的重试语义不变（仍由 `ModelRetryMiddleware` 按 `max_retries` 处理，`default_retry_on` 的 `ModelError.is_retryable` 判定保留）。

`retry_non_network_errors` 依赖 `langchain.agents.middleware.model_retry.default_retry_on` 保持默认语义；上游若调整该函数，此处同步。

## 验证

`backend/test/unit/agents/test_network_retry.py`：

- `test_composed_middlewares_honor_budget_and_fail_explicitly`：按真实装配顺序组合两个中间件（虚拟时钟），持续 `ConnectionError` 下累计等待受单次 600s 预算约束（未被放大成 3 份），且最终**抛出**异常而非返回含错误文本的响应；
- `test_non_network_error_still_retried_by_outer_model_retry`：非网络错误仍由外层按 `max_retries` 重试成功，语义未变。
