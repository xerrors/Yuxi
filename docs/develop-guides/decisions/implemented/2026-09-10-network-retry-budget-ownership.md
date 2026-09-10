# 网络重试预算：单中间件内区分网络预算重试与非网络次数重试

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/agents/middlewares/network_retry.py

## 问题

断网时任务应挂起等待恢复，而不是烧尽重试次数后「假完成」（把错误文本写成 assistant 消息、Run 标 completed）。

初版拆成两个中间件：`NetworkRetryMiddleware`（预算内退避重试）挂在 `ModelRetryMiddleware` 之内。但外层 `ModelRetryMiddleware(max_retries=2)` 仍会重试网络类错误，后果有二：

1. **预算被放大**：`NetworkRetryMiddleware.awrap_model_call` 每次调用都重新记录起始时间，外层每轮重试都会开启一个全新的 600 秒预算，实际可重复 `max_retries + 1` 次。
2. **仍然假完成**：预算耗尽后抛出的网络错误被外层 `on_failure="continue"` 吞成含错误文本的 `AIMessage`。

## 决策

网络类错误和非网络类错误的重试维度不同（前者预算、后者次数），必须分开处理，但**不拆成两个中间件**——拆分会因为装配顺序和外层重试网络错误而放大预算。改为让 `NetworkRetryMiddleware` 继承 `ModelRetryMiddleware`，在同一个中间件内区分两类错误：

- 网络错误：按 `network_budget_seconds`（默认 600s，环境变量 `YUXI_NETWORK_RETRY_BUDGET_SECONDS`）预算退避重试，耗尽后**显式抛出**（保留 `error_type`/`error_message` 归因，Run 以 `failed` 结束），不经过 `on_failure`；
- 非网络错误：复用父类的 `max_retries`/`retry_on`（`default_retry_on`）/`on_failure` 语义，与原来 `ModelRetryMiddleware` 行为一致。

同步 `wrap_model_call` 与异步 `awrap_model_call` 对称实现，避免同步路径静默走父类语义而丢失网络预算重试。

## 替代方案

- 保留两个中间件（内层 `NetworkRetry` + 外层 `ModelRetry`，用 `retry_on` 谓词排除网络错误）：功能等价，但装配绕、且「网络错误不归外层管」这个不变量靠装配顺序+谓词两处共同维持，一处漏改就重新放大预算；采纳作者「合并成单中间件」的建议后拒绝。
- 让预算是进程级/全局共享：会把互不相关的模型调用互相拖累；拒绝。
- 用请求对象标识跨外层重试共享预算：需要为「同一次模型调用」建立稳定身份，而 `ModelRequest` 无此语义；拒绝。

## 后果

网络错误的唯一重试入口是 `NetworkRetryMiddleware` 自身的预算循环，`network_budget_seconds` 是真实上限，不再有外层放大。非网络错误重试语义与 `ModelRetryMiddleware` 一致（`default_retry_on` 的 `ModelError.is_retryable` 判定保留）。

移除了 `retry_non_network_errors` 谓词：合并后不再有外层 `ModelRetryMiddleware` 需要排除网络错误。网络重试参数用 `network_` 前缀（`network_budget_seconds`/`network_initial_delay`/`network_max_delay`）与父类非网络重试的 `initial_delay`/`max_delay` 区分。

## 验证

`backend/test/unit/agents/test_network_retry.py`（20 passed）：

- `test_network_budget_honored_and_fails_explicitly`：虚拟时钟下持续 `ConnectionError`，累计等待受单次 600s 预算约束，最终**抛出**异常而非返回含错误文本的响应；
- `test_non_network_error_retried_by_max_retries_then_continue`：非网络错误按 `max_retries` 重试后 `on_failure=continue` 返回错误 AIMessage；
- `test_non_retryable_model_error_propagates`：`ModelError.is_retryable=False` 立即抛出，不消耗重试次数；
- `test_non_network_error_retry_succeeds_after_backoff`：非网络错误重试成功后正常返回。
