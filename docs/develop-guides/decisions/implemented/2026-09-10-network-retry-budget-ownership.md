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

本记录拥有网络预算与异常分类规则；次数重试耗尽策略由[模型重试耗尽保留失败语义](./2026-09-23-model-retry-failure.md)部分取代，统一使用 `on_failure="error"`。网络预算的归属与计时规则继续有效。

网络类错误和非网络类错误的重试维度不同（前者预算、后者次数），必须分开处理，但**不拆成两个中间件**——拆分会因为装配顺序和外层重试网络错误而放大预算。改为让 `NetworkRetryMiddleware` 继承 `ModelRetryMiddleware`，用 **handler 包装**区分两类错误，不复制父类的重试逻辑：

- 网络错误：`_wrap_network_retry`/`_awrap_network_retry` 用闭包包装 handler，在 `network_budget_seconds`（默认 600s，环境变量 `YUXI_NETWORK_RETRY_BUDGET_SECONDS`）预算内吞掉网络异常退避重试，耗尽后**显式抛出**（保留 `error_type`/`error_message` 归因，Run 以 `failed` 结束）；
- 非网络错误：wrapped handler 原样抛出，交给父类 `wrap_model_call`/`awrap_model_call` 按 `retry_on=_retry_non_network_errors`（排除网络异常、`ModelError.is_retryable` 判定）和 `max_retries` 处理，耗尽后通过显式配置的 `on_failure="error"` 抛出原异常。

预算起点（`started`）和退避进度（`delay`）在闭包创建时固定，跨父类的非网络重试保持，不会因外层重试而放大成多份。`retry_on` 排除网络异常，保证预算耗尽后的网络错误直接抛出、不被父类再次重试或吞成错误消息。

## 替代方案

- 保留两个中间件（内层 `NetworkRetry` + 外层 `ModelRetry`，用 `retry_on` 谓词排除网络错误）：功能等价，但装配绕、且「网络错误不归外层管」这个不变量靠装配顺序+谓词两处共同维持，一处漏改就重新放大预算；采纳作者「合并成单中间件」的建议后拒绝。
- 让预算是进程级/全局共享：会把互不相关的模型调用互相拖累；拒绝。
- 用请求对象标识跨外层重试共享预算：需要为「同一次模型调用」建立稳定身份，而 `ModelRequest` 无此语义；拒绝。

## 后果

网络错误的唯一重试入口是 `NetworkRetryMiddleware` 自身的预算循环，`network_budget_seconds` 是真实上限，不再有外层放大。非网络错误复用父类的次数重试与 `ModelError.is_retryable` 判定，耗尽后抛出原异常。

网络重试参数用 `network_` 前缀（`network_budget_seconds`/`network_initial_delay`/`network_max_delay`）与父类非网络重试的 `initial_delay`/`max_delay` 区分。`_is_network_error` 与 `_retry_non_network_errors` 均为模块私有（不再从 `__init__.py` 导出），且依赖公开的 LangChain 模型异常与 HTTPX 异常，不再 import `langchain.agents.middleware._retry` 私有模块。

## 验证

`backend/test/unit/agents/test_network_retry.py`：

- `test_network_budget_honored_and_fails_explicitly`：虚拟时钟下持续 `ConnectionError`，累计等待受单次 600s 预算约束，最终**抛出**异常而非返回含错误文本的响应；
- `test_rate_limit_exhaustion_preserves_original_error`：同步/异步调用在零次或两次重试配置下，429 耗尽后抛出同一个原异常，调用次数为 `max_retries + 1`；
- `test_non_retryable_model_error_propagates`：`ModelError.is_retryable=False` 立即抛出，不消耗重试次数；
- `test_non_network_error_retry_succeeds_after_backoff`：非网络错误重试成功后正常返回；
- `test_network_then_non_network_error_routes_to_parent_retry`：网络异常重试后遇到非网络异常，交给父类按 `max_retries` 重试成功；
- `test_network_budget_survives_parent_retry`：网络 → 非网络 → 网络的序列下，预算起点跨父类重试保持，累计等待不被放大。

## 异常分类边界

`_is_network_error` 沿异常链优先读取 HTTP 4xx/5xx、LangChain 标准模型异常与 HTTPX 传输异常。4xx 保持非网络重试语义；5xx、连接、超时及远端流式协议中断进入网络预算。标准 `ModelError` 的其他类型保持非网络语义，未知包装的文本不能覆盖内层明确的分类。仅在整条链都没有结构化分类时使用原有文本兜底。

只扩充关键词会继续依赖供应商响应措辞，无法可靠区分非法 `timeout` 参数与请求超时；逐个接入供应商 SDK 会增加重复映射。因此复用已有 LangChain/HTTPX 依赖和 SDK 的 HTTP 状态，不引入依赖或配置。异常分类遵循本记录，次数重试耗尽遵循[显式失败策略](./2026-09-23-model-retry-failure.md)。

回归证据由 `test_network_retry.py` 中真实 SDK 状态码、标准模型错误、同步/异步预算耗尽与非法参数测试提供。恢复关键词优先实现时，这些案例因错误分类、返回错误 AIMessage 或重复执行非法请求失败。真实 worker 断网恢复、最终 Run 状态与用户取消仍需 E2E 验证，分类单测不证明这些结果。
