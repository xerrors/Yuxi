# 模型重试耗尽合成回答破坏终态关联

日期：2026-09-23
Owner：backend/package/yuxi/agents/middlewares/network_retry.py
关联决策：[模型重试耗尽保留失败语义](../decisions/implemented/2026-09-23-model-retry-failure.md)

## 影响

[Issue #1062](https://github.com/xerrors/Yuxi/issues/1062) 报告子智能体 429 重试耗尽后发生输出一致性异常，并连锁出现主运行清理失败。确定性回放确认模型错误被替换为输出持久化错误；历史五子任务并发下的清理崩溃尚未复现。普通和子智能体共用同一个重试中间件，均受失败语义影响。

## 事实时间线

- 2026-09-23，PR #1068 提议给合成 AIMessage 打标记并豁免终态审计关联。
- 2026-09-23，隔离 Compose 的确定性回放确认旧 continue 行为使父智能体读取到“最终输出持久化或绑定失败”；切换 error 策略后，同一链路保留 429 原因并完成父任务和清理。

## 因果链

provider 持续限流 → 模型次数重试耗尽 → 上游默认 continue 合成 AIMessage → 消息没有真实 model lifecycle 审计 → chat_service 无法关联当前 Run 的最终消息 → 输出持久化错误覆盖真实模型失败。豁免关联并写 completed 又会产生成功状态与实际失败不一致的问题。

## 安全网为何漏过

既有次数重试单测明确期待错误 AIMessage，验证了上游默认行为，却没有检查它与 Yuxi 的 Run / audit 契约是否兼容。网络预算测试只覆盖显式抛出路径，无法发现次数重试的另一种耗尽结果。

## 修正与验证

统一中间件显式配置 on_failure="error"。`test_rate_limit_exhaustion_preserves_original_error` 验证同步/异步、零次/两次重试保留原异常。`test_model_retry_exhaustion_preserves_failure_and_parent_recovers` 通过真实 API、worker、HTTP / SSE 与 PostgreSQL，验证首次/工具调用后 429、普通/子 Run、父任务读取失败及重复请求。

命令与结果见[决策记录](../decisions/implemented/2026-09-23-model-retry-failure.md#验证)。恢复旧策略后，unit 四项失败；子任务 E2E 在“父任务收到真实 429 原因”断言处失败。

## 防复发措施

耗尽错误的 oracle 由真实异常、Run 失败字段、部分输出错误元数据与父任务工具结果共同组成。单元测试与现有 Runtime System Tests 的确定性 E2E gate 拒绝合成回答、错误原因丢失及失败后无法继续运行的回归。

## 未解决风险

真实 provider 限流节奏、五子任务并发以及历史清理崩溃仍需现场或外部探针验证；本修复不宣称解决所有 runtime cleanup 故障。
